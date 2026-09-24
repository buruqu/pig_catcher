"""Expand in-flight feast counters without discarding already earned rewards."""

from .model import Migration

MIGRATION_0072 = Migration(
    version=72,
    name="feast-balance-and-three-cooks",
    statements=(
        "ALTER TABLE player_clover_chains RENAME TO player_clover_chains_v71",
        """CREATE TABLE player_clover_chains(
            source_food_instance_id TEXT PRIMARY KEY REFERENCES food_instances(food_instance_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            initial_target INTEGER NOT NULL DEFAULT 7 CHECK(initial_target IN (7,10)),
            initial_used INTEGER NOT NULL DEFAULT 0 CHECK(initial_used BETWEEN 0 AND initial_target),
            star_sum INTEGER NOT NULL DEFAULT 0 CHECK(star_sum BETWEEN initial_used AND initial_used*6),
            cook_used INTEGER NOT NULL DEFAULT 0 CHECK(cook_used BETWEEN 0 AND 3),
            reward_granted INTEGER NOT NULL DEFAULT 0 CHECK(reward_granted IN (0,3,6,9)),
            reward_used INTEGER NOT NULL DEFAULT 0 CHECK(reward_used BETWEEN 0 AND reward_granted),
            stage TEXT NOT NULL CHECK(stage IN ('initial','cook','reward','complete')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """INSERT INTO player_clover_chains(
            source_food_instance_id,player_id,scope_id,initial_target,initial_used,star_sum,
            cook_used,reward_granted,reward_used,stage,created_at,updated_at)
            SELECT source_food_instance_id,player_id,scope_id,10,initial_used,star_sum,
                   CASE WHEN stage IN ('reward','complete') THEN 3 ELSE 0 END,
                   CASE WHEN stage='reward' OR reward_used>0 THEN 3 ELSE 0 END,
                   reward_used,stage,created_at,updated_at
            FROM player_clover_chains_v71""",
        "DROP TABLE player_clover_chains_v71",
        "CREATE UNIQUE INDEX idx_clover_active ON player_clover_chains(player_id) WHERE stage!='complete'",
        """CREATE TRIGGER player_clover_chains_scope_insert BEFORE INSERT ON player_clover_chains
            WHEN NOT EXISTS(SELECT 1 FROM players p JOIN food_instances f
                ON f.food_instance_id=NEW.source_food_instance_id
                WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id
                AND f.owner_player_id=NEW.player_id AND f.scope_id=NEW.scope_id AND f.rarity=6)
            BEGIN SELECT RAISE(ABORT,'美食状态来源、玩家与群范围不一致'); END""",
        """CREATE TRIGGER player_clover_chains_scope_update BEFORE UPDATE ON player_clover_chains
            WHEN NOT EXISTS(SELECT 1 FROM players p JOIN food_instances f
                ON f.food_instance_id=NEW.source_food_instance_id
                WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id
                AND f.owner_player_id=NEW.player_id AND f.scope_id=NEW.scope_id AND f.rarity=6)
            BEGIN SELECT RAISE(ABORT,'美食状态来源、玩家与群范围不一致'); END""",
        "ALTER TABLE player_moon_feasts RENAME TO player_moon_feasts_v71",
        """CREATE TABLE player_moon_feasts(
            source_food_instance_id TEXT PRIMARY KEY REFERENCES food_instances(food_instance_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            blocked_start TEXT NOT NULL, blocked_end TEXT NOT NULL,
            target_start TEXT NOT NULL, target_end TEXT NOT NULL,
            discount_start TEXT NOT NULL, discount_end TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0 CHECK(used BETWEEN 0 AND 18),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            CHECK(blocked_start<blocked_end AND blocked_end<=target_start AND target_start<target_end),
            CHECK(discount_start<discount_end)
        )""",
        """INSERT INTO player_moon_feasts SELECT * FROM player_moon_feasts_v71""",
        "DROP TABLE player_moon_feasts_v71",
        "CREATE INDEX idx_moon_active ON player_moon_feasts(player_id,target_end)",
        """CREATE TRIGGER player_moon_feasts_scope_insert BEFORE INSERT ON player_moon_feasts
            WHEN NOT EXISTS(SELECT 1 FROM players p JOIN food_instances f
                ON f.food_instance_id=NEW.source_food_instance_id
                WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id
                AND f.owner_player_id=NEW.player_id AND f.scope_id=NEW.scope_id AND f.rarity=6)
            BEGIN SELECT RAISE(ABORT,'美食状态来源、玩家与群范围不一致'); END""",
        """CREATE TRIGGER player_moon_feasts_scope_update BEFORE UPDATE ON player_moon_feasts
            WHEN NOT EXISTS(SELECT 1 FROM players p JOIN food_instances f
                ON f.food_instance_id=NEW.source_food_instance_id
                WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id
                AND f.owner_player_id=NEW.player_id AND f.scope_id=NEW.scope_id AND f.rarity=6)
            BEGIN SELECT RAISE(ABORT,'美食状态来源、玩家与群范围不一致'); END""",
        """UPDATE player_food_effects SET granted_uses=3
            WHERE effect_id='clover-six-star-cook' AND consumed_uses=0 AND granted_uses=1
            AND source_food_instance_id IN (
                SELECT source_food_instance_id FROM player_clover_chains WHERE stage='cook')""",
    ),
)
