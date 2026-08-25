"""第八期限定素材与群体术式的纯领域常量。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .rules import normalize_weights

KFC_PIG_TEMPLATE_ID = "pig-kfc-crazy-thursday"
KFC_FOOD_TEMPLATE_ID = "food-kfc-family-bucket"
GOJO_PIG_TEMPLATE_ID = "pig-jjk-gojo"
SUKUNA_PIG_TEMPLATE_ID = "pig-jjk-sukuna"
SUKUNA_FOOD_TEMPLATE_ID = "food-jjk-malevolent-kitchen"
GOJO_BLUE_FOOD_TEMPLATE_ID = "food-jjk-limitless-blue"
GOJO_RED_FOOD_TEMPLATE_ID = "food-jjk-limitless-red"
GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS = (
    GOJO_BLUE_FOOD_TEMPLATE_ID,
    GOJO_RED_FOOD_TEMPLATE_ID,
)
SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS = frozenset(
    {
        KFC_FOOD_TEMPLATE_ID,
        SUKUNA_FOOD_TEMPLATE_ID,
        *GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS,
    }
)

INVERTED_SPEAR_ITEM_ID = "inverted-spear-of-heaven"

TECHNIQUE_MALEVOLENT_KITCHEN = "malevolent-kitchen"
TECHNIQUE_LAPSE_BLUE = "lapse-blue"
TECHNIQUE_REVERSAL_RED = "reversal-red"
TECHNIQUE_HOLLOW_PURPLE = "hollow-purple"
TECHNIQUE_DOMAIN_GOJO_BYPASS = "domain-gojo-bypass"

GROUP_TECHNIQUE_IDS = frozenset(
    {
        TECHNIQUE_MALEVOLENT_KITCHEN,
        TECHNIQUE_LAPSE_BLUE,
        TECHNIQUE_REVERSAL_RED,
    }
)
PERMIT_TECHNIQUE_IDS = frozenset(
    {
        *GROUP_TECHNIQUE_IDS,
        TECHNIQUE_HOLLOW_PURPLE,
        TECHNIQUE_DOMAIN_GOJO_BYPASS,
    }
)

TECHNIQUE_DISPLAY_NAMES = {
    TECHNIQUE_MALEVOLENT_KITCHEN: "领域展开·伏魔御厨子",
    TECHNIQUE_LAPSE_BLUE: "术式顺转·苍",
    TECHNIQUE_REVERSAL_RED: "术式反转·赫",
    TECHNIQUE_HOLLOW_PURPLE: "虚式·茈",
    TECHNIQUE_DOMAIN_GOJO_BYPASS: "领域内术式解除",
}

# 领域内自动做菜不读取玩家等级、厨具、道具或个人菜品效果。1 至 5 星原料
# 显著偏向本身可达的高档结果；6 星原料按用户指定固定为 25% 六星菜。
DOMAIN_COOKING_WEIGHTS = {
    1: normalize_weights((35, 50, 15, 0, 0, 0)),
    2: normalize_weights((5, 35, 45, 15, 0, 0)),
    3: normalize_weights((0, 5, 30, 45, 20, 0)),
    4: normalize_weights((0, 0, 10, 40, 50, 0)),
    5: normalize_weights((0, 0, 0, 20, 80, 0)),
    6: normalize_weights((0, 0, 0, 0, 75, 25)),
}


def is_crazy_thursday(now: datetime, *, timezone_name: str) -> bool:
    """Return whether ``now`` is Thursday in the configured catch timezone."""

    # Windows 的精简 Python 环境不一定附带 IANA tzdata；插件配置目前也只
    # 允许 Asia/Shanghai，因此直接使用无夏令时的 UTC+8，避免额外运行依赖。
    del timezone_name
    beijing = timezone(timedelta(hours=8), "Asia/Shanghai")
    value = now if now.tzinfo is not None else now.replace(tzinfo=beijing)
    return value.astimezone(beijing).weekday() == 3


def domain_cooking_weights(source_rarity: int) -> tuple[float, ...]:
    """Return the independent fixed cooking distribution for one domain catch."""

    try:
        return DOMAIN_COOKING_WEIGHTS[int(source_rarity)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("领域自动做菜原料品质必须位于 1 至 6。") from exc
