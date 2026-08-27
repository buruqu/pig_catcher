"""Schema 37：派遣、可复用材料账本、统一占用与持久活动事实。"""

from .model import Migration

MIGRATION_0037 = Migration(
    version=37,
    name="dispatch-materials-occupancies-and-facts",
    statements=(
        """
        CREATE TABLE material_balances(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            material_id TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            remainder_units INTEGER NOT NULL DEFAULT 0 CHECK(remainder_units BETWEEN 0 AND 9999999),
            PRIMARY KEY(player_id, material_id)
        )
        """,
        """
        CREATE TABLE material_ledger(
            entry_key TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            material_id TEXT NOT NULL,
            delta_units INTEGER NOT NULL CHECK(delta_units != 0),
            balance_units INTEGER NOT NULL CHECK(balance_units >= 0),
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_material_ledger_player_source ON material_ledger(player_id, source_kind, occurred_at)",
        """CREATE TRIGGER material_ledger_no_update BEFORE UPDATE ON material_ledger
        BEGIN SELECT RAISE(ABORT, '材料账本不可改写'); END""",
        """CREATE TRIGGER material_ledger_no_delete BEFORE DELETE ON material_ledger
        BEGIN SELECT RAISE(ABORT, '材料账本不可删除'); END""",
        """
        CREATE TABLE asset_occupancies(
            pig_instance_id TEXT PRIMARY KEY REFERENCES pig_instances(pig_instance_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            purpose TEXT NOT NULL CHECK(purpose IN ('dispatch', 'tour', 'battle')),
            activity_id TEXT NOT NULL,
            busy_until_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_asset_occupancy_activity ON asset_occupancies(purpose, activity_id)",
        """
        CREATE TRIGGER occupancy_validate_owner BEFORE INSERT ON asset_occupancies
        WHEN NOT EXISTS(SELECT 1 FROM pig_instances p WHERE p.pig_instance_id = NEW.pig_instance_id
          AND p.owner_player_id = NEW.player_id AND p.scope_id = NEW.scope_id
          AND p.state = 'active' AND p.locked_trade_id IS NULL)
        BEGIN SELECT RAISE(ABORT, '占用对象已不属于当前玩家或已锁定'); END
        """,
        """
        CREATE TRIGGER occupancy_validate_update BEFORE UPDATE ON asset_occupancies
        WHEN NOT EXISTS(SELECT 1 FROM pig_instances p WHERE p.pig_instance_id = NEW.pig_instance_id
          AND p.owner_player_id = NEW.player_id AND p.scope_id = NEW.scope_id
          AND p.state = 'active' AND p.locked_trade_id IS NULL)
        BEGIN SELECT RAISE(ABORT, '占用对象已不属于当前玩家或已锁定'); END
        """,
        """
        CREATE TRIGGER occupied_pig_no_dispose
        BEFORE UPDATE OF state, owner_player_id, locked_trade_id, scope_id ON pig_instances
        WHEN EXISTS(SELECT 1 FROM asset_occupancies o WHERE o.pig_instance_id = OLD.pig_instance_id)
          AND (NEW.state != OLD.state OR NEW.owner_player_id != OLD.owner_player_id OR NEW.scope_id != OLD.scope_id
               OR COALESCE(NEW.locked_trade_id,'') != COALESCE(OLD.locked_trade_id,''))
        BEGIN SELECT RAISE(ABORT, '猪猪正在活动中，不能消耗或转让'); END
        """,
        """CREATE TRIGGER occupied_pig_no_delete BEFORE DELETE ON pig_instances
        WHEN EXISTS(SELECT 1 FROM asset_occupancies o WHERE o.pig_instance_id = OLD.pig_instance_id)
        BEGIN SELECT RAISE(ABORT, '猪猪正在活动中，不能删除'); END""",
        """
        CREATE TABLE dispatch_profiles(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            effective_seconds INTEGER NOT NULL DEFAULT 0 CHECK(effective_seconds >= 0),
            covered_until_ms INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE dispatch_teams(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 3),
            member_ids_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(player_id, slot)
        )
        """,
        """
        CREATE TABLE dispatch_trips(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id TEXT NOT NULL UNIQUE,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 3),
            starts_ms INTEGER NOT NULL,
            ends_ms INTEGER NOT NULL,
            processed_blocks INTEGER NOT NULL DEFAULT 0 CHECK(processed_blocks BETWEEN 0 AND 6),
            status TEXT NOT NULL DEFAULT 'traveling' CHECK(status IN ('traveling','completed','recalled')),
            snapshot_json TEXT NOT NULL,
            progress_json TEXT NOT NULL,
            random_seed TEXT NOT NULL,
            settled_ms INTEGER,
            viewed INTEGER NOT NULL DEFAULT 0 CHECK(viewed IN (0,1)),
            CHECK(ends_ms > starts_ms)
        )
        """,
        "CREATE UNIQUE INDEX idx_dispatch_active_team ON dispatch_trips(player_id,slot) WHERE status='traveling'",
        "CREATE INDEX idx_dispatch_player_history ON dispatch_trips(player_id,status,sequence DESC)",
        """CREATE INDEX idx_dispatch_unread_returns ON dispatch_trips(player_id,sequence)
        WHERE status!='traveling' AND viewed=0""",
        "CREATE INDEX idx_dispatch_due ON dispatch_trips(ends_ms,player_id) WHERE status='traveling'",
        """
        CREATE TABLE dispatch_route_progress(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            region_id TEXT NOT NULL,
            exploration_tenths INTEGER NOT NULL DEFAULT 0 CHECK(exploration_tenths BETWEEN 0 AND 9),
            misses INTEGER NOT NULL DEFAULT 0 CHECK(misses BETWEEN 0 AND 9),
            PRIMARY KEY(player_id,region_id)
        )
        """,
        """CREATE TABLE dispatch_proficiency(
            pig_instance_id TEXT PRIMARY KEY REFERENCES pig_instances(pig_instance_id),
            hours INTEGER NOT NULL DEFAULT 0 CHECK(hours >= 0)
        )""",
        """CREATE TABLE dispatch_contributions(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            pig_instance_id TEXT NOT NULL REFERENCES pig_instances(pig_instance_id),
            hours INTEGER NOT NULL DEFAULT 0 CHECK(hours >= 0),
            normal_hours INTEGER NOT NULL DEFAULT 0 CHECK(normal_hours BETWEEN 0 AND hours),
            PRIMARY KEY(player_id,pig_instance_id)
        )""",
        """CREATE TABLE dispatch_souvenirs(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            souvenir_id TEXT NOT NULL,
            trip_id TEXT NOT NULL REFERENCES dispatch_trips(trip_id),
            found_at TEXT NOT NULL,
            PRIMARY KEY(player_id,souvenir_id)
        )""",
        """CREATE TABLE dispatch_tools(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            tool_id TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            PRIMARY KEY(player_id,tool_id)
        )""",
        """CREATE TABLE dispatch_pending(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            operation TEXT NOT NULL CHECK(operation IN ('team','start','recall')),
            payload_json TEXT NOT NULL,
            expires_ms INTEGER NOT NULL
        )""",
        """CREATE TABLE dispatch_choices(
            choice_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            trip_id TEXT NOT NULL REFERENCES dispatch_trips(trip_id),
            options_json TEXT NOT NULL,
            selected INTEGER CHECK(selected IN (1,2)),
            claimed_at TEXT
        )""",
        "CREATE INDEX idx_dispatch_pending_choices ON dispatch_choices(player_id) WHERE selected IS NULL",
        """CREATE TABLE activity_facts(
            fact_key TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            subevent_id TEXT NOT NULL,
            definition_version INTEGER NOT NULL,
            occurred_ms INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(player_id,source_type,source_id,subevent_id)
        )""",
        "CREATE INDEX idx_activity_facts_player_source ON activity_facts(player_id,source_type,occurred_ms)",
        """CREATE TRIGGER activity_facts_no_update BEFORE UPDATE ON activity_facts
        BEGIN SELECT RAISE(ABORT, '活动事实不可改写'); END""",
        """CREATE TRIGGER activity_facts_no_delete BEFORE DELETE ON activity_facts
        BEGIN SELECT RAISE(ABORT, '活动事实不可删除'); END""",
    ),
)
