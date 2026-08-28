"""通用奖励券复用既有库存，追加可审计发放/使用记录和限时自选确认。"""

from .model import Migration

TABLES = ("reward_coupon_grants", "reward_coupon_uses", "item_coupon_choices")

MIGRATION_0043 = Migration(
    version=43,
    name="general-reward-coupons-and-item-bag",
    statements=(
        """CREATE TABLE reward_coupon_grants(
            grant_key TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            coupon_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity>0),
            source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
            source_receipt_id TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(player_id,source_kind,source_id,coupon_id)
        )""",
        """CREATE TABLE reward_coupon_uses(
            use_key TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            coupon_id TEXT NOT NULL, operation TEXT NOT NULL,
            detail_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE item_coupon_choices(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            operation TEXT NOT NULL CHECK(operation='pig-choice'),
            payload_json TEXT NOT NULL,
            expires_ms INTEGER NOT NULL CHECK(expires_ms>0),
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_reward_coupon_grants_player ON reward_coupon_grants(player_id,created_at)",
        "CREATE INDEX idx_reward_coupon_uses_player ON reward_coupon_uses(player_id,created_at)",
        *(
            f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'奖励券账本不可改写'); END"
            for table in ("reward_coupon_grants", "reward_coupon_uses")
            for operation in ("UPDATE", "DELETE")
        ),
    ),
)

GUARDS = (
    "idx_reward_coupon_grants_player",
    "idx_reward_coupon_uses_player",
    "reward_coupon_grants_no_update",
    "reward_coupon_grants_no_delete",
    "reward_coupon_uses_no_update",
    "reward_coupon_uses_no_delete",
)
