"""Schema 18：六星菜效果快照、滚动七天额度与专属抓猪次数。"""

from .model import Migration

MIGRATION_0018 = Migration(
    version=18,
    name="food-effect-rebalance",
    statements=(
        """
        ALTER TABLE command_receipts
        ADD COLUMN catch_quota_cost INTEGER NOT NULL DEFAULT 1
            CHECK (catch_quota_cost IN (0, 1))
        """,
        """
        UPDATE food_templates
        SET effect_id = 'quota-reset', effect_params_json = '{"count":1}'
        WHERE rarity = 6
          AND display_name = '糖醋排骨'
          AND effect_id = 'exclusive-catch-quality'
        """,
        """
        UPDATE food_templates
        SET effect_params_json = CASE display_name
            WHEN '猪鼻蛋包饭' THEN '{"six_star_percent":60,"uses":2}'
            WHEN '小马猪蒙布朗' THEN '{"six_star_percent":60,"uses":5}'
            WHEN '雾蓝键盘大福' THEN '{"five_star_percent":30,"four_star_percent":60,"six_star_percent":10,"uses":10}'
            WHEN '彩彩修车猪慕斯' THEN '{"uses":10}'
            WHEN '猪保千猪排轮盘' THEN '{"uses":10}'
            WHEN '一猪六吃' THEN '{"bonus_percent":15}'
        END
        WHERE display_name IN (
            '猪鼻蛋包饭', '小马猪蒙布朗', '雾蓝键盘大福',
            '彩彩修车猪慕斯', '猪保千猪排轮盘', '一猪六吃'
        )
        """,
        """
        UPDATE food_instances
        SET effect_id = 'quota-reset', effect_params_json = '{"count":1}'
        WHERE display_name_snapshot = '糖醋排骨'
          AND effect_id = 'exclusive-catch-quality'
          AND state IN ('active', 'locked-for-trade')
        """,
        """
        UPDATE food_instances
        SET effect_params_json = CASE display_name_snapshot
            WHEN '猪鼻蛋包饭' THEN '{"six_star_percent":60,"uses":2}'
            WHEN '小马猪蒙布朗' THEN '{"six_star_percent":60,"uses":5}'
            WHEN '雾蓝键盘大福' THEN '{"five_star_percent":30,"four_star_percent":60,"six_star_percent":10,"uses":10}'
            WHEN '彩彩修车猪慕斯' THEN '{"uses":10}'
            WHEN '猪保千猪排轮盘' THEN '{"uses":10}'
            WHEN '一猪六吃' THEN '{"bonus_percent":15}'
        END
        WHERE state IN ('active', 'locked-for-trade')
          AND display_name_snapshot IN (
              '猪鼻蛋包饭', '小马猪蒙布朗', '雾蓝键盘大福',
              '彩彩修车猪慕斯', '猪保千猪排轮盘', '一猪六吃'
          )
        """,
        """
        UPDATE player_food_effects
        SET effect_id = 'quota-reset',
            params_json = '{"count":1}',
            granted_uses = 1
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot = '糖醋排骨'
          )
          AND effect_id = 'exclusive-catch-quality'
        """,
        """
        UPDATE player_food_effects
        SET params_json = CASE (
                SELECT display_name_snapshot
                FROM food_instances
                WHERE food_instance_id = player_food_effects.source_food_instance_id
            )
            WHEN '猪鼻蛋包饭' THEN '{"six_star_percent":60,"uses":2}'
            WHEN '小马猪蒙布朗' THEN '{"six_star_percent":60,"uses":5}'
            WHEN '雾蓝键盘大福' THEN '{"five_star_percent":30,"four_star_percent":60,"six_star_percent":10,"uses":10}'
            WHEN '彩彩修车猪慕斯' THEN '{"uses":10}'
            WHEN '猪保千猪排轮盘' THEN '{"uses":10}'
            WHEN '一猪六吃' THEN '{"bonus_percent":15}'
        END,
        granted_uses = CASE (
                SELECT display_name_snapshot
                FROM food_instances
                WHERE food_instance_id = player_food_effects.source_food_instance_id
            )
            WHEN '猪鼻蛋包饭' THEN 2
            WHEN '小马猪蒙布朗' THEN 5
            WHEN '雾蓝键盘大福' THEN 10
            WHEN '彩彩修车猪慕斯' THEN 10
            WHEN '猪保千猪排轮盘' THEN 10
            ELSE granted_uses
        END
        WHERE consumed_uses < granted_uses
          AND source_food_instance_id IN (
              SELECT food_instance_id
              FROM food_instances
              WHERE display_name_snapshot IN (
                  '猪鼻蛋包饭', '小马猪蒙布朗', '雾蓝键盘大福',
                  '彩彩修车猪慕斯', '猪保千猪排轮盘', '一猪六吃'
              )
          )
        """,
        """
        UPDATE player_catch_quota_bonuses
        SET weekly_expires_at = CASE
            WHEN CAST(strftime('%H', datetime(
                COALESCE((
                    SELECT disposed_at FROM food_instances
                    WHERE food_instance_id = weekly_source_food_instance_id
                ), updated_at), '+8 hours', '+7 days'
            )) AS INTEGER) < 9
                THEN strftime(
                    '%Y-%m-%dT%H:%M:%fZ',
                    datetime(date(datetime(
                        COALESCE((
                            SELECT disposed_at FROM food_instances
                            WHERE food_instance_id = weekly_source_food_instance_id
                        ), updated_at), '+8 hours', '+7 days'
                    )) || ' 09:00:00', '-8 hours')
                )
            WHEN CAST(strftime('%H', datetime(
                COALESCE((
                    SELECT disposed_at FROM food_instances
                    WHERE food_instance_id = weekly_source_food_instance_id
                ), updated_at), '+8 hours', '+7 days'
            )) AS INTEGER) < 12
                THEN strftime(
                    '%Y-%m-%dT%H:%M:%fZ',
                    datetime(date(datetime(
                        COALESCE((
                            SELECT disposed_at FROM food_instances
                            WHERE food_instance_id = weekly_source_food_instance_id
                        ), updated_at), '+8 hours', '+7 days'
                    )) || ' 12:00:00', '-8 hours')
                )
            WHEN CAST(strftime('%H', datetime(
                COALESCE((
                    SELECT disposed_at FROM food_instances
                    WHERE food_instance_id = weekly_source_food_instance_id
                ), updated_at), '+8 hours', '+7 days'
            )) AS INTEGER) < 19
                THEN strftime(
                    '%Y-%m-%dT%H:%M:%fZ',
                    datetime(date(datetime(
                        COALESCE((
                            SELECT disposed_at FROM food_instances
                            WHERE food_instance_id = weekly_source_food_instance_id
                        ), updated_at), '+8 hours', '+7 days'
                    )) || ' 19:00:00', '-8 hours')
                )
            ELSE strftime(
                '%Y-%m-%dT%H:%M:%fZ',
                datetime(date(datetime(
                    COALESCE((
                        SELECT disposed_at FROM food_instances
                        WHERE food_instance_id = weekly_source_food_instance_id
                    ), updated_at), '+8 hours', '+8 days'
                )) || ' 00:00:00', '-8 hours')
            )
        END
        WHERE weekly_bonus > 0
          AND weekly_source_food_instance_id IS NOT NULL
        """,
    ),
)

__all__ = ["MIGRATION_0018"]
