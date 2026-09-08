"""每道水镜冻独立记录各玩家的两次原始抓猪，避免复制品递归触发。"""

from .model import Migration

MIGRATION_0068 = Migration(
    version=68,
    name="mirror-foods",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
          SELECT 1 FROM battle_matches b WHERE b.battle_id=NEW.battle_id AND
          ((b.definition_version=1 AND NEW.total_uses=5)
           OR (b.definition_version BETWEEN 2 AND 16 AND NEW.total_uses=3)))
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END""",
        """CREATE TABLE water_mirror_targets (
          source_food_id TEXT NOT NULL REFERENCES food_instances(food_instance_id),
          scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
          receiver_id TEXT NOT NULL REFERENCES players(player_id),
          catcher_id TEXT NOT NULL REFERENCES players(player_id),
          remaining INTEGER NOT NULL CHECK(remaining BETWEEN 0 AND 2),
          created_at TEXT NOT NULL,
          PRIMARY KEY(source_food_id,catcher_id))""",
        """CREATE INDEX water_mirror_active ON water_mirror_targets(catcher_id,scope_id,remaining)""",
        """CREATE TABLE water_mirror_claims (
          source_food_id TEXT NOT NULL REFERENCES food_instances(food_instance_id),
          original_pig_id TEXT NOT NULL REFERENCES pig_instances(pig_instance_id),
          copy_pig_id TEXT NOT NULL UNIQUE REFERENCES pig_instances(pig_instance_id),
          PRIMARY KEY(source_food_id,original_pig_id))""",
        """CREATE TRIGGER water_mirror_scope_guard BEFORE INSERT ON water_mirror_targets
          WHEN NOT EXISTS(SELECT 1 FROM players a JOIN players b ON a.scope_id=b.scope_id
            JOIN food_instances f ON f.owner_player_id=a.player_id AND f.scope_id=a.scope_id
            WHERE a.player_id=NEW.receiver_id AND b.player_id=NEW.catcher_id
              AND a.scope_id=NEW.scope_id AND f.food_instance_id=NEW.source_food_id)
          BEGIN SELECT RAISE(ABORT,'水镜复制不能跨群'); END""",
    ),
)
