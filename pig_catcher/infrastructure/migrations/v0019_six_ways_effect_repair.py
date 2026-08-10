"""Schema 19：修复存量“一猪六吃”效果类型与参数不匹配。"""

from .model import Migration

MIGRATION_0019 = Migration(
    version=19,
    name="six-ways-effect-repair",
    statements=(
        """
        UPDATE food_templates
        SET effect_id = 'next-six-star-cook-bonus',
            effect_params_json = '{"bonus_percent":15}'
        WHERE display_name = '一猪六吃'
          AND effect_id IN ('next-six-star-cook', 'next-six-star-cook-bonus')
        """,
        """
        UPDATE food_instances
        SET effect_id = 'next-six-star-cook-bonus',
            effect_params_json = '{"bonus_percent":15}'
        WHERE display_name_snapshot = '一猪六吃'
          AND state IN ('active', 'locked-for-trade')
          AND effect_id IN ('next-six-star-cook', 'next-six-star-cook-bonus')
        """,
        """
        UPDATE player_food_effects
        SET effect_id = 'next-six-star-cook-bonus',
            params_json = '{"bonus_percent":15}',
            granted_uses = 1
        WHERE consumed_uses < granted_uses
          AND effect_id IN ('next-six-star-cook', 'next-six-star-cook-bonus')
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '一猪六吃'
          )
        """,
    ),
)

__all__ = ["MIGRATION_0019"]
