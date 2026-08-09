"""Allow case-insensitive, variable-length alphanumeric asset short codes."""

from .model import Migration

MIGRATION_0016 = Migration(
    version=16,
    name="alphanumeric_short_codes",
    statements=(
        """
        CREATE TABLE pig_instances_v16 (
            pig_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (
                length(short_code) BETWEEN 4 AND 16
                AND short_code NOT GLOB '*[^0-9A-Za-z]*'
            ),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            owner_player_id TEXT NOT NULL REFERENCES players(player_id),
            template_id TEXT NOT NULL REFERENCES pig_templates(template_id),
            template_version INTEGER NOT NULL CHECK (template_version >= 1),
            rarity INTEGER NOT NULL CHECK (rarity BETWEEN 1 AND 6),
            display_name_snapshot TEXT NOT NULL,
            size_value REAL NOT NULL CHECK (size_value > 0),
            size_percentile REAL NOT NULL CHECK (size_percentile BETWEEN 0 AND 1),
            weight_value REAL NOT NULL CHECK (weight_value > 0),
            weight_percentile REAL NOT NULL CHECK (weight_percentile BETWEEN 0 AND 1),
            fat_ratio REAL NOT NULL CHECK (fat_ratio BETWEEN 0 AND 100),
            official_value INTEGER NOT NULL CHECK (official_value >= 0),
            ruleset_version INTEGER NOT NULL CHECK (ruleset_version >= 1),
            random_snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'active', 'locked-for-trade', 'sold',
                    'consumed-for-cooking', 'admin-removed'
                )
            ),
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL,
            display_variant TEXT NOT NULL DEFAULT 'pig'
                CHECK (display_variant IN ('pig', 'sticker'))
        )
        """,
        """
        INSERT INTO pig_instances_v16(
            pig_instance_id, short_code, scope_id, owner_player_id,
            template_id, template_version, rarity, display_name_snapshot,
            size_value, size_percentile, weight_value, weight_percentile,
            fat_ratio, official_value, ruleset_version, random_snapshot_json,
            state, locked_trade_id, acquired_at, disposed_at, updated_at,
            display_variant
        )
        SELECT pig_instance_id, short_code, scope_id, owner_player_id,
               template_id, template_version, rarity, display_name_snapshot,
               size_value, size_percentile, weight_value, weight_percentile,
               fat_ratio, official_value, ruleset_version, random_snapshot_json,
               state, locked_trade_id, acquired_at, disposed_at, updated_at,
               display_variant
        FROM pig_instances
        """,
        "DROP TABLE pig_instances",
        "ALTER TABLE pig_instances_v16 RENAME TO pig_instances",
        "CREATE INDEX idx_pig_instances_owner_state ON pig_instances(owner_player_id, state)",
        (
            "CREATE INDEX idx_pig_instances_owner_acquired "
            "ON pig_instances(owner_player_id, acquired_at DESC)"
        ),
        (
            "CREATE INDEX idx_pig_instances_owner_state_value "
            "ON pig_instances(owner_player_id, state, official_value DESC)"
        ),
        """
        CREATE TABLE food_instances_v16 (
            food_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (
                length(short_code) BETWEEN 4 AND 16
                AND short_code NOT GLOB '*[^0-9A-Za-z]*'
            ),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            owner_player_id TEXT NOT NULL REFERENCES players(player_id),
            template_id TEXT NOT NULL REFERENCES food_templates(template_id),
            template_version INTEGER NOT NULL CHECK (template_version >= 1),
            source_pig_instance_id TEXT REFERENCES pig_instances(pig_instance_id),
            rarity INTEGER NOT NULL CHECK (rarity BETWEEN 1 AND 6),
            display_name_snapshot TEXT NOT NULL,
            portion_weight REAL NOT NULL CHECK (portion_weight > 0),
            fat_category TEXT NOT NULL CHECK (fat_category IN ('lean', 'balanced', 'fatty')),
            official_value INTEGER NOT NULL CHECK (official_value >= 0),
            effect_id TEXT NOT NULL DEFAULT '',
            effect_params_json TEXT NOT NULL DEFAULT '{}',
            ruleset_version INTEGER NOT NULL CHECK (ruleset_version >= 1),
            random_snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('active', 'locked-for-trade', 'sold', 'consumed', 'admin-removed')
            ),
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO food_instances_v16(
            food_instance_id, short_code, scope_id, owner_player_id,
            template_id, template_version, source_pig_instance_id,
            rarity, display_name_snapshot, portion_weight, fat_category,
            official_value, effect_id, effect_params_json, ruleset_version,
            random_snapshot_json, state, locked_trade_id, acquired_at,
            disposed_at, updated_at
        )
        SELECT food_instance_id, short_code, scope_id, owner_player_id,
               template_id, template_version, source_pig_instance_id,
               rarity, display_name_snapshot, portion_weight, fat_category,
               official_value, effect_id, effect_params_json, ruleset_version,
               random_snapshot_json, state, locked_trade_id, acquired_at,
               disposed_at, updated_at
        FROM food_instances
        """,
        "DROP TABLE food_instances",
        "ALTER TABLE food_instances_v16 RENAME TO food_instances",
        "CREATE INDEX idx_food_instances_owner_state ON food_instances(owner_player_id, state)",
        (
            "CREATE INDEX idx_food_instances_owner_state_acquired "
            "ON food_instances(owner_player_id, state, acquired_at DESC)"
        ),
    ),
)
