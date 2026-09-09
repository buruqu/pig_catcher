"""放行 Battle v18 战利品；历史战斗和资产保持原样。"""

from .model import Migration

MIGRATION_0071 = Migration(
    version=71,
    name="firefly-battle-v18",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
          SELECT 1 FROM battle_matches b WHERE b.battle_id=NEW.battle_id AND
          ((b.definition_version=1 AND NEW.total_uses=5)
           OR (b.definition_version BETWEEN 2 AND 18 AND NEW.total_uses=3)))
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END""",
    ),
)
