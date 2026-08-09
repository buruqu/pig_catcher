"""Enable negative admin balances, operational access bans, and removals."""

from .model import Migration

MIGRATION_0015 = Migration(
    version=15,
    name="administrator_commands",
    statements=(
        """
        CREATE TABLE players_v15 (
            player_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            platform_user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            coin_balance INTEGER NOT NULL DEFAULT 0,
            experience INTEGER NOT NULL DEFAULT 0 CHECK (experience >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            batch_keep_highest INTEGER NOT NULL DEFAULT 0,
            UNIQUE (scope_id, platform_user_id)
        )
        """,
        """
        INSERT INTO players_v15(
            player_id, scope_id, platform_user_id, display_name,
            coin_balance, experience, created_at, updated_at,
            batch_keep_highest
        )
        SELECT player_id, scope_id, platform_user_id, display_name,
               coin_balance, experience, created_at, updated_at,
               batch_keep_highest
        FROM players
        """,
        "DROP TABLE players",
        "ALTER TABLE players_v15 RENAME TO players",
        "CREATE INDEX idx_players_scope ON players(scope_id)",
        """
        CREATE TABLE currency_ledger_v15 (
            ledger_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            amount INTEGER NOT NULL CHECK (amount <> 0),
            balance_after INTEGER NOT NULL,
            reason_code TEXT NOT NULL,
            reason_text TEXT NOT NULL,
            source_object_type TEXT NOT NULL DEFAULT '',
            source_object_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT UNIQUE,
            created_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO currency_ledger_v15(
            ledger_entry_id, player_id, scope_id, amount, balance_after,
            reason_code, reason_text, source_object_type, source_object_id,
            idempotency_key, created_at
        )
        SELECT ledger_entry_id, player_id, scope_id, amount, balance_after,
               reason_code, reason_text, source_object_type, source_object_id,
               idempotency_key, created_at
        FROM currency_ledger
        """,
        "DROP TABLE currency_ledger",
        "ALTER TABLE currency_ledger_v15 RENAME TO currency_ledger",
        (
            "CREATE INDEX idx_currency_ledger_player_created "
            "ON currency_ledger(player_id, created_at)"
        ),
        """
        CREATE TABLE pig_instances_v15 (
            pig_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL UNIQUE CHECK (length(short_code) = 8),
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
        INSERT INTO pig_instances_v15(
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
        "ALTER TABLE pig_instances_v15 RENAME TO pig_instances",
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
        CREATE TABLE food_instances_v15 (
            food_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL UNIQUE CHECK (length(short_code) = 8),
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
        INSERT INTO food_instances_v15(
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
        "ALTER TABLE food_instances_v15 RENAME TO food_instances",
        "CREATE INDEX idx_food_instances_owner_state ON food_instances(owner_player_id, state)",
        (
            "CREATE INDEX idx_food_instances_owner_state_acquired "
            "ON food_instances(owner_player_id, state, acquired_at DESC)"
        ),
        """
        CREATE TABLE player_restrictions_v15 (
            restriction_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            restriction_type TEXT NOT NULL CHECK (
                restriction_type IN (
                    'plugin-access-ban', 'gift-transfer-ban',
                    'trade-ban', 'catch-window-limit'
                )
            ),
            limit_value INTEGER CHECK (
                (restriction_type IN (
                    'plugin-access-ban', 'gift-transfer-ban', 'trade-ban'
                ) AND limit_value IS NULL)
                OR
                (restriction_type = 'catch-window-limit' AND limit_value >= 0)
            ),
            starts_at TEXT NOT NULL,
            expires_at TEXT,
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(player_id, restriction_type),
            CHECK (expires_at IS NULL OR expires_at > starts_at)
        )
        """,
        """
        INSERT INTO player_restrictions_v15(
            restriction_id, player_id, restriction_type, limit_value,
            starts_at, expires_at, reason, source, created_by,
            created_at, updated_at
        )
        SELECT restriction_id, player_id, restriction_type, limit_value,
               starts_at, expires_at, reason, source, created_by,
               created_at, updated_at
        FROM player_restrictions
        """,
        "DROP TABLE player_restrictions",
        "ALTER TABLE player_restrictions_v15 RENAME TO player_restrictions",
        """
        CREATE INDEX idx_player_restrictions_type_expiry
        ON player_restrictions(restriction_type, expires_at)
        """,
    ),
)
