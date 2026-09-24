"""新菜效果的事务内状态操作，任何失败均由调用方整体回滚。"""

import json
from datetime import datetime, timedelta, timezone

from ...domain.errors import DailyCatchLimitError, FoodEffectError
from ...domain.feasts import (
    CLOVER_CATCH,
    CLOVER_COOK,
    CLOVER_COOKS,
    CLOVER_INITIAL_CATCHES,
    CLOVER_REWARD_CATCHES,
    MOON_REWARD_CATCHES,
)
from .economy import EconomyRepository


async def active_clover(session, player_id):
    row = await session.fetch_one(
        "SELECT * FROM player_clover_chains WHERE player_id=? AND stage!='complete'", (player_id,)
    )
    return dict(row) if row else None


async def active_moon(session, player_id, now):
    row = await session.fetch_one(
        "SELECT * FROM player_moon_feasts WHERE player_id=? AND target_end>? ORDER BY created_at LIMIT 1",
        (player_id, now),
    )
    return dict(row) if row else None


async def moon_discount_active(session, player_id, now):
    moon = await active_moon(session, player_id, now)
    return bool(moon and moon["discount_start"] <= now < moon["discount_end"])


async def feast_status(session, player_id, now):
    lines = []
    chain = await active_clover(session, player_id)
    if chain:
        if chain["stage"] == "initial":
            target = chain["initial_target"]
            lines.append(
                f"粉蓝冰糕：初始专属抓猪剩余{target - chain['initial_used']}/{target}次，"
                "抓完后启用第1轮六星猪做菜加成。"
            )
        elif chain["stage"] == "cook":
            lines.append(
                f"粉蓝冰糕：第{chain['cook_used'] + 1}/{CLOVER_COOKS}轮六星猪做菜加成已就绪；"
                f"累计专属抓猪星数{chain['star_sum']}，每星提高六星菜概率1个百分点。"
            )
        else:
            lines.append(
                f"粉蓝冰糕：第{chain['cook_used']}/{CLOVER_COOKS}轮成功奖励抓猪剩余"
                f"{chain['reward_granted'] - chain['reward_used']}/{CLOVER_REWARD_CATCHES}次；"
                "本轮抓完后才启用下一次六星猪做菜加成。"
            )
    moon = await active_moon(session, player_id, now)
    if moon:
        target = datetime.fromisoformat(moon["target_start"].replace("Z", "+00:00"))
        label = target.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        if now < moon["blocked_start"]:
            lines.append(f"月栖卷：下一时段禁抓；北京时间{label}起{MOON_REWARD_CATCHES}次额外抓猪与商城道具8.8折。")
        elif now < moon["blocked_end"]:
            lines.append(f"月栖卷：本时段禁止所有抓猪；北京时间{label}起领取{MOON_REWARD_CATCHES}次额外抓猪与8.8折。")
        else:
            lines.append(
                f"月栖卷：本时段额外抓猪剩余{max(0, MOON_REWARD_CATCHES - moon['used'])}"
                f"/{MOON_REWARD_CATCHES}次，4/5/6星概率×3.5；商城道具8.8折。"
            )
    return tuple(lines)


async def start_moon(session, identity, source_id, now, blocked, target):
    if await active_moon(session, identity.player_id, now):
        raise FoodEffectError("月栖萤光卷的禁抓或奖励时段尚未结束；本次美食未消耗。")
    if await EconomyRepository().active_catch_window_transfer(session, player_id=identity.player_id, now=now):
        raise FoodEffectError("旧版月栖额度计划尚未结束，请在结束后食用新版月栖卷。")
    await session.execute(
        "INSERT INTO player_moon_feasts(source_food_instance_id,player_id,scope_id,blocked_start,blocked_end,"
        "target_start,target_end,discount_start,discount_end,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, identity.player_id, identity.scope.value, *blocked, *target, *target, now, now),
    )


async def require_catch_allowed(session, player_id, now):
    moon = await active_moon(session, player_id, now)
    if moon and moon["blocked_start"] <= now < moon["blocked_end"]:
        raise DailyCatchLimitError(
            f"月栖萤光卷：本时段禁止抓猪，所有普通、专属及战利品次数保留；下一时段可使用{MOON_REWARD_CATCHES}次奖励。"
        )
    return moon


async def queue_clover(
    session,
    *,
    player_id,
    source_id,
    phase,
    now,
    entry_id,
    star_sum=0,
    initial_target=CLOVER_INITIAL_CATCHES,
):
    cook = phase == "cook"
    await EconomyRepository().insert_food_effect(
        session,
        effect_entry_id=entry_id,
        player_id=player_id,
        source_food_instance_id=source_id,
        effect_id=CLOVER_COOK if cook else CLOVER_CATCH,
        params_json=json.dumps(
            {"chain_id": source_id, "phase": phase, "star_sum": star_sum, "initial_target": initial_target}
        ),
        granted_uses=1 if cook else (initial_target if phase == "initial" else CLOVER_REWARD_CATCHES),
        expires_at=None,
        now=now,
    )


async def start_clover(session, identity, source_id, now, entry_id):
    if await active_clover(session, identity.player_id):
        raise FoodEffectError("上一次粉蓝冰糕的专属抓猪或做菜尚未完成；本次美食未消耗。")
    if await EconomyRepository().active_window_resonance(session, player_id=identity.player_id, now=now):
        raise FoodEffectError("旧版粉蓝共鸣仍在本时段生效，请在结束后食用新版冰糕。")
    await session.execute(
        "INSERT INTO player_clover_chains(source_food_instance_id,player_id,scope_id,stage,created_at,updated_at) "
        "VALUES(?,?,?,'initial',?,?)",
        (source_id, identity.player_id, identity.scope.value, now, now),
    )
    await queue_clover(
        session,
        player_id=identity.player_id,
        source_id=source_id,
        phase="initial",
        now=now,
        entry_id=entry_id,
        initial_target=CLOVER_INITIAL_CATCHES,
    )


async def settle_clover_catch(session, *, player_id, effects, consumed, rarity, now, entry_id):
    selected = next((e for e in effects if e.effect_id == CLOVER_CATCH and e.effect_entry_id in consumed), None)
    if selected is None:
        return ()
    chain = await active_clover(session, player_id)
    if (
        chain is None
        or chain["source_food_instance_id"] != selected.params["chain_id"]
        or chain["stage"] != selected.params["phase"]
    ):
        raise FoodEffectError("粉蓝专属次数与累计状态不一致，本次抓猪回滚。")
    if chain["stage"] == "initial":
        target = chain["initial_target"]
        used, stars = chain["initial_used"] + 1, chain["star_sum"] + int(rarity)
        if used > target:
            raise FoodEffectError("粉蓝专属抓猪超过已发放次数，本次抓猪回滚。")
        await session.execute(
            "UPDATE player_clover_chains SET initial_used=?,star_sum=?,stage=?,updated_at=? "
            "WHERE source_food_instance_id=?",
            (used, stars, "cook" if used == target else "initial", now, chain["source_food_instance_id"]),
        )
        if used == target:
            await queue_clover(
                session,
                player_id=player_id,
                source_id=chain["source_food_instance_id"],
                phase="cook",
                now=now,
                entry_id=entry_id,
                star_sum=stars,
                initial_target=target,
            )
        progress = "已解锁第1轮六星猪做菜加成。" if used == target else f"完成{target}次后启用做菜加成。"
        return (f"粉蓝专属抓猪：{used}/{target}次；{progress}",)
    if chain["stage"] != "reward":
        raise FoodEffectError("粉蓝奖励阶段异常，本次抓猪回滚。")
    used = chain["reward_used"] + 1
    if used > chain["reward_granted"]:
        raise FoodEffectError("粉蓝奖励抓猪超出已发放次数，本次抓猪回滚。")
    stars = chain["star_sum"] + int(rarity)
    round_finished = used == chain["reward_granted"]
    stage = "cook" if round_finished else "reward"
    await session.execute(
        "UPDATE player_clover_chains SET reward_used=?,star_sum=?,stage=?,updated_at=? "
        "WHERE source_food_instance_id=?",
        (used, stars, stage, now, chain["source_food_instance_id"]),
    )
    if round_finished:
        await queue_clover(
            session,
            player_id=player_id,
            source_id=chain["source_food_instance_id"],
            phase="cook",
            now=now,
            entry_id=entry_id,
            star_sum=stars,
            initial_target=chain["initial_target"],
        )
    return (
        f"粉蓝成功做菜奖励：本轮已使用{CLOVER_REWARD_CATCHES - (chain['reward_granted'] - used)}"
        f"/{CLOVER_REWARD_CATCHES}次；累计专属抓猪星数{stars}。"
        + ("已解锁下一轮六星猪做菜加成。" if round_finished else "抓完本轮额外次数后再启用做菜加成。"),
    )


async def settle_clover_cook(session, *, player_id, effects, consumed, success, now, entry_id):
    selected = next((e for e in effects if e.effect_id == CLOVER_COOK and e.effect_entry_id in consumed), None)
    if selected is None:
        return False, False
    chain = await active_clover(session, player_id)
    if (
        chain is None
        or chain["stage"] != "cook"
        or chain["initial_used"] != chain["initial_target"]
        or chain["source_food_instance_id"] != selected.params["chain_id"]
        or chain["star_sum"] != selected.params.get("star_sum")
    ):
        raise FoodEffectError("粉蓝做菜累计状态异常，本次做菜回滚。")
    cook_used = chain["cook_used"] + 1
    if cook_used > CLOVER_COOKS:
        raise FoodEffectError("粉蓝做菜次数超出上限，本次做菜回滚。")
    reward_due = success and cook_used < CLOVER_COOKS
    reward_granted = chain["reward_granted"] + (CLOVER_REWARD_CATCHES if reward_due else 0)
    stage = "reward" if reward_due else "complete"
    await session.execute(
        "UPDATE player_clover_chains SET cook_used=?,reward_granted=?,stage=?,updated_at=? "
        "WHERE source_food_instance_id=?",
        (cook_used, reward_granted, stage, now, chain["source_food_instance_id"]),
    )
    if reward_due:
        await queue_clover(
            session,
            player_id=player_id,
            source_id=chain["source_food_instance_id"],
            phase="reward",
            now=now,
            entry_id=entry_id,
        )
    return reward_due, stage == "complete"
