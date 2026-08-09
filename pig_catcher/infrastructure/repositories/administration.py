"""Repository primitives for audited current-group administrator commands."""

from __future__ import annotations

from ...domain.enums import AssetKind
from ..database import DatabaseSession


class AdministrationRepository:
    """Read and mutate administrator-owned state without owning transactions."""

    async def player_by_platform_user_id(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        platform_user_id: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT player_id, scope_id, platform_user_id, display_name,
                   coin_balance, experience, created_at, updated_at
            FROM players
            WHERE scope_id = ? AND platform_user_id = ?
            """,
            (scope_id, platform_user_id),
        )
        return dict(row) if row is not None else None

    async def players_in_scope(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT player_id, platform_user_id, display_name, coin_balance
            FROM players
            WHERE scope_id = ?
            ORDER BY created_at, player_id
            """,
            (scope_id,),
        )
        return [dict(row) for row in rows]

    async def eligible_templates(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        asset_kind: AssetKind,
        selector: str,
    ) -> list[dict[str, object]]:
        table = "pig_templates" if asset_kind is AssetKind.PIG else "food_templates"
        allowed_table = (
            "scope_pig_templates"
            if asset_kind is AssetKind.PIG
            else "scope_food_templates"
        )
        normalized = "".join(str(selector or "").split())
        rows = await session.fetch_all(
            f"""
            SELECT template.*
            FROM {table} AS template
            LEFT JOIN {allowed_table} AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            WHERE template.enabled = 1
              AND (
                  template.template_id = ?
                  OR template.display_name = ?
                  OR REPLACE(REPLACE(template.display_name, ' ', ''), '　', '')
                     = ? COLLATE NOCASE
              )
              AND (
                  template.scope_type = 'common'
                  OR (
                      template.scope_type = 'group'
                      AND allowed.authorized = 1
                      AND allowed.consent_status = 'granted'
                  )
              )
            ORDER BY template.template_id
            """,
            (scope_id, selector, selector, normalized),
        )
        return [dict(row) for row in rows]

    async def active_asset_by_selector(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        owner_player_id: str,
        asset_kind: AssetKind,
        display_name: str,
        short_code: str,
    ) -> dict[str, object] | None:
        table, id_column = self._asset_table(asset_kind)
        row = await session.fetch_one(
            f"""
            SELECT instance.*, template.description
            FROM {table} AS instance
            JOIN {'pig_templates' if asset_kind is AssetKind.PIG else 'food_templates'} AS template
              ON template.template_id = instance.template_id
            WHERE instance.scope_id = ?
              AND instance.owner_player_id = ?
              AND instance.display_name_snapshot = ?
              AND instance.short_code COLLATE NOCASE = ?
              AND instance.state IN ('active', 'locked-for-trade')
            """,
            (scope_id, owner_player_id, display_name, short_code),
        )
        if row is None:
            return None
        result = dict(row)
        result["asset_instance_id"] = str(result[id_column])
        return result

    async def cancel_pending_trade_for_asset(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        asset_kind: AssetKind,
        asset_instance_id: str,
        now: str,
    ) -> int:
        cursor = await session.execute(
            """
            UPDATE trade_offers
            SET status = 'cancelled', resolved_at = ?
            WHERE scope_id = ?
              AND asset_kind = ?
              AND asset_instance_id = ?
              AND status = 'pending'
            """,
            (now, scope_id, asset_kind.value, asset_instance_id),
        )
        return max(int(cursor.rowcount), 0)

    async def mark_asset_admin_removed(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        owner_player_id: str,
        asset_kind: AssetKind,
        asset_instance_id: str,
        now: str,
    ) -> bool:
        table, id_column = self._asset_table(asset_kind)
        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET state = 'admin-removed', locked_trade_id = NULL,
                disposed_at = ?, updated_at = ?
            WHERE {id_column} = ?
              AND scope_id = ?
              AND owner_player_id = ?
              AND state IN ('active', 'locked-for-trade')
            """,
            (now, now, asset_instance_id, scope_id, owner_player_id),
        )
        return cursor.rowcount == 1

    async def clear_showcase_asset(
        self,
        session: DatabaseSession,
        *,
        owner_player_id: str,
        asset_kind: AssetKind,
        asset_instance_id: str,
        now: str,
    ) -> None:
        column = "pig_instance_id" if asset_kind is AssetKind.PIG else "food_instance_id"
        await session.execute(
            f"""
            UPDATE display_preferences
            SET {column} = NULL, updated_at = ?
            WHERE player_id = ? AND {column} = ?
            """,
            (now, owner_player_id, asset_instance_id),
        )

    async def repair_records_after_pig_removal(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        template_id: str,
        pig_instance_id: str,
    ) -> None:
        """Remove invalid references and promote the best non-removed historical pig."""

        await session.execute(
            "DELETE FROM giant_sightings WHERE pig_instance_id = ?",
            (pig_instance_id,),
        )
        await session.execute(
            "DELETE FROM group_records WHERE pig_instance_id = ?",
            (pig_instance_id,),
        )
        await session.execute(
            "DELETE FROM group_global_records WHERE pig_instance_id = ?",
            (pig_instance_id,),
        )
        for record_type, value_column in (("size", "size_value"), ("weight", "weight_value")):
            await session.execute(
                f"""
                INSERT INTO group_records(
                    scope_id, template_id, record_type, pig_instance_id,
                    record_value, player_id, achieved_at
                )
                SELECT scope_id, template_id, ?, pig_instance_id,
                       {value_column}, owner_player_id, acquired_at
                FROM pig_instances
                WHERE scope_id = ?
                  AND template_id = ?
                  AND state <> 'admin-removed'
                ORDER BY {value_column} DESC, acquired_at, pig_instance_id
                LIMIT 1
                ON CONFLICT(scope_id, template_id, record_type) DO NOTHING
                """,
                (record_type, scope_id, template_id),
            )
            await session.execute(
                f"""
                INSERT INTO group_global_records(
                    scope_id, record_type, pig_instance_id, template_id,
                    record_value, player_id, achieved_at
                )
                SELECT scope_id, ?, pig_instance_id, template_id,
                       {value_column}, owner_player_id, acquired_at
                FROM pig_instances
                WHERE scope_id = ?
                  AND state <> 'admin-removed'
                ORDER BY {value_column} DESC, acquired_at, pig_instance_id
                LIMIT 1
                ON CONFLICT(scope_id, record_type) DO NOTHING
                """,
                (record_type, scope_id),
            )

    async def insert_audit_event(
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

    async def delete_restriction(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        restriction_type: str,
    ) -> bool:
        cursor = await session.execute(
            """
            DELETE FROM player_restrictions
            WHERE player_id = ? AND restriction_type = ?
            """,
            (player_id, restriction_type),
        )
        return cursor.rowcount == 1

    async def player_catch_usage_since(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        effective_start: str,
        window_end: str,
    ) -> int:
        row = await session.fetch_one(
            """
            SELECT COUNT(*) AS catch_count
            FROM command_receipts
            WHERE player_id = ?
              AND command_name = 'pig-catcher.catch'
              AND created_at >= ?
              AND created_at < ?
            """,
            (player_id, effective_start, window_end),
        )
        return int(row["catch_count"]) if row is not None else 0

    @staticmethod
    def _asset_table(asset_kind: AssetKind) -> tuple[str, str]:
        if asset_kind is AssetKind.PIG:
            return "pig_instances", "pig_instance_id"
        return "food_instances", "food_instance_id"
