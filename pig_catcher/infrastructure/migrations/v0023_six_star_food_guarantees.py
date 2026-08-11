"""Restore composite cooking boosts and add six-star catch guarantees."""

from .model import Migration

_OMELETTE_PARAMS = (
    '{"coin_per_player":1004,"dedicated_catches":0,'
    '"five_star_multiplier":1.004,"personal_six_star_cook_percent":60,'
    '"personal_six_star_cook_uses":2,"six_star_multiplier":1.004,'
    '"source_label":"猪鼻蛋包饭"}'
)
_RIBS_PARAMS = (
    '{"count":1,"five_star_multiplier":1.007,"group_coin":1007,'
    '"group_dedicated_catches":10,"hidden_boost_chance_percent":10,'
    '"hidden_five_star_multiplier":10.04,"hidden_six_star_multiplier":10.04,'
    '"six_star_multiplier":1.007}'
)
_RIBS_GROUP_PARAMS = (
    '{"coin_per_player":1007,"dedicated_catches":10,'
    '"five_star_multiplier":1.007,"hidden_boost_chance_percent":10,'
    '"hidden_five_star_multiplier":10.04,"hidden_six_star_multiplier":10.04,'
    '"six_star_multiplier":1.007,"source_label":"糖醋排骨"}'
)
_MIST_DAIFUKU_PARAMS = (
    '{"five_star_percent":30,"four_star_percent":60,'
    '"last_use_six_star_percent":50,"six_star_percent":10,"uses":10}'
)
_BAOGIAN_ROULETTE_PARAMS = '{"last_use_six_star_percent":50,"uses":10}'


def _escaped(value: str) -> str:
    return value.replace("'", "''")


MIGRATION_0023 = Migration(
    version=23,
    name="six_star_food_guarantees",
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
        UPDATE food_templates
        SET effect_id = 'quota-reset',
            effect_params_json = '{_escaped(_RIBS_PARAMS)}'
        WHERE rarity = 6 AND display_name = '糖醋排骨'
        """,
        f"""
        UPDATE food_instances
        SET effect_id = 'quota-reset',
            effect_params_json = '{_escaped(_RIBS_PARAMS)}'
        WHERE rarity = 6
          AND display_name_snapshot = '糖醋排骨'
          AND state IN ('active', 'locked-for-trade')
        """,
        f"""
        UPDATE food_templates
        SET effect_id = 'next-high-star-catch',
            effect_params_json = '{_escaped(_MIST_DAIFUKU_PARAMS)}'
        WHERE rarity = 6 AND display_name = '雾蓝键盘大福'
        """,
        f"""
        UPDATE food_instances
        SET effect_id = 'next-high-star-catch',
            effect_params_json = '{_escaped(_MIST_DAIFUKU_PARAMS)}'
        WHERE rarity = 6
          AND display_name_snapshot = '雾蓝键盘大福'
          AND state IN ('active', 'locked-for-trade')
        """,
        f"""
        UPDATE food_templates
        SET effect_id = 'even-catch-distribution',
            effect_params_json = '{_escaped(_BAOGIAN_ROULETTE_PARAMS)}'
        WHERE rarity = 6 AND display_name = '猪保千猪排轮盘'
        """,
        f"""
        UPDATE food_instances
        SET effect_id = 'even-catch-distribution',
            effect_params_json = '{_escaped(_BAOGIAN_ROULETTE_PARAMS)}'
        WHERE rarity = 6
          AND display_name_snapshot = '猪保千猪排轮盘'
          AND state IN ('active', 'locked-for-trade')
        """,
        f"""
        UPDATE player_food_effects
        SET params_json = '{_escaped(_RIBS_PARAMS)}',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE effect_id = 'quota-reset'
          AND consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '糖醋排骨'
          )
        """,
        f"""
        UPDATE player_food_effects
        SET params_json = '{_escaped(_MIST_DAIFUKU_PARAMS)}',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE effect_id = 'next-high-star-catch'
          AND consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '雾蓝键盘大福'
          )
        """,
        f"""
        UPDATE player_food_effects
        SET params_json = '{_escaped(_BAOGIAN_ROULETTE_PARAMS)}',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE effect_id = 'even-catch-distribution'
          AND consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '猪保千猪排轮盘'
          )
        """,
        f"""
        UPDATE group_food_effects
        SET params_json = '{_escaped(_RIBS_GROUP_PARAMS)}',
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE effect_id = 'group-window-high-star-boost'
          AND expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '糖醋排骨'
          )
        """,
        f"""
        UPDATE group_food_effects
        SET params_json = '{_escaped(_OMELETTE_PARAMS)}',
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

__all__ = ["MIGRATION_0023"]
