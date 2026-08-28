"""猪猪派遣应用服务：原子结算、精确确认、独立材料和幂等回执。"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from ..commands.dispatch import DispatchRequest
from ..domain.dispatch import (
    DISPATCH_VERSION,
    DURATIONS,
    MATERIAL_SCALE,
    MATERIALS,
    REGIONS_BY_ID,
    TOOLS_BY_ID,
    DispatchError,
    material_id,
    team_bonus,
    team_slots,
)
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchView
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, SystemClock
from ..domain.selectors import parse_asset_selector
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories.achievement_coupons import AchievementCouponRepository
from ..infrastructure.repositories.dispatch import DispatchRepository, encode, iso_ms, timestamp_ms
from ..infrastructure.repositories.economy import EconomyRepository
from ..infrastructure.repositories.framework import FrameworkRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from .command_state import validate_existing_receipt
from .dispatch_queries import DispatchQueries, local_time, option_text, reward_lines
from .receipts import request_fingerprint

_MUTATIONS = frozenset(("team", "start", "recall", "confirm", "cancel", "craft", "convert", "choose", "returns"))
_QUERIES = frozenset(("overview", "routes", "bag", "recipes", "journal", "detail", "souvenirs", "choices"))


@dataclass(frozen=True, slots=True)
class DispatchResult:
    view: DispatchView
    receipt: CommandReceipt | None = None


class DispatchService:
    def __init__(
        self, database: PigCatcherDatabase, *, clock: Clock | None = None, seed_factory: Callable[[], str] | None = None
    ) -> None:
        self.database = database
        self.clock = clock or SystemClock()
        self.seed_factory = seed_factory or (lambda: secrets.token_hex(32))
        self.repository = DispatchRepository()
        self.framework = FrameworkRepository()
        self.receipts = ReceiptRepository()
        self.economy = EconomyRepository()
        self.queries = DispatchQueries(self.repository)

    async def execute(self, identity: CommandIdentity, request: DispatchRequest) -> DispatchResult:
        if request.action not in _MUTATIONS | _QUERIES:
            raise DispatchError("未知派遣操作。")
        # 出发精度固定为秒，平行旅行的有效时间并集也不会有毫秒截断损失。
        now_ms = timestamp_ms(self.clock.now()) // 1000 * 1000
        now = iso_ms(now_ms)
        command = f"pig-catcher.dispatch.{request.action}"
        key = MessageKeyFactory.build(identity, command) if request.action in _MUTATIONS else ""
        async with self.database.transaction() as session:
            if key:
                receipt = await self.receipts.get_by_key(session, key)
                if receipt:
                    validate_existing_receipt(
                        receipt, identity=identity, command_name=command, request_payload=request.args
                    )
                    view = DispatchView.from_payload(json.loads(receipt.result_json)["view"])
                    return DispatchResult(await self._restrict_media(session, identity, view), receipt)
            await self.framework.touch_identity(session, identity=identity, now=now)
            view = await self._perform(session, identity, request, now_ms, key)
            view = await self._restrict_media(session, identity, view)
            if not key:
                return DispatchResult(view)
            reservation = await self.receipts.reserve(
                session,
                idempotency_key=key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command,
                request_fingerprint=request_fingerprint(request.args),
                result_type="dispatch",
                result_object_id=key,
                result_json=encode({"version": DISPATCH_VERSION, "action": request.action, "view": view.payload()}),
                text_summary=view.text(),
                now=now,
                catch_quota_cost=0,
            )
            return DispatchResult(view, reservation.receipt)

    @staticmethod
    async def _restrict_media(session: DatabaseSession, identity: CommandIdentity, view: DispatchView) -> DispatchView:
        if not view.pigs:
            return view
        templates = tuple({pig.template_id for pig in view.pigs})
        placeholders = ",".join("?" for _ in templates)
        rows = await session.fetch_all(
            f"""SELECT t.template_id FROM pig_templates t LEFT JOIN scope_pig_templates s
            ON s.template_id=t.template_id AND s.scope_id=?
            WHERE t.template_id IN ({placeholders}) AND
            (t.scope_type='common' OR (s.authorized=1 AND s.consent_status='granted'))""",
            (identity.scope.value, *templates),
        )
        permitted = {row["template_id"] for row in rows}
        return replace(
            view,
            pigs=tuple(
                pig if pig.template_id in permitted else replace(pig, image_relpath="", tags=())
                for pig in view.pigs
            ),
        )

    async def _perform(
        self, session: DatabaseSession, identity: CommandIdentity, request: DispatchRequest, now_ms: int, key: str
    ) -> DispatchView:
        action, args = request.action, request.args
        if action == "overview":
            return await self.queries.overview(session, identity, now_ms)
        if action == "routes":
            return self.queries.routes(identity)
        if action == "bag":
            return await self.queries.bag(session, identity)
        if action == "recipes":
            return self.queries.recipes(identity)
        if action == "journal":
            return await self.queries.journal(session, identity, args["page"])
        if action == "souvenirs":
            return await self.queries.souvenirs(session, identity, args["page"])
        if action == "choices":
            return await self.queries.choices(session, identity)
        if action == "detail":
            trip = await self._owned_trip(session, identity, args["trip_id"])
            return await self.queries.trip_detail(session, identity, trip)
        if action == "team":
            return await self._team_preview(session, identity, args, now_ms)
        if action == "start":
            return await self._start_preview(session, identity, args, now_ms)
        if action == "recall":
            return await self._recall_preview(session, identity, args["slot"], now_ms)
        if action == "confirm":
            return await self._confirm(session, identity, now_ms, key)
        if action == "cancel":
            result = await session.execute("DELETE FROM dispatch_pending WHERE player_id=?", (identity.player_id,))
            return self.queries.view(
                identity,
                "已取消确认" if result.rowcount else "没有待确认操作",
                banner="未扣除猪币或器具；已出发的旅行不受取消确认影响。",
                hints=("/猪猪派遣 返回远行社；要中止在途旅行请使用 召回。",),
            )
        if action == "craft":
            return await self._craft(session, identity, args, now_ms, key)
        if action == "convert":
            return await self._convert(session, identity, args, now_ms, key)
        if action == "choose":
            chosen = await self.repository.claim_choice(
                session,
                player_id=identity.player_id,
                choice_id=args["choice_id"],
                selected=args["selected"],
                now_ms=now_ms,
            )
            return self.queries.view(
                identity,
                "奇遇已选择",
                banner=(
                    "此奇遇已结算，不会重复获得奖励。" if chosen["already_claimed"] else option_text(chosen["option"])
                ),
                panels=(Panel(f"已选候选{chosen['selected']}", reward_lines(chosen["rewards"])),),
                hints=("/派遣背包 查看当前材料；/派遣游记 查看旅程。",),
            )
        if action == "returns":
            return await self._returns(session, identity)
        raise DispatchError("未知派遣操作。")

    async def _check_slot(
        self, session: DatabaseSession, identity: CommandIdentity, slot: int, *, idle: bool = True
    ) -> dict[str, Any] | None:
        profile = await self.repository.profile(session, identity.player_id)
        slots = team_slots(profile["effective_seconds"])
        if not isinstance(slot, int) or not 1 <= slot <= slots:
            raise DispatchError(f"目前解锁{slots}队；累计有效远行12小时/72小时分别解锁第2/3队。")
        active = await session.fetch_one(
            "SELECT * FROM dispatch_trips WHERE player_id=? AND slot=? AND status='traveling'",
            (identity.player_id, slot),
        )
        if idle and active:
            raise DispatchError("这支队伍正在旅行，不能改队或再次出发；可先召回。")
        return dict(active) if active else None

    async def _owned_trip(self, session: DatabaseSession, identity: CommandIdentity, trip_id: str) -> dict[str, Any]:
        row = await session.fetch_one(
            "SELECT * FROM dispatch_trips WHERE trip_id=? AND player_id=? AND scope_id=?",
            (trip_id.upper(), identity.player_id, identity.scope.value),
        )
        if row is None:
            raise DispatchError("没有找到本群属于你的这趟旅行。")
        return dict(row)

    async def _pending(
        self, session: DatabaseSession, identity: CommandIdentity, operation: str, payload: dict[str, Any], now_ms: int
    ) -> None:
        await session.execute(
            """INSERT INTO dispatch_pending VALUES(?,?,?,?) ON CONFLICT(player_id)
            DO UPDATE SET operation=excluded.operation,payload_json=excluded.payload_json,
            expires_ms=excluded.expires_ms""",
            (identity.player_id, operation, encode(payload), now_ms + 120_000),
        )

    async def _select_member(
        self, session: DatabaseSession, identity: CommandIdentity, text: str, selected: list[str]
    ) -> dict[str, Any]:
        selector = parse_asset_selector(text)
        clause = "AND p.short_code=? COLLATE NOCASE" if selector.short_code else "AND p.is_favorite=0"
        parameters: list[object] = [
            identity.player_id,
            identity.scope.value,
            selector.name,
            "".join(selector.name.split()),
        ]
        if selector.short_code:
            parameters.append(selector.short_code)
        exclusions = ""
        if selected:
            exclusions = "AND p.pig_instance_id NOT IN (" + ",".join("?" for _ in selected) + ")"
            parameters.extend(selected)
        # 自动选取在SQL LIMIT之前排除所有保护状态，背包再大也不会先取错20只。
        rows = await session.fetch_all(
            f"""SELECT p.pig_instance_id FROM pig_instances p
            WHERE p.owner_player_id=? AND p.scope_id=? AND p.state='active' AND p.locked_trade_id IS NULL
            AND (p.display_name_snapshot=? OR REPLACE(REPLACE(p.display_name_snapshot,' ',''),'　','')=? COLLATE NOCASE)
            AND NOT EXISTS(SELECT 1 FROM asset_occupancies o WHERE o.pig_instance_id=p.pig_instance_id)
            {clause} {exclusions} ORDER BY p.official_value,p.acquired_at,p.pig_instance_id LIMIT 1""",
            parameters,
        )
        if not rows:
            raise DispatchError(
                f"找不到可出发的“{text}”：请检查收藏、交易、在途状态或是否重复点名；收藏猪需完整名称#编号。"
            )
        return await self.repository.member(session, identity.player_id, rows[0]["pig_instance_id"])

    async def _team_preview(
        self, session: DatabaseSession, identity: CommandIdentity, args: dict[str, Any], now_ms: int
    ) -> DispatchView:
        slot = args["slot"]
        await self._check_slot(session, identity, slot)
        selectors = args["selectors"]
        if not isinstance(selectors, list) or len(selectors) > 3:
            raise DispatchError("每队最多3只猪。")
        members, ids = [], []
        for text in selectors:
            member = await self._select_member(session, identity, text, ids)
            members.append(member)
            ids.append(member["pig_instance_id"])
        if members:
            team_bonus(members, REGIONS_BY_ID["grassland"])
        team = await session.fetch_one(
            "SELECT revision FROM dispatch_teams WHERE player_id=? AND slot=?", (identity.player_id, slot)
        )
        await self._pending(
            session, identity, "team", {"slot": slot, "members": members, "revision": team[0] if team else 0}, now_ms
        )
        return self.queries.team_preview(identity, slot, members)

    async def _fresh_members(
        self, session: DatabaseSession, identity: CommandIdentity, original: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        members = []
        for previous in original:
            current = await self.repository.member(session, identity.player_id, previous["pig_instance_id"])
            self.repository.require_available(current)
            if current != previous:
                raise DispatchError("队员的编号、属性、收藏或状态已变化，请重新预览后确认。")
            members.append(current)
        return members

    async def _start_preview(
        self, session: DatabaseSession, identity: CommandIdentity, args: dict[str, Any], now_ms: int
    ) -> DispatchView:
        slot = args["slot"]
        await self._check_slot(session, identity, slot)
        team = await session.fetch_one(
            "SELECT * FROM dispatch_teams WHERE player_id=? AND slot=?", (identity.player_id, slot)
        )
        ids = json.loads(team["member_ids_json"]) if team else []
        if not ids:
            raise DispatchError(f"第{slot}队还没有队员，请先 /猪猪派遣 编队 {slot} 猪名、猪名。")
        members = [await self.repository.member(session, identity.player_id, pig_id) for pig_id in ids]
        for member in members:
            self.repository.require_available(member)
        if args["region_id"] not in REGIONS_BY_ID or args["hours"] not in DURATIONS:
            raise DispatchError("路线或时长不合法。")
        region = REGIONS_BY_ID[args["region_id"]]
        snapshot = {
            **args,
            "definition_version": DISPATCH_VERSION,
            "members": members,
            "bonus": team_bonus(members, region),
            "fee": region.fee * (args["hours"] // 4),
            "team_revision": team["revision"],
        }
        coupons = await AchievementCouponRepository().selected(
            session, identity.player_id, ("dispatch-numeric", "dispatch-visual")
        )
        snapshot["coupons"] = coupons
        snapshot["original_fee"] = snapshot["fee"]
        if coupons.get("dispatch-numeric", {}).get("ticket_id") == "dispatch-bill":
            snapshot["fee"] = max(0, snapshot["fee"] - 120)
        await self._check_resources(session, identity, snapshot)
        await self._pending(session, identity, "start", snapshot, now_ms)
        return self.queries.start_preview(identity, snapshot, now_ms)

    async def _check_resources(
        self, session: DatabaseSession, identity: CommandIdentity, snapshot: dict[str, Any]
    ) -> None:
        player = await session.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (identity.player_id,))
        if snapshot["fee"] and player[0] < snapshot["fee"]:
            raise DispatchError(f"本趟需要{snapshot['fee']}猪币，余额不足；可以先去免费的青草近郊。")
        tool_id = snapshot["tool_id"]
        if tool_id:
            if tool_id not in TOOLS_BY_ID:
                raise DispatchError("未知旅行器具。")
            row = await session.fetch_one(
                "SELECT quantity FROM dispatch_tools WHERE player_id=? AND tool_id=?", (identity.player_id, tool_id)
            )
            if row is None or row[0] < 1:
                raise DispatchError(f"没有{TOOLS_BY_ID[tool_id].name}，请先 /派遣背包 配方 查看制作材料。")

    async def _recall_preview(
        self, session: DatabaseSession, identity: CommandIdentity, slot: int, now_ms: int
    ) -> DispatchView:
        trip = await self._check_slot(session, identity, slot, idle=False)
        if trip is None:
            raise DispatchError("此队没有进行中的旅行；到期旅行已自动结算，可查看 /猪猪派遣 返程。")
        snapshot = json.loads(trip["snapshot_json"])
        await self._pending(session, identity, "recall", {"trip_id": trip["trip_id"], "slot": slot}, now_ms)
        return self.queries.view(
            identity,
            f"召回预览 · 第{slot}队",
            banner="召回仅保留完整4小时块的材料、奇遇与熟练度；未满一块的进度丢弃。",
            panels=(
                Panel(
                    REGIONS_BY_ID[snapshot["region_id"]].name,
                    (
                        Line("目前可结算", f"{trip['processed_blocks'] * 4}h", "以确认时完整块数为准"),
                        Line("出发费用 / 器具", "不会退还"),
                        Line("原定返程", local_time(trip["ends_ms"])),
                    ),
                ),
            ),
            hints=("2分钟内 /猪猪派遣 确认 召回；/猪猪派遣 取消 继续旅行。",),
        )

    async def _confirm(
        self, session: DatabaseSession, identity: CommandIdentity, now_ms: int, key: str
    ) -> DispatchView:
        pending = await session.fetch_one("SELECT * FROM dispatch_pending WHERE player_id=?", (identity.player_id,))
        if pending is None:
            raise DispatchError("没有本群待确认的派遣操作，请先编队、出发或召回预览。")
        if now_ms >= pending["expires_ms"]:
            await session.execute("DELETE FROM dispatch_pending WHERE player_id=?", (identity.player_id,))
            return self.queries.view(identity, "确认已过期", banner="超过2分钟，未扣除费用或器具；请重新预览。")
        payload = json.loads(pending["payload_json"])
        operation = pending["operation"]
        if operation == "team":
            await self._check_slot(session, identity, payload["slot"])
            members = await self._fresh_members(session, identity, payload["members"])
            team = await session.fetch_one(
                "SELECT revision FROM dispatch_teams WHERE player_id=? AND slot=?",
                (identity.player_id, payload["slot"]),
            )
            if (team[0] if team else 0) != payload["revision"]:
                raise DispatchError("队伍已被其他操作修改，请重新预览。")
            await session.execute(
                """INSERT INTO dispatch_teams VALUES(?,?,?,1) ON CONFLICT(player_id,slot)
                DO UPDATE SET member_ids_json=excluded.member_ids_json,revision=revision+1""",
                (identity.player_id, payload["slot"], encode([m["pig_instance_id"] for m in members])),
            )
            view = self.queries.view(
                identity,
                f"第{payload['slot']}队已保存",
                banner="、".join(m["name"] for m in members) or "空闲队伍已清空。",
                hints=(f"/猪猪派遣 出发 {payload['slot']} 青草近郊 4小时",),
            )
        elif operation == "start":
            view = await self._depart(session, identity, payload, now_ms, key)
        elif operation == "recall":
            trip = await self._owned_trip(session, identity, payload["trip_id"])
            if trip["status"] == "traveling":
                trip = await self.repository.finish(session, trip, now_ms=now_ms, recalled=True)
            # 确认期间恰好自然到期，返回已完成回执，不能把它倒改成召回。
            view = await self.queries.trip_detail(session, identity, trip)
            await session.execute("UPDATE dispatch_trips SET viewed=1 WHERE trip_id=?", (trip["trip_id"],))
        else:
            raise DispatchError("待确认派遣操作无法识别。")
        await session.execute("DELETE FROM dispatch_pending WHERE player_id=?", (identity.player_id,))
        return view

    async def _depart(
        self, session: DatabaseSession, identity: CommandIdentity, snapshot: dict[str, Any], now_ms: int, key: str
    ) -> DispatchView:
        slot = snapshot["slot"]
        await self._check_slot(session, identity, slot)
        team = await session.fetch_one(
            "SELECT * FROM dispatch_teams WHERE player_id=? AND slot=?", (identity.player_id, slot)
        )
        if team is None or team["revision"] != snapshot["team_revision"]:
            raise DispatchError("队伍已变化，请重新出发预览。")
        members = await self._fresh_members(session, identity, snapshot["members"])
        if json.loads(team["member_ids_json"]) != [m["pig_instance_id"] for m in members]:
            raise DispatchError("预览队员与当前队伍不一致，请重新预览。")
        team_bonus(members, REGIONS_BY_ID[snapshot["region_id"]])
        coupon_repo = AchievementCouponRepository()
        current_coupons = await coupon_repo.selected(
            session, identity.player_id, ("dispatch-numeric", "dispatch-visual")
        )
        if current_coupons != snapshot.get("coupons", {}):
            raise DispatchError("成就券选择已变化，请重新预览出发。")
        await self._check_resources(session, identity, snapshot)
        snapshot["coupon_uses"] = []
        for coupon_slot, selected in current_coupons.items():
            if selected["ticket_id"] == "dispatch-bill" and not snapshot.get("original_fee"):
                continue  # 免费近郊不吃掉路费券，仍可选择下一趟付费旅行。
            usage = await coupon_repo.consume(
                session,
                identity.player_id,
                coupon_slot,
                key,
                iso_ms(now_ms),
                expected=selected,
                effect={
                    "coin_saving": snapshot.get("original_fee", snapshot["fee"]) - snapshot["fee"]
                    if selected["ticket_id"] == "dispatch-bill"
                    else 0
                },
            )
            snapshot["coupon_uses"].append(usage)
        now = iso_ms(now_ms)
        # 遗留选择、扣款、器具、旅行和占用全部同事务；失败时无部分领奖或扣费。
        old_choices = await self.repository.claim_old_choices(session, identity.player_id, now_ms)
        trip_id = "D" + uuid4().hex[:10].upper()
        if snapshot["fee"]:
            balance = await self.economy.apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=-snapshot["fee"],
                reason_code="dispatch-departure",
                reason_text="猪猪派遣出发费",
                source_object_type="dispatch",
                source_object_id=trip_id,
                ledger_entry_id=uuid4().hex,
                idempotency_key=key,
                now=now,
            )
            if balance is None:
                raise DispatchError("猪币不足，本次旅行没有出发。")
        if snapshot["tool_id"]:
            changed = await session.execute(
                "UPDATE dispatch_tools SET quantity=quantity-1 WHERE player_id=? AND tool_id=? AND quantity>0",
                (identity.player_id, snapshot["tool_id"]),
            )
            if changed.rowcount != 1:
                raise DispatchError("旅行器具已被其他操作使用，请重新预览。")
        ends_ms = now_ms + snapshot["hours"] * 3600_000
        state = {"primary_units": 0, "supply_units": 0, "events": [], "compass_used": False}
        await session.execute(
            """INSERT INTO dispatch_trips(
            trip_id,player_id,scope_id,slot,starts_ms,ends_ms,snapshot_json,progress_json,random_seed)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                trip_id,
                identity.player_id,
                identity.scope.value,
                slot,
                now_ms,
                ends_ms,
                encode(snapshot),
                encode(state),
                self.seed_factory(),
            ),
        )
        for member in members:
            await session.execute(
                "INSERT INTO asset_occupancies VALUES(?,?,?,?,?,?,?)",
                (
                    member["pig_instance_id"],
                    identity.player_id,
                    identity.scope.value,
                    "dispatch",
                    trip_id,
                    ends_ms,
                    now,
                ),
            )
        await self.repository.fact(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            source_id=trip_id,
            subevent="departed",
            at_ms=now_ms,
            payload={"snapshot": snapshot, "starts_ms": now_ms, "ends_ms": ends_ms},
        )
        return self.queries.start_preview(
            identity, snapshot, now_ms, started=True, trip_id=trip_id, auto_choices=len(old_choices)
        )

    async def _material_change(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        material: str,
        amount: int,
        *,
        key: str,
        kind: str,
        now_ms: int,
    ) -> None:
        await self.repository.materials.change(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            material_id=material,
            delta_units=amount * MATERIAL_SCALE,
            source_kind=kind,
            source_id=key,
            entry_key=f"{key}:{material}",
            now=iso_ms(now_ms),
        )

    async def _craft(
        self, session: DatabaseSession, identity: CommandIdentity, args: dict[str, Any], now_ms: int, key: str
    ) -> DispatchView:
        tool = TOOLS_BY_ID.get(args["tool_id"])
        quantity = args["quantity"]
        if tool is None or not isinstance(quantity, int) or not 1 <= quantity <= 99:
            raise DispatchError("制作数量应为1至99。")
        for material, cost in tool.costs:
            await self._material_change(
                session, identity, material, -cost * quantity, key=key, kind="dispatch-tool-craft", now_ms=now_ms
            )
        await session.execute(
            """INSERT INTO dispatch_tools VALUES(?,?,?) ON CONFLICT(player_id,tool_id)
                              DO UPDATE SET quantity=quantity+excluded.quantity""",
            (identity.player_id, tool.tool_id, quantity),
        )
        await self.repository.fact(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            source_id=key,
            subevent="crafted",
            source_type="material",
            at_ms=now_ms,
            payload={"tool_id": tool.tool_id, "quantity": quantity, "costs": tool.costs},
        )
        return self.queries.view(
            identity,
            "器具制作完成",
            banner=f"{tool.name} ×{quantity}",
            panels=(
                Panel("消耗材料", tuple(Line(MATERIALS[m], f"-{c * quantity}") for m, c in tool.costs)),
                Panel("使用说明", (Line(tool.name, tool.summary),)),
            ),
            hints=("出发命令末尾指定器具和参数；/猪猪派遣 帮助 查看示例。",),
        )

    async def _convert(
        self, session: DatabaseSession, identity: CommandIdentity, args: dict[str, Any], now_ms: int, key: str
    ) -> DispatchView:
        source, target = material_id(args["source"], basic_only=True), material_id(args["target"], basic_only=True)
        quantity = args["quantity"]
        if source == target or not isinstance(quantity, int) or not 1 <= quantity <= 100_000:
            raise DispatchError("转换材料或数量不合法。")
        await self._material_change(
            session, identity, source, -3 * quantity, key=key, kind="material-conversion", now_ms=now_ms
        )
        await self._material_change(
            session, identity, target, quantity, key=key, kind="material-conversion", now_ms=now_ms
        )
        await self.repository.fact(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            source_id=key,
            subevent="converted",
            source_type="material",
            at_ms=now_ms,
            payload={"source": source, "target": target, "quantity": quantity, "rate": 3},
        )
        return self.queries.view(
            identity,
            "材料转换完成",
            panels=(
                Panel(
                    "3:1基础材料转换",
                    (
                        Line(MATERIALS[source], f"-{3 * quantity}"),
                        Line(MATERIALS[target], f"+{quantity}"),
                    ),
                ),
            ),
            hints=("/派遣背包 查看余额。转换不会计入自然采集成绩或生成旅行纪念品。",),
        )

    async def _returns(self, session: DatabaseSession, identity: CommandIdentity) -> DispatchView:
        trips = await session.fetch_all(
            """SELECT * FROM dispatch_trips WHERE player_id=? AND scope_id=? AND status!='traveling' AND viewed=0
            ORDER BY sequence LIMIT 3""",
            (identity.player_id, identity.scope.value),
        )
        panels = []
        for trip in trips:
            snapshot, state = json.loads(trip["snapshot_json"]), json.loads(trip["progress_json"])
            panels.append(
                Panel(
                    f"第{trip['slot']}队 · {REGIONS_BY_ID[snapshot['region_id']].name}",
                    reward_lines(state.get("rewards", [])),
                    f"{'平安归来' if trip['status'] == 'completed' else '提前召回'} · 有效{state['settled_hours']}h · "
                    f"奇遇{len(state['events'])}次 · /派遣游记 {trip['trip_id']}",
                )
            )
            if state.get("achievement_story"):
                story = state["achievement_story"]
                panels.append(Panel(story["title"], (Line(story["region"], story["text"], "原创旅行纪念"),)))
            if snapshot.get("coupon_uses"):
                panels.append(
                    Panel(
                        f"第{trip['slot']}队 · 成就券记录",
                        tuple(Line(c["name"], f"使用后剩余{c['remaining']}张") for c in snapshot["coupon_uses"]),
                    )
                )
            await session.execute("UPDATE dispatch_trips SET viewed=1 WHERE trip_id=?", (trip["trip_id"],))
        return self.queries.view(
            identity,
            "欢迎回家！" if trips else "暂时没有新的返程",
            panels=tuple(panels),
            banner="猪猪已解除占用，材料已自动入账。这里仅展示收获，不会再次发奖。",
            hints=(
                "/派遣奇遇 处理罗盘候选；/派遣背包 看当前余额。",
                "/猪猪派遣 返程 查看下一批；历史记录始终可在 /派遣游记 查阅。",
            ),
        )
