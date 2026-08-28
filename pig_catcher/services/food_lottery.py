"""绿芯小猪派的事务内抽奖发奖，不伪造抓猪/做菜行为或再次自动吃掉奖品。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any
from uuid import uuid4

from ..domain.economy import generate_food_attributes, recipe_affinity
from ..domain.errors import AssetStateConflictError, FoodEffectError, ReceiptConflictError
from ..domain.food_lottery import HINA_PIG_TEMPLATE_ID, YILU_LOTTERY, LotteryPrize, choose_lottery_prize, validated_roll
from ..domain.gameplay import generate_pig_attributes
from ..domain.models import CommandIdentity
from ..domain.ports import RandomSource
from ..domain.short_codes import new_short_code, normalize_short_code
from ..domain.special_content import SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.achievements import AchievementRepository
from ..infrastructure.repositories.asset_codes import AssetCodeRepository
from ..infrastructure.repositories.economy import EconomyRepository
from ..infrastructure.repositories.gameplay import GameplayRepository
from ..version import RULESET_VERSION

_AUDIT_ACTION = "food-lottery-granted"


@dataclass(frozen=True, slots=True)
class FoodLotteryItem:
    kind: str
    template_id: str
    name: str
    rarity: int
    instance_id: str
    short_code: str
    value: int
    # 与模板 image_relpath 一致，相对于插件 data_dir；不在发奖事务中读取或发送图片。
    asset_path: str
    media_format: str
    image_fit: str
    is_new: bool


@dataclass(frozen=True, slots=True)
class FoodLotteryGrant:
    prize_id: str
    prize_label: str
    animation: str
    roll: float
    items: tuple[FoodLotteryItem, ...]
    replayed: bool = False

    @property
    def summary(self) -> str:
        if self.prize_id == "hina-guest":
            reward = "1只天才猪（日菜）"
        else:
            reward = f"{len(self.items)}道{'五' if self.prize_id == 'five-star-feast' else '六'}星美食"
        return f"{self.prize_label}：已获得{reward}，已存入当前群背包。"

    def payload(self) -> dict[str, Any]:
        return {
            "prize_id": self.prize_id,
            "prize_label": self.prize_label,
            "animation": self.animation,
            "roll": self.roll,
            "items": [asdict(item) for item in self.items],
            "summary": self.summary,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FoodLotteryGrant:
        try:
            items = tuple(FoodLotteryItem(**item) for item in payload["items"])
            if not items:
                raise ValueError("empty lottery result")
            return cls(
                prize_id=payload["prize_id"],
                prize_label=payload["prize_label"],
                animation=payload["animation"],
                roll=validated_roll(payload["roll"]),
                items=items,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReceiptConflictError("绿芯派历史抽奖快照不完整，拒绝重新抽奖。") from exc


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _key(*parts: str) -> str:
    return YILU_LOTTERY + ":" + hashlib.sha256(_json(parts).encode("utf-8")).hexdigest()


async def _candidate_templates(session: DatabaseSession, scope_id: str, prize: LotteryPrize) -> list[dict[str, Any]]:
    table = "pig_templates" if prize.kind == "pig" else "food_templates"
    allowed_table = "scope_pig_templates" if prize.kind == "pig" else "scope_food_templates"
    rows = await session.fetch_all(
        f"""SELECT template.* FROM {table} AS template
        LEFT JOIN {allowed_table} AS allowed
          ON allowed.template_id=template.template_id AND allowed.scope_id=?
        WHERE template.enabled=1 AND template.rarity=? AND (
            (template.scope_type='common' AND template.consent_status='not-required')
            OR (template.scope_type='group' AND template.consent_status='granted'
                AND allowed.authorized=1 AND allowed.consent_status='granted')
        ) ORDER BY template.template_id""",
        (scope_id, prize.rarity),
    )
    if prize.kind == "pig":
        result = [dict(row) for row in rows if row["template_id"] == HINA_PIG_TEMPLATE_ID]
    else:
        # 原料绑定食谱永远不进入随机奖励池，不能用抽奖绕过 KFC / JJK 专属原料。
        result = [dict(row) for row in rows if row["template_id"] not in SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS]
    if not result:
        target = "天才猪（日菜）" if prize.kind == "pig" else f"{prize.rarity}星非原料绑定美食"
        raise FoodEffectError(f"当前群没有已启用且已授权的{target}，本次品鉴未结算。")
    return result


async def _unique_short_code(session: DatabaseSession) -> str:
    for _ in range(64):
        candidate = normalize_short_code(new_short_code())
        if not await AssetCodeRepository.code_is_occupied(session, candidate):
            return candidate
    raise AssetStateConflictError("暂时无法生成不冲突的奖励编号，本次品鉴未结算。")


async def _grant_item(
    session: DatabaseSession,
    *,
    identity: CommandIdentity,
    template: dict[str, Any],
    prize: LotteryPrize,
    snapshot: dict[str, Any],
    now: str,
    random_source: RandomSource,
) -> FoodLotteryItem:
    instance_id = uuid4().hex
    short_code = await _unique_short_code(session)
    values = {
        "short_code": short_code,
        "scope_id": identity.scope.value,
        "owner_player_id": identity.player_id,
        "template_id": str(template["template_id"]),
        "template_version": int(template["template_version"]),
        "rarity": int(template["rarity"]),
        "display_name_snapshot": str(template["display_name"]),
        "ruleset_version": RULESET_VERSION,
        "acquired_at": now,
        "updated_at": now,
    }
    if prize.kind == "pig":
        rolls = tuple(validated_roll(random_source.random()) for _ in range(5))
        attributes = generate_pig_attributes(
            rarity=prize.rarity,
            length_min=float(template["length_min"]),
            length_max=float(template["length_max"]),
            weight_min=float(template["weight_min"]),
            weight_max=float(template["weight_max"]),
            fat_profile=str(template["fat_profile"]),
            random_values=rolls,
        )
        snapshot["attribute_rolls"] = list(rolls)
        values.update(
            pig_instance_id=instance_id,
            size_value=attributes.size_value,
            size_percentile=attributes.size_percentile,
            weight_value=attributes.weight_value,
            weight_percentile=attributes.weight_percentile,
            fat_ratio=attributes.fat_ratio,
            official_value=attributes.official_value,
            random_snapshot_json=_json(snapshot),
        )
        gameplay = GameplayRepository()
        await gameplay.insert_pig_instance(session, values=values)
        is_new = await gameplay.upsert_pig_catalog(
            session,
            player_id=identity.player_id,
            template_id=values["template_id"],
            size_value=attributes.size_value,
            weight_value=attributes.weight_value,
            now=now,
        )
    else:
        # 系统直接发菜没有真实原料猪；沿用管理员发菜的60kg、50%原料百分位基准。
        # 不伪造 source_pig_instance_id，不受当前厨具、等级或排队中的菜品效果影响。
        portion_roll = validated_roll(random_source.random())
        attributes = generate_food_attributes(
            rarity=prize.rarity,
            template_id=values["template_id"],
            source_weight=60.0,
            source_weight_percentile=0.5,
            portion_roll=portion_roll,
        )
        try:
            tags = json.loads(template["recipe_tags_json"])
        except (ValueError, TypeError) as exc:
            raise FoodEffectError("奖励食谱标签无法读取，本次品鉴未结算。") from exc
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise FoodEffectError("奖励食谱标签无效，本次品鉴未结算。")
        snapshot.update(portion_roll=portion_roll, synthetic_source_weight=60.0, synthetic_source_weight_percentile=0.5)
        values.update(
            food_instance_id=instance_id,
            source_pig_instance_id=None,
            portion_weight=attributes.portion_weight,
            fat_category=recipe_affinity(tags),
            official_value=attributes.official_value,
            effect_id=str(template["effect_id"]),
            effect_params_json=str(template["effect_params_json"]),
            random_snapshot_json=_json(snapshot),
        )
        economy = EconomyRepository()
        await economy.insert_food_instance(session, values=values)
        is_new = await economy.upsert_food_catalog(
            session,
            player_id=identity.player_id,
            template_id=values["template_id"],
            portion_weight=attributes.portion_weight,
            now=now,
        )
    return FoodLotteryItem(
        kind=prize.kind,
        template_id=values["template_id"],
        name=values["display_name_snapshot"],
        rarity=prize.rarity,
        instance_id=instance_id,
        short_code=short_code,
        value=attributes.official_value,
        asset_path=str(template["image_relpath"]),
        media_format=str(template["media_format"]),
        image_fit=str(template["image_fit"]),
        is_new=is_new,
    )


async def grant_food_lottery(
    session: DatabaseSession,
    *,
    identity: CommandIdentity,
    food_instance_id: str,
    source_key: str,
    now: str,
    random_source: RandomSource,
) -> FoodLotteryGrant:
    """在成功 ``consume_food`` 的同一事务中抽一次并发奖，不自行提交或发送动画。

    调用顺序固定为：1次分支随机；每道菜1次模板随机+1次份量随机，或天才猪5次属性随机。
    相同来源重放不再读取随机源；消息键和源食物各有一个持久唯一边界。
    """

    if not all(isinstance(value, str) and value.strip() for value in (food_instance_id, source_key, now)):
        raise FoodEffectError("绿芯派抽奖缺少实例、来源键或结算时间。")
    operation_key = _key("source", identity.scope.value, identity.player_id, source_key)
    audit_key = _key("food", food_instance_id)
    request = {
        "scope_id": identity.scope.value,
        "player_id": identity.player_id,
        "food_instance_id": food_instance_id,
        "source_key": source_key,
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
            or operation["operation_type"] != YILU_LOTTERY
            or audit["scope_id"] != identity.scope.value
            or audit["actor_user_id"] != identity.user_id
            or audit["action"] != _AUDIT_ACTION
            or audit["object_type"] != "food"
            or audit["object_id"] != food_instance_id
            or operation["result_json"] != audit["detail_json"]
        ):
            raise ReceiptConflictError("绿芯派抽奖来源与原结算不一致，不能重新抽奖。")
        try:
            previous = json.loads(operation["result_json"])
        except (ValueError, TypeError) as exc:
            raise ReceiptConflictError("绿芯派历史抽奖快照无法读取，拒绝重新抽奖。") from exc
        if not isinstance(previous, dict) or previous.get("request") != request:
            raise ReceiptConflictError("绿芯派抽奖来源与原结算不一致，不能重新抽奖。")
        return replace(FoodLotteryGrant.from_payload(previous.get("result", {})), replayed=True)

    food = await session.fetch_one(
        "SELECT scope_id,owner_player_id,state,rarity,effect_id FROM food_instances WHERE food_instance_id=?",
        (food_instance_id,),
    )
    if (
        food is None
        or food["scope_id"] != identity.scope.value
        or food["owner_player_id"] != identity.player_id
        or food["state"] != "consumed"
    ):
        raise AssetStateConflictError("抽奖只能由当前群本人成功吃下的绿芯派触发。")
    if food["rarity"] != 6 or food["effect_id"] != YILU_LOTTERY:
        raise FoodEffectError("美食实例不是六星绿芯派抽奖效果，本次品鉴未结算。")

    roll = validated_roll(random_source.random())
    prize = choose_lottery_prize(roll)
    templates = await _candidate_templates(session, identity.scope.value, prize)
    pool_ids = [str(template["template_id"]) for template in templates]
    pool_hash = hashlib.sha256(_json(pool_ids).encode("utf-8")).hexdigest()
    items = []
    for ordinal in range(prize.quantity):
        template_roll = validated_roll(random_source.random()) if prize.kind == "food" else None
        template = templates[int(template_roll * len(templates))] if template_roll is not None else templates[0]
        snapshot = {
            "source": YILU_LOTTERY,
            "ruleset_version": RULESET_VERSION,
            "source_food_instance_id": food_instance_id,
            "source_key": source_key,
            "prize_id": prize.prize_id,
            "prize_roll": roll,
            "grant_ordinal": ordinal + 1,
            "template_roll": template_roll,
            "candidate_pool_hash": pool_hash,
            "gameplay_rewards_applied": False,
            "statistics_incremented": False,
        }
        items.append(
            await _grant_item(
                session,
                identity=identity,
                template=template,
                prize=prize,
                snapshot=snapshot,
                now=now,
                random_source=random_source,
            )
        )
    result = FoodLotteryGrant(prize.prize_id, prize.label, prize.animation, roll, tuple(items))
    detail_json = _json(
        {
            "request": request,
            "ruleset_version": RULESET_VERSION,
            "template_pool_ids": pool_ids,
            "result": result.payload(),
        }
    )
    await AchievementRepository().insert_operation(
        session,
        operation_key=operation_key,
        player_id=identity.player_id,
        operation_type=YILU_LOTTERY,
        result_json=detail_json,
        now=now,
    )
    await session.execute(
        "INSERT INTO audit_events(audit_event_id,scope_id,actor_user_id,action,"
        "object_type,object_id,detail_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (audit_key, identity.scope.value, identity.user_id, _AUDIT_ACTION, "food", food_instance_id, detail_json, now),
    )
    return result
