"""西西即时奖品在品鉴事务内发放；不记为抓猪、做菜或奖励次数。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from ..domain.errors import AssetStateConflictError, FoodEffectError, ReceiptConflictError
from ..domain.food_lottery import LotteryPrize, validated_roll
from ..domain.mid_autumn import EXCLUSIVE_MOONCAKE_IDS
from ..domain.models import CommandIdentity
from ..domain.ports import RandomSource
from ..domain.xixi_feast import (
    DANIYA_BIRTHDAY_FEAST,
    XIXI_SIX_STAR_COOK,
    XIXI_STAR_CHEESE_LOTTERY,
    XIXI_TARGETED_CATCH,
    choose_star_cheese_prize,
    scoped_template_id,
)
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.achievements import AchievementRepository
from ..infrastructure.repositories.economy import EconomyRepository
from .food_lottery import _candidate_templates, _grant_item


async def _scoped_template(session: DatabaseSession, scope_id: str, kind: str, suffix: str) -> dict[str, Any]:
    template_id = scoped_template_id(scope_id, kind, suffix)
    table = "pig_templates" if kind == "pig" else "food_templates"
    allowed = "scope_pig_templates" if kind == "pig" else "scope_food_templates"
    row = await session.fetch_one(
        f"SELECT t.* FROM {table} t JOIN {allowed} a ON a.template_id=t.template_id "
        "WHERE t.template_id=? AND t.enabled=1 AND t.rarity=6 AND t.scope_type='group' "
        "AND t.consent_status='granted' AND a.scope_id=? AND a.authorized=1 AND a.consent_status='granted'",
        (template_id, scope_id),
    )
    if row is None:
        raise FoodEffectError("当前群缺少已启用且授权的西西奖励模板，美食未消耗。")
    return dict(row)


async def grant_xixi_feast(
    session: DatabaseSession,
    *,
    identity: CommandIdentity,
    food_instance_id: str,
    source_key: str,
    now: str,
    random_source: RandomSource,
    effect_id: str,
) -> dict[str, Any]:
    """调用者已消费食品；独立来源凭证可重放，所有失败由外层事务整体回滚。"""
    if effect_id not in {XIXI_STAR_CHEESE_LOTTERY, DANIYA_BIRTHDAY_FEAST}:
        raise FoodEffectError("西西即时效果未注册。")
    request = {
        "scope_id": identity.scope.value,
        "player_id": identity.player_id,
        "food_instance_id": food_instance_id,
        "source_key": source_key,
        "effect_id": effect_id,
    }
    operation_key = "xixi-feast:" + hashlib.sha256(food_instance_id.encode()).hexdigest()
    previous = await session.fetch_one(
        "SELECT player_id,operation_type,result_json FROM achievement_operations WHERE operation_key=?",
        (operation_key,),
    )
    if previous:
        try:
            payload = json.loads(previous["result_json"])
            if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
                raise ValueError("invalid feast receipt")
        except (TypeError, ValueError) as exc:
            raise ReceiptConflictError("西西奖励历史凭证无法读取，不能重新发奖。") from exc
        if (
            previous["player_id"] != identity.player_id
            or previous["operation_type"] != effect_id
            or payload.get("request") != request
        ):
            raise ReceiptConflictError("西西奖励来源与原结算不一致，不能重新发奖。")
        return payload["result"]
    food = await session.fetch_one(
        "SELECT scope_id,owner_player_id,state,rarity,effect_id FROM food_instances WHERE food_instance_id=?",
        (food_instance_id,),
    )
    if (
        food is None
        or food["scope_id"] != identity.scope.value
        or food["owner_player_id"] != identity.player_id
        or food["state"] != "consumed"
        or food["rarity"] != 6
        or food["effect_id"] != effect_id
    ):
        raise AssetStateConflictError("西西奖励只能由本人在当前群成功品鉴指定美食触发。")
    items = []
    rolls = []
    if effect_id == XIXI_STAR_CHEESE_LOTTERY:
        rolls = [validated_roll(random_source.random()) for _ in range(5)]
        branches = [choose_star_cheese_prize(roll) for roll in rolls]
    else:
        branches = ["daniya", "xixi"]
    for ordinal, branch in enumerate(branches):
        kind = "food" if effect_id == XIXI_STAR_CHEESE_LOTTERY else "pig"
        rarity = 5 if branch == "five-star" else 6
        prize = LotteryPrize(branch, 0, kind, rarity, 1, branch)
        template_roll = None
        if branch == "five-star":
            templates = await _candidate_templates(session, identity.scope.value, prize)
            templates = [row for row in templates if row["template_id"] not in EXCLUSIVE_MOONCAKE_IDS]
            if not templates:
                raise FoodEffectError("当前群没有合法的普通五星奖励菜，美食未消耗。")
            template_roll = validated_roll(random_source.random())
            template = templates[int(template_roll * len(templates))]
        else:
            template = await _scoped_template(session, identity.scope.value, kind, branch)
        item = await _grant_item(
            session,
            identity=identity,
            template=template,
            prize=prize,
            snapshot={
                "source": effect_id,
                "source_food_instance_id": food_instance_id,
                "source_key": source_key,
                "prize_roll": rolls[ordinal] if rolls else None,
                "prize_id": branch,
                "template_roll": template_roll,
                "grant_ordinal": ordinal + 1,
                "gameplay_rewards_applied": False,
                "statistics_incremented": False,
            },
            now=now,
            random_source=random_source,
        )
        items.append(asdict(item))
    queued = []
    if effect_id == DANIYA_BIRTHDAY_FEAST:
        for queue_id, params in (
            (XIXI_TARGETED_CATCH, {"mode": "other-six"}),
            (XIXI_SIX_STAR_COOK, {"six_star_percent": 100}),
        ):
            entry_id = uuid4().hex
            await EconomyRepository().insert_food_effect(
                session,
                effect_entry_id=entry_id,
                player_id=identity.player_id,
                source_food_instance_id=food_instance_id,
                effect_id=queue_id,
                params_json=json.dumps(params),
                granted_uses=1,
                expires_at=None,
                now=now,
            )
            queued.append(entry_id)
    summary = (
        "西西昭日星酪：5次独立抽奖已完成，5件美食已存入当前群背包。"
        if effect_id == XIXI_STAR_CHEESE_LOTTERY
        else "达妮娅的生日蛋糕：达妮娅猪和西西猪各1只已存入当前群背包；其他六星抓猪与配对六星做菜效果各排队1次。"
    )
    result = {
        "kind": effect_id,
        "items": items,
        "rolls": rolls,
        "branches": branches,
        "effect_entry_ids": queued,
        "summary": summary,
    }
    await AchievementRepository().insert_operation(
        session,
        operation_key=operation_key,
        player_id=identity.player_id,
        operation_type=effect_id,
        result_json=json.dumps({"request": request, "result": result}, ensure_ascii=False, sort_keys=True),
        now=now,
    )
    return result
