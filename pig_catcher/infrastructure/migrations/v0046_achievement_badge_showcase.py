"""Schema 46: owned, player-scoped badge slots; preserve the legacy first slot."""

from .model import Migration

MIGRATION_0046 = Migration(
    version=46,
    name="achievement-badge-showcase",
    statements=(
        """
        CREATE TABLE achievement_badge_slots(
            player_id TEXT NOT NULL REFERENCES achievement_profiles(player_id) ON DELETE CASCADE,
            slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 3),
            badge_id TEXT NOT NULL CHECK(length(badge_id) > 0),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(player_id, slot),
            UNIQUE(player_id, badge_id)
        )
        """,
        # 1.x has no achievements. Early 2.0 stores either an achievement ID or
        # a weekly badge ID in this field; migrate only an actually owned badge.
        """
        INSERT INTO achievement_badge_slots(player_id, slot, badge_id, updated_at)
        SELECT ap.player_id, 1, owned.reward_id, ap.updated_at
        FROM achievement_profiles ap
        JOIN achievement_reward_inventory owned ON owned.player_id=ap.player_id
          AND owned.reward_type='badge' AND owned.quantity>0
        WHERE owned.reward_id = COALESCE(
            (SELECT direct.reward_id FROM achievement_reward_inventory direct
             WHERE direct.player_id=ap.player_id AND direct.reward_type='badge'
               AND direct.reward_id=ap.showcase_achievement_id AND direct.quantity>0),
            (SELECT json_extract(reward.value, '$.id')
             FROM achievement_definition_snapshots definition, json_each(definition.rewards_json) reward
             WHERE definition.achievement_id=ap.showcase_achievement_id
               AND json_extract(reward.value, '$.type')='badge'
             ORDER BY definition.definition_version DESC, reward.key LIMIT 1)
        )
        """,
        """
        CREATE TRIGGER achievement_badge_slot_insert_guard
        BEFORE INSERT ON achievement_badge_slots
        BEGIN
            SELECT CASE WHEN NOT EXISTS(
                SELECT 1 FROM achievement_reward_inventory r WHERE r.player_id=NEW.player_id
                AND r.reward_type='badge' AND r.reward_id=NEW.badge_id AND r.quantity>0
            ) THEN RAISE(ABORT, 'badge not owned') END;
            SELECT CASE WHEN NEW.slot>1 AND NOT EXISTS(
                SELECT 1 FROM achievement_reward_inventory r WHERE r.player_id=NEW.player_id
                AND r.reward_type='cosmetic' AND r.reward_id='badge-showcase-3' AND r.quantity>0
            ) THEN RAISE(ABORT, 'badge slot locked') END;
        END
        """,
        """
        CREATE TRIGGER achievement_badge_slot_update_guard
        BEFORE UPDATE ON achievement_badge_slots
        BEGIN
            SELECT CASE WHEN NOT EXISTS(
                SELECT 1 FROM achievement_reward_inventory r WHERE r.player_id=NEW.player_id
                AND r.reward_type='badge' AND r.reward_id=NEW.badge_id AND r.quantity>0
            ) THEN RAISE(ABORT, 'badge not owned') END;
            SELECT CASE WHEN NEW.slot>1 AND NOT EXISTS(
                SELECT 1 FROM achievement_reward_inventory r WHERE r.player_id=NEW.player_id
                AND r.reward_type='cosmetic' AND r.reward_id='badge-showcase-3' AND r.quantity>0
            ) THEN RAISE(ABORT, 'badge slot locked') END;
        END
        """,
    ),
)
