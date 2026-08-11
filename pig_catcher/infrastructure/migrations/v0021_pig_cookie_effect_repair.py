"""Schema 21：收敛热加载期间产生的“猪利猪”旧效果快照。"""

from .model import Migration

MIGRATION_0021 = Migration(
    version=21,
    name="pig-cookie-effect-repair",
    statements=(
        """
        UPDATE food_templates
        SET effect_id = 'next-small-six-star-catch',
            effect_params_json = '{"bonus_percent":1}'
        WHERE display_name = '猪利猪'
        """,
        """
        UPDATE food_instances
        SET effect_id = 'next-small-six-star-catch',
            effect_params_json = '{"bonus_percent":1}'
        WHERE state IN ('active', 'locked-for-trade')
          AND display_name_snapshot = '猪利猪'
        """,
        """
        UPDATE player_food_effects
        SET effect_id = 'next-small-six-star-catch',
            params_json = '{"bonus_percent":1}',
            granted_uses = 1
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '猪利猪'
          )
        """,
    ),
)

__all__ = ["MIGRATION_0021"]
