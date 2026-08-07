"""Per-player batch operation keep-highest preference flag."""

from .model import Migration

MIGRATION_0014 = Migration(
    version=14,
    name="batch_keep_highest",
    statements=(
        (
            "ALTER TABLE players ADD COLUMN batch_keep_highest "
            "INTEGER NOT NULL DEFAULT 0"
        ),
    ),
)
