"""Validated, one-shot effects granted by high-rarity food."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .enums import Rarity
from .errors import FoodEffectError
from .rules import (
    apply_monotonic_high_rarity_multipliers,
    lift_target_rarity_from_lower,
    normalize_weights,
)

NEXT_CATCH_QUALITY = "next-catch-quality"
NEXT_COOK_QUALITY = "next-cook-quality"
NEXT_SIX_STAR_COOK = "next-six-star-cook"
EXTRA_CATCHES = "extra-catches"
NEXT_PIG_RARITY = "next-pig-rarity"
NEXT_FOOD_RARITY = "next-food-rarity"
NEXT_PIG_STATURE = "next-pig-stature"
NEXT_SIX_STAR_CATCH = "next-six-star-catch"
WEEKLY_WINDOW_CATCHES = "weekly-window-catches"
PERMANENT_WINDOW_CATCH = "permanent-window-catch"
NEXT_HIGH_STAR_CATCH = "next-high-star-catch"
NEXT_FIVE_STAR_COOK = "next-five-star-cook"
EVEN_CATCH_DISTRIBUTION = "even-catch-distribution"
QUOTA_RESET_CHANCE = "quota-reset"
CURRENT_WINDOW_CATCHES = "current-window-catches"
TODAY_WINDOW_CATCHES = "today-window-catches"
NEXT_SIX_STAR_COOK_BONUS = "next-six-star-cook-bonus"
NEXT_STACKABLE_SIX_STAR_COOK_BONUS = "next-stackable-six-star-cook-bonus"
NEXT_GIANT_FIVE_STAR_CATCH = "next-giant-five-star-catch"
NEXT_COLLABORATION_CATCH = "next-collaboration-catch"
NEXT_EXTREME_FIVE_STAR_COOK = "next-extreme-five-star-cook"
NEXT_FIVE_SIX_STAR_CATCH = "next-five-six-star-catch"
NEXT_SMALL_SIX_STAR_CATCH = "next-small-six-star-catch"
GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH = "group-next-exclusive-high-star-catch"
GROUP_WINDOW_HIGH_STAR_BOOST = "group-window-high-star-boost"
# Schema 18 前糖醋排骨使用的历史独占加权；保留解析能力以兼容旧审计快照。
EXCLUSIVE_CATCH_QUALITY = "exclusive-catch-quality"

# 独占效果：六星菜效果独立作用，不与任何其他菜品效果或道具叠加。
# 激活独占效果时，本次动作忽略装备道具（道具保留、不消耗）。
EXCLUSIVE_CATCH_EFFECTS = frozenset(
    {
        NEXT_SIX_STAR_CATCH,
        NEXT_HIGH_STAR_CATCH,
        EVEN_CATCH_DISTRIBUTION,
        EXCLUSIVE_CATCH_QUALITY,
    }
)
EXCLUSIVE_COOK_EFFECTS = frozenset({NEXT_SIX_STAR_COOK, NEXT_FIVE_STAR_COOK})

# 这三种六星菜自带独立抓猪次数。成功结算时消耗效果次数，但不消耗正常时段额度。
QUOTA_EXEMPT_CATCH_EFFECTS = frozenset(
    {
        NEXT_SIX_STAR_CATCH,
        NEXT_HIGH_STAR_CATCH,
        EVEN_CATCH_DISTRIBUTION,
    }
)

CATCH_EFFECT_IDS = frozenset(
    {
        NEXT_CATCH_QUALITY,
        NEXT_PIG_RARITY,
        NEXT_PIG_STATURE,
        NEXT_SIX_STAR_CATCH,
        NEXT_HIGH_STAR_CATCH,
        EVEN_CATCH_DISTRIBUTION,
        EXCLUSIVE_CATCH_QUALITY,
        NEXT_GIANT_FIVE_STAR_CATCH,
        NEXT_COLLABORATION_CATCH,
        NEXT_FIVE_SIX_STAR_CATCH,
        NEXT_SMALL_SIX_STAR_CATCH,
    }
)
COOK_EFFECT_IDS = frozenset(
    {
        NEXT_COOK_QUALITY,
        NEXT_SIX_STAR_COOK,
        NEXT_SIX_STAR_COOK_BONUS,
        NEXT_STACKABLE_SIX_STAR_COOK_BONUS,
        NEXT_FOOD_RARITY,
        NEXT_FIVE_STAR_COOK,
        NEXT_EXTREME_FIVE_STAR_COOK,
    }
)
QUOTA_EFFECT_IDS = frozenset({CURRENT_WINDOW_CATCHES, TODAY_WINDOW_CATCHES})
IMMEDIATE_EFFECT_IDS = frozenset({WEEKLY_WINDOW_CATCHES, PERMANENT_WINDOW_CATCH})
GROUP_EFFECT_IDS = frozenset(
    {
        GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH,
        GROUP_WINDOW_HIGH_STAR_BOOST,
    }
)
SUPPORTED_EFFECT_IDS = (
    CATCH_EFFECT_IDS
    | COOK_EFFECT_IDS
    | {EXTRA_CATCHES}
    | QUOTA_EFFECT_IDS
    | IMMEDIATE_EFFECT_IDS
    | GROUP_EFFECT_IDS
    | {QUOTA_RESET_CHANCE}
)

# 互斥作用组：同组效果对同一次动作最多生效一个。
# 抓猪概率组：所有“提高抓猪高星概率”类效果互斥，防止同类型菜品叠加。
CATCH_PROBABILITY_GROUP = frozenset(
    {
        NEXT_CATCH_QUALITY,
        NEXT_PIG_RARITY,
        NEXT_SIX_STAR_CATCH,
        NEXT_HIGH_STAR_CATCH,
        EVEN_CATCH_DISTRIBUTION,
        EXCLUSIVE_CATCH_QUALITY,
        NEXT_GIANT_FIVE_STAR_CATCH,
        NEXT_COLLABORATION_CATCH,
        NEXT_FIVE_SIX_STAR_CATCH,
        NEXT_SMALL_SIX_STAR_CATCH,
    }
)
# 抓猪体型组：与概率组正交，可同时生效。
CATCH_STATURE_GROUP = frozenset({NEXT_PIG_STATURE})
# 做菜概率组：所有“提高做菜成品品质”类效果互斥。
COOK_PROBABILITY_GROUP = frozenset(
    {
        NEXT_COOK_QUALITY,
        NEXT_FOOD_RARITY,
        NEXT_SIX_STAR_COOK,
        NEXT_SIX_STAR_COOK_BONUS,
        NEXT_STACKABLE_SIX_STAR_COOK_BONUS,
        NEXT_FIVE_STAR_COOK,
        NEXT_EXTREME_FIVE_STAR_COOK,
    }
)


@dataclass(frozen=True, slots=True)
class FoodEffectGrant:
    """A validated durable grant created when one food is eaten."""

    effect_id: str
    params: dict[str, object]
    granted_uses: int
    summary: str


@dataclass(frozen=True, slots=True)
class ActiveFoodEffect:
    """One persisted effect that still has at least one use."""

    effect_entry_id: str
    effect_id: str
    params: Mapping[str, object]
    granted_uses: int
    consumed_uses: int
    expires_at: str
    created_at: str


@dataclass(frozen=True, slots=True)
class CatchEffectApplication:
    """Catch weights and stature bias after applying queued effects."""

    weights: tuple[float, ...]
    stature_bias: float
    consumed_entry_ids: tuple[str, ...]
    summaries: tuple[str, ...]
    skipped_summaries: tuple[str, ...] = ()
    collaboration_only: bool = False
    giant_template_multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class CookingEffectApplication:
    """Cooking weights after applying queued effects."""

    weights: tuple[float, ...]
    consumed_entry_ids: tuple[str, ...]
    summaries: tuple[str, ...]
    skipped_summaries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActiveGroupFoodEffect:
    """One current-scope six-star food effect shared by every player."""

    group_effect_entry_id: str
    effect_id: str
    params: Mapping[str, object]
    granted_uses_per_player: int
    consumed_uses: int
    source_user_id: str
    starts_at: str
    expires_at: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GroupCatchEffectApplication:
    """Group-wide probability and dedicated-quota selection for one catch."""

    weights: tuple[float, ...]
    consumed_entry_ids: tuple[str, ...]
    dedicated_entry_id: str
    summaries: tuple[str, ...]
    skipped_summaries: tuple[str, ...]
    exclusive: bool


def active_effect_from_row(row: Mapping[str, object]) -> ActiveFoodEffect:
    """Decode one repository row and reject malformed persisted parameters."""

    try:
        params = json.loads(str(row.get("params_json") or "{}"))
    except json.JSONDecodeError as exc:
        raise FoodEffectError("待触发美食效果参数不是有效 JSON。") from exc
    if not isinstance(params, dict):
        raise FoodEffectError("待触发美食效果参数必须是 JSON 对象。")
    effect_id = str(row.get("effect_id") or "")
    resolve_food_effect(effect_id, params)
    return ActiveFoodEffect(
        effect_entry_id=str(row["effect_entry_id"]),
        effect_id=effect_id,
        params=params,
        granted_uses=int(row["granted_uses"]),
        consumed_uses=int(row["consumed_uses"]),
        expires_at=str(row.get("expires_at") or ""),
        created_at=str(row["created_at"]),
    )


def active_group_effect_from_row(row: Mapping[str, object]) -> ActiveGroupFoodEffect:
    """Decode one current group-effect row and validate its persisted parameters."""

    try:
        params = json.loads(str(row.get("params_json") or "{}"))
    except json.JSONDecodeError as exc:
        raise FoodEffectError("群体美食效果参数不是有效 JSON。") from exc
    if not isinstance(params, dict):
        raise FoodEffectError("群体美食效果参数必须是 JSON 对象。")
    effect_id = str(row.get("effect_id") or "")
    if effect_id not in GROUP_EFFECT_IDS:
        raise FoodEffectError(f"群体美食效果“{effect_id}”尚未注册。")
    resolve_food_effect(effect_id, params)
    return ActiveGroupFoodEffect(
        group_effect_entry_id=str(row["group_effect_entry_id"]),
        effect_id=effect_id,
        params=params,
        granted_uses_per_player=int(row["granted_uses_per_player"]),
        consumed_uses=int(row.get("consumed_uses") or 0),
        source_user_id=str(row.get("source_user_id") or ""),
        starts_at=str(row["starts_at"]),
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
    )


def _number(
    params: Mapping[str, object],
    name: str,
    *,
    lower: float,
    upper: float,
) -> float:
    try:
        value = float(params[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise FoodEffectError(f"美食效果参数 {name} 必须是数字。") from exc
    if not lower <= value <= upper:
        raise FoodEffectError(f"美食效果参数 {name} 必须位于 {lower:g} 至 {upper:g}。")
    return value


def _integer(
    params: Mapping[str, object],
    name: str,
    *,
    lower: int,
    upper: int,
) -> int:
    value = _number(params, name, lower=lower, upper=upper)
    integer = int(value)
    if value != integer:
        raise FoodEffectError(f"美食效果参数 {name} 必须是整数。")
    return integer


def resolve_food_effect(
    effect_id: str,
    params: Mapping[str, object],
) -> FoodEffectGrant:
    """Validate one manifest-defined effect and build its user-facing grant."""

    normalized_id = str(effect_id or "").strip()
    raw = dict(params)
    if normalized_id in {NEXT_CATCH_QUALITY, EXCLUSIVE_CATCH_QUALITY}:
        multiplier = _number(raw, "multiplier", lower=1.01, upper=4.0)
        exclusive = normalized_id == EXCLUSIVE_CATCH_QUALITY
        uses = 1 if exclusive else _integer(raw, "uses", lower=1, upper=3) if "uses" in raw else 1
        label = "（独立生效，不与道具和其他菜品效果叠加）" if exclusive else ""
        return FoodEffectGrant(
            normalized_id,
            {"multiplier": multiplier, **({"uses": uses} if not exclusive else {})},
            uses,
            (
                f"接下来 {uses} 次抓猪时，4 至 6 星相对权重提升至 ×{multiplier:g}。{label}"
                if uses > 1
                else f"下一次抓猪时，4 至 6 星相对权重提升至 ×{multiplier:g}。{label}"
            ),
        )
    if normalized_id == NEXT_COOK_QUALITY:
        shift = _number(raw, "shift_percent", lower=1.0, upper=30.0)
        uses = _integer(raw, "uses", lower=1, upper=3) if "uses" in raw else 1
        return FoodEffectGrant(
            normalized_id,
            {"shift_percent": shift, "uses": uses},
            uses,
            (
                f"接下来 {uses} 次普通做菜时，向更高一档转移 {shift:g} 个百分点。"
                if uses > 1
                else f"下一次普通做菜时，向更高一档转移 {shift:g} 个百分点。"
            ),
        )
    if normalized_id == NEXT_SIX_STAR_COOK_BONUS:
        bonus = _number(raw, "bonus_percent", lower=1.0, upper=30.0)
        return FoodEffectGrant(
            normalized_id,
            {"bonus_percent": bonus},
            1,
            f"下一次用 6 星猪做菜时，6 星菜最终概率额外 +{bonus:g} 个百分点（最高 50%）。",
        )
    if normalized_id == NEXT_STACKABLE_SIX_STAR_COOK_BONUS:
        bonus = _number(raw, "bonus_percent", lower=0.1, upper=5.0)
        max_stacks = _integer(raw, "max_stacks", lower=1, upper=5)
        return FoodEffectGrant(
            normalized_id,
            {"bonus_percent": bonus, "max_stacks": max_stacks},
            1,
            (
                f"下一次用 6 星猪做菜时，6 星菜最终概率额外 +{bonus:g} 个百分点；"
                f"同类最多叠加 {max_stacks} 层，并在该次做菜一并消耗。"
            ),
        )
    if normalized_id == NEXT_SIX_STAR_COOK:
        percent = _number(raw, "six_star_percent", lower=11.0, upper=60.0)
        uses = _integer(raw, "uses", lower=1, upper=10) if "uses" in raw else 1
        return FoodEffectGrant(
            normalized_id,
            {"six_star_percent": percent, "uses": uses},
            uses,
            (
                f"接下来 {uses} 次用 6 星猪做菜时，6 星定制菜概率提升至 {percent:g}%。"
                if uses > 1
                else f"下一次用 6 星猪做菜时，6 星定制菜概率提升至 {percent:g}%。"
            ),
        )
    if normalized_id == NEXT_SIX_STAR_CATCH:
        percent = _number(raw, "six_star_percent", lower=11.0, upper=60.0)
        uses = _integer(raw, "uses", lower=1, upper=10) if "uses" in raw else 1
        return FoodEffectGrant(
            normalized_id,
            {"six_star_percent": percent, "uses": uses},
            uses,
            (
                f"接下来 {uses} 次专属抓猪，6 星猪概率提升至 {percent:g}%；不消耗正常时段额度。"
                if uses > 1
                else f"下一次专属抓猪，6 星猪概率提升至 {percent:g}%；不消耗正常时段额度。"
            ),
        )
    if normalized_id == EXTRA_CATCHES:
        count = _integer(raw, "count", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
            count,
            f"今天额外获得 {count} 次抓猪机会。",
        )
    if normalized_id == CURRENT_WINDOW_CATCHES:
        count = _integer(raw, "count", lower=1, upper=5)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
            1,
            f"当前抓猪时段的基础额度 +{count} 次；本时段内不可重复叠加。",
        )
    if normalized_id == TODAY_WINDOW_CATCHES:
        count = _integer(raw, "count", lower=1, upper=5)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
            1,
            f"今天当前及后续每个抓猪时段的基础额度都 +{count} 次；当天不可重复叠加。",
        )
    if normalized_id == WEEKLY_WINDOW_CATCHES:
        count = _integer(raw, "count", lower=1, upper=5)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
            1,
            (
                f"从食用起滚动 7 天内，每个抓猪时段基础额度额外 +{count} 次；"
                "第 7 天对应时段仍生效，有效期内不可重复叠加。"
            ),
        )
    if normalized_id == PERMANENT_WINDOW_CATCH:
        count = _integer(raw, "count", lower=1, upper=1)
        max_bonus = _integer(raw, "max_bonus", lower=1, upper=5)
        return FoodEffectGrant(
            normalized_id,
            {"count": count, "max_bonus": max_bonus},
            1,
            f"永久增加所有抓猪时段基础额度 +{count} 次，累计上限 +{max_bonus}。",
        )
    if normalized_id in {NEXT_PIG_RARITY, NEXT_FOOD_RARITY}:
        rarity_upper = 6 if normalized_id == NEXT_PIG_RARITY else 5
        rarity = _integer(raw, "rarity", lower=2, upper=rarity_upper)
        multiplier_upper = 12.0 if normalized_id == NEXT_PIG_RARITY and rarity == 6 else 8.0
        multiplier = _number(
            raw,
            "multiplier",
            lower=1.01,
            upper=multiplier_upper,
        )
        target = "抓猪" if normalized_id == NEXT_PIG_RARITY else "做菜"
        noun = "猪猪" if normalized_id == NEXT_PIG_RARITY else "美食"
        return FoodEffectGrant(
            normalized_id,
            {"rarity": rarity, "multiplier": multiplier},
            1,
            f"下一次{target}时，{rarity} 星{noun}相对权重提升至 ×{multiplier:g}；"
            "新增概率仅从更低星级转移，不压低更高星级。",
        )
    if normalized_id == NEXT_SMALL_SIX_STAR_CATCH:
        bonus = _number(raw, "bonus_percent", lower=0.1, upper=3.0)
        return FoodEffectGrant(
            normalized_id,
            {"bonus_percent": bonus},
            1,
            (
                f"下一次抓猪时，6 星最终概率额外 +{bonus:g} 个百分点；"
                "新增概率只从 1 至 3 星转移，不压低 4 星或 5 星。"
            ),
        )
    if normalized_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH:
        five_multiplier = _number(raw, "five_star_multiplier", lower=1.01, upper=20.0)
        six_multiplier = _number(raw, "six_star_multiplier", lower=1.01, upper=20.0)
        uses_per_player = _integer(raw, "uses_per_player", lower=1, upper=3)
        self_coin = _integer(raw, "self_coin", lower=0, upper=1_000_000)
        other_coin = _integer(raw, "other_coin", lower=0, upper=1_000_000)
        return FoodEffectGrant(
            normalized_id,
            {
                "five_star_multiplier": five_multiplier,
                "six_star_multiplier": six_multiplier,
                "uses_per_player": uses_per_player,
                "self_coin": self_coin,
                "other_coin": other_coin,
                "source_label": str(raw.get("source_label") or "神龙化猪七星云海锅").strip(),
            },
            uses_per_player,
            (
                f"食用者立即获得 {self_coin} 猪币，其余本群已登记玩家各获得 {other_coin} 猪币；"
                f"有效期内本群每名玩家的下一次抓猪，5 星与 6 星相对权重分别 ×{five_multiplier:g} "
                f"和 ×{six_multiplier:g}，并按六星菜独占规则结算。"
            ),
        )
    if normalized_id == GROUP_WINDOW_HIGH_STAR_BOOST:
        five_multiplier = _number(raw, "five_star_multiplier", lower=1.001, upper=4.0)
        six_multiplier = _number(raw, "six_star_multiplier", lower=1.001, upper=4.0)
        coin_per_player = _integer(raw, "coin_per_player", lower=0, upper=1_000_000)
        dedicated_catches = _integer(raw, "dedicated_catches", lower=0, upper=20)
        source_label = str(raw.get("source_label") or "六星菜").strip()
        if not source_label or len(source_label) > 64:
            raise FoodEffectError("群体美食效果的 source_label 必须是 1 至 64 个字符。")
        quota_text = (
            f"，并让每名玩家获得 {dedicated_catches} 次专属抓猪额度"
            if dedicated_catches
            else ""
        )
        return FoodEffectGrant(
            normalized_id,
            {
                "five_star_multiplier": five_multiplier,
                "six_star_multiplier": six_multiplier,
                "coin_per_player": coin_per_player,
                "dedicated_catches": dedicated_catches,
                "source_label": source_label,
            },
            max(1, dedicated_catches),
            (
                f"本群已登记玩家各获得 {coin_per_player} 猪币{quota_text}；"
                f"到次日同一抓猪时段刷新前，5 星与 6 星相对权重分别 ×{five_multiplier:g} "
                f"和 ×{six_multiplier:g}，可与道具和非六星菜叠加。"
            ),
        )
    if normalized_id == NEXT_PIG_STATURE:
        mode = str(raw.get("mode") or "").strip()
        if mode not in {"giant", "mini"}:
            raise FoodEffectError("体型美食效果的 mode 只能是 giant 或 mini。")
        strength = _number(raw, "strength", lower=0.05, upper=0.50)
        label = "巨物" if mode == "giant" else "迷你"
        return FoodEffectGrant(
            normalized_id,
            {"mode": mode, "strength": strength},
            1,
            f"下一次抓猪更容易出现{label}个体。",
        )
    if normalized_id == NEXT_GIANT_FIVE_STAR_CATCH:
        multiplier = _number(raw, "five_star_multiplier", lower=1.01, upper=8.0)
        stature_bias = _number(raw, "stature_bias", lower=0.05, upper=0.50)
        template_multiplier = _number(raw, "giant_template_multiplier", lower=1.01, upper=10.0)
        return FoodEffectGrant(
            normalized_id,
            {
                "five_star_multiplier": multiplier,
                "stature_bias": stature_bias,
                "giant_template_multiplier": template_multiplier,
            },
            1,
            (
                f"下一次抓猪：5 星相对权重 ×{multiplier:g}、体型偏移 +{stature_bias:.2f}；"
                f"若抓到 5 星，巨物模板抽取权重 ×{template_multiplier:g}。"
            ),
        )
    if normalized_id == NEXT_COLLABORATION_CATCH:
        three = _number(raw, "three_star_percent", lower=0.1, upper=100.0)
        four = _number(raw, "four_star_percent", lower=0.1, upper=100.0)
        five = _number(raw, "five_star_percent", lower=0.1, upper=100.0)
        if not 99.5 <= three + four + five <= 100.5:
            raise FoodEffectError("联动猪抓取效果的三星、四星、五星概率合计必须等于 100%。")
        return FoodEffectGrant(
            normalized_id,
            {
                "three_star_percent": round(three, 4),
                "four_star_percent": round(four, 4),
                "five_star_percent": round(five, 4),
            },
            1,
            (
                "下一次抓猪必定获得联动猪："
                f"3 星 {three:g}%、4 星 {four:g}%、5 星 {five:g}%。"
            ),
        )
    if normalized_id == NEXT_FIVE_SIX_STAR_CATCH:
        five_bonus = _number(raw, "five_star_bonus_percent", lower=1.0, upper=30.0)
        six_bonus = _number(raw, "six_star_bonus_percent", lower=0.1, upper=10.0)
        return FoodEffectGrant(
            normalized_id,
            {
                "five_star_bonus_percent": five_bonus,
                "six_star_bonus_percent": six_bonus,
            },
            1,
            (
                "下一次抓猪时，5 星最终概率额外 "
                f"+{five_bonus:g} 个百分点、6 星额外 +{six_bonus:g} 个百分点；"
                "新增概率只从 1 至 3 星转移。"
            ),
        )
    if normalized_id == NEXT_HIGH_STAR_CATCH:
        uses = _integer(raw, "uses", lower=1, upper=10)
        four = _number(raw, "four_star_percent", lower=1.0, upper=100.0)
        five = _number(raw, "five_star_percent", lower=1.0, upper=100.0)
        six = _number(raw, "six_star_percent", lower=0.0, upper=100.0)
        total = four + five + six
        if total < 99.5 or total > 100.5:
            raise FoodEffectError("高星抓猪效果的三档概率合计必须等于 100%。")
        return FoodEffectGrant(
            normalized_id,
            {
                "uses": uses,
                "four_star_percent": round(four, 4),
                "five_star_percent": round(five, 4),
                "six_star_percent": round(six, 4),
            },
            uses,
            (
                f"接下来 {uses} 次专属抓猪必定获得高星猪：4 星 {four:g}%、"
                f"5 星 {five:g}%、6 星 {six:g}%；不消耗正常时段额度。"
            ),
        )
    if normalized_id == NEXT_FIVE_STAR_COOK:
        uses = _integer(raw, "uses", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses},
            uses,
            f"接下来 {uses} 次做菜必定获得 5 星美食。",
        )
    if normalized_id == NEXT_EXTREME_FIVE_STAR_COOK:
        percent = _number(raw, "five_star_percent", lower=51.0, upper=95.0)
        return FoodEffectGrant(
            normalized_id,
            {"five_star_percent": percent},
            1,
            (
                "下一次使用 1 至 5 星猪做菜时，5 星菜最终概率至少为 "
                f"{percent:g}%；6 星猪不触发且效果保留。"
            ),
        )
    if normalized_id == EVEN_CATCH_DISTRIBUTION:
        uses = _integer(raw, "uses", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses},
            uses,
            f"接下来 {uses} 次专属抓猪，所有品质概率完全相同；不消耗正常时段额度。",
        )
    if normalized_id == QUOTA_RESET_CHANCE:
        count = _integer(raw, "count", lower=1, upper=3)
        dedicated_catches = _integer(
            raw,
            "group_dedicated_catches",
            lower=0,
            upper=20,
        ) if "group_dedicated_catches" in raw else 0
        five_multiplier = _number(
            raw,
            "five_star_multiplier",
            lower=1.001,
            upper=4.0,
        ) if "five_star_multiplier" in raw else 1.0
        six_multiplier = _number(
            raw,
            "six_star_multiplier",
            lower=1.001,
            upper=4.0,
        ) if "six_star_multiplier" in raw else 1.0
        group_coin = _integer(
            raw,
            "group_coin",
            lower=0,
            upper=1_000_000,
        ) if "group_coin" in raw else 0
        params: dict[str, object] = {"count": count}
        if dedicated_catches or group_coin or five_multiplier > 1.0 or six_multiplier > 1.0:
            params.update(
                {
                    "group_dedicated_catches": dedicated_catches,
                    "five_star_multiplier": five_multiplier,
                    "six_star_multiplier": six_multiplier,
                    "group_coin": group_coin,
                }
            )
            return FoodEffectGrant(
                normalized_id,
                params,
                count,
                (
                    f"获得 {count} 次 /重置额度 机会；每次重置会让本群已登记玩家各获得 "
                    f"{group_coin} 猪币和 {dedicated_catches} 次专属抓猪额度，并在次日同一时段"
                    f"刷新前令 5 星与 6 星相对权重分别 ×{five_multiplier:g} 和 ×{six_multiplier:g}。"
                ),
            )
        return FoodEffectGrant(
            normalized_id,
            params,
            count,
            f"获得 {count} 次重置本群抓猪额度窗口的机会，可发送 /重置额度 使用。",
        )
    raise FoodEffectError(f"美食效果“{normalized_id}”尚未注册，当前不会消耗这份美食。")


def effect_summary(effect_id: str, params: Mapping[str, object]) -> str:
    """Return a stable Chinese summary for cards and catalogs."""

    if not str(effect_id or "").strip():
        return "基础效果：食用后获得抓猪经验。"
    return resolve_food_effect(effect_id, params).summary


def _one_per_group(
    effects: Sequence[ActiveFoodEffect],
    group: frozenset[str],
    *,
    compatible: Callable[[ActiveFoodEffect], bool] | None = None,
) -> tuple[ActiveFoodEffect | None, tuple[str, ...]]:
    """Pick the first compatible effect of one mutual-exclusion group.

    Returns (chosen, skipped_summaries); same-group effects that were queued
    alongside the chosen one are reported as skipped so the player sees why
    they did not stack.
    """

    candidates = [effect for effect in effects if effect.effect_id in group]
    if not candidates:
        return None, ()
    chosen: ActiveFoodEffect | None = None
    skipped: list[str] = []
    for candidate in candidates:
        if compatible is not None and not compatible(candidate):
            skipped.append(
                f"{resolve_food_effect(candidate.effect_id, candidate.params).summary}（当前条件不适用，未消耗）"
            )
            continue
        if chosen is None:
            chosen = candidate
        else:
            skipped.append(
                f"{resolve_food_effect(candidate.effect_id, candidate.params).summary}（与已生效效果同类型，未叠加）"
            )
    return chosen, tuple(skipped)


def active_quota_effect_bonuses(
    effects: Sequence[ActiveFoodEffect],
) -> tuple[int, int]:
    """Return active current-window and today-every-window quota bonuses.

    Eating is responsible for preventing duplicate grants in the same period;
    summing here keeps old rows deterministic if data was imported manually.
    """

    current_window = 0
    today_windows = 0
    for effect in effects:
        if effect.effect_id not in QUOTA_EFFECT_IDS:
            continue
        grant = resolve_food_effect(effect.effect_id, effect.params)
        count = int(grant.params["count"])
        if effect.effect_id == CURRENT_WINDOW_CATCHES:
            current_window += count
        else:
            today_windows += count
    return current_window, today_windows


def has_compatible_exclusive_catch_effect(
    effects: Sequence[ActiveFoodEffect],
    *,
    six_star_available: bool,
) -> bool:
    """Whether this catch will be governed by one six-star exclusive effect."""

    return any(
        effect.effect_id in EXCLUSIVE_CATCH_EFFECTS and (effect.effect_id != NEXT_SIX_STAR_CATCH or six_star_available)
        for effect in effects
    )


def has_compatible_exclusive_cook_effect(
    effects: Sequence[ActiveFoodEffect],
    *,
    source_rarity: Rarity | int,
) -> bool:
    """Whether this cook will be governed by one six-star exclusive effect."""

    rarity = Rarity(int(source_rarity))
    return any(
        effect.effect_id in EXCLUSIVE_COOK_EFFECTS
        and (
            (effect.effect_id == NEXT_SIX_STAR_COOK and rarity is Rarity.SIX)
            or (effect.effect_id == NEXT_FIVE_STAR_COOK and rarity is not Rarity.SIX)
        )
        for effect in effects
    )


def _exclusive_skipped_summary(effect: ActiveFoodEffect) -> str:
    return resolve_food_effect(effect.effect_id, effect.params).summary + "（本次由六星菜独占规则接管，未生效且未消耗）"


def _lift_five_and_six_by_points(
    weights: Sequence[float],
    *,
    five_star_bonus: float,
    six_star_bonus: float,
) -> tuple[float, ...]:
    """Fund fixed 5/6-star point bonuses only from the 1-3-star pool."""

    adjusted = list(normalize_weights(weights))
    five_bonus = max(0.0, float(five_star_bonus))
    six_bonus = max(0.0, float(six_star_bonus))
    if adjusted[5] <= 0.0:
        five_bonus += six_bonus
        six_bonus = 0.0
    requested = five_bonus + six_bonus
    donor_total = sum(adjusted[:3])
    if requested <= 0.0 or donor_total <= 0.0:
        return tuple(adjusted)
    funding_scale = min(1.0, donor_total / requested)
    funded = requested * funding_scale
    donor_scale = (donor_total - funded) / donor_total
    adjusted[:3] = [value * donor_scale for value in adjusted[:3]]
    adjusted[4] += five_bonus * funding_scale
    adjusted[5] += six_bonus * funding_scale
    return normalize_weights(adjusted)


def _raise_five_star_cook_floor(
    weights: Sequence[float],
    *,
    five_star_percent: float,
) -> tuple[float, ...]:
    """Raise a normal cook's five-star result to a declared minimum."""

    adjusted = list(normalize_weights(weights))
    target = max(adjusted[4], min(100.0, float(five_star_percent)))
    donor_total = sum(adjusted[:4])
    requested = target - adjusted[4]
    if requested <= 0.0 or donor_total <= 0.0:
        return tuple(adjusted)
    funded = min(donor_total, requested)
    donor_scale = (donor_total - funded) / donor_total
    adjusted[:4] = [value * donor_scale for value in adjusted[:4]]
    adjusted[4] += funded
    adjusted[5] = 0.0
    return normalize_weights(adjusted)


def apply_catch_effects(
    weights: Sequence[float],
    effects: Sequence[ActiveFoodEffect],
) -> CatchEffectApplication:
    """Apply ordinary effect families or one priority six-star exclusive effect."""

    adjusted = list(normalize_weights(weights))
    stature_bias = 0.0
    consumed: list[str] = []
    summaries: list[str] = []
    skipped: list[str] = []
    collaboration_only = False
    giant_template_multiplier = 1.0

    def exclusive_compatible(effect: ActiveFoodEffect) -> bool:
        return effect.effect_id != NEXT_SIX_STAR_CATCH or adjusted[5] > 0

    exclusive, exclusive_skipped = _one_per_group(
        effects,
        EXCLUSIVE_CATCH_EFFECTS,
        compatible=exclusive_compatible,
    )
    skipped.extend(exclusive_skipped)
    if exclusive is not None:
        grant = resolve_food_effect(exclusive.effect_id, exclusive.params)
        if exclusive.effect_id == NEXT_SIX_STAR_CATCH:
            target = float(grant.params["six_star_percent"])
            lower_total = sum(adjusted[:5])
            if lower_total > 0:
                scale = (100.0 - target) / lower_total
                adjusted = [value * scale for value in adjusted[:5]] + [target]
        elif exclusive.effect_id == NEXT_HIGH_STAR_CATCH:
            four = float(grant.params["four_star_percent"])
            five = float(grant.params["five_star_percent"])
            six = float(grant.params["six_star_percent"])
            if adjusted[5] <= 0 and six > 0:
                five += six
                six = 0.0
            adjusted = [0.0, 0.0, 0.0, four, five, six]
        elif exclusive.effect_id == EVEN_CATCH_DISTRIBUTION:
            adjusted = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0] if adjusted[5] > 0 else [1.0, 1.0, 1.0, 1.0, 2.0, 0.0]
        elif exclusive.effect_id == EXCLUSIVE_CATCH_QUALITY:
            multiplier = float(grant.params["multiplier"])
            adjusted = list(
                apply_monotonic_high_rarity_multipliers(
                    adjusted,
                    (1.0, 1.0, 1.0, multiplier, multiplier, multiplier),
                )
            )
        consumed.append(exclusive.effect_entry_id)
        summaries.append(grant.summary)
        already_reported = {
            exclusive.effect_entry_id,
            *(effect.effect_entry_id for effect in effects if effect.effect_id in EXCLUSIVE_CATCH_EFFECTS),
        }
        skipped.extend(
            _exclusive_skipped_summary(effect)
            for effect in effects
            if effect.effect_id in CATCH_EFFECT_IDS and effect.effect_entry_id not in already_reported
        )
        return CatchEffectApplication(
            weights=normalize_weights(adjusted),
            stature_bias=0.0,
            consumed_entry_ids=tuple(consumed),
            summaries=tuple(summaries),
            skipped_summaries=tuple(skipped),
        )

    ordinary_probability_group = CATCH_PROBABILITY_GROUP - EXCLUSIVE_CATCH_EFFECTS

    def ordinary_probability_compatible(effect: ActiveFoodEffect) -> bool:
        if effect.effect_id == NEXT_PIG_RARITY:
            grant = resolve_food_effect(effect.effect_id, effect.params)
            target_index = int(grant.params["rarity"]) - 1
            return adjusted[target_index] > 0.0 and sum(adjusted[:target_index]) > 0.0
        if effect.effect_id == NEXT_GIANT_FIVE_STAR_CATCH:
            return adjusted[4] > 0.0 and sum(adjusted[:4]) > 0.0
        if effect.effect_id == NEXT_SMALL_SIX_STAR_CATCH:
            return adjusted[5] > 0.0 and sum(adjusted[:3]) > 0.0
        return True

    probability_effect, probability_skipped = _one_per_group(
        effects,
        ordinary_probability_group,
        compatible=ordinary_probability_compatible,
    )
    skipped.extend(probability_skipped)
    if probability_effect is not None:
        grant = resolve_food_effect(probability_effect.effect_id, probability_effect.params)
        if probability_effect.effect_id == NEXT_CATCH_QUALITY:
            multiplier = float(grant.params["multiplier"])
            adjusted = list(
                apply_monotonic_high_rarity_multipliers(
                    adjusted,
                    (1.0, 1.0, 1.0, multiplier, multiplier, multiplier),
                )
            )
        elif probability_effect.effect_id == NEXT_PIG_RARITY:
            adjusted = list(
                lift_target_rarity_from_lower(
                    adjusted,
                    target_rarity=int(grant.params["rarity"]),
                    multiplier=float(grant.params["multiplier"]),
                )
            )
        elif probability_effect.effect_id == NEXT_GIANT_FIVE_STAR_CATCH:
            adjusted = list(
                lift_target_rarity_from_lower(
                    adjusted,
                    target_rarity=5,
                    multiplier=float(grant.params["five_star_multiplier"]),
                )
            )
            stature_bias += float(grant.params["stature_bias"])
            giant_template_multiplier = float(grant.params["giant_template_multiplier"])
        elif probability_effect.effect_id == NEXT_COLLABORATION_CATCH:
            adjusted = [
                0.0,
                0.0,
                float(grant.params["three_star_percent"]),
                float(grant.params["four_star_percent"]),
                float(grant.params["five_star_percent"]),
                0.0,
            ]
            collaboration_only = True
        elif probability_effect.effect_id == NEXT_FIVE_SIX_STAR_CATCH:
            adjusted = list(
                _lift_five_and_six_by_points(
                    adjusted,
                    five_star_bonus=float(grant.params["five_star_bonus_percent"]),
                    six_star_bonus=float(grant.params["six_star_bonus_percent"]),
                )
            )
        elif probability_effect.effect_id == NEXT_SMALL_SIX_STAR_CATCH:
            adjusted = list(
                _lift_five_and_six_by_points(
                    adjusted,
                    five_star_bonus=0.0,
                    six_star_bonus=float(grant.params["bonus_percent"]),
                )
            )
        consumed.append(probability_effect.effect_entry_id)
        summaries.append(grant.summary)

    if probability_effect is not None and probability_effect.effect_id == NEXT_GIANT_FIVE_STAR_CATCH:
        for effect in effects:
            if effect.effect_id in CATCH_STATURE_GROUP:
                skipped.append(
                    resolve_food_effect(effect.effect_id, effect.params).summary
                    + "（复合巨物效果已占用体型效果位，未叠加且未消耗）"
                )
    else:
        stature_effect, stature_skipped = _one_per_group(effects, CATCH_STATURE_GROUP)
        skipped.extend(stature_skipped)
        if stature_effect is not None:
            grant = resolve_food_effect(stature_effect.effect_id, stature_effect.params)
            direction = 1.0 if grant.params["mode"] == "giant" else -1.0
            stature_bias += direction * float(grant.params["strength"])
            consumed.append(stature_effect.effect_entry_id)
            summaries.append(grant.summary)
    return CatchEffectApplication(
        weights=normalize_weights(adjusted),
        stature_bias=max(-0.50, min(0.50, stature_bias)),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
        skipped_summaries=tuple(skipped),
        collaboration_only=collaboration_only,
        giant_template_multiplier=giant_template_multiplier,
    )


def has_compatible_exclusive_group_catch_effect(
    effects: Sequence[ActiveGroupFoodEffect],
) -> bool:
    """Whether one per-player group exclusive catch use is still available."""

    return any(
        effect.effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH
        and effect.consumed_uses < effect.granted_uses_per_player
        for effect in effects
    )


def apply_group_catch_effects(
    weights: Sequence[float],
    effects: Sequence[ActiveGroupFoodEffect],
) -> GroupCatchEffectApplication:
    """Apply one exclusive group effect or the strongest ordinary group boost.

    Six-star group boosts never multiply with each other. The exclusive cloud-sea
    pot takes priority and consumes one per-player use. Otherwise the strongest
    current-window multiplier may stack with one item and ordinary food effects;
    its dedicated quota, when present, is consumed separately after settlement.
    """

    adjusted = normalize_weights(weights)
    exclusive_candidates = [
        effect
        for effect in effects
        if effect.effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH
        and effect.consumed_uses < effect.granted_uses_per_player
    ]
    if exclusive_candidates:
        chosen = exclusive_candidates[0]
        grant = resolve_food_effect(chosen.effect_id, chosen.params)
        adjusted = apply_monotonic_high_rarity_multipliers(
            adjusted,
            (
                1.0,
                1.0,
                1.0,
                1.0,
                float(grant.params["five_star_multiplier"]),
                float(grant.params["six_star_multiplier"]),
            ),
        )
        source_label = str(grant.params["source_label"])
        summary = (
            f"{source_label}全群独占（发动群友 ID：{chosen.source_user_id}）："
            "本次 5 星与 6 星相对权重分别 "
            f"×{float(grant.params['five_star_multiplier']):g} 和 "
            f"×{float(grant.params['six_star_multiplier']):g}。"
        )
        skipped = tuple(
            f"{str(effect.params.get('source_label') or '六星菜')}全群加成"
            "（本次由神龙化猪七星云海锅独占，未叠加）"
            for effect in effects
            if effect.group_effect_entry_id != chosen.group_effect_entry_id
        )
        return GroupCatchEffectApplication(
            weights=adjusted,
            consumed_entry_ids=(chosen.group_effect_entry_id,),
            dedicated_entry_id="",
            summaries=(summary,),
            skipped_summaries=skipped,
            exclusive=True,
        )

    ordinary = [
        effect
        for effect in effects
        if effect.effect_id == GROUP_WINDOW_HIGH_STAR_BOOST
    ]
    if not ordinary:
        return GroupCatchEffectApplication(
            weights=adjusted,
            consumed_entry_ids=(),
            dedicated_entry_id="",
            summaries=(),
            skipped_summaries=(),
            exclusive=False,
        )

    def strength(effect: ActiveGroupFoodEffect) -> tuple[float, float]:
        grant = resolve_food_effect(effect.effect_id, effect.params)
        five = float(grant.params["five_star_multiplier"])
        six = float(grant.params["six_star_multiplier"])
        return max(five, six), five + six

    chosen = max(ordinary, key=strength)
    grant = resolve_food_effect(chosen.effect_id, chosen.params)
    five_multiplier = float(grant.params["five_star_multiplier"])
    six_multiplier = float(grant.params["six_star_multiplier"])
    adjusted = apply_monotonic_high_rarity_multipliers(
        adjusted,
        (1.0, 1.0, 1.0, 1.0, five_multiplier, six_multiplier),
    )
    remaining = max(
        0,
        chosen.granted_uses_per_player - chosen.consumed_uses,
    )
    source_label = str(grant.params["source_label"])
    quota_text = f"；专属抓猪额度剩余 {remaining} 次" if remaining else ""
    summary = (
        f"{source_label}全群加成（发动群友 ID：{chosen.source_user_id}）："
        "5 星与 6 星相对权重分别 "
        f"×{five_multiplier:g} 和 ×{six_multiplier:g}{quota_text}。"
    )
    skipped = tuple(
        f"{str(effect.params.get('source_label') or '六星菜')}全群权重加成"
        "（同类六星群体加成只取最高倍率，未相乘）"
        for effect in ordinary
        if effect.group_effect_entry_id != chosen.group_effect_entry_id
    )
    return GroupCatchEffectApplication(
        weights=adjusted,
        consumed_entry_ids=(),
        dedicated_entry_id=(chosen.group_effect_entry_id if remaining else ""),
        summaries=(summary,),
        skipped_summaries=skipped,
        exclusive=False,
    )


def apply_cooking_effects(
    weights: Sequence[float],
    effects: Sequence[ActiveFoodEffect],
    *,
    source_rarity: Rarity | int,
) -> CookingEffectApplication:
    """Apply at most one queued effect from each compatible cooking family."""

    rarity = Rarity(int(source_rarity))
    adjusted = list(normalize_weights(weights))
    consumed: list[str] = []
    summaries: list[str] = []
    skipped: list[str] = []

    def exclusive_compatible(effect: ActiveFoodEffect) -> bool:
        return (effect.effect_id == NEXT_SIX_STAR_COOK and rarity is Rarity.SIX) or (
            effect.effect_id == NEXT_FIVE_STAR_COOK and rarity is not Rarity.SIX
        )

    exclusive, exclusive_skipped = _one_per_group(
        effects,
        EXCLUSIVE_COOK_EFFECTS,
        compatible=exclusive_compatible,
    )
    skipped.extend(exclusive_skipped)
    if exclusive is not None:
        grant = resolve_food_effect(exclusive.effect_id, exclusive.params)
        if exclusive.effect_id == NEXT_SIX_STAR_COOK:
            six_star_percent = float(grant.params["six_star_percent"])
            adjusted = [
                0.0,
                0.0,
                0.0,
                0.0,
                100.0 - six_star_percent,
                six_star_percent,
            ]
        else:
            adjusted = [0.0, 0.0, 0.0, 0.0, 100.0, 0.0]
        consumed.append(exclusive.effect_entry_id)
        summaries.append(grant.summary)
        exclusive_ids = {effect.effect_entry_id for effect in effects if effect.effect_id in EXCLUSIVE_COOK_EFFECTS}
        skipped.extend(
            _exclusive_skipped_summary(effect)
            for effect in effects
            if effect.effect_id in COOK_EFFECT_IDS and effect.effect_entry_id not in exclusive_ids
        )
        return CookingEffectApplication(
            weights=normalize_weights(adjusted),
            consumed_entry_ids=tuple(consumed),
            summaries=tuple(summaries),
            skipped_summaries=tuple(skipped),
        )

    normal_cook_group = frozenset(
        {
            NEXT_COOK_QUALITY,
            NEXT_FOOD_RARITY,
            NEXT_EXTREME_FIVE_STAR_COOK,
        }
    )
    six_star_bonus_group = frozenset({NEXT_SIX_STAR_COOK_BONUS})

    if rarity is Rarity.SIX:
        for effect in effects:
            if effect.effect_id in normal_cook_group:
                skipped.append(
                    resolve_food_effect(effect.effect_id, effect.params).summary
                    + "（当前使用 6 星猪，不适用且未消耗）"
                )

        standard_bonus, standard_skipped = _one_per_group(effects, six_star_bonus_group)
        skipped.extend(standard_skipped)
        selected_ids: set[str] = set()
        total_bonus = 0.0
        if standard_bonus is not None:
            grant = resolve_food_effect(standard_bonus.effect_id, standard_bonus.params)
            selected_ids.add(standard_bonus.effect_entry_id)
            total_bonus += float(grant.params["bonus_percent"])
            summaries.append(grant.summary)

        stackable = [
            effect
            for effect in effects
            if effect.effect_id == NEXT_STACKABLE_SIX_STAR_COOK_BONUS
        ]
        if stackable:
            first_grant = resolve_food_effect(stackable[0].effect_id, stackable[0].params)
            max_stacks = int(first_grant.params["max_stacks"])
            selected_stackable = stackable[:max_stacks]
            for effect in selected_stackable:
                grant = resolve_food_effect(effect.effect_id, effect.params)
                selected_ids.add(effect.effect_entry_id)
                total_bonus += float(grant.params["bonus_percent"])
            if selected_stackable:
                stack_bonus = sum(
                    float(resolve_food_effect(effect.effect_id, effect.params).params["bonus_percent"])
                    for effect in selected_stackable
                )
                summaries.append(
                    f"猪饺叠加 {len(selected_stackable)} 层：本次 6 星菜概率额外 "
                    f"+{stack_bonus:g} 个百分点。"
                )
            skipped.extend(
                resolve_food_effect(effect.effect_id, effect.params).summary
                + f"（已达到 {max_stacks} 层上限，未消耗）"
                for effect in stackable[max_stacks:]
            )

        if total_bonus > 0.0:
            shifted = min(adjusted[4], total_bonus, max(0.0, 50.0 - adjusted[5]))
            adjusted[4] -= shifted
            adjusted[5] += shifted
        consumed.extend(
            effect.effect_entry_id
            for effect in effects
            if effect.effect_entry_id in selected_ids
        )
    else:
        for effect in effects:
            if effect.effect_id in six_star_bonus_group | {NEXT_STACKABLE_SIX_STAR_COOK_BONUS}:
                skipped.append(
                    resolve_food_effect(effect.effect_id, effect.params).summary
                    + "（需要使用 6 星猪，当前未消耗）"
                )

        def normal_cook_compatible(effect: ActiveFoodEffect) -> bool:
            if effect.effect_id != NEXT_FOOD_RARITY:
                return True
            grant = resolve_food_effect(effect.effect_id, effect.params)
            target_index = int(grant.params["rarity"]) - 1
            return adjusted[target_index] > 0.0 and sum(adjusted[:target_index]) > 0.0

        chosen, group_skipped = _one_per_group(
            effects,
            normal_cook_group,
            compatible=normal_cook_compatible,
        )
        skipped.extend(group_skipped)
        if chosen is not None:
            grant = resolve_food_effect(chosen.effect_id, chosen.params)
            if chosen.effect_id == NEXT_COOK_QUALITY:
                lowest_index = next(index for index, value in enumerate(adjusted) if value > 0)
                target_index = min(lowest_index + 1, len(adjusted) - 1)
                shift = min(
                    adjusted[lowest_index],
                    float(grant.params["shift_percent"]),
                )
                adjusted[lowest_index] -= shift
                adjusted[target_index] += shift
            elif chosen.effect_id == NEXT_FOOD_RARITY:
                adjusted = list(
                    lift_target_rarity_from_lower(
                        adjusted,
                        target_rarity=int(grant.params["rarity"]),
                        multiplier=float(grant.params["multiplier"]),
                    )
                )
            elif chosen.effect_id == NEXT_EXTREME_FIVE_STAR_COOK:
                adjusted = list(
                    _raise_five_star_cook_floor(
                        adjusted,
                        five_star_percent=float(grant.params["five_star_percent"]),
                    )
                )
            consumed.append(chosen.effect_entry_id)
            summaries.append(grant.summary)
        adjusted[5] = 0.0
    return CookingEffectApplication(
        weights=normalize_weights(adjusted),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
        skipped_summaries=tuple(skipped),
    )
