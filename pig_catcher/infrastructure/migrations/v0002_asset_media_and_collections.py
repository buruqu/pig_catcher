"""Add real media metadata and collaboration collection fields."""

from .model import Migration

MIGRATION_0002 = Migration(
    version=2,
    name="asset_media_and_collections",
    statements=(
        "ALTER TABLE pig_templates ADD COLUMN media_format TEXT NOT NULL DEFAULT 'PNG'",
        "ALTER TABLE pig_templates ADD COLUMN is_animated INTEGER NOT NULL DEFAULT 0 CHECK (is_animated IN (0, 1))",
        "ALTER TABLE pig_templates ADD COLUMN frame_count INTEGER NOT NULL DEFAULT 1 CHECK (frame_count >= 1)",
        (
            "ALTER TABLE pig_templates ADD COLUMN total_duration_ms "
            "INTEGER NOT NULL DEFAULT 0 CHECK (total_duration_ms >= 0)"
        ),
        "ALTER TABLE pig_templates ADD COLUMN loop_count INTEGER",
        (
            "ALTER TABLE pig_templates ADD COLUMN has_transparency "
            "INTEGER NOT NULL DEFAULT 0 CHECK (has_transparency IN (0, 1))"
        ),
        "ALTER TABLE pig_templates ADD COLUMN collaboration_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pig_templates ADD COLUMN collection_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pig_templates ADD COLUMN collection_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pig_templates ADD COLUMN collection_slot INTEGER",
        (
            "ALTER TABLE pig_templates ADD COLUMN collection_total "
            "INTEGER NOT NULL DEFAULT 0 CHECK (collection_total >= 0)"
        ),
        "ALTER TABLE pig_templates ADD COLUMN character_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pig_templates ADD COLUMN character_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE pig_templates ADD COLUMN official_profile_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE food_templates ADD COLUMN media_format TEXT NOT NULL DEFAULT 'PNG'",
        "ALTER TABLE food_templates ADD COLUMN is_animated INTEGER NOT NULL DEFAULT 0 CHECK (is_animated IN (0, 1))",
        "ALTER TABLE food_templates ADD COLUMN frame_count INTEGER NOT NULL DEFAULT 1 CHECK (frame_count >= 1)",
        (
            "ALTER TABLE food_templates ADD COLUMN total_duration_ms "
            "INTEGER NOT NULL DEFAULT 0 CHECK (total_duration_ms >= 0)"
        ),
        "ALTER TABLE food_templates ADD COLUMN loop_count INTEGER",
        (
            "ALTER TABLE food_templates ADD COLUMN has_transparency "
            "INTEGER NOT NULL DEFAULT 0 CHECK (has_transparency IN (0, 1))"
        ),
        "CREATE INDEX idx_pig_templates_collection ON pig_templates(collection_id, collection_slot)",
    ),
)
