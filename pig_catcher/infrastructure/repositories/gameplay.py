"""第三轮抓猪、收藏、纪录和道具仓储。"""

from __future__ import annotations

import json
from collections.abc import Mapping

from ...domain.enums import RecordType
from ...domain.models import AssetSelector
from ..database import DatabaseSession

_INVENTORY_ORDER_SQL = {
    "获得时间": "instance.acquired_at DESC, instance.pig_instance_id",
    "品质": "instance.rarity DESC, instance.acquired_at DESC",
    "价值": "instance.official_value DESC, instance.acquired_at DESC",
    "体型": "instance.size_value DESC, instance.acquired_at DESC",
    "重量": "instance.weight_value DESC, instance.acquired_at DESC",
    "名称": "instance.display_name_snapshot COLLATE NOCASE, instance.acquired_at DESC",
}


class GameplayRepository:
    """执行第三轮 SQL，不提交事务也不生成随机业务结果。"""

    async def list_drawable_pig_templates(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT template.*
            FROM pig_templates AS template
            LEFT JOIN scope_pig_templates AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            WHERE template.enabled = 1
              AND (
                  template.scope_type = 'common'
                  OR (
                      template.scope_type = 'group'
                      AND allowed.authorized = 1
                      AND allowed.consent_status = 'granted'
                  )
              )
            ORDER BY template.rarity, template.template_id
            """,
            (scope_id,),
        )
        return [dict(row) for row in rows]

    async def catch_usage(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        day_start: str,
        day_end: str,
    ) -> tuple[int, str | None]:
        row = await session.fetch_one(
            """
            SELECT
                (
                    SELECT COUNT(*)
                    FROM command_receipts
                    WHERE player_id = ?
                      AND command_name = 'pig-catcher.catch'
                      AND created_at >= ?
                      AND created_at < ?
                ) AS daily_count,
                statistic.last_catch_at AS last_acquired_at
            FROM player_statistics AS statistic
            WHERE statistic.player_id = ?
            """,
            (player_id, day_start, day_end, player_id),
        )
        if row is None:
            return 0, None
        last = row["last_acquired_at"]
        return int(row["daily_count"]), str(last) if last is not None else None

    async def get_feed_level(self, session: DatabaseSession, *, player_id: str) -> int:
        row = await session.fetch_one(
            """
            SELECT level
            FROM upgrades
            WHERE player_id = ? AND upgrade_type = 'feed'
            """,
            (player_id,),
        )
        return int(row["level"]) if row is not None else 0

    async def get_armed_item(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        action_type: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT armed.item_id, inventory.quantity, armed.armed_at
            FROM armed_items AS armed
            LEFT JOIN item_inventory AS inventory
              ON inventory.player_id = armed.player_id
             AND inventory.item_id = armed.item_id
            WHERE armed.player_id = ? AND armed.action_type = ?
            """,
            (player_id, action_type),
        )
        return dict(row) if row is not None else None

    async def short_code_exists(self, session: DatabaseSession, short_code: str) -> bool:
        row = await session.fetch_one(
            """
            SELECT 1
            FROM pig_instances
            WHERE short_code = ?
            UNION ALL
            SELECT 1
            FROM food_instances
            WHERE short_code = ?
            LIMIT 1
            """,
            (short_code, short_code),
        )
        return row is not None

    async def insert_pig_instance(
        self,
        session: DatabaseSession,
        *,
        values: Mapping[str, object],
    ) -> None:
        await session.execute(
            """
            INSERT INTO pig_instances(
                pig_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                size_value, size_percentile, weight_value, weight_percentile,
                fat_ratio, official_value, ruleset_version, random_snapshot_json,
                state, acquired_at, updated_at
            )
            VALUES (
                :pig_instance_id, :short_code, :scope_id, :owner_player_id,
                :template_id, :template_version, :rarity, :display_name_snapshot,
                :size_value, :size_percentile, :weight_value, :weight_percentile,
                :fat_ratio, :official_value, :ruleset_version, :random_snapshot_json,
                'active', :acquired_at, :updated_at
            )
            """,
            values,
        )

    async def apply_catch_rewards(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        coin_reward: int,
        experience_reward: int,
        ledger_entry_id: str,
        pig_instance_id: str,
        idempotency_key: str,
        now: str,
    ) -> tuple[int, int]:
        await session.execute(
            """
            UPDATE players
            SET coin_balance = coin_balance + ?,
                experience = experience + ?,
                updated_at = ?
            WHERE player_id = ?
            """,
            (coin_reward, experience_reward, now, player_id),
        )
        row = await session.fetch_one(
            "SELECT coin_balance, experience FROM players WHERE player_id = ?",
            (player_id,),
        )
        if row is None:
            raise RuntimeError("抓猪奖励更新后无法读取玩家。")
        balance = int(row["coin_balance"])
        experience = int(row["experience"])
        await session.execute(
            """
            INSERT INTO currency_ledger(
                ledger_entry_id, player_id, scope_id, amount, balance_after,
                reason_code, reason_text, source_object_type, source_object_id,
                idempotency_key, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'catch-reward', '抓猪奖励', 'pig', ?, ?, ?)
            """,
            (
                ledger_entry_id,
                player_id,
                scope_id,
                coin_reward,
                balance,
                pig_instance_id,
                f"{idempotency_key}:coin",
                now,
            ),
        )
        await session.execute(
            """
            UPDATE player_statistics
            SET
                total_catches = total_catches + 1,
                last_catch_at = ?,
                updated_at = ?
            WHERE player_id = ?
            """,
            (now, now, player_id),
        )
        return balance, experience

    async def upsert_pig_catalog(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        template_id: str,
        size_value: float,
        weight_value: float,
        now: str,
    ) -> bool:
        existing = await session.fetch_one(
            """
            SELECT 1
            FROM pig_catalog_entries
            WHERE player_id = ? AND template_id = ?
            """,
            (player_id, template_id),
        )
        await session.execute(
            """
            INSERT INTO pig_catalog_entries(
                player_id, template_id, first_acquired_at, last_acquired_at,
                acquired_count, best_size, best_weight
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(player_id, template_id) DO UPDATE SET
                last_acquired_at = excluded.last_acquired_at,
                acquired_count = pig_catalog_entries.acquired_count + 1,
                best_size = MAX(pig_catalog_entries.best_size, excluded.best_size),
                best_weight = MAX(pig_catalog_entries.best_weight, excluded.best_weight)
            """,
            (player_id, template_id, now, now, size_value, weight_value),
        )
        return existing is None

    async def update_group_record(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        template_id: str,
        record_type: RecordType,
        pig_instance_id: str,
        record_value: float,
        player_id: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT INTO group_records(
                scope_id, template_id, record_type, pig_instance_id,
                record_value, player_id, achieved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_id, template_id, record_type) DO UPDATE SET
                pig_instance_id = excluded.pig_instance_id,
                record_value = excluded.record_value,
                player_id = excluded.player_id,
                achieved_at = excluded.achieved_at
            WHERE excluded.record_value > group_records.record_value
            """,
            (
                scope_id,
                template_id,
                record_type.value,
                pig_instance_id,
                record_value,
                player_id,
                now,
            ),
        )
        return cursor.rowcount == 1

    async def consume_armed_item(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        action_type: str,
        item_id: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE item_inventory
            SET quantity = quantity - 1, updated_at = ?
            WHERE player_id = ? AND item_id = ? AND quantity > 0
            """,
            (now, player_id, item_id),
        )
        if cursor.rowcount != 1:
            return False
        await session.execute(
            """
            DELETE FROM armed_items
            WHERE player_id = ? AND action_type = ? AND item_id = ?
            """,
            (player_id, action_type, item_id),
        )
        return True

    async def get_pig_by_instance_id(
        self,
        session: DatabaseSession,
        *,
        pig_instance_id: str,
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
                template.stature_profile,
                template.collection_name,
                template.collection_total,
                template.character_name,
                player.display_name AS owner_display_name,
                EXISTS(
                    SELECT 1
                    FROM group_records AS record
                    WHERE record.pig_instance_id = instance.pig_instance_id
                      AND record.record_type = 'size'
                ) AS is_size_record,
                EXISTS(
                    SELECT 1
                    FROM group_records AS record
                    WHERE record.pig_instance_id = instance.pig_instance_id
                      AND record.record_type = 'weight'
                ) AS is_weight_record,
                EXISTS(
                    SELECT 1
                    FROM group_global_records AS record
                    WHERE record.pig_instance_id = instance.pig_instance_id
                      AND record.record_type = 'size'
                ) AS is_global_size_record,
                EXISTS(
                    SELECT 1
                    FROM group_global_records AS record
                    WHERE record.pig_instance_id = instance.pig_instance_id
                      AND record.record_type = 'weight'
                ) AS is_global_weight_record,
                EXISTS(
                    SELECT 1
                    FROM giant_sightings AS sighting
                    WHERE sighting.pig_instance_id = instance.pig_instance_id
                ) AS is_giant_sighting,
                CASE
                    WHEN template.scope_type = 'common' THEN 1
                    WHEN EXISTS(
                        SELECT 1
                        FROM scope_pig_templates AS allowed
                        WHERE allowed.scope_id = instance.scope_id
                          AND allowed.template_id = template.template_id
                          AND allowed.authorized = 1
                          AND allowed.consent_status = 'granted'
                    ) THEN 1
                    ELSE 0
                END AS media_visible
            FROM pig_instances AS instance
            JOIN pig_templates AS template
              ON template.template_id = instance.template_id
            JOIN players AS player
              ON player.player_id = instance.owner_player_id
            WHERE instance.pig_instance_id = ?
            """,
            (pig_instance_id,),
        )
        return dict(row) if row is not None else None

    async def find_active_pigs(
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
            SELECT instance.pig_instance_id
            FROM pig_instances AS instance
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
            result = await self.get_pig_by_instance_id(
                session,
                pig_instance_id=str(row["pig_instance_id"]),
            )
            if result is not None:
                results.append(result)
        return results

    async def profile_row(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT
                player.player_id,
                player.scope_id,
                player.display_name,
                player.coin_balance,
                player.experience,
                statistic.total_catches,
                (
                    SELECT COUNT(*)
                    FROM pig_instances AS instance
                    WHERE instance.owner_player_id = player.player_id
                      AND instance.state IN ('active', 'locked-for-trade')
                ) AS active_pigs,
                (
                    SELECT COUNT(*)
                    FROM pig_catalog_entries AS catalog
                    WHERE catalog.player_id = player.player_id
                ) AS catalog_count,
                (
                    SELECT COUNT(*)
                    FROM group_records AS record
                    WHERE record.player_id = player.player_id
                ) AS held_records
            FROM players AS player
            JOIN player_statistics AS statistic
              ON statistic.player_id = player.player_id
            WHERE player.player_id = ?
            """,
            (player_id,),
        )
        return dict(row) if row is not None else None

    async def visible_catalog_counts(
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
            FROM pig_templates AS template
            LEFT JOIN scope_pig_templates AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            LEFT JOIN pig_catalog_entries AS catalog
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

    async def visible_catalog_total(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
    ) -> int:
        """Compatibility wrapper for callers that only need the denominator."""

        _, total = await self.visible_catalog_counts(
            session,
            player_id=player_id,
            scope_id=scope_id,
        )
        return total

    async def inventory_page(
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
            FROM pig_instances AS instance
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              {rarity_clause}
            """,
            parameters,
        )
        order_sql = _INVENTORY_ORDER_SQL[sort]
        rows = await session.fetch_all(
            f"""
            SELECT
                instance.*,
                template.description,
                template.image_relpath,
                template.image_fit,
                template.media_format,
                template.is_animated,
                template.frame_count,
                template.stature_profile,
                EXISTS(
                    SELECT 1
                    FROM group_global_records AS record
                    WHERE record.pig_instance_id = instance.pig_instance_id
                      AND record.record_type = 'size'
                ) AS is_global_size_record,
                EXISTS(
                    SELECT 1
                    FROM group_global_records AS record
                    WHERE record.pig_instance_id = instance.pig_instance_id
                      AND record.record_type = 'weight'
                ) AS is_global_weight_record,
                EXISTS(
                    SELECT 1
                    FROM giant_sightings AS sighting
                    WHERE sighting.pig_instance_id = instance.pig_instance_id
                ) AS is_giant_sighting,
                CASE
                    WHEN template.scope_type = 'common' THEN 1
                    WHEN EXISTS(
                        SELECT 1
                        FROM scope_pig_templates AS allowed
                        WHERE allowed.scope_id = instance.scope_id
                          AND allowed.template_id = template.template_id
                          AND allowed.authorized = 1
                          AND allowed.consent_status = 'granted'
                    ) THEN 1
                    ELSE 0
                END AS media_visible
            FROM pig_instances AS instance
            JOIN pig_templates AS template
              ON template.template_id = instance.template_id
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              {rarity_clause}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        )
        total = int(count_row["total_count"]) if count_row is not None else 0
        return total, [dict(row) for row in rows]

    async def catalog_page(
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
        visibility_sql = """
            template.enabled = 1
            AND (
                template.scope_type = 'common'
                OR (
                    template.scope_type = 'group'
                    AND allowed.authorized = 1
                    AND allowed.consent_status = 'granted'
                    AND catalog.player_id IS NOT NULL
                )
            )
        """
        common_sql = f"""
            FROM pig_templates AS template
            LEFT JOIN scope_pig_templates AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            LEFT JOIN pig_catalog_entries AS catalog
              ON catalog.template_id = template.template_id
             AND catalog.player_id = ?
            WHERE {visibility_sql}
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
                template.collection_id,
                template.collection_name,
                template.collection_slot,
                template.collection_total,
                template.character_name,
                catalog.first_acquired_at,
                catalog.last_acquired_at,
                catalog.acquired_count,
                catalog.best_size,
                catalog.best_weight,
                CASE WHEN catalog.player_id IS NULL THEN 0 ELSE 1 END AS discovered
            {common_sql}
            ORDER BY template.rarity, template.display_name, template.template_id
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        )
        total = int(count_row["total_count"]) if count_row is not None else 0
        return total, [dict(row) for row in rows]

    async def records_page(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, object]]]:
        count_row = await session.fetch_one(
            "SELECT COUNT(*) AS total_count FROM group_records WHERE scope_id = ?",
            (scope_id,),
        )
        rows = await session.fetch_all(
            """
            SELECT
                record.record_type,
                record.record_value,
                record.achieved_at,
                template.display_name,
                template.rarity,
                instance.short_code,
                player.display_name AS holder_display_name
            FROM group_records AS record
            JOIN pig_templates AS template
              ON template.template_id = record.template_id
            JOIN pig_instances AS instance
              ON instance.pig_instance_id = record.pig_instance_id
            JOIN players AS player
              ON player.player_id = record.player_id
            WHERE record.scope_id = ?
            ORDER BY record.achieved_at DESC, template.display_name, record.record_type
            LIMIT ? OFFSET ?
            """,
            (scope_id, limit, offset),
        )
        total = int(count_row["total_count"]) if count_row is not None else 0
        return total, [dict(row) for row in rows]

    async def item_quantity(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        item_id: str,
    ) -> int:
        row = await session.fetch_one(
            """
            SELECT quantity
            FROM item_inventory
            WHERE player_id = ? AND item_id = ?
            """,
            (player_id, item_id),
        )
        return int(row["quantity"]) if row is not None else 0

    async def arm_item(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        action_type: str,
        item_id: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO armed_items(player_id, action_type, item_id, armed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(player_id, action_type) DO UPDATE SET
                item_id = excluded.item_id,
                armed_at = excluded.armed_at
            """,
            (player_id, action_type, item_id, now),
        )

    async def cancel_armed_item(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        action_type: str,
    ) -> str | None:
        row = await session.fetch_one(
            """
            SELECT item_id
            FROM armed_items
            WHERE player_id = ? AND action_type = ?
            """,
            (player_id, action_type),
        )
        if row is None:
            return None
        await session.execute(
            """
            DELETE FROM armed_items
            WHERE player_id = ? AND action_type = ?
            """,
            (player_id, action_type),
        )
        return str(row["item_id"])

    @staticmethod
    def random_snapshot_json(snapshot: Mapping[str, object]) -> str:
        return json.dumps(
            dict(snapshot),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
