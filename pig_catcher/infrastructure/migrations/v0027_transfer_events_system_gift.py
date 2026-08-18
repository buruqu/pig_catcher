"""Rebuild asset_transfer_events to allow system group-effect gifts."""

from .model import Migration

MIGRATION_0027 = Migration(
    version=27,
    name="transfer_events_system_gift",
    statements=(
        # 兜底：旧库（如 v9 最小结构）没有该表时先按旧结构创建，保证源表可拷贝。
        # trade_id 不再引用 trade_offers，避免依赖表存在性，功能不变。
        """
        CREATE TABLE IF NOT EXISTS asset_transfer_events (
            transfer_event_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            asset_kind TEXT NOT NULL CHECK (asset_kind IN ('pig', 'food')),
            asset_instance_id TEXT NOT NULL,
            from_player_id TEXT NOT NULL REFERENCES players(player_id),
            to_player_id TEXT NOT NULL REFERENCES players(player_id),
            transfer_type TEXT NOT NULL CHECK (transfer_type IN ('gift', 'trade')),
            trade_id TEXT,
            created_at TEXT NOT NULL,
            CHECK (from_player_id <> to_player_id)
        )
        """,
        """
        CREATE TABLE asset_transfer_events_v27 (
            transfer_event_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            asset_kind TEXT NOT NULL CHECK (asset_kind IN ('pig', 'food')),
            asset_instance_id TEXT NOT NULL,
            from_player_id TEXT NOT NULL REFERENCES players(player_id),
            to_player_id TEXT NOT NULL REFERENCES players(player_id),
            transfer_type TEXT NOT NULL CHECK (
                transfer_type IN ('gift', 'trade', 'system-group-effect')
            ),
            trade_id TEXT,
            created_at TEXT NOT NULL,
            CHECK (from_player_id <> to_player_id)
        )
        """,
        """
        INSERT INTO asset_transfer_events_v27(
            transfer_event_id, scope_id, asset_kind, asset_instance_id,
            from_player_id, to_player_id, transfer_type, trade_id, created_at
        )
        SELECT transfer_event_id, scope_id, asset_kind, asset_instance_id,
               from_player_id, to_player_id, transfer_type, trade_id, created_at
        FROM asset_transfer_events
        """,
        "DROP TABLE asset_transfer_events",
        "ALTER TABLE asset_transfer_events_v27 RENAME TO asset_transfer_events",
        """
        CREATE INDEX idx_transfer_events_scope_created
        ON asset_transfer_events(scope_id, created_at DESC)
        """,
    ),
)
