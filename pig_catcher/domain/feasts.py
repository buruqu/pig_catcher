"""粉蓝冰糕次数链与月栖延迟奖励的固定规则。"""

from .rules import normalize_weights

CLOVER_FEAST = "clover-feast"
CLOVER_CATCH = "clover-dedicated-catch"
CLOVER_COOK = "clover-six-star-cook"
MOON_FEAST = "moon-feast"
CLOVER_INITIAL_CATCHES = 7
CLOVER_COOKS = 3
CLOVER_REWARD_CATCHES = 3
CLOVER_REWARD_FOODS = 7
CLOVER_BONUS_POINTS = (3.07, 30.7)
MOON_REWARD_CATCHES = 18
MOON_HIGH_STAR_MULTIPLIER = 3.5

CLOVER_DESCRIPTION = (
    "获得7次专属额外抓猪；每次独立以50%概率使六星概率+3.07或+30.7个百分点。"
    "完成7次后，下3次六星猪做菜每次独立以50%概率使六星菜概率+3.07或+30.7个百分点；"
    "每次做菜无论成败均消耗一次，成功做出六星菜即获得3次同规则专属抓猪和7道随机非六星菜。"
    "专属抓猪与做菜不受其他菜品、道具及永久提升影响；专属次数跨时段保留，不消耗普通额度。"
)
MOON_DESCRIPTION = (
    "下一时段禁止所有抓猪，再下一时段获得18次额外抓猪，4/5/6星概率×3.5；"
    "可叠加商城道具和其他临时菜品，排除永久提升及其他独立抓猪次数。18次机会仅在奖励时段有效，"
    "不消耗普通额度。获得18次机会的奖励时段，全部商城道具8.8折。"
)


def discounted_price(price: int) -> int:
    """猪币仅有整数，先对每件商品向上取整，再乘购买数量。"""
    return (price * 88 + 99) // 100


def moon_weights(weights):
    """把最终4/5/6星概率各乘3.5，从低星池扣除；高星总概率最多100%。"""
    original = normalize_weights(weights)
    high = sum(original[3:])
    target = min(100.0, high * MOON_HIGH_STAR_MULTIPLIER)
    if high <= 0 or high >= 100:
        return original
    return tuple(v * (100 - target) / (100 - high) for v in original[:3]) + tuple(
        v * target / high for v in original[3:]
    )
