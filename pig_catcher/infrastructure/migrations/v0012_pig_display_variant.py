"""Allow players to switch owned pigs between default art and alternate art."""

from .model import Migration

MIGRATION_0012 = Migration(
    version=12,
    name="pig_display_variant",
    statements=(
        (
            "ALTER TABLE pig_instances ADD COLUMN display_variant "
            "TEXT NOT NULL DEFAULT 'pig' CHECK (display_variant IN ('pig', 'sticker'))"
        ),
    ),
)
