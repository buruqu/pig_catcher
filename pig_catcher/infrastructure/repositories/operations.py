"""生产巡检所需的只读聚合查询。"""

from __future__ import annotations

from ..database import DatabaseSession


class OperationsRepository:
    """读取全库账本一致性与当前启用素材路径，不执行修复。"""

    async def balance_mismatch_count(self, session: DatabaseSession) -> int:
        row = await session.fetch_one(
            """
            SELECT COUNT(*) AS mismatch_count
            FROM players AS player
            LEFT JOIN (
                SELECT player_id, COALESCE(SUM(amount), 0) AS ledger_total
                FROM currency_ledger
                GROUP BY player_id
            ) AS ledger ON ledger.player_id = player.player_id
            WHERE player.coin_balance <> COALESCE(ledger.ledger_total, 0)
            """
        )
        return int(row["mismatch_count"]) if row is not None else 0

    async def active_asset_paths(self, session: DatabaseSession) -> tuple[str, ...]:
        rows = await session.fetch_all(
            """
            SELECT image_relpath
            FROM pig_templates
            WHERE enabled = 1
            UNION ALL
            SELECT image_relpath
            FROM food_templates
            WHERE enabled = 1
            ORDER BY image_relpath
            """
        )
        return tuple(str(row["image_relpath"]) for row in rows)
