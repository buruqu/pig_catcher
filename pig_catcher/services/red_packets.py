"""Atomic red-packet escrow, claims, refunds and receipt-backed image views."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

from ..domain.dispatch import safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchView
from ..domain.errors import DomainValidationError, InsufficientBalanceError
from ..domain.models import CommandIdentity
from ..domain.ports import Clock, MessageKeyFactory, RandomSource, SystemClock, SystemRandomSource
from ..domain.red_packets import (
    PACKET_ACTIVE_LIMIT,
    PACKET_LIFETIME_HOURS,
    PacketRequest,
    draw_packet_amount,
    validate_packet,
)
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories.economy import EconomyRepository
from ..infrastructure.repositories.framework import FrameworkRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from .command_state import iso_timestamp, validate_existing_receipt
from .dispatch import DispatchResult
from .receipts import request_fingerprint


class RedPacketService:
    def __init__(
        self, database: PigCatcherDatabase, *, clock: Clock | None = None, random_source: RandomSource | None = None
    ) -> None:
        self.database = database
        self.clock = clock or SystemClock()
        self.random_source = random_source or SystemRandomSource()
        self.framework = FrameworkRepository()
        self.economy = EconomyRepository()
        self.receipts = ReceiptRepository()

    async def send(self, identity: CommandIdentity, request: PacketRequest, *, admin: bool = False) -> DispatchResult:
        """The privileged adapter must authenticate before passing admin=True."""
        validate_packet(request.total, request.count)
        if len(request.greeting) > 80 or any(ord(c) < 32 for c in request.greeting):
            raise DomainValidationError("红包祝福语限80字，不包含控制字符。")
        action = "admin-send" if admin else "send"
        payload = {"total": request.total, "count": request.count, "greeting": request.greeting}
        command = f"pig-catcher.red-packet.{action}"
        key = MessageKeyFactory.build(identity, command)
        now_dt = self.clock.now()
        now = iso_timestamp(now_dt)
        async with self.database.transaction() as session:
            existing = await self._existing(session, identity, command, key, payload)
            if existing:
                return existing
            await self.framework.touch_identity(session, identity=identity, now=now)
            await self._expire(session, now)
            count = await session.fetch_one(
                "SELECT COUNT(*) FROM red_packets WHERE sender_player_id=? AND status='active'",
                (identity.player_id,),
            )
            if count[0] >= PACKET_ACTIVE_LIMIT:
                raise DomainValidationError("你还有10个未领完红包，请等群友领取后再发。")
            packet_id = uuid4().hex
            code = packet_id[:10].upper()
            # Independent packet namespace; never competes with pig/food operation codes.
            while await session.fetch_one("SELECT 1 FROM red_packets WHERE short_code=?", (code,)):
                code = uuid4().hex[:10].upper()
            if not admin:
                await self._coins(
                    session, identity.player_id, identity.scope.value, -request.total, packet_id, "escrow", now
                )
            await session.execute(
                "INSERT INTO red_packets(packet_id,short_code,scope_id,sender_player_id,sender_name,funding,greeting,"
                "total_coins,total_count,remaining_coins,remaining_count,status,created_at,expires_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                (
                    packet_id,
                    code,
                    identity.scope.value,
                    identity.player_id,
                    identity.display_name,
                    "admin" if admin else "player",
                    request.greeting,
                    request.total,
                    request.count,
                    request.total,
                    request.count,
                    now,
                    iso_timestamp(now_dt + timedelta(hours=PACKET_LIFETIME_HOURS)),
                    now,
                ),
            )
            view = await self._view(session, identity, packet_id, title="猪管福利红包" if admin else "拼手气红包来啦")
            return await self._commit_receipt(session, identity, command, key, payload, packet_id, view, now)

    async def claim(self, identity: CommandIdentity, code: str = "") -> DispatchResult:
        code = self._code(code)
        command = "pig-catcher.red-packet.claim"
        key = MessageKeyFactory.build(identity, command)
        payload = {"code": code}
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self._existing(session, identity, command, key, payload)
            if existing:
                return existing
            await self.framework.touch_identity(session, identity=identity, now=now)
            await self._expire(session, now)
            if code:
                packet = await session.fetch_one(
                    "SELECT * FROM red_packets WHERE scope_id=? AND short_code=?",
                    (identity.scope.value, code),
                )
            else:
                # Omitted ID always means newest active packet, not another unclaimed one.
                packet = await session.fetch_one(
                    "SELECT * FROM red_packets WHERE scope_id=? AND status='active' AND expires_at>? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (identity.scope.value, now),
                )
            if packet is None:
                raise DomainValidationError("本群没有这个可抢红包；/红包 查看最近红包。")
            if packet["status"] != "active" or packet["expires_at"] <= now:
                raise DomainValidationError("这个红包已领完或已过期；/红包 可以查看领取记录。")
            if await session.fetch_one(
                "SELECT 1 FROM red_packet_claims WHERE packet_id=? AND player_id=?",
                (packet["packet_id"], identity.player_id),
            ):
                raise DomainValidationError("你已经抢过这个红包啦，每人每包只能领一次。")
            amount = draw_packet_amount(
                int(packet["remaining_coins"]), int(packet["remaining_count"]), self.random_source.random()
            )
            balance = await self._coins(
                session, identity.player_id, identity.scope.value, amount, packet["packet_id"], "claim", now
            )
            await session.execute(
                "INSERT INTO red_packet_claims(packet_id,player_id,display_name,amount,created_at) VALUES(?,?,?,?,?)",
                (packet["packet_id"], identity.player_id, identity.display_name, amount, now),
            )
            await session.execute(
                "UPDATE red_packets SET remaining_coins=remaining_coins-?,remaining_count=remaining_count-1,"
                "status=CASE WHEN remaining_count=1 THEN 'claimed' ELSE 'active' END,updated_at=? WHERE packet_id=?",
                (amount, now, packet["packet_id"]),
            )
            view = await self._view(
                session, identity, packet["packet_id"], title="红包抢到啦！", award=amount, balance=balance
            )
            return await self._commit_receipt(session, identity, command, key, payload, packet["packet_id"], view, now)

    async def detail(self, identity: CommandIdentity, code: str = "") -> DispatchResult:
        code = self._code(code)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self._expire(session, now)
            if code:
                row = await session.fetch_one(
                    "SELECT packet_id FROM red_packets WHERE scope_id=? AND short_code=?", (identity.scope.value, code)
                )
            else:
                row = await session.fetch_one(
                    "SELECT packet_id FROM red_packets WHERE scope_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                    (identity.scope.value,),
                )
            if row is None:
                raise DomainValidationError("本群还没有红包。试试 /发红包 1000 10")
            return DispatchResult(await self._view(session, identity, row[0], title="红包领取详情"))

    async def expire(self) -> int:
        async with self.database.transaction() as session:
            return await self._expire(session, iso_timestamp(self.clock.now()))

    async def _expire(self, session: DatabaseSession, now: str) -> int:
        packets = await session.fetch_all(
            "SELECT * FROM red_packets WHERE status='active' AND expires_at<=? ORDER BY expires_at LIMIT 100", (now,)
        )
        for packet in packets:
            refund = int(packet["remaining_coins"]) if packet["funding"] == "player" else 0
            if refund:
                await self._coins(
                    session, packet["sender_player_id"], packet["scope_id"], refund, packet["packet_id"], "refund", now
                )
            await session.execute(
                "UPDATE red_packets SET status='expired',refunded_coins=?,updated_at=? WHERE packet_id=?",
                (refund, now, packet["packet_id"]),
            )
            await self._audit(
                session,
                packet["scope_id"],
                "system",
                "expired",
                packet["packet_id"],
                {"refund": refund, "unused": int(packet["remaining_coins"])},
                now,
            )
        return len(packets)

    async def _coins(self, session, player_id, scope_id, amount, packet_id, action, now) -> int:
        balance = await self.economy.apply_currency_change(
            session,
            player_id=player_id,
            scope_id=scope_id,
            amount=amount,
            reason_code=f"red-packet-{action}",
            reason_text={"escrow": "发出拼手气红包", "claim": "抢到红包", "refund": "红包过期退回"}[action],
            source_object_type="red-packet",
            source_object_id=packet_id,
            ledger_entry_id=uuid4().hex,
            idempotency_key=f"packet:{packet_id}:{action}:{player_id}",
            now=now,
        )
        if balance is None:
            raise InsufficientBalanceError("猪币不足，红包没有发出，也没有扣款。")
        return balance

    async def _view(self, session, identity, packet_id, *, title, award=None, balance=None) -> DispatchView:
        row = await session.fetch_one("SELECT * FROM red_packets WHERE packet_id=?", (packet_id,))
        claims = await session.fetch_all(
            "SELECT display_name,amount FROM red_packet_claims WHERE packet_id=? ORDER BY amount DESC,created_at",
            (packet_id,),
        )
        sender = safe_display_name(row["sender_name"], row["sender_player_id"])
        stats = [
            Line("已领 / 总个数", f"{row['total_count'] - row['remaining_count']} / {row['total_count']}"),
            Line("红包总额", f"{row['total_coins']:,} 猪币"),
            Line("剩余猪币", f"{row['remaining_coins']:,}"),
        ]
        if award is not None:
            stats = [Line("本次手气", f"+{award:,} 猪币"), Line("当前余额", f"{balance:,}"), *stats]
        state = {"active": "正在拼手气", "claimed": "已全部领完", "expired": "已过期"}[row["status"]]
        note = (
            f"未领的 {row['refunded_coins']:,} 猪币已退还发包人。"
            if row["refunded_coins"]
            else "猪管系统福利，不扣管理员余额。"
            if row["funding"] == "admin"
            else "猪币已从发包人余额存入红包；24小时未领完自动原路退回。"
        )
        claim_lines = tuple(
            Line(
                safe_display_name(item["display_name"], ""),
                f"{item['amount']:,} 猪币",
                "手气最佳" if row["status"] == "claimed" and item["amount"] == claims[0]["amount"] else "",
            )
            for item in claims[:12]
        )
        return DispatchView(
            title,
            safe_display_name(identity.display_name, identity.user_id),
            subtitle=f"{sender} 的红包 · {state}",
            banner=row["greeting"],
            stats=tuple(stats),
            panels=(Panel("领取榜", claim_lines, f"{len(claims)}人已领 · 展示手气前12名"),),
            hints=(
                f"/抢红包 {row['short_code']}  ·  /红包 {row['short_code']}",
                "每人每包限领一次；省略编号领取本群最新的未结束红包。",
                note,
            ),
            presentation="red-packet",
            scene_key=row["short_code"],
        )

    async def _existing(self, session, identity, command, key, payload):
        receipt = await self.receipts.get_by_key(session, key)
        if receipt:
            validate_existing_receipt(receipt, identity=identity, command_name=command, request_payload=payload)
            return DispatchResult(DispatchView.from_payload(json.loads(receipt.result_json)["view"]), receipt)
        return None

    async def _commit_receipt(self, session, identity, command, key, payload, packet_id, view, now):
        await self._audit(
            session, identity.scope.value, identity.user_id, command.rsplit(".", 1)[-1], packet_id, payload, now
        )
        reservation = await self.receipts.reserve(
            session,
            idempotency_key=key,
            scope_id=identity.scope.value,
            player_id=identity.player_id,
            command_name=command,
            request_fingerprint=request_fingerprint(payload),
            result_type="red-packet",
            result_object_id=packet_id,
            result_json=json.dumps({"view": view.payload()}, ensure_ascii=False),
            text_summary=view.text(),
            now=now,
            catch_quota_cost=0,
        )
        return DispatchResult(view, reservation.receipt)

    @staticmethod
    async def _audit(session, scope, actor, action, packet_id, detail, now):
        await session.execute(
            "INSERT INTO audit_events(audit_event_id,scope_id,actor_user_id,action,object_type,object_id,"
            "detail_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                scope,
                actor,
                f"red-packet-{action}",
                "red-packet",
                packet_id,
                json.dumps(detail, ensure_ascii=False),
                now,
            ),
        )

    @staticmethod
    def _code(code: str) -> str:
        normalized = str(code or "").strip().removeprefix("#").upper()
        if normalized and (len(normalized) != 10 or any(c not in "0123456789ABCDEF" for c in normalized)):
            raise DomainValidationError("请输入红包卡片上的10位红包编号，或省略编号使用本群最新红包。")
        return normalized
