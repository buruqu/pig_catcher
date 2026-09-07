"""Schema 66: explicit operator-authorized, restart-safe season handoff."""

from .model import Migration

MIGRATION_0066 = Migration(
    version=66,
    name="weekly-transition",
    statements=(
        """
        CREATE TABLE weekly_transitions(
            transition_id TEXT PRIMARY KEY,
            previous_id TEXT NOT NULL UNIQUE REFERENCES weekly_competitions(competition_id),
            next_id TEXT NOT NULL UNIQUE REFERENCES weekly_competitions(competition_id),
            scope_ids_json TEXT NOT NULL,
            delay_seconds INTEGER NOT NULL CHECK(delay_seconds >= 120),
            not_before TEXT NOT NULL,
            created_at TEXT NOT NULL,
            activated_at TEXT,
            CHECK(previous_id <> next_id)
        )
        """,
    ),
)
