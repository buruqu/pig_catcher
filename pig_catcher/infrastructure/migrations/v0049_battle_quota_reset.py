"""Schema 49：可审计、可重复重置的每日对战双角色额度。"""

from .model import Migration

TABLES = {"battle_daily_quota_state", "battle_daily_reuses"}
GUARDS = {
    "idx_battle_daily_reuses_battle",
    "battle_quota_state_scope_insert",
    "battle_quota_state_advance_only",
    "battle_quota_state_no_delete",
    "battle_reuse_scope_guard",
    "battle_reuse_participant_guard",
    "battle_reuse_generation_guard",
    "battle_daily_reuses_no_update",
    "battle_daily_reuses_no_delete",
}

MIGRATION_0049 = Migration(
    version=49,
    name="battle-daily-quota-reset-generations",
    statements=(
        """
        CREATE TABLE battle_daily_quota_state(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            day TEXT NOT NULL CHECK(length(day) = 10),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            generation INTEGER NOT NULL CHECK(generation >= 1),
            reset_audit_id TEXT NOT NULL REFERENCES audit_events(audit_event_id),
            updated_ms INTEGER NOT NULL CHECK(updated_ms > 0),
            PRIMARY KEY(player_id, day)
        )
        """,
        """
        CREATE TABLE battle_daily_reuses(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            day TEXT NOT NULL CHECK(length(day) = 10),
            role TEXT NOT NULL CHECK(role IN('initiator','opponent')),
            generation INTEGER NOT NULL CHECK(generation >= 1),
            battle_id TEXT NOT NULL REFERENCES battle_matches(battle_id),
            occurred_ms INTEGER NOT NULL CHECK(occurred_ms > 0),
            PRIMARY KEY(player_id, day, role, generation),
            FOREIGN KEY(player_id, day)
                REFERENCES battle_daily_quota_state(player_id, day)
        )
        """,
        """
        CREATE INDEX idx_battle_daily_reuses_battle
        ON battle_daily_reuses(battle_id, occurred_ms)
        """,
        """
        CREATE TRIGGER battle_quota_state_scope_insert
        BEFORE INSERT ON battle_daily_quota_state
        WHEN NOT EXISTS(
            SELECT 1 FROM players p
            WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id
        )
        BEGIN SELECT RAISE(ABORT,'对战额度重置玩家与群范围不一致'); END
        """,
        """
        CREATE TRIGGER battle_quota_state_advance_only
        BEFORE UPDATE ON battle_daily_quota_state
        WHEN NEW.player_id!=OLD.player_id OR NEW.day!=OLD.day OR NEW.scope_id!=OLD.scope_id
          OR NEW.generation!=OLD.generation+1 OR NEW.reset_audit_id=OLD.reset_audit_id
          OR NEW.updated_ms<OLD.updated_ms
        BEGIN SELECT RAISE(ABORT,'对战额度重置代次只能逐次前进'); END
        """,
        """
        CREATE TRIGGER battle_quota_state_no_delete
        BEFORE DELETE ON battle_daily_quota_state
        BEGIN SELECT RAISE(ABORT,'对战额度重置状态不可删除'); END
        """,
        """
        CREATE TRIGGER battle_reuse_scope_guard
        BEFORE INSERT ON battle_daily_reuses
        WHEN NOT EXISTS(
            SELECT 1 FROM players p
            WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id
        )
        BEGIN SELECT RAISE(ABORT,'重置后的对战额度与群范围不一致'); END
        """,
        """
        CREATE TRIGGER battle_reuse_participant_guard
        BEFORE INSERT ON battle_daily_reuses
        WHEN NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND b.status='active'
              AND b.scope_id=NEW.scope_id AND b.accepted_day=NEW.day
              AND ((NEW.role='initiator' AND NEW.player_id=b.initiator_id)
                OR (NEW.role='opponent' AND NEW.player_id=b.opponent_id))
        )
        BEGIN SELECT RAISE(ABORT,'重置后的对战额度与参与者不符'); END
        """,
        """
        CREATE TRIGGER battle_reuse_generation_guard
        BEFORE INSERT ON battle_daily_reuses
        WHEN NOT EXISTS(
            SELECT 1 FROM battle_daily_quota_state q
            WHERE q.player_id=NEW.player_id AND q.day=NEW.day
              AND q.scope_id=NEW.scope_id AND q.generation=NEW.generation
        )
        BEGIN SELECT RAISE(ABORT,'重置后的对战额度代次不匹配'); END
        """,
        """
        CREATE TRIGGER battle_daily_reuses_no_update
        BEFORE UPDATE ON battle_daily_reuses
        BEGIN SELECT RAISE(ABORT,'重置后的对战额度账本不可改写'); END
        """,
        """
        CREATE TRIGGER battle_daily_reuses_no_delete
        BEFORE DELETE ON battle_daily_reuses
        BEGIN SELECT RAISE(ABORT,'重置后的对战额度账本不可删除'); END
        """,
    ),
)
