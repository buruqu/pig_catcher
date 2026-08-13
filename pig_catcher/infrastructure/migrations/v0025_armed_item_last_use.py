"""Normalize armed-item queues so depleted rows are represented by absence."""

from .model import Migration

MIGRATION_0025 = Migration(
    version=25,
    name="armed_item_last_use",
    statements=(
        """
        CREATE TABLE armed_items_v25 (
            player_id TEXT NOT NULL
                REFERENCES players(player_id) ON DELETE CASCADE,
            action_type TEXT NOT NULL
                CHECK (action_type IN ('catching', 'cooking')),
            item_id TEXT NOT NULL,
            armed_at TEXT NOT NULL,
            remaining_uses INTEGER NOT NULL DEFAULT 1
                CHECK (remaining_uses > 0),
            PRIMARY KEY (player_id, action_type)
        )
        """,
        """
        INSERT INTO armed_items_v25(
            player_id, action_type, item_id, armed_at, remaining_uses
        )
        SELECT player_id, action_type, item_id, armed_at, remaining_uses
        FROM armed_items
        WHERE remaining_uses > 0
        """,
        """
        DROP TABLE armed_items
        """,
        """
        ALTER TABLE armed_items_v25 RENAME TO armed_items
        """,
    ),
)


__all__ = ["MIGRATION_0025"]
