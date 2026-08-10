"""Schema 17：赠送与成交交易共用的自动监管案件、提醒和临时限制。"""

from .model import Migration

MIGRATION_0017 = Migration(
    version=17,
    name="automatic-regulation",
    statements=(
        """
        CREATE TABLE anti_abuse_cases (
            case_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            target_signature TEXT NOT NULL,
            target_player_ids_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'watching', 'supervised', 'social-restricted',
                    'plugin-restricted', 'closed', 'dismissed'
                )
            ),
            score INTEGER NOT NULL CHECK (score >= 0),
            ruleset_version INTEGER NOT NULL CHECK (ruleset_version > 0),
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_evidence_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """,
        """
        CREATE TABLE anti_abuse_case_members (
            case_id TEXT NOT NULL REFERENCES anti_abuse_cases(case_id) ON DELETE CASCADE,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (
                role IN ('source', 'relay', 'target', 'active-trader')
            ),
            active_participant INTEGER NOT NULL DEFAULT 0
                CHECK (active_participant IN (0, 1)),
            warning_served_at TEXT,
            incident_count INTEGER NOT NULL DEFAULT 0 CHECK (incident_count >= 0),
            last_incident_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (case_id, player_id)
        )
        """,
        """
        CREATE TABLE anti_abuse_notices (
            notice_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES anti_abuse_cases(case_id) ON DELETE CASCADE,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            stage TEXT NOT NULL CHECK (
                stage IN (
                    'warning', 'supervision', 'social-restriction',
                    'plugin-restriction', 'release'
                )
            ),
            incident_number INTEGER NOT NULL DEFAULT 0 CHECK (incident_number >= 0),
            message_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'claimed', 'sent', 'failed')),
            source_operation_key TEXT NOT NULL DEFAULT '',
            error_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sent_at TEXT,
            UNIQUE(case_id, player_id, stage, incident_number)
        )
        """,
        """
        CREATE TABLE anti_abuse_holds (
            hold_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES anti_abuse_cases(case_id) ON DELETE CASCADE,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            hold_type TEXT NOT NULL CHECK (hold_type IN ('social', 'plugin')),
            sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'expired', 'released')),
            starts_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            released_at TEXT,
            CHECK (expires_at > starts_at),
            UNIQUE(case_id, player_id, hold_type, sequence_number)
        )
        """,
        """
        CREATE TABLE anti_abuse_events (
            event_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES anti_abuse_cases(case_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            player_id TEXT REFERENCES players(player_id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            score INTEGER NOT NULL CHECK (score >= 0),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_anti_abuse_cases_scope_updated
        ON anti_abuse_cases(scope_id, updated_at DESC)
        """,
        """
        CREATE INDEX idx_anti_abuse_members_player
        ON anti_abuse_case_members(player_id, updated_at DESC)
        """,
        """
        CREATE INDEX idx_anti_abuse_notices_player_status
        ON anti_abuse_notices(player_id, status, created_at)
        """,
        """
        CREATE INDEX idx_anti_abuse_holds_player_expiry
        ON anti_abuse_holds(player_id, hold_type, status, expires_at)
        """,
        """
        CREATE INDEX idx_anti_abuse_events_case_created
        ON anti_abuse_events(case_id, created_at DESC)
        """,
    ),
)

__all__ = ["MIGRATION_0017"]
