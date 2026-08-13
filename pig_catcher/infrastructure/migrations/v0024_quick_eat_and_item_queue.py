"""Add last-food confirmation state and multi-use item queues."""

from .model import Migration

MIGRATION_0024 = Migration(
    version=24,
    name="quick_eat_and_item_queue",
    statements=(
        """
        CREATE TABLE IF NOT EXISTS armed_items (
            player_id TEXT NOT NULL
                REFERENCES players(player_id) ON DELETE CASCADE,
            action_type TEXT NOT NULL
                CHECK (action_type IN ('catching', 'cooking')),
            item_id TEXT NOT NULL,
            armed_at TEXT NOT NULL,
            PRIMARY KEY (player_id, action_type)
        )
        """,
        """
        ALTER TABLE armed_items
        ADD COLUMN remaining_uses INTEGER NOT NULL DEFAULT 1
            CHECK (remaining_uses >= 0)
        """,
        """
        CREATE TABLE pending_food_confirmations (
            player_id TEXT PRIMARY KEY
                REFERENCES players(player_id) ON DELETE CASCADE,
            food_instance_id TEXT NOT NULL
                REFERENCES food_instances(food_instance_id) ON DELETE CASCADE,
            requested_name TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_pending_food_confirmations_expiry
        ON pending_food_confirmations(expires_at)
        """,
    ),
)

__all__ = ["MIGRATION_0024"]
