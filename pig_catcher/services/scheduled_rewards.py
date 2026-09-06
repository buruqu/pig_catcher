"""Durable, exactly-once group reward campaigns; no platform credentials or timers in payloads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchView
from ..domain.enums import AssetKind
from ..domain.errors import DomainValidationError
from ..domain.item_bag import CODE_CHANGE_COUPON, PIG_CHOICE_COUPON
from ..domain.models import CommandIdentity, ScopeKey
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import PigCatcherDatabase
from ..infrastructure.repositories.item_bag import ItemBagRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from .administration import AdministrationService
from .command_state import iso_timestamp
from .dispatch import DispatchResult

BIRTHDAY_ID = "pig-admin-birthday-20260906"
BIRTHDAY_AT = datetime.fromisoformat("2026-09-06T19:00:00+08:00")
BIRTHDAY_REWARDS = {
    "coins": 9600,
    "coupons": {PIG_CHOICE_COUPON: 1, CODE_CHANGE_COUPON: 1},
    "pig": "撅撅猪",
    "food": "撅撅猪派",
    "commemorative_code": "20260906",
    "numbering": "commemorative-label-with-unique-operation-code",
}


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ScheduledRewardService:
    def __init__(self, database: PigCatcherDatabase, *, clock: Clock | None = None) -> None:
        self.database = database
        self.clock = clock or SystemClock()
        self.admin = AdministrationService(
            database, refresh_hours=(0, 9, 12, 19), timezone_name="Asia/Shanghai", clock=self.clock
        )
        self.items = ItemBagRepository()
        self.receipts = ReceiptRepository()

    async def schedule_birthday(self, scope_ids: list[str], *, numbering_confirmed: bool) -> dict:
        if not numbering_confirmed:
            raise DomainValidationError("生日纪念编号方案尚未确认，不能启动发放。")
        scopes = sorted({ScopeKey.parse(scope).value for scope in scope_ids})
        if not scopes:
            raise DomainValidationError("生日礼包至少需要一个明确的群范围。")
        payload = {**BIRTHDAY_REWARDS, "scope_ids": scopes}
        raw = encode(payload)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await session.fetch_one(
                "SELECT payload_hash,scheduled_at FROM scheduled_reward_campaigns WHERE campaign_id=?", (BIRTHDAY_ID,)
            )
            if existing:
                if existing["payload_hash"] != digest or existing["scheduled_at"] != iso_timestamp(BIRTHDAY_AT):
                    raise DomainValidationError("同一生日活动已存在不同参数，禁止改写或重复建立。")
                return {"campaign_id": BIRTHDAY_ID, "created": False, "scheduled_at": iso_timestamp(BIRTHDAY_AT)}
            for scope in scopes:
                if not await session.fetch_one("SELECT 1 FROM scopes WHERE scope_id=? AND enabled=1", (scope,)):
                    raise DomainValidationError(f"未找到启用的群范围：{scope}")
                await self._templates(session, scope, payload)
            await session.execute(
                "INSERT INTO scheduled_reward_campaigns(campaign_id,title,scheduled_at,payload_json,payload_hash,"
                "created_at) VALUES(?,?,?,?,?,?)",
                (BIRTHDAY_ID, "猪管生日快乐！", iso_timestamp(BIRTHDAY_AT), raw, digest, now),
            )
            await session.executemany(
                "INSERT INTO scheduled_reward_scopes(campaign_id,scope_id) VALUES(?,?)",
                [(BIRTHDAY_ID, scope) for scope in scopes],
            )
            await self.admin.repository.insert_audit_event(
                session,
                audit_event_id=uuid4().hex,
                scope_id=None,
                actor_user_id="system-birthday",
                action="birthday-campaign-scheduled",
                object_type="reward-campaign",
                object_id=BIRTHDAY_ID,
                detail_json=encode(
                    {"scheduled_at": iso_timestamp(BIRTHDAY_AT), "payload_hash": digest, "scope_ids": scopes}
                ),
                now=now,
            )
        return {"campaign_id": BIRTHDAY_ID, "created": True, "scheduled_at": iso_timestamp(BIRTHDAY_AT)}

    async def process_due(self) -> int:
        """One short transaction per group; crashes retry only uncommitted groups."""
        now = iso_timestamp(self.clock.now())
        due = await self.database.fetch_all(
            "SELECT campaign_id FROM scheduled_reward_campaigns WHERE state='scheduled' AND scheduled_at<=?",
            (now,),
        )
        granted = 0
        errors: list[Exception] = []
        for campaign in due:
            scopes = await self.database.fetch_all(
                "SELECT scope_id FROM scheduled_reward_scopes WHERE campaign_id=? AND completed_at IS NULL",
                (campaign["campaign_id"],),
            )
            # Do not silently skip a group on errors; the runner retries durable state.
            for scope in scopes:
                try:
                    granted += await self._grant_scope(campaign["campaign_id"], scope["scope_id"], now)
                except Exception as exc:
                    # One group's authorization/storage fault must not hold back others.
                    errors.append(exc)
            async with self.database.transaction() as session:
                await session.execute(
                    "UPDATE scheduled_reward_campaigns SET state='complete',completed_at=? WHERE campaign_id=? "
                    "AND NOT EXISTS(SELECT 1 FROM scheduled_reward_scopes "
                    "WHERE campaign_id=? AND completed_at IS NULL)",
                    (now, campaign["campaign_id"], campaign["campaign_id"]),
                )
        if errors:
            raise errors[0]
        return granted

    async def _grant_scope(self, campaign_id: str, scope_id: str, now: str) -> int:
        async with self.database.transaction() as session:
            row = await session.fetch_one(
                "SELECT c.*,s.completed_at AS scope_completed FROM scheduled_reward_campaigns c "
                "JOIN scheduled_reward_scopes s ON s.campaign_id=c.campaign_id WHERE c.campaign_id=? AND s.scope_id=?",
                (campaign_id, scope_id),
            )
            if not row or row["scope_completed"] or row["scheduled_at"] > now:
                return 0
            payload = json.loads(row["payload_json"])
            if hashlib.sha256(encode(payload).encode()).hexdigest() != row["payload_hash"]:
                raise DomainValidationError("生日礼包参数校验失败，发放已停止。")
            pig_template, food_template = await self._templates(session, scope_id, payload)
            scope = await session.fetch_one("SELECT * FROM scopes WHERE scope_id=?", (scope_id,))
            # Freeze eligibility at the requested moment even when recovering after downtime.
            players = await session.fetch_all(
                "SELECT * FROM players WHERE scope_id=? AND created_at<=? ORDER BY player_id",
                (scope_id, row["scheduled_at"]),
            )
            if not players:
                # Empty mirrored transports are included in the campaign but must not
                # duplicate the official group's announcement or invent recipients.
                await session.execute(
                    "UPDATE scheduled_reward_scopes SET recipient_count=0,completed_at=? "
                    "WHERE campaign_id=? AND scope_id=?",
                    (now, campaign_id, scope_id),
                )
                return 0
            actor = CommandIdentity(
                ScopeKey.parse(scope_id),
                str(scope["stream_id"] or "birthday-system"),
                "system-birthday",
                "猪管生日福利",
                group_name=scope["group_name"],
            )
            for player in players:
                player = dict(player)
                balance = await self.admin.economy_repository.apply_currency_change(
                    session,
                    player_id=player["player_id"],
                    scope_id=scope_id,
                    amount=payload["coins"],
                    reason_code="birthday-campaign",
                    reason_text="猪管生日福利·20260906",
                    source_object_type="reward-campaign",
                    source_object_id=campaign_id,
                    ledger_entry_id=uuid4().hex,
                    idempotency_key=f"{campaign_id}:{player['player_id']}:coins",
                    now=now,
                )
                if balance is None:
                    raise RuntimeError("生日猪币入账失败，当前群整批回滚。")
                for coupon_id, quantity in payload["coupons"].items():
                    await self.items.grant_coupon(
                        session,
                        player_id=player["player_id"],
                        scope_id=scope_id,
                        coupon_id=coupon_id,
                        quantity=quantity,
                        source_id=campaign_id,
                        source_kind="birthday-campaign",
                        now=now,
                    )
                pig = await self.admin._insert_granted_asset(
                    session,
                    identity=actor,
                    target=player,
                    asset_kind=AssetKind.PIG,
                    template=pig_template,
                    requested_short_code=None,
                    now=now,
                )
                await session.execute(
                    "UPDATE pig_instances SET commemorative_code=? WHERE pig_instance_id=?",
                    (payload["commemorative_code"], pig["instance_id"]),
                )
                food = await self.admin._insert_granted_asset(
                    session,
                    identity=actor,
                    target=player,
                    asset_kind=AssetKind.FOOD,
                    template=food_template,
                    requested_short_code=None,
                    now=now,
                )
                await session.execute(
                    "INSERT INTO scheduled_reward_grants(campaign_id,player_id,scope_id,result_json,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (
                        campaign_id,
                        player["player_id"],
                        scope_id,
                        encode(
                            {
                                "balance": balance,
                                "coins": payload["coins"],
                                "coupons": payload["coupons"],
                                "pig": pig,
                                "food": food,
                                "commemorative_code": payload["commemorative_code"],
                            }
                        ),
                        now,
                    ),
                )
            view = DispatchView(
                "猪管生日快乐！",
                scope["group_name"] or "全体猪友",
                subtitle="PiG Dream! · 2026.09.06 生日派对",
                banner="感谢每一位猪友的陪伴！今天的快乐，和大家一起分享。生日福利已到账！",
                stats=(
                    Line("本群领取人数", f"{len(players)} 人"),
                    Line("每人猪币", "+9,600"),
                    Line("生日纪念编号", "20260906"),
                ),
                panels=(
                    Panel(
                        "送给每一位猪友",
                        (
                            Line("猪猪自选券", "×1"),
                            Line("编号修改券", "×1"),
                            Line("撅撅猪", "×1 · 20260906 生日纪念"),
                            Line("撅撅猪派", "×1"),
                        ),
                    ),
                ),
                hints=(
                    "/道具背包 · /猪猪背包 · /美食背包 查看到账福利。",
                    "发放对象：今日19:00前已在本群登记的玩家。",
                    "生日猪共享纪念编号，赠送与使用仍按各自的唯一操作编号。",
                ),
                presentation="birthday",
                scene_key="20260906",
            )
            receipt = await self.receipts.reserve(
                session,
                idempotency_key=f"{campaign_id}:{scope_id}:notice",
                scope_id=scope_id,
                player_id=None,
                command_name="pig-catcher.scheduled-reward",
                request_fingerprint=row["payload_hash"],
                result_type="birthday-reward",
                result_object_id=campaign_id,
                result_json=encode({"view": view.payload(), "recipient_count": len(players)}),
                text_summary=view.text(),
                now=now,
                catch_quota_cost=0,
            )
            await session.execute(
                "UPDATE scheduled_reward_scopes SET receipt_id=?,recipient_count=?,completed_at=? "
                "WHERE campaign_id=? AND scope_id=?",
                (receipt.receipt.receipt_id, len(players), now, campaign_id, scope_id),
            )
            await self.admin.repository.insert_audit_event(
                session,
                audit_event_id=uuid4().hex,
                scope_id=scope_id,
                actor_user_id="system-birthday",
                action="birthday-campaign-granted",
                object_type="reward-campaign",
                object_id=campaign_id,
                detail_json=encode({"recipient_count": len(players), "payload_hash": row["payload_hash"]}),
                now=now,
            )
            return len(players)

    async def pending_notices(self) -> list[tuple[str, DispatchResult]]:
        rows = await self.database.fetch_all(
            "SELECT r.idempotency_key,s.stream_id FROM scheduled_reward_scopes g "
            "JOIN command_receipts r ON r.receipt_id=g.receipt_id JOIN scopes s ON s.scope_id=g.scope_id "
            "WHERE r.send_status='pending' AND s.stream_id<>'' ORDER BY g.scope_id",
        )
        results = []
        async with self.database.transaction(immediate=False) as session:
            for row in rows:
                receipt = await self.receipts.get_by_key(session, row["idempotency_key"])
                results.append(
                    (
                        row["stream_id"],
                        DispatchResult(DispatchView.from_payload(json.loads(receipt.result_json)["view"]), receipt),
                    )
                )
        return results

    async def _templates(self, session, scope, payload):
        result = []
        for kind, name in ((AssetKind.PIG, payload["pig"]), (AssetKind.FOOD, payload["food"])):
            templates = await self.admin.repository.eligible_templates(
                session, scope_id=scope, asset_kind=kind, selector=name
            )
            if len(templates) != 1:
                raise DomainValidationError(f"{scope} 的生日奖励 {name} 未唯一授权，本群没有发放。")
            result.append(templates[0])
        return tuple(result)
