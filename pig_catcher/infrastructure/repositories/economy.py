"""第四轮做菜、美食、商城、售卖和猪币账本仓储。"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.models import AssetSelector
from ..database import DatabaseSession
from .batch_safety import (
    highest_collaboration_pig_ids_per_template,
    highest_instance_ids_per_template,
)

_FOOD_INVENTORY_ORDER_SQL = {
    "获得时间": "instance.acquired_at DESC, instance.food_instance_id",
    "品质": "instance.rarity DESC, instance.acquired_at DESC",
    "价值": "instance.official_value DESC, instance.acquired_at DESC",
    "份量": "instance.portion_weight DESC, instance.acquired_at DESC",
    "名称": "instance.display_name_snapshot, instance.acquired_at DESC",
}


class EconomyRepository:
    """Read and mutate fourth-round state without owning transactions."""

    async def list_drawable_food_templates(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        rarity: int,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT template.*
            FROM food_templates AS template
            LEFT JOIN scope_food_templates AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            WHERE template.enabled = 1
              AND template.rarity = ?
              AND (
                  template.scope_type = 'common'
                  OR (
                      template.scope_type = 'group'
                      AND allowed.authorized = 1
                      AND allowed.consent_status = 'granted'
                  )
              )
            ORDER BY template.template_id
            """,
            (scope_id, rarity),
        )
        return [dict(row) for row in rows]

    async def catch_quota_bonuses(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        now: str,
    ) -> tuple[int, int]:
        """Return the permanent bonus and the currently active weekly bonus."""

        row = await session.fetch_one(
            """
            SELECT
                permanent_bonus,
                CASE
                    WHEN weekly_expires_at IS NOT NULL AND weekly_expires_at > ?
                    THEN weekly_bonus
                    ELSE 0
                END AS active_weekly_bonus
            FROM player_catch_quota_bonuses
            WHERE player_id = ?
            """,
            (now, player_id),
        )
        if row is None:
            return 0, 0
        return int(row["permanent_bonus"]), int(row["active_weekly_bonus"])

    async def grant_weekly_catch_bonus(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        source_food_instance_id: str,
        count: int,
        expires_at: str,
        now: str,
    ) -> bool:
        """Grant one weekly bonus only when no unexpired weekly grant exists."""

        cursor = await session.execute(
            """
            INSERT INTO player_catch_quota_bonuses(
                player_id, permanent_bonus, weekly_bonus, weekly_expires_at,
                weekly_source_food_instance_id, permanent_source_food_instance_id,
                created_at, updated_at
            )
            VALUES (?, 0, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                weekly_bonus = excluded.weekly_bonus,
                weekly_expires_at = excluded.weekly_expires_at,
                weekly_source_food_instance_id = excluded.weekly_source_food_instance_id,
                updated_at = excluded.updated_at
            WHERE player_catch_quota_bonuses.weekly_expires_at IS NULL
               OR player_catch_quota_bonuses.weekly_expires_at <= ?
            """,
            (
                player_id,
                count,
                expires_at,
                source_food_instance_id,
                now,
                now,
                now,
            ),
        )
        return cursor.rowcount == 1

    async def increment_permanent_catch_bonus(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        source_food_instance_id: str,
        count: int,
        max_bonus: int,
        now: str,
    ) -> int | None:
        """Increment the permanent bonus, returning None when already capped."""

        cursor = await session.execute(
            """
            INSERT INTO player_catch_quota_bonuses(
                player_id, permanent_bonus, weekly_bonus, weekly_expires_at,
                weekly_source_food_instance_id, permanent_source_food_instance_id,
                created_at, updated_at
            )
            VALUES (?, ?, 0, NULL, NULL, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                permanent_bonus = MIN(?, player_catch_quota_bonuses.permanent_bonus + ?),
                permanent_source_food_instance_id = excluded.permanent_source_food_instance_id,
                updated_at = excluded.updated_at
            WHERE player_catch_quota_bonuses.permanent_bonus < ?
            """,
            (
                player_id,
                count,
                source_food_instance_id,
                now,
                now,
                max_bonus,
                count,
                max_bonus,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = await session.fetch_one(
            "SELECT permanent_bonus FROM player_catch_quota_bonuses WHERE player_id = ?",
            (player_id,),
        )
        return int(row["permanent_bonus"]) if row is not None else None

    async def six_star_progress_stacks(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> int:
        """Return the player's current permanent six-star progress stacks."""

        row = await session.fetch_one(
            "SELECT stacks FROM player_six_star_progress WHERE player_id = ?",
            (player_id,),
        )
        return int(row["stacks"]) if row is not None else 0

    async def increment_six_star_progress(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        source_food_instance_id: str,
        max_stacks: int,
        now: str,
    ) -> int | None:
        """Increment one stack, returning the new value; None when already capped."""

        cursor = await session.execute(
            """
            INSERT INTO player_six_star_progress(
                player_id, stacks, source_food_instance_id, created_at, updated_at
            )
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                stacks = MIN(?, player_six_star_progress.stacks + 1),
                source_food_instance_id = excluded.source_food_instance_id,
                updated_at = excluded.updated_at
            WHERE player_six_star_progress.stacks < ?
            """,
            (
                player_id,
                source_food_instance_id,
                now,
                now,
                max_stacks,
                max_stacks,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = await session.fetch_one(
            "SELECT stacks FROM player_six_star_progress WHERE player_id = ?",
            (player_id,),
        )
        return int(row["stacks"]) if row is not None else None

    async def get_upgrade_levels(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> dict[str, int]:
        rows = await session.fetch_all(
            """
            SELECT upgrade_type, level
            FROM upgrades
            WHERE player_id = ?
            """,
            (player_id,),
        )
        levels = {"feed": 0, "cookware": 0}
        for row in rows:
            levels[str(row["upgrade_type"])] = int(row["level"])
        return levels

    async def list_active_food_effects(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        now: str,
    ) -> list[dict[str, object]]:
        """Return queued, non-expired effects in FIFO order."""

        rows = await session.fetch_all(
            """
            SELECT
                effect.effect_entry_id, effect.effect_id, effect.params_json,
                effect.granted_uses, effect.consumed_uses,
                effect.expires_at, effect.created_at,
                source.rarity AS source_food_rarity,
                source.display_name_snapshot AS source_food_name
            FROM player_food_effects AS effect
            JOIN food_instances AS source
              ON source.food_instance_id = effect.source_food_instance_id
            WHERE effect.player_id = ?
              AND effect.consumed_uses < effect.granted_uses
              AND (effect.expires_at IS NULL OR effect.expires_at > ?)
            ORDER BY effect.created_at, effect.effect_entry_id
            """,
            (player_id, now),
        )
        return [dict(row) for row in rows]

    async def players_in_scope(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
    ) -> list[dict[str, object]]:
        """Return every registered player in deterministic reward order."""

        rows = await session.fetch_all(
            """
            SELECT player_id, display_name, coin_balance, created_at
            FROM players
            WHERE scope_id = ?
            ORDER BY created_at, player_id
            """,
            (scope_id,),
        )
        return [dict(row) for row in rows]

    async def insert_group_food_effect(
        self,
        session: DatabaseSession,
        *,
        group_effect_entry_id: str,
        scope_id: str,
        source_player_id: str,
        source_food_instance_id: str,
        effect_id: str,
        params_json: str,
        granted_uses_per_player: int,
        starts_at: str,
        expires_at: str,
        now: str,
    ) -> None:
        """Persist one independently expiring six-star group effect."""

        await session.execute(
            """
            INSERT INTO group_food_effects(
                group_effect_entry_id, scope_id, source_player_id,
                source_food_instance_id, effect_id, params_json,
                granted_uses_per_player, starts_at, expires_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_effect_entry_id,
                scope_id,
                source_player_id,
                source_food_instance_id,
                effect_id,
                params_json,
                granted_uses_per_player,
                starts_at,
                expires_at,
                now,
                now,
            ),
        )

    async def list_active_group_food_effects(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        player_id: str,
        now: str,
    ) -> list[dict[str, object]]:
        """Return active group effects with this player's independent usage."""

        rows = await session.fetch_all(
            """
            SELECT
                effect.group_effect_entry_id,
                effect.effect_id,
                effect.params_json,
                effect.granted_uses_per_player,
                COALESCE(usage.consumed_uses, 0) AS consumed_uses,
                source.platform_user_id AS source_user_id,
                source.display_name AS source_display_name,
                effect.starts_at,
                effect.expires_at,
                effect.created_at
            FROM group_food_effects AS effect
            JOIN players AS source
              ON source.player_id = effect.source_player_id
            LEFT JOIN group_food_effect_usage AS usage
              ON usage.group_effect_entry_id = effect.group_effect_entry_id
             AND usage.player_id = ?
            WHERE effect.scope_id = ?
              AND effect.starts_at <= ?
              AND effect.expires_at > ?
            ORDER BY effect.created_at, effect.group_effect_entry_id
            """,
            (player_id, scope_id, now, now),
        )
        return [dict(row) for row in rows]

    async def consume_group_food_effect_use(
        self,
        session: DatabaseSession,
        *,
        group_effect_entry_id: str,
        player_id: str,
        now: str,
    ) -> None:
        """Consume one per-player group-effect use or rollback the catch."""

        await session.execute(
            """
            INSERT OR IGNORE INTO group_food_effect_usage(
                group_effect_entry_id, player_id, consumed_uses, updated_at
            )
            VALUES (?, ?, 0, ?)
            """,
            (group_effect_entry_id, player_id, now),
        )
        cursor = await session.execute(
            """
            UPDATE group_food_effect_usage
            SET consumed_uses = consumed_uses + 1,
                updated_at = ?
            WHERE group_effect_entry_id = ?
              AND player_id = ?
              AND consumed_uses < (
                  SELECT granted_uses_per_player
                  FROM group_food_effects
                  WHERE group_effect_entry_id = ?
                    AND starts_at <= ?
                    AND expires_at > ?
              )
            """,
            (
                now,
                group_effect_entry_id,
                player_id,
                group_effect_entry_id,
                now,
                now,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("全群美食效果次数已变化，本次抓猪未结算。")

    async def insert_food_effect(
        self,
        session: DatabaseSession,
        *,
        effect_entry_id: str,
        player_id: str,
        source_food_instance_id: str,
        effect_id: str,
        params_json: str,
        granted_uses: int,
        expires_at: str | None,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                effect_entry_id,
                player_id,
                source_food_instance_id,
                effect_id,
                params_json,
                granted_uses,
                expires_at,
                now,
                now,
            ),
        )

    async def consume_food_effects(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        effect_entry_ids: tuple[str, ...],
        now: str,
    ) -> None:
        """Consume one use from every selected effect or fail the transaction."""

        for effect_entry_id in effect_entry_ids:
            cursor = await session.execute(
                """
                UPDATE player_food_effects
                SET consumed_uses = consumed_uses + 1,
                    updated_at = ?
                WHERE effect_entry_id = ?
                  AND player_id = ?
                  AND consumed_uses < granted_uses
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (now, effect_entry_id, player_id, now),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("待触发美食效果状态已变化，本次操作未结算。")

    async def extra_catch_grants(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        now: str,
    ) -> tuple[int, int]:
        """Return today's total granted and already consumed extra catches."""

        row = await session.fetch_one(
            """
            SELECT
                COALESCE(SUM(granted_uses), 0) AS granted,
                COALESCE(SUM(consumed_uses), 0) AS consumed
            FROM player_food_effects
            WHERE player_id = ?
              AND effect_id = 'extra-catches'
              AND expires_at > ?
            """,
            (player_id, now),
        )
        if row is None:
            return 0, 0
        return int(row["granted"]), int(row["consumed"])

    async def consume_extra_catch(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        now: str,
    ) -> str:
        """Consume the oldest available extra-catch use."""

        row = await session.fetch_one(
            """
            SELECT effect_entry_id
            FROM player_food_effects
            WHERE player_id = ?
              AND effect_id = 'extra-catches'
              AND consumed_uses < granted_uses
              AND expires_at > ?
            ORDER BY created_at, effect_entry_id
            LIMIT 1
            """,
            (player_id, now),
        )
        if row is None:
            raise RuntimeError("额外抓猪次数已不可用，本次抓猪未结算。")
        effect_entry_id = str(row["effect_entry_id"])
        await self.consume_food_effects(
            session,
            player_id=player_id,
            effect_entry_ids=(effect_entry_id,),
            now=now,
        )
        return effect_entry_id

    async def find_active_foods(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        selector: AssetSelector,
        prefer_highest: bool = False,
    ) -> list[dict[str, object]]:
        # 精确匹配优先；若未命中再按“去空白 + 忽略英文大小写”的紧凑名兜底。
        compact_name = "".join(selector.name.split())
        parameters: list[object] = [player_id, selector.name, compact_name]
        short_code_clause = ""
        if selector.short_code is not None:
            short_code_clause = "AND instance.short_code COLLATE NOCASE = ?"
            parameters.append(selector.short_code)
        order_sql = (
            "instance.official_value DESC, instance.acquired_at, instance.food_instance_id"
            if prefer_highest
            else (
                "instance.is_favorite, instance.official_value, "
                "instance.acquired_at, instance.food_instance_id"
            )
        )
        limit = 1 if prefer_highest else 20
        rows = await session.fetch_all(
            f"""
            SELECT instance.food_instance_id
            FROM food_instances AS instance
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              AND instance.locked_trade_id IS NULL
              AND (
                  instance.display_name_snapshot = ?
                  OR REPLACE(REPLACE(instance.display_name_snapshot, ' ', ''), '　', '')
                     = ? COLLATE NOCASE
              )
              {short_code_clause}
            ORDER BY {order_sql}
            LIMIT {limit}
            """,
            parameters,
        )
        results: list[dict[str, object]] = []
        for row in rows:
            result = await self.get_food_by_instance_id(
                session,
                food_instance_id=str(row["food_instance_id"]),
            )
            if result is not None:
                results.append(result)
        return results

    async def upsert_pending_food_confirmation(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        food_instance_id: str,
        requested_name: str,
        expires_at: str,
        now: str,
    ) -> None:
        """Replace this player's pending last-copy food confirmation."""

        await session.execute(
            """
            INSERT INTO pending_food_confirmations(
                player_id, food_instance_id, requested_name,
                expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                food_instance_id = excluded.food_instance_id,
                requested_name = excluded.requested_name,
                expires_at = excluded.expires_at,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                player_id,
                food_instance_id,
                requested_name,
                expires_at,
                now,
                now,
            ),
        )

    async def get_pending_food_confirmation(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT
                pending.player_id,
                pending.food_instance_id,
                pending.requested_name,
                pending.expires_at,
                pending.created_at,
                food.short_code,
                food.display_name_snapshot,
                food.rarity,
                food.official_value,
                food.state,
                food.locked_trade_id
            FROM pending_food_confirmations AS pending
            JOIN food_instances AS food
              ON food.food_instance_id = pending.food_instance_id
            WHERE pending.player_id = ?
            """,
            (player_id,),
        )
        return dict(row) if row is not None else None

    async def delete_pending_food_confirmation(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        food_instance_id: str | None = None,
    ) -> None:
        if food_instance_id is None:
            await session.execute(
                "DELETE FROM pending_food_confirmations WHERE player_id = ?",
                (player_id,),
            )
            return
        await session.execute(
            """
            DELETE FROM pending_food_confirmations
            WHERE player_id = ? AND food_instance_id = ?
            """,
            (player_id, food_instance_id),
        )

    async def cheapest_active_asset_id(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        asset_kind: str,
        max_rarity: int = 3,
    ) -> str | None:
        """Select the cheapest unlocked low-rarity pig or food deterministically."""

        if asset_kind not in {"pig", "food"}:
            raise ValueError("asset_kind must be pig or food")
        table = "pig_instances" if asset_kind == "pig" else "food_instances"
        id_column = "pig_instance_id" if asset_kind == "pig" else "food_instance_id"
        row = await session.fetch_one(
            f"""
            SELECT {id_column} AS asset_id
            FROM {table}
            WHERE owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
              AND rarity <= ?
            ORDER BY official_value, acquired_at, {id_column}
            LIMIT 1
            """,
            (player_id, scope_id, max_rarity),
        )
        return str(row["asset_id"]) if row is not None else None

    async def favorite_asset_rows(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        asset_kind: str,
        selector: AssetSelector,
    ) -> list[dict[str, object]]:
        """Return every active unlocked asset matched by a favorite command."""

        if asset_kind not in {"pig", "food"}:
            raise ValueError("asset_kind must be pig or food")
        table = "pig_instances" if asset_kind == "pig" else "food_instances"
        id_column = "pig_instance_id" if asset_kind == "pig" else "food_instance_id"
        compact_name = "".join(selector.name.split())
        parameters: list[object] = [player_id, scope_id, selector.name, compact_name]
        short_code_clause = ""
        if selector.short_code is not None:
            short_code_clause = "AND short_code COLLATE NOCASE = ?"
            parameters.append(selector.short_code)
        rows = await session.fetch_all(
            f"""
            SELECT
                {id_column} AS asset_id,
                display_name_snapshot,
                short_code,
                is_favorite
            FROM {table}
            WHERE owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND (
                  display_name_snapshot = ?
                  OR REPLACE(REPLACE(display_name_snapshot, ' ', ''), '　', '')
                     = ? COLLATE NOCASE
              )
              {short_code_clause}
            ORDER BY official_value DESC, acquired_at, {id_column}
            """,
            parameters,
        )
        return [dict(row) for row in rows]

    async def set_assets_favorite(
        self,
        session: DatabaseSession,
        *,
        asset_kind: str,
        asset_ids: tuple[str, ...],
        favorite: bool,
        now: str,
    ) -> int:
        """Set favorite state for an exact, pre-resolved asset set."""

        if asset_kind not in {"pig", "food"}:
            raise ValueError("asset_kind must be pig or food")
        if not asset_ids:
            return 0
        table = "pig_instances" if asset_kind == "pig" else "food_instances"
        id_column = "pig_instance_id" if asset_kind == "pig" else "food_instance_id"
        placeholders = ",".join("?" for _ in asset_ids)
        target = 1 if favorite else 0
        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET is_favorite = ?, updated_at = ?
            WHERE {id_column} IN ({placeholders})
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite != ?
            """,
            (target, now, *asset_ids, target),
        )
        return cursor.rowcount

    async def batch_sell_low_rarity(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        asset_kind: str,
        max_rarity: int,
        now: str,
        rarity: int | None = None,
        keep_highest: bool = False,
        display_name: str = "",
    ) -> tuple[int, int]:
        """Sell all unlocked assets at or below one rarity and return count/value.

        ``rarity`` 指定时只处理该品质；不指定时处理 ``1..max_rarity``。
        联动猪始终按模板保留一只价值最高的实例；``keep_highest`` 开启时，
        普通猪猪或美食也按模板各保留一只（同价值取最小实例 id）。
        """

        if asset_kind not in {"pig", "food"}:
            raise ValueError("asset_kind must be pig or food")
        table = "pig_instances" if asset_kind == "pig" else "food_instances"
        id_column = "pig_instance_id" if asset_kind == "pig" else "food_instance_id"
        rarity_clause = (
            "AND rarity = ?" if rarity is not None else "AND rarity <= ?"
        )
        rarity_param: object = rarity if rarity is not None else max_rarity
        normalized_name = str(display_name or "").strip()
        name_clause = ""
        name_parameters: tuple[object, ...] = ()
        if normalized_name:
            name_clause = """
              AND (
                  display_name_snapshot = ?
                  OR REPLACE(REPLACE(display_name_snapshot, ' ', ''), '　', '')
                     = ? COLLATE NOCASE
              )
            """
            name_parameters = (
                normalized_name,
                "".join(normalized_name.split()),
            )
        keep_ids = await self._batch_keep_ids(
            session,
            player_id=player_id,
            scope_id=scope_id,
            asset_kind=asset_kind,
            max_rarity=max_rarity,
            rarity=rarity,
            keep_highest=bool(keep_highest),
            display_name=normalized_name,
        )
        keep_clause = ""
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            keep_clause = f"AND {id_column} NOT IN ({placeholders})"
        row = await session.fetch_one(
            f"""
            SELECT COUNT(*) AS asset_count, COALESCE(SUM(official_value), 0) AS total_value
            FROM {table}
            WHERE owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
              {rarity_clause}
              {name_clause}
              {keep_clause}
            """,
            (player_id, scope_id, rarity_param, *name_parameters, *keep_ids),
        )
        count = int(row["asset_count"]) if row is not None else 0
        total_value = int(row["total_value"]) if row is not None else 0
        if count == 0:
            return 0, 0
        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET state = 'sold', disposed_at = ?, updated_at = ?
            WHERE owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
              {rarity_clause}
              {name_clause}
              {keep_clause}
            """,
            (
                now,
                now,
                player_id,
                scope_id,
                rarity_param,
                *name_parameters,
                *keep_ids,
            ),
        )
        if cursor.rowcount != count:
            raise RuntimeError("批量售卖资产数量发生变化，本次操作未结算。")
        showcase_column = (
            "pig_instance_id" if asset_kind == "pig" else "food_instance_id"
        )
        await session.execute(
            f"""
            UPDATE display_preferences
            SET {showcase_column} = NULL, updated_at = ?
            WHERE player_id = ?
              AND {showcase_column} IN (
                  SELECT {id_column}
                  FROM {table}
                  WHERE owner_player_id = ?
                    AND scope_id = ?
                    AND state = 'sold'
                    AND disposed_at = ?
                    {rarity_clause}
                    {name_clause}
                    {keep_clause}
              )
            """,
            (
                now,
                player_id,
                player_id,
                scope_id,
                now,
                rarity_param,
                *name_parameters,
                *keep_ids,
            ),
        )
        return count, total_value

    async def _batch_keep_ids(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        asset_kind: str,
        max_rarity: int,
        rarity: int | None,
        keep_highest: bool,
        display_name: str = "",
    ) -> list[str]:
        """Return instance ids that must be kept from one batch operation.

        联动猪无论开关状态都按模板保留一个最高价值实例。开关开启时，
        在本批操作范围内再按模板各保留一个最高价值的普通猪猪或美食实例。
        """

        keep_ids: list[str] = []
        if asset_kind == "pig":
            keep_ids.extend(
                await highest_collaboration_pig_ids_per_template(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    max_rarity=max_rarity,
                    rarity=rarity,
                )
            )
        if keep_highest:
            keep_ids.extend(
                await highest_instance_ids_per_template(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    asset_kind=asset_kind,
                    max_rarity=max_rarity,
                    rarity=rarity,
                    display_name=display_name,
                )
            )
        return list(dict.fromkeys(keep_ids))

    async def get_food_by_instance_id(
        self,
        session: DatabaseSession,
        *,
        food_instance_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT
                instance.*,
                template.description,
                template.image_relpath,
                template.image_fit,
                template.image_sha256,
                template.media_format,
                template.is_animated,
                template.frame_count,
                template.scope_type,
                template.recipe_tags_json,
                player.display_name AS owner_display_name,
                source.display_name_snapshot AS source_pig_name,
                source.short_code AS source_pig_short_code,
                CASE
                    WHEN template.scope_type = 'common' THEN 1
                    WHEN EXISTS(
                        SELECT 1
                        FROM scope_food_templates AS allowed
                        WHERE allowed.scope_id = instance.scope_id
                          AND allowed.template_id = template.template_id
                          AND allowed.authorized = 1
                          AND allowed.consent_status = 'granted'
                    ) THEN 1
                    ELSE 0
                END AS media_visible
            FROM food_instances AS instance
            JOIN food_templates AS template
              ON template.template_id = instance.template_id
            JOIN players AS player
              ON player.player_id = instance.owner_player_id
            LEFT JOIN pig_instances AS source
              ON source.pig_instance_id = instance.source_pig_instance_id
            WHERE instance.food_instance_id = ?
            """,
            (food_instance_id,),
        )
        return dict(row) if row is not None else None

    async def insert_food_instance(
        self,
        session: DatabaseSession,
        *,
        values: Mapping[str, object],
    ) -> None:
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, source_pig_instance_id,
                rarity, display_name_snapshot, portion_weight, fat_category,
                official_value, effect_id, effect_params_json, ruleset_version,
                random_snapshot_json, state, acquired_at, updated_at
            )
            VALUES (
                :food_instance_id, :short_code, :scope_id, :owner_player_id,
                :template_id, :template_version, :source_pig_instance_id,
                :rarity, :display_name_snapshot, :portion_weight, :fat_category,
                :official_value, :effect_id, :effect_params_json, :ruleset_version,
                :random_snapshot_json, 'active', :acquired_at, :updated_at
            )
            """,
            values,
        )

    async def consume_pig_for_cooking(
        self,
        session: DatabaseSession,
        *,
        pig_instance_id: str,
        player_id: str,
        scope_id: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE pig_instances
            SET state = 'consumed-for-cooking',
                disposed_at = ?,
                updated_at = ?
            WHERE pig_instance_id = ?
              AND owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
            """,
            (now, now, pig_instance_id, player_id, scope_id),
        )
        return cursor.rowcount == 1

    async def consume_food(
        self,
        session: DatabaseSession,
        *,
        food_instance_id: str,
        player_id: str,
        scope_id: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE food_instances
            SET state = 'consumed',
                disposed_at = ?,
                updated_at = ?
            WHERE food_instance_id = ?
              AND owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
            """,
            (now, now, food_instance_id, player_id, scope_id),
        )
        return cursor.rowcount == 1

    async def sell_pig(
        self,
        session: DatabaseSession,
        *,
        pig_instance_id: str,
        player_id: str,
        scope_id: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE pig_instances
            SET state = 'sold',
                disposed_at = ?,
                updated_at = ?
            WHERE pig_instance_id = ?
              AND owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
            """,
            (now, now, pig_instance_id, player_id, scope_id),
        )
        return cursor.rowcount == 1

    async def sell_food(
        self,
        session: DatabaseSession,
        *,
        food_instance_id: str,
        player_id: str,
        scope_id: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE food_instances
            SET state = 'sold',
                disposed_at = ?,
                updated_at = ?
            WHERE food_instance_id = ?
              AND owner_player_id = ?
              AND scope_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
            """,
            (now, now, food_instance_id, player_id, scope_id),
        )
        return cursor.rowcount == 1

    async def apply_currency_change(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        amount: int,
        reason_code: str,
        reason_text: str,
        source_object_type: str,
        source_object_id: str,
        ledger_entry_id: str,
        idempotency_key: str,
        now: str,
        allow_negative: bool = False,
    ) -> int | None:
        if amount == 0:
            raise ValueError("Currency mutation amount cannot be zero.")
        if amount < 0 and not allow_negative:
            cursor = await session.execute(
                """
                UPDATE players
                SET coin_balance = coin_balance + ?, updated_at = ?
                WHERE player_id = ? AND coin_balance >= ?
                """,
                (amount, now, player_id, -amount),
            )
        else:
            cursor = await session.execute(
                """
                UPDATE players
                SET coin_balance = coin_balance + ?, updated_at = ?
                WHERE player_id = ?
                """,
                (amount, now, player_id),
            )
        if cursor.rowcount != 1:
            return None
        row = await session.fetch_one(
            "SELECT coin_balance FROM players WHERE player_id = ?",
            (player_id,),
        )
        if row is None:
            raise RuntimeError("猪币变动后无法读取玩家余额。")
        balance = int(row["coin_balance"])
        await session.execute(
            """
            INSERT INTO currency_ledger(
                ledger_entry_id, player_id, scope_id, amount, balance_after,
                reason_code, reason_text, source_object_type, source_object_id,
                idempotency_key, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ledger_entry_id,
                player_id,
                scope_id,
                amount,
                balance,
                reason_code,
                reason_text,
                source_object_type,
                source_object_id,
                idempotency_key,
                now,
            ),
        )
        return balance

    async def add_experience(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        experience: int,
        now: str,
    ) -> int:
        if experience < 0:
            raise ValueError("Experience mutation cannot be negative.")
        await session.execute(
            """
            UPDATE players
            SET experience = experience + ?, updated_at = ?
            WHERE player_id = ?
            """,
            (experience, now, player_id),
        )
        row = await session.fetch_one(
            "SELECT experience FROM players WHERE player_id = ?",
            (player_id,),
        )
        if row is None:
            raise RuntimeError("经验变动后无法读取玩家。")
        return int(row["experience"])

    async def upsert_food_catalog(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        template_id: str,
        portion_weight: float,
        now: str,
    ) -> bool:
        existing = await session.fetch_one(
            """
            SELECT 1
            FROM food_catalog_entries
            WHERE player_id = ? AND template_id = ?
            """,
            (player_id, template_id),
        )
        await session.execute(
            """
            INSERT INTO food_catalog_entries(
                player_id, template_id, first_acquired_at, last_acquired_at,
                acquired_count, best_portion_weight
            )
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(player_id, template_id) DO UPDATE SET
                last_acquired_at = excluded.last_acquired_at,
                acquired_count = food_catalog_entries.acquired_count + 1,
                best_portion_weight = MAX(
                    food_catalog_entries.best_portion_weight,
                    excluded.best_portion_weight
                )
            """,
            (player_id, template_id, now, now, portion_weight),
        )
        return existing is None

    async def add_item_inventory(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        item_id: str,
        quantity: int,
        now: str,
    ) -> int:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = item_inventory.quantity + excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (player_id, item_id, quantity, now),
        )
        row = await session.fetch_one(
            """
            SELECT quantity
            FROM item_inventory
            WHERE player_id = ? AND item_id = ?
            """,
            (player_id, item_id),
        )
        if row is None:
            raise RuntimeError("道具购买后无法读取库存。")
        return int(row["quantity"])

    async def set_upgrade_level(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        upgrade_type: str,
        expected_level: int,
        target_level: int,
        now: str,
    ) -> bool:
        if expected_level == 0:
            cursor = await session.execute(
                """
                INSERT INTO upgrades(player_id, upgrade_type, level, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(player_id, upgrade_type) DO NOTHING
                """,
                (player_id, upgrade_type, target_level, now),
            )
            return cursor.rowcount == 1
        cursor = await session.execute(
            """
            UPDATE upgrades
            SET level = ?, updated_at = ?
            WHERE player_id = ? AND upgrade_type = ? AND level = ?
            """,
            (target_level, now, player_id, upgrade_type, expected_level),
        )
        return cursor.rowcount == 1

    async def food_inventory_page(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        rarity: int | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, object]]]:
        rarity_clause = ""
        parameters: list[object] = [player_id]
        if rarity is not None:
            rarity_clause = "AND instance.rarity = ?"
            parameters.append(rarity)
        count_row = await session.fetch_one(
            f"""
            SELECT COUNT(*) AS total_count
            FROM food_instances AS instance
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              {rarity_clause}
            """,
            parameters,
        )
        order_sql = _FOOD_INVENTORY_ORDER_SQL[sort]
        rows = await session.fetch_all(
            f"""
            SELECT instance.food_instance_id
            FROM food_instances AS instance
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              {rarity_clause}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        )
        results: list[dict[str, object]] = []
        for row in rows:
            result = await self.get_food_by_instance_id(
                session,
                food_instance_id=str(row["food_instance_id"]),
            )
            if result is not None:
                results.append(result)
        total = int(count_row["total_count"]) if count_row is not None else 0
        return total, results

    async def visible_food_catalog_counts(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
    ) -> tuple[int, int]:
        row = await session.fetch_one(
            """
            SELECT
                COUNT(*) AS total_count,
                COUNT(catalog.player_id) AS collected_count
            FROM food_templates AS template
            LEFT JOIN scope_food_templates AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            LEFT JOIN food_catalog_entries AS catalog
              ON catalog.template_id = template.template_id
             AND catalog.player_id = ?
            WHERE template.enabled = 1
              AND (
                  template.scope_type = 'common'
                  OR (
                      template.scope_type = 'group'
                      AND allowed.authorized = 1
                      AND allowed.consent_status = 'granted'
                  )
              )
            """,
            (scope_id, player_id),
        )
        if row is None:
            return 0, 0
        return int(row["collected_count"]), int(row["total_count"])

    async def food_catalog_entries(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        rarity: int | None,
        undiscovered_only: bool,
    ) -> tuple[int, list[dict[str, object]]]:
        filters: list[str] = []
        parameters: list[object] = [scope_id, player_id]
        if rarity is not None:
            filters.append("template.rarity = ?")
            parameters.append(rarity)
        if undiscovered_only:
            filters.append("catalog.player_id IS NULL")
        filter_sql = "".join(f" AND {condition}" for condition in filters)
        common_sql = f"""
            FROM food_templates AS template
            LEFT JOIN scope_food_templates AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            LEFT JOIN food_catalog_entries AS catalog
              ON catalog.template_id = template.template_id
             AND catalog.player_id = ?
            WHERE template.enabled = 1
              AND (
                  template.scope_type = 'common'
                  OR (
                      template.scope_type = 'group'
                      AND allowed.authorized = 1
                      AND allowed.consent_status = 'granted'
                  )
              )
              {filter_sql}
        """
        count_row = await session.fetch_one(
            f"SELECT COUNT(*) AS total_count {common_sql}",
            parameters,
        )
        rows = await session.fetch_all(
            f"""
            SELECT
                template.template_id,
                template.display_name,
                template.rarity,
                template.description,
                template.image_relpath,
                template.image_fit,
                template.media_format,
                template.is_animated,
                template.frame_count,
                template.recipe_tags_json,
                template.effect_id,
                template.effect_params_json,
                catalog.first_acquired_at,
                catalog.last_acquired_at,
                catalog.acquired_count,
                catalog.best_portion_weight,
                CASE WHEN catalog.player_id IS NULL THEN 0 ELSE 1 END AS discovered
            {common_sql}
            ORDER BY template.rarity, template.display_name, template.template_id
            """,
            parameters,
        )
        total = int(count_row["total_count"]) if count_row is not None else 0
        return total, [dict(row) for row in rows]

    async def economy_profile_row(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT
                player.coin_balance,
                player.experience,
                (
                    SELECT COUNT(*)
                    FROM food_instances AS instance
                    WHERE instance.owner_player_id = player.player_id
                ) AS total_foods,
                (
                    SELECT COUNT(*)
                    FROM food_instances AS instance
                    WHERE instance.owner_player_id = player.player_id
                      AND instance.state IN ('active', 'locked-for-trade')
                ) AS active_foods,
                statistic.total_cooks
            FROM players AS player
            JOIN player_statistics AS statistic
              ON statistic.player_id = player.player_id
            WHERE player.player_id = ?
            """,
            (player_id,),
        )
        return dict(row) if row is not None else None

    async def ledger_page(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, object]]]:
        count_row = await session.fetch_one(
            """
            SELECT COUNT(*) AS total_count
            FROM currency_ledger
            WHERE player_id = ?
            """,
            (player_id,),
        )
        rows = await session.fetch_all(
            """
            SELECT
                ledger_entry_id, amount, balance_after, reason_code, reason_text,
                source_object_type, source_object_id, created_at
            FROM currency_ledger
            WHERE player_id = ?
            ORDER BY created_at DESC, ledger_entry_id DESC
            LIMIT ? OFFSET ?
            """,
            (player_id, limit, offset),
        )
        total = int(count_row["total_count"]) if count_row is not None else 0
        return total, [dict(row) for row in rows]

    async def balance_reconciliation(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> tuple[int, int]:
        row = await session.fetch_one(
            """
            SELECT
                player.coin_balance,
                COALESCE((
                    SELECT SUM(ledger.amount)
                    FROM currency_ledger AS ledger
                    WHERE ledger.player_id = player.player_id
                ), 0) AS ledger_total
            FROM players AS player
            WHERE player.player_id = ?
            """,
            (player_id,),
        )
        if row is None:
            raise RuntimeError("无法读取玩家猪币对账信息。")
        return int(row["coin_balance"]), int(row["ledger_total"])
