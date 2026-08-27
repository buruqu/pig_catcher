"""对战仓储；调用者拥有事务，不提交、不渲染、不调用网络。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ...domain.battle import dumps, loads
from ...domain.battle_catalog import BATTLE_VERSION, FIGHTERS_BY_TEMPLATE, MATERIAL_IDS, TOOLS_BY_ID, BattleError
from ...domain.dispatch import MATERIAL_SCALE, safe_display_name
from ...domain.models import CommandIdentity
from ...domain.selectors import parse_asset_selector
from ..database import DatabaseSession
from .achievement_coupons import AchievementCouponRepository
from .dispatch import DispatchRepository, iso_ms, timestamp_ms
from .economy import EconomyRepository
from .materials import MaterialRepository


def beijing_day(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, timezone(timedelta(hours=8))).date().isoformat()


class BattleRepository:
    async def fact(
        self,
        session: DatabaseSession,
        player_id: str,
        scope_id: str,
        source_id: str,
        subevent: str,
        now_ms: int,
        payload: dict,
    ) -> None:
        key = hashlib.sha256(f"{player_id}|battle|{source_id}|{subevent}".encode()).hexdigest()
        encoded = dumps(payload)
        old = await session.fetch_one("SELECT payload_json FROM activity_facts WHERE fact_key=?", (key,))
        if old:
            if old[0] != encoded:
                raise BattleError("对战事实与原操作不一致。")
            return
        await session.execute(
            "INSERT INTO activity_facts VALUES(?,?,?,?,?,?,?,?,?)",
            (key, player_id, scope_id, "battle", source_id, subevent, BATTLE_VERSION, now_ms, encoded),
        )

    async def profile(self, session: DatabaseSession, player_id: str) -> dict:
        await session.execute("INSERT INTO battle_profiles(player_id) VALUES(?) ON CONFLICT DO NOTHING", (player_id,))
        return dict(await session.fetch_one("SELECT * FROM battle_profiles WHERE player_id=?", (player_id,)))

    async def member(self, session: DatabaseSession, player_id: str, pig_id: str, *, available: bool = False) -> dict:
        member = await DispatchRepository().member(session, player_id, pig_id)
        definition = FIGHTERS_BY_TEMPLATE.get(member["template_id"])
        if definition is None:
            raise BattleError("目前只有宿傩猪和五条猪拥有战斗盘。")
        if available:
            if member["locked_trade_id"] or member["busy_purpose"]:
                raise BattleError("战斗猪正被派遣、巡演、对战或交易占用，请先结束对应活动。")
        level = await session.fetch_one("SELECT level FROM battle_training WHERE pig_instance_id=?", (pig_id,))
        member.update(
            fighter_id=definition.fighter_id,
            level=int(level[0]) if level else 0,
            trait_bonus=int((member["size_q"] + member["weight_q"]) / 2 >= 0.75),
        )
        return member

    async def select(self, session: DatabaseSession, identity: CommandIdentity, selector_text: str) -> dict:
        if not selector_text:
            profile = await self.profile(session, identity.player_id)
            if not profile["pig_instance_id"]:
                raise BattleError("请先 /战斗猪 设置 宿傩猪（或五条猪）；+0即可参战。")
            return await self.member(session, identity.player_id, profile["pig_instance_id"], available=True)
        selector = parse_asset_selector(selector_text)
        clause = "AND p.short_code=? COLLATE NOCASE" if selector.short_code else "AND p.is_favorite=0"
        params = [identity.player_id, identity.scope.value, selector.name, "".join(selector.name.split())]
        if selector.short_code:
            params.append(selector.short_code)
        row = await session.fetch_one(
            f"""SELECT p.pig_instance_id FROM pig_instances p WHERE p.owner_player_id=? AND p.scope_id=?
            AND p.state='active' AND p.locked_trade_id IS NULL
            AND (p.display_name_snapshot=? OR REPLACE(REPLACE(p.display_name_snapshot,' ',''),'　','')=? COLLATE NOCASE)
            AND NOT EXISTS(SELECT 1 FROM asset_occupancies o WHERE o.pig_instance_id=p.pig_instance_id)
            {clause} ORDER BY p.official_value,p.acquired_at,p.pig_instance_id LIMIT 1""",
            params,
        )
        if not row:
            raise BattleError("找不到空闲战斗猪；名称选最低价值非收藏猪，收藏猪请明确输入全名#编号。")
        return await self.member(session, identity.player_id, row[0], available=True)

    async def protect(self, session: DatabaseSession, player_id: str, scope_id: str, pig_id: str) -> None:
        await session.execute(
            """INSERT INTO battle_protections VALUES(?,?,?,1) ON CONFLICT(pig_instance_id)
            DO UPDATE SET player_id=excluded.player_id,scope_id=excluded.scope_id,protected=1""",
            (pig_id, player_id, scope_id),
        )

    async def snapshot(self, session: DatabaseSession, player_id: str, scope_id: str, now_ms: int) -> dict:
        row = await session.fetch_one("SELECT * FROM players WHERE player_id=? AND scope_id=?", (player_id, scope_id))
        if not row:
            raise BattleError("只能挑战本群已设置战斗猪的另一位玩家。")
        await DispatchRepository().settle_elapsed(session, player_id, iso_ms(now_ms))
        profile = await self.profile(session, player_id)
        if not profile["pig_instance_id"]:
            raise BattleError(f"{safe_display_name(row['display_name'], row['platform_user_id'])}还没有设置战斗猪。")
        member = await self.member(session, player_id, profile["pig_instance_id"], available=True)
        tool = profile["tool_id"]
        if tool:
            balance = await session.fetch_one(
                "SELECT quantity FROM battle_tools WHERE player_id=? AND tool_id=?", (player_id, tool)
            )
            if tool not in TOOLS_BY_ID or not balance or balance[0] < 1:
                raise BattleError("已选择的对战器具不足，请先制作或 /战斗猪 器具 无。")
        return {
            **member,
            "player_id": player_id,
            "player_name": safe_display_name(row["display_name"], row["platform_user_id"]),
            "tool_id": tool,
            "profile_revision": profile["revision"],
            "achievement_coupons": await AchievementCouponRepository().selected(session, player_id, ("battle-visual",)),
            "coupon_preview": await AchievementCouponRepository().preview(session, player_id, ("battle-visual",)),
        }

    @staticmethod
    def fingerprint(snapshot: dict) -> dict:
        return {
            key: snapshot[key]
            for key in (
                "pig_instance_id",
                "template_id",
                "level",
                "trait_bonus",
                "size_value",
                "weight_value",
                "tool_id",
                "profile_revision",
                "achievement_coupons",
            )
        }

    async def spend(
        self, session: DatabaseSession, identity: CommandIdentity, costs: dict, key: str, now_ms: int
    ) -> None:
        amounts = {MATERIAL_IDS.get(material, material): amount for material, amount in costs.items()}
        coins = amounts.pop("coins", 0)
        if coins:
            balance = await EconomyRepository().apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=-coins,
                reason_code="battle-training",
                reason_text="战斗猪养成",
                source_object_type="battle",
                source_object_id=key,
                ledger_entry_id=uuid4().hex,
                idempotency_key=f"battle-coins:{key}",
                now=iso_ms(now_ms),
            )
            if balance is None:
                raise BattleError("猪币不足；本次未消耗任何资源。")
        for material, amount in amounts.items():
            await MaterialRepository().change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                material_id=material,
                delta_units=-amount * MATERIAL_SCALE,
                source_kind="battle-spend",
                source_id=key,
                entry_key=f"battle:{key}:{material}",
                now=iso_ms(now_ms),
            )

    async def tool_change(
        self,
        session: DatabaseSession,
        player_id: str,
        tool: str,
        delta: int,
        *,
        reason: str,
        source: str,
        key: str,
        now_ms: int,
    ) -> None:
        old = await session.fetch_one("SELECT * FROM battle_tool_ledger WHERE entry_key=?", (key,))
        if old:
            if (old["player_id"], old["tool_id"], old["delta"], old["reason"], old["source_id"]) != (
                player_id,
                tool,
                delta,
                reason,
                source,
            ):
                raise BattleError("器具账本与原操作不一致。")
            return
        row = await session.fetch_one(
            "SELECT quantity FROM battle_tools WHERE player_id=? AND tool_id=?", (player_id, tool)
        )
        after = (int(row[0]) if row else 0) + delta
        if after < 0:
            raise BattleError("对战器具数量不足。")
        await session.execute(
            """INSERT INTO battle_tools VALUES(?,?,?) ON CONFLICT(player_id,tool_id)
            DO UPDATE SET quantity=excluded.quantity""",
            (player_id, tool, after),
        )
        await session.execute(
            "INSERT INTO battle_tool_ledger VALUES(?,?,?,?,?,?,?,?)",
            (key, player_id, tool, delta, after, reason, source, now_ms),
        )

    async def active(self, session: DatabaseSession, scope_id: str) -> dict | None:
        row = await session.fetch_one(
            "SELECT * FROM battle_matches WHERE scope_id=? AND status IN('pending','active')", (scope_id,)
        )
        return dict(row) if row else None

    async def expire_scope(self, session: DatabaseSession, scope_id: str, now: str) -> None:
        now_ms = timestamp_ms(now)
        match = await self.active(session, scope_id)
        if match and match["expires_ms"] <= now_ms:
            await self.finish(session, match, loads(match["state_json"]), "expired", now_ms)

    async def finish(
        self,
        session: DatabaseSession,
        match: dict,
        state: dict,
        status: str,
        now_ms: int,
        *,
        winner_id: str | None = None,
    ) -> None:
        if match["status"] not in {"pending", "active"}:
            raise BattleError("对战已经结束。")
        state["status"] = status
        await session.execute(
            """UPDATE battle_matches SET status=?,state_json=?,winner_id=?,finished_ms=? WHERE battle_id=?""",
            (status, dumps(state), winner_id, now_ms, match["battle_id"]),
        )
        if match["status"] == "active":
            for side in state["sides"]:
                snapshot = side["snapshot"]
                tool = snapshot.get("tool_id", "")
                if tool and not side["tool_used"]:
                    await self.tool_change(
                        session,
                        snapshot["player_id"],
                        tool,
                        1,
                        reason="unused-refund",
                        source=match["battle_id"],
                        key=f"battle-refund:{match['battle_id']}:{snapshot['player_id']}",
                        now_ms=now_ms,
                    )
        await session.execute(
            "DELETE FROM asset_occupancies WHERE purpose='battle' AND activity_id=?", (match["battle_id"],)
        )
        for pid in (match["initiator_id"], match["opponent_id"]):
            await self.fact(
                session,
                pid,
                match["scope_id"],
                match["battle_id"],
                "finished",
                now_ms,
                {
                    "status": status,
                    "natural_end": status == "completed",
                    "winner_id": winner_id,
                    "state": state,
                    "quota_refund": False,
                },
            )
        if status == "completed":
            loser = match["opponent_id"] if winner_id == match["initiator_id"] else match["initiator_id"]
            await session.execute(
                "INSERT INTO battle_loot(battle_id,actor_id,recipient_id,scope_id,created_ms) VALUES(?,?,?,?,?)",
                (match["battle_id"], loser, winner_id, match["scope_id"], now_ms),
            )
