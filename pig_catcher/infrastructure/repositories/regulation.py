"""自动监管案件、提醒、临时限制与流转证据仓储。"""

from __future__ import annotations

from collections.abc import Sequence

from ..database import DatabaseSession


def _normalized(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


class RegulationRepository:
    """只执行 SQL，不拥有事务边界或策略判断。"""

    async def transfer_rows(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        since: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT
                event.transfer_event_id,
                event.asset_kind,
                event.asset_instance_id,
                event.from_player_id,
                event.to_player_id,
                event.transfer_type,
                event.trade_id,
                event.created_at,
                COALESCE(pig.rarity, food.rarity, 0) AS rarity,
                COALESCE(pig.official_value, food.official_value, 0) AS official_value,
                COALESCE(food.source_pig_instance_id, '') AS source_pig_instance_id,
                offer.price
            FROM asset_transfer_events AS event
            LEFT JOIN pig_instances AS pig
              ON event.asset_kind = 'pig'
             AND pig.pig_instance_id = event.asset_instance_id
            LEFT JOIN food_instances AS food
              ON event.asset_kind = 'food'
             AND food.food_instance_id = event.asset_instance_id
            LEFT JOIN trade_offers AS offer
              ON offer.trade_id = event.trade_id
             AND offer.status = 'accepted'
            WHERE event.scope_id = ? AND event.created_at >= ?
            ORDER BY event.created_at, event.transfer_event_id
            """,
            (scope_id, since),
        )
        return [dict(row) for row in rows]

    async def asset_lineage(
        self,
        session: DatabaseSession,
        *,
        asset_kind: str,
        asset_instance_id: str,
    ) -> str:
        if asset_kind == "pig":
            return f"pig-lineage:{asset_instance_id}"
        row = await session.fetch_one(
            """
            SELECT source_pig_instance_id
            FROM food_instances
            WHERE food_instance_id = ?
            """,
            (asset_instance_id,),
        )
        source_pig_id = str(row["source_pig_instance_id"] or "") if row else ""
        return (
            f"pig-lineage:{source_pig_id}"
            if source_pig_id
            else f"food:{asset_instance_id}"
        )

    async def latest_related_case(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        target_player_ids: Sequence[str],
        target_signature: str,
        since: str,
    ) -> dict[str, object] | None:
        targets = _normalized(target_player_ids)
        if not targets:
            return None
        placeholders = ",".join("?" for _ in targets)
        row = await session.fetch_one(
            f"""
            SELECT DISTINCT incident.*
            FROM anti_abuse_cases AS incident
            JOIN anti_abuse_case_members AS member
              ON member.case_id = incident.case_id
             AND member.role IN ('target', 'active-trader')
            WHERE incident.scope_id = ?
              AND incident.status NOT IN ('closed', 'dismissed')
              AND incident.updated_at >= ?
              AND (
                  incident.target_signature = ?
                  OR member.player_id IN ({placeholders})
              )
            ORDER BY incident.updated_at DESC, incident.case_id
            LIMIT 1
            """,
            (scope_id, since, target_signature, *targets),
        )
        return dict(row) if row is not None else None

    async def case_by_id_prefix(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        case_id_prefix: str,
        visible_since: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, object]]:
        clauses = ["scope_id = ?", "case_id LIKE ?"]
        parameters: list[object] = [scope_id, f"{case_id_prefix.strip()}%"]
        if active_only:
            clauses.append("status NOT IN ('closed', 'dismissed')")
        if visible_since is not None:
            clauses.append("created_at >= ?")
            parameters.append(visible_since)
        rows = await session.fetch_all(
            f"""
            SELECT *
            FROM anti_abuse_cases
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, case_id
            LIMIT 3
            """,
            tuple(parameters),
        )
        return [dict(row) for row in rows]

    async def insert_case(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        scope_id: str,
        target_signature: str,
        target_player_ids_json: str,
        score: int,
        ruleset_version: int,
        evidence_json: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO anti_abuse_cases(
                case_id, scope_id, target_signature, target_player_ids_json,
                status, score, ruleset_version, evidence_json,
                created_at, updated_at, last_evidence_at, resolved_at
            )
            VALUES (?, ?, ?, ?, 'watching', ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                case_id,
                scope_id,
                target_signature,
                target_player_ids_json,
                score,
                ruleset_version,
                evidence_json,
                now,
                now,
                now,
            ),
        )

    async def update_case(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        target_signature: str,
        target_player_ids_json: str,
        status: str,
        score: int,
        evidence_json: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            UPDATE anti_abuse_cases
            SET target_signature = ?, target_player_ids_json = ?, status = ?,
                score = ?, evidence_json = ?, updated_at = ?, last_evidence_at = ?
            WHERE case_id = ?
            """,
            (
                target_signature,
                target_player_ids_json,
                status,
                score,
                evidence_json,
                now,
                now,
                case_id,
            ),
        )

    async def close_case(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        status: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE anti_abuse_cases
            SET status = ?, resolved_at = ?, updated_at = ?
            WHERE case_id = ? AND status NOT IN ('closed', 'dismissed')
            """,
            (status, now, now, case_id),
        )
        return cursor.rowcount == 1

    async def upsert_member(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        player_id: str,
        role: str,
        active_participant: bool,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO anti_abuse_case_members(
                case_id, player_id, role, active_participant,
                warning_served_at, incident_count, last_incident_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, ?)
            ON CONFLICT(case_id, player_id) DO UPDATE SET
                role = CASE
                    WHEN anti_abuse_case_members.role = 'active-trader'
                      OR excluded.role = 'active-trader'
                    THEN 'active-trader'
                    WHEN anti_abuse_case_members.role = 'relay'
                      OR excluded.role = 'relay'
                    THEN 'relay'
                    WHEN anti_abuse_case_members.role = 'source'
                      OR excluded.role = 'source'
                    THEN 'source'
                    ELSE 'target'
                END,
                active_participant = MAX(
                    anti_abuse_case_members.active_participant,
                    excluded.active_participant
                ),
                updated_at = excluded.updated_at
            """,
            (case_id, player_id, role, int(active_participant), now, now),
        )

    async def member(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        player_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT * FROM anti_abuse_case_members
            WHERE case_id = ? AND player_id = ?
            """,
            (case_id, player_id),
        )
        return dict(row) if row is not None else None

    async def members(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT member.*, player.platform_user_id, player.display_name
            FROM anti_abuse_case_members AS member
            JOIN players AS player ON player.player_id = member.player_id
            WHERE member.case_id = ?
            ORDER BY member.role, player.display_name, member.player_id
            """,
            (case_id,),
        )
        return [dict(row) for row in rows]

    async def increment_incident(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        player_id: str,
        now: str,
    ) -> int:
        await session.execute(
            """
            UPDATE anti_abuse_case_members
            SET incident_count = incident_count + 1,
                last_incident_at = ?, updated_at = ?
            WHERE case_id = ? AND player_id = ? AND warning_served_at IS NOT NULL
            """,
            (now, now, case_id, player_id),
        )
        row = await self.member(session, case_id=case_id, player_id=player_id)
        return int(row["incident_count"]) if row is not None else 0

    async def mark_warning_served(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        player_id: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            UPDATE anti_abuse_case_members
            SET warning_served_at = COALESCE(warning_served_at, ?), updated_at = ?
            WHERE case_id = ? AND player_id = ?
            """,
            (now, now, case_id, player_id),
        )

    async def player_rows(
        self,
        session: DatabaseSession,
        *,
        player_ids: Sequence[str],
    ) -> list[dict[str, object]]:
        players = _normalized(player_ids)
        if not players:
            return []
        placeholders = ",".join("?" for _ in players)
        rows = await session.fetch_all(
            f"""
            SELECT
                player.player_id,
                player.platform_user_id,
                player.display_name,
                player.scope_id,
                player.created_at,
                COALESCE(statistic.total_catches, 0) AS total_catches,
                COALESCE(statistic.total_cooks, 0) AS total_cooks
            FROM players AS player
            LEFT JOIN player_statistics AS statistic
              ON statistic.player_id = player.player_id
            WHERE player.player_id IN ({placeholders})
            ORDER BY player.player_id
            """,
            players,
        )
        return [dict(row) for row in rows]

    async def insert_notice(
        self,
        session: DatabaseSession,
        *,
        notice_id: str,
        case_id: str,
        player_id: str,
        stage: str,
        incident_number: int,
        message_text: str,
        source_operation_key: str,
        now: str,
    ) -> str:
        await session.execute(
            """
            INSERT INTO anti_abuse_notices(
                notice_id, case_id, player_id, stage, incident_number,
                message_text, status, source_operation_key, error_text,
                created_at, updated_at, sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, '', ?, ?, NULL)
            ON CONFLICT(case_id, player_id, stage, incident_number) DO NOTHING
            """,
            (
                notice_id,
                case_id,
                player_id,
                stage,
                incident_number,
                message_text,
                source_operation_key,
                now,
                now,
            ),
        )
        row = await session.fetch_one(
            """
            SELECT notice_id FROM anti_abuse_notices
            WHERE case_id = ? AND player_id = ? AND stage = ? AND incident_number = ?
            """,
            (case_id, player_id, stage, incident_number),
        )
        if row is None:
            raise RuntimeError("监管提醒写入后无法读取。")
        return str(row["notice_id"])

    async def notice(
        self,
        session: DatabaseSession,
        *,
        notice_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT notice.*, player.display_name, player.platform_user_id
            FROM anti_abuse_notices AS notice
            JOIN players AS player ON player.player_id = notice.player_id
            WHERE notice.notice_id = ?
            """,
            (notice_id,),
        )
        return dict(row) if row is not None else None

    async def claim_notice(
        self,
        session: DatabaseSession,
        *,
        notice_id: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE anti_abuse_notices
            SET status = 'claimed', updated_at = ?
            WHERE notice_id = ? AND status IN ('pending', 'failed')
              AND EXISTS (
                  SELECT 1
                  FROM anti_abuse_cases AS incident
                  WHERE incident.case_id = anti_abuse_notices.case_id
                    AND incident.status NOT IN ('closed', 'dismissed')
              )
            """,
            (now, notice_id),
        )
        return cursor.rowcount == 1

    async def requeue_stale_claimed_notice(
        self,
        session: DatabaseSession,
        *,
        notice_id: str,
        stale_before: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE anti_abuse_notices
            SET status = 'failed', error_text = '发送领取超时，允许重新投递',
                updated_at = ?
            WHERE notice_id = ? AND status = 'claimed' AND updated_at <= ?
            """,
            (now, notice_id, stale_before),
        )
        return cursor.rowcount == 1

    async def mark_notice_sent(
        self,
        session: DatabaseSession,
        *,
        notice_id: str,
        now: str,
    ) -> dict[str, object] | None:
        row = await self.notice(session, notice_id=notice_id)
        if row is None:
            return None
        cursor = await session.execute(
            """
            UPDATE anti_abuse_notices
            SET status = 'sent', sent_at = ?, error_text = '', updated_at = ?
            WHERE notice_id = ? AND status = 'claimed'
            """,
            (now, now, notice_id),
        )
        return row if cursor.rowcount == 1 else None

    async def mark_notice_failed(
        self,
        session: DatabaseSession,
        *,
        notice_id: str,
        error_text: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE anti_abuse_notices
            SET status = 'failed', error_text = ?, updated_at = ?
            WHERE notice_id = ? AND status = 'claimed'
            """,
            (error_text[:1000], now, notice_id),
        )
        return cursor.rowcount == 1

    async def expire_holds(self, session: DatabaseSession, *, now: str) -> int:
        cursor = await session.execute(
            """
            UPDATE anti_abuse_holds
            SET status = 'expired', updated_at = ?
            WHERE status = 'active' AND expires_at <= ?
            """,
            (now, now),
        )
        return max(int(cursor.rowcount), 0)

    async def active_holds(
        self,
        session: DatabaseSession,
        *,
        player_ids: Sequence[str],
        hold_types: Sequence[str],
        now: str,
    ) -> list[dict[str, object]]:
        players = _normalized(player_ids)
        types = _normalized(hold_types)
        if not players or not types:
            return []
        player_marks = ",".join("?" for _ in players)
        type_marks = ",".join("?" for _ in types)
        rows = await session.fetch_all(
            f"""
            SELECT hold.*, incident.scope_id
            FROM anti_abuse_holds AS hold
            JOIN anti_abuse_cases AS incident ON incident.case_id = hold.case_id
            WHERE hold.player_id IN ({player_marks})
              AND hold.hold_type IN ({type_marks})
              AND hold.status = 'active'
              AND hold.starts_at <= ? AND hold.expires_at > ?
            ORDER BY hold.expires_at DESC, hold.hold_id
            """,
            (*players, *types, now, now),
        )
        return [dict(row) for row in rows]

    async def plugin_hold_history(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        since: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT * FROM anti_abuse_holds
            WHERE player_id = ? AND hold_type = 'plugin' AND starts_at >= ?
            ORDER BY starts_at DESC, hold_id
            """,
            (player_id, since),
        )
        return [dict(row) for row in rows]

    async def insert_hold(
        self,
        session: DatabaseSession,
        *,
        hold_id: str,
        case_id: str,
        player_id: str,
        hold_type: str,
        sequence_number: int,
        starts_at: str,
        expires_at: str,
        reason: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO anti_abuse_holds(
                hold_id, case_id, player_id, hold_type, sequence_number,
                status, starts_at, expires_at, reason,
                created_at, updated_at, released_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(case_id, player_id, hold_type, sequence_number) DO NOTHING
            """,
            (
                hold_id,
                case_id,
                player_id,
                hold_type,
                sequence_number,
                starts_at,
                expires_at,
                reason,
                now,
                now,
            ),
        )

    async def release_case_holds(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
        now: str,
    ) -> int:
        cursor = await session.execute(
            """
            UPDATE anti_abuse_holds
            SET status = 'released', released_at = ?, updated_at = ?
            WHERE case_id = ? AND status = 'active'
            """,
            (now, now, case_id),
        )
        return max(int(cursor.rowcount), 0)

    async def insert_event(
        self,
        session: DatabaseSession,
        *,
        event_id: str,
        case_id: str,
        scope_id: str,
        player_id: str | None,
        event_type: str,
        score: int,
        payload_json: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO anti_abuse_events(
                event_id, case_id, scope_id, player_id,
                event_type, score, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                case_id,
                scope_id,
                player_id,
                event_type,
                score,
                payload_json,
                now,
            ),
        )

    async def insert_admin_audit(
        self,
        session: DatabaseSession,
        *,
        audit_event_id: str,
        scope_id: str,
        actor_user_id: str,
        case_id: str,
        detail_json: str,
        now: str,
        action: str = "automatic-regulation-released",
        object_type: str = "anti-abuse-case",
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
                case_id,
                detail_json,
                now,
            ),
        )

    async def cases_for_reset(
        self,
        session: DatabaseSession,
        *,
        scope_id: str | None = None,
        created_before: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, object]]:
        clauses: list[str] = []
        parameters: list[object] = []
        if scope_id is not None:
            clauses.append("scope_id = ?")
            parameters.append(scope_id)
        if active_only:
            clauses.append("status NOT IN ('closed', 'dismissed')")
        if created_before is not None:
            clauses.append("created_at <= ?")
            parameters.append(created_before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await session.fetch_all(
            f"""
            SELECT *
            FROM anti_abuse_cases
            {where}
            ORDER BY created_at, case_id
            """,
            tuple(parameters),
        )
        return [dict(row) for row in rows]

    async def reset_case_state(
        self,
        session: DatabaseSession,
        *,
        case_ids: Sequence[str],
        now: str,
        notice_reason: str,
    ) -> dict[str, int]:
        normalized = _normalized(case_ids)
        if not normalized:
            return {
                "cases": 0,
                "members": 0,
                "notices": 0,
                "holds": 0,
            }
        placeholders = ",".join("?" for _ in normalized)
        case_cursor = await session.execute(
            f"""
            UPDATE anti_abuse_cases
            SET status = 'dismissed', score = 0,
                resolved_at = COALESCE(resolved_at, ?), updated_at = ?
            WHERE case_id IN ({placeholders})
            """,
            (now, now, *normalized),
        )
        member_cursor = await session.execute(
            f"""
            UPDATE anti_abuse_case_members
            SET warning_served_at = NULL, incident_count = 0,
                last_incident_at = NULL, updated_at = ?
            WHERE case_id IN ({placeholders})
            """,
            (now, *normalized),
        )
        notice_cursor = await session.execute(
            f"""
            UPDATE anti_abuse_notices
            SET status = 'failed', error_text = ?, updated_at = ?
            WHERE case_id IN ({placeholders})
              AND status IN ('pending', 'claimed', 'failed')
            """,
            (notice_reason[:1000], now, *normalized),
        )
        hold_cursor = await session.execute(
            f"""
            UPDATE anti_abuse_holds
            SET status = 'released', released_at = COALESCE(released_at, ?),
                updated_at = ?
            WHERE case_id IN ({placeholders}) AND status = 'active'
            """,
            (now, now, *normalized),
        )
        return {
            "cases": max(int(case_cursor.rowcount), 0),
            "members": max(int(member_cursor.rowcount), 0),
            "notices": max(int(notice_cursor.rowcount), 0),
            "holds": max(int(hold_cursor.rowcount), 0),
        }

    async def list_cases(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        limit: int,
        visible_since: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT incident.*,
                   COUNT(DISTINCT member.player_id) AS member_count,
                   COUNT(DISTINCT CASE WHEN hold.status = 'active' THEN hold.hold_id END)
                       AS active_hold_count
            FROM anti_abuse_cases AS incident
            LEFT JOIN anti_abuse_case_members AS member
              ON member.case_id = incident.case_id
            LEFT JOIN anti_abuse_holds AS hold
              ON hold.case_id = incident.case_id
            WHERE incident.scope_id = ?
              AND incident.status NOT IN ('closed', 'dismissed')
              AND incident.created_at >= ?
            GROUP BY incident.case_id
            ORDER BY incident.updated_at DESC, incident.case_id
            LIMIT ?
            """,
            (scope_id, visible_since, limit),
        )
        return [dict(row) for row in rows]

    async def holds_for_case(
        self,
        session: DatabaseSession,
        *,
        case_id: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT hold.*, player.display_name, player.platform_user_id
            FROM anti_abuse_holds AS hold
            JOIN players AS player ON player.player_id = hold.player_id
            WHERE hold.case_id = ?
            ORDER BY hold.created_at, hold.hold_id
            """,
            (case_id,),
        )
        return [dict(row) for row in rows]


__all__ = ["RegulationRepository"]
