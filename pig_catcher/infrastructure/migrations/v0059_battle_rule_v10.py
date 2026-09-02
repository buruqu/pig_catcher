"""Schema 59：允许 Battle v10 规则场按三次战利品结算。"""

from .model import Migration
from .v0058_battle_rule_v9 import GUARDS as _V0058_GUARDS

GUARDS = _V0058_GUARDS

MIGRATION_0059 = Migration(
    version=59,
    name="battle-rule-v10-loot-total",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """
        CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND (
                (b.definition_version=1 AND NEW.total_uses=5)
                OR (b.definition_version IN (2,3,4,5,6,7,8,9,10) AND NEW.total_uses=3)
            )
        )
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END
        """,
    ),
)
