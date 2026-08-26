"""Schema 36: data-driven weekly competitions, entries and settlement awards."""

from .model import Migration

MIGRATION_0036 = Migration(
    version=36,
    name="weekly-competitions",
    statements=(
        """
        CREATE TABLE weekly_competitions(
            competition_id TEXT PRIMARY KEY,
            season_number INTEGER NOT NULL UNIQUE CHECK(season_number >= 1),
            definition_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            source_result_type TEXT NOT NULL,
            source_field TEXT NOT NULL,
            aggregation TEXT NOT NULL CHECK(aggregation IN ('sum', 'max', 'min', 'count')),
            sort_direction TEXT NOT NULL CHECK(sort_direction IN ('asc', 'desc')),
            metric_label TEXT NOT NULL,
            metric_unit TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('scheduled', 'active', 'settled', 'cancelled')),
            ruleset_version INTEGER NOT NULL CHECK(ruleset_version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(starts_at < ends_at)
        )
        """,
        """
        CREATE TABLE weekly_competition_entries(
            entry_id TEXT PRIMARY KEY,
            competition_id TEXT NOT NULL REFERENCES weekly_competitions(competition_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            receipt_id TEXT NOT NULL REFERENCES command_receipts(receipt_id) ON DELETE RESTRICT,
            source_object_type TEXT NOT NULL,
            source_object_id TEXT NOT NULL,
            metric_value REAL NOT NULL,
            rarity INTEGER NOT NULL DEFAULT 0 CHECK(rarity BETWEEN 0 AND 6),
            source_snapshot_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(competition_id, receipt_id)
        )
        """,
        """
        CREATE TABLE weekly_competition_settlements(
            settlement_id TEXT PRIMARY KEY,
            competition_id TEXT NOT NULL REFERENCES weekly_competitions(competition_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            participant_count INTEGER NOT NULL CHECK(participant_count >= 0),
            winner_count INTEGER NOT NULL CHECK(winner_count BETWEEN 0 AND 10),
            settled_at TEXT NOT NULL,
            UNIQUE(competition_id, scope_id)
        )
        """,
        """
        CREATE TABLE weekly_competition_awards(
            award_id TEXT PRIMARY KEY,
            settlement_id TEXT NOT NULL REFERENCES weekly_competition_settlements(settlement_id) ON DELETE CASCADE,
            competition_id TEXT NOT NULL REFERENCES weekly_competitions(competition_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            final_rank INTEGER NOT NULL CHECK(final_rank BETWEEN 1 AND 10),
            score_value REAL NOT NULL,
            reward_snapshot_json TEXT NOT NULL,
            notification_status TEXT NOT NULL DEFAULT 'pending' CHECK(
                notification_status IN ('pending', 'claimed', 'sent', 'failed')
            ),
            notification_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(competition_id, scope_id, player_id),
            UNIQUE(competition_id, scope_id, final_rank)
        )
        """,
        """
        CREATE INDEX idx_weekly_competitions_status_window
        ON weekly_competitions(status, starts_at, ends_at)
        """,
        """
        CREATE INDEX idx_weekly_entries_scope_player
        ON weekly_competition_entries(competition_id, scope_id, player_id, occurred_at)
        """,
        """
        CREATE INDEX idx_weekly_entries_source
        ON weekly_competition_entries(competition_id, source_object_type, source_object_id)
        """,
        """
        CREATE INDEX idx_weekly_awards_notification
        ON weekly_competition_awards(player_id, notification_status, created_at)
        """,
    ),
)


__all__ = ["MIGRATION_0036"]
