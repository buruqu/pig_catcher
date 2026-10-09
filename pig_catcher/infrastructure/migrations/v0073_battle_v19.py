"""Battle v19：西西战前形态选择与三次战利品版本约束。"""

from .model import Migration

MIGRATION_0073 = Migration(
    version=73,
    name="daniya-xixi-battle-v19",
    statements=(
        """ALTER TABLE battle_profiles ADD COLUMN battle_form_id TEXT NOT NULL DEFAULT ''
        CHECK(battle_form_id IN ('','xixi-celestial'))""",
        "DROP TRIGGER battle_loot_total_insert",
        """CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
          SELECT 1 FROM battle_matches b WHERE b.battle_id=NEW.battle_id AND
          ((b.definition_version=1 AND NEW.total_uses=5)
           OR (b.definition_version BETWEEN 2 AND 19 AND NEW.total_uses=3)))
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END""",
    ),
)
