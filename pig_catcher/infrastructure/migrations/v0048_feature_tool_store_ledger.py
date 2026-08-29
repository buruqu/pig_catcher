"""Schema 48：功能商城器具购买的不可变数量账本。"""

from .model import Migration

TABLES = {"feature_tool_store_ledger"}
GUARDS = {
    "idx_feature_tool_store_ledger_player",
    "feature_tool_store_ledger_scope_guard",
    "feature_tool_store_ledger_no_update",
    "feature_tool_store_ledger_no_delete",
}

MIGRATION_0048 = Migration(
    version=48,
    name="feature-tool-store-ledger",
    statements=(
        """
        CREATE TABLE feature_tool_store_ledger(
            entry_key TEXT PRIMARY KEY CHECK(length(entry_key) > 0),
            player_id TEXT NOT NULL REFERENCES players(player_id),
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            system TEXT NOT NULL CHECK(system IN ('dispatch','tour','battle')),
            product_id TEXT NOT NULL CHECK(length(product_id) > 0),
            tool_id TEXT NOT NULL CHECK(length(tool_id) > 0),
            delta INTEGER NOT NULL CHECK(delta > 0),
            balance_after INTEGER NOT NULL CHECK(balance_after >= delta),
            unit_price INTEGER NOT NULL CHECK(unit_price > 0),
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            total_price INTEGER NOT NULL CHECK(total_price > 0 AND total_price = unit_price * quantity),
            source_kind TEXT NOT NULL CHECK(length(source_kind) > 0),
            occurred_at TEXT NOT NULL CHECK(length(occurred_at) > 0),
            CHECK(delta = quantity)
        )
        """,
        """
        CREATE INDEX idx_feature_tool_store_ledger_player
        ON feature_tool_store_ledger(player_id, system, occurred_at)
        """,
        """
        CREATE TRIGGER feature_tool_store_ledger_scope_guard
        BEFORE INSERT ON feature_tool_store_ledger
        WHEN NOT EXISTS(
            SELECT 1 FROM players
            WHERE players.player_id = NEW.player_id
              AND players.scope_id = NEW.scope_id
        )
        BEGIN SELECT RAISE(ABORT, '功能商城器具购买的玩家与群范围不一致'); END
        """,
        """
        CREATE TRIGGER feature_tool_store_ledger_no_update
        BEFORE UPDATE ON feature_tool_store_ledger
        BEGIN SELECT RAISE(ABORT, '功能商城器具购买账本不可改写'); END
        """,
        """
        CREATE TRIGGER feature_tool_store_ledger_no_delete
        BEFORE DELETE ON feature_tool_store_ledger
        BEGIN SELECT RAISE(ABORT, '功能商城器具购买账本不可删除'); END
        """,
    ),
)
