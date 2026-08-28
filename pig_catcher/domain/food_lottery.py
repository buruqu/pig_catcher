"""绿芯派互斥奖池与雾蓝概率洗牌的纯规则。"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .errors import FoodEffectError
from .rules import normalize_weights

YILU_LOTTERY = "yilu-food-lottery"
HINA_PIG_TEMPLATE_ID = "pig-bandori-hina-genius"


@dataclass(frozen=True, slots=True)
class LotteryPrize:
    prize_id: str
    weight: int
    kind: str
    rarity: int
    quantity: int
    label: str
    animation: str = ""


LOTTERY_PRIZES = (
    LotteryPrize("five-star-feast", 30_000, "food", 5, 10, "五星十连盛宴"),
    LotteryPrize("six-star-taste", 50_113, "food", 6, 1, "六星珍味"),
    LotteryPrize("six-star-double", 9_470, "food", 6, 2, "六星双份大奖", "pure-947"),
    LotteryPrize("hina-guest", 9_470, "pig", 5, 1, "日菜来访大奖", "pure-947"),
    LotteryPrize("six-star-jackpot", 947, "food", 6, 6, "六星六连超级大奖", "original-947"),
)


def validated_roll(value: float) -> float:
    roll = float(value)
    if not math.isfinite(roll) or not 0.0 <= roll < 1.0:
        raise FoodEffectError("抽奖随机值必须位于 [0, 1)。")
    return roll


def choose_lottery_prize(roll: float) -> LotteryPrize:
    """整数权重的累计边界只比较一次，五个分支严格互斥。"""

    value = validated_roll(roll)
    cumulative = 0
    total = sum(prize.weight for prize in LOTTERY_PRIZES)
    for prize in LOTTERY_PRIZES:
        cumulative += prize.weight
        if value < cumulative / total:
            return prize
    raise FoodEffectError("奖池没有覆盖当前随机落点。")


def shuffled_catch_distribution(
    weights: Sequence[float], random_value: Callable[[], float]
) -> tuple[tuple[float, ...], tuple[int, ...], tuple[float, ...]]:
    """Fisher–Yates等概率排列六档，使用五次独立随机而非六次随意赋值。"""

    base = normalize_weights(weights)
    order = list(range(6))
    rolls: list[float] = []
    for end in range(5, 0, -1):
        roll = validated_roll(random_value())
        rolls.append(roll)
        chosen = int(roll * (end + 1))
        order[end], order[chosen] = order[chosen], order[end]
    return tuple(base[index] for index in order), tuple(index + 1 for index in order), tuple(rolls)


LOTTERY_DESCRIPTION = (
    "食用后独立抽奖：30%获得10道随机五星菜；50.113%获得1道随机六星菜；"
    "9.47%获得2道随机六星菜；9.47%获得1只天才猪（日菜）；0.947%获得6道随机六星菜。"
    "两种大奖播放纯良947揭晓动效，超级大奖播放原版947揭晓动效；奖励仅自己领取。"
)
