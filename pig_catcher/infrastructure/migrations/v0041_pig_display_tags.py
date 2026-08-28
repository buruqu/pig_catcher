"""展示标签独立持久化；不修改任何既有实例或参与业务的食谱标签。"""

from .model import Migration

MIGRATION_0041 = Migration(
    version=41,
    name="pig_display_tags",
    statements=(
        "ALTER TABLE pig_templates ADD COLUMN display_tags_json TEXT NOT NULL DEFAULT '[]'",
    ),
)
