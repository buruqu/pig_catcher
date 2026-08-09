"""第一版概率和固定料理规则。"""

from __future__ import annotations

from collections.abc import Sequence

from .enums import Rarity
from .errors import DomainValidationError

BASE_CATCH_WEIGHTS: tuple[float, ...] = (40.0, 30.0, 17.0, 8.0, 4.0, 1.0)
LUCKY_WHISTLE_BASE_WEIGHTS: tuple[float, ...] = (34.0, 27.0, 16.0, 12.0, 7.0, 4.0)
SUPER_LUCKY_WHISTLE_BASE_WEIGHTS: tuple[float, ...] = (
    27.0,
    23.0,
    15.0,
    15.0,
    12.0,
    8.0,
)
STAR_PIG_RADAR_BASE_WEIGHTS: tuple[float, ...] = (0.0, 0.0, 45.0, 30.0, 18.0, 7.0)
FEED_RARITY_MULTIPLIER_STEPS: tuple[float, ...] = (
    0.0,
    0.01,
    0.02,
    0.03,
    0.04,
    0.01,
)
LEVEL_CATCH_BONUS_INTERVAL = 4
LEVEL_CATCH_BONUS_MAX_SCALE = 5.0
LEVEL_CATCH_BONUS_CAP_LEVEL = int(LEVEL_CATCH_BONUS_MAX_SCALE) * LEVEL_CATCH_BONUS_INTERVAL + 1

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


def feed_rarity_multipliers(feed_level: int) -> tuple[float, ...]:
    """返回猪饲料对六档抓猪权重的逐级相对乘数。"""

    normalized_level = int(feed_level)
    if not 0 <= normalized_level <= 5:
        raise DomainValidationError("猪饲料等级必须位于 0 至 5。")
    return tuple(1.0 + step * normalized_level for step in FEED_RARITY_MULTIPLIER_STEPS)


def level_catch_bonus_scale(player_level: int) -> float:
    """把数值等级映射为 0 至 5 的透明抓猪成长档。"""

    normalized_level = int(player_level)
    if normalized_level < 1:
        raise DomainValidationError("玩家等级必须大于等于 1。")
    if normalized_level >= LEVEL_CATCH_BONUS_CAP_LEVEL:
        return LEVEL_CATCH_BONUS_MAX_SCALE
    return (normalized_level - 1) / LEVEL_CATCH_BONUS_INTERVAL


def level_catch_rarity_multipliers(player_level: int) -> tuple[float, ...]:
    """返回数值等级对六档抓猪权重的封顶相对乘数。"""

    scale = level_catch_bonus_scale(player_level)
    return tuple(1.0 + step * scale for step in FEED_RARITY_MULTIPLIER_STEPS)


def catch_weights(
    base_weights: Sequence[float] = BASE_CATCH_WEIGHTS,
    *,
    feed_level: int = 0,
    player_level: int = 1,
    lucky_whistle: bool = False,
    super_lucky_whistle: bool = False,
    item_id: str = "",
    six_star_available: bool = True,
) -> tuple[float, ...]:
    """计算等级、饲料、互斥消耗品和六星资格修正后的抓取权重。"""

    selected_item = str(item_id or "").strip()
    legacy_items = int(lucky_whistle) + int(super_lucky_whistle)
    if legacy_items > 1 or (selected_item and legacy_items):
        raise DomainValidationError("一次抓猪只能应用一个品质概率道具。")
    if lucky_whistle:
        selected_item = "lucky-whistle"
    elif super_lucky_whistle:
        selected_item = "super-lucky-whistle"

    weights = list(normalize_weights(base_weights))
    feed_multipliers = feed_rarity_multipliers(feed_level)
    level_multipliers = level_catch_rarity_multipliers(player_level)
    weights = [
        value * feed_multiplier * level_multiplier
        for value, feed_multiplier, level_multiplier in zip(
            weights,
            feed_multipliers,
            level_multipliers,
            strict=True,
        )
    ]
    item_distributions = {
        "lucky-whistle": LUCKY_WHISTLE_BASE_WEIGHTS,
        "super-lucky-whistle": SUPER_LUCKY_WHISTLE_BASE_WEIGHTS,
        "star-pig-radar": STAR_PIG_RADAR_BASE_WEIGHTS,
    }
    target_distribution = item_distributions.get(selected_item)
    if target_distribution is not None:
        weights = [
            value * (target / baseline if baseline > 0 else 0.0)
            for value, target, baseline in zip(
                weights,
                target_distribution,
                BASE_CATCH_WEIGHTS,
                strict=True,
            )
        ]
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
