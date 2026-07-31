"""抓猪额度窗口与审计重置仓储。"""

from __future__ import annotations

from ..database import DatabaseSession

_RESET_ACTIONS = (
    "daily-catch-quota-reset",
    "catch-quota-window-reset",
)


class QuotaRepository:
    """在不删除历史回执的前提下重置额度计数。"""

    async def scope_exists(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
    ) -> bool:
        row = await session.fetch_one(
            "SELECT 1 FROM scopes WHERE scope_id = ?",
            (scope_id,),
        )
        return row is not None

    async def effective_window_start(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        window_start: str,
        window_end: str,
    ) -> str:
        row = await session.fetch_one(
            """
            SELECT COALESCE(MAX(created_at), ?) AS effective_start
            FROM audit_events
            WHERE action IN (?, ?)
              AND created_at >= ?
              AND created_at < ?
              AND (scope_id IS NULL OR scope_id = ?)
            """,
            (
                window_start,
                *_RESET_ACTIONS,
                window_start,
                window_end,
                scope_id,
            ),
        )
        return str(row["effective_start"]) if row is not None else window_start

    async def usage_since(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        effective_start: str,
        window_end: str,
    ) -> tuple[int, int]:
        row = await session.fetch_one(
            """
            SELECT
                COUNT(*) AS catch_count,
                COUNT(DISTINCT player_id) AS player_count
            FROM command_receipts
            WHERE scope_id = ?
              AND command_name = 'pig-catcher.catch'
              AND created_at >= ?
              AND created_at < ?
            """,
            (scope_id, effective_start, window_end),
        )
        if row is None:
            return 0, 0
        return int(row["catch_count"]), int(row["player_count"])

    async def insert_reset_event(
        self,
        session: DatabaseSession,
        *,
        audit_event_id: str,
        scope_id: str,
        actor_user_id: str,
        object_id: str,
        detail_json: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO audit_events(
                audit_event_id, scope_id, actor_user_id, action,
                object_type, object_id, detail_json, created_at
            )
            VALUES (?, ?, ?, 'catch-quota-window-reset',
                    'catch-quota-window', ?, ?, ?)
            """,
            (
                audit_event_id,
                scope_id,
                actor_user_id,
                object_id,
                detail_json,
                now,
            ),
        )
