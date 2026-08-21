"""Reduce Assam milk-fog-pot automatic gifting from 50% to 40%."""

from .model import Migration

MIGRATION_0029 = Migration(
    version=29,
    name="asamu_auto_gift_rebalance",
    statements=(
        """
        UPDATE food_templates
        SET effect_params_json = json_set(
                effect_params_json,
                '$.auto_gift_chance_percent',
                40.0
            )
        WHERE rarity = 6
          AND display_name = '阿萨姆红茶奶雾锅'
          AND effect_id = 'group-next-exclusive-high-star-catch'
        """,
        """
        UPDATE food_instances
        SET effect_params_json = json_set(
                effect_params_json,
                '$.auto_gift_chance_percent',
                40.0
            )
        WHERE rarity = 6
          AND display_name_snapshot = '阿萨姆红茶奶雾锅'
          AND effect_id = 'group-next-exclusive-high-star-catch'
          AND state IN ('active', 'locked-for-trade')
        """,
        """
        UPDATE group_food_effects
        SET params_json = json_set(
                params_json,
                '$.auto_gift_chance_percent',
                40.0
            ),
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE effect_id = 'group-next-exclusive-high-star-catch'
          AND expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '阿萨姆红茶奶雾锅'
          )
        """,
    ),
)

__all__ = ["MIGRATION_0029"]
