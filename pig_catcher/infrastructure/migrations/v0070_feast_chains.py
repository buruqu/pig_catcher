"""保留旧的已食用效果；未食用实例切换到新版，历史回执不变。"""

from .model import Migration

TABLES = {"player_clover_chains", "player_moon_feasts"}
GUARDS = {"idx_clover_active", "idx_moon_active"} | {
    f"{table}_scope_{operation}" for table in TABLES for operation in ("insert", "update")
}

MIGRATION_0070 = Migration(
    version=70,
    name="clover-chain-and-moon-feast",
    statements=(
        """CREATE TABLE player_clover_chains(
            source_food_instance_id TEXT PRIMARY KEY REFERENCES food_instances(food_instance_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            initial_used INTEGER NOT NULL DEFAULT 0 CHECK(initial_used BETWEEN 0 AND 10),
            star_sum INTEGER NOT NULL DEFAULT 0 CHECK(star_sum BETWEEN initial_used AND initial_used*6),
            reward_used INTEGER NOT NULL DEFAULT 0 CHECK(reward_used BETWEEN 0 AND 3),
            stage TEXT NOT NULL CHECK(stage IN ('initial','cook','reward','complete')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        "CREATE UNIQUE INDEX idx_clover_active ON player_clover_chains(player_id) WHERE stage!='complete'",
        """CREATE TABLE player_moon_feasts(
            source_food_instance_id TEXT PRIMARY KEY REFERENCES food_instances(food_instance_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            blocked_start TEXT NOT NULL, blocked_end TEXT NOT NULL,
            target_start TEXT NOT NULL, target_end TEXT NOT NULL,
            discount_start TEXT NOT NULL, discount_end TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0 CHECK(used BETWEEN 0 AND 15),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            CHECK(blocked_start<blocked_end AND blocked_end<=target_start AND target_start<target_end),
            CHECK(discount_start<discount_end)
        )""",
        "CREATE INDEX idx_moon_active ON player_moon_feasts(player_id,target_end)",
        """UPDATE food_instances SET effect_id='clover-feast',effect_params_json='{}'
           WHERE effect_id='window-six-star-resonance' AND state IN ('active','locked-for-trade')""",
        """UPDATE food_instances SET effect_id='moon-feast',effect_params_json='{}'
           WHERE effect_id='catch-window-transfer' AND state IN ('active','locked-for-trade')""",
    )
    + tuple(
        f"""CREATE TRIGGER {table}_scope_{operation} BEFORE {operation.upper()} ON {table}
        WHEN NOT EXISTS(SELECT 1 FROM players p JOIN food_instances f
            ON f.food_instance_id=NEW.source_food_instance_id
            WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id
            AND f.owner_player_id=NEW.player_id AND f.scope_id=NEW.scope_id AND f.rarity=6)
        BEGIN SELECT RAISE(ABORT,'美食状态来源、玩家与群范围不一致'); END"""
        for table in sorted(TABLES)
        for operation in ("insert", "update")
    ),
)
