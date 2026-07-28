"""Add indexes used by daily limits, inventory pages, and group records."""

from .model import Migration

MIGRATION_0003 = Migration(
    version=3,
    name="catching_query_indexes",
    statements=(
        (
            "CREATE INDEX idx_pig_instances_owner_acquired "
            "ON pig_instances(owner_player_id, acquired_at DESC)"
        ),
        (
            "CREATE INDEX idx_group_records_scope_achieved "
            "ON group_records(scope_id, achieved_at DESC)"
        ),
    ),
)
