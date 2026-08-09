"""Validated, one-shot effects granted by high-rarity food."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .enums import Rarity
from .errors import FoodEffectError
from .rules import normalize_weights

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
# 糖醋排骨专用独占加权（与普通菜的 next-catch-quality 区分来源）
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

CATCH_EFFECT_IDS = frozenset(
    {
        NEXT_CATCH_QUALITY,
        NEXT_PIG_RARITY,
        NEXT_PIG_STATURE,
        NEXT_SIX_STAR_CATCH,
        NEXT_HIGH_STAR_CATCH,
        EVEN_CATCH_DISTRIBUTION,
        EXCLUSIVE_CATCH_QUALITY,
    }
)
COOK_EFFECT_IDS = frozenset(
    {
        NEXT_COOK_QUALITY,
        NEXT_SIX_STAR_COOK,
        NEXT_SIX_STAR_COOK_BONUS,
        NEXT_FOOD_RARITY,
        NEXT_FIVE_STAR_COOK,
    }
)
QUOTA_EFFECT_IDS = frozenset({CURRENT_WINDOW_CATCHES, TODAY_WINDOW_CATCHES})
IMMEDIATE_EFFECT_IDS = frozenset({WEEKLY_WINDOW_CATCHES, PERMANENT_WINDOW_CATCH})
SUPPORTED_EFFECT_IDS = (
    CATCH_EFFECT_IDS
    | COOK_EFFECT_IDS
    | {EXTRA_CATCHES}
    | QUOTA_EFFECT_IDS
    | IMMEDIATE_EFFECT_IDS
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
        NEXT_FIVE_STAR_COOK,
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


@dataclass(frozen=True, slots=True)
class CookingEffectApplication:
    """Cooking weights after applying queued effects."""

    weights: tuple[float, ...]
    consumed_entry_ids: tuple[str, ...]
    summaries: tuple[str, ...]
    skipped_summaries: tuple[str, ...] = ()


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
    if normalized_id == NEXT_SIX_STAR_COOK:
        percent = _number(raw, "six_star_percent", lower=11.0, upper=60.0)
        return FoodEffectGrant(
            normalized_id,
            {"six_star_percent": percent},
            1,
            f"下一次用 6 星猪做菜时，6 星定制菜概率提升至 {percent:g}%。",
        )
    if normalized_id == NEXT_SIX_STAR_CATCH:
        percent = _number(raw, "six_star_percent", lower=11.0, upper=60.0)
        return FoodEffectGrant(
            normalized_id,
            {"six_star_percent": percent},
            1,
            f"下一次抓猪时，6 星猪概率提升至 {percent:g}%。",
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
            f"本周所有抓猪时段的基础额度额外 +{count} 次；不可重复叠加。",
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
        rarity = _integer(raw, "rarity", lower=1, upper=rarity_upper)
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
            f"下一次{target}时，{rarity} 星{noun}相对权重提升至 ×{multiplier:g}。",
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
            f"接下来 {uses} 次抓猪必定获得高星猪：4 星 {four:g}%、5 星 {five:g}%、6 星 {six:g}%。",
        )
    if normalized_id == NEXT_FIVE_STAR_COOK:
        uses = _integer(raw, "uses", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses},
            uses,
            f"接下来 {uses} 次做菜必定获得 5 星美食。",
        )
    if normalized_id == EVEN_CATCH_DISTRIBUTION:
        uses = _integer(raw, "uses", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"uses": uses},
            uses,
            f"接下来 {uses} 次抓猪，所有品质的获取概率完全相同。",
        )
    if normalized_id == QUOTA_RESET_CHANCE:
        count = _integer(raw, "count", lower=1, upper=3)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
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
            for index in range(3, 6):
                adjusted[index] *= multiplier
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
    for group in (ordinary_probability_group, CATCH_STATURE_GROUP):
        chosen, group_skipped = _one_per_group(effects, group)
        skipped.extend(group_skipped)
        if chosen is None:
            continue
        grant = resolve_food_effect(chosen.effect_id, chosen.params)
        if chosen.effect_id == NEXT_CATCH_QUALITY:
            multiplier = float(grant.params["multiplier"])
            for index in range(3, 6):
                adjusted[index] *= multiplier
        elif chosen.effect_id == NEXT_PIG_RARITY:
            target_index = int(grant.params["rarity"]) - 1
            adjusted[target_index] *= float(grant.params["multiplier"])
        elif chosen.effect_id == NEXT_PIG_STATURE:
            direction = 1.0 if grant.params["mode"] == "giant" else -1.0
            stature_bias += direction * float(grant.params["strength"])
        consumed.append(chosen.effect_entry_id)
        summaries.append(grant.summary)
    return CatchEffectApplication(
        weights=normalize_weights(adjusted),
        stature_bias=max(-0.50, min(0.50, stature_bias)),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
        skipped_summaries=tuple(skipped),
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

    def compatible(effect: ActiveFoodEffect) -> bool:
        if effect.effect_id == NEXT_SIX_STAR_COOK_BONUS:
            return rarity is Rarity.SIX
        return effect.effect_id not in EXCLUSIVE_COOK_EFFECTS and rarity is not Rarity.SIX

    chosen, group_skipped = _one_per_group(
        effects,
        COOK_PROBABILITY_GROUP - EXCLUSIVE_COOK_EFFECTS,
        compatible=compatible,
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
            target_index = int(grant.params["rarity"]) - 1
            adjusted[target_index] *= float(grant.params["multiplier"])
        elif chosen.effect_id == NEXT_SIX_STAR_COOK_BONUS:
            bonus = float(grant.params["bonus_percent"])
            shifted = min(adjusted[4], bonus, max(0.0, 50.0 - adjusted[5]))
            adjusted[4] -= shifted
            adjusted[5] += shifted
        consumed.append(chosen.effect_entry_id)
        summaries.append(grant.summary)
    if rarity is not Rarity.SIX:
        adjusted[5] = 0.0
    return CookingEffectApplication(
        weights=normalize_weights(adjusted),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
        skipped_summaries=tuple(skipped),
    )
