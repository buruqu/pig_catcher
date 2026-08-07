"""抓猪额度窗口与审计重置仓储。"""

from __future__ import annotations

from ..database import DatabaseSession

_RESET_ACTIONS = (
    "daily-catch-quota-reset",
    "catch-quota-window-reset",
    "catch-quota-window-boost",
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
        action_placeholders = ",".join("?" for _ in _RESET_ACTIONS)
        row = await session.fetch_one(
            f"""
            SELECT COALESCE(MAX(created_at), ?) AS effective_start
            FROM audit_events
            WHERE action IN ({action_placeholders})
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

    async def insert_boost_event(
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
        """写入一次群级窗口提额审计；同时作为该窗口的有效额度重置事件。"""

        await session.execute(
            """
            INSERT INTO audit_events(
                audit_event_id, scope_id, actor_user_id, action,
                object_type, object_id, detail_json, created_at
            )
            VALUES (?, ?, ?, 'catch-quota-window-boost',
                    'quota-window-boost', ?, ?, ?)
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

    async def active_window_boost(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        window_start: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT scope_id, window_start, limit_value,
                   created_by, reason, created_at
            FROM quota_window_boosts
            WHERE scope_id = ? AND window_start = ?
            """,
            (scope_id, window_start),
        )
        return dict(row) if row is not None else None

    async def upsert_window_boost(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        window_start: str,
        limit_value: int,
        created_by: str,
        reason: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO quota_window_boosts(
                scope_id, window_start, limit_value,
                created_by, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_id, window_start) DO UPDATE SET
                limit_value = excluded.limit_value,
                created_by = excluded.created_by,
                reason = excluded.reason,
                created_at = excluded.created_at
            """,
            (
                scope_id,
                window_start,
                int(limit_value),
                str(created_by or "").strip(),
                str(reason or "").strip(),
                now,
            ),
        )

    async def delete_expired_boosts(
        self,
        session: DatabaseSession,
        *,
        window_start: str,
    ) -> int:
        """清理所有目标窗口已结束的历史提额记录，仅用于维护任务。"""

        cursor = await session.execute(
            "DELETE FROM quota_window_boosts WHERE window_start < ?",
            (window_start,),
        )
        return max(int(cursor.rowcount), 0)
