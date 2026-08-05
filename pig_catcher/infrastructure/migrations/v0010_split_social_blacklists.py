"""Split the legacy social ban into permanent gift and trade blacklists."""

from .model import Migration

MIGRATION_0010 = Migration(
    version=10,
    name="split_social_blacklists",
    statements=(
        """
        CREATE TABLE player_restrictions_v2 (
            restriction_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL
                REFERENCES players(player_id) ON DELETE CASCADE,
            restriction_type TEXT NOT NULL CHECK (
                restriction_type IN (
                    'gift-transfer-ban',
                    'trade-ban',
                    'catch-window-limit'
                )
            ),
            limit_value INTEGER CHECK (
                (restriction_type IN ('gift-transfer-ban', 'trade-ban')
                    AND limit_value IS NULL)
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
        INSERT INTO player_restrictions_v2(
            restriction_id, player_id, restriction_type, limit_value,
            starts_at, expires_at, reason, source, created_by,
            created_at, updated_at
        )
        SELECT
            restriction_id, player_id, restriction_type, limit_value,
            starts_at, expires_at, reason, source, created_by,
            created_at, updated_at
        FROM player_restrictions
        WHERE restriction_type = 'catch-window-limit'
        """,
        """
        INSERT INTO player_restrictions_v2(
            restriction_id, player_id, restriction_type, limit_value,
            starts_at, expires_at, reason, source, created_by,
            created_at, updated_at
        )
        SELECT
            restriction_id || ':gift', player_id, 'gift-transfer-ban', NULL,
            starts_at, NULL, reason, source, created_by,
            created_at, updated_at
        FROM player_restrictions
        WHERE restriction_type = 'social-transfer-ban'
        """,
        """
        INSERT INTO player_restrictions_v2(
            restriction_id, player_id, restriction_type, limit_value,
            starts_at, expires_at, reason, source, created_by,
            created_at, updated_at
        )
        SELECT
            restriction_id || ':trade', player_id, 'trade-ban', NULL,
            starts_at, NULL, reason, source, created_by,
            created_at, updated_at
        FROM player_restrictions
        WHERE restriction_type = 'social-transfer-ban'
        """,
        "DROP TABLE player_restrictions",
        "ALTER TABLE player_restrictions_v2 RENAME TO player_restrictions",
        """
        CREATE INDEX idx_player_restrictions_type_expiry
        ON player_restrictions(restriction_type, expires_at)
        """,
    ),
)
