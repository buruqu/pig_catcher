"""Schema 42：只为仍持有的资产保留短编号，保留全部历史 UUID 引用。"""

from .model import Migration
from .v0016_alphanumeric_short_codes import MIGRATION_0016
from .v0031_asset_favorites import MIGRATION_0031
from .v0037_dispatch import MIGRATION_0037
from .v0038_tours import MIGRATION_0038
from .v0039_battles import MIGRATION_0039

# SQLite 重建表时会删除该表自己的触发器；其他表中引用它的触发器还会令
# DROP/RENAME 之间的 schema 暂时无效。因此只暂停这些既有约束，原样重建。
# 旧迁移是不可变的版本定义，不读取运行时数据库或猜测第三方 SQL。
_PRESERVED_TRIGGERS = tuple(
    statement
    for migration in (MIGRATION_0037, MIGRATION_0038, MIGRATION_0039)
    for statement in migration.statements
    if statement.lstrip().startswith("CREATE TRIGGER")
    and ("pig_instances" in statement or "food_instances" in statement)
)
_PRESERVED_INDEXES = tuple(
    statement
    for migration in (MIGRATION_0016, MIGRATION_0031)
    for statement in migration.statements
    if statement.lstrip().startswith("CREATE INDEX") and ("pig_instances" in statement or "food_instances" in statement)
)

ACTIVE_CODE_INDEXES = (
    "idx_pig_active_short_code",
    "idx_food_active_short_code",
)

_CROSS_KIND_GUARDS = tuple(
    f"""CREATE TRIGGER {kind}_active_short_code_{operation.lower()}
    BEFORE {event} ON {kind}_instances
    WHEN NEW.state IN ('active', 'locked-for-trade')
      AND EXISTS(
        SELECT 1 FROM {other}_instances
        WHERE short_code COLLATE NOCASE = NEW.short_code
          AND state IN ('active', 'locked-for-trade')
      )
    BEGIN SELECT RAISE(ABORT, '资产短编号已被仍持有的猪猪或美食占用'); END"""
    for kind, other in (("pig", "food"), ("food", "pig"))
    for operation, event in (("INSERT", "INSERT"), ("UPDATE", "UPDATE OF short_code, state"))
)

GUARDS = ACTIVE_CODE_INDEXES + tuple(sql.split()[2] for sql in _CROSS_KIND_GUARDS)

MIGRATION_0042 = Migration(
    version=42,
    name="asset-code-lifecycle",
    statements=(
        # 旧版只在应用层校验跨类型唯一。发现未知旧冲突时必须整体回滚，
        # 不能擅自给玩家改号或让迁移后的查询随机选中其中一个实例。
        "CREATE TABLE asset_code_v42_guard(short_code TEXT NOT NULL COLLATE NOCASE UNIQUE)",
        """
        INSERT INTO asset_code_v42_guard(short_code)
        SELECT short_code FROM pig_instances WHERE state IN ('active', 'locked-for-trade')
        UNION ALL
        SELECT short_code FROM food_instances WHERE state IN ('active', 'locked-for-trade')
        """,
        "DROP TABLE asset_code_v42_guard",
        *(f"DROP TRIGGER {sql.split()[2]}" for sql in _PRESERVED_TRIGGERS),
        """
        CREATE TABLE pig_instances_v42 (
            pig_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL COLLATE NOCASE CHECK (
                length(short_code) BETWEEN 4 AND 16
                AND short_code NOT GLOB '*[^0-9A-Za-z]*'
            ),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            owner_player_id TEXT NOT NULL REFERENCES players(player_id),
            template_id TEXT NOT NULL REFERENCES pig_templates(template_id),
            template_version INTEGER NOT NULL CHECK (template_version >= 1),
            rarity INTEGER NOT NULL CHECK (rarity BETWEEN 1 AND 6),
            display_name_snapshot TEXT NOT NULL,
            size_value REAL NOT NULL CHECK (size_value > 0),
            size_percentile REAL NOT NULL CHECK (size_percentile BETWEEN 0 AND 1),
            weight_value REAL NOT NULL CHECK (weight_value > 0),
            weight_percentile REAL NOT NULL CHECK (weight_percentile BETWEEN 0 AND 1),
            fat_ratio REAL NOT NULL CHECK (fat_ratio BETWEEN 0 AND 100),
            official_value INTEGER NOT NULL CHECK (official_value >= 0),
            ruleset_version INTEGER NOT NULL CHECK (ruleset_version >= 1),
            random_snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'active', 'locked-for-trade', 'sold',
                    'consumed-for-cooking', 'admin-removed'
                )
            ),
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL,
            display_variant TEXT NOT NULL DEFAULT 'pig'
                CHECK (display_variant IN ('pig', 'sticker')),
            is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1))
        )
        """,
        """
        INSERT INTO pig_instances_v42(
            pig_instance_id, short_code, scope_id, owner_player_id,
            template_id, template_version, rarity, display_name_snapshot,
            size_value, size_percentile, weight_value, weight_percentile,
            fat_ratio, official_value, ruleset_version, random_snapshot_json,
            state, locked_trade_id, acquired_at, disposed_at, updated_at,
            display_variant, is_favorite
        )
        SELECT pig_instance_id, short_code, scope_id, owner_player_id,
               template_id, template_version, rarity, display_name_snapshot,
               size_value, size_percentile, weight_value, weight_percentile,
               fat_ratio, official_value, ruleset_version, random_snapshot_json,
               state, locked_trade_id, acquired_at, disposed_at, updated_at,
               display_variant, is_favorite
        FROM pig_instances
        """,
        "DROP TABLE pig_instances",
        "ALTER TABLE pig_instances_v42 RENAME TO pig_instances",
        """
        CREATE TABLE food_instances_v42 (
            food_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL COLLATE NOCASE CHECK (
                length(short_code) BETWEEN 4 AND 16
                AND short_code NOT GLOB '*[^0-9A-Za-z]*'
            ),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            owner_player_id TEXT NOT NULL REFERENCES players(player_id),
            template_id TEXT NOT NULL REFERENCES food_templates(template_id),
            template_version INTEGER NOT NULL CHECK (template_version >= 1),
            source_pig_instance_id TEXT REFERENCES pig_instances(pig_instance_id),
            rarity INTEGER NOT NULL CHECK (rarity BETWEEN 1 AND 6),
            display_name_snapshot TEXT NOT NULL,
            portion_weight REAL NOT NULL CHECK (portion_weight > 0),
            fat_category TEXT NOT NULL CHECK (fat_category IN ('lean', 'balanced', 'fatty')),
            official_value INTEGER NOT NULL CHECK (official_value >= 0),
            effect_id TEXT NOT NULL DEFAULT '',
            effect_params_json TEXT NOT NULL DEFAULT '{}',
            ruleset_version INTEGER NOT NULL CHECK (ruleset_version >= 1),
            random_snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('active', 'locked-for-trade', 'sold', 'consumed', 'admin-removed')
            ),
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL,
            is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1))
        )
        """,
        """
        INSERT INTO food_instances_v42(
            food_instance_id, short_code, scope_id, owner_player_id,
            template_id, template_version, source_pig_instance_id,
            rarity, display_name_snapshot, portion_weight, fat_category,
            official_value, effect_id, effect_params_json, ruleset_version,
            random_snapshot_json, state, locked_trade_id, acquired_at,
            disposed_at, updated_at, is_favorite
        )
        SELECT food_instance_id, short_code, scope_id, owner_player_id,
               template_id, template_version, source_pig_instance_id,
               rarity, display_name_snapshot, portion_weight, fat_category,
               official_value, effect_id, effect_params_json, ruleset_version,
               random_snapshot_json, state, locked_trade_id, acquired_at,
               disposed_at, updated_at, is_favorite
        FROM food_instances
        """,
        "DROP TABLE food_instances",
        "ALTER TABLE food_instances_v42 RENAME TO food_instances",
        *_PRESERVED_INDEXES,
        """
        CREATE UNIQUE INDEX idx_pig_active_short_code
        ON pig_instances(short_code COLLATE NOCASE)
        WHERE state IN ('active', 'locked-for-trade')
        """,
        """
        CREATE UNIQUE INDEX idx_food_active_short_code
        ON food_instances(short_code COLLATE NOCASE)
        WHERE state IN ('active', 'locked-for-trade')
        """,
        *_PRESERVED_TRIGGERS,
        *_CROSS_KIND_GUARDS,
    ),
)
