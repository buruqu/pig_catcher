"""Schema 53：允许 Battle v5 规则场按既定三次战利品结算。"""

from .model import Migration
from .v0052_battle_rule_v4 import GUARDS as _V0052_GUARDS

GUARDS = _V0052_GUARDS

MIGRATION_0053 = Migration(
    version=53,
    name="battle-rule-v5-loot-total",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """
        CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND (
                (b.definition_version=1 AND NEW.total_uses=5)
                OR (b.definition_version IN (2,3,4,5) AND NEW.total_uses=3)
            )
        )
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END
        """,
    ),
)
