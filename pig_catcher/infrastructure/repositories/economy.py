"""第四轮做菜、美食、商城、售卖和猪币账本仓储。"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.models import AssetSelector
from ..database import DatabaseSession

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

    async def find_active_foods(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        selector: AssetSelector,
    ) -> list[dict[str, object]]:
        parameters: list[object] = [player_id, selector.name]
        short_code_clause = ""
        if selector.short_code is not None:
            short_code_clause = "AND instance.short_code = ?"
            parameters.append(selector.short_code)
        rows = await session.fetch_all(
            f"""
            SELECT instance.food_instance_id
            FROM food_instances AS instance
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              AND instance.display_name_snapshot = ?
              {short_code_clause}
            ORDER BY instance.acquired_at DESC
            LIMIT 20
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
    ) -> int | None:
        if amount == 0:
            raise ValueError("Currency mutation amount cannot be zero.")
        if amount < 0:
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
                      AND catalog.player_id IS NOT NULL
                  )
              )
            """,
            (scope_id, player_id),
        )
        if row is None:
            return 0, 0
        return int(row["collected_count"]), int(row["total_count"])

    async def food_catalog_page(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        rarity: int | None,
        undiscovered_only: bool,
        limit: int,
        offset: int,
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
                      AND catalog.player_id IS NOT NULL
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
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
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
                      AND instance.state = 'active'
                ) AS active_foods,
                (
                    SELECT COUNT(*)
                    FROM command_receipts AS receipt
                    WHERE receipt.player_id = player.player_id
                      AND receipt.command_name = 'pig-catcher.cook'
                ) AS total_cooks
            FROM players AS player
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
