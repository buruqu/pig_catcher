"""第一版概率和固定料理规则。"""

from __future__ import annotations

from collections.abc import Sequence

from .enums import Rarity
from .errors import DomainValidationError

BASE_CATCH_WEIGHTS: tuple[float, ...] = (40.0, 30.0, 17.0, 8.0, 4.0, 1.0)

BASE_COOKING_WEIGHTS: dict[Rarity, tuple[float, ...]] = {
    Rarity.ONE: (75.0, 22.0, 3.0, 0.0, 0.0, 0.0),
    Rarity.TWO: (15.0, 65.0, 18.0, 2.0, 0.0, 0.0),
    Rarity.THREE: (0.0, 20.0, 60.0, 18.0, 2.0, 0.0),
    Rarity.FOUR: (0.0, 5.0, 25.0, 60.0, 10.0, 0.0),
    Rarity.FIVE: (0.0, 0.0, 5.0, 25.0, 70.0, 0.0),
    Rarity.SIX: (0.0, 0.0, 0.0, 0.0, 90.0, 10.0),
}


def normalize_weights(weights: Sequence[float]) -> tuple[float, ...]:
    """校验非负权重并归一化为总和 100。"""

    if len(weights) != 6:
        raise DomainValidationError("品质权重必须正好包含六项。")
    normalized = tuple(float(value) for value in weights)
    if any(value < 0 for value in normalized):
        raise DomainValidationError("品质权重不能为负数。")
    total = sum(normalized)
    if total <= 0:
        raise DomainValidationError("品质权重总和必须大于零。")
    return tuple(value * 100.0 / total for value in normalized)


def catch_weights(
    base_weights: Sequence[float] = BASE_CATCH_WEIGHTS,
    *,
    feed_level: int = 0,
    lucky_whistle: bool = False,
    six_star_available: bool = True,
) -> tuple[float, ...]:
    """计算饲料、消耗品和六星资格修正后的抓取权重。"""

    if not 0 <= feed_level <= 5:
        raise DomainValidationError("猪饲料等级必须位于 0 至 5。")
    weights = list(normalize_weights(base_weights))
    feed_multipliers = (
        1.0,
        1.0 + 0.01 * feed_level,
        1.0 + 0.02 * feed_level,
        1.0 + 0.03 * feed_level,
        1.0 + 0.04 * feed_level,
        1.0 + 0.01 * feed_level,
    )
    weights = [value * multiplier for value, multiplier in zip(weights, feed_multipliers, strict=True)]
    if lucky_whistle:
        for index in range(2, 5):
            weights[index] *= 1.12
        weights[5] *= 1.02
    if not six_star_available:
        weights[4] += weights[5]
        weights[5] = 0.0
    return normalize_weights(weights)


def cooking_weights(pig_rarity: Rarity | int) -> tuple[float, ...]:
    """返回对应猪品质的首版基础料理矩阵。"""

    try:
        rarity = Rarity(int(pig_rarity))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("猪品质必须位于 1 至 6。") from exc
    return BASE_COOKING_WEIGHTS[rarity]


def choose_rarity(weights: Sequence[float], roll: float) -> Rarity:
    """按左闭右开随机落点选择品质。"""

    normalized = normalize_weights(weights)
    value = float(roll)
    if not 0.0 <= value < 1.0:
        raise DomainValidationError("随机落点必须位于 [0, 1)。")
    target = value * 100.0
    cumulative = 0.0
    for rarity, weight in zip(Rarity, normalized, strict=True):
        cumulative += weight
        if target < cumulative:
            return rarity
    for rarity, weight in reversed(tuple(zip(Rarity, normalized, strict=True))):
        if weight > 0:
            return rarity
    raise DomainValidationError("品质权重没有可选结果。")
