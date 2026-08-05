"""Add permanent social blacklists and expiring catch-quota restrictions."""

from .model import Migration

MIGRATION_0009 = Migration(
    version=9,
    name="player_restrictions",
    statements=(
        """
        CREATE TABLE player_restrictions (
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
        CREATE INDEX idx_player_restrictions_type_expiry
        ON player_restrictions(restriction_type, expires_at)
        """,
    ),
)
