"""Persistent permits and exact-scope group technique state."""

from __future__ import annotations

from ...domain.special_content import (
    GROUP_TECHNIQUE_IDS,
    PERMIT_TECHNIQUE_IDS,
    TECHNIQUE_HOLLOW_PURPLE,
    TECHNIQUE_LAPSE_BLUE,
    TECHNIQUE_REVERSAL_RED,
)
from ..database import DatabaseSession


class TechniqueRepository:
    """Store JJK technique state without owning the surrounding transaction."""

    @staticmethod
    def _validate_permit_id(technique_id: str) -> str:
        normalized = str(technique_id or "").strip()
        if normalized not in PERMIT_TECHNIQUE_IDS:
            raise ValueError("未知术式资格。")
        return normalized

    async def grant_permit(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        technique_id: str,
        uses: int,
        now: str,
    ) -> int:
        normalized = self._validate_permit_id(technique_id)
        if int(uses) <= 0:
            raise ValueError("术式资格发放次数必须大于零。")
        await session.execute(
            """
            INSERT INTO player_technique_permits(
                player_id, technique_id, granted_uses, consumed_uses,
                created_at, updated_at
            )
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(player_id, technique_id) DO UPDATE SET
                granted_uses = player_technique_permits.granted_uses
                    + excluded.granted_uses,
                updated_at = excluded.updated_at
            """,
            (player_id, normalized, int(uses), now, now),
        )
        return await self.available_permits(
            session,
            player_id=player_id,
            technique_id=normalized,
        )

    async def available_permits(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        technique_id: str,
    ) -> int:
        normalized = self._validate_permit_id(technique_id)
        row = await session.fetch_one(
            """
            SELECT granted_uses - consumed_uses AS available
            FROM player_technique_permits
            WHERE player_id = ? AND technique_id = ?
            """,
            (player_id, normalized),
        )
        return int(row["available"] or 0) if row is not None else 0

    async def consume_permit(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        technique_id: str,
        now: str,
    ) -> bool:
        normalized = self._validate_permit_id(technique_id)
        cursor = await session.execute(
            """
            UPDATE player_technique_permits
            SET consumed_uses = consumed_uses + 1, updated_at = ?
            WHERE player_id = ?
              AND technique_id = ?
              AND consumed_uses < granted_uses
            """,
            (now, player_id, normalized),
        )
        return cursor.rowcount == 1

    async def active_group_effect(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT effect.effect_entry_id, effect.scope_id,
                   effect.technique_id, effect.source_player_id,
                   effect.remaining_uses, effect.total_uses,
                   effect.created_at, player.display_name AS source_display_name
            FROM group_technique_effects AS effect
            JOIN players AS player
              ON player.player_id = effect.source_player_id
            WHERE effect.scope_id = ?
              AND effect.status = 'active'
              AND effect.remaining_uses > 0
            ORDER BY effect.created_at, effect.effect_entry_id
            LIMIT 1
            """,
            (scope_id,),
        )
        return dict(row) if row is not None else None

    async def insert_group_effect(
        self,
        session: DatabaseSession,
        *,
        effect_entry_id: str,
        scope_id: str,
        technique_id: str,
        source_player_id: str,
        uses: int,
        now: str,
    ) -> None:
        normalized = str(technique_id or "").strip()
        if normalized not in GROUP_TECHNIQUE_IDS:
            raise ValueError("未知群体术式。")
        if int(uses) <= 0:
            raise ValueError("群体术式次数必须大于零。")
        await session.execute(
            """
            INSERT INTO group_technique_effects(
                effect_entry_id, scope_id, technique_id, source_player_id,
                remaining_uses, total_uses, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                effect_entry_id,
                scope_id,
                normalized,
                source_player_id,
                int(uses),
                int(uses),
                now,
                now,
            ),
        )

    async def consume_group_effect_use(
        self,
        session: DatabaseSession,
        *,
        effect_entry_id: str,
        now: str,
    ) -> int:
        cursor = await session.execute(
            """
            UPDATE group_technique_effects
            SET remaining_uses = remaining_uses - 1,
                status = CASE
                    WHEN remaining_uses = 1 THEN 'completed'
                    ELSE status
                END,
                updated_at = ?
            WHERE effect_entry_id = ?
              AND status = 'active'
              AND remaining_uses > 0
            """,
            (now, effect_entry_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("群体术式次数已被其他结算消耗。")
        row = await session.fetch_one(
            """
            SELECT remaining_uses
            FROM group_technique_effects
            WHERE effect_entry_id = ?
            """,
            (effect_entry_id,),
        )
        if row is None:
            raise RuntimeError("群体术式结算后无法读取。")
        return int(row["remaining_uses"])

    async def record_color_activation(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        technique_id: str,
        now: str,
    ) -> int:
        """Record one Blue/Red activation and grant each newly completed pair."""

        normalized = str(technique_id or "").strip()
        if normalized not in {TECHNIQUE_LAPSE_BLUE, TECHNIQUE_REVERSAL_RED}:
            return 0
        await session.execute(
            """
            INSERT INTO player_technique_progress(
                player_id, blue_activations, red_activations,
                purple_unlocks, created_at, updated_at
            )
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                blue_activations = player_technique_progress.blue_activations
                    + excluded.blue_activations,
                red_activations = player_technique_progress.red_activations
                    + excluded.red_activations,
                updated_at = excluded.updated_at
            """,
            (
                player_id,
                1 if normalized == TECHNIQUE_LAPSE_BLUE else 0,
                1 if normalized == TECHNIQUE_REVERSAL_RED else 0,
                now,
                now,
            ),
        )
        row = await session.fetch_one(
            """
            SELECT blue_activations, red_activations, purple_unlocks
            FROM player_technique_progress
            WHERE player_id = ?
            """,
            (player_id,),
        )
        if row is None:
            raise RuntimeError("术式组合进度写入后无法读取。")
        paired = min(int(row["blue_activations"]), int(row["red_activations"]))
        unlocked = int(row["purple_unlocks"])
        newly_unlocked = max(0, paired - unlocked)
        if newly_unlocked:
            await session.execute(
                """
                UPDATE player_technique_progress
                SET purple_unlocks = ?, updated_at = ?
                WHERE player_id = ?
                """,
                (paired, now, player_id),
            )
            await self.grant_permit(
                session,
                player_id=player_id,
                technique_id=TECHNIQUE_HOLLOW_PURPLE,
                uses=newly_unlocked,
                now=now,
            )
        return newly_unlocked
