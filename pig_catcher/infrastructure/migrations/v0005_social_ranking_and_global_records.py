"""Add social transactions, stable counters, showcases, and global records."""

from .model import Migration

MIGRATION_0005 = Migration(
    version=5,
    name="social_ranking_and_global_records",
    statements=(
        (
            "ALTER TABLE pig_templates ADD COLUMN stature_profile TEXT NOT NULL "
            "DEFAULT 'standard' CHECK (stature_profile IN ('mini', 'standard', 'giant'))"
        ),
        """
        UPDATE pig_templates
        SET
            length_min = 4.0,
            length_max = 16.0,
            weight_min = 0.35,
            weight_max = 6.0,
            stature_profile = 'mini',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE template_id = 'pig-r2-tiny'
        """,
        """
        UPDATE pig_templates
        SET
            length_min = 120.0,
            length_max = 260.0,
            weight_min = 350.0,
            weight_max = 1800.0,
            stature_profile = 'giant',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE template_id = 'pig-r2-elephant'
        """,
        """
        CREATE TABLE player_statistics (
            player_id TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
            total_catches INTEGER NOT NULL DEFAULT 0 CHECK (total_catches >= 0),
            total_cooks INTEGER NOT NULL DEFAULT 0 CHECK (total_cooks >= 0),
            gifts_sent INTEGER NOT NULL DEFAULT 0 CHECK (gifts_sent >= 0),
            gifts_received INTEGER NOT NULL DEFAULT 0 CHECK (gifts_received >= 0),
            trades_completed INTEGER NOT NULL DEFAULT 0 CHECK (trades_completed >= 0),
            last_catch_at TEXT,
            last_cook_at TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO player_statistics(
            player_id, total_catches, total_cooks, gifts_sent, gifts_received,
            trades_completed, last_catch_at, last_cook_at, updated_at
        )
        SELECT
            player.player_id,
            (
                SELECT COUNT(*)
                FROM command_receipts AS receipt
                WHERE receipt.player_id = player.player_id
                  AND receipt.command_name = 'pig-catcher.catch'
            ),
            (
                SELECT COUNT(*)
                FROM command_receipts AS receipt
                WHERE receipt.player_id = player.player_id
                  AND receipt.command_name = 'pig-catcher.cook'
            ),
            0,
            0,
            0,
            (
                SELECT MAX(receipt.created_at)
                FROM command_receipts AS receipt
                WHERE receipt.player_id = player.player_id
                  AND receipt.command_name = 'pig-catcher.catch'
            ),
            (
                SELECT MAX(receipt.created_at)
                FROM command_receipts AS receipt
                WHERE receipt.player_id = player.player_id
                  AND receipt.command_name = 'pig-catcher.cook'
            ),
            player.updated_at
        FROM players AS player
        """,
        """
        CREATE TABLE asset_transfer_events (
            transfer_event_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            asset_kind TEXT NOT NULL CHECK (asset_kind IN ('pig', 'food')),
            asset_instance_id TEXT NOT NULL,
            from_player_id TEXT NOT NULL REFERENCES players(player_id),
            to_player_id TEXT NOT NULL REFERENCES players(player_id),
            transfer_type TEXT NOT NULL CHECK (transfer_type IN ('gift', 'trade')),
            trade_id TEXT REFERENCES trade_offers(trade_id),
            created_at TEXT NOT NULL,
            CHECK (from_player_id <> to_player_id)
        )
        """,
        """
        CREATE TABLE group_global_records (
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            record_type TEXT NOT NULL CHECK (record_type IN ('size', 'weight')),
            pig_instance_id TEXT NOT NULL REFERENCES pig_instances(pig_instance_id),
            template_id TEXT NOT NULL REFERENCES pig_templates(template_id),
            record_value REAL NOT NULL CHECK (record_value > 0),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            achieved_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, record_type)
        )
        """,
        """
        WITH ranked AS (
            SELECT
                pig.*,
                ROW_NUMBER() OVER (
                    PARTITION BY pig.scope_id
                    ORDER BY pig.size_value DESC, pig.acquired_at, pig.pig_instance_id
                ) AS position
            FROM pig_instances AS pig
        )
        INSERT INTO group_global_records(
            scope_id, record_type, pig_instance_id, template_id,
            record_value, player_id, achieved_at
        )
        SELECT
            scope_id, 'size', pig_instance_id, template_id,
            size_value, owner_player_id, acquired_at
        FROM ranked
        WHERE position = 1
        """,
        """
        WITH ranked AS (
            SELECT
                pig.*,
                ROW_NUMBER() OVER (
                    PARTITION BY pig.scope_id
                    ORDER BY pig.weight_value DESC, pig.acquired_at, pig.pig_instance_id
                ) AS position
            FROM pig_instances AS pig
        )
        INSERT INTO group_global_records(
            scope_id, record_type, pig_instance_id, template_id,
            record_value, player_id, achieved_at
        )
        SELECT
            scope_id, 'weight', pig_instance_id, template_id,
            weight_value, owner_player_id, acquired_at
        FROM ranked
        WHERE position = 1
        """,
        """
        CREATE TABLE giant_sightings (
            pig_instance_id TEXT PRIMARY KEY REFERENCES pig_instances(pig_instance_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            template_id TEXT NOT NULL REFERENCES pig_templates(template_id),
            size_value REAL NOT NULL CHECK (size_value > 0),
            weight_value REAL NOT NULL CHECK (weight_value > 0),
            giant_score REAL NOT NULL CHECK (giant_score > 0),
            size_qualified INTEGER NOT NULL CHECK (size_qualified IN (0, 1)),
            weight_qualified INTEGER NOT NULL CHECK (weight_qualified IN (0, 1)),
            achieved_at TEXT NOT NULL,
            CHECK (size_qualified = 1 OR weight_qualified = 1)
        )
        """,
        """
        INSERT INTO giant_sightings(
            pig_instance_id, scope_id, player_id, template_id,
            size_value, weight_value, giant_score,
            size_qualified, weight_qualified, achieved_at
        )
        SELECT
            pig_instance_id,
            scope_id,
            owner_player_id,
            template_id,
            size_value,
            weight_value,
            ROUND(
                100.0 * (
                    0.55 * size_value / 120.0
                    + 0.45 * weight_value / 350.0
                ),
                6
            ),
            CASE WHEN size_value >= 120.0 THEN 1 ELSE 0 END,
            CASE WHEN weight_value >= 350.0 THEN 1 ELSE 0 END,
            acquired_at
        FROM pig_instances
        WHERE size_value >= 120.0 OR weight_value >= 350.0
        """,
        (
            "CREATE UNIQUE INDEX idx_trade_pending_asset "
            "ON trade_offers(scope_id, asset_kind, asset_instance_id) "
            "WHERE status = 'pending'"
        ),
        (
            "CREATE INDEX idx_trade_offers_sender_status "
            "ON trade_offers(sender_player_id, status, created_at DESC)"
        ),
        (
            "CREATE INDEX idx_transfer_events_scope_created "
            "ON asset_transfer_events(scope_id, created_at DESC)"
        ),
        (
            "CREATE INDEX idx_global_records_scope_value "
            "ON group_global_records(scope_id, record_type, record_value DESC)"
        ),
        (
            "CREATE INDEX idx_giant_sightings_scope_score "
            "ON giant_sightings(scope_id, giant_score DESC, achieved_at)"
        ),
    ),
)
