"""Schema 34: repair released v33 databases that retained the old source UNIQUE constraint."""

from .model import Migration

MIGRATION_0034 = Migration(
    version=34,
    name="player-food-effect-source-repair",
    statements=(
        "DROP INDEX IF EXISTS idx_player_food_effects_active",
        "DROP INDEX IF EXISTS idx_player_food_effects_source",
        "ALTER TABLE player_food_effects RENAME TO player_food_effects_v33",
        """
        CREATE TABLE player_food_effects (
            effect_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            source_food_instance_id TEXT NOT NULL
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
        INSERT INTO player_food_effects(
            effect_entry_id, player_id, source_food_instance_id,
            effect_id, params_json, granted_uses, consumed_uses,
            expires_at, created_at, updated_at
        )
        SELECT effect_entry_id, player_id, source_food_instance_id,
               effect_id, params_json, granted_uses, consumed_uses,
               expires_at, created_at, updated_at
        FROM player_food_effects_v33
        """,
        "DROP TABLE player_food_effects_v33",
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
        """
        CREATE INDEX idx_player_food_effects_source
        ON player_food_effects(source_food_instance_id, created_at)
        """,
    ),
)


__all__ = ["MIGRATION_0034"]
