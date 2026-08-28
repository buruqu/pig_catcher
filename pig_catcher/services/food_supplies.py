"""吃菜事务内的补给发放；材料、券、来源记录与吃菜一同提交或一同回滚。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any

from ..domain.dispatch import MATERIAL_SCALE
from ..domain.errors import AssetStateConflictError, FoodEffectError, ReceiptConflictError
from ..domain.food_supplies import FOOD_SUPPLY_PACK, FOOD_SUPPLY_VERSION, resolve_food_supply_pack, resolve_supply_pack
from ..domain.models import CommandIdentity
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.achievements import AchievementRepository
from ..infrastructure.repositories.materials import MaterialRepository

_AUDIT_ACTION = "food-supply-pack-granted"


@dataclass(frozen=True, slots=True)
class FoodSupplyGrantItem:
    kind: str
    reward_id: str
    name: str
    quantity: int
    balance_after: int
    use_hint: str


@dataclass(frozen=True, slots=True)
class FoodSupplyGrant:
    pack_id: str
    title: str
    food_name: str
    items: tuple[FoodSupplyGrantItem, ...]
    replayed: bool = False

    @property
    def summary(self) -> str:
        return "补给已到账：" + "、".join(f"{item.name}×{item.quantity}" for item in self.items) + "。"

    def payload(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "title": self.title,
            "food_name": self.food_name,
            "items": [asdict(item) for item in self.items],
            "summary": self.summary,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FoodSupplyGrant:
        try:
            return cls(
                pack_id=payload["pack_id"],
                title=payload["title"],
                food_name=payload["food_name"],
                items=tuple(FoodSupplyGrantItem(**item) for item in payload["items"]),
            )
        except (KeyError, TypeError) as exc:
            raise ReceiptConflictError("美食补给历史快照不完整，拒绝重复发放。") from exc


def _key(*parts: str) -> str:
    serialized = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return FOOD_SUPPLY_PACK + ":" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def grant_food_supply_pack(
    session: DatabaseSession,
    *,
    identity: CommandIdentity,
    food_instance_id: str,
    source_key: str,
    pack_id: str,
    now: str,
) -> FoodSupplyGrant:
    """在 ``consume_food`` 成功之后调用；本函数不拥有事务，也不自动装备任何券。

    来源键与食物实例各有一个持久化唯一边界。相同请求重放返回首次库存快照；
    换消息重领同一食物、或同消息换食物都拒绝。只复用已有的通用库存与操作表，
    不创建成就、派遣产出、巡演经验或周榜事实。
    """

    pack = resolve_supply_pack(pack_id)
    if not all(isinstance(value, str) and value.strip() for value in (food_instance_id, source_key, now)):
        raise FoodEffectError("美食补给缺少实例、来源键或结算时间。")
    operation_key = _key("source", identity.scope.value, identity.player_id, source_key)
    audit_key = _key("food", food_instance_id)
    request = {
        "scope_id": identity.scope.value,
        "player_id": identity.player_id,
        "food_instance_id": food_instance_id,
        "source_key": source_key,
        "pack_id": pack_id,
    }
    operation = await session.fetch_one(
        "SELECT player_id,operation_type,result_json FROM achievement_operations WHERE operation_key=?",
        (operation_key,),
    )
    audit = await session.fetch_one("SELECT * FROM audit_events WHERE audit_event_id=?", (audit_key,))
    if operation is not None or audit is not None:
        if (
            operation is None
            or audit is None
            or operation["player_id"] != identity.player_id
            or operation["operation_type"] != FOOD_SUPPLY_PACK
            or audit["scope_id"] != identity.scope.value
            or audit["actor_user_id"] != identity.user_id
            or audit["action"] != _AUDIT_ACTION
            or audit["object_type"] != "food"
            or audit["object_id"] != food_instance_id
            or operation["result_json"] != audit["detail_json"]
        ):
            raise ReceiptConflictError("美食补给来源与原结算不一致，不能重复领取。")
        try:
            previous = json.loads(operation["result_json"])
        except (TypeError, ValueError) as exc:
            raise ReceiptConflictError("美食补给历史快照无法读取，拒绝重复发放。") from exc
        if not isinstance(previous, dict) or previous.get("request") != request:
            raise ReceiptConflictError("美食补给来源与原结算不一致，不能重复领取。")
        return replace(FoodSupplyGrant.from_payload(previous.get("result", {})), replayed=True)

    food = await session.fetch_one(
        "SELECT scope_id,owner_player_id,state,rarity,effect_id,effect_params_json "
        "FROM food_instances WHERE food_instance_id=?",
        (food_instance_id,),
    )
    if (
        food is None
        or food["scope_id"] != identity.scope.value
        or food["owner_player_id"] != identity.player_id
        or food["state"] != "consumed"
    ):
        raise AssetStateConflictError("补给只能由当前群本人成功吃下的美食发放。")
    if food["rarity"] != pack.food_rarity or food["effect_id"] != FOOD_SUPPLY_PACK:
        raise FoodEffectError("美食实例与补给套餐不匹配，本次品鉴未结算。")
    try:
        params = json.loads(food["effect_params_json"])
    except (TypeError, ValueError) as exc:
        raise FoodEffectError("美食实例的补给参数无法读取，本次品鉴未结算。") from exc
    if resolve_food_supply_pack(params).pack_id != pack_id:
        raise FoodEffectError("美食实例与补给套餐不匹配，本次品鉴未结算。")

    achievements = AchievementRepository()
    materials = MaterialRepository()
    items = []
    for reward in pack.rewards:
        if reward.kind == "material":
            balance = await materials.change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                material_id=reward.reward_id,
                delta_units=reward.quantity * MATERIAL_SCALE,
                source_kind=FOOD_SUPPLY_PACK,
                source_id=food_instance_id,
                entry_key=f"{operation_key}:material:{reward.reward_id}",
                now=now,
            )
        else:
            await achievements.grant_reward(
                session,
                player_id=identity.player_id,
                reward_type=reward.kind,
                reward_id=reward.reward_id,
                quantity=reward.quantity,
                now=now,
            )
            row = await session.fetch_one(
                "SELECT quantity FROM achievement_reward_inventory "
                "WHERE player_id=? AND reward_type=? AND reward_id=?",
                (identity.player_id, reward.kind, reward.reward_id),
            )
            if row is None:
                raise RuntimeError("美食补给库存写入后未找到，本次品鉴回滚。")
            balance = int(row["quantity"])
        items.append(
            FoodSupplyGrantItem(reward.kind, reward.reward_id, reward.name, reward.quantity, balance, reward.use_hint)
        )
    result = FoodSupplyGrant(pack.pack_id, pack.title, pack.food_name, tuple(items))
    detail_json = json.dumps(
        {"supply_version": FOOD_SUPPLY_VERSION, "request": request, "result": result.payload()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    await achievements.insert_operation(
        session,
        operation_key=operation_key,
        player_id=identity.player_id,
        operation_type=FOOD_SUPPLY_PACK,
        result_json=detail_json,
        now=now,
    )
    await session.execute(
        "INSERT INTO audit_events(audit_event_id,scope_id,actor_user_id,action,"
        "object_type,object_id,detail_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (audit_key, identity.scope.value, identity.user_id, _AUDIT_ACTION, "food", food_instance_id, detail_json, now),
    )
    return result
