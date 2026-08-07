"""Window-scoped per-group catch quota boost (admin temporary limit raise)."""

from .model import Migration

MIGRATION_0013 = Migration(
    version=13,
    name="quota_window_boosts",
    statements=(
        (
            "CREATE TABLE IF NOT EXISTS quota_window_boosts("
            "scope_id TEXT NOT NULL, "
            "window_start TEXT NOT NULL, "
            "limit_value INTEGER NOT NULL CHECK (limit_value >= 1), "
            "created_by TEXT NOT NULL, "
            "reason TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL, "
            "PRIMARY KEY (scope_id, window_start)"
            ")"
        ),
        (
            "CREATE INDEX IF NOT EXISTS idx_quota_window_boosts_window_start "
            "ON quota_window_boosts(window_start)"
        ),
    ),
)
