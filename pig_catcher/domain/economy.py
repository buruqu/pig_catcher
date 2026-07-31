"""第四轮做菜、美食价值、奖励和商城的纯领域规则。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .enums import Rarity, UpgradeType
from .errors import DomainValidationError, StoreProductError
from .gameplay import ITEM_DEFINITIONS, ItemDefinition
from .rules import cooking_weights, level_catch_bonus_scale, normalize_weights

COOKWARE_HIGHER_RARITY_STEP = 0.02
LEVEL_COOKING_HIGHER_RARITY_STEP = 0.01

FOOD_RARITY_NAMES: dict[Rarity, str] = {
    Rarity.ONE: "猪食",
    Rarity.TWO: "家常菜",
    Rarity.THREE: "美味大餐",
    Rarity.FOUR: "极品佳肴",
    Rarity.FIVE: "传说珍馐",
    Rarity.SIX: "漂亮定制菜",
}

FOOD_BASE_VALUES: dict[Rarity, int] = {
    Rarity.ONE: 12,
    Rarity.TWO: 35,
    Rarity.THREE: 100,
    Rarity.FOUR: 320,
    Rarity.FIVE: 1100,
    Rarity.SIX: 25000,
}

COOK_COIN_REWARDS: dict[Rarity, int] = {
    Rarity.ONE: 3,
    Rarity.TWO: 7,
    Rarity.THREE: 18,
    Rarity.FOUR: 45,
    Rarity.FIVE: 120,
    Rarity.SIX: 1500,
}

COOK_EXPERIENCE_REWARDS: dict[Rarity, int] = {
    Rarity.ONE: 4,
    Rarity.TWO: 8,
    Rarity.THREE: 18,
    Rarity.FOUR: 40,
    Rarity.FIVE: 85,
    Rarity.SIX: 800,
}

EAT_EXPERIENCE_REWARDS: dict[Rarity, int] = {
    Rarity.ONE: 8,
    Rarity.TWO: 18,
    Rarity.THREE: 40,
    Rarity.FOUR: 65,
    Rarity.FIVE: 110,
    Rarity.SIX: 1200,
}

UPGRADE_DISPLAY_NAMES: dict[UpgradeType, str] = {
    UpgradeType.FEED: "猪饲料升级",
    UpgradeType.COOKWARE: "厨具升级",
}

_UPGRADE_ALIASES: dict[str, UpgradeType] = {
    "猪饲料": UpgradeType.FEED,
    "猪饲料升级": UpgradeType.FEED,
    "饲料": UpgradeType.FEED,
    "厨具": UpgradeType.COOKWARE,
    "厨具升级": UpgradeType.COOKWARE,
}

_RECIPE_KEYWORDS: dict[str, frozenset[str]] = {
    "lean": frozenset({"寿司", "海味", "煎制", "烧烤", "切片", "清爽"}),
    "balanced": frozenset({"米饭", "家常", "面点", "蒸制", "盖饭", "丰盛", "快餐", "蛋包饭"}),
    "fatty": frozenset({"培根", "油炸", "浓香", "汤圆", "甜点", "炖", "五花"}),
}


@dataclass(frozen=True, slots=True)
class FoodAttributes:
    """由原料猪和食谱模板得到的美食份量与官方价值。"""

    portion_weight: float
    official_value: int
    recipe_factor: float


@dataclass(frozen=True, slots=True)
class StoreProduct:
    """商城中的一次性道具或当前可购买升级。"""

    product_id: str
    display_name: str
    category: str
    product_type: str
    unit_price: int
    effect_summary: str
    current_level: int = 0
    target_level: int = 0


def _unit(value: float, *, label: str) -> float:
    normalized = float(value)
    if not 0.0 <= normalized < 1.0:
        raise DomainValidationError(f"{label}必须位于 [0, 1)。")
    return normalized


def cookware_higher_rarity_multiplier(cookware_level: int) -> float:
    """返回厨具对高于原料品质结果的相对权重乘数。"""

    normalized_level = int(cookware_level)
    if not 0 <= normalized_level <= 5:
        raise DomainValidationError("厨具等级必须位于 0 至 5。")
    return 1.0 + COOKWARE_HIGHER_RARITY_STEP * normalized_level


def level_cooking_higher_rarity_multiplier(player_level: int) -> float:
    """返回数值等级对普通做菜高档结果的封顶相对权重乘数。"""

    return 1.0 + LEVEL_COOKING_HIGHER_RARITY_STEP * level_catch_bonus_scale(
        player_level
    )


def adjusted_cooking_weights(
    source_rarity: Rarity | int,
    *,
    size_percentile: float,
    weight_percentile: float,
    cookware_level: int,
    player_level: int = 1,
    chef_spice: bool,
) -> tuple[float, ...]:
    """应用属性、等级、厨具和主厨香料，同时保持六星猪固定 90/10。"""

    try:
        rarity = Rarity(int(source_rarity))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("原料猪品质必须位于 1 至 6。") from exc
    size = float(size_percentile)
    weight = float(weight_percentile)
    if not 0.0 <= size <= 1.0 or not 0.0 <= weight <= 1.0:
        raise DomainValidationError("原料猪属性百分位必须位于 0 至 1。")
    cookware_multiplier = cookware_higher_rarity_multiplier(cookware_level)
    level_multiplier = level_cooking_higher_rarity_multiplier(player_level)
    if rarity is Rarity.SIX:
        return cooking_weights(rarity)

    weights = list(cooking_weights(rarity))
    lowest_index = next(index for index, value in enumerate(weights) if value > 0)
    target_index = min(lowest_index + 1, len(weights) - 1)
    attribute_shift = min(weights[lowest_index], 8.0 * ((size + weight) / 2.0))
    weights[lowest_index] -= attribute_shift
    weights[target_index] += attribute_shift
    if chef_spice:
        spice_shift = min(weights[lowest_index], 6.0)
        weights[lowest_index] -= spice_shift
        weights[target_index] += spice_shift

    higher_multiplier = cookware_multiplier * level_multiplier
    source_index = int(rarity) - 1
    for index in range(source_index + 1, len(weights)):
        weights[index] *= higher_multiplier
    weights[5] = 0.0
    return normalize_weights(weights)


def recipe_affinity(recipe_tags: tuple[str, ...] | list[str]) -> str:
    """从审核过的静态标签确定偏瘦、均衡或偏肥食谱倾向。"""

    normalized = {str(tag).strip() for tag in recipe_tags if str(tag).strip()}
    scores = {
        affinity: len(normalized.intersection(keywords))
        for affinity, keywords in _RECIPE_KEYWORDS.items()
    }
    best = max(scores, key=scores.__getitem__)
    return best if scores[best] > 0 else "balanced"


def stable_recipe_factor(template_id: str) -> float:
    """按稳定模板 ID 映射到文档规定的 0.90 至 1.10 固定食谱系数。"""

    normalized = str(template_id or "").strip()
    if not normalized:
        raise DomainValidationError("美食模板 ID 不能为空。")
    ratio = int.from_bytes(sha256(normalized.encode("utf-8")).digest()[:4], "big") / 0xFFFFFFFF
    return round(0.90 + ratio * 0.20, 6)


def generate_food_attributes(
    *,
    rarity: Rarity | int,
    template_id: str,
    source_weight: float,
    source_weight_percentile: float,
    portion_roll: float,
) -> FoodAttributes:
    """生成份量，并按既定食谱系数与原料重量百分位计算官方价值。"""

    try:
        resolved_rarity = Rarity(int(rarity))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("美食品质必须位于 1 至 6。") from exc
    source_weight_value = float(source_weight)
    source_percentile = float(source_weight_percentile)
    if source_weight_value <= 0:
        raise DomainValidationError("原料猪重量必须大于零。")
    if not 0.0 <= source_percentile <= 1.0:
        raise DomainValidationError("原料猪重量百分位必须位于 0 至 1。")
    roll = _unit(portion_roll, label="美食份量随机值")
    portion_weight = source_weight_value * (0.35 + 0.15 * roll)
    recipe_factor = stable_recipe_factor(template_id)
    portion_factor = 0.85 + 0.30 * source_percentile
    official_value = round(
        FOOD_BASE_VALUES[resolved_rarity] * portion_factor * recipe_factor
    )
    return FoodAttributes(
        portion_weight=round(portion_weight, 6),
        official_value=official_value,
        recipe_factor=recipe_factor,
    )


def upgrade_type_by_name(name: str) -> UpgradeType | None:
    """返回升级商品类型；普通道具名称返回 ``None``。"""

    return _UPGRADE_ALIASES.get(str(name or "").strip())


def item_product_by_name(name: str) -> ItemDefinition:
    """按商城显示名称读取一次性道具。"""

    normalized = str(name or "").strip()
    for item in ITEM_DEFINITIONS:
        if item.display_name == normalized:
            return item
    choices = "、".join(item.display_name for item in ITEM_DEFINITIONS)
    raise StoreProductError(f"商城中没有“{normalized}”。可购买道具：{choices}")


def build_store_products(
    *,
    feed_level: int,
    cookware_level: int,
    feed_prices: list[int],
    cookware_prices: list[int],
) -> tuple[StoreProduct, ...]:
    """生成当前玩家可见的固定道具和下一等级升级商品。"""

    products = [
        StoreProduct(
            product_id=item.item_id,
            display_name=item.display_name,
            category="抓猪道具" if item.action_type == "catching" else "做菜道具",
            product_type="item",
            unit_price=item.price,
            effect_summary=item.effect_summary,
        )
        for item in ITEM_DEFINITIONS
    ]
    for upgrade_type, level, prices in (
        (UpgradeType.FEED, feed_level, feed_prices),
        (UpgradeType.COOKWARE, cookware_level, cookware_prices),
    ):
        target = min(level + 1, 5)
        products.append(
            StoreProduct(
                product_id=f"upgrade-{upgrade_type.value}",
                display_name=UPGRADE_DISPLAY_NAMES[upgrade_type],
                category="永久升级",
                product_type="upgrade",
                unit_price=prices[level] if level < 5 else 0,
                effect_summary="已满级" if level >= 5 else f"购买后提升至 Lv.{target}",
                current_level=level,
                target_level=target,
            )
        )
    return tuple(products)
