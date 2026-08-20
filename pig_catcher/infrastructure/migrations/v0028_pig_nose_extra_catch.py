"""Turn the pig-nose group window multiplier into one extra catch per player."""

from .model import Migration

_OMELETTE_PARAMS = (
    '{"coin_per_player":1004,"dedicated_catches":1,"dedicated_only":true,'
    '"five_star_multiplier":1.004,"personal_six_star_cook_percent":60,'
    '"personal_six_star_cook_uses":2,"six_star_multiplier":1.004,'
    '"source_label":"猪鼻蛋包饭"}'
)


def _escaped(value: str) -> str:
    return value.replace("'", "''")


MIGRATION_0028 = Migration(
    version=28,
    name="pig_nose_extra_catch",
    statements=(
        f"""
        UPDATE food_templates
        SET effect_id = 'group-window-high-star-boost',
            effect_params_json = '{_escaped(_OMELETTE_PARAMS)}'
        WHERE rarity = 6 AND display_name = '猪鼻蛋包饭'
        """,
        f"""
        UPDATE food_instances
        SET effect_id = 'group-window-high-star-boost',
            effect_params_json = '{_escaped(_OMELETTE_PARAMS)}'
        WHERE rarity = 6
          AND display_name_snapshot = '猪鼻蛋包饭'
          AND state IN ('active', 'locked-for-trade')
        """,
        f"""
        UPDATE group_food_effects
        SET params_json = '{_escaped(_OMELETTE_PARAMS)}',
            granted_uses_per_player = 1,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE effect_id = 'group-window-high-star-boost'
          AND expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '猪鼻蛋包饭'
          )
        """,
    ),
)

__all__ = ["MIGRATION_0028"]
