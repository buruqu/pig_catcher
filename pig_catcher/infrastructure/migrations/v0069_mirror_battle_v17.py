"""放行 Battle v17 战利品；存量芭菲队列保留剩余次数和冻结概率。"""

from .model import Migration

MIGRATION_0069 = Migration(
    version=69,
    name="mirror-battle-v17",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
          SELECT 1 FROM battle_matches b WHERE b.battle_id=NEW.battle_id AND
          ((b.definition_version=1 AND NEW.total_uses=5)
           OR (b.definition_version BETWEEN 2 AND 17 AND NEW.total_uses=3)))
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END""",
    ),
)
