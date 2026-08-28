"""Validated, one-shot effects granted by high-rarity food."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from .enums import Rarity
from .errors import FoodEffectError
from .food_lottery import LOTTERY_DESCRIPTION, YILU_LOTTERY, shuffled_catch_distribution
from .rules import (
    apply_monotonic_high_rarity_multipliers,
    lift_target_rarity_from_lower,
    normalize_weights,
)
from .special_content import (
    TECHNIQUE_DISPLAY_NAMES,
    TECHNIQUE_LAPSE_BLUE,
    TECHNIQUE_MALEVOLENT_KITCHEN,
    TECHNIQUE_REVERSAL_RED,
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
# 达妮娅泡泡云冻：永久累计被动，抓猪/做菜六星概率逐层提升（吃菜时立即累计，不进效果队列）。
PERMANENT_SIX_STAR_PROGRESS = "permanent-six-star-progress"
GROUP_COIN_TRIBUTE = "group-coin-tribute"
TECHNIQUE_PERMIT = "technique-permit"
CURRENT_WINDOW_CATCHES = "current-window-catches"
TODAY_WINDOW_CATCHES = "today-window-catches"
NEXT_SIX_STAR_COOK_BONUS = "next-six-star-cook-bonus"
NEXT_STACKABLE_SIX_STAR_COOK_BONUS = "next-stackable-six-star-cook-bonus"
NEXT_GIANT_FIVE_STAR_CATCH = "next-giant-five-star-catch"
NEXT_COLLABORATION_CATCH = "next-collaboration-catch"
NEXT_EXTREME_FIVE_STAR_COOK = "next-extreme-five-star-cook"
NEXT_FIVE_SIX_STAR_CATCH = "next-five-six-star-catch"
NEXT_SMALL_SIX_STAR_CATCH = "next-small-six-star-catch"
CATCH_DUPLICATION_CHANCE = "catch-duplication-chance"
NEXT_GUARANTEED_SIX_STAR_CATCH = "next-guaranteed-six-star-catch"
ROLLING_DAY_WINDOW_CATCHES = "rolling-day-window-catches"
ROULETTE_CHANCES = "roulette-chances"
SIX_STAR_COOK_FAILURE_RETURN = "six-star-cook-failure-return"
GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH = "group-next-exclusive-high-star-catch"
GROUP_WINDOW_HIGH_STAR_BOOST = "group-window-high-star-boost"
# Schema 18 前糖醋排骨使用的历史独占加权；保留解析能力以兼容旧审计快照。
EXCLUSIVE_CATCH_QUALITY = "exclusive-catch-quality"
SHUFFLED_CATCH_DISTRIBUTION = "shuffled-catch-distribution"
CATCH_REWARD_BONUS = "catch-reward-bonus"
COOK_SERVING_BONUS = "cook-serving-bonus"
FOOD_SUPPLY_PACK = "food-supply-pack"
WEEK_END_WINDOW_CATCHES = "week-end-window-catches"

# 独占效果：六星菜效果独立作用，不与任何其他菜品效果或道具叠加。
# 激活独占效果时，本次动作忽略装备道具（道具保留、不消耗）。
EXCLUSIVE_CATCH_EFFECTS = frozenset(
    {
        NEXT_SIX_STAR_CATCH,
        NEXT_HIGH_STAR_CATCH,
        EVEN_CATCH_DISTRIBUTION,
        NEXT_GUARANTEED_SIX_STAR_CATCH,
        EXCLUSIVE_CATCH_QUALITY,
        SHUFFLED_CATCH_DISTRIBUTION,
    }
)
EXCLUSIVE_COOK_EFFECTS = frozenset({NEXT_SIX_STAR_COOK, NEXT_FIVE_STAR_COOK, SIX_STAR_COOK_FAILURE_RETURN})

# 这三种六星菜自带独立抓猪次数。成功结算时消耗效果次数，但不消耗正常时段额度。
QUOTA_EXEMPT_CATCH_EFFECTS = frozenset(
    {
        NEXT_SIX_STAR_CATCH,
        NEXT_HIGH_STAR_CATCH,
        EVEN_CATCH_DISTRIBUTION,
        SHUFFLED_CATCH_DISTRIBUTION,
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
        CATCH_DUPLICATION_CHANCE,
        NEXT_GUARANTEED_SIX_STAR_CATCH,
        SHUFFLED_CATCH_DISTRIBUTION,
        CATCH_REWARD_BONUS,
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
        SIX_STAR_COOK_FAILURE_RETURN,
        COOK_SERVING_BONUS,
    }
)
QUOTA_EFFECT_IDS = frozenset(
    {CURRENT_WINDOW_CATCHES, TODAY_WINDOW_CATCHES, ROLLING_DAY_WINDOW_CATCHES, WEEK_END_WINDOW_CATCHES}
)
IMMEDIATE_EFFECT_IDS = frozenset(
    {
        WEEKLY_WINDOW_CATCHES,
        PERMANENT_WINDOW_CATCH,
        PERMANENT_SIX_STAR_PROGRESS,
        GROUP_COIN_TRIBUTE,
        TECHNIQUE_PERMIT,
        ROULETTE_CHANCES,
        FOOD_SUPPLY_PACK,
        YILU_LOTTERY,
    }
)
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
        NEXT_GUARANTEED_SIX_STAR_CATCH,
        SHUFFLED_CATCH_DISTRIBUTION,
    }
)
# 抓猪体型组：与概率组正交，可同时生效。
CATCH_STATURE_GROUP = frozenset({NEXT_PIG_STATURE})
# 独立复制组：不改变品质概率，可与普通概率/体型效果同时结算。
CATCH_DUPLICATION_GROUP = frozenset({CATCH_DUPLICATION_CHANCE})
CATCH_REWARD_GROUP = frozenset({CATCH_REWARD_BONUS})
COOK_SERVING_GROUP = frozenset({COOK_SERVING_BONUS})
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
        SIX_STAR_COOK_FAILURE_RETURN,
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
    source_food_rarity: int = 0
    source_food_name: str = ""


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
    duplicate_chance_percent: float = 0.0
    duplication_entry_id: str = ""
    coin_bonus: int = 0
    experience_multiplier: float = 1.0
    shuffle_permutation: tuple[int, ...] = ()
    shuffle_rolls: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class CookingEffectApplication:
    """Cooking weights after applying queued effects."""

    weights: tuple[float, ...]
    consumed_entry_ids: tuple[str, ...]
    summaries: tuple[str, ...]
    skipped_summaries: tuple[str, ...] = ()
    serving_multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class ActiveGroupFoodEffect:
    """One current-scope six-star food effect shared by every player."""

    group_effect_entry_id: str
    effect_id: str
    params: Mapping[str, object]
    granted_uses_per_player: int
    consumed_uses: int
    source_user_id: str
    source_display_name: str
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
    hidden_boost_roll: float | None = None
    hidden_boost_triggered: bool = False
    source_user_id: str = ""
    source_display_name: str = ""
    # 群效果独占结算的本次抓猪是否豁免正常时段额度（额外抓猪次数）。
    quota_exempt: bool = False


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
        source_food_rarity=int(row.get("source_food_rarity") or 0),
        source_food_name=str(row.get("source_food_name") or ""),
    )


def _consumption_summary(
    effect: ActiveFoodEffect,
    summary: str,
) -> str:
    """Append the post-settlement remainder for a genuinely multi-use effect."""

    if effect.granted_uses <= 1:
        return summary
    remaining = max(
        0,
        effect.granted_uses - effect.consumed_uses - 1,
    )
    return f"{summary}（本次结算后剩余 {remaining}/{effect.granted_uses} 次）"


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
    source_display_name = str(row.get("source_display_name") or "").strip()
    return ActiveGroupFoodEffect(
        group_effect_entry_id=str(row["group_effect_entry_id"]),
        effect_id=effect_id,
        params=params,
        granted_uses_per_player=int(row["granted_uses_per_player"]),
        consumed_uses=int(row.get("consumed_uses") or 0),
        source_user_id=str(row.get("source_user_id") or ""),
        # 昵称缺失时使用中性文案，不得回退泄露 QQ 号或 OpenID。
        source_display_name=source_display_name or "未命名群友",
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
    if normalized_id == FOOD_SUPPLY_PACK:
        from .food_supplies import resolve_food_supply_pack

        pack = resolve_food_supply_pack(raw)
        return FoodEffectGrant(normalized_id, {"pack_id": str(raw["pack_id"])}, 1, pack.summary)
    if normalized_id == YILU_LOTTERY:
        if raw:
            raise FoodEffectError("绿芯派奖池使用固定已审核概率，不接受额外参数。")
        return FoodEffectGrant(normalized_id, {}, 1, LOTTERY_DESCRIPTION)
    if normalized_id == SHUFFLED_CATCH_DISTRIBUTION:
        uses = _integer(raw, "uses", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses},
            uses,
            f"获得{uses}次额外抓猪机会：每次随机换位六档基础概率；"
            "不消耗正常额度，不叠加等级、道具或其他菜品加成，其他临时效果保留。",
        )
    if normalized_id == CATCH_REWARD_BONUS:
        uses = _integer(raw, "uses", lower=1, upper=5)
        coins = _integer(raw, "coin_bonus", lower=1, upper=100)
        multiplier = _number(raw, "experience_multiplier", lower=1, upper=2)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses, "coin_bonus": coins, "experience_multiplier": multiplier},
            uses,
            f"接下来{uses}次普通抓猪，每次额外{coins}猪币、抓猪经验×{multiplier:g}；不改变品质概率。",
        )
    if normalized_id == COOK_SERVING_BONUS:
        uses = _integer(raw, "uses", lower=1, upper=5)
        multiplier = _number(raw, "multiplier", lower=1.01, upper=1.6)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses, "multiplier": multiplier},
            uses,
            f"接下来{uses}次使用一至五星猪做菜，成品份量与价值×{multiplier:g}，与道具合计最高×2；不改变品质概率。",
        )
    if normalized_id == WEEK_END_WINDOW_CATCHES:
        count = _integer(raw, "count", lower=1, upper=5)
        return FoodEffectGrant(
            normalized_id, {"count": count}, 1, f"本自然周各抓猪时段基础额度+{count}，下周一北京时间00:00清除。"
        )
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
    if normalized_id == ROLLING_DAY_WINDOW_CATCHES:
        count = _integer(raw, "count", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
            1,
            f"从当前时段起到次日同一时段刷新前，每个抓猪时段基础额度 +{count} 次。",
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
        overflow: dict[str, object] = {}
        overflow_text = ""
        if "overflow_coin" in raw:
            coins = _integer(raw, "overflow_coin", lower=1, upper=100_000)
            weekly = _integer(raw, "overflow_weekly_bonus", lower=1, upper=5)
            if raw.get("overflow_coupon") != "asset-code-change":
                raise FoodEffectError("满层额度派奖励券必须为编号修改券。")
            overflow = {"overflow_coin": coins, "overflow_weekly_bonus": weekly, "overflow_coupon": "asset-code-change"}
            overflow_text = (
                f"满层后每份改为：本自然周各时段额度+{weekly}、{coins}猪币和1张编号修改券；"
                "周加成可累计，下周一00:00清除。"
            )
        return FoodEffectGrant(
            normalized_id,
            {"count": count, "max_bonus": max_bonus, **overflow},
            1,
            f"永久增加所有抓猪时段基础额度 +{count} 次，累计上限 +{max_bonus}。{overflow_text}",
        )
    if normalized_id == PERMANENT_SIX_STAR_PROGRESS:
        catch_bonus = _number(raw, "catch_bonus_per_stack", lower=0.05, upper=2.0)
        cook_bonus = _number(raw, "cook_bonus_per_stack", lower=0.5, upper=5.0)
        max_stacks = _integer(raw, "max_stacks", lower=1, upper=5)
        overflow = {}
        overflow_text = "满层后无法重复食用。"
        if "overflow_coin" in raw:
            coins = _integer(raw, "overflow_coin", lower=1, upper=100_000)
            if raw.get("overflow_coupon") != "pig-choice":
                raise FoodEffectError("满层云冻奖励券必须为猪猪自选券。")
            overflow = {"overflow_coin": coins, "overflow_coupon": "pig-choice"}
            overflow_text = f"满层后每份改为获得{coins}猪币和1张猪猪自选券。"
        return FoodEffectGrant(
            normalized_id,
            {
                "catch_bonus_per_stack": catch_bonus,
                "cook_bonus_per_stack": cook_bonus,
                "max_stacks": max_stacks,
                **overflow,
            },
            1,
            (
                f"永久累计：每层让抓猪 6 星概率 +{catch_bonus:g} 个百分点、"
                f"用 6 星猪做菜的 6 星菜概率 +{cook_bonus:g} 个百分点，"
                f"最多累计 {max_stacks} 层；{overflow_text}"
            ),
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
        bonus = _number(raw, "bonus_percent", lower=0.1, upper=20.0)
        return FoodEffectGrant(
            normalized_id,
            {"bonus_percent": bonus},
            1,
            (f"下一次抓猪时，6 星最终概率额外 +{bonus:g} 个百分点；新增概率只从 1 至 3 星转移，不压低 4 星或 5 星。"),
        )
    if normalized_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH:
        # 可选固定品质分布：存在时替代 5/6 星乘数模式，品质直接按该分布结算。
        fixed_weights: tuple[float, ...] | None = None
        if "fixed_weights" in raw:
            raw_weights = raw["fixed_weights"]
            if not isinstance(raw_weights, (list, tuple)) or len(raw_weights) != 6:
                raise FoodEffectError("fixed_weights 必须正好包含六项品质权重。")
            parsed_weights = [float(value) for value in raw_weights]
            if any(value < 0.0 for value in parsed_weights):
                raise FoodEffectError("fixed_weights 不能包含负数。")
            if sum(parsed_weights) <= 0.0:
                raise FoodEffectError("fixed_weights 总和必须大于零。")
            fixed_weights = tuple(parsed_weights)
        five_multiplier = (
            _number(raw, "five_star_multiplier", lower=1.01, upper=20.0) if "five_star_multiplier" in raw else 1.0
        )
        six_multiplier = (
            _number(raw, "six_star_multiplier", lower=1.01, upper=20.0) if "six_star_multiplier" in raw else 1.0
        )
        if fixed_weights is None and not ("five_star_multiplier" in raw and "six_star_multiplier" in raw):
            raise FoodEffectError("未提供 fixed_weights 时，必须同时提供 five_star_multiplier 与 six_star_multiplier。")
        uses_per_player = _integer(raw, "uses_per_player", lower=1, upper=3)
        self_coin = _integer(raw, "self_coin", lower=0, upper=1_000_000)
        other_coin = _integer(raw, "other_coin", lower=0, upper=1_000_000)
        auto_gift_chance = (
            _number(raw, "auto_gift_chance_percent", lower=0.1, upper=100.0)
            if "auto_gift_chance_percent" in raw
            else 0.0
        )
        # 可选自动赠送品质（缺省仅 6 星，保持旧行为）。
        auto_gift_rarities: tuple[int, ...] = (6,)
        if "auto_gift_rarities" in raw:
            raw_rarities = raw["auto_gift_rarities"]
            if not isinstance(raw_rarities, (list, tuple)) or not raw_rarities:
                raise FoodEffectError("auto_gift_rarities 必须是品质列表。")
            parsed_rarities: list[int] = []
            for value in raw_rarities:
                rarity_value = int(value)
                if not 1 <= rarity_value <= 6:
                    raise FoodEffectError("auto_gift_rarities 只接受 1 至 6 的品质。")
                parsed_rarities.append(rarity_value)
            auto_gift_rarities = tuple(sorted(set(parsed_rarities)))
        # 可选：群效果独占结算的本次抓猪豁免正常额度（额外抓猪次数）。
        quota_exempt = False
        if "quota_exempt" in raw:
            quota_raw = raw["quota_exempt"]
            if isinstance(quota_raw, bool):
                quota_exempt = quota_raw
            elif isinstance(quota_raw, (int, float)):
                quota_exempt = bool(quota_raw)
            else:
                quota_text_value = str(quota_raw).strip().lower()
                if quota_text_value in ("1", "true", "yes", "on"):
                    quota_exempt = True
                elif quota_text_value in ("0", "false", "no", "off"):
                    quota_exempt = False
                else:
                    raise FoodEffectError("美食效果参数 quota_exempt 必须是布尔值。")
        params: dict[str, object] = {
            "uses_per_player": uses_per_player,
            "self_coin": self_coin,
            "other_coin": other_coin,
            "source_label": str(raw.get("source_label") or "神龙化猪七星云海锅").strip(),
        }
        if fixed_weights is not None:
            params["fixed_weights"] = list(fixed_weights)
        else:
            params["five_star_multiplier"] = five_multiplier
            params["six_star_multiplier"] = six_multiplier
        if auto_gift_chance:
            params["auto_gift_chance_percent"] = auto_gift_chance
        if auto_gift_rarities != (6,):
            params["auto_gift_rarities"] = list(auto_gift_rarities)
        if quota_exempt:
            params["quota_exempt"] = True
        gift_text = (
            (
                f"；抓到 {'、'.join(f'{r} 星' for r in auto_gift_rarities)} 猪时有 "
                f"{auto_gift_chance:g}% 概率把该猪自动赠送给本次发动群友"
            )
            if auto_gift_chance
            else ""
        )
        if fixed_weights is not None:
            distribution_text = "、".join(
                f"{index} 星 {value:g}%" for index, value in enumerate(fixed_weights, start=1) if value > 0.0
            )
            effect_summary = (
                f"食用者立即获得 {self_coin} 猪币，其余本群已登记玩家各获得 {other_coin} 猪币；"
                f"有效期内本群每名玩家的下一次抓猪，品质固定为 {distribution_text}，"
                f"并按六星菜独占规则结算{gift_text}。"
            )
        else:
            effect_summary = (
                f"食用者立即获得 {self_coin} 猪币，其余本群已登记玩家各获得 {other_coin} 猪币；"
                f"有效期内本群每名玩家的下一次抓猪，5 星与 6 星相对权重分别 ×{five_multiplier:g} "
                f"和 ×{six_multiplier:g}，并按六星菜独占规则结算{gift_text}。"
            )
        return FoodEffectGrant(
            normalized_id,
            params,
            uses_per_player,
            effect_summary,
        )
    if normalized_id == GROUP_WINDOW_HIGH_STAR_BOOST:
        five_multiplier = _number(raw, "five_star_multiplier", lower=1.001, upper=4.0)
        six_multiplier = _number(raw, "six_star_multiplier", lower=1.001, upper=4.0)
        coin_per_player = _integer(raw, "coin_per_player", lower=0, upper=1_000_000)
        dedicated_catches = _integer(raw, "dedicated_catches", lower=0, upper=20)
        dedicated_only = raw.get("dedicated_only", False)
        if not isinstance(dedicated_only, bool):
            raise FoodEffectError("美食效果参数 dedicated_only 必须是布尔值。")
        if dedicated_only and dedicated_catches <= 0:
            raise FoodEffectError("dedicated_only 启用时，dedicated_catches 必须大于 0。")
        personal_cook_uses = (
            _integer(raw, "personal_six_star_cook_uses", lower=0, upper=10)
            if "personal_six_star_cook_uses" in raw
            else 0
        )
        personal_cook_percent = 0.0
        if personal_cook_uses:
            personal_cook_percent = _number(
                raw,
                "personal_six_star_cook_percent",
                lower=11.0,
                upper=60.0,
            )
        elif "personal_six_star_cook_percent" in raw:
            raise FoodEffectError("个人六星做菜概率存在时，personal_six_star_cook_uses 必须大于 0。")
        hidden_chance = (
            _number(raw, "hidden_boost_chance_percent", lower=0.1, upper=100.0)
            if "hidden_boost_chance_percent" in raw
            else 0.0
        )
        hidden_five_multiplier = 1.0
        hidden_six_multiplier = 1.0
        if hidden_chance:
            hidden_five_multiplier = _number(
                raw,
                "hidden_five_star_multiplier",
                lower=five_multiplier,
                upper=20.0,
            )
            hidden_six_multiplier = _number(
                raw,
                "hidden_six_star_multiplier",
                lower=six_multiplier,
                upper=20.0,
            )
        source_label = str(raw.get("source_label") or "六星菜").strip()
        if not source_label or len(source_label) > 64:
            raise FoodEffectError("群体美食效果的 source_label 必须是 1 至 64 个字符。")
        quota_text = f"，并让每名玩家获得 {dedicated_catches} 次专属抓猪额度" if dedicated_catches else ""
        params: dict[str, object] = {
            "five_star_multiplier": five_multiplier,
            "six_star_multiplier": six_multiplier,
            "coin_per_player": coin_per_player,
            "dedicated_catches": dedicated_catches,
            "source_label": source_label,
        }
        if dedicated_only:
            params["dedicated_only"] = True
        personal_text = ""
        if personal_cook_uses:
            params.update(
                {
                    "personal_six_star_cook_uses": personal_cook_uses,
                    "personal_six_star_cook_percent": personal_cook_percent,
                }
            )
            personal_text = (
                f"；食用者还获得连续 {personal_cook_uses} 次六星猪做菜机会，每次六星菜概率为 {personal_cook_percent:g}%"
            )
        hidden_text = ""
        if hidden_chance:
            params.update(
                {
                    "hidden_boost_chance_percent": hidden_chance,
                    "hidden_five_star_multiplier": hidden_five_multiplier,
                    "hidden_six_star_multiplier": hidden_six_multiplier,
                }
            )
            hidden_text = (
                f"；每次专属抓猪有 {hidden_chance:g}% 概率触发隐藏爆发，"
                f"令本次 5 星与 6 星相对权重改为 ×{hidden_five_multiplier:g} "
                f"和 ×{hidden_six_multiplier:g}"
            )
        if dedicated_only:
            summary = (
                f"本群已登记玩家各获得 {coin_per_player} 猪币和 "
                f"{dedicated_catches} 次额外抓猪机会；机会需在次日同一抓猪时段刷新前使用，"
                f"仅该次抓猪的 5 星与 6 星相对权重分别 ×{five_multiplier:g} "
                f"和 ×{six_multiplier:g}，且不消耗正常抓猪额度，可与道具和非六星菜叠加"
                f"{personal_text}{hidden_text}。"
            )
        else:
            summary = (
                f"本群已登记玩家各获得 {coin_per_player} 猪币{quota_text}；"
                f"到次日同一抓猪时段刷新前，5 星与 6 星相对权重分别 ×{five_multiplier:g} "
                f"和 ×{six_multiplier:g}，可与道具和非六星菜叠加"
                f"{personal_text}{hidden_text}。"
            )
        return FoodEffectGrant(
            normalized_id,
            params,
            max(1, dedicated_catches),
            summary,
        )
    if normalized_id == NEXT_PIG_STATURE:
        mode = str(raw.get("mode") or "").strip()
        if mode not in {"giant", "mini"}:
            raise FoodEffectError("体型美食效果的 mode 只能是 giant 或 mini。")
        strength = _number(raw, "strength", lower=0.05, upper=0.50)
        label = "巨物" if mode == "giant" else "迷你"
        uses = _integer(raw, "uses", lower=1, upper=5) if "uses" in raw else 1
        return FoodEffectGrant(
            normalized_id,
            {"mode": mode, "strength": strength, **({"uses": uses} if "uses" in raw else {})},
            uses,
            (
                f"接下来{uses}次抓猪更容易出现{label}个体，体型偏移{'+' if mode == 'giant' else '-'}{strength:g}。"
                if uses > 1
                else f"下一次抓猪更容易出现{label}个体。"
            ),
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
    if normalized_id == CATCH_DUPLICATION_CHANCE:
        chance = _number(raw, "chance_percent", lower=1.0, upper=100.0)
        uses = _integer(raw, "uses", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"chance_percent": chance, "uses": uses},
            uses,
            (f"接下来 {uses} 次抓猪各有 {chance:g}% 概率复制本次抓到的猪猪；复制品不重复发放抓猪奖励。"),
        )
    if normalized_id == NEXT_GUARANTEED_SIX_STAR_CATCH:
        return FoodEffectGrant(
            normalized_id,
            {},
            1,
            "下一次抓猪必定获得 6 星猪；不增加抓猪额度。",
        )
    if normalized_id == GROUP_COIN_TRIBUTE:
        coin_per_player = _integer(
            raw,
            "coin_per_player",
            lower=1,
            upper=100_000,
        )
        return FoodEffectGrant(
            normalized_id,
            {"coin_per_player": coin_per_player},
            1,
            (f"食用后，本群其他已登记群友各支付 {coin_per_player} 猪币给食用者；余额不足者支付当前全部余额。"),
        )
    if normalized_id == TECHNIQUE_PERMIT:
        technique_id = str(raw.get("technique_id") or "").strip()
        if technique_id not in {
            TECHNIQUE_MALEVOLENT_KITCHEN,
            TECHNIQUE_LAPSE_BLUE,
            TECHNIQUE_REVERSAL_RED,
        }:
            raise FoodEffectError("technique_id 不是可由美食解锁的术式。")
        command = {
            TECHNIQUE_MALEVOLENT_KITCHEN: "/领域展开 伏魔御厨子",
            TECHNIQUE_LAPSE_BLUE: "/术式顺转 苍",
            TECHNIQUE_REVERSAL_RED: "/术式反转 赫",
        }[technique_id]
        return FoodEffectGrant(
            normalized_id,
            {"technique_id": technique_id},
            1,
            f"获得 1 次{TECHNIQUE_DISPLAY_NAMES[technique_id]}发动资格；使用 {command} 发动。",
        )
    if normalized_id == ROULETTE_CHANCES:
        count = _integer(raw, "count", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
            1,
            f"获得 {count} 次猪排轮盘机会；使用 /转轮盘 抽取。",
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
            (f"下一次抓猪必定获得联动猪：3 星 {three:g}%、4 星 {four:g}%、5 星 {five:g}%。"),
        )
    if normalized_id == NEXT_FIVE_SIX_STAR_CATCH:
        five_bonus = _number(raw, "five_star_bonus_percent", lower=0.0, upper=30.0)
        six_bonus = _number(raw, "six_star_bonus_percent", lower=0.1, upper=20.0)
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
        last_six = (
            _number(raw, "last_use_six_star_percent", lower=0.0, upper=100.0)
            if "last_use_six_star_percent" in raw
            else 0.0
        )
        last_four = 0.0
        if last_six:
            last_four = 100.0 - five - last_six
            if last_four < 0.0:
                raise FoodEffectError("高星抓猪小保底的五星与六星概率合计不能超过 100%。")
            if last_six <= six:
                raise FoodEffectError("高星抓猪小保底的六星概率必须高于普通次数。")
        params: dict[str, object] = {
            "uses": uses,
            "four_star_percent": round(four, 4),
            "five_star_percent": round(five, 4),
            "six_star_percent": round(six, 4),
        }
        current_window_only = raw.get("current_window_only", False)
        if not isinstance(current_window_only, bool):
            raise FoodEffectError("current_window_only 必须是布尔值。")
        if current_window_only:
            params["current_window_only"] = True
        pity_text = ""
        if last_six:
            params["last_use_six_star_percent"] = round(last_six, 4)
            pity_text = f"；最后一次小保底为 4 星 {last_four:g}%、5 星 {five:g}%、6 星 {last_six:g}%"
        duration_text = "，且仅在当前抓猪时段有效" if current_window_only else ""
        return FoodEffectGrant(
            normalized_id,
            params,
            uses,
            (
                f"接下来 {uses} 次专属抓猪必定获得高星猪：4 星 {four:g}%、"
                f"5 星 {five:g}%、6 星 {six:g}%{pity_text}；不消耗正常时段额度"
                f"{duration_text}。"
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
    if normalized_id == SIX_STAR_COOK_FAILURE_RETURN:
        uses = _integer(raw, "uses", lower=1, upper=10)
        chance = _number(raw, "return_chance_percent", lower=1.0, upper=100.0)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses, "return_chance_percent": chance},
            uses,
            (f"接下来 {uses} 次使用 6 星猪做菜失败时，各有 {chance:g}% 概率返还原料猪；做出 6 星菜时不消耗保护次数。"),
        )
    if normalized_id == NEXT_EXTREME_FIVE_STAR_COOK:
        percent = _number(raw, "five_star_percent", lower=51.0, upper=95.0)
        return FoodEffectGrant(
            normalized_id,
            {"five_star_percent": percent},
            1,
            (f"下一次使用 1 至 5 星猪做菜时，5 星菜最终概率至少为 {percent:g}%；6 星猪不触发且效果保留。"),
        )
    if normalized_id == EVEN_CATCH_DISTRIBUTION:
        uses = _integer(raw, "uses", lower=1, upper=10)
        last_six = (
            _number(raw, "last_use_six_star_percent", lower=0.0, upper=100.0)
            if "last_use_six_star_percent" in raw
            else 0.0
        )
        if last_six and last_six <= 100.0 / 6.0:
            raise FoodEffectError("轮盘小保底的六星概率必须高于普通轮盘概率。")
        params: dict[str, object] = {"uses": uses}
        pity_text = ""
        if last_six:
            params["last_use_six_star_percent"] = round(last_six, 4)
            pity_text = f"；最后一次小保底将六星概率提升为 {last_six:g}%"
        return FoodEffectGrant(
            normalized_id,
            params,
            uses,
            (f"接下来 {uses} 次专属抓猪，所有品质概率完全相同{pity_text}；不消耗正常时段额度。"),
        )
    if normalized_id == QUOTA_RESET_CHANCE:
        count = _integer(raw, "count", lower=1, upper=3)
        dedicated_catches = (
            _integer(
                raw,
                "group_dedicated_catches",
                lower=0,
                upper=20,
            )
            if "group_dedicated_catches" in raw
            else 0
        )
        five_multiplier = (
            _number(
                raw,
                "five_star_multiplier",
                lower=1.001,
                upper=4.0,
            )
            if "five_star_multiplier" in raw
            else 1.0
        )
        six_multiplier = (
            _number(
                raw,
                "six_star_multiplier",
                lower=1.001,
                upper=4.0,
            )
            if "six_star_multiplier" in raw
            else 1.0
        )
        group_coin = (
            _integer(
                raw,
                "group_coin",
                lower=0,
                upper=1_000_000,
            )
            if "group_coin" in raw
            else 0
        )
        hidden_chance = (
            _number(raw, "hidden_boost_chance_percent", lower=0.1, upper=100.0)
            if "hidden_boost_chance_percent" in raw
            else 0.0
        )
        hidden_five_multiplier = 1.0
        hidden_six_multiplier = 1.0
        if hidden_chance:
            hidden_five_multiplier = _number(
                raw,
                "hidden_five_star_multiplier",
                lower=five_multiplier,
                upper=20.0,
            )
            hidden_six_multiplier = _number(
                raw,
                "hidden_six_star_multiplier",
                lower=six_multiplier,
                upper=20.0,
            )
        params: dict[str, object] = {"count": count}
        if dedicated_catches or group_coin or five_multiplier > 1.0 or six_multiplier > 1.0 or hidden_chance:
            params.update(
                {
                    "group_dedicated_catches": dedicated_catches,
                    "five_star_multiplier": five_multiplier,
                    "six_star_multiplier": six_multiplier,
                    "group_coin": group_coin,
                }
            )
            hidden_text = ""
            if hidden_chance:
                params.update(
                    {
                        "hidden_boost_chance_percent": hidden_chance,
                        "hidden_five_star_multiplier": hidden_five_multiplier,
                        "hidden_six_star_multiplier": hidden_six_multiplier,
                    }
                )
                hidden_text = (
                    f"；每次专属抓猪有 {hidden_chance:g}% 概率令本次 5 星/6 星"
                    f"相对权重爆发为 ×{hidden_five_multiplier:g}/×{hidden_six_multiplier:g}"
                )
            return FoodEffectGrant(
                normalized_id,
                params,
                count,
                (
                    f"获得 {count} 次 /重置额度 机会；每次重置会让本群已登记玩家各获得 "
                    f"{group_coin} 猪币和 {dedicated_catches} 次专属抓猪额度，并在次日同一时段"
                    f"刷新前令 5 星与 6 星相对权重分别 ×{five_multiplier:g} 和 ×{six_multiplier:g}"
                    f"{hidden_text}。"
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
        if effect.effect_id in {
            CURRENT_WINDOW_CATCHES,
            ROLLING_DAY_WINDOW_CATCHES,
        }:
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
        effect.effect_id in EXCLUSIVE_CATCH_EFFECTS
        and (effect.effect_id not in {NEXT_SIX_STAR_CATCH, NEXT_GUARANTEED_SIX_STAR_CATCH} or six_star_available)
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
            or (effect.effect_id == SIX_STAR_COOK_FAILURE_RETURN and rarity is Rarity.SIX)
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


def apply_six_star_progress(
    weights: Sequence[float],
    *,
    stacks: int,
    bonus_per_stack: float,
    action: str,
) -> tuple[float, ...]:
    """Apply the permanent Daniya six-star progress bonus to one action's weights.

    每层为抓猪/做菜六星档提供固定百分点加成，加成只从 1 至 3 星（做菜时从
    非六星可达档）转移，不压低任何更高星级，也不参与独占或互斥效果队列。
    """

    normalized_stacks = max(0, min(5, int(stacks)))
    bonus = normalized_stacks * max(0.0, float(bonus_per_stack))
    if bonus <= 0.0:
        return normalize_weights(weights)
    adjusted = list(normalize_weights(weights))
    if adjusted[5] <= 0.0:
        return tuple(adjusted)
    donor_total = sum(adjusted[:3]) if action == "catch" else sum(adjusted[:5])
    requested = min(bonus, 100.0 - adjusted[5])
    if requested <= 0.0 or donor_total <= 0.0:
        return tuple(adjusted)
    funded = min(donor_total, requested)
    donor_scale = (donor_total - funded) / donor_total
    donor_end = 3 if action == "catch" else 5
    adjusted[:donor_end] = [value * donor_scale for value in adjusted[:donor_end]]
    adjusted[5] += funded
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
    *,
    random_value: Callable[[], float] | None = None,
    shuffle_base_weights: Sequence[float] | None = None,
) -> CatchEffectApplication:
    """Apply ordinary effect families or one priority six-star exclusive effect."""

    effects = tuple(
        sorted(
            effects,
            key=lambda effect: (effect.created_at, effect.effect_entry_id),
        )
    )
    adjusted = list(normalize_weights(weights))
    stature_bias = 0.0
    consumed: list[str] = []
    summaries: list[str] = []
    skipped: list[str] = []
    collaboration_only = False
    giant_template_multiplier = 1.0
    shuffle_permutation: tuple[int, ...] = ()
    shuffle_rolls: tuple[float, ...] = ()

    def exclusive_compatible(effect: ActiveFoodEffect) -> bool:
        return (
            effect.effect_id
            not in {
                NEXT_SIX_STAR_CATCH,
                NEXT_GUARANTEED_SIX_STAR_CATCH,
            }
            or adjusted[5] > 0
        )

    exclusive, exclusive_skipped = _one_per_group(
        effects,
        EXCLUSIVE_CATCH_EFFECTS,
        compatible=exclusive_compatible,
    )
    skipped.extend(exclusive_skipped)
    if exclusive is not None:
        grant = resolve_food_effect(exclusive.effect_id, exclusive.params)
        exclusive_summary = grant.summary
        if exclusive.effect_id == SHUFFLED_CATCH_DISTRIBUTION:
            if random_value is None:
                raise FoodEffectError("雾蓝抓猪结算缺少可审计的随机源。")
            shuffled, shuffle_permutation, shuffle_rolls = shuffled_catch_distribution(
                shuffle_base_weights if shuffle_base_weights is not None else weights, random_value
            )
            adjusted = list(shuffled)
            exclusive_summary += " 本次洗牌：" + " / ".join(f"{value:g}%" for value in shuffled) + "。"
        elif exclusive.effect_id == NEXT_SIX_STAR_CATCH:
            target = float(grant.params["six_star_percent"])
            lower_total = sum(adjusted[:5])
            if lower_total > 0:
                scale = (100.0 - target) / lower_total
                adjusted = [value * scale for value in adjusted[:5]] + [target]
        elif exclusive.effect_id == NEXT_HIGH_STAR_CATCH:
            four = float(grant.params["four_star_percent"])
            five = float(grant.params["five_star_percent"])
            six = float(grant.params["six_star_percent"])
            remaining = exclusive.granted_uses - exclusive.consumed_uses
            last_six = float(grant.params.get("last_use_six_star_percent") or 0.0)
            if remaining == 1 and last_six > 0.0:
                six = last_six
                four = 100.0 - five - six
                exclusive_summary += f" 小保底触发：这是最后一次专属抓猪，六星概率提升为 {six:g}%。"
            if adjusted[5] <= 0 and six > 0:
                five += six
                six = 0.0
            adjusted = [0.0, 0.0, 0.0, four, five, six]
        elif exclusive.effect_id == EVEN_CATCH_DISTRIBUTION:
            remaining = exclusive.granted_uses - exclusive.consumed_uses
            last_six = float(grant.params.get("last_use_six_star_percent") or 0.0)
            if remaining == 1 and last_six > 0.0:
                other = (100.0 - last_six) / 5.0
                adjusted = [other, other, other, other, other, last_six]
                exclusive_summary += f" 小保底触发：这是最后一次专属抓猪，六星概率提升为 {last_six:g}%。"
                if adjusted[5] > 0 and weights[5] <= 0:
                    adjusted[4] += adjusted[5]
                    adjusted[5] = 0.0
            else:
                adjusted = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0] if adjusted[5] > 0 else [1.0, 1.0, 1.0, 1.0, 2.0, 0.0]
        elif exclusive.effect_id == NEXT_GUARANTEED_SIX_STAR_CATCH:
            adjusted = [0.0, 0.0, 0.0, 0.0, 0.0, 100.0] if adjusted[5] > 0.0 else [0.0, 0.0, 0.0, 0.0, 100.0, 0.0]
        elif exclusive.effect_id == EXCLUSIVE_CATCH_QUALITY:
            multiplier = float(grant.params["multiplier"])
            adjusted = list(
                apply_monotonic_high_rarity_multipliers(
                    adjusted,
                    (1.0, 1.0, 1.0, multiplier, multiplier, multiplier),
                )
            )
        consumed.append(exclusive.effect_entry_id)
        summaries.append(_consumption_summary(exclusive, exclusive_summary))
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
            shuffle_permutation=shuffle_permutation,
            shuffle_rolls=shuffle_rolls,
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
        summaries.append(_consumption_summary(probability_effect, grant.summary))

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
            summaries.append(_consumption_summary(stature_effect, grant.summary))
    duplication_effect, duplication_skipped = _one_per_group(
        effects,
        CATCH_DUPLICATION_GROUP,
    )
    skipped.extend(duplication_skipped)
    duplicate_chance = 0.0
    duplication_entry_id = ""
    if duplication_effect is not None:
        grant = resolve_food_effect(
            duplication_effect.effect_id,
            duplication_effect.params,
        )
        duplicate_chance = float(grant.params["chance_percent"])
        duplication_entry_id = duplication_effect.effect_entry_id
        consumed.append(duplication_entry_id)
        summaries.append(_consumption_summary(duplication_effect, grant.summary))
    reward_effect, reward_skipped = _one_per_group(effects, CATCH_REWARD_GROUP)
    skipped.extend(reward_skipped)
    coin_bonus = 0
    experience_multiplier = 1.0
    if reward_effect is not None:
        grant = resolve_food_effect(reward_effect.effect_id, reward_effect.params)
        coin_bonus = int(grant.params["coin_bonus"])
        experience_multiplier = float(grant.params["experience_multiplier"])
        consumed.append(reward_effect.effect_entry_id)
        summaries.append(_consumption_summary(reward_effect, grant.summary))
    return CatchEffectApplication(
        weights=normalize_weights(adjusted),
        stature_bias=max(-0.50, min(0.50, stature_bias)),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
        skipped_summaries=tuple(skipped),
        collaboration_only=collaboration_only,
        giant_template_multiplier=giant_template_multiplier,
        duplicate_chance_percent=duplicate_chance,
        duplication_entry_id=duplication_entry_id,
        coin_bonus=coin_bonus,
        experience_multiplier=experience_multiplier,
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


def _group_effect_display_name(effect: ActiveGroupFoodEffect) -> str:
    """Return a visible nickname without falling back to a platform identifier."""

    display_name = effect.source_display_name.strip()
    source_user_id = effect.source_user_id.strip()
    if not display_name or display_name.casefold() == source_user_id.casefold():
        return "未命名群友"
    return display_name


def _group_catch_effect_available(effect: ActiveGroupFoodEffect) -> bool:
    """Return whether this group effect can still affect the current player."""

    if effect.effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH:
        return effect.consumed_uses < effect.granted_uses_per_player
    if effect.effect_id != GROUP_WINDOW_HIGH_STAR_BOOST:
        return False
    grant = resolve_food_effect(effect.effect_id, effect.params)
    return not bool(grant.params.get("dedicated_only")) or (effect.consumed_uses < effect.granted_uses_per_player)


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
        if effect.effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH and _group_catch_effect_available(effect)
    ]
    if exclusive_candidates:
        chosen = exclusive_candidates[0]
        grant = resolve_food_effect(chosen.effect_id, chosen.params)
        fixed_weights = grant.params.get("fixed_weights")
        source_label = str(grant.params["source_label"])
        source_display_name = _group_effect_display_name(chosen)
        if fixed_weights is not None:
            adjusted = normalize_weights(fixed_weights)
            distribution_text = "、".join(
                f"{index} 星 {value:g}%" for index, value in enumerate(adjusted, start=1) if value > 0.0
            )
            summary = f"{source_label}全群独占（发动群友：{source_display_name}）：本次品质固定为 {distribution_text}。"
        else:
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
            summary = (
                f"{source_label}全群独占（发动群友：{source_display_name}）："
                "本次 5 星与 6 星相对权重分别 "
                f"×{float(grant.params['five_star_multiplier']):g} 和 "
                f"×{float(grant.params['six_star_multiplier']):g}。"
            )
        if chosen.granted_uses_per_player > 1:
            remaining = max(
                0,
                chosen.granted_uses_per_player - chosen.consumed_uses - 1,
            )
            summary += f"（本次结算后剩余 {remaining}/{chosen.granted_uses_per_player} 次）"
        skipped = tuple(
            f"{str(effect.params.get('source_label') or '六星菜')}全群加成（本次由神龙化猪七星云海锅独占，未叠加）"
            for effect in effects
            if effect.group_effect_entry_id != chosen.group_effect_entry_id and _group_catch_effect_available(effect)
        )
        return GroupCatchEffectApplication(
            weights=adjusted,
            consumed_entry_ids=(chosen.group_effect_entry_id,),
            dedicated_entry_id="",
            summaries=(summary,),
            skipped_summaries=skipped,
            exclusive=True,
            source_user_id=chosen.source_user_id,
            source_display_name=source_display_name,
            quota_exempt=bool(grant.params.get("quota_exempt") or False),
        )

    ordinary = [
        effect
        for effect in effects
        if effect.effect_id == GROUP_WINDOW_HIGH_STAR_BOOST and _group_catch_effect_available(effect)
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
    chosen_available = max(
        0,
        chosen.granted_uses_per_player - chosen.consumed_uses,
    )
    dedicated_source = (
        chosen
        if chosen_available
        else next(
            (
                effect
                for effect in ordinary
                if effect.group_effect_entry_id != chosen.group_effect_entry_id
                and resolve_food_effect(effect.effect_id, effect.params).params.get("dedicated_only")
                and effect.consumed_uses < effect.granted_uses_per_player
            ),
            None,
        )
    )
    dedicated_available = (
        max(
            0,
            dedicated_source.granted_uses_per_player - dedicated_source.consumed_uses,
        )
        if dedicated_source is not None
        else 0
    )
    remaining = max(0, dedicated_available - 1)
    source_label = str(grant.params["source_label"])
    source_display_name = _group_effect_display_name(chosen)
    if dedicated_source is chosen and grant.params.get("dedicated_only"):
        quota_text = (
            f"；本次不消耗正常抓猪额度；本次结算后额外抓猪机会剩余 {remaining}/{chosen.granted_uses_per_player} 次"
        )
    else:
        quota_text = (
            f"；本次结算后专属抓猪额度剩余 {remaining}/{chosen.granted_uses_per_player} 次"
            if dedicated_source is chosen
            else ""
        )
    summary = (
        f"{source_label}全群加成（发动群友：{source_display_name}）："
        "5 星与 6 星相对权重分别 "
        f"×{five_multiplier:g} 和 ×{six_multiplier:g}{quota_text}。"
    )
    summaries = [summary]
    if dedicated_source is not None and dedicated_source is not chosen:
        dedicated_grant = resolve_food_effect(
            dedicated_source.effect_id,
            dedicated_source.params,
        )
        dedicated_label = str(dedicated_grant.params["source_label"])
        dedicated_display_name = _group_effect_display_name(dedicated_source)
        summaries.append(
            f"{dedicated_label}全群额外抓猪（发动群友：{dedicated_display_name}）："
            "本次不消耗正常抓猪额度；本次结算后额外抓猪机会剩余 "
            f"{remaining}/{dedicated_source.granted_uses_per_player} 次；"
            f"其 5 星与 6 星 ×{float(dedicated_grant.params['five_star_multiplier']):g}/"
            f"×{float(dedicated_grant.params['six_star_multiplier']):g} 已由更高的 "
            f"{source_label}群倍率覆盖，未相乘。"
        )
    skipped = tuple(
        f"{str(effect.params.get('source_label') or '六星菜')}全群权重加成（同类六星群体加成只取最高倍率，未相乘）"
        for effect in ordinary
        if effect.group_effect_entry_id != chosen.group_effect_entry_id
        and (dedicated_source is None or effect.group_effect_entry_id != dedicated_source.group_effect_entry_id)
    )
    return GroupCatchEffectApplication(
        weights=adjusted,
        consumed_entry_ids=(),
        dedicated_entry_id=(dedicated_source.group_effect_entry_id if dedicated_source is not None else ""),
        summaries=tuple(summaries),
        skipped_summaries=skipped,
        exclusive=False,
        source_user_id=chosen.source_user_id,
        source_display_name=source_display_name,
    )


def group_hidden_boost_chance(
    application: GroupCatchEffectApplication,
    effects: Sequence[ActiveGroupFoodEffect],
) -> float:
    """Return the hidden-proc chance for the selected dedicated group quota."""

    if not application.dedicated_entry_id or application.exclusive:
        return 0.0
    for effect in effects:
        if effect.group_effect_entry_id != application.dedicated_entry_id:
            continue
        grant = resolve_food_effect(effect.effect_id, effect.params)
        return float(grant.params.get("hidden_boost_chance_percent") or 0.0)
    return 0.0


def apply_group_hidden_boost(
    application: GroupCatchEffectApplication,
    effects: Sequence[ActiveGroupFoodEffect],
    *,
    roll: float,
) -> GroupCatchEffectApplication:
    """Resolve one per-catch hidden multiplier without changing quota selection."""

    chance = group_hidden_boost_chance(application, effects)
    if chance <= 0.0:
        return application
    numeric_roll = float(roll)
    if numeric_roll < 0.0 or numeric_roll >= 1.0:
        raise FoodEffectError("隐藏效果随机值必须位于 [0, 1) 区间。")
    if numeric_roll >= chance / 100.0:
        return replace(application, hidden_boost_roll=numeric_roll)

    chosen = next(effect for effect in effects if effect.group_effect_entry_id == application.dedicated_entry_id)
    grant = resolve_food_effect(chosen.effect_id, chosen.params)
    base_five = float(grant.params["five_star_multiplier"])
    base_six = float(grant.params["six_star_multiplier"])
    hidden_five = float(grant.params["hidden_five_star_multiplier"])
    hidden_six = float(grant.params["hidden_six_star_multiplier"])
    boosted_weights = apply_monotonic_high_rarity_multipliers(
        application.weights,
        (
            1.0,
            1.0,
            1.0,
            1.0,
            hidden_five / base_five,
            hidden_six / base_six,
        ),
    )
    source_label = str(grant.params["source_label"])
    source_display_name = _group_effect_display_name(chosen)
    hidden_summary = (
        f"🔥 {source_label}隐藏效果爆发（发动群友：{source_display_name}）："
        f"本次专属抓猪的 5 星与 6 星相对权重由 ×{base_five:g}/×{base_six:g} "
        f"改为 ×{hidden_five:g}/×{hidden_six:g}！"
    )
    return replace(
        application,
        weights=boosted_weights,
        summaries=application.summaries + (hidden_summary,),
        hidden_boost_roll=numeric_roll,
        hidden_boost_triggered=True,
    )


def apply_cooking_effects(
    weights: Sequence[float],
    effects: Sequence[ActiveFoodEffect],
    *,
    source_rarity: Rarity | int,
) -> CookingEffectApplication:
    """Apply at most one queued effect from each compatible cooking family."""

    rarity = Rarity(int(source_rarity))
    effects = tuple(
        sorted(
            effects,
            key=lambda effect: (effect.created_at, effect.effect_entry_id),
        )
    )
    adjusted = list(normalize_weights(weights))
    consumed: list[str] = []
    summaries: list[str] = []
    skipped: list[str] = []

    def exclusive_compatible(effect: ActiveFoodEffect) -> bool:
        return (
            (effect.effect_id == NEXT_SIX_STAR_COOK and rarity is Rarity.SIX)
            or (effect.effect_id == NEXT_FIVE_STAR_COOK and rarity is not Rarity.SIX)
            or (effect.effect_id == SIX_STAR_COOK_FAILURE_RETURN and rarity is Rarity.SIX)
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
        elif exclusive.effect_id == NEXT_FIVE_STAR_COOK:
            adjusted = [0.0, 0.0, 0.0, 0.0, 100.0, 0.0]
        if exclusive.effect_id == SIX_STAR_COOK_FAILURE_RETURN:
            remaining = exclusive.granted_uses - exclusive.consumed_uses
            summaries.append(f"{grant.summary}（本次做菜前剩余 {remaining}/{exclusive.granted_uses} 次）")
        else:
            consumed.append(exclusive.effect_entry_id)
            summaries.append(_consumption_summary(exclusive, grant.summary))
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
                    resolve_food_effect(effect.effect_id, effect.params).summary + "（当前使用 6 星猪，不适用且未消耗）"
                )

        standard_bonus, standard_skipped = _one_per_group(effects, six_star_bonus_group)
        skipped.extend(standard_skipped)
        selected_ids: set[str] = set()
        total_bonus = 0.0
        if standard_bonus is not None:
            grant = resolve_food_effect(standard_bonus.effect_id, standard_bonus.params)
            selected_ids.add(standard_bonus.effect_entry_id)
            total_bonus += float(grant.params["bonus_percent"])
            summaries.append(_consumption_summary(standard_bonus, grant.summary))

        stackable = [effect for effect in effects if effect.effect_id == NEXT_STACKABLE_SIX_STAR_COOK_BONUS]
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
                    f"猪饺叠加 {len(selected_stackable)} 层：本次 6 星菜概率额外 +{stack_bonus:g} 个百分点。"
                )
            skipped.extend(
                resolve_food_effect(effect.effect_id, effect.params).summary + f"（已达到 {max_stacks} 层上限，未消耗）"
                for effect in stackable[max_stacks:]
            )

        if total_bonus > 0.0:
            shifted = min(adjusted[4], total_bonus, max(0.0, 50.0 - adjusted[5]))
            adjusted[4] -= shifted
            adjusted[5] += shifted
        consumed.extend(effect.effect_entry_id for effect in effects if effect.effect_entry_id in selected_ids)
    else:
        for effect in effects:
            if effect.effect_id in six_star_bonus_group | {NEXT_STACKABLE_SIX_STAR_COOK_BONUS}:
                skipped.append(
                    resolve_food_effect(effect.effect_id, effect.params).summary + "（需要使用 6 星猪，当前未消耗）"
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
            summaries.append(_consumption_summary(chosen, grant.summary))
        adjusted[5] = 0.0
    serving_effect, serving_skipped = _one_per_group(
        effects, COOK_SERVING_GROUP, compatible=lambda effect: rarity is not Rarity.SIX
    )
    skipped.extend(serving_skipped)
    serving_multiplier = 1.0
    if serving_effect is not None:
        grant = resolve_food_effect(serving_effect.effect_id, serving_effect.params)
        serving_multiplier = float(grant.params["multiplier"])
        consumed.append(serving_effect.effect_entry_id)
        summaries.append(_consumption_summary(serving_effect, grant.summary))
    return CookingEffectApplication(
        weights=normalize_weights(adjusted),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
        skipped_summaries=tuple(skipped),
        serving_multiplier=serving_multiplier,
    )
