"""Schema 35: extensible achievements, rewards, cosmetics and notifications."""

from .model import Migration

MIGRATION_0035 = Migration(
    version=35,
    name="achievement-system",
    statements=(
        """
        CREATE TABLE achievement_definition_snapshots(
            achievement_id TEXT NOT NULL,
            definition_version INTEGER NOT NULL CHECK(definition_version >= 1),
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            tier TEXT NOT NULL CHECK(tier IN (
                'normal', 'fine', 'rare', 'epic', 'legendary', 'ultimate'
            )),
            hidden INTEGER NOT NULL CHECK(hidden IN (0, 1)),
            points INTEGER NOT NULL CHECK(points > 0),
            description TEXT NOT NULL,
            hint TEXT NOT NULL,
            condition_json TEXT NOT NULL,
            rewards_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(achievement_id, definition_version)
        )
        """,
        """
        CREATE TABLE achievement_profiles(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
            achievement_points INTEGER NOT NULL DEFAULT 0 CHECK(achievement_points >= 0),
            equipped_title_id TEXT NOT NULL DEFAULT '',
            equipped_frame_id TEXT NOT NULL DEFAULT '',
            showcase_achievement_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE achievement_progress(
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            definition_version INTEGER NOT NULL,
            progress_value INTEGER NOT NULL DEFAULT 0 CHECK(progress_value >= 0),
            state_json TEXT NOT NULL DEFAULT '{}',
            unlocked_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(player_id, achievement_id),
            FOREIGN KEY(achievement_id, definition_version)
                REFERENCES achievement_definition_snapshots(achievement_id, definition_version)
        )
        """,
        """
        CREATE TABLE achievement_events(
            event_id TEXT PRIMARY KEY,
            receipt_id TEXT NOT NULL,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(player_id, receipt_id)
        )
        """,
        """
        CREATE TABLE achievement_unlocks(
            unlock_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            definition_version INTEGER NOT NULL,
            source_event_id TEXT NOT NULL REFERENCES achievement_events(event_id),
            source_receipt_id TEXT NOT NULL,
            points_awarded INTEGER NOT NULL CHECK(points_awarded > 0),
            rewards_json TEXT NOT NULL,
            notification_status TEXT NOT NULL DEFAULT 'pending' CHECK(
                notification_status IN ('pending', 'claimed', 'sent', 'failed', 'summary')
            ),
            notification_error TEXT NOT NULL DEFAULT '',
            unlocked_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(player_id, achievement_id),
            FOREIGN KEY(achievement_id, definition_version)
                REFERENCES achievement_definition_snapshots(achievement_id, definition_version)
        )
        """,
        """
        CREATE TABLE achievement_reward_inventory(
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            reward_type TEXT NOT NULL CHECK(reward_type IN (
                'ticket', 'title', 'frame', 'badge', 'chest', 'cosmetic'
            )),
            reward_id TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(player_id, reward_type, reward_id)
        )
        """,
        """
        CREATE TABLE achievement_metric_counters(
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            metric_key TEXT NOT NULL,
            metric_value INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(player_id, metric_key)
        )
        """,
        """
        INSERT INTO achievement_metric_counters(
            player_id, metric_key, metric_value, updated_at
        )
        SELECT player_id, 'ordinary_coins_earned', SUM(amount), MAX(created_at)
        FROM currency_ledger
        WHERE amount > 0 AND reason_code <> 'admin-coin-adjustment'
        GROUP BY player_id
        """,
        """
        INSERT INTO achievement_metric_counters(
            player_id, metric_key, metric_value, updated_at
        )
        SELECT player_id, 'admin_coin_adjustment_net', SUM(amount), MAX(created_at)
        FROM currency_ledger
        WHERE reason_code = 'admin-coin-adjustment'
        GROUP BY player_id
        """,
        """
        CREATE TRIGGER achievement_metric_ordinary_coin_insert
        AFTER INSERT ON currency_ledger
        WHEN NEW.amount > 0 AND NEW.reason_code <> 'admin-coin-adjustment'
        BEGIN
            INSERT INTO achievement_metric_counters(
                player_id, metric_key, metric_value, updated_at
            ) VALUES (
                NEW.player_id, 'ordinary_coins_earned', NEW.amount, NEW.created_at
            )
            ON CONFLICT(player_id, metric_key) DO UPDATE SET
                metric_value = metric_value + NEW.amount,
                updated_at = NEW.created_at;
        END
        """,
        """
        CREATE TRIGGER achievement_metric_admin_coin_insert
        AFTER INSERT ON currency_ledger
        WHEN NEW.reason_code = 'admin-coin-adjustment'
        BEGIN
            INSERT INTO achievement_metric_counters(
                player_id, metric_key, metric_value, updated_at
            ) VALUES (
                NEW.player_id, 'admin_coin_adjustment_net', NEW.amount, NEW.created_at
            )
            ON CONFLICT(player_id, metric_key) DO UPDATE SET
                metric_value = metric_value + NEW.amount,
                updated_at = NEW.created_at;
        END
        """,
        """
        CREATE TABLE achievement_scope_targets(
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            target_key TEXT NOT NULL,
            target_label TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            PRIMARY KEY(scope_id, achievement_id, target_key)
        )
        """,
        """
        CREATE TABLE achievement_backfill_state(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed')),
            last_error TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        """
        INSERT INTO achievement_backfill_state(player_id, status, updated_at)
        SELECT player_id, 'pending', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM players
        """,
        """
        CREATE TABLE achievement_milestone_claims(
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            milestone_points INTEGER NOT NULL CHECK(milestone_points > 0),
            rewards_json TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            PRIMARY KEY(player_id, milestone_points)
        )
        """,
        """
        CREATE TABLE achievement_operations(
            operation_key TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            operation_type TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE achievement_ticket_effects(
            effect_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            ticket_id TEXT NOT NULL,
            action_type TEXT NOT NULL CHECK(action_type IN ('catching', 'cooking', 'visual')),
            granted_uses INTEGER NOT NULL CHECK(granted_uses > 0),
            consumed_uses INTEGER NOT NULL DEFAULT 0 CHECK(
                consumed_uses >= 0 AND consumed_uses <= granted_uses
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX idx_achievement_progress_player_category
        ON achievement_progress(player_id, unlocked_at, updated_at)
        """,
        """
        CREATE INDEX idx_achievement_unlock_notifications
        ON achievement_unlocks(source_receipt_id, player_id, notification_status)
        """,
        """
        CREATE INDEX idx_achievement_unlock_ranking
        ON achievement_unlocks(scope_id, player_id, unlocked_at)
        """,
        """
        CREATE INDEX idx_achievement_events_scope_created
        ON achievement_events(scope_id, created_at)
        """,
        """
        CREATE INDEX idx_achievement_rewards_player
        ON achievement_reward_inventory(player_id, reward_type, quantity)
        """,
        """
        CREATE INDEX idx_achievement_ticket_effects_active
        ON achievement_ticket_effects(
            player_id, action_type, ticket_id, consumed_uses, created_at
        )
        """,
    ),
)


__all__ = ["MIGRATION_0035"]
