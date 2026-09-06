"""Schema 65: escrow-backed luck packets and durable scheduled reward campaigns."""

from .model import Migration

MIGRATION_0065 = Migration(
    version=65,
    name="red-packets-and-scheduled-campaigns",
    statements=(
        """
        CREATE TABLE red_packets(
            packet_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            sender_player_id TEXT NOT NULL REFERENCES players(player_id),
            sender_name TEXT NOT NULL,
            funding TEXT NOT NULL CHECK(funding IN ('player','admin')),
            greeting TEXT NOT NULL,
            total_coins INTEGER NOT NULL CHECK(total_coins>0),
            total_count INTEGER NOT NULL CHECK(total_count>0 AND total_count<=total_coins),
            remaining_coins INTEGER NOT NULL CHECK(remaining_coins>=0 AND remaining_coins<=total_coins),
            remaining_count INTEGER NOT NULL CHECK(remaining_count>=0 AND remaining_count<=total_count),
            refunded_coins INTEGER NOT NULL DEFAULT 0 CHECK(refunded_coins>=0),
            status TEXT NOT NULL CHECK(status IN ('active','claimed','expired')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK((remaining_count=0)=(remaining_coins=0)),
            CHECK(remaining_coins>=remaining_count)
        )
        """,
        "CREATE INDEX red_packets_scope_active ON red_packets(scope_id,status,created_at)",
        "CREATE INDEX red_packets_due ON red_packets(status,expires_at)",
        """
        CREATE TABLE red_packet_claims(
            packet_id TEXT NOT NULL REFERENCES red_packets(packet_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            display_name TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount>0),
            created_at TEXT NOT NULL,
            PRIMARY KEY(packet_id,player_id)
        )
        """,
        """
        CREATE TABLE scheduled_reward_campaigns(
            campaign_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'scheduled' CHECK(state IN ('scheduled','complete')),
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """,
        """
        CREATE TABLE scheduled_reward_scopes(
            campaign_id TEXT NOT NULL REFERENCES scheduled_reward_campaigns(campaign_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            receipt_id TEXT REFERENCES command_receipts(receipt_id),
            recipient_count INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            PRIMARY KEY(campaign_id,scope_id)
        )
        """,
        """
        CREATE TABLE scheduled_reward_grants(
            campaign_id TEXT NOT NULL REFERENCES scheduled_reward_campaigns(campaign_id),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id,player_id)
        )
        """,
        "ALTER TABLE pig_instances ADD COLUMN commemorative_code TEXT NOT NULL DEFAULT ''",
    ),
)
