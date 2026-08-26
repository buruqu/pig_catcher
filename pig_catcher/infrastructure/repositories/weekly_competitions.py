"""SQLite primitives for weekly competitions.

The repository owns no transactions and only exposes deterministic persistence
operations.  Score aggregation and reward policy remain in the service/domain
layers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..database import DatabaseSession

_SOURCE_TABLES: Mapping[str, tuple[str, str, Mapping[str, str]]] = {
    "pig": (
        "pig_instances",
        "pig_instance_id",
        {
            "official_value": "source.official_value",
            "size_value": "source.size_value",
            "weight_value": "source.weight_value",
            "rarity": "source.rarity",
        },
    ),
    "food": (
        "food_instances",
        "food_instance_id",
        {
            "official_value": "source.official_value",
            "portion_weight": "source.portion_weight",
            "rarity": "source.rarity",
        },
    ),
}


class WeeklyCompetitionRepository:
    """Persist event snapshots, immutable score entries and settlement awards."""

    async def competition_by_definition(
        self,
        session: DatabaseSession,
        *,
        definition_key: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            "SELECT * FROM weekly_competitions WHERE definition_key = ?",
            (definition_key,),
        )
        return dict(row) if row is not None else None

    async def latest_competition(self, session: DatabaseSession) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT * FROM weekly_competitions
            WHERE status <> 'cancelled'
            ORDER BY season_number DESC
            LIMIT 1
            """
        )
        return dict(row) if row is not None else None

    async def competitions_for_refresh(self, session: DatabaseSession) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT * FROM weekly_competitions
            WHERE status IN ('scheduled', 'active')
            ORDER BY season_number
            """
        )
        return [dict(row) for row in rows]

    async def insert_competition(
        self,
        session: DatabaseSession,
        *,
        values: Mapping[str, object],
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO weekly_competitions(
                competition_id, season_number, definition_key, name,
                source_result_type, source_field, aggregation, sort_direction,
                metric_label, metric_unit, definition_json, starts_at, ends_at,
                status, ruleset_version, created_at, updated_at
            ) VALUES (
                :competition_id, :season_number, :definition_key, :name,
                :source_result_type, :source_field, :aggregation, :sort_direction,
                :metric_label, :metric_unit, :definition_json, :starts_at, :ends_at,
                :status, :ruleset_version, :created_at, :updated_at
            )
            """,
            values,
        )
        return cursor.rowcount == 1

    async def set_competition_status(
        self,
        session: DatabaseSession,
        *,
        competition_id: str,
        expected_statuses: Sequence[str],
        status: str,
        now: str,
    ) -> bool:
        if not expected_statuses:
            return False
        placeholders = ",".join("?" for _ in expected_statuses)
        cursor = await session.execute(
            f"""
            UPDATE weekly_competitions
            SET status = ?, updated_at = ?
            WHERE competition_id = ? AND status IN ({placeholders})
            """,
            (status, now, competition_id, *expected_statuses),
        )
        return cursor.rowcount == 1

    async def source_receipt_rows(
        self,
        session: DatabaseSession,
        *,
        source_result_type: str,
        source_field: str,
        command_names: Sequence[str],
        starts_at: str,
        ends_at: str,
        receipt_id: str | None = None,
    ) -> list[dict[str, object]]:
        source_config = _SOURCE_TABLES.get(source_result_type)
        if source_config is None:
            raise ValueError(f"Unsupported weekly source result type: {source_result_type}")
        source_table, source_id_column, metric_fields = source_config
        metric_expression = "1" if not source_field else metric_fields.get(source_field)
        if metric_expression is None:
            raise ValueError(f"Unsupported weekly metric field: {source_result_type}.{source_field}")
        normalized_commands = tuple(dict.fromkeys(str(value) for value in command_names if str(value)))
        if not normalized_commands:
            return []
        command_placeholders = ",".join("?" for _ in normalized_commands)
        receipt_filter = " AND receipt.receipt_id = ?" if receipt_id else ""
        parameters: tuple[object, ...] = (
            source_result_type,
            *normalized_commands,
            starts_at,
            ends_at,
            *((receipt_id,) if receipt_id else ()),
        )
        rows = await session.fetch_all(
            f"""
            SELECT
                receipt.receipt_id,
                receipt.scope_id,
                receipt.player_id,
                receipt.command_name,
                receipt.result_type,
                receipt.result_object_id,
                receipt.created_at AS occurred_at,
                source.{source_id_column} AS source_object_id,
                source.display_name_snapshot,
                source.rarity,
                source.official_value,
                {metric_expression} AS metric_value
            FROM command_receipts AS receipt
            JOIN {source_table} AS source
              ON source.{source_id_column} = receipt.result_object_id
            WHERE receipt.business_status = 'committed'
              AND receipt.player_id IS NOT NULL
              AND receipt.result_type = ?
              AND receipt.command_name IN ({command_placeholders})
              AND receipt.created_at >= ?
              AND receipt.created_at < ?
              {receipt_filter}
            ORDER BY receipt.created_at, receipt.receipt_id
            """,
            parameters,
        )
        return [dict(row) for row in rows]

    async def insert_entry(
        self,
        session: DatabaseSession,
        *,
        values: Mapping[str, object],
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO weekly_competition_entries(
                entry_id, competition_id, scope_id, player_id, receipt_id,
                source_object_type, source_object_id, metric_value, rarity,
                source_snapshot_json, occurred_at, created_at
            ) VALUES (
                :entry_id, :competition_id, :scope_id, :player_id, :receipt_id,
                :source_object_type, :source_object_id, :metric_value, :rarity,
                :source_snapshot_json, :occurred_at, :created_at
            )
            """,
            values,
        )
        return cursor.rowcount == 1

    async def entry_rows(
        self,
        session: DatabaseSession,
        *,
        competition_id: str,
        scope_id: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT entry.*, player.display_name
            FROM weekly_competition_entries AS entry
            JOIN players AS player ON player.player_id = entry.player_id
            WHERE entry.competition_id = ? AND entry.scope_id = ?
            ORDER BY entry.occurred_at, entry.entry_id
            """,
            (competition_id, scope_id),
        )
        return [dict(row) for row in rows]

    async def entry_scope_ids(
        self,
        session: DatabaseSession,
        *,
        competition_id: str,
    ) -> tuple[str, ...]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT scope_id
            FROM weekly_competition_entries
            WHERE competition_id = ?
            ORDER BY scope_id
            """,
            (competition_id,),
        )
        return tuple(str(row["scope_id"]) for row in rows)

    async def settlement_row(
        self,
        session: DatabaseSession,
        *,
        competition_id: str,
        scope_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT * FROM weekly_competition_settlements
            WHERE competition_id = ? AND scope_id = ?
            """,
            (competition_id, scope_id),
        )
        return dict(row) if row is not None else None

    async def insert_settlement(
        self,
        session: DatabaseSession,
        *,
        settlement_id: str,
        competition_id: str,
        scope_id: str,
        participant_count: int,
        winner_count: int,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO weekly_competition_settlements(
                settlement_id, competition_id, scope_id,
                participant_count, winner_count, settled_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (settlement_id, competition_id, scope_id, participant_count, winner_count, now),
        )
        return cursor.rowcount == 1

    async def insert_award(
        self,
        session: DatabaseSession,
        *,
        values: Mapping[str, object],
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO weekly_competition_awards(
                award_id, settlement_id, competition_id, scope_id, player_id,
                final_rank, score_value, reward_snapshot_json,
                notification_status, created_at, updated_at
            ) VALUES (
                :award_id, :settlement_id, :competition_id, :scope_id, :player_id,
                :final_rank, :score_value, :reward_snapshot_json,
                'pending', :created_at, :updated_at
            )
            """,
            values,
        )
        return cursor.rowcount == 1

    async def pending_award_row(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        stale_claimed_before: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT award.*, competition.name, competition.season_number,
                   competition.metric_unit, player.display_name
            FROM weekly_competition_awards AS award
            JOIN weekly_competitions AS competition
              ON competition.competition_id = award.competition_id
            JOIN players AS player ON player.player_id = award.player_id
            WHERE award.player_id = ?
              AND (
                award.notification_status IN ('pending', 'failed')
                OR (
                  award.notification_status = 'claimed'
                  AND award.updated_at <= ?
                )
              )
            ORDER BY award.created_at, award.award_id
            LIMIT 1
            """,
            (player_id, stale_claimed_before),
        )
        return dict(row) if row is not None else None

    async def player_award_for_competition(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        competition_name: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT award.*, competition.name, competition.season_number
            FROM weekly_competition_awards AS award
            JOIN weekly_competitions AS competition
              ON competition.competition_id = award.competition_id
            WHERE award.player_id = ?
              AND lower(competition.name) = lower(?)
            ORDER BY competition.season_number DESC
            LIMIT 1
            """,
            (player_id, competition_name),
        )
        return dict(row) if row is not None else None

    async def update_award_notification(
        self,
        session: DatabaseSession,
        *,
        award_id: str,
        from_status: str,
        to_status: str,
        error: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE weekly_competition_awards
            SET notification_status = ?, notification_error = ?, updated_at = ?
            WHERE award_id = ? AND notification_status = ?
            """,
            (to_status, str(error or "")[:500], now, award_id, from_status),
        )
        return cursor.rowcount == 1

    async def owns_reward(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        reward_type: str,
        reward_id: str,
    ) -> bool:
        row = await session.fetch_one(
            """
            SELECT 1 FROM achievement_reward_inventory
            WHERE player_id = ? AND reward_type = ? AND reward_id = ? AND quantity > 0
            """,
            (player_id, reward_type, reward_id),
        )
        return row is not None


__all__ = ["WeeklyCompetitionRepository"]
