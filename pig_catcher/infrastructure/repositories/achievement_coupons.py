"""Eight purpose-specific coupons, separate from catch/cook effect queues.

Selection reserves nothing. Successful business confirmation atomically consumes
inventory and records an immutable source binding. Previews cannot spend coupons.
"""

from __future__ import annotations

import json

from ...domain.activity_achievements import ACTIVITY_REWARDS
from ...domain.errors import DomainValidationError
from .achievements import AchievementRepository

SLOTS = {
    "dispatch-bill": "dispatch-numeric",
    "dispatch-luggage": "dispatch-numeric",
    "dispatch-story": "dispatch-visual",
    "tour-steady-stage": "tour-stage",
    "tour-encore-photo": "tour-visual",
    "training-rebate": "battle-training",
    "battle-banner": "battle-visual",
}


class AchievementCouponRepository:
    async def preview(self, session, player_id: str, slots=()) -> list[dict]:
        rows = await session.fetch_all(
            "SELECT s.slot,s.ticket_id,i.quantity FROM achievement_coupon_selection s "
            "JOIN achievement_reward_inventory i ON i.player_id=s.player_id "
            "AND i.reward_type='ticket' AND i.reward_id=s.ticket_id WHERE s.player_id=? ORDER BY s.slot",
            (player_id,),
        )
        return [
            {"ticket_id": r["ticket_id"], "quantity": r["quantity"], **ACTIVITY_REWARDS[r["ticket_id"]]}
            for r in rows
            if not slots or r["slot"] in slots
        ]

    async def selected(self, session, player_id: str, slots=()) -> dict:
        rows = await session.fetch_all("SELECT * FROM achievement_coupon_selection WHERE player_id=?", (player_id,))
        return {
            r["slot"]: {"ticket_id": r["ticket_id"], "selected_at": r["selected_at"]}
            for r in rows
            if not slots or r["slot"] in slots
        }

    async def select(self, session, player_id: str, ticket_id: str, now: str) -> None:
        if ticket_id not in SLOTS:
            raise DomainValidationError("该成就券不能放入活动槽。")
        row = await session.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory "
            "WHERE player_id=? AND reward_type='ticket' AND reward_id=?",
            (player_id, ticket_id),
        )
        if not row or row[0] < 1:
            raise DomainValidationError("你没有可用的" + ACTIVITY_REWARDS[ticket_id]["name"] + "。")
        await session.execute(
            """INSERT INTO achievement_coupon_selection VALUES(?,?,?,?) ON CONFLICT(player_id,slot)
            DO UPDATE SET ticket_id=excluded.ticket_id,selected_at=excluded.selected_at""",
            (player_id, SLOTS[ticket_id], ticket_id, now),
        )

    async def consume(
        self,
        session,
        player_id: str,
        slot: str,
        source: str,
        now: str,
        *,
        expected: dict | None = None,
        effect: dict | None = None,
    ) -> dict:
        existing = await session.fetch_one(
            "SELECT effect_json FROM achievement_coupon_uses WHERE player_id=? AND source_id=? AND slot=?",
            (player_id, source, slot),
        )
        if existing:
            return json.loads(existing[0])
        selected = (await self.selected(session, player_id, [slot])).get(slot)
        if expected is not None and selected != expected:
            raise DomainValidationError("成就券选择发生变化，请重新预览确认。")
        if selected is None:
            return {}
        ticket = selected["ticket_id"]
        if not await AchievementRepository().consume_reward(
            session, player_id=player_id, reward_type="ticket", reward_id=ticket, quantity=1, now=now
        ):
            raise DomainValidationError("所选成就券已不足，本次操作未扣除任何资源。")
        remaining = await session.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory "
            "WHERE player_id=? AND reward_type='ticket' AND reward_id=?",
            (player_id, ticket),
        )
        result = {
            "ticket_id": ticket,
            "name": ACTIVITY_REWARDS[ticket]["name"],
            "remaining": remaining[0],
            **(effect or {}),
        }
        await session.execute(
            "INSERT INTO achievement_coupon_uses VALUES(?,?,?,?,?,?,?)",
            (
                f"{source}:{player_id}:{slot}",
                player_id,
                ticket,
                slot,
                source,
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        await session.execute(
            "DELETE FROM achievement_coupon_selection WHERE player_id=? AND slot=?", (player_id, slot)
        )
        return result

    @staticmethod
    def description(plan: dict) -> str:
        return "；".join(ACTIVITY_REWARDS[item["ticket_id"]]["name"] for item in plan.values()) or "无"
