"""Persist one-shot food effects without rewriting historical food instances."""

from .model import Migration

MIGRATION_0006 = Migration(
    version=6,
    name="food_effect_queue",
    statements=(
        """
        CREATE TABLE player_food_effects (
            effect_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            source_food_instance_id TEXT NOT NULL UNIQUE
                REFERENCES food_instances(food_instance_id),
            effect_id TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            granted_uses INTEGER NOT NULL CHECK (granted_uses >= 1),
            consumed_uses INTEGER NOT NULL DEFAULT 0 CHECK (
                consumed_uses >= 0 AND consumed_uses <= granted_uses
            ),
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_player_food_effects_active
        ON player_food_effects(
            player_id,
            effect_id,
            consumed_uses,
            expires_at,
            created_at
        )
        """,
    ),
)
