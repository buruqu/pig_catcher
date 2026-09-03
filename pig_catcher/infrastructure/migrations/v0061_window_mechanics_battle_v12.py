"""Schema 61：跨时段额度平移、三月七时段共鸣与 Battle v12。"""

from .model import Migration

TABLES = (
    "player_catch_window_transfers",
    "player_window_resonance",
)

GUARDS = {
    "idx_catch_window_transfer_target",
    "idx_window_resonance_active",
    "battle_loot_total_insert",
}

MIGRATION_0061 = Migration(
    version=61,
    name="window-mechanics-and-battle-rule-v12",
    statements=(
        """
        CREATE TABLE player_catch_window_transfers(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            source_food_instance_id TEXT NOT NULL UNIQUE REFERENCES food_instances(food_instance_id),
            blocked_window_start TEXT NOT NULL,
            blocked_window_end TEXT NOT NULL,
            target_window_start TEXT NOT NULL,
            target_window_end TEXT NOT NULL,
            transferred_uses INTEGER NOT NULL CHECK(transferred_uses >= 0),
            fixed_weights_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(blocked_window_start < blocked_window_end),
            CHECK(blocked_window_end <= target_window_start),
            CHECK(target_window_start < target_window_end)
        )
        """,
        """
        CREATE INDEX idx_catch_window_transfer_target
        ON player_catch_window_transfers(scope_id, target_window_start, target_window_end)
        """,
        """
        CREATE TABLE player_window_resonance(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            source_food_instance_id TEXT NOT NULL UNIQUE REFERENCES food_instances(food_instance_id),
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            cook_bonus_basis_points INTEGER NOT NULL DEFAULT 0 CHECK(cook_bonus_basis_points >= 0),
            catch_bonus_basis_points INTEGER NOT NULL DEFAULT 0 CHECK(catch_bonus_basis_points >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(window_start < window_end)
        )
        """,
        """
        CREATE INDEX idx_window_resonance_active
        ON player_window_resonance(scope_id, window_start, window_end)
        """,
        "DROP TRIGGER battle_loot_total_insert",
        """
        CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND (
                (b.definition_version=1 AND NEW.total_uses=5)
                OR (b.definition_version IN (2,3,4,5,6,7,8,9,10,11,12) AND NEW.total_uses=3)
            )
        )
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END
        """,
    ),
)
