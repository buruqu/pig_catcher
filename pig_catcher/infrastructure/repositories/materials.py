"""共用材料库存与不可变账本；调用者拥有事务，奖品不冒充猪币或资产实例。"""

from __future__ import annotations

from ...domain.dispatch import MATERIAL_SCALE, MATERIALS, DispatchError
from ..database import DatabaseSession


class MaterialRepository:
    async def balances(self, session: DatabaseSession, player_id: str) -> dict[str, int]:
        rows = await session.fetch_all("SELECT * FROM material_balances WHERE player_id=?", (player_id,))
        return {str(row["material_id"]): int(row["quantity"]) for row in rows}

    async def change(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        material_id: str,
        delta_units: int,
        source_kind: str,
        source_id: str,
        entry_key: str,
        now: str,
    ) -> int:
        if material_id not in MATERIALS or not isinstance(delta_units, int) or delta_units == 0:
            raise DispatchError("材料变动参数不合法。")
        existing = await session.fetch_one("SELECT * FROM material_ledger WHERE entry_key=?", (entry_key,))
        if existing is not None:
            if (
                existing["player_id"],
                existing["scope_id"],
                existing["material_id"],
                existing["delta_units"],
                existing["source_kind"],
                existing["source_id"],
            ) != (player_id, scope_id, material_id, delta_units, source_kind, source_id):
                raise DispatchError("材料账本幂等键与原操作不一致。")
            return int(existing["balance_units"]) // MATERIAL_SCALE
        owner = await session.fetch_one("SELECT scope_id FROM players WHERE player_id=?", (player_id,))
        if owner is None or str(owner["scope_id"]) != scope_id:
            raise DispatchError("材料归属与群范围不一致。")
        row = await session.fetch_one(
            "SELECT quantity,remainder_units FROM material_balances WHERE player_id=? AND material_id=?",
            (player_id, material_id),
        )
        before = int(row[0]) * MATERIAL_SCALE + int(row[1]) if row else 0
        after = before + delta_units
        if after < 0:
            raise DispatchError(f"{MATERIALS[material_id]}不足，本次操作没有扣除任何材料。")
        quantity, remainder = divmod(after, MATERIAL_SCALE)
        await session.execute(
            """INSERT INTO material_balances VALUES(?,?,?,?) ON CONFLICT(player_id,material_id)
            DO UPDATE SET quantity=excluded.quantity,remainder_units=excluded.remainder_units""",
            (player_id, material_id, quantity, remainder),
        )
        await session.execute(
            "INSERT INTO material_ledger VALUES(?,?,?,?,?,?,?,?,?)",
            (entry_key, player_id, scope_id, material_id, delta_units, after, source_kind, source_id, now),
        )
        return quantity

    async def reconcile(self, session: DatabaseSession) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """WITH keys AS (
                SELECT player_id,material_id FROM material_balances UNION
                SELECT player_id,material_id FROM material_ledger
            ), totals AS (
                SELECT player_id,material_id,SUM(delta_units) AS total FROM material_ledger
                GROUP BY player_id,material_id
            )
            SELECT k.player_id,k.material_id,b.quantity,b.remainder_units,COALESCE(t.total,0) AS total
            FROM keys k LEFT JOIN material_balances b USING(player_id,material_id)
            LEFT JOIN totals t USING(player_id,material_id)
            WHERE b.player_id IS NULL OR b.quantity*10000000+b.remainder_units != COALESCE(t.total,0)"""
        )
        return [dict(row) for row in rows]
