"""Schema 51：允许新版半额继承对战按既定三次战利品结算。"""

from .model import Migration
from .v0050_battle_loot_total import GUARDS as _V0050_GUARDS

GUARDS = _V0050_GUARDS

MIGRATION_0051 = Migration(
    version=51,
    name="battle-rule-v3-loot-total",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """
        CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND (
                (b.definition_version=1 AND NEW.total_uses=5)
                OR (b.definition_version IN (2,3) AND NEW.total_uses=3)
            )
        )
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END
        """,
    ),
)
