"""Schema 31: player-controlled favorite protection for owned assets."""

from .model import Migration

MIGRATION_0031 = Migration(
    version=31,
    name="asset-favorites",
    statements=(
        """
        ALTER TABLE pig_instances
        ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0
            CHECK (is_favorite IN (0, 1))
        """,
        """
        ALTER TABLE food_instances
        ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0
            CHECK (is_favorite IN (0, 1))
        """,
        """
        CREATE INDEX idx_pig_instances_owner_favorite_active
        ON pig_instances(owner_player_id, is_favorite, rarity, official_value)
        WHERE state = 'active' AND locked_trade_id IS NULL
        """,
        """
        CREATE INDEX idx_food_instances_owner_favorite_active
        ON food_instances(owner_player_id, is_favorite, rarity, official_value)
        WHERE state = 'active' AND locked_trade_id IS NULL
        """,
    ),
)
