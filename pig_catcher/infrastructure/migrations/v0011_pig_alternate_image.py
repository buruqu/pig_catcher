"""Add optional alternate image (e.g. sticker) for pig templates."""

from .model import Migration

MIGRATION_0011 = Migration(
    version=11,
    name="pig_alternate_image",
    statements=(
        "ALTER TABLE pig_templates ADD COLUMN alternate_image_relpath TEXT NOT NULL DEFAULT ''",
    ),
)
