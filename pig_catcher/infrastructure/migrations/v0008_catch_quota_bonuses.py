"""Persist non-stackable weekly and capped permanent catch-window bonuses."""

from .model import Migration

MIGRATION_0008 = Migration(
    version=8,
    name="catch_quota_bonuses",
    statements=(
        """
        CREATE TABLE player_catch_quota_bonuses (
            player_id TEXT PRIMARY KEY
                REFERENCES players(player_id) ON DELETE CASCADE,
            permanent_bonus INTEGER NOT NULL DEFAULT 0
                CHECK (permanent_bonus BETWEEN 0 AND 5),
            weekly_bonus INTEGER NOT NULL DEFAULT 0
                CHECK (weekly_bonus BETWEEN 0 AND 5),
            weekly_expires_at TEXT,
            weekly_source_food_instance_id TEXT,
            permanent_source_food_instance_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_player_catch_quota_weekly_expiry
        ON player_catch_quota_bonuses(weekly_expires_at)
        """,
    ),
)
