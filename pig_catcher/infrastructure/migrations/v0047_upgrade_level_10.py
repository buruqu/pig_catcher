"""Schema 47：永久升级扩展为十级，并等价迁移既有五级进度。"""

from .model import Migration

MIGRATION_0047 = Migration(
    version=47,
    name="upgrade-level-10",
    statements=(
        # A few very old, valid partial snapshots did not materialize the
        # optional economy table even though their user_version had advanced.
        # Recreate the historical shape first so the forward migration stays
        # total and those databases can still open safely.
        """
        CREATE TABLE IF NOT EXISTS upgrades (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            upgrade_type TEXT NOT NULL CHECK (upgrade_type IN ('feed', 'cookware')),
            level INTEGER NOT NULL CHECK (level BETWEEN 0 AND 5),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (player_id, upgrade_type)
        )
        """,
        """
        CREATE TABLE upgrades_v47 (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            upgrade_type TEXT NOT NULL CHECK (upgrade_type IN ('feed', 'cookware')),
            level INTEGER NOT NULL CHECK (level BETWEEN 0 AND 10),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (player_id, upgrade_type)
        )
        """,
        # 新版每一级提供旧版半级效果；等级乘二可使所有既有玩家
        # 在迁移前后保持完全相同的实际饲料和厨具收益。
        """
        INSERT INTO upgrades_v47(player_id, upgrade_type, level, updated_at)
        SELECT player_id, upgrade_type, level * 2, updated_at
        FROM upgrades
        """,
        "DROP TABLE upgrades",
        "ALTER TABLE upgrades_v47 RENAME TO upgrades",
    ),
)
