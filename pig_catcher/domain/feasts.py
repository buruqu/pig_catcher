"""粉蓝冰糕次数链与月栖延迟奖励的固定规则。"""

from .rules import normalize_weights

CLOVER_FEAST = "clover-feast"
CLOVER_CATCH = "clover-dedicated-catch"
CLOVER_COOK = "clover-six-star-cook"
MOON_FEAST = "moon-feast"

CLOVER_DESCRIPTION = (
    "获得10次专属额外抓猪，六星概率+3.07个百分点，不受其他菜品、道具或永久加成影响。"
    "完成全部10次后，每颗星为下一次六星猪做菜增加1个百分点，再加3.07个百分点；"
    "该次加成无论成败均消耗。成功做出六星菜，获得3次同概率专属抓猪与7道随机非六星菜。"
    "专属次数跨时段保留，不消耗普通额度。"
)
MOON_DESCRIPTION = (
    "下一时段禁止所有抓猪，再下一时段获得15次额外抓猪，4/5/6星概率×3；"
    "可叠加商城道具和非六星菜，排除永久提升及其他六星菜。15次机会仅在奖励时段有效，"
    "不消耗普通额度。获得15次机会的奖励时段，全部商城道具8.8折。"
)


def discounted_price(price: int) -> int:
    """猪币仅有整数，先对每件商品向上取整，再乘购买数量。"""
    return (price * 88 + 99) // 100


def moon_weights(weights):
    """把最终4/5/6星概率各乘3，从低星池扣除；高星总概率最多100%。"""
    original = normalize_weights(weights)
    high = sum(original[3:])
    target = min(100.0, high * 3.0)
    if high <= 0 or high >= 100:
        return original
    return tuple(v * (100 - target) / (100 - high) for v in original[:3]) + tuple(
        v * target / high for v in original[3:]
    )
