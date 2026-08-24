"""Add a covering index for catch-quota usage queries."""

from .model import Migration

MIGRATION_0030 = Migration(
    version=30,
    name="catch_quota_covering_index",
    statements=(
        """
        CREATE INDEX idx_command_receipts_player_command_created_quota
        ON command_receipts(player_id, command_name, created_at, catch_quota_cost)
        """,
    ),
)

__all__ = ["MIGRATION_0030"]
