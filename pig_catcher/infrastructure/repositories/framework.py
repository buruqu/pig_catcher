"""群范围和玩家基础仓储。"""

from __future__ import annotations

from ...domain.models import CommandIdentity, ScopeKey
from ..database import DatabaseSession
from .dispatch import DispatchRepository


class FrameworkRepository:
    """写入范围和玩家身份快照，不拥有事务。"""

    async def ensure_scope(
        self,
        session: DatabaseSession,
        *,
        scope: ScopeKey,
        group_name: str,
        stream_id: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO scopes(
                scope_id, platform, group_id, group_name, stream_id, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(scope_id) DO UPDATE SET
                group_name = CASE
                    WHEN excluded.group_name <> '' THEN excluded.group_name
                    ELSE scopes.group_name
                END,
                stream_id = CASE
                    WHEN excluded.stream_id <> '' THEN excluded.stream_id
                    ELSE scopes.stream_id
                END,
                updated_at = excluded.updated_at
            """,
            (scope.value, scope.platform, scope.group_id, group_name, stream_id, now, now),
        )

    async def ensure_player(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO players(
                player_id, scope_id, platform_user_id, display_name,
                coin_balance, experience, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 0, 0, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (
                identity.player_id,
                identity.scope.value,
                identity.user_id,
                identity.display_name,
                now,
                now,
            ),
        )
        await session.execute(
            """
            INSERT INTO player_statistics(player_id, updated_at)
            VALUES (?, ?)
            ON CONFLICT(player_id) DO NOTHING
            """,
            (identity.player_id, now),
        )

    async def touch_identity(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        now: str,
    ) -> None:
        await self.ensure_scope(
            session,
            scope=identity.scope,
            group_name=identity.group_name,
            stream_id=identity.stream_id,
            now=now,
        )
        await self.ensure_player(session, identity=identity, now=now)
        # 每次相关操作最多推进三趟旅行；与业务共用事务，到期后先释放猪猪。
        await DispatchRepository().settle_elapsed(session, identity.player_id, now)
