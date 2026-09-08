"""固定历史镜像概率。只读取原始抓猪事实，不读取当前背包。"""

from collections.abc import Sequence

from .errors import FoodEffectError

HISTORY_MIRROR_CATCH = "history-mirror-catch"
GROUP_WATER_MIRROR = "group-water-mirror"
MATCHA_PIG_NAMES = frozenset({"抹茶猪咪", "黄瓜猪", "墨提斯猪"})


def mirrored_history_weights(rarities: Sequence[int]) -> list[int]:
    if len(rarities) != 10 or any(type(value) is not int or not 1 <= value <= 6 for value in rarities):
        raise FoodEffectError("翠玉抹茶芭菲需要本群最近10次成功抓猪记录；记录不足，美食不会消耗。")
    return [10 * rarities.count(7 - star) for star in range(1, 7)]
