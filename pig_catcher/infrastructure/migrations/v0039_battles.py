"""Schema39：对战独立状态、整数快照、保护、角色额度与自然结局战利品。"""

from .model import Migration

TABLES = (
    "battle_profiles",
    "battle_training",
    "battle_protections",
    "battle_upgrades",
    "battle_tools",
    "battle_tool_ledger",
    "battle_pending",
    "battle_matches",
    "battle_daily_uses",
    "battle_rounds",
    "battle_moves",
    "battle_loot",
    "battle_loot_deliveries",
)
IMMUTABLE = (
    "battle_upgrades",
    "battle_tool_ledger",
    "battle_daily_uses",
    "battle_rounds",
    "battle_moves",
    "battle_loot_deliveries",
)

MIGRATION_0039 = Migration(
    version=39,
    name="battle-wheels-training-state-and-loot",
    statements=(
        """CREATE TABLE battle_profiles(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            pig_instance_id TEXT REFERENCES pig_instances(pig_instance_id),
            tool_id TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
            last_invite_ms INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE battle_training(
            pig_instance_id TEXT PRIMARY KEY REFERENCES pig_instances(pig_instance_id),
            level INTEGER NOT NULL DEFAULT 0 CHECK(level BETWEEN 0 AND 5))""",
        """CREATE TABLE battle_protections(
            pig_instance_id TEXT PRIMARY KEY REFERENCES pig_instances(pig_instance_id),
            player_id TEXT NOT NULL REFERENCES players(player_id), scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            protected INTEGER NOT NULL CHECK(protected IN(0,1)))""",
        """CREATE TABLE battle_upgrades(
            entry_key TEXT PRIMARY KEY, player_id TEXT NOT NULL REFERENCES players(player_id),
            pig_instance_id TEXT NOT NULL REFERENCES pig_instances(pig_instance_id),
            from_level INTEGER NOT NULL, to_level INTEGER NOT NULL CHECK(to_level BETWEEN 1 AND 5),
            costs_json TEXT NOT NULL, occurred_ms INTEGER NOT NULL, CHECK(to_level=from_level+1),
            UNIQUE(pig_instance_id,to_level))""",
        """CREATE TABLE battle_tools(
            player_id TEXT NOT NULL REFERENCES players(player_id), tool_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity>=0), PRIMARY KEY(player_id,tool_id))""",
        """CREATE TABLE battle_tool_ledger(
            entry_key TEXT PRIMARY KEY, player_id TEXT NOT NULL REFERENCES players(player_id), tool_id TEXT NOT NULL,
            delta INTEGER NOT NULL CHECK(delta!=0), balance INTEGER NOT NULL CHECK(balance>=0),
            reason TEXT NOT NULL, source_id TEXT NOT NULL, occurred_ms INTEGER NOT NULL)""",
        """CREATE TABLE battle_pending(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id), operation TEXT NOT NULL,
            payload_json TEXT NOT NULL, expires_ms INTEGER NOT NULL)""",
        """CREATE TABLE battle_matches(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, battle_id TEXT NOT NULL UNIQUE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            initiator_id TEXT NOT NULL REFERENCES players(player_id),
            opponent_id TEXT NOT NULL REFERENCES players(player_id),
            status TEXT NOT NULL
                CHECK(status IN('pending','active','completed','declined','cancelled','expired','surrendered')),
            definition_version INTEGER NOT NULL, random_seed TEXT NOT NULL, state_json TEXT NOT NULL,
            invitation_json TEXT NOT NULL, accepted_day TEXT NOT NULL DEFAULT '',
            expires_ms INTEGER NOT NULL, created_ms INTEGER NOT NULL, finished_ms INTEGER,
            winner_id TEXT REFERENCES players(player_id), CHECK(initiator_id!=opponent_id))""",
        "CREATE UNIQUE INDEX idx_battle_active_scope ON battle_matches(scope_id) WHERE status IN('pending','active')",
        "CREATE INDEX idx_battle_initiator_history ON battle_matches(initiator_id,sequence DESC)",
        "CREATE INDEX idx_battle_opponent_history ON battle_matches(opponent_id,sequence DESC)",
        "CREATE INDEX idx_battle_expiry ON battle_matches(expires_ms) WHERE status IN('pending','active')",
        "CREATE INDEX idx_battle_protected_player ON battle_protections(player_id) WHERE protected=1",
        """CREATE TABLE battle_daily_uses(
            player_id TEXT NOT NULL REFERENCES players(player_id), day TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN('initiator','opponent')),
            battle_id TEXT NOT NULL REFERENCES battle_matches(battle_id),
            PRIMARY KEY(player_id,day,role))""",
        """CREATE TABLE battle_rounds(
            battle_id TEXT NOT NULL REFERENCES battle_matches(battle_id),
            round_number INTEGER NOT NULL CHECK(round_number>0),
            result_json TEXT NOT NULL, occurred_ms INTEGER NOT NULL, PRIMARY KEY(battle_id,round_number))""",
        """CREATE TABLE battle_moves(
            battle_id TEXT NOT NULL REFERENCES battle_matches(battle_id),
            round_number INTEGER NOT NULL CHECK(round_number>0),
            side INTEGER NOT NULL CHECK(side IN(0,1)), ordinal INTEGER NOT NULL CHECK(ordinal>0),
            event_json TEXT NOT NULL, occurred_ms INTEGER NOT NULL,
            PRIMARY KEY(battle_id,round_number,side,ordinal))""",
        """CREATE TABLE battle_loot(
            battle_id TEXT PRIMARY KEY REFERENCES battle_matches(battle_id),
            actor_id TEXT NOT NULL REFERENCES players(player_id),
            recipient_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            used INTEGER NOT NULL DEFAULT 0 CHECK(used BETWEEN 0 AND 5),
            created_ms INTEGER NOT NULL, CHECK(actor_id!=recipient_id))""",
        "CREATE INDEX idx_battle_loot_actor ON battle_loot(actor_id,created_ms,battle_id) WHERE used<5",
        """CREATE TABLE battle_loot_deliveries(
            battle_id TEXT NOT NULL REFERENCES battle_loot(battle_id),
            ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 5),
            pig_instance_id TEXT NOT NULL UNIQUE REFERENCES pig_instances(pig_instance_id),
            receipt_key TEXT NOT NULL UNIQUE, snapshot_json TEXT NOT NULL, occurred_ms INTEGER NOT NULL,
            PRIMARY KEY(battle_id,ordinal))""",
        *(
            f"CREATE TRIGGER battle_profile_owner_{op.lower()} BEFORE {op} ON battle_profiles "
            "WHEN NEW.pig_instance_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM pig_instances p "
            "WHERE p.pig_instance_id=NEW.pig_instance_id AND p.owner_player_id=NEW.player_id AND p.state='active') "
            "BEGIN SELECT RAISE(ABORT,'战斗猪归属不符'); END"
            for op in ("INSERT", "UPDATE")
        ),
        *(
            f"CREATE TRIGGER battle_protection_owner_{op.lower()} BEFORE {op} ON battle_protections "
            "WHEN NEW.protected=1 AND NOT EXISTS(SELECT 1 FROM pig_instances p "
            "WHERE p.pig_instance_id=NEW.pig_instance_id AND p.owner_player_id=NEW.player_id "
            "AND p.scope_id=NEW.scope_id AND p.state='active' AND p.locked_trade_id IS NULL) "
            "BEGIN SELECT RAISE(ABORT,'战斗保护对象归属不符或已锁定'); END"
            for op in ("INSERT", "UPDATE")
        ),
        """CREATE TRIGGER battle_protected_pig_no_dispose
        BEFORE UPDATE OF state,owner_player_id,scope_id,locked_trade_id ON pig_instances
        WHEN EXISTS(SELECT 1 FROM battle_protections b WHERE b.pig_instance_id=OLD.pig_instance_id AND b.protected=1)
        AND (NEW.state!=OLD.state OR NEW.owner_player_id!=OLD.owner_player_id OR NEW.scope_id!=OLD.scope_id
        OR COALESCE(NEW.locked_trade_id,'')!=COALESCE(OLD.locked_trade_id,''))
        BEGIN SELECT RAISE(ABORT,'猪猪受战斗保护，请先明确解除保护'); END""",
        """CREATE TRIGGER battle_protected_pig_no_delete BEFORE DELETE ON pig_instances
        WHEN EXISTS(SELECT 1 FROM battle_protections b WHERE b.pig_instance_id=OLD.pig_instance_id AND b.protected=1)
        BEGIN SELECT RAISE(ABORT,'猪猪受战斗保护，不能删除'); END""",
        """CREATE TRIGGER battle_trained_pig_new_owner AFTER UPDATE OF owner_player_id ON pig_instances
        WHEN NEW.owner_player_id!=OLD.owner_player_id AND NEW.state='active'
        AND EXISTS(SELECT 1 FROM battle_training b WHERE b.pig_instance_id=NEW.pig_instance_id AND b.level>0)
        BEGIN INSERT INTO battle_protections VALUES(NEW.pig_instance_id,NEW.owner_player_id,NEW.scope_id,1)
        ON CONFLICT(pig_instance_id) DO UPDATE SET player_id=excluded.player_id,
        scope_id=excluded.scope_id,protected=1; END""",
        *(
            f"CREATE TRIGGER battle_match_scope_{op.lower()} BEFORE {op} ON battle_matches "
            "WHEN NOT EXISTS(SELECT 1 FROM players p JOIN players q ON q.player_id=NEW.opponent_id "
            "WHERE p.player_id=NEW.initiator_id AND p.scope_id=NEW.scope_id AND q.scope_id=NEW.scope_id) "
            "BEGIN SELECT RAISE(ABORT,'对战必须属于同一群'); END"
            for op in ("INSERT", "UPDATE")
        ),
        """CREATE TRIGGER battle_finished_no_update BEFORE UPDATE ON battle_matches
        WHEN OLD.status NOT IN('pending','active') BEGIN SELECT RAISE(ABORT,'已结束对战不可重写'); END""",
        """CREATE TRIGGER battle_quota_participant BEFORE INSERT ON battle_daily_uses
        WHEN NOT EXISTS(SELECT 1 FROM battle_matches b WHERE b.battle_id=NEW.battle_id AND b.status='active'
        AND b.accepted_day=NEW.day AND ((NEW.role='initiator' AND NEW.player_id=b.initiator_id)
        OR (NEW.role='opponent' AND NEW.player_id=b.opponent_id)))
        BEGIN SELECT RAISE(ABORT,'对战额度与参与者不符'); END""",
        """CREATE TRIGGER battle_loot_natural_end BEFORE INSERT ON battle_loot
        WHEN NOT EXISTS(SELECT 1 FROM battle_matches b WHERE b.battle_id=NEW.battle_id AND b.status='completed'
        AND b.scope_id=NEW.scope_id AND b.winner_id=NEW.recipient_id
        AND NEW.actor_id IN(b.initiator_id,b.opponent_id) AND NEW.actor_id!=b.winner_id)
        BEGIN SELECT RAISE(ABORT,'仅自然力竭结局生成五次战利品'); END""",
        """CREATE TRIGGER battle_loot_only_advance BEFORE UPDATE ON battle_loot
        WHEN NEW.battle_id!=OLD.battle_id OR NEW.actor_id!=OLD.actor_id OR NEW.recipient_id!=OLD.recipient_id
        OR NEW.scope_id!=OLD.scope_id OR NEW.created_ms!=OLD.created_ms OR NEW.used!=OLD.used+1
        BEGIN SELECT RAISE(ABORT,'战利品只能逐次结算'); END""",
        """CREATE TRIGGER battle_delivery_owner BEFORE INSERT ON battle_loot_deliveries
        WHEN NOT EXISTS(SELECT 1 FROM battle_loot l JOIN pig_instances p ON p.pig_instance_id=NEW.pig_instance_id
        WHERE l.battle_id=NEW.battle_id AND l.used+1=NEW.ordinal
        AND p.owner_player_id=l.recipient_id AND p.scope_id=l.scope_id)
        BEGIN SELECT RAISE(ABORT,'战利品归属或顺序不符'); END""",
        *(
            f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'对战账本不可修改或删除'); END"
            for table in IMMUTABLE
            for op in ("UPDATE", "DELETE")
        ),
    ),
)

# 与初始化完整性校验共用同一清单，漏部署索引/触发器时明确拒绝启动。
GUARDS = tuple(sql.split()[2] for sql in MIGRATION_0039.statements if sql.startswith("CREATE TRIGGER")) + (
    "idx_battle_active_scope",
    "idx_battle_initiator_history",
    "idx_battle_opponent_history",
    "idx_battle_expiry",
    "idx_battle_protected_player",
    "idx_battle_loot_actor",
)
