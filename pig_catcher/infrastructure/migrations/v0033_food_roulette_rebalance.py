"""Schema 33: food rebalances, durable roulette chances and active-effect conversion."""

from .model import Migration

_MIST_PARAMS = (
    '{"current_window_only":true,"five_star_percent":30.7692,'
    '"four_star_percent":61.5385,"six_star_percent":7.6923,"uses":5}'
)

_CURRENT_WINDOW_EXPIRY_SQL = """
CASE
    WHEN CAST(strftime('%H', 'now', '+8 hours') AS INTEGER) < 9
        THEN strftime('%Y-%m-%dT01:00:00.000Z', 'now', '+8 hours')
    WHEN CAST(strftime('%H', 'now', '+8 hours') AS INTEGER) < 12
        THEN strftime('%Y-%m-%dT04:00:00.000Z', 'now', '+8 hours')
    WHEN CAST(strftime('%H', 'now', '+8 hours') AS INTEGER) < 19
        THEN strftime('%Y-%m-%dT11:00:00.000Z', 'now', '+8 hours')
    ELSE strftime('%Y-%m-%dT16:00:00.000Z', 'now', '+8 hours')
END
"""


MIGRATION_0033 = Migration(
    version=33,
    name="food-roulette-rebalance",
    statements=(
        """
        CREATE TABLE player_roulette_state(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            available_spins INTEGER NOT NULL DEFAULT 0 CHECK (available_spins >= 0),
            source_food_instance_id TEXT NOT NULL REFERENCES food_instances(food_instance_id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO player_roulette_state(
            player_id, available_spins, source_food_instance_id,
            created_at, updated_at
        )
        SELECT effect.player_id, COUNT(*) * 3,
               MIN(effect.source_food_instance_id),
               strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
               strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM player_food_effects AS effect
        JOIN food_instances AS source
          ON source.food_instance_id = effect.source_food_instance_id
        WHERE source.display_name_snapshot = '猪保千猪排轮盘'
          AND effect.consumed_uses < effect.granted_uses
        GROUP BY effect.player_id
        """,
        """
        UPDATE food_templates
        SET effect_id = CASE display_name
                WHEN '猪利猪' THEN 'next-five-six-star-catch'
                WHEN '猪皮奶' THEN 'next-small-six-star-catch'
                WHEN '珍猪奶茶' THEN 'catch-duplication-chance'
                WHEN '雾蓝键盘大福' THEN 'next-high-star-catch'
                WHEN '猪保千猪排轮盘' THEN 'roulette-chances'
                WHEN '彩彩修车猪慕斯' THEN 'six-star-cook-failure-return'
            END,
            effect_params_json = CASE display_name
                WHEN '猪利猪' THEN '{"five_star_bonus_percent":5,"six_star_bonus_percent":3}'
                WHEN '猪皮奶' THEN '{"bonus_percent":15}'
                WHEN '珍猪奶茶' THEN '{"chance_percent":55,"uses":2}'
                WHEN '雾蓝键盘大福' THEN '__MIST_PARAMS__'
                WHEN '猪保千猪排轮盘' THEN '{"count":3}'
                WHEN '彩彩修车猪慕斯' THEN '{"return_chance_percent":75,"uses":3}'
            END,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE display_name IN (
            '猪利猪', '猪皮奶', '珍猪奶茶', '雾蓝键盘大福',
            '猪保千猪排轮盘', '彩彩修车猪慕斯'
        )
        """.replace("__MIST_PARAMS__", _MIST_PARAMS),
        """
        UPDATE food_instances
        SET effect_id = CASE display_name_snapshot
                WHEN '猪利猪' THEN 'next-five-six-star-catch'
                WHEN '猪皮奶' THEN 'next-small-six-star-catch'
                WHEN '珍猪奶茶' THEN 'catch-duplication-chance'
                WHEN '雾蓝键盘大福' THEN 'next-high-star-catch'
                WHEN '猪保千猪排轮盘' THEN 'roulette-chances'
                WHEN '彩彩修车猪慕斯' THEN 'six-star-cook-failure-return'
            END,
            effect_params_json = CASE display_name_snapshot
                WHEN '猪利猪' THEN '{"five_star_bonus_percent":5,"six_star_bonus_percent":3}'
                WHEN '猪皮奶' THEN '{"bonus_percent":15}'
                WHEN '珍猪奶茶' THEN '{"chance_percent":55,"uses":2}'
                WHEN '雾蓝键盘大福' THEN '__MIST_PARAMS__'
                WHEN '猪保千猪排轮盘' THEN '{"count":3}'
                WHEN '彩彩修车猪慕斯' THEN '{"return_chance_percent":75,"uses":3}'
            END,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE state IN ('active', 'locked-for-trade')
          AND display_name_snapshot IN (
              '猪利猪', '猪皮奶', '珍猪奶茶', '雾蓝键盘大福',
              '猪保千猪排轮盘', '彩彩修车猪慕斯'
          )
        """.replace("__MIST_PARAMS__", _MIST_PARAMS),
        """
        UPDATE player_food_effects
        SET effect_id = 'next-five-six-star-catch',
            params_json = '{"five_star_bonus_percent":5,"six_star_bonus_percent":3}',
            granted_uses = 1, consumed_uses = 0,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id FROM food_instances
              WHERE display_name_snapshot = '猪利猪'
          )
        """,
        """
        UPDATE player_food_effects
        SET effect_id = 'next-small-six-star-catch',
            params_json = '{"bonus_percent":15}',
            granted_uses = 1, consumed_uses = 0,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id FROM food_instances
              WHERE display_name_snapshot = '猪皮奶'
          )
        """,
        """
        UPDATE player_food_effects
        SET effect_id = 'catch-duplication-chance',
            params_json = '{"chance_percent":55,"uses":2}',
            granted_uses = 2, consumed_uses = 0,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id FROM food_instances
              WHERE display_name_snapshot = '珍猪奶茶'
          )
        """,
        f"""
        UPDATE player_food_effects
        SET effect_id = 'next-high-star-catch',
            params_json = '{_MIST_PARAMS}',
            granted_uses = 5, consumed_uses = 0,
            expires_at = {_CURRENT_WINDOW_EXPIRY_SQL},
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id FROM food_instances
              WHERE display_name_snapshot = '雾蓝键盘大福'
          )
        """,
        """
        UPDATE player_food_effects
        SET consumed_uses = granted_uses,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id FROM food_instances
              WHERE display_name_snapshot = '猪保千猪排轮盘'
          )
        """,
        """
        UPDATE player_food_effects
        SET effect_id = 'six-star-cook-failure-return',
            params_json = '{"return_chance_percent":75,"uses":3}',
            granted_uses = 3, consumed_uses = 0,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id FROM food_instances
              WHERE display_name_snapshot = '彩彩修车猪慕斯'
          )
        """,
        "DROP INDEX IF EXISTS idx_player_food_effects_active",
        "ALTER TABLE player_food_effects RENAME TO player_food_effects_v32",
        """
        CREATE TABLE player_food_effects (
            effect_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            source_food_instance_id TEXT NOT NULL
                REFERENCES food_instances(food_instance_id),
            effect_id TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            granted_uses INTEGER NOT NULL CHECK (granted_uses >= 1),
            consumed_uses INTEGER NOT NULL DEFAULT 0 CHECK (
                consumed_uses >= 0 AND consumed_uses <= granted_uses
            ),
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO player_food_effects(
            effect_entry_id, player_id, source_food_instance_id,
            effect_id, params_json, granted_uses, consumed_uses,
            expires_at, created_at, updated_at
        )
        SELECT effect_entry_id, player_id, source_food_instance_id,
               effect_id, params_json, granted_uses, consumed_uses,
               expires_at, created_at, updated_at
        FROM player_food_effects_v32
        """,
        "DROP TABLE player_food_effects_v32",
        """
        CREATE INDEX idx_player_food_effects_active
        ON player_food_effects(
            player_id,
            effect_id,
            consumed_uses,
            expires_at,
            created_at
        )
        """,
        """
        CREATE INDEX idx_player_food_effects_source
        ON player_food_effects(source_food_instance_id, created_at)
        """,
    ),
)

__all__ = ["MIGRATION_0033"]
