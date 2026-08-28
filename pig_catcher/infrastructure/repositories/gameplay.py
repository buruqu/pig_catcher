"""第三轮抓猪、收藏、纪录和道具仓储。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from ...domain.enums import RecordType
from ...domain.models import AssetSelector
from ..database import DatabaseSession
from .batch_safety import (
    highest_collaboration_pig_ids_per_template,
    highest_instance_ids_per_template,
)

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
        window_start: str,
        window_end: str,
    ) -> tuple[int, int, str | None]:
        row = await session.fetch_one(
            """
            WITH player_scope AS (
                SELECT scope_id
                FROM players
                WHERE player_id = ?
            ),
            effective_window AS (
                SELECT COALESCE(MAX(reset.created_at), ?) AS effective_start
                FROM audit_events AS reset
                WHERE (
                    (
                        reset.action IN (
                            'daily-catch-quota-reset',
                            'catch-quota-window-reset',
                            'catch-quota-window-boost'
                        )
                        AND reset.created_at >= ?
                        AND reset.created_at < ?
                        AND (
                            reset.scope_id IS NULL
                            OR reset.scope_id = (SELECT scope_id FROM player_scope)
                        )
                    )
                    OR (
                        reset.action = 'player-catch-quota-window-reset'
                        AND reset.scope_id = (SELECT scope_id FROM player_scope)
                        AND reset.object_id = ?
                        AND reset.created_at >= ?
                        AND reset.created_at < ?
                    )
                )
            )
            SELECT
                COALESCE(SUM(receipt.catch_quota_cost), 0) AS daily_count,
                COUNT(*) AS total_count,
                MAX(receipt.created_at) AS last_acquired_at
            FROM command_receipts AS receipt
            CROSS JOIN effective_window
            WHERE receipt.player_id = ?
              AND receipt.command_name IN ('pig-catcher.catch', 'pig-catcher.battle.loot')
              AND receipt.created_at >= effective_window.effective_start
              AND receipt.created_at < ?
            """,
            (
                player_id,
                window_start,
                window_start,
                window_end,
                player_id,
                window_start,
                window_end,
                player_id,
                window_end,
            ),
        )
        if row is None:
            return 0, 0, None
        last = row["last_acquired_at"]
        return (
            int(row["daily_count"]),
            int(row["total_count"]),
            str(last) if last is not None else None,
        )

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

    async def get_player_experience(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> int:
        row = await session.fetch_one(
            "SELECT experience FROM players WHERE player_id = ?",
            (player_id,),
        )
        if row is None:
            raise RuntimeError("玩家初始化后无法读取累计经验。")
        return int(row["experience"])

    async def get_armed_item(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        action_type: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT
                armed.item_id,
                inventory.quantity,
                armed.remaining_uses,
                armed.armed_at
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
            WHERE short_code COLLATE NOCASE = ?
            UNION ALL
            SELECT 1
            FROM food_instances
            WHERE short_code COLLATE NOCASE = ?
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
        armed_row = await session.fetch_one(
            """
            SELECT remaining_uses
            FROM armed_items
            WHERE player_id = ?
              AND action_type = ?
              AND item_id = ?
              AND remaining_uses > 0
            """,
            (player_id, action_type, item_id),
        )
        if armed_row is None:
            return False
        remaining_uses = int(armed_row["remaining_uses"])
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
        if remaining_uses == 1:
            armed = await session.execute(
                """
                DELETE FROM armed_items
                WHERE player_id = ?
                  AND action_type = ?
                  AND item_id = ?
                  AND remaining_uses = 1
                """,
                (player_id, action_type, item_id),
            )
        else:
            armed = await session.execute(
                """
                UPDATE armed_items
                SET remaining_uses = remaining_uses - 1,
                    armed_at = ?
                WHERE player_id = ?
                  AND action_type = ?
                  AND item_id = ?
                  AND remaining_uses = ?
                """,
                (now, player_id, action_type, item_id, remaining_uses),
            )
        if armed.rowcount != 1:
            raise RuntimeError("道具连续使用队列在结算过程中发生并发变化，本次业务已回滚。")
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
                (SELECT purpose FROM asset_occupancies busy
                 WHERE busy.pig_instance_id=instance.pig_instance_id) AS busy_purpose,
                EXISTS(SELECT 1 FROM tour_protections protected
                       WHERE protected.pig_instance_id=instance.pig_instance_id
                       AND protected.protected=1) AS tour_protected,
                EXISTS(SELECT 1 FROM battle_protections protected
                       WHERE protected.pig_instance_id=instance.pig_instance_id
                       AND protected.protected=1) AS battle_protected,
                template.description,
                template.image_relpath,
                template.image_fit,
                template.image_sha256,
                template.media_format,
                template.is_animated,
                template.frame_count,
                template.scope_type,
                template.stature_profile,
                template.display_tags_json,
                template.collection_name,
                template.collection_total,
                template.character_name,
                template.paired_food_template_id,
                template.alternate_image_relpath,
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
        prefer_highest: bool = False,
        available_only: bool = False,
    ) -> list[dict[str, object]]:
        # 精确匹配优先；若未命中再按“去空白 + 忽略英文大小写”的紧凑名兜底，
        # 以兼容名称中含空格或英文大小写差异（如“白吃 Token 的猪”）。
        compact_name = "".join(selector.name.split())
        parameters: list[object] = [player_id, selector.name, compact_name]
        short_code_clause = ""
        if selector.short_code is not None:
            short_code_clause = "AND instance.short_code COLLATE NOCASE = ?"
            parameters.append(selector.short_code)
        order_sql = (
            "instance.official_value DESC, instance.acquired_at, instance.pig_instance_id"
            if prefer_highest
            else (
                "instance.is_favorite, instance.official_value, "
                "instance.acquired_at, instance.pig_instance_id"
            )
        )
        limit = 1 if prefer_highest else 20
        available_clause = """
            AND instance.locked_trade_id IS NULL AND instance.is_favorite=0
            AND NOT EXISTS(SELECT 1 FROM asset_occupancies busy WHERE busy.pig_instance_id=instance.pig_instance_id)
            AND NOT EXISTS(SELECT 1 FROM tour_protections protected
                           WHERE protected.pig_instance_id=instance.pig_instance_id AND protected.protected=1)
              AND NOT EXISTS(SELECT 1 FROM battle_protections protected
                  WHERE protected.pig_instance_id=instance.pig_instance_id AND protected.protected=1)
        """ if available_only else ""
        rows = await session.fetch_all(
            f"""
            SELECT instance.pig_instance_id
            FROM pig_instances AS instance
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              AND (
                  instance.display_name_snapshot = ?
                  OR REPLACE(REPLACE(instance.display_name_snapshot, ' ', ''), '　', '')
                     = ? COLLATE NOCASE
              )
              {short_code_clause}
              {available_clause}
            ORDER BY {order_sql}
            LIMIT {limit}
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

    async def list_cookable_pigs(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        rarity: int | None,
        keep_highest: bool = False,
    ) -> list[dict[str, object]]:
        """List active, unlocked pigs eligible for batch cooking.

        默认只处理一至三星低星原料猪（``rarity`` 指定时按指定品质）；联动猪
        始终按模板保留一只价值最高实例。``keep_highest`` 开启时，普通猪也按模板
        各保留一只价值最高实例。
        """

        rarity_clause = (
            "AND instance.rarity = ?"
            if rarity is not None
            else "AND instance.rarity <= 3"
        )
        keep_ids = await self._batch_keep_pig_ids(
            session,
            player_id=player_id,
            scope_id=scope_id,
            rarity=rarity,
            keep_highest=bool(keep_highest),
        )
        keep_clause = ""
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            keep_clause = f"AND instance.pig_instance_id NOT IN ({placeholders})"
        parameters: list[object] = [player_id, scope_id]
        if rarity is not None:
            parameters.append(rarity)
        parameters.extend(keep_ids)
        rows = await session.fetch_all(
            f"""
            SELECT instance.pig_instance_id
            FROM pig_instances AS instance
            WHERE instance.owner_player_id = ?
              AND instance.scope_id = ?
              AND instance.state = 'active'
              AND instance.locked_trade_id IS NULL
              AND instance.is_favorite = 0
              AND NOT EXISTS(SELECT 1 FROM asset_occupancies busy WHERE busy.pig_instance_id=instance.pig_instance_id)
              AND NOT EXISTS(SELECT 1 FROM tour_protections protected
                             WHERE protected.pig_instance_id=instance.pig_instance_id AND protected.protected=1)
              AND NOT EXISTS(SELECT 1 FROM battle_protections protected
                  WHERE protected.pig_instance_id=instance.pig_instance_id AND protected.protected=1)
              {rarity_clause}
              {keep_clause}
            ORDER BY
                instance.rarity DESC,
                instance.acquired_at ASC,
                instance.pig_instance_id ASC
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

    async def _batch_keep_pig_ids(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        rarity: int | None,
        keep_highest: bool,
    ) -> list[str]:
        """Return pig instance ids kept from one batch cooking/selling operation.

        联动猪无论开关状态都按模板保留一个最高价值实例；开关开启时，
        在批量做菜品质区间内再按模板各保留一个最高价值的普通猪实例。
        """

        keep_ids = await highest_collaboration_pig_ids_per_template(
            session,
            player_id=player_id,
            scope_id=scope_id,
            max_rarity=3,
            rarity=rarity,
        )
        if keep_highest:
            keep_ids.extend(
                await highest_instance_ids_per_template(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    asset_kind="pig",
                    max_rarity=3,
                    rarity=rarity,
                )
            )
        return list(dict.fromkeys(keep_ids))

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
                (SELECT purpose FROM asset_occupancies busy
                 WHERE busy.pig_instance_id=instance.pig_instance_id) AS busy_purpose,
                EXISTS(SELECT 1 FROM tour_protections protected
                       WHERE protected.pig_instance_id=instance.pig_instance_id
                       AND protected.protected=1) AS tour_protected,
                EXISTS(SELECT 1 FROM battle_protections protected
                       WHERE protected.pig_instance_id=instance.pig_instance_id
                       AND protected.protected=1) AS battle_protected,
                template.description,
                template.image_relpath,
                template.image_fit,
                template.media_format,
                template.is_animated,
                template.frame_count,
                template.stature_profile,
                template.alternate_image_relpath,
                template.display_tags_json,
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

    async def catalog_entries(
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
        visibility_sql = """
            template.enabled = 1
            AND (
                template.scope_type = 'common'
                OR (
                    template.scope_type = 'group'
                    AND allowed.authorized = 1
                    AND allowed.consent_status = 'granted'
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
                template.display_tags_json,
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
            """,
            parameters,
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

    async def daily_giants(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        start_at: str,
        end_at: str,
        limit: int,
    ) -> tuple[int, int, list[dict[str, object]], list[dict[str, object]]]:
        """Return each player's best size and weight among today's real catches."""

        summary = await session.fetch_one(
            """
            SELECT COUNT(DISTINCT receipt.player_id) AS participant_count,
                   COUNT(*) AS catch_count
            FROM command_receipts AS receipt
            JOIN pig_instances AS instance
              ON instance.pig_instance_id = receipt.result_object_id
             AND instance.scope_id = receipt.scope_id
            WHERE receipt.scope_id = ?
              AND receipt.command_name = 'pig-catcher.catch'
              AND receipt.result_type = 'pig'
              AND receipt.created_at >= ?
              AND receipt.created_at <= ?
            """,
            (scope_id, start_at, end_at),
        )
        query = """
            WITH ranked AS (
                SELECT
                    instance.pig_instance_id,
                    receipt.player_id,
                    player.display_name AS holder_display_name,
                    instance.display_name_snapshot AS display_name,
                    instance.rarity,
                    instance.short_code,
                    instance.size_value,
                    instance.weight_value,
                    receipt.created_at AS acquired_at,
                    template.image_relpath,
                    template.image_fit,
                    template.is_animated,
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
                    END AS media_visible,
                    ROW_NUMBER() OVER (
                        PARTITION BY receipt.player_id
                        ORDER BY instance.{primary_metric} DESC,
                                 instance.{secondary_metric} DESC,
                                 receipt.created_at ASC,
                                 instance.pig_instance_id ASC
                    ) AS player_rank
                FROM command_receipts AS receipt
                JOIN pig_instances AS instance
                  ON instance.pig_instance_id = receipt.result_object_id
                 AND instance.scope_id = receipt.scope_id
                JOIN players AS player
                  ON player.player_id = receipt.player_id
                JOIN pig_templates AS template
                  ON template.template_id = instance.template_id
                WHERE receipt.scope_id = ?
                  AND receipt.command_name = 'pig-catcher.catch'
                  AND receipt.result_type = 'pig'
                  AND receipt.created_at >= ?
                  AND receipt.created_at <= ?
            )
            SELECT *
            FROM ranked
            WHERE player_rank = 1
            ORDER BY ranked.{primary_metric} DESC,
                     ranked.{secondary_metric} DESC,
                     acquired_at ASC,
                     player_id ASC
            LIMIT ?
        """

        async def best_rows(primary_metric: str, secondary_metric: str) -> list[dict[str, object]]:
            sql = query.format(
                primary_metric=primary_metric,
                secondary_metric=secondary_metric,
            )
            rows = await session.fetch_all(sql, (scope_id, start_at, end_at, limit))
            return [dict(row) for row in rows]

        participant_count = int(summary["participant_count"]) if summary is not None else 0
        catch_count = int(summary["catch_count"]) if summary is not None else 0
        return (
            participant_count,
            catch_count,
            await best_rows("size_value", "weight_value"),
            await best_rows("weight_value", "size_value"),
        )

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
        remaining_uses: int,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO armed_items(
                player_id, action_type, item_id, armed_at, remaining_uses
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(player_id, action_type) DO UPDATE SET
                item_id = excluded.item_id,
                armed_at = excluded.armed_at,
                remaining_uses = excluded.remaining_uses
            """,
            (player_id, action_type, item_id, now, remaining_uses),
        )

    async def cancel_armed_item(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        action_type: str,
    ) -> tuple[str, int] | None:
        row = await session.fetch_one(
            """
            SELECT item_id, remaining_uses
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
        return str(row["item_id"]), int(row["remaining_uses"])

    async def transfer_pig_owner(
        self,
        session: DatabaseSession,
        *,
        pig_instance_id: str,
        owner_player_id: str,
        now: str,
    ) -> bool:
        """Reassign one active pig to another player (system group-effect gift)."""

        cursor = await session.execute(
            """
            UPDATE pig_instances
            SET owner_player_id = ?, updated_at = ?
            WHERE pig_instance_id = ? AND state = 'active'
              AND NOT EXISTS(SELECT 1 FROM asset_occupancies busy
                             WHERE busy.pig_instance_id=pig_instances.pig_instance_id)
              AND NOT EXISTS(SELECT 1 FROM tour_protections protected
                             WHERE protected.pig_instance_id=pig_instances.pig_instance_id AND protected.protected=1)
              AND NOT EXISTS(SELECT 1 FROM battle_protections protected
                  WHERE protected.pig_instance_id=pig_instances.pig_instance_id AND protected.protected=1)
            """,
            (owner_player_id, now, pig_instance_id),
        )
        return cursor.rowcount == 1

    async def list_baogian_instances(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> list[dict[str, object]]:
        """List active baogian pig instances owned by the player.

        保千猪按显示名快照与模板备用图判定，覆盖四个群（NapCat 双群 + QQ
        官方双群）的所有保千猪模板副本。
        """

        return await self.list_switchable_pig_instances(
            session,
            player_id=player_id,
            display_name="保千猪",
        )

    async def list_switchable_pig_instances(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        display_name: str,
    ) -> list[dict[str, object]]:
        """List active owned pigs of one name that provide alternate artwork."""

        rows = await session.fetch_all(
            """
            SELECT instance.pig_instance_id, instance.short_code,
                   instance.display_variant
            FROM pig_instances AS instance
            JOIN pig_templates AS template
              ON template.template_id = instance.template_id
            WHERE instance.owner_player_id = ?
              AND instance.state = 'active'
              AND instance.display_name_snapshot = ?
              AND template.alternate_image_relpath IS NOT NULL
              AND template.alternate_image_relpath != ''
            ORDER BY instance.acquired_at ASC, instance.pig_instance_id ASC
            """,
            (player_id, str(display_name)),
        )
        return [dict(row) for row in rows]

    async def toggle_baogian_instances(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        instance_ids: Sequence[str],
        now: str,
    ) -> tuple[int, str]:
        """Toggle display_variant for the given active baogian instances.

        Returns (updated_count, new_variant).
        """

        return await self.toggle_pig_instances(
            session,
            player_id=player_id,
            instance_ids=instance_ids,
            now=now,
        )

    async def toggle_pig_instances(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        instance_ids: Sequence[str],
        now: str,
    ) -> tuple[int, str]:
        """Toggle default/alternate art for validated owned pig instances."""

        normalized = tuple(dict.fromkeys(instance_ids))
        if not normalized:
            return 0, "pig"
        placeholders = ",".join("?" for _ in normalized)
        rows = await session.fetch_all(
            f"""
            SELECT pig_instance_id, display_variant
            FROM pig_instances
            WHERE owner_player_id = ?
              AND state = 'active'
              AND pig_instance_id IN ({placeholders})
            """,
            (player_id, *normalized),
        )
        if not rows:
            return 0, "pig"
        new_variant = "sticker" if any(
            str(row["display_variant"]) != "sticker" for row in rows
        ) else "pig"
        await session.execute(
            f"""
            UPDATE pig_instances
            SET display_variant = ?, updated_at = ?
            WHERE owner_player_id = ?
              AND state = 'active'
              AND pig_instance_id IN ({placeholders})
            """,
            (new_variant, now, player_id, *normalized),
        )
        return len(rows), new_variant

    @staticmethod
    def random_snapshot_json(snapshot: Mapping[str, object]) -> str:
        return json.dumps(
            dict(snapshot),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


    async def batch_keep_highest(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> bool:
        row = await session.fetch_one(
            "SELECT batch_keep_highest FROM players WHERE player_id = ?",
            (player_id,),
        )
        return bool(row is not None and int(row["batch_keep_highest"]))

    async def set_batch_keep_highest(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        enabled: bool,
        now: str,
    ) -> None:
        await session.execute(
            """
            UPDATE players
            SET batch_keep_highest = ?, updated_at = ?
            WHERE player_id = ?
            """,
            (1 if enabled else 0, now, player_id),
        )
