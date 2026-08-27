"""Schema 38：巡演、档期账本、训练贡献、站点快照和可解除的乐队保护。"""

from .model import Migration

MIGRATION_0038 = Migration(
    version=38,
    name="tour-bands-training-stages-joint-performances",
    statements=(
        """CREATE TABLE tour_profiles(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '粉', emblem TEXT NOT NULL DEFAULT '星星', costume TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
            tickets INTEGER NOT NULL DEFAULT 0 CHECK(tickets BETWEEN 0 AND 7),
            last_ticket_day TEXT NOT NULL, fans INTEGER NOT NULL DEFAULT 0 CHECK(fans>=0),
            equipment INTEGER NOT NULL DEFAULT 0 CHECK(equipment BETWEEN 0 AND 5),
            active_slot INTEGER NOT NULL DEFAULT 1 CHECK(active_slot BETWEEN 1 AND 3),
            guest_id TEXT REFERENCES pig_instances(pig_instance_id),
            plans_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE tour_ticket_ledger(
            entry_key TEXT PRIMARY KEY, player_id TEXT NOT NULL REFERENCES players(player_id),
            delta INTEGER NOT NULL CHECK(delta!=0), balance INTEGER NOT NULL CHECK(balance BETWEEN 0 AND 7),
            reason TEXT NOT NULL, source_id TEXT NOT NULL, occurred_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_tour_ticket_player ON tour_ticket_ledger(player_id,occurred_at)",
        """CREATE TABLE tour_rosters(
            player_id TEXT NOT NULL REFERENCES players(player_id), slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 3),
            member_ids_json TEXT NOT NULL, captain_id TEXT NOT NULL DEFAULT '', center_id TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(player_id,slot)
        )""",
        """CREATE TABLE tour_proficiency(
            pig_instance_id TEXT PRIMARY KEY REFERENCES pig_instances(pig_instance_id),
            experience INTEGER NOT NULL DEFAULT 0 CHECK(experience>=0), branch TEXT NOT NULL DEFAULT ''
        )""",
        """CREATE TABLE tour_contributions(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            pig_instance_id TEXT NOT NULL REFERENCES pig_instances(pig_instance_id),
            natural_exp INTEGER NOT NULL DEFAULT 0 CHECK(natural_exp>=0),
            practice_exp INTEGER NOT NULL DEFAULT 0 CHECK(practice_exp>=0),
            stages INTEGER NOT NULL DEFAULT 0 CHECK(stages>=0), PRIMARY KEY(player_id,pig_instance_id)
        )""",
        """CREATE TABLE tour_song_progress(
            player_id TEXT NOT NULL REFERENCES players(player_id), song_id TEXT NOT NULL,
            plays INTEGER NOT NULL DEFAULT 0 CHECK(plays>=0), PRIMARY KEY(player_id,song_id)
        )""",
        """CREATE TABLE tour_protections(
            pig_instance_id TEXT PRIMARY KEY REFERENCES pig_instances(pig_instance_id),
            player_id TEXT NOT NULL REFERENCES players(player_id), scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            protected INTEGER NOT NULL DEFAULT 1 CHECK(protected IN (0,1))
        )""",
        "CREATE INDEX idx_tour_protected_player ON tour_protections(player_id) WHERE protected=1",
        """CREATE TABLE tour_practice_days(
            pig_instance_id TEXT NOT NULL REFERENCES pig_instances(pig_instance_id), practice_day TEXT NOT NULL,
            player_id TEXT NOT NULL REFERENCES players(player_id), entry_key TEXT NOT NULL UNIQUE,
            PRIMARY KEY(pig_instance_id,practice_day)
        )""",
        """CREATE TABLE tour_runs(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE,
            player_id TEXT NOT NULL REFERENCES players(player_id), scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed','abandoned')),
            stage_count INTEGER NOT NULL DEFAULT 0 CHECK(stage_count BETWEEN 0 AND 3),
            definition_version INTEGER NOT NULL, plans_json TEXT NOT NULL, random_seed TEXT NOT NULL,
            initial_roster_json TEXT NOT NULL, started_ms INTEGER NOT NULL, completed_ms INTEGER,
            summary_json TEXT NOT NULL DEFAULT '{}', joint_id TEXT NOT NULL DEFAULT '',
            CHECK(status!='completed' OR stage_count=3)
        )""",
        "CREATE UNIQUE INDEX idx_tour_active_player ON tour_runs(player_id) WHERE status='active'",
        "CREATE INDEX idx_tour_history ON tour_runs(player_id,sequence DESC)",
        """CREATE TABLE tour_stages(
            run_id TEXT NOT NULL REFERENCES tour_runs(run_id),
            stage_number INTEGER NOT NULL CHECK(stage_number BETWEEN 1 AND 3),
            player_id TEXT NOT NULL REFERENCES players(player_id), scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            snapshot_json TEXT NOT NULL, occurred_ms INTEGER NOT NULL, PRIMARY KEY(run_id,stage_number)
        )""",
        """CREATE TABLE tour_tools(
            player_id TEXT NOT NULL REFERENCES players(player_id), tool_id TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0), PRIMARY KEY(player_id,tool_id)
        )""",
        """CREATE TABLE tour_collections(
            player_id TEXT NOT NULL REFERENCES players(player_id), collection_key TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, detail_json TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES tour_runs(run_id), acquired_at TEXT NOT NULL,
            PRIMARY KEY(player_id,collection_key)
        )""",
        "CREATE INDEX idx_tour_collections_page ON tour_collections(player_id,acquired_at DESC,collection_key)",
        """CREATE TABLE tour_pending(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id), operation TEXT NOT NULL,
            payload_json TEXT NOT NULL, expires_ms INTEGER NOT NULL
        )""",
        """CREATE TABLE tour_joint_invites(
            joint_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            inviter_id TEXT NOT NULL REFERENCES players(player_id),
            recipient_id TEXT NOT NULL REFERENCES players(player_id),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','declined','cancelled','expired')),
            payload_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
            expires_ms INTEGER NOT NULL, created_at TEXT NOT NULL, CHECK(inviter_id!=recipient_id)
        )""",
        """CREATE TABLE tour_joint_reservations(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            joint_id TEXT NOT NULL REFERENCES tour_joint_invites(joint_id)
        )""",
        "CREATE INDEX idx_tour_joint_reservation ON tour_joint_reservations(joint_id)",
        """CREATE TRIGGER tour_protection_owner_insert BEFORE INSERT ON tour_protections
        WHEN NEW.protected=1 AND NOT EXISTS(SELECT 1 FROM pig_instances p WHERE p.pig_instance_id=NEW.pig_instance_id
        AND p.owner_player_id=NEW.player_id AND p.scope_id=NEW.scope_id
        AND p.state='active' AND p.locked_trade_id IS NULL)
        BEGIN SELECT RAISE(ABORT,'乐队保护对象归属不符或不可用'); END""",
        """CREATE TRIGGER tour_protection_owner_update BEFORE UPDATE ON tour_protections
        WHEN NEW.protected=1 AND NOT EXISTS(SELECT 1 FROM pig_instances p WHERE p.pig_instance_id=NEW.pig_instance_id
        AND p.owner_player_id=NEW.player_id AND p.scope_id=NEW.scope_id
        AND p.state='active' AND p.locked_trade_id IS NULL)
        BEGIN SELECT RAISE(ABORT,'乐队保护对象归属不符或不可用'); END""",
        """CREATE TRIGGER tour_protected_pig_no_dispose
        BEFORE UPDATE OF state,owner_player_id,scope_id,locked_trade_id ON pig_instances
        WHEN EXISTS(SELECT 1 FROM tour_protections p WHERE p.pig_instance_id=OLD.pig_instance_id AND p.protected=1)
        AND (NEW.state!=OLD.state OR NEW.owner_player_id!=OLD.owner_player_id OR NEW.scope_id!=OLD.scope_id
        OR COALESCE(NEW.locked_trade_id,'')!=COALESCE(OLD.locked_trade_id,''))
        BEGIN SELECT RAISE(ABORT,'猪猪受乐队保护，请先明确解除保护'); END""",
        """CREATE TRIGGER tour_protected_pig_no_delete BEFORE DELETE ON pig_instances
        WHEN EXISTS(SELECT 1 FROM tour_protections p WHERE p.pig_instance_id=OLD.pig_instance_id AND p.protected=1)
        BEGIN SELECT RAISE(ABORT,'猪猪受乐队保护，不能删除'); END""",
        """CREATE TRIGGER tour_trained_pig_new_owner AFTER UPDATE OF owner_player_id ON pig_instances
        WHEN NEW.owner_player_id!=OLD.owner_player_id AND NEW.state='active'
        AND EXISTS(SELECT 1 FROM tour_proficiency t WHERE t.pig_instance_id=NEW.pig_instance_id AND t.experience>0)
        BEGIN INSERT INTO tour_protections VALUES(NEW.pig_instance_id,NEW.owner_player_id,NEW.scope_id,1)
        ON CONFLICT(pig_instance_id) DO UPDATE SET player_id=excluded.player_id,
        scope_id=excluded.scope_id,protected=1; END""",
        """CREATE TRIGGER tour_profile_scope_insert BEFORE INSERT ON tour_profiles
        WHEN NOT EXISTS(SELECT 1 FROM players p WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id)
        BEGIN SELECT RAISE(ABORT,'乐队群范围不符'); END""",
        """CREATE TRIGGER tour_run_scope_insert BEFORE INSERT ON tour_runs
        WHEN NOT EXISTS(SELECT 1 FROM players p WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id)
        BEGIN SELECT RAISE(ABORT,'巡演群范围不符'); END""",
        """CREATE TRIGGER tour_stage_owner_insert BEFORE INSERT ON tour_stages
        WHEN NOT EXISTS(SELECT 1 FROM tour_runs r WHERE r.run_id=NEW.run_id AND r.player_id=NEW.player_id
        AND r.scope_id=NEW.scope_id AND r.status='active' AND NEW.stage_number=r.stage_count+1)
        BEGIN SELECT RAISE(ABORT,'巡演站点归属或顺序不符'); END""",
        """CREATE TRIGGER tour_joint_scope_insert BEFORE INSERT ON tour_joint_invites
        WHEN NOT EXISTS(SELECT 1 FROM players p JOIN players q ON q.player_id=NEW.recipient_id
        WHERE p.player_id=NEW.inviter_id AND p.scope_id=NEW.scope_id AND q.scope_id=NEW.scope_id)
        BEGIN SELECT RAISE(ABORT,'联演必须属于同一群'); END""",
        """CREATE TRIGGER tour_finished_run_no_update BEFORE UPDATE ON tour_runs WHEN OLD.status!='active'
        BEGIN SELECT RAISE(ABORT,'已结束巡演不可重写'); END""",
        *(
            f"CREATE TRIGGER {table}_scope_update BEFORE UPDATE OF player_id,scope_id ON {table} "
            "WHEN NOT EXISTS(SELECT 1 FROM players p WHERE p.player_id=NEW.player_id AND p.scope_id=NEW.scope_id) "
            "BEGIN SELECT RAISE(ABORT,'巡演群范围不符'); END"
            for table in ("tour_profiles", "tour_runs")
        ),
        *(
            f"CREATE TRIGGER tour_roster_owner_{operation.lower()} BEFORE {operation} ON tour_rosters "
            "WHEN json_valid(NEW.member_ids_json)=0 OR json_type(NEW.member_ids_json)!='array' "
            "OR json_array_length(NEW.member_ids_json)>5 "
            "OR EXISTS(SELECT 1 FROM json_each(NEW.member_ids_json) m "
            "LEFT JOIN pig_instances p ON p.pig_instance_id=m.value "
            "WHERE p.pig_instance_id IS NULL OR p.owner_player_id!=NEW.player_id OR p.state!='active') "
            "BEGIN SELECT RAISE(ABORT,'阵容实例不属于本人或阵容数据不合法'); END"
            for operation in ("INSERT", "UPDATE")
        ),
        """CREATE TRIGGER tour_joint_reservation_owner BEFORE INSERT ON tour_joint_reservations
        WHEN NOT EXISTS(SELECT 1 FROM tour_joint_invites i WHERE i.joint_id=NEW.joint_id AND i.status='pending'
            AND NEW.player_id IN(i.inviter_id,i.recipient_id))
        BEGIN SELECT RAISE(ABORT,'联演预约不属于参与者'); END""",
        *(
            f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'巡演账本与结算快照不可改写或删除'); END"
            for table in ("tour_ticket_ledger", "tour_stages", "tour_collections")
            for operation in ("UPDATE", "DELETE")
        ),
    ),
)
