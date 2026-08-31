"""Schema 52：允许撅撅猪双形态规则场按三次战利品结算。"""

from .model import Migration
from .v0051_battle_rule_v3 import GUARDS as _V0051_GUARDS

GUARDS = _V0051_GUARDS

MIGRATION_0052 = Migration(
    version=52,
    name="battle-rule-v4-loot-total",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """
        CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND (
                (b.definition_version=1 AND NEW.total_uses=5)
                OR (b.definition_version IN (2,3,4) AND NEW.total_uses=3)
            )
        )
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END
        """,
    ),
)
