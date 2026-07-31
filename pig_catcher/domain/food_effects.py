"""Validated, one-shot effects granted by high-rarity food."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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

CATCH_EFFECT_IDS = frozenset(
    {
        NEXT_CATCH_QUALITY,
        NEXT_PIG_RARITY,
        NEXT_PIG_STATURE,
    }
)
COOK_EFFECT_IDS = frozenset(
    {
        NEXT_COOK_QUALITY,
        NEXT_SIX_STAR_COOK,
        NEXT_FOOD_RARITY,
    }
)
SUPPORTED_EFFECT_IDS = CATCH_EFFECT_IDS | COOK_EFFECT_IDS | {EXTRA_CATCHES}


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


@dataclass(frozen=True, slots=True)
class CookingEffectApplication:
    """Cooking weights after applying queued effects."""

    weights: tuple[float, ...]
    consumed_entry_ids: tuple[str, ...]
    summaries: tuple[str, ...]


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
        raise FoodEffectError(
            f"美食效果参数 {name} 必须位于 {lower:g} 至 {upper:g}。"
        )
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
    if normalized_id == NEXT_CATCH_QUALITY:
        multiplier = _number(raw, "multiplier", lower=1.01, upper=3.0)
        return FoodEffectGrant(
            normalized_id,
            {"multiplier": multiplier},
            1,
            f"下一次抓猪时，4 至 6 星相对权重提升至 ×{multiplier:g}。",
        )
    if normalized_id == NEXT_COOK_QUALITY:
        shift = _number(raw, "shift_percent", lower=1.0, upper=20.0)
        return FoodEffectGrant(
            normalized_id,
            {"shift_percent": shift},
            1,
            f"下一次做菜时，向更高一档转移 {shift:g} 个百分点。",
        )
    if normalized_id == NEXT_SIX_STAR_COOK:
        percent = _number(raw, "six_star_percent", lower=11.0, upper=35.0)
        return FoodEffectGrant(
            normalized_id,
            {"six_star_percent": percent},
            1,
            f"下一次用 6 星猪做菜时，6 星定制菜概率提升至 {percent:g}%。",
        )
    if normalized_id == EXTRA_CATCHES:
        count = _integer(raw, "count", lower=1, upper=10)
        return FoodEffectGrant(
            normalized_id,
            {"count": count},
            count,
            f"今天额外获得 {count} 次抓猪机会。",
        )
    if normalized_id in {NEXT_PIG_RARITY, NEXT_FOOD_RARITY}:
        rarity_upper = 6 if normalized_id == NEXT_PIG_RARITY else 5
        rarity = _integer(raw, "rarity", lower=1, upper=rarity_upper)
        multiplier = _number(raw, "multiplier", lower=1.01, upper=3.0)
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
        strength = _number(raw, "strength", lower=0.05, upper=0.35)
        label = "巨物" if mode == "giant" else "迷你"
        return FoodEffectGrant(
            normalized_id,
            {"mode": mode, "strength": strength},
            1,
            f"下一次抓猪更容易出现{label}个体。",
        )
    raise FoodEffectError(
        f"美食效果“{normalized_id}”尚未注册，当前不会消耗这份美食。"
    )


def effect_summary(effect_id: str, params: Mapping[str, object]) -> str:
    """Return a stable Chinese summary for cards and catalogs."""

    if not str(effect_id or "").strip():
        return "基础效果：食用后获得抓猪经验。"
    return resolve_food_effect(effect_id, params).summary


def _first_per_effect(
    effects: Sequence[ActiveFoodEffect],
    supported: frozenset[str],
) -> tuple[ActiveFoodEffect, ...]:
    selected: list[ActiveFoodEffect] = []
    seen: set[str] = set()
    for effect in effects:
        if effect.effect_id not in supported or effect.effect_id in seen:
            continue
        seen.add(effect.effect_id)
        selected.append(effect)
    return tuple(selected)


def apply_catch_effects(
    weights: Sequence[float],
    effects: Sequence[ActiveFoodEffect],
) -> CatchEffectApplication:
    """Apply at most one queued effect from each catch-effect family."""

    adjusted = list(normalize_weights(weights))
    stature_bias = 0.0
    consumed: list[str] = []
    summaries: list[str] = []
    for effect in _first_per_effect(effects, CATCH_EFFECT_IDS):
        grant = resolve_food_effect(effect.effect_id, effect.params)
        if effect.effect_id == NEXT_CATCH_QUALITY:
            multiplier = float(grant.params["multiplier"])
            for index in range(3, 6):
                adjusted[index] *= multiplier
        elif effect.effect_id == NEXT_PIG_RARITY:
            target_index = int(grant.params["rarity"]) - 1
            adjusted[target_index] *= float(grant.params["multiplier"])
        elif effect.effect_id == NEXT_PIG_STATURE:
            direction = 1.0 if grant.params["mode"] == "giant" else -1.0
            stature_bias += direction * float(grant.params["strength"])
        consumed.append(effect.effect_entry_id)
        summaries.append(grant.summary)
    return CatchEffectApplication(
        weights=normalize_weights(adjusted),
        stature_bias=max(-0.35, min(0.35, stature_bias)),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
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
    for effect in _first_per_effect(effects, COOK_EFFECT_IDS):
        six_star_only = effect.effect_id == NEXT_SIX_STAR_COOK
        if six_star_only and rarity is not Rarity.SIX:
            continue
        if not six_star_only and rarity is Rarity.SIX:
            continue
        grant = resolve_food_effect(effect.effect_id, effect.params)
        if effect.effect_id == NEXT_COOK_QUALITY:
            lowest_index = next(
                index for index, value in enumerate(adjusted) if value > 0
            )
            target_index = min(lowest_index + 1, len(adjusted) - 1)
            shift = min(
                adjusted[lowest_index],
                float(grant.params["shift_percent"]),
            )
            adjusted[lowest_index] -= shift
            adjusted[target_index] += shift
        elif effect.effect_id == NEXT_FOOD_RARITY:
            target_index = int(grant.params["rarity"]) - 1
            adjusted[target_index] *= float(grant.params["multiplier"])
        elif effect.effect_id == NEXT_SIX_STAR_COOK:
            six_star_percent = float(grant.params["six_star_percent"])
            adjusted = [0.0, 0.0, 0.0, 0.0, 100.0 - six_star_percent, six_star_percent]
        consumed.append(effect.effect_entry_id)
        summaries.append(grant.summary)
    if rarity is not Rarity.SIX:
        adjusted[5] = 0.0
    return CookingEffectApplication(
        weights=normalize_weights(adjusted),
        consumed_entry_ids=tuple(consumed),
        summaries=tuple(summaries),
    )
