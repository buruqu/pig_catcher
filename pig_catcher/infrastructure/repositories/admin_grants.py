"""把管理奖励写入各玩法正在使用的库存；外层服务负责统一事务、审计和重放。"""

from __future__ import annotations

from ...domain.admin_grants import GRANT_RESOURCES_BY_ID, GrantResource, validate_grant_quantity
from ...domain.dispatch import MATERIAL_SCALE
from ...domain.errors import DomainValidationError
from ...domain.feature_shop import FEATURE_SHOP_PRODUCTS_BY_ID
from ...domain.item_bag import COUPONS
from ..database import DatabaseSession
from .achievements import AchievementRepository
from .battle import BattleRepository
from .economy import EconomyRepository
from .item_bag import ItemBagRepository
from .materials import MaterialRepository


class AdminGrantRepository:
    async def grant(
        self,
        session: DatabaseSession,
        *,
        resource: GrantResource,
        player_id: str,
        scope_id: str,
        quantity: int,
        source_id: str,
        now: str,
        now_ms: int,
    ) -> dict[str, object]:
        validate_grant_quantity(quantity)
        if GRANT_RESOURCES_BY_ID.get(resource.resource_id) != resource:
            raise DomainValidationError("发放资源不在正式目录中。")
        owner = await session.fetch_one("SELECT scope_id FROM players WHERE player_id=?", (player_id,))
        if owner is None or owner[0] != scope_id:
            raise DomainValidationError("发放目标不属于当前群。")
        key = resource.resource_id
        actual = quantity
        if resource.storage == "item":
            after = await EconomyRepository().add_item_inventory(
                session,
                player_id=player_id,
                item_id=key,
                quantity=quantity,
                now=now,
            )
        elif resource.storage == "reward":
            if key in COUPONS:
                result = await ItemBagRepository().grant_coupon(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    coupon_id=key,
                    quantity=quantity,
                    source_id=source_id,
                    source_kind="admin",
                    now=now,
                )
                after = int(result["remaining"])
            else:
                await AchievementRepository().grant_reward(
                    session,
                    player_id=player_id,
                    reward_type=resource.reward_type,
                    reward_id=key,
                    quantity=quantity,
                    now=now,
                )
                row = await session.fetch_one(
                    "SELECT quantity FROM achievement_reward_inventory "
                    "WHERE player_id=? AND reward_type=? AND reward_id=?",
                    (player_id, resource.reward_type, key),
                )
                after = int(row[0])
        elif resource.storage == "material":
            after = await MaterialRepository().change(
                session,
                player_id=player_id,
                scope_id=scope_id,
                material_id=key,
                delta_units=quantity * MATERIAL_SCALE,
                source_kind="admin-grant",
                source_id=source_id,
                entry_key=f"admin:{source_id}:{player_id}:{key}",
                now=now,
            )
        elif resource.storage == "feature":
            product = FEATURE_SHOP_PRODUCTS_BY_ID[key]
            # 表名只能来自固定分支；目录和用户输入都不能拼接任意SQL标识符。
            table = {"dispatch": "dispatch_tools", "tour": "tour_tools", "battle": "battle_tools"}[product.system]
            if product.system == "battle":
                await BattleRepository().tool_change(
                    session,
                    player_id,
                    product.tool_id,
                    quantity,
                    reason="admin-grant",
                    source=source_id,
                    key=f"admin:{source_id}:{player_id}:{key}",
                    now_ms=now_ms,
                )
            else:
                await session.execute(
                    f"INSERT INTO {table}(player_id,tool_id,quantity) VALUES(?,?,?) "
                    f"ON CONFLICT(player_id,tool_id) DO UPDATE SET quantity={table}.quantity+excluded.quantity",
                    (player_id, product.tool_id, quantity),
                )
            row = await session.fetch_one(
                f"SELECT quantity FROM {table} WHERE player_id=? AND tool_id=?",
                (player_id, product.tool_id),
            )
            after = int(row[0])
        elif resource.storage == "upgrade":
            row = await session.fetch_one(
                "SELECT level FROM upgrades WHERE player_id=? AND upgrade_type=?",
                (player_id, key),
            )
            before = int(row[0]) if row else 0
            after = min(10, before + quantity)
            actual = after - before
            if actual:
                await session.execute(
                    "INSERT INTO upgrades(player_id,upgrade_type,level,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(player_id,upgrade_type) DO UPDATE SET "
                    "level=excluded.level,updated_at=excluded.updated_at",
                    (player_id, key, after, now),
                )
        else:
            raise DomainValidationError("发放资源没有对应库存。")
        if not 0 <= after <= 9_000_000_000_000_000:
            raise DomainValidationError("发放后的库存超出安全整数范围，本批次已撤销。")
        return {"quantity_before": after - actual, "quantity_after": after, "granted_quantity": actual}
