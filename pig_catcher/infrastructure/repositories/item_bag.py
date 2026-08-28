"""道具库存只读投影与奖励券账本；不拥有事务、不重复建库存。"""

from __future__ import annotations

import hashlib
import json

from ...domain.activity_achievements import ACTIVITY_REWARDS
from ...domain.battle_catalog import TOOLS as BATTLE_TOOLS
from ...domain.dispatch import TOOLS as DISPATCH_TOOLS
from ...domain.errors import DomainValidationError, ReceiptConflictError
from ...domain.gameplay import ITEMS_BY_ID
from ...domain.item_bag import COUPONS, REWARD_NAMES, BagEntry, coupon_definition
from ...domain.tour_catalog import TOOLS as TOUR_TOOLS
from ..database import DatabaseSession
from .achievements import AchievementRepository


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ItemBagRepository:
    async def quantity(self, session: DatabaseSession, player_id: str, coupon_id: str) -> int:
        row = await session.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory "
            "WHERE player_id=? AND reward_type='ticket' AND reward_id=?",
            (player_id, coupon_id),
        )
        return int(row[0]) if row else 0

    async def grant_coupon(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        coupon_id: str,
        quantity: int,
        source_id: str,
        now: str,
        source_kind: str = "food",
        source_receipt_id: str = "",
    ) -> dict[str, object]:
        """在调用方事务中幂等发券；同来源重放返回原结果，参数变动拒绝。"""

        definition = coupon_definition(coupon_id)
        if type(quantity) is not int or not 1 <= quantity <= 10_000:
            raise DomainValidationError("奖励券数量必须是1至10000的整数。")
        if not source_id or len(source_id) > 512 or not source_kind or len(source_kind) > 80:
            raise DomainValidationError("奖励券必须有有效、稳定的发放来源。")
        player = await session.fetch_one("SELECT scope_id FROM players WHERE player_id=?", (player_id,))
        if player is None or player[0] != scope_id:
            raise DomainValidationError("只能在玩家所属的当前群发放奖励券。")
        coupon_id = definition.coupon_id
        existing = await session.fetch_one(
            "SELECT quantity,scope_id,source_receipt_id,result_json FROM reward_coupon_grants "
            "WHERE player_id=? AND source_kind=? AND source_id=? AND coupon_id=?",
            (player_id, source_kind, source_id, coupon_id),
        )
        if existing is not None:
            if (
                existing["quantity"] != quantity
                or existing["scope_id"] != scope_id
                or existing["source_receipt_id"] != source_receipt_id
            ):
                raise ReceiptConflictError("同一来源的奖励券发放参数发生变化。")
            return json.loads(existing["result_json"])
        await AchievementRepository().grant_reward(
            session, player_id=player_id, reward_type="ticket", reward_id=coupon_id, quantity=quantity, now=now
        )
        result = {
            "coupon_id": coupon_id,
            "name": definition.name,
            "quantity": quantity,
            "remaining": await self.quantity(session, player_id, coupon_id),
        }
        grant_key = (
            "reward-coupon:"
            + hashlib.sha256(
                _json({"player": player_id, "kind": source_kind, "source": source_id, "coupon": coupon_id}).encode(
                    "utf-8"
                )
            ).hexdigest()
        )
        await session.execute(
            "INSERT INTO reward_coupon_grants("
            "grant_key,player_id,scope_id,coupon_id,quantity,source_kind,source_id,source_receipt_id,result_json,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                grant_key,
                player_id,
                scope_id,
                coupon_id,
                quantity,
                source_kind,
                source_id,
                source_receipt_id,
                _json(result),
                now,
            ),
        )
        return result

    async def consume_coupon(self, session: DatabaseSession, *, player_id: str, coupon_id: str, now: str) -> int:
        if not await AchievementRepository().consume_reward(
            session, player_id=player_id, reward_type="ticket", reward_id=coupon_id, quantity=1, now=now
        ):
            raise DomainValidationError(f"你没有可用的{REWARD_NAMES.get(coupon_id, '奖励券')}，本次没有变更资产。")
        return await self.quantity(session, player_id, coupon_id)

    async def record_use(
        self,
        session: DatabaseSession,
        *,
        key: str,
        player_id: str,
        scope_id: str,
        coupon_id: str,
        operation: str,
        detail: dict[str, object],
        now: str,
    ) -> None:
        await session.execute(
            "INSERT INTO reward_coupon_uses VALUES(?,?,?,?,?,?,?)",
            (key, player_id, scope_id, coupon_id, operation, _json(detail), now),
        )

    async def entries(self, session: DatabaseSession, *, player_id: str) -> tuple[BagEntry, ...]:
        entries: list[BagEntry] = []
        items = await session.fetch_all(
            "SELECT inventory.item_id,inventory.quantity,COALESCE(SUM(armed.remaining_uses),0) AS armed_uses,"
            "COUNT(CASE WHEN armed.remaining_uses>0 THEN 1 END) AS armed_slots "
            "FROM item_inventory inventory LEFT JOIN armed_items armed "
            "ON armed.player_id=inventory.player_id AND armed.item_id=inventory.item_id "
            "WHERE inventory.player_id=? AND inventory.quantity>0 "
            "GROUP BY inventory.item_id,inventory.quantity ORDER BY inventory.item_id",
            (player_id,),
        )
        for row in items:
            item = ITEMS_BY_ID.get(row["item_id"])
            quantity, reserved, equipped = int(row["quantity"]), int(row["armed_uses"]), int(row["armed_slots"])
            if reserved > quantity:
                raise DomainValidationError("道具排队数量超过实际库存，请联系管理员检查；未自动改动库存。")
            entries.append(
                BagEntry(
                    row["item_id"],
                    "商城道具",
                    item.display_name if item else "旧版道具",
                    quantity,
                    quantity - reserved,
                    f"已装备 {equipped} · 排队 {reserved - equipped}",
                    item.effect_summary if item else "保留原有库存，请联系管理员核对定义。",
                )
            )

        rows = await session.fetch_all(
            "SELECT reward_type,reward_id,quantity FROM achievement_reward_inventory "
            "WHERE player_id=? AND reward_type IN('ticket','chest') AND quantity>0",
            (player_id,),
        )
        inventory = {(row["reward_type"], row["reward_id"]): int(row["quantity"]) for row in rows}
        active = await session.fetch_all(
            "SELECT ticket_id,SUM(granted_uses-consumed_uses) AS remaining FROM achievement_ticket_effects "
            "WHERE player_id=? AND consumed_uses<granted_uses GROUP BY ticket_id",
            (player_id,),
        )
        activated = {row["ticket_id"]: int(row["remaining"]) for row in active}
        selected_rows = await session.fetch_all(
            "SELECT ticket_id,COUNT(*) AS amount FROM achievement_coupon_selection "
            "WHERE player_id=? GROUP BY ticket_id",
            (player_id,),
        )
        selections = {row["ticket_id"]: int(row["amount"]) for row in selected_rows}
        keys = set(inventory) | {("ticket", key) for key in activated}
        for reward_type, reward_id in sorted(keys):
            quantity = inventory.get((reward_type, reward_id), 0)
            # 活动券选择尚未扣库存；旧成就券激活已从库存移出，因此后者单独并入总数。
            in_effect = activated.get(reward_id, 0) if reward_type == "ticket" else 0
            selected = min(quantity, selections.get(reward_id, 0)) if reward_type == "ticket" else 0
            if reward_id in COUPONS:
                description = COUPONS[reward_id].summary
            elif reward_id in ACTIVITY_REWARDS:
                description = ACTIVITY_REWARDS[reward_id].get("effect", "每份兑换1份指定范围内的材料。")
            elif reward_id == "identifier-reforge":
                description = "旧券继续有效：/重铸编号 猪猪 旧编号 新编号（美食同理）。"
            elif reward_id == "regular-five-star-memorial":
                description = "使用入口：/领取成就纪念猪 猪名；只兑换公共五星纪念猪。"
            elif reward_type == "chest":
                description = "使用入口：/打开成就宝箱 奖励名称。"
            else:
                description = "使用入口：/使用成就券 " + REWARD_NAMES.get(reward_id, reward_id)
            entries.append(
                BagEntry(
                    reward_id,
                    "奖励券与自选份",
                    REWARD_NAMES.get(reward_id, f"旧版奖励·{reward_id}"),
                    quantity + in_effect,
                    quantity - selected,
                    f"待命 {selected} · 已激活 {in_effect}",
                    description,
                )
            )

        # 三系统器具在出发/应战时已消费；计划和预览不会产生第二份库存。
        groups = (
            ("dispatch_tools", "派遣器具", {t.tool_id: (t.name, t.summary) for t in DISPATCH_TOOLS}),
            ("tour_tools", "巡演器具", {t.tool_id: (t.name, t.summary) for t in TOUR_TOOLS}),
            ("battle_tools", "对战器具", {t.tool_id: (t.name, t.description) for t in BATTLE_TOOLS}),
        )
        for table, category, definitions in groups:
            rows = await session.fetch_all(
                f"SELECT tool_id,quantity FROM {table} WHERE player_id=? AND quantity>0", (player_id,)
            )
            for row in rows:
                name, summary = definitions.get(row["tool_id"], ("旧版器具", "保留原有库存。"))
                entries.append(
                    BagEntry(
                        row["tool_id"],
                        category,
                        name,
                        int(row["quantity"]),
                        int(row["quantity"]),
                        "预览不占库存",
                        summary,
                    )
                )
        return tuple(entries)
