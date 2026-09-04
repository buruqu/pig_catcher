"""Schema 62：Battle v13 达妮娅世界与熠～噜猪精确轮盘。"""

from .model import Migration

GUARDS = {"battle_loot_total_insert"}

MIGRATION_0062 = Migration(
    version=62,
    name="battle-rule-v13",
    statements=(
        "DROP TRIGGER battle_loot_total_insert",
        """
        CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND (
                (b.definition_version=1 AND NEW.total_uses=5)
                OR (b.definition_version IN (2,3,4,5,6,7,8,9,10,11,12,13) AND NEW.total_uses=3)
            )
        )
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END
        """,
    ),
)
