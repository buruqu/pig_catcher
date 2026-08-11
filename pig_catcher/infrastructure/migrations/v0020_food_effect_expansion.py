"""Schema 20：迁移九道公共四、五星菜的效果快照。"""

from .model import Migration

_FOOD_NAMES = """
    '猪利猪', '猪籽军舰', '猪猪玉子烧', '猪饺',
    '黑猪麻汤圆', '猪猪白菜炖粉条', '猪咪莓蛋糕', '猪果冻', '猪皮奶'
"""

_EFFECT_ID_CASE = """
    CASE {name_column}
        WHEN '猪利猪' THEN 'next-small-six-star-catch'
        WHEN '猪籽军舰' THEN 'next-food-rarity'
        WHEN '猪猪玉子烧' THEN 'next-cook-quality'
        WHEN '猪饺' THEN 'next-stackable-six-star-cook-bonus'
        WHEN '黑猪麻汤圆' THEN 'next-giant-five-star-catch'
        WHEN '猪猪白菜炖粉条' THEN 'next-collaboration-catch'
        WHEN '猪咪莓蛋糕' THEN 'next-extreme-five-star-cook'
        WHEN '猪果冻' THEN 'next-catch-quality'
        WHEN '猪皮奶' THEN 'next-five-six-star-catch'
    END
"""

_PARAMS_CASE = """
    CASE {name_column}
        WHEN '猪利猪' THEN '{{"bonus_percent":1}}'
        WHEN '猪籽军舰' THEN '{{"multiplier":2.0,"rarity":5}}'
        WHEN '猪猪玉子烧' THEN '{{"shift_percent":15,"uses":1}}'
        WHEN '猪饺' THEN '{{"bonus_percent":1,"max_stacks":5}}'
        WHEN '黑猪麻汤圆' THEN
            '{{"five_star_multiplier":3.0,"giant_template_multiplier":4.0,"stature_bias":0.5}}'
        WHEN '猪猪白菜炖粉条' THEN
            '{{"five_star_percent":30,"four_star_percent":55,"three_star_percent":15}}'
        WHEN '猪咪莓蛋糕' THEN '{{"five_star_percent":85}}'
        WHEN '猪果冻' THEN '{{"multiplier":3.0,"uses":3}}'
        WHEN '猪皮奶' THEN '{{"five_star_bonus_percent":20,"six_star_bonus_percent":3}}'
    END
"""

_SOURCE_NAME = """
    (SELECT display_name_snapshot
     FROM food_instances
     WHERE food_instance_id = player_food_effects.source_food_instance_id)
"""

MIGRATION_0020 = Migration(
    version=20,
    name="food-effect-expansion",
    statements=(
        f"""
        UPDATE food_templates
        SET effect_id = {_EFFECT_ID_CASE.format(name_column='display_name')},
            effect_params_json = {_PARAMS_CASE.format(name_column='display_name')}
        WHERE display_name IN ({_FOOD_NAMES})
        """,
        f"""
        UPDATE food_instances
        SET effect_id = {_EFFECT_ID_CASE.format(name_column='display_name_snapshot')},
            effect_params_json = {_PARAMS_CASE.format(name_column='display_name_snapshot')}
        WHERE state IN ('active', 'locked-for-trade')
          AND display_name_snapshot IN ({_FOOD_NAMES})
        """,
        f"""
        UPDATE player_food_effects
        SET effect_id = {_EFFECT_ID_CASE.format(name_column=_SOURCE_NAME)},
            params_json = {_PARAMS_CASE.format(name_column=_SOURCE_NAME)},
            granted_uses = CASE {_SOURCE_NAME}
                WHEN '猪果冻' THEN 3
                ELSE 1
            END
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot IN ({_FOOD_NAMES})
          )
        """,
    ),
)

__all__ = ["MIGRATION_0020"]
