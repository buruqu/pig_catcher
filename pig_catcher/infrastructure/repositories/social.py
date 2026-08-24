"""SQLite repository for gifts, trades, showcases, rankings, and global records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...domain.enums import AssetKind, RecordType, TradeStatus
from ..database import DatabaseSession

_ASSET_TABLES: dict[AssetKind, tuple[str, str]] = {
    AssetKind.PIG: ("pig_instances", "pig_instance_id"),
    AssetKind.FOOD: ("food_instances", "food_instance_id"),
}
_STAT_FIELDS = frozenset(
    {
        "total_catches",
        "total_cooks",
        "gifts_sent",
        "gifts_received",
        "trades_completed",
    }
)


def _asset_table(kind: AssetKind) -> tuple[str, str]:
    return _ASSET_TABLES[kind]


class SocialRepository:
    """Run social and ranking SQL without owning transaction boundaries."""

    async def get_last_cook_at(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> str | None:
        row = await session.fetch_one(
            """
            SELECT last_cook_at
            FROM player_statistics
            WHERE player_id = ?
            """,
            (player_id,),
        )
        return str(row["last_cook_at"]) if row is not None and row["last_cook_at"] else None

    async def increment_statistic(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        field: str,
        now: str,
        amount: int = 1,
    ) -> None:
        if field not in _STAT_FIELDS:
            raise ValueError(f"Unsupported player statistic: {field}")
        if amount <= 0:
            raise ValueError("Statistic increment must be positive")
        timestamp_field = {
            "total_catches": "last_catch_at",
            "total_cooks": "last_cook_at",
        }.get(field)
        timestamp_sql = f", {timestamp_field} = ?" if timestamp_field else ""
        parameters: tuple[object, ...]
        if timestamp_field:
            parameters = (amount, now, now, player_id)
        else:
            parameters = (amount, now, player_id)
        cursor = await session.execute(
            f"""
            UPDATE player_statistics
            SET {field} = {field} + ?{timestamp_sql}, updated_at = ?
            WHERE player_id = ?
            """,
            parameters,
        )
        if cursor.rowcount != 1:
            raise RuntimeError("玩家统计行不存在。")

    async def update_global_record(
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
        existing = await session.fetch_one(
            """
            SELECT record_value
            FROM group_global_records
            WHERE scope_id = ? AND record_type = ?
            """,
            (scope_id, record_type.value),
        )
        if existing is not None and float(existing["record_value"]) >= record_value:
            return False
        await session.execute(
            """
            INSERT INTO group_global_records(
                scope_id, record_type, pig_instance_id, template_id,
                record_value, player_id, achieved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_id, record_type) DO UPDATE SET
                pig_instance_id = excluded.pig_instance_id,
                template_id = excluded.template_id,
                record_value = excluded.record_value,
                player_id = excluded.player_id,
                achieved_at = excluded.achieved_at
            WHERE excluded.record_value > group_global_records.record_value
            """,
            (
                scope_id,
                record_type.value,
                pig_instance_id,
                template_id,
                record_value,
                player_id,
                now,
            ),
        )
        return True

    async def insert_giant_sighting(
        self,
        session: DatabaseSession,
        *,
        pig_instance_id: str,
        scope_id: str,
        player_id: str,
        template_id: str,
        size_value: float,
        weight_value: float,
        giant_score: float,
        size_qualified: bool,
        weight_qualified: bool,
        now: str,
    ) -> bool:
        if not size_qualified and not weight_qualified:
            return False
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO giant_sightings(
                pig_instance_id, scope_id, player_id, template_id,
                size_value, weight_value, giant_score,
                size_qualified, weight_qualified, achieved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pig_instance_id,
                scope_id,
                player_id,
                template_id,
                size_value,
                weight_value,
                giant_score,
                int(size_qualified),
                int(weight_qualified),
                now,
            ),
        )
        return cursor.rowcount == 1

    async def global_records(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
    ) -> list[dict[str, object]]:
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
            FROM group_global_records AS record
            JOIN pig_templates AS template
              ON template.template_id = record.template_id
            JOIN pig_instances AS instance
              ON instance.pig_instance_id = record.pig_instance_id
            JOIN players AS player
              ON player.player_id = record.player_id
            WHERE record.scope_id = ?
            ORDER BY record.record_type
            """,
            (scope_id,),
        )
        return [dict(row) for row in rows]

    async def giant_sightings(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        limit: int,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT
                sighting.pig_instance_id,
                sighting.size_value,
                sighting.weight_value,
                sighting.giant_score,
                sighting.size_qualified,
                sighting.weight_qualified,
                sighting.achieved_at,
                template.display_name,
                template.rarity,
                instance.short_code,
                player.display_name AS holder_display_name
            FROM giant_sightings AS sighting
            JOIN pig_templates AS template
              ON template.template_id = sighting.template_id
            JOIN pig_instances AS instance
              ON instance.pig_instance_id = sighting.pig_instance_id
            JOIN players AS player
              ON player.player_id = sighting.player_id
            WHERE sighting.scope_id = ?
            ORDER BY sighting.achieved_at DESC, sighting.pig_instance_id
            LIMIT ?
            """,
            (scope_id, limit),
        )
        return [dict(row) for row in rows]

    async def trade_id_exists(
        self,
        session: DatabaseSession,
        *,
        trade_id: str,
    ) -> bool:
        row = await session.fetch_one(
            "SELECT 1 FROM trade_offers WHERE trade_id = ?",
            (trade_id,),
        )
        return row is not None

    async def transfer_active_asset(
        self,
        session: DatabaseSession,
        *,
        asset_kind: AssetKind,
        asset_instance_id: str,
        scope_id: str,
        from_player_id: str,
        to_player_id: str,
        now: str,
    ) -> bool:
        table, id_column = _asset_table(asset_kind)
        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET owner_player_id = ?, updated_at = ?
            WHERE {id_column} = ?
              AND scope_id = ?
              AND owner_player_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
            """,
            (
                to_player_id,
                now,
                asset_instance_id,
                scope_id,
                from_player_id,
            ),
        )
        return cursor.rowcount == 1

    async def lock_asset_for_trade(
        self,
        session: DatabaseSession,
        *,
        asset_kind: AssetKind,
        asset_instance_id: str,
        scope_id: str,
        owner_player_id: str,
        trade_id: str,
        now: str,
    ) -> bool:
        table, id_column = _asset_table(asset_kind)
        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET state = 'locked-for-trade', locked_trade_id = ?, updated_at = ?
            WHERE {id_column} = ?
              AND scope_id = ?
              AND owner_player_id = ?
              AND state = 'active'
              AND locked_trade_id IS NULL
              AND is_favorite = 0
            """,
            (trade_id, now, asset_instance_id, scope_id, owner_player_id),
        )
        return cursor.rowcount == 1

    async def unlock_trade_asset(
        self,
        session: DatabaseSession,
        *,
        asset_kind: AssetKind,
        asset_instance_id: str,
        trade_id: str,
        now: str,
    ) -> bool:
        table, id_column = _asset_table(asset_kind)
        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET state = 'active', locked_trade_id = NULL, updated_at = ?
            WHERE {id_column} = ?
              AND state = 'locked-for-trade'
              AND locked_trade_id = ?
            """,
            (now, asset_instance_id, trade_id),
        )
        return cursor.rowcount == 1

    async def accept_trade_asset(
        self,
        session: DatabaseSession,
        *,
        asset_kind: AssetKind,
        asset_instance_id: str,
        trade_id: str,
        scope_id: str,
        sender_player_id: str,
        recipient_player_id: str,
        now: str,
    ) -> bool:
        table, id_column = _asset_table(asset_kind)
        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET
                owner_player_id = ?,
                state = 'active',
                locked_trade_id = NULL,
                updated_at = ?
            WHERE {id_column} = ?
              AND scope_id = ?
              AND owner_player_id = ?
              AND state = 'locked-for-trade'
              AND locked_trade_id = ?
            """,
            (
                recipient_player_id,
                now,
                asset_instance_id,
                scope_id,
                sender_player_id,
                trade_id,
            ),
        )
        return cursor.rowcount == 1

    async def insert_trade_offer(
        self,
        session: DatabaseSession,
        *,
        trade_id: str,
        scope_id: str,
        sender_player_id: str,
        recipient_player_id: str,
        asset_kind: AssetKind,
        asset_instance_id: str,
        price: int,
        created_at: str,
        expires_at: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO trade_offers(
                trade_id, scope_id, sender_player_id, recipient_player_id,
                asset_kind, asset_instance_id, price, status,
                created_at, expires_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL)
            """,
            (
                trade_id,
                scope_id,
                sender_player_id,
                recipient_player_id,
                asset_kind.value,
                asset_instance_id,
                price,
                created_at,
                expires_at,
            ),
        )

    async def trade_row(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        trade_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT
                offer.*,
                sender.display_name AS sender_display_name,
                recipient.display_name AS recipient_display_name,
                COALESCE(pig.display_name_snapshot, food.display_name_snapshot, '') AS asset_display_name,
                COALESCE(pig.short_code, food.short_code, '') AS asset_short_code,
                COALESCE(pig.rarity, food.rarity, 0) AS asset_rarity,
                COALESCE(pig.official_value, food.official_value, 0) AS asset_official_value
            FROM trade_offers AS offer
            JOIN players AS sender
              ON sender.player_id = offer.sender_player_id
            JOIN players AS recipient
              ON recipient.player_id = offer.recipient_player_id
            LEFT JOIN pig_instances AS pig
              ON offer.asset_kind = 'pig'
             AND pig.pig_instance_id = offer.asset_instance_id
            LEFT JOIN food_instances AS food
              ON offer.asset_kind = 'food'
             AND food.food_instance_id = offer.asset_instance_id
            WHERE offer.scope_id = ? AND offer.trade_id = ?
            """,
            (scope_id, trade_id),
        )
        return dict(row) if row is not None else None

    async def resolve_trade(
        self,
        session: DatabaseSession,
        *,
        trade_id: str,
        expected_status: TradeStatus,
        new_status: TradeStatus,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE trade_offers
            SET status = ?, resolved_at = ?
            WHERE trade_id = ? AND status = ?
            """,
            (new_status.value, now, trade_id, expected_status.value),
        )
        return cursor.rowcount == 1

    async def expire_stale_offers(
        self,
        session: DatabaseSession,
        *,
        now: str,
    ) -> int:
        rows = await session.fetch_all(
            """
            SELECT trade_id, asset_kind, asset_instance_id
            FROM trade_offers
            WHERE status = 'pending' AND expires_at <= ?
            ORDER BY expires_at, trade_id
            """,
            (now,),
        )
        expired = 0
        for row in rows:
            changed = await self.resolve_trade(
                session,
                trade_id=str(row["trade_id"]),
                expected_status=TradeStatus.PENDING,
                new_status=TradeStatus.EXPIRED,
                now=now,
            )
            if not changed:
                continue
            await self.unlock_trade_asset(
                session,
                asset_kind=AssetKind(str(row["asset_kind"])),
                asset_instance_id=str(row["asset_instance_id"]),
                trade_id=str(row["trade_id"]),
                now=now,
            )
            expired += 1
        return expired

    async def cancel_pending_offers_for_players(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        player_ids: Sequence[str],
        now: str,
    ) -> int:
        """取消处罚对象参与的待处理报价并释放卖方资产锁。"""

        normalized = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in player_ids
                if str(value).strip()
            )
        )
        if not normalized:
            return 0
        placeholders = ",".join("?" for _ in normalized)
        rows = await session.fetch_all(
            f"""
            SELECT trade_id, asset_kind, asset_instance_id
            FROM trade_offers
            WHERE scope_id = ?
              AND status = 'pending'
              AND (
                  sender_player_id IN ({placeholders})
                  OR recipient_player_id IN ({placeholders})
              )
            ORDER BY created_at, trade_id
            """,
            (scope_id, *normalized, *normalized),
        )
        cancelled = 0
        for row in rows:
            changed = await self.resolve_trade(
                session,
                trade_id=str(row["trade_id"]),
                expected_status=TradeStatus.PENDING,
                new_status=TradeStatus.CANCELLED,
                now=now,
            )
            if not changed:
                continue
            await self.unlock_trade_asset(
                session,
                asset_kind=AssetKind(str(row["asset_kind"])),
                asset_instance_id=str(row["asset_instance_id"]),
                trade_id=str(row["trade_id"]),
                now=now,
            )
            cancelled += 1
        return cancelled

    async def insert_transfer_event(
        self,
        session: DatabaseSession,
        *,
        transfer_event_id: str,
        scope_id: str,
        asset_kind: AssetKind,
        asset_instance_id: str,
        from_player_id: str,
        to_player_id: str,
        transfer_type: str,
        trade_id: str | None,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO asset_transfer_events(
                transfer_event_id, scope_id, asset_kind, asset_instance_id,
                from_player_id, to_player_id, transfer_type, trade_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transfer_event_id,
                scope_id,
                asset_kind.value,
                asset_instance_id,
                from_player_id,
                to_player_id,
                transfer_type,
                trade_id,
                now,
            ),
        )

    async def clear_showcase_asset(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        asset_kind: AssetKind,
        asset_instance_id: str,
        now: str,
    ) -> None:
        column = (
            "pig_instance_id"
            if asset_kind is AssetKind.PIG
            else "food_instance_id"
        )
        await session.execute(
            f"""
            UPDATE display_preferences
            SET {column} = NULL, updated_at = ?
            WHERE player_id = ? AND {column} = ?
            """,
            (now, player_id, asset_instance_id),
        )

    async def set_showcase(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        asset_kind: AssetKind,
        asset_instance_id: str | None,
        now: str,
    ) -> None:
        if asset_kind is AssetKind.PIG:
            await session.execute(
                """
                INSERT INTO display_preferences(
                    player_id, pig_instance_id, food_instance_id, updated_at
                )
                VALUES (?, ?, NULL, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    pig_instance_id = excluded.pig_instance_id,
                    updated_at = excluded.updated_at
                """,
                (player_id, asset_instance_id, now),
            )
            return
        await session.execute(
            """
            INSERT INTO display_preferences(
                player_id, pig_instance_id, food_instance_id, updated_at
            )
            VALUES (?, NULL, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                food_instance_id = excluded.food_instance_id,
                updated_at = excluded.updated_at
            """,
            (player_id, asset_instance_id, now),
        )

    async def showcase_row(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
    ) -> dict[str, object]:
        row = await session.fetch_one(
            """
            SELECT
                preference.pig_instance_id,
                preference.food_instance_id,
                pig.display_name_snapshot AS pig_display_name,
                pig.short_code AS pig_short_code,
                food.display_name_snapshot AS food_display_name,
                food.short_code AS food_short_code
            FROM players AS player
            LEFT JOIN display_preferences AS preference
              ON preference.player_id = player.player_id
            LEFT JOIN pig_instances AS pig
              ON pig.pig_instance_id = preference.pig_instance_id
             AND pig.owner_player_id = player.player_id
             AND pig.state IN ('active', 'locked-for-trade')
            LEFT JOIN food_instances AS food
              ON food.food_instance_id = preference.food_instance_id
             AND food.owner_player_id = player.player_id
             AND food.state IN ('active', 'locked-for-trade')
            WHERE player.player_id = ?
            """,
            (player_id,),
        )
        return dict(row) if row is not None else {}

    async def trade_page(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        status: TradeStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, object]]]:
        status_clause = ""
        parameters: list[object] = [player_id, player_id]
        if status is not None:
            status_clause = "AND offer.status = ?"
            parameters.append(status.value)
        count_row = await session.fetch_one(
            f"""
            SELECT COUNT(*) AS total_count
            FROM trade_offers AS offer
            WHERE (
                offer.sender_player_id = ?
                OR offer.recipient_player_id = ?
            )
            {status_clause}
            """,
            parameters,
        )
        rows = await session.fetch_all(
            f"""
            SELECT
                offer.*,
                sender.display_name AS sender_display_name,
                recipient.display_name AS recipient_display_name,
                COALESCE(pig.display_name_snapshot, food.display_name_snapshot, '') AS asset_display_name,
                COALESCE(pig.short_code, food.short_code, '') AS asset_short_code,
                COALESCE(pig.rarity, food.rarity, 0) AS asset_rarity
            FROM trade_offers AS offer
            JOIN players AS sender
              ON sender.player_id = offer.sender_player_id
            JOIN players AS recipient
              ON recipient.player_id = offer.recipient_player_id
            LEFT JOIN pig_instances AS pig
              ON offer.asset_kind = 'pig'
             AND pig.pig_instance_id = offer.asset_instance_id
            LEFT JOIN food_instances AS food
              ON offer.asset_kind = 'food'
             AND food.food_instance_id = offer.asset_instance_id
            WHERE (
                offer.sender_player_id = ?
                OR offer.recipient_player_id = ?
            )
            {status_clause}
            ORDER BY offer.created_at DESC, offer.trade_id
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        )
        total = int(count_row["total_count"]) if count_row is not None else 0
        return total, [dict(row) for row in rows]

    async def ranking_base_rows(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        giant_size_threshold_cm: float,
        giant_weight_threshold_kg: float,
    ) -> tuple[int, int, list[dict[str, object]]]:
        pig_total_row = await session.fetch_one(
            """
            SELECT COUNT(*) AS total_count
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
            """,
            (scope_id,),
        )
        food_total_row = await session.fetch_one(
            """
            SELECT COUNT(*) AS total_count
            FROM food_templates AS template
            LEFT JOIN scope_food_templates AS allowed
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
            """,
            (scope_id,),
        )
        rows = await session.fetch_all(
            """
            SELECT
                player.player_id,
                player.display_name,
                player.coin_balance,
                player.experience,
                player.created_at,
                player.updated_at,
                statistic.total_catches,
                statistic.total_cooks,
                statistic.last_catch_at,
                statistic.last_cook_at,
                (
                    SELECT COUNT(*)
                    FROM pig_catalog_entries AS catalog
                    WHERE catalog.player_id = player.player_id
                ) AS pig_catalog_count,
                (
                    SELECT COUNT(*)
                    FROM food_catalog_entries AS catalog
                    WHERE catalog.player_id = player.player_id
                ) AS food_catalog_count,
                (
                    SELECT MAX(catalog.last_acquired_at)
                    FROM pig_catalog_entries AS catalog
                    WHERE catalog.player_id = player.player_id
                ) AS pig_catalog_reached_at,
                (
                    SELECT MAX(catalog.last_acquired_at)
                    FROM food_catalog_entries AS catalog
                    WHERE catalog.player_id = player.player_id
                ) AS food_catalog_reached_at,
                (
                    SELECT COUNT(*)
                    FROM pig_instances AS pig
                    WHERE pig.owner_player_id = player.player_id
                      AND pig.state IN ('active', 'locked-for-trade')
                ) AS active_pigs,
                (
                    SELECT COUNT(*)
                    FROM food_instances AS food
                    WHERE food.owner_player_id = player.player_id
                      AND food.state IN ('active', 'locked-for-trade')
                ) AS active_foods,
                (
                    SELECT COALESCE(SUM(pig.official_value), 0)
                    FROM pig_instances AS pig
                    WHERE pig.owner_player_id = player.player_id
                      AND pig.state IN ('active', 'locked-for-trade')
                ) + (
                    SELECT COALESCE(SUM(food.official_value), 0)
                    FROM food_instances AS food
                    WHERE food.owner_player_id = player.player_id
                      AND food.state IN ('active', 'locked-for-trade')
                ) AS asset_value,
                COALESCE(
                    preference.pig_instance_id,
                    (
                        SELECT pig.pig_instance_id
                        FROM pig_instances AS pig
                        WHERE pig.owner_player_id = player.player_id
                          AND pig.state IN ('active', 'locked-for-trade')
                        ORDER BY pig.official_value DESC, pig.acquired_at, pig.pig_instance_id
                        LIMIT 1
                    )
                ) AS showcase_pig_id,
                COALESCE(
                    preference.food_instance_id,
                    (
                        SELECT food.food_instance_id
                        FROM food_instances AS food
                        WHERE food.owner_player_id = player.player_id
                          AND food.state IN ('active', 'locked-for-trade')
                        ORDER BY food.official_value DESC, food.acquired_at, food.food_instance_id
                        LIMIT 1
                    )
                ) AS showcase_food_id,
                (
                    SELECT pig.pig_instance_id
                    FROM pig_instances AS pig
                    WHERE pig.owner_player_id = player.player_id
                      AND pig.state IN ('active', 'locked-for-trade')
                    ORDER BY
                        (
                            0.55 * pig.size_value / ?
                            + 0.45 * pig.weight_value / ?
                        ) DESC,
                        pig.acquired_at,
                        pig.pig_instance_id
                    LIMIT 1
                ) AS giant_pig_id,
                (
                    SELECT pig.size_value
                    FROM pig_instances AS pig
                    WHERE pig.owner_player_id = player.player_id
                      AND pig.state IN ('active', 'locked-for-trade')
                    ORDER BY
                        (
                            0.55 * pig.size_value / ?
                            + 0.45 * pig.weight_value / ?
                        ) DESC,
                        pig.acquired_at,
                        pig.pig_instance_id
                    LIMIT 1
                ) AS giant_size_value,
                (
                    SELECT pig.weight_value
                    FROM pig_instances AS pig
                    WHERE pig.owner_player_id = player.player_id
                      AND pig.state IN ('active', 'locked-for-trade')
                    ORDER BY
                        (
                            0.55 * pig.size_value / ?
                            + 0.45 * pig.weight_value / ?
                        ) DESC,
                        pig.acquired_at,
                        pig.pig_instance_id
                    LIMIT 1
                ) AS giant_weight_value,
                (
                    SELECT pig.acquired_at
                    FROM pig_instances AS pig
                    WHERE pig.owner_player_id = player.player_id
                      AND pig.state IN ('active', 'locked-for-trade')
                    ORDER BY
                        (
                            0.55 * pig.size_value / ?
                            + 0.45 * pig.weight_value / ?
                        ) DESC,
                        pig.acquired_at,
                        pig.pig_instance_id
                    LIMIT 1
                ) AS giant_reached_at,
                (
                    SELECT MAX(ledger.created_at)
                    FROM currency_ledger AS ledger
                    WHERE ledger.player_id = player.player_id
                ) AS coin_reached_at
            FROM players AS player
            JOIN player_statistics AS statistic
              ON statistic.player_id = player.player_id
            LEFT JOIN display_preferences AS preference
              ON preference.player_id = player.player_id
             AND (
                 preference.pig_instance_id IS NULL
                 OR EXISTS(
                     SELECT 1
                     FROM pig_instances AS shown_pig
                     WHERE shown_pig.pig_instance_id = preference.pig_instance_id
                       AND shown_pig.owner_player_id = player.player_id
                       AND shown_pig.state IN ('active', 'locked-for-trade')
                 )
             )
             AND (
                 preference.food_instance_id IS NULL
                 OR EXISTS(
                     SELECT 1
                     FROM food_instances AS shown_food
                     WHERE shown_food.food_instance_id = preference.food_instance_id
                       AND shown_food.owner_player_id = player.player_id
                       AND shown_food.state IN ('active', 'locked-for-trade')
                 )
             )
            WHERE player.scope_id = ?
            ORDER BY player.player_id
            """,
            (
                giant_size_threshold_cm,
                giant_weight_threshold_kg,
                giant_size_threshold_cm,
                giant_weight_threshold_kg,
                giant_size_threshold_cm,
                giant_weight_threshold_kg,
                giant_size_threshold_cm,
                giant_weight_threshold_kg,
                scope_id,
            ),
        )
        pig_total = int(pig_total_row["total_count"]) if pig_total_row else 0
        food_total = int(food_total_row["total_count"]) if food_total_row else 0
        return pig_total, food_total, [dict(row) for row in rows]

    @staticmethod
    def result_payload(row: Mapping[str, object]) -> dict[str, object]:
        """Copy one SQLite mapping to a JSON-safe primitive dictionary."""

        return {str(key): row[key] for key in row}
