"""可到期的玩家社交与抓猪额度限制仓储。"""

from __future__ import annotations

from collections.abc import Sequence

from ..database import DatabaseSession

GIFT_TRANSFER_BAN = "gift-transfer-ban"
TRADE_BAN = "trade-ban"
CATCH_WINDOW_LIMIT = "catch-window-limit"


class RestrictionRepository:
    """读取和写入玩家限制，不自行拥有事务。"""

    async def active_restriction(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        restriction_type: str,
        now: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT restriction_id, player_id, restriction_type, limit_value,
                   starts_at, expires_at, reason, source, created_by
            FROM player_restrictions
            WHERE player_id = ?
              AND restriction_type = ?
              AND starts_at <= ?
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (player_id, restriction_type, now, now),
        )
        return dict(row) if row is not None else None

    async def active_restrictions_for_players(
        self,
        session: DatabaseSession,
        *,
        player_ids: Sequence[str],
        restriction_type: str,
        now: str,
    ) -> list[dict[str, object]]:
        normalized = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in player_ids
                if str(value).strip()
            )
        )
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        rows = await session.fetch_all(
            f"""
            SELECT restriction_id, player_id, restriction_type, limit_value,
                   starts_at, expires_at, reason, source, created_by
            FROM player_restrictions
            WHERE player_id IN ({placeholders})
              AND restriction_type = ?
              AND starts_at <= ?
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY player_id
            """,
            (*normalized, restriction_type, now, now),
        )
        return [dict(row) for row in rows]

    async def upsert_restriction(
        self,
        session: DatabaseSession,
        *,
        restriction_id: str,
        player_id: str,
        restriction_type: str,
        limit_value: int | None,
        starts_at: str,
        expires_at: str | None,
        reason: str,
        source: str,
        created_by: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO player_restrictions(
                restriction_id, player_id, restriction_type, limit_value,
                starts_at, expires_at, reason, source, created_by,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id, restriction_type) DO UPDATE SET
                limit_value = excluded.limit_value,
                starts_at = excluded.starts_at,
                expires_at = excluded.expires_at,
                reason = excluded.reason,
                source = excluded.source,
                created_by = excluded.created_by,
                updated_at = excluded.updated_at
            """,
            (
                restriction_id,
                player_id,
                restriction_type,
                limit_value,
                starts_at,
                expires_at,
                reason,
                source,
                created_by,
                now,
                now,
            ),
        )

    async def players_in_scope(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        player_ids: Sequence[str],
    ) -> list[dict[str, object]]:
        normalized = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in player_ids
                if str(value).strip()
            )
        )
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        rows = await session.fetch_all(
            f"""
            SELECT player_id, platform_user_id, display_name
            FROM players
            WHERE scope_id = ? AND player_id IN ({placeholders})
            ORDER BY player_id
            """,
            (scope_id, *normalized),
        )
        return [dict(row) for row in rows]

    async def scopes_for_group(
        self,
        session: DatabaseSession,
        *,
        group_id: str,
        platform: str = "",
    ) -> list[dict[str, object]]:
        """Resolve an exact group, optionally narrowed to one platform."""

        normalized_platform = str(platform or "").strip()
        if normalized_platform:
            rows = await session.fetch_all(
                """
                SELECT scope_id, platform, group_id, group_name, stream_id
                FROM scopes
                WHERE group_id = ? AND platform = ?
                ORDER BY scope_id
                """,
                (str(group_id).strip(), normalized_platform),
            )
        else:
            rows = await session.fetch_all(
                """
                SELECT scope_id, platform, group_id, group_name, stream_id
                FROM scopes
                WHERE group_id = ?
                ORDER BY scope_id
                """,
                (str(group_id).strip(),),
            )
        return [dict(row) for row in rows]

    async def players_by_platform_user_ids(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        platform_user_ids: Sequence[str],
    ) -> list[dict[str, object]]:
        normalized = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in platform_user_ids
                if str(value).strip()
            )
        )
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        rows = await session.fetch_all(
            f"""
            SELECT player_id, platform_user_id, display_name
            FROM players
            WHERE scope_id = ? AND platform_user_id IN ({placeholders})
            ORDER BY player_id
            """,
            (scope_id, *normalized),
        )
        return [dict(row) for row in rows]

    async def delete_restrictions(
        self,
        session: DatabaseSession,
        *,
        player_ids: Sequence[str],
        restriction_type: str,
    ) -> int:
        normalized = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in player_ids
                if str(value).strip()
            )
        )
        if not normalized:
            return 0
        placeholders = ",".join("?" for _ in normalized)
        cursor = await session.execute(
            f"""
            DELETE FROM player_restrictions
            WHERE player_id IN ({placeholders}) AND restriction_type = ?
            """,
            (*normalized, restriction_type),
        )
        return max(int(cursor.rowcount), 0)

    async def insert_operation_audit_event(
        self,
        session: DatabaseSession,
        *,
        audit_event_id: str,
        scope_id: str,
        actor_user_id: str,
        action: str,
        object_type: str,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_event_id,
                scope_id,
                actor_user_id,
                action,
                object_type,
                object_id,
                detail_json,
                now,
            ),
        )

    async def insert_audit_event(
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
            VALUES (?, ?, ?, 'player-restrictions-applied',
                    'player-restriction-batch', ?, ?, ?)
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
