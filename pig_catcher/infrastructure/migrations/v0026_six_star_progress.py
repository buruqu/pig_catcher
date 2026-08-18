"""Persist per-player permanent six-star catch/cook progress stacks."""

from .model import Migration

MIGRATION_0026 = Migration(
    version=26,
    name="player_six_star_progress",
    statements=(
        """
        CREATE TABLE player_six_star_progress (
            player_id TEXT PRIMARY KEY
                REFERENCES players(player_id) ON DELETE CASCADE,
            stacks INTEGER NOT NULL DEFAULT 0
                CHECK (stacks BETWEEN 0 AND 5),
            source_food_instance_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
)
