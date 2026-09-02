"""Schema 56: launch grants, daily manual-gift indexes and coupon choices."""

from .model import Migration
from .v0055_battle_rule_v7 import GUARDS as _V0055_GUARDS

TABLES = ("launch_campaign_grants",)
GUARDS = (
    *_V0055_GUARDS,
    "idx_transfer_manual_gift_sender_day",
    "idx_transfer_manual_gift_recipient_day",
    "launch_campaign_grants_no_update",
    "launch_campaign_grants_no_delete",
)

MIGRATION_0056 = Migration(
    version=56,
    name="launch-campaign-social-limits-and-coupon-choices",
    statements=(
        "CREATE INDEX idx_transfer_manual_gift_sender_day "
        "ON asset_transfer_events(scope_id,from_player_id,transfer_type,created_at)",
        "CREATE INDEX idx_transfer_manual_gift_recipient_day "
        "ON asset_transfer_events(scope_id,to_player_id,transfer_type,created_at)",
        # Pending choices live for only 30 seconds, so a migration may safely
        # discard them while expanding the operation constraint.
        "DROP TABLE item_coupon_choices",
        """CREATE TABLE item_coupon_choices(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            operation TEXT NOT NULL CHECK(operation IN(
                'pig-choice','food-choice','battle-pig-choice'
            )),
            payload_json TEXT NOT NULL,
            expires_ms INTEGER NOT NULL CHECK(expires_ms>0),
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE launch_campaign_grants(
            campaign_id TEXT NOT NULL,
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(campaign_id,player_id)
        )""",
        "CREATE INDEX idx_launch_campaign_grants_scope ON launch_campaign_grants(scope_id,created_at)",
        """CREATE TRIGGER launch_campaign_grants_no_update
        BEFORE UPDATE ON launch_campaign_grants
        BEGIN SELECT RAISE(ABORT,'开服礼包发放记录不可改写'); END""",
        """CREATE TRIGGER launch_campaign_grants_no_delete
        BEFORE DELETE ON launch_campaign_grants
        BEGIN SELECT RAISE(ABORT,'开服礼包发放记录不可删除'); END""",
    ),
)
