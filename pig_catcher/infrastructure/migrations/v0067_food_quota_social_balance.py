"""Schema 67：双向第二场对战、有限时段额度及慕斯加餐。"""

from .model import Migration

MIGRATION_0067 = Migration(
    version=67,
    name="food-quota-social-balance",
    statements=(
        """CREATE TABLE battle_daily_second_uses(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            day TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN('initiator','opponent')),
            generation INTEGER NOT NULL CHECK(generation>=0),
            battle_id TEXT NOT NULL REFERENCES battle_matches(battle_id),
            occurred_ms INTEGER NOT NULL,
            PRIMARY KEY(player_id,day,role,generation),
            UNIQUE(player_id,battle_id,role))""",
        """CREATE TRIGGER battle_second_use_guard BEFORE INSERT ON battle_daily_second_uses
        WHEN NOT EXISTS(SELECT 1 FROM battle_matches b JOIN players p ON p.player_id=NEW.player_id
          WHERE b.battle_id=NEW.battle_id AND b.status='active' AND b.scope_id=NEW.scope_id
            AND p.scope_id=NEW.scope_id AND b.accepted_day=NEW.day
            AND ((NEW.role='initiator' AND NEW.player_id=b.initiator_id)
              OR (NEW.role='opponent' AND NEW.player_id=b.opponent_id)))
          OR NEW.generation!=COALESCE((SELECT generation FROM battle_daily_quota_state
            WHERE player_id=NEW.player_id AND day=NEW.day),0)
        BEGIN SELECT RAISE(ABORT,'第二次对战额度与场次、群或重置代次不符'); END""",
        """CREATE TRIGGER battle_second_use_no_update BEFORE UPDATE ON battle_daily_second_uses
        BEGIN SELECT RAISE(ABORT,'对战额度账本不可改写'); END""",
        """CREATE TRIGGER battle_second_use_no_delete BEFORE DELETE ON battle_daily_second_uses
        BEGIN SELECT RAISE(ABORT,'对战额度账本不可删除'); END""",
        "ALTER TABLE player_catch_window_transfers ADD COLUMN target_catches_used INTEGER NOT NULL DEFAULT 0",
        # 旧搬移计划按指定来源收敛，收据与已获得资产保持原样。
        """UPDATE player_catch_window_transfers SET transferred_uses=MIN(transferred_uses,5
          +MIN(5,COALESCE((SELECT permanent_bonus FROM player_catch_quota_bonuses q
             WHERE q.player_id=player_catch_window_transfers.player_id),0))
          +MIN(5,COALESCE((SELECT weekly_bonus FROM player_catch_quota_bonuses q
             WHERE q.player_id=player_catch_window_transfers.player_id
             AND q.weekly_expires_at>player_catch_window_transfers.blocked_window_start),0))
          +CASE WHEN EXISTS(SELECT 1 FROM player_food_effects e JOIN food_instances f
             ON f.food_instance_id=e.source_food_instance_id
             WHERE e.player_id=player_catch_window_transfers.player_id AND e.effect_id='today-window-catches'
             AND f.display_name_snapshot='猪寿司拼盘' AND e.consumed_uses<e.granted_uses
             AND e.expires_at>player_catch_window_transfers.blocked_window_start) THEN 2 ELSE 0 END)""",
        """UPDATE player_catch_window_transfers SET target_catches_used=MIN(34,(
           SELECT COUNT(*) FROM command_receipts r
           WHERE r.player_id=player_catch_window_transfers.player_id
             AND r.command_name='pig-catcher.catch'
             AND r.created_at>=player_catch_window_transfers.target_window_start
             AND r.created_at<player_catch_window_transfers.target_window_end
             AND r.text_summary LIKE '%月栖萤光卷平移时段%'))""",
        """CREATE TRIGGER catch_transfer_cap_insert BEFORE INSERT ON player_catch_window_transfers
        WHEN NEW.transferred_uses NOT BETWEEN 0 AND 17 OR NEW.target_catches_used NOT BETWEEN 0 AND 34
        BEGIN SELECT RAISE(ABORT,'月栖卷搬移最多17次，高星结算最多34次'); END""",
        """CREATE TRIGGER catch_transfer_cap_update BEFORE UPDATE ON player_catch_window_transfers
        WHEN NEW.transferred_uses NOT BETWEEN 0 AND 17 OR NEW.target_catches_used NOT BETWEEN 0 AND 34
        BEGIN SELECT RAISE(ABORT,'月栖卷搬移最多17次，高星结算最多34次'); END""",
        """UPDATE food_templates SET effect_id='next-six-star-cook-duplicate',effect_params_json='{}'
        WHERE display_name='彩彩修车猪慕斯'""",
        """UPDATE food_instances SET effect_id='next-six-star-cook-duplicate',effect_params_json='{}'
        WHERE display_name_snapshot='彩彩修车猪慕斯' AND state IN('active','locked-for-trade')""",
        """UPDATE player_food_effects SET effect_id='next-six-star-cook-duplicate',params_json='{}',
        granted_uses=1,consumed_uses=0 WHERE effect_id='six-star-cook-failure-return'
        AND consumed_uses<granted_uses AND (expires_at IS NULL OR expires_at>strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        AND source_food_instance_id IN(SELECT food_instance_id FROM food_instances
          WHERE display_name_snapshot='彩彩修车猪慕斯')""",
    ),
)
