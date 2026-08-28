"""Schema45：仅增强后续新生成柠檬茶模板，保留旧实例及队列效果。"""

from .model import Migration

MIGRATION_0045 = Migration(
    version=45,
    name="economy-template-balance",
    statements=(
        """
        UPDATE food_templates
        SET effect_params_json = json_set(effect_params_json, '$.strength', 0.50),
            template_version = template_version + 1,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE template_id = 'food-r4-pig-paw-lemon-tea'
          AND rarity = 4
          AND effect_id = 'next-pig-stature'
          AND json_extract(effect_params_json, '$.mode') = 'mini'
          AND json_extract(effect_params_json, '$.strength') = 0.22
        """,
    ),
)

__all__ = ["MIGRATION_0045"]
