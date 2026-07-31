"""Add indexes used by cooking, food collection, sales, and ledger queries."""

from .model import Migration

MIGRATION_0004 = Migration(
    version=4,
    name="cooking_and_economy_indexes",
    statements=(
        (
            "CREATE INDEX idx_food_instances_owner_state_acquired "
            "ON food_instances(owner_player_id, state, acquired_at DESC)"
        ),
        (
            "CREATE INDEX idx_food_catalog_player_last "
            "ON food_catalog_entries(player_id, last_acquired_at DESC)"
        ),
        (
            "CREATE INDEX idx_pig_instances_owner_state_value "
            "ON pig_instances(owner_player_id, state, official_value DESC)"
        ),
    ),
)
