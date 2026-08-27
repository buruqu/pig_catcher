"""Durable activity outbox, incremental projections and purpose-specific coupons."""

from .model import Migration

TABLES = (
    "achievement_activity_queue",
    "achievement_activity_state",
    "achievement_coupon_selection",
    "achievement_coupon_uses",
    "achievement_material_choices",
)

MIGRATION_0040 = Migration(
    version=40,
    name="activity-achievements-and-typed-coupons",
    statements=(
        """CREATE TABLE achievement_activity_queue(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        fact_key TEXT NOT NULL UNIQUE REFERENCES activity_facts(fact_key),
        player_id TEXT NOT NULL REFERENCES players(player_id), scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
        processed_at TEXT, historical INTEGER NOT NULL DEFAULT 0 CHECK(historical IN (0,1)))""",
        "CREATE INDEX idx_achievement_activity_pending ON achievement_activity_queue(scope_id,sequence) "
        "WHERE processed_at IS NULL",
        """INSERT INTO achievement_activity_queue(fact_key,player_id,scope_id,historical)
        SELECT fact_key,player_id,scope_id,1 FROM activity_facts ORDER BY rowid""",
        """CREATE TRIGGER achievement_activity_enqueue AFTER INSERT ON activity_facts BEGIN
        INSERT INTO achievement_activity_queue(fact_key,player_id,scope_id)
        VALUES(NEW.fact_key,NEW.player_id,NEW.scope_id); END""",
        """CREATE TABLE achievement_activity_state(
        player_id TEXT PRIMARY KEY REFERENCES players(player_id), definition_version INTEGER NOT NULL,
        state_json TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE achievement_coupon_selection(
        player_id TEXT NOT NULL REFERENCES players(player_id), slot TEXT NOT NULL,
        ticket_id TEXT NOT NULL, selected_at TEXT NOT NULL, PRIMARY KEY(player_id,slot))""",
        """CREATE TABLE achievement_coupon_uses(
        entry_key TEXT PRIMARY KEY, player_id TEXT NOT NULL REFERENCES players(player_id),
        ticket_id TEXT NOT NULL, slot TEXT NOT NULL, source_id TEXT NOT NULL,
        effect_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(player_id,source_id,slot))""",
        """CREATE TABLE achievement_material_choices(
        player_id TEXT PRIMARY KEY REFERENCES players(player_id), chest_id TEXT NOT NULL,
        material_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
        expires_ms INTEGER NOT NULL)""",
        "CREATE INDEX idx_achievement_unlock_notification_scope "
        "ON achievement_unlocks(scope_id,source_receipt_id,notification_status)",
        """CREATE INDEX idx_activity_facts_source ON activity_facts(player_id,source_type,source_id,subevent_id)""",
        *(
            f"CREATE TRIGGER achievement_coupon_uses_no_{op.lower()} BEFORE {op} ON achievement_coupon_uses "
            "BEGIN SELECT RAISE(ABORT,'成就券使用账本不可重写'); END"
            for op in ("UPDATE", "DELETE")
        ),
    ),
)
GUARDS = (
    "achievement_activity_enqueue",
    "idx_achievement_activity_pending",
    "idx_activity_facts_source",
    "idx_achievement_unlock_notification_scope",
    "achievement_coupon_uses_no_update",
    "achievement_coupon_uses_no_delete",
)
