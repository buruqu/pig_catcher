"""旧业务和新活动共用的占用筛选，批量操作在选取/排序之前排除忙碌猪。"""

from ...domain.errors import AssetStateConflictError
from ..database import DatabaseSession


def unoccupied_clause(asset_kind: str, alias: str = "pig_instances") -> str:
    if asset_kind == "food":
        return ""
    if asset_kind != "pig" or alias not in {"pig_instances", "instance", "candidate", "p"}:
        raise ValueError("Invalid static asset SQL selector")
    return (
        f"AND NOT EXISTS(SELECT 1 FROM asset_occupancies busy WHERE busy.pig_instance_id={alias}.pig_instance_id) "
        f"AND NOT EXISTS(SELECT 1 FROM tour_protections protected "
        f"WHERE protected.pig_instance_id={alias}.pig_instance_id AND protected.protected=1) "
        f"AND NOT EXISTS(SELECT 1 FROM battle_protections protected "
        f"WHERE protected.pig_instance_id={alias}.pig_instance_id AND protected.protected=1)"
    )


async def require_unoccupied(session: DatabaseSession, pig_instance_id: str) -> None:
    row = await session.fetch_one("SELECT purpose FROM asset_occupancies WHERE pig_instance_id=?", (pig_instance_id,))
    if row is not None:
        name = {"dispatch": "派遣", "tour": "巡演", "battle": "对战"}.get(row["purpose"], "活动")
        raise AssetStateConflictError(f"这只猪正在{name}中，不能做菜、售卖、赠送、交易或删除；请等待归来或先召回。")
    protected = await session.fetch_one(
        "SELECT 1 FROM tour_protections WHERE pig_instance_id=? AND protected=1", (pig_instance_id,)
    )
    if protected:
        raise AssetStateConflictError("这只猪受乐队保护；请先 /我的猪猪乐队 解除保护 猪名#编号，并确认移出阵容。")
    if await session.fetch_one(
        "SELECT 1 FROM battle_protections WHERE pig_instance_id=? AND protected=1", (pig_instance_id,)
    ):
        raise AssetStateConflictError("这只猪受战斗保护；请先 /战斗猪 解除保护 猪名#编号，再 /战斗猪 确认。")
