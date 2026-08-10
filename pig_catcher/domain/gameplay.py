"""第三轮抓猪、等级和道具的纯领域定义。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt

from .enums import FatProfile, Rarity
from .errors import DomainValidationError

PIG_RARITY_NAMES: dict[Rarity, str] = {
    Rarity.ONE: "普通家养猪",
    Rarity.TWO: "美味家养猪",
    Rarity.THREE: "优质家养猪",
    Rarity.FOUR: "极品佳肴猪",
    Rarity.FIVE: "传说珍馐猪",
    Rarity.SIX: "可爱猪群友",
}

PIG_BASE_VALUES: dict[Rarity, int] = {
    Rarity.ONE: 20,
    Rarity.TWO: 55,
    Rarity.THREE: 150,
    Rarity.FOUR: 450,
    Rarity.FIVE: 1500,
    Rarity.SIX: 5000,
}

CATCH_COIN_REWARDS: dict[Rarity, int] = {
    Rarity.ONE: 2,
    Rarity.TWO: 5,
    Rarity.THREE: 12,
    Rarity.FOUR: 30,
    Rarity.FIVE: 80,
    Rarity.SIX: 250,
}

CATCH_EXPERIENCE_REWARDS: dict[Rarity, int] = {
    Rarity.ONE: 5,
    Rarity.TWO: 10,
    Rarity.THREE: 22,
    Rarity.FOUR: 45,
    Rarity.FIVE: 90,
    Rarity.SIX: 180,
}

LEVEL_THRESHOLDS: tuple[tuple[str, int], ...] = (
    ("被猪拱", 0),
    ("抓猪萌新", 100),
    ("抓猪老手", 500),
    ("抓猪高手", 1800),
    ("抓猪大神", 6000),
    ("抓群友", 20000),
)

LEVEL_EXPERIENCE_FACTOR = 50


@dataclass(frozen=True, slots=True)
class ItemDefinition:
    """可装备到下一次兼容动作的一次性道具。"""

    item_id: str
    display_name: str
    action_type: str
    price: int
    effect_summary: str


ITEM_DEFINITIONS: tuple[ItemDefinition, ...] = (
    ItemDefinition(
        "lucky-whistle",
        "幸运猪哨",
        "catching",
        480,
        "下一次抓猪：先将基准六档调整为 34% / 27% / 16% / 12% / 7% / 4%，"
        "再叠加等级与饲料；4 至 6 星均不会被成长加成压低",
    ),
    ItemDefinition(
        "super-lucky-whistle",
        "超级幸运猪哨",
        "catching",
        1320,
        "下一次抓猪：先将基准六档调整为 27% / 23% / 15% / 15% / 12% / 8%，"
        "再叠加等级与饲料；4 至 6 星均不会被成长加成压低",
    ),
    ItemDefinition(
        "star-pig-radar",
        "星辉探猪镜",
        "catching",
        1680,
        "下一次抓猪必为 3 至 6 星，基准概率为 45% / 30% / 18% / 7%；再叠加等级与饲料且不会压低 4 至 6 星",
    ),
    ItemDefinition(
        "giant-corn",
        "巨物玉米",
        "catching",
        240,
        "下一次抓猪体型百分位 +22%、重量百分位 +14%",
    ),
    ItemDefinition(
        "fattening-bean-cake",
        "增膘豆饼",
        "catching",
        200,
        "下一次抓猪肥瘦率 +22 点、重量百分位 +12%",
    ),
    ItemDefinition(
        "lean-green-feed",
        "精瘦青饲料",
        "catching",
        200,
        "下一次抓猪肥瘦率 -22 点、体型百分位 +10%、重量百分位 +5%",
    ),
    ItemDefinition(
        "coin-bounty-tag",
        "猪币悬赏牌",
        "catching",
        620,
        "下一次抓猪的猪币奖励 ×2、经验奖励 ×1.5；不改变品质概率",
    ),
    ItemDefinition(
        "chef-spice",
        "主厨香料",
        "cooking",
        480,
        "下一次用 1 至 5 星猪做菜：从最低可出档向高一档转移最多 18 个百分点",
    ),
    ItemDefinition(
        "super-chef-spice",
        "超级主厨香料",
        "cooking",
        1180,
        "下一次用 6 星猪做菜：6 星菜概率额外 +10 个百分点；遇六星菜独占效果时保留不消耗",
    ),
    ItemDefinition(
        "precision-knife",
        "精准刀工券",
        "cooking",
        220,
        "下一次普通做菜优先偏瘦食谱，成品份量与价值额外 +12%",
    ),
    ItemDefinition(
        "slow-cook-seasoning",
        "慢炖调料包",
        "cooking",
        260,
        "下一次普通做菜优先偏肥食谱，成品份量与价值额外 +18%",
    ),
    ItemDefinition(
        "large-lunch-box",
        "大份餐盒",
        "cooking",
        520,
        "下一次普通做菜有 45% 概率额外获得同款成品一份",
    ),
    ItemDefinition(
        "no-downgrade-lid",
        "稳火保底锅盖",
        "cooking",
        780,
        "下一次用 1 至 5 星猪做菜不会产出低于原料品质的美食",
    ),
    ItemDefinition(
        "ascension-stove-core",
        "升星炉芯",
        "cooking",
        1080,
        "下一次用 1 至 4 星猪做菜，高于原料品质的相对权重提升至 ×2.5",
    ),
    ItemDefinition(
        "harvest-apron",
        "丰收围裙",
        "cooking",
        460,
        "下一次普通做菜的所有成品份量与价值额外 +25%",
    ),
)

ITEMS_BY_ID = {item.item_id: item for item in ITEM_DEFINITIONS}
ITEMS_BY_NAME = {item.display_name: item for item in ITEM_DEFINITIONS}


@dataclass(frozen=True, slots=True)
class PigAttributes:
    """一次抓取得到的相关属性与价值快照。"""

    size_value: float
    size_percentile: float
    weight_value: float
    weight_percentile: float
    fat_ratio: float
    official_value: int

    @property
    def fat_category(self) -> str:
        if self.fat_ratio <= 35:
            return "lean"
        if self.fat_ratio <= 64:
            return "balanced"
        return "fatty"


@dataclass(frozen=True, slots=True)
class LevelProgress:
    """累计经验对应的数值等级、荣誉称号与下一等级进度。"""

    level: int
    title: str
    experience: int
    current_threshold: int
    next_threshold: int | None

    @property
    def progress_percent(self) -> float:
        if self.next_threshold is None:
            return 100.0
        span = self.next_threshold - self.current_threshold
        if span <= 0:
            return 100.0
        return min(100.0, max(0.0, (self.experience - self.current_threshold) * 100.0 / span))


def _unit(value: float, *, name: str) -> float:
    normalized = float(value)
    if not 0.0 <= normalized < 1.0:
        raise DomainValidationError(f"{name}必须位于 [0, 1)。")
    return normalized


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def item_by_name(name: str) -> ItemDefinition:
    """按中文显示名解析道具。"""

    normalized = str(name or "").strip()
    try:
        return ITEMS_BY_NAME[normalized]
    except KeyError as exc:
        choices = "、".join(item.display_name for item in ITEM_DEFINITIONS)
        raise DomainValidationError(f"未知道具“{normalized}”。可用道具：{choices}") from exc


def item_by_id(item_id: str) -> ItemDefinition:
    """读取已持久化道具 ID，未知 ID 视为数据错误。"""

    try:
        return ITEMS_BY_ID[str(item_id or "").strip()]
    except KeyError as exc:
        raise DomainValidationError("已装备道具不存在于当前规则版本。") from exc


def generate_pig_attributes(
    *,
    rarity: Rarity | int,
    length_min: float,
    length_max: float,
    weight_min: float,
    weight_max: float,
    fat_profile: FatProfile | str,
    random_values: tuple[float, float, float, float, float],
    item_id: str = "",
    stature_bias: float = 0.0,
) -> PigAttributes:
    """用中心更常见的相关分布生成体型、重量、肥瘦率和价值。"""

    try:
        resolved_rarity = Rarity(int(rarity))
        resolved_profile = FatProfile(str(fat_profile))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("猪模板的品质或肥瘦画像无效。") from exc
    if length_min <= 0 or length_max < length_min:
        raise DomainValidationError("猪模板体型范围无效。")
    if weight_min <= 0 or weight_max < weight_min:
        raise DomainValidationError("猪模板重量范围无效。")
    first, second, third, fourth, fifth = (
        _unit(value, name=f"属性随机值 {index}") for index, value in enumerate(random_values, start=1)
    )
    size_percentile = (first + second) / 2.0
    condition_noise = (third + fourth) / 2.0
    weight_percentile = _clamp(0.65 * size_percentile + 0.35 * condition_noise, 0.0, 1.0)
    profile_range = {
        FatProfile.LEAN: (5.0, 55.0),
        FatProfile.BALANCED: (25.0, 75.0),
        FatProfile.FATTY: (45.0, 95.0),
    }[resolved_profile]
    fat_ratio = profile_range[0] + (profile_range[1] - profile_range[0]) * fifth
    bias = float(stature_bias)
    if not -0.50 <= bias <= 0.50:
        raise DomainValidationError("体型效果偏移必须位于 -0.50 至 0.50。")
    size_percentile = _clamp(size_percentile + bias, 0.0, 1.0)
    weight_percentile = _clamp(weight_percentile + bias * 0.65, 0.0, 1.0)

    if item_id:
        item = item_by_id(item_id)
        if item.action_type != "catching":
            raise DomainValidationError("做菜道具不能用于抓猪。")
        if item.item_id == "giant-corn":
            size_percentile = _clamp(size_percentile + 0.22, 0.0, 1.0)
            weight_percentile = _clamp(weight_percentile + 0.14, 0.0, 1.0)
        elif item.item_id == "fattening-bean-cake":
            fat_ratio = _clamp(fat_ratio + 22.0, 0.0, 100.0)
            weight_percentile = _clamp(weight_percentile + 0.12, 0.0, 1.0)
        elif item.item_id == "lean-green-feed":
            fat_ratio = _clamp(fat_ratio - 22.0, 0.0, 100.0)
            size_percentile = _clamp(size_percentile + 0.10, 0.0, 1.0)
            weight_percentile = _clamp(weight_percentile + 0.05, 0.0, 1.0)

    size_value = length_min + (length_max - length_min) * size_percentile
    weight_value = weight_min + (weight_max - weight_min) * weight_percentile
    size_factor = 0.70 + 0.60 * size_percentile
    weight_factor = 0.70 + 0.70 * weight_percentile
    official_value = round(PIG_BASE_VALUES[resolved_rarity] * size_factor * weight_factor)
    return PigAttributes(
        size_value=round(size_value, 6),
        size_percentile=round(size_percentile, 6),
        weight_value=round(weight_value, 6),
        weight_percentile=round(weight_percentile, 6),
        fat_ratio=round(fat_ratio, 6),
        official_value=official_value,
    )


def level_progress(experience: int) -> LevelProgress:
    """把经验映射到无概率加成的数值等级与独立荣誉称号。"""

    normalized = int(experience)
    if normalized < 0:
        raise DomainValidationError("累计经验不能为负数。")
    title_index = 0
    for index, (_, threshold) in enumerate(LEVEL_THRESHOLDS):
        if normalized < threshold:
            break
        title_index = index
    title = LEVEL_THRESHOLDS[title_index][0]
    level = isqrt(normalized // LEVEL_EXPERIENCE_FACTOR) + 1
    threshold = LEVEL_EXPERIENCE_FACTOR * (level - 1) ** 2
    next_threshold = LEVEL_EXPERIENCE_FACTOR * level**2
    return LevelProgress(
        level=level,
        title=title,
        experience=normalized,
        current_threshold=threshold,
        next_threshold=next_threshold,
    )


def size_label(percentile: float) -> str:
    """Describe a size percentile without exposing implementation-oriented numbers."""

    value = float(percentile)
    if value < 0.12:
        return "迷你个体"
    if value < 0.32:
        return "小巧"
    if value < 0.72:
        return "标准体型"
    if value < 0.90:
        return "壮硕"
    return "超大个体"


def weight_label(percentile: float) -> str:
    """Describe a weight percentile in player-facing language."""

    value = float(percentile)
    if value < 0.15:
        return "轻盈"
    if value < 0.35:
        return "偏轻"
    if value < 0.72:
        return "匀称"
    if value < 0.90:
        return "厚实"
    return "重量级"
