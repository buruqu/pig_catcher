"""Group-wide timed six-star food effects and v1.15 dish convergence."""

from .model import Migration

_OMELETTE_PARAMS = (
    '{"coin_per_player":1004,"dedicated_catches":0,'
    '"five_star_multiplier":1.004,"six_star_multiplier":1.004,'
    '"source_label":"猪鼻蛋包饭"}'
)
_RIBS_PARAMS = (
    '{"count":1,"five_star_multiplier":1.007,"group_coin":1007,'
    '"group_dedicated_catches":10,"six_star_multiplier":1.007}'
)


MIGRATION_0022 = Migration(
    version=22,
    name="group_food_effects",
    statements=(
        """
        CREATE TABLE group_food_effects(
            group_effect_entry_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            source_player_id TEXT NOT NULL REFERENCES players(player_id),
            source_food_instance_id TEXT NOT NULL REFERENCES food_instances(food_instance_id),
            effect_id TEXT NOT NULL,
            params_json TEXT NOT NULL,
            granted_uses_per_player INTEGER NOT NULL DEFAULT 0
                CHECK (granted_uses_per_player BETWEEN 0 AND 100),
            starts_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (expires_at > starts_at)
        )
        """,
        """
        CREATE TABLE group_food_effect_usage(
            group_effect_entry_id TEXT NOT NULL
                REFERENCES group_food_effects(group_effect_entry_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            consumed_uses INTEGER NOT NULL DEFAULT 0 CHECK (consumed_uses >= 0),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (group_effect_entry_id, player_id)
        )
        """,
        """
        CREATE INDEX idx_group_food_effects_scope_expiry
        ON group_food_effects(scope_id, expires_at, starts_at)
        """,
        """
        CREATE INDEX idx_group_food_effect_usage_player
        ON group_food_effect_usage(player_id, group_effect_entry_id)
        """,
        f"""
        UPDATE food_templates
        SET effect_id = 'group-window-high-star-boost',
            effect_params_json = '{_OMELETTE_PARAMS.replace("'", "''")}'
        WHERE rarity = 6 AND display_name = '猪鼻蛋包饭'
        """,
        f"""
        UPDATE food_instances
        SET effect_id = 'group-window-high-star-boost',
            effect_params_json = '{_OMELETTE_PARAMS.replace("'", "''")}'
        WHERE rarity = 6
          AND display_name_snapshot = '猪鼻蛋包饭'
          AND state IN ('active', 'locked-for-trade')
        """,
        f"""
        UPDATE food_templates
        SET effect_id = 'quota-reset',
            effect_params_json = '{_RIBS_PARAMS.replace("'", "''")}'
        WHERE rarity = 6 AND display_name = '糖醋排骨'
        """,
        f"""
        UPDATE food_instances
        SET effect_id = 'quota-reset',
            effect_params_json = '{_RIBS_PARAMS.replace("'", "''")}'
        WHERE rarity = 6
          AND display_name_snapshot = '糖醋排骨'
          AND state IN ('active', 'locked-for-trade')
        """,
        f"""
        UPDATE player_food_effects
        SET params_json = '{_RIBS_PARAMS.replace("'", "''")}',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE effect_id = 'quota-reset'
          AND consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '糖醋排骨'
          )
        """,
    ),
)

__all__ = ["MIGRATION_0022"]
