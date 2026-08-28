"""资产短编号的活跃占用与券服务使用的原子改号原语。"""

from __future__ import annotations

from ...domain.enums import AssetKind
from ...domain.errors import AssetStateConflictError, DomainValidationError
from ...domain.short_codes import normalize_short_code
from ..database import DatabaseSession


class AssetCodeRepository:
    """只操作调用方事务，不自行提交、发奖励或消费改号券。"""

    @staticmethod
    async def code_is_occupied(session: DatabaseSession, short_code: str) -> bool:
        """历史实例保留原码，但仅仍持有/交易锁定的实例占用公共编号空间。"""

        row = await session.fetch_one(
            """
            SELECT 1 FROM pig_instances
            WHERE short_code COLLATE NOCASE = ?
              AND state IN ('active', 'locked-for-trade')
            UNION ALL
            SELECT 1 FROM food_instances
            WHERE short_code COLLATE NOCASE = ?
              AND state IN ('active', 'locked-for-trade')
            LIMIT 1
            """,
            (short_code, short_code),
        )
        return row is not None

    async def rename_owned_asset(
        self,
        session: DatabaseSession,
        *,
        asset_kind: AssetKind,
        asset_instance_id: str,
        owner_player_id: str,
        scope_id: str,
        new_short_code: str,
        now: str,
    ) -> dict[str, str]:
        """改一个当前群自有闲置实例的编号；券消费与审计必须在同一事务内。

        收藏不是处置，因此可以改号且保留收藏状态。交易、派遣、巡演、对战
        占用和养成保护均需先解除，防止活动快照与正在输入的资产选择器错位。
        结果包含不可变 UUID、原码、新码，供调用方写入不可变审计和幂等回执。
        """

        if not isinstance(asset_kind, AssetKind) or asset_kind not in (AssetKind.PIG, AssetKind.FOOD):
            raise DomainValidationError("仅支持修改猪猪或美食的编号。")
        canonical_code = normalize_short_code(new_short_code)
        table = "pig_instances" if asset_kind is AssetKind.PIG else "food_instances"
        id_column = "pig_instance_id" if asset_kind is AssetKind.PIG else "food_instance_id"
        row = await session.fetch_one(
            f"""
            SELECT {id_column}, short_code, display_name_snapshot, state, locked_trade_id
            FROM {table}
            WHERE {id_column} = ? AND owner_player_id = ? AND scope_id = ?
            """,
            (asset_instance_id, owner_player_id, scope_id),
        )
        if row is None or row["state"] != "active" or row["locked_trade_id"] is not None:
            raise AssetStateConflictError("该资产不在当前群的有效背包中，或已被交易锁定，不能改号。")
        if asset_kind is AssetKind.PIG:
            protected = await session.fetch_one(
                """
                SELECT 1 FROM asset_occupancies WHERE pig_instance_id = ?
                UNION ALL
                SELECT 1 FROM tour_protections WHERE pig_instance_id = ? AND protected = 1
                UNION ALL
                SELECT 1 FROM battle_protections WHERE pig_instance_id = ? AND protected = 1
                LIMIT 1
                """,
                (asset_instance_id, asset_instance_id, asset_instance_id),
            )
            if protected is not None:
                raise AssetStateConflictError("这只猪正在活动中或受养成保护，请先结束活动或明确解除保护后再改号。")
        old_short_code = str(row["short_code"])
        if old_short_code.upper() == canonical_code:
            raise DomainValidationError("新编号与原编号相同（不区分大小写），未使用编号修改券。")
        if await self.code_is_occupied(session, canonical_code):
            raise DomainValidationError(f"短编号 {canonical_code} 已被仍持有的猪猪或美食占用。")

        cursor = await session.execute(
            f"""
            UPDATE {table}
            SET short_code = ?, updated_at = ?
            WHERE {id_column} = ? AND owner_player_id = ? AND scope_id = ?
              AND state = 'active' AND locked_trade_id IS NULL
              AND short_code COLLATE NOCASE = ?
            """,
            (canonical_code, now, asset_instance_id, owner_player_id, scope_id, old_short_code),
        )
        if cursor.rowcount != 1:
            raise AssetStateConflictError("资产状态已改变，本次改号未结算。")
        return {
            "asset_instance_id": asset_instance_id,
            "asset_kind": asset_kind.value,
            "display_name": str(row["display_name_snapshot"]),
            "old_short_code": old_short_code,
            "new_short_code": canonical_code,
        }
