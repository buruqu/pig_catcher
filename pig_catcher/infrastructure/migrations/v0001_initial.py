"""初始完整领域表。"""

from .model import Migration

MIGRATION_0001 = Migration(
    version=1,
    name="initial_domain_schema",
    statements=(
        """
        CREATE TABLE scopes (
            scope_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL DEFAULT '',
            stream_id TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (platform, group_id)
        )
        """,
        """
        CREATE TABLE players (
            player_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            platform_user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            coin_balance INTEGER NOT NULL DEFAULT 0 CHECK (coin_balance >= 0),
            experience INTEGER NOT NULL DEFAULT 0 CHECK (experience >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (scope_id, platform_user_id)
        )
        """,
        """
        CREATE TABLE asset_manifest_imports (
            catalog_hash TEXT PRIMARY KEY,
            catalog_id TEXT NOT NULL,
            manifest_version INTEGER NOT NULL CHECK (manifest_version >= 1),
            source_label TEXT NOT NULL,
            storage_relpath TEXT NOT NULL,
            entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
            status TEXT NOT NULL CHECK (status IN ('active', 'replaced')),
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE pig_templates (
            template_id TEXT PRIMARY KEY,
            catalog_hash TEXT NOT NULL REFERENCES asset_manifest_imports(catalog_hash),
            template_version INTEGER NOT NULL DEFAULT 1 CHECK (template_version >= 1),
            display_name TEXT NOT NULL,
            rarity INTEGER NOT NULL CHECK (rarity BETWEEN 1 AND 6),
            scope_type TEXT NOT NULL CHECK (scope_type IN ('common', 'group')),
            description TEXT NOT NULL,
            image_relpath TEXT NOT NULL,
            image_sha256 TEXT NOT NULL,
            image_fit TEXT NOT NULL CHECK (image_fit IN ('contain', 'cover')),
            length_min REAL NOT NULL CHECK (length_min > 0),
            length_max REAL NOT NULL CHECK (length_max >= length_min),
            weight_min REAL NOT NULL CHECK (weight_min > 0),
            weight_max REAL NOT NULL CHECK (weight_max >= weight_min),
            fat_profile TEXT NOT NULL CHECK (fat_profile IN ('lean', 'balanced', 'fatty')),
            recipe_tags_json TEXT NOT NULL DEFAULT '[]',
            source_label TEXT NOT NULL,
            license TEXT NOT NULL,
            consent_status TEXT NOT NULL CHECK (consent_status IN ('not-required', 'granted', 'revoked')),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (scope_type = 'common' AND rarity BETWEEN 1 AND 5 AND consent_status = 'not-required')
                OR
                (scope_type = 'group' AND rarity = 6 AND consent_status IN ('granted', 'revoked'))
            )
        )
        """,
        """
        CREATE TABLE scope_pig_templates (
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            template_id TEXT NOT NULL REFERENCES pig_templates(template_id) ON DELETE CASCADE,
            authorized INTEGER NOT NULL CHECK (authorized IN (0, 1)),
            consent_status TEXT NOT NULL CHECK (consent_status IN ('granted', 'revoked')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, template_id)
        )
        """,
        """
        CREATE TABLE food_templates (
            template_id TEXT PRIMARY KEY,
            catalog_hash TEXT NOT NULL REFERENCES asset_manifest_imports(catalog_hash),
            template_version INTEGER NOT NULL DEFAULT 1 CHECK (template_version >= 1),
            display_name TEXT NOT NULL,
            rarity INTEGER NOT NULL CHECK (rarity BETWEEN 1 AND 6),
            scope_type TEXT NOT NULL CHECK (scope_type IN ('common', 'group')),
            description TEXT NOT NULL,
            image_relpath TEXT NOT NULL,
            image_sha256 TEXT NOT NULL,
            image_fit TEXT NOT NULL CHECK (image_fit IN ('contain', 'cover')),
            recipe_tags_json TEXT NOT NULL DEFAULT '[]',
            effect_id TEXT NOT NULL DEFAULT '',
            effect_params_json TEXT NOT NULL DEFAULT '{}',
            source_label TEXT NOT NULL,
            license TEXT NOT NULL,
            consent_status TEXT NOT NULL CHECK (consent_status IN ('not-required', 'granted', 'revoked')),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (scope_type = 'common' AND rarity BETWEEN 1 AND 5 AND consent_status = 'not-required')
                OR
                (scope_type = 'group' AND rarity = 6 AND consent_status IN ('granted', 'revoked'))
            )
        )
        """,
        """
        CREATE TABLE scope_food_templates (
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            template_id TEXT NOT NULL REFERENCES food_templates(template_id) ON DELETE CASCADE,
            authorized INTEGER NOT NULL CHECK (authorized IN (0, 1)),
            consent_status TEXT NOT NULL CHECK (consent_status IN ('granted', 'revoked')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, template_id)
        )
        """,
        """
        CREATE TABLE pig_instances (
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
                state IN ('active', 'locked-for-trade', 'sold', 'consumed-for-cooking')
            ),
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE food_instances (
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
            state TEXT NOT NULL CHECK (state IN ('active', 'locked-for-trade', 'sold', 'consumed')),
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE pig_catalog_entries (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            template_id TEXT NOT NULL REFERENCES pig_templates(template_id),
            first_acquired_at TEXT NOT NULL,
            last_acquired_at TEXT NOT NULL,
            acquired_count INTEGER NOT NULL CHECK (acquired_count >= 1),
            best_size REAL NOT NULL CHECK (best_size > 0),
            best_weight REAL NOT NULL CHECK (best_weight > 0),
            PRIMARY KEY (player_id, template_id)
        )
        """,
        """
        CREATE TABLE food_catalog_entries (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            template_id TEXT NOT NULL REFERENCES food_templates(template_id),
            first_acquired_at TEXT NOT NULL,
            last_acquired_at TEXT NOT NULL,
            acquired_count INTEGER NOT NULL CHECK (acquired_count >= 1),
            best_portion_weight REAL NOT NULL CHECK (best_portion_weight > 0),
            PRIMARY KEY (player_id, template_id)
        )
        """,
        """
        CREATE TABLE group_records (
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            template_id TEXT NOT NULL REFERENCES pig_templates(template_id),
            record_type TEXT NOT NULL CHECK (record_type IN ('size', 'weight')),
            pig_instance_id TEXT NOT NULL REFERENCES pig_instances(pig_instance_id),
            record_value REAL NOT NULL CHECK (record_value > 0),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            achieved_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, template_id, record_type)
        )
        """,
        """
        CREATE TABLE currency_ledger (
            ledger_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            amount INTEGER NOT NULL CHECK (amount <> 0),
            balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
            reason_code TEXT NOT NULL,
            reason_text TEXT NOT NULL,
            source_object_type TEXT NOT NULL DEFAULT '',
            source_object_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT UNIQUE,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE upgrades (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            upgrade_type TEXT NOT NULL CHECK (upgrade_type IN ('feed', 'cookware')),
            level INTEGER NOT NULL CHECK (level BETWEEN 0 AND 5),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (player_id, upgrade_type)
        )
        """,
        """
        CREATE TABLE item_inventory (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            item_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity >= 0),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (player_id, item_id)
        )
        """,
        """
        CREATE TABLE armed_items (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            action_type TEXT NOT NULL CHECK (action_type IN ('catching', 'cooking')),
            item_id TEXT NOT NULL,
            armed_at TEXT NOT NULL,
            PRIMARY KEY (player_id, action_type)
        )
        """,
        """
        CREATE TABLE trade_offers (
            trade_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            sender_player_id TEXT NOT NULL REFERENCES players(player_id),
            recipient_player_id TEXT NOT NULL REFERENCES players(player_id),
            asset_kind TEXT NOT NULL CHECK (asset_kind IN ('pig', 'food')),
            asset_instance_id TEXT NOT NULL,
            price INTEGER NOT NULL CHECK (price > 0),
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'accepted', 'rejected', 'cancelled', 'expired')
            ),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            resolved_at TEXT,
            CHECK (sender_player_id <> recipient_player_id)
        )
        """,
        """
        CREATE TABLE display_preferences (
            player_id TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
            pig_instance_id TEXT REFERENCES pig_instances(pig_instance_id) ON DELETE SET NULL,
            food_instance_id TEXT REFERENCES food_instances(food_instance_id) ON DELETE SET NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE command_receipts (
            receipt_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            scope_id TEXT NOT NULL,
            player_id TEXT,
            command_name TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            result_type TEXT NOT NULL,
            result_object_id TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            text_summary TEXT NOT NULL,
            business_status TEXT NOT NULL DEFAULT 'committed' CHECK (business_status = 'committed'),
            send_status TEXT NOT NULL DEFAULT 'pending' CHECK (
                send_status IN ('pending', 'claimed', 'sent', 'failed')
            ),
            send_error TEXT NOT NULL DEFAULT '',
            claimed_at TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (scope_id) REFERENCES scopes(scope_id),
            FOREIGN KEY (player_id) REFERENCES players(player_id)
        )
        """,
        """
        CREATE TABLE audit_events (
            audit_event_id TEXT PRIMARY KEY,
            scope_id TEXT,
            actor_user_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            object_type TEXT NOT NULL DEFAULT '',
            object_id TEXT NOT NULL DEFAULT '',
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY (scope_id) REFERENCES scopes(scope_id)
        )
        """,
        "CREATE INDEX idx_players_scope ON players(scope_id)",
        "CREATE INDEX idx_pig_instances_owner_state ON pig_instances(owner_player_id, state)",
        "CREATE INDEX idx_food_instances_owner_state ON food_instances(owner_player_id, state)",
        "CREATE INDEX idx_pig_templates_catalog ON pig_templates(catalog_hash)",
        "CREATE INDEX idx_food_templates_catalog ON food_templates(catalog_hash)",
        "CREATE INDEX idx_scope_pig_templates_enabled ON scope_pig_templates(scope_id, authorized)",
        "CREATE INDEX idx_scope_food_templates_enabled ON scope_food_templates(scope_id, authorized)",
        "CREATE INDEX idx_trade_offers_recipient_status ON trade_offers(recipient_player_id, status)",
        "CREATE INDEX idx_trade_offers_expiry ON trade_offers(status, expires_at)",
        "CREATE INDEX idx_command_receipts_scope_created ON command_receipts(scope_id, created_at)",
        "CREATE INDEX idx_currency_ledger_player_created ON currency_ledger(player_id, created_at)",
        "CREATE INDEX idx_audit_events_scope_created ON audit_events(scope_id, created_at)",
    ),
)
