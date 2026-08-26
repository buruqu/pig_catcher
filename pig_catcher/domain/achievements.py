"""Data-driven achievement definitions and immutable domain values.

The registry deliberately contains data only.  Runtime evaluation lives in the
achievement service, so adding a threshold, collection stamp, or receipt event
does not require another command handler or database migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class AchievementTier(StrEnum):
    NORMAL = "normal"
    FINE = "fine"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"
    ULTIMATE = "ultimate"


class AchievementConditionKind(StrEnum):
    THRESHOLD = "threshold"
    EVENT = "event"
    SET = "set"
    SNAPSHOT = "snapshot"


TIER_LABELS: Mapping[AchievementTier, str] = MappingProxyType(
    {
        AchievementTier.NORMAL: "普通",
        AchievementTier.FINE: "精良",
        AchievementTier.RARE: "稀有",
        AchievementTier.EPIC: "史诗",
        AchievementTier.LEGENDARY: "传说",
        AchievementTier.ULTIMATE: "终极",
    }
)

TIER_POINTS: Mapping[AchievementTier, int] = MappingProxyType(
    {
        AchievementTier.NORMAL: 5,
        AchievementTier.FINE: 10,
        AchievementTier.RARE: 20,
        AchievementTier.EPIC: 40,
        AchievementTier.LEGENDARY: 80,
        AchievementTier.ULTIMATE: 200,
    }
)


@dataclass(frozen=True, slots=True)
class AchievementReward:
    reward_type: str
    reward_id: str
    quantity: int = 1

    def __post_init__(self) -> None:
        if not self.reward_type.strip() or not self.reward_id.strip():
            raise ValueError("Achievement reward identifiers cannot be blank.")
        if self.quantity < 1:
            raise ValueError("Achievement reward quantity must be positive.")


@dataclass(frozen=True, slots=True)
class AchievementCondition:
    kind: AchievementConditionKind
    metric: str
    target: int = 1
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metric.strip():
            raise ValueError("Achievement metric cannot be blank.")
        if self.target < 1:
            raise ValueError("Achievement target must be positive.")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True, slots=True)
class AchievementDefinition:
    achievement_id: str
    name: str
    category: str
    tier: AchievementTier
    description: str
    hint: str
    condition: AchievementCondition
    rewards: tuple[AchievementReward, ...]
    hidden: bool = False
    definition_version: int = 1

    @property
    def points(self) -> int:
        return TIER_POINTS[self.tier]


@dataclass(frozen=True, slots=True)
class AchievementUnlock:
    achievement_id: str
    name: str
    tier: AchievementTier
    points: int
    rewards: tuple[AchievementReward, ...]
    unlocked_at: str


def _coin(amount: int) -> AchievementReward:
    return AchievementReward("coin", "pig-coin", amount)


def _ticket(ticket_id: str, quantity: int = 1) -> AchievementReward:
    return AchievementReward("ticket", ticket_id, quantity)


def _cosmetic(kind: str, cosmetic_id: str) -> AchievementReward:
    return AchievementReward(kind, cosmetic_id)


def _tier_for_index(index: int, count: int) -> AchievementTier:
    ratio = (index + 1) / count
    if ratio <= 0.30:
        return AchievementTier.NORMAL
    if ratio <= 0.60:
        return AchievementTier.FINE
    if ratio <= 0.86:
        return AchievementTier.RARE
    return AchievementTier.EPIC


def _standard_rewards(tier: AchievementTier) -> tuple[AchievementReward, ...]:
    coins = {
        AchievementTier.NORMAL: 200,
        AchievementTier.FINE: 600,
        AchievementTier.RARE: 1500,
        AchievementTier.EPIC: 3500,
        AchievementTier.LEGENDARY: 8000,
        AchievementTier.ULTIMATE: 50000,
    }[tier]
    return (_coin(coins),)


def _threshold_series(
    *,
    id_prefix: str,
    category: str,
    metric: str,
    targets: tuple[int, ...],
    names: tuple[str, ...],
    unit: str,
) -> list[AchievementDefinition]:
    definitions: list[AchievementDefinition] = []
    for index, (target, name) in enumerate(zip(targets, names, strict=True)):
        tier = _tier_for_index(index, len(targets))
        definitions.append(
            AchievementDefinition(
                achievement_id=f"{id_prefix}-{target}",
                name=name,
                category=category,
                tier=tier,
                description=f"累计达到 {target:,} {unit}。",
                hint=f"当前进度会随{category}自动更新。",
                condition=AchievementCondition(AchievementConditionKind.THRESHOLD, metric, target),
                rewards=_standard_rewards(tier),
            )
        )
    return definitions


def _regular_definitions() -> list[AchievementDefinition]:
    result: list[AchievementDefinition] = []
    result += _threshold_series(
        id_prefix="catch-total",
        category="捕猎历程",
        metric="total_catches",
        targets=(1, 10, 50, 200, 500, 1000, 2000),
        names=("第一次伸手", "十拿九稳", "猪圈常客", "百猪过境", "五百回响", "千猪猎手", "两千次相遇"),
        unit="次抓猪",
    )
    result += _threshold_series(
        id_prefix="catch-five-star",
        category="高星猎手",
        metric="five_star_pigs",
        targets=(1, 20, 100),
        names=("第一颗五星", "五星列阵", "百星猎人"),
        unit="只五星猪",
    )
    result += _threshold_series(
        id_prefix="catch-six-star",
        category="高星猎手",
        metric="six_star_pigs",
        targets=(1, 10, 50, 100),
        names=("虹光初见", "十道虹光", "六星巡礼", "百虹加冕"),
        unit="只六星猪",
    )
    result += _threshold_series(
        id_prefix="pig-catalog",
        category="图鉴收藏",
        metric="pig_catalog_count",
        targets=(10, 30, 60, 100, 130),
        names=("十页猪谱", "图鉴启程", "六十种相逢", "百猪绘卷", "收藏家的长卷"),
        unit="种猪猪图鉴",
    )
    result += _threshold_series(
        id_prefix="food-catalog",
        category="图鉴收藏",
        metric="food_catalog_count",
        targets=(5, 15, 30),
        names=("五味初开", "十五席", "三十道盛宴"),
        unit="种美食图鉴",
    )
    result += _threshold_series(
        id_prefix="cook-total",
        category="料理品鉴",
        metric="total_cooks",
        targets=(1, 10, 50, 200, 500),
        names=("第一次开火", "十席小厨", "锅铲熟手", "两百次开饭", "五百席主厨"),
        unit="次做菜",
    )
    result += _threshold_series(
        id_prefix="cook-five-star",
        category="料理品鉴",
        metric="five_star_foods",
        targets=(20, 100),
        names=("五星宴席", "百道五星"),
        unit="道五星菜",
    )
    result += _threshold_series(
        id_prefix="cook-six-star",
        category="料理品鉴",
        metric="six_star_foods",
        targets=(1, 10),
        names=("六星开宴", "十席传说"),
        unit="道六星菜",
    )
    result += _threshold_series(
        id_prefix="eat-total",
        category="料理品鉴",
        metric="foods_eaten",
        targets=(100,),
        names=("百味品鉴家",),
        unit="道美食",
    )

    event_rows = (
        ("giant-size-board", "体型榜初见", "size_board_entries", "首次进入今日体型榜。"),
        ("giant-weight-board", "重量榜初见", "weight_board_entries", "首次进入今日重量榜。"),
        ("giant-dual-board", "双榜同辉", "dual_board_entries", "同一只猪同时进入体型榜和重量榜。"),
        ("giant-ten-sightings", "十次巨物目击", "giant_sightings", "累计完成 10 次巨物目击。"),
        ("giant-size-record", "群体型纪录刷新者", "size_record_breaks", "刷新一次群体型纪录。"),
        ("giant-weight-record", "群重量纪录刷新者", "weight_record_breaks", "刷新一次群重量纪录。"),
    )
    for index, (aid, name, metric, description) in enumerate(event_rows):
        target = 10 if metric == "giant_sightings" else 1
        tier = AchievementTier.RARE if index >= 2 else AchievementTier.FINE
        result.append(
            AchievementDefinition(
                aid,
                name,
                "巨物纪录",
                tier,
                description,
                "去寻找更极端的个体。",
                AchievementCondition(AchievementConditionKind.THRESHOLD, metric, target),
                _standard_rewards(tier),
            )
        )

    result += _threshold_series(
        id_prefix="level",
        category="成长经营",
        metric="player_level",
        targets=(10, 21, 31, 41, 51, 61),
        names=("猪圈学徒", "抓猪大神", "资深猎手", "群山巡猎", "传说饲养员", "六十一阶之证"),
        unit="级",
    )
    result.append(
        AchievementDefinition(
            "earned-coins-100000",
            "十万猪币的旅程",
            "成长经营",
            AchievementTier.EPIC,
            "通过正常玩法累计获得 100,000 猪币。",
            "管理员调整不计入进度。",
            AchievementCondition(AchievementConditionKind.THRESHOLD, "ordinary_coins_earned", 100000),
            (_coin(5000), _ticket("achievement-catch", 2)),
        )
    )

    result.extend(
        (
            AchievementDefinition(
                "social-showcase",
                "把它放在聚光灯下",
                "社交展示",
                AchievementTier.NORMAL,
                "设置一次展示猪猪或美食。",
                "使用现有展示指令即可推进。",
                AchievementCondition(AchievementConditionKind.EVENT, "showcase_set"),
                _standard_rewards(AchievementTier.NORMAL),
            ),
            AchievementDefinition(
                "social-favorites-10",
                "十件心头好",
                "社交展示",
                AchievementTier.FINE,
                "同时收藏 10 件猪猪或美食。",
                "收藏资产不会被批量操作选中。",
                AchievementCondition(AchievementConditionKind.THRESHOLD, "favorite_assets", 10),
                _standard_rewards(AchievementTier.FINE),
            ),
            AchievementDefinition(
                "social-gift-partners-3",
                "礼物走过三双手",
                "社交展示",
                AchievementTier.FINE,
                "与 3 名不同玩家完成合法赠送。",
                "重复与同一玩家互动只计一次。",
                AchievementCondition(AchievementConditionKind.SET, "gift_partners", 3),
                (_cosmetic("badge", "three-gift-partners"),),
            ),
            AchievementDefinition(
                "social-trade-partners-3",
                "三方成交簿",
                "社交展示",
                AchievementTier.FINE,
                "与 3 名不同玩家完成合法交易。",
                "被监管拦截或管理操作不计。",
                AchievementCondition(AchievementConditionKind.SET, "trade_partners", 3),
                (_cosmetic("badge", "three-trade-partners"),),
            ),
        )
    )
    return result


_COLLECTIONS = (
    ("poppin-party", "Poppin'Party"),
    ("afterglow", "Afterglow"),
    ("mygo", "MyGO!!!!!"),
    ("morfonica", "Morfonica"),
    ("mugendai", "梦限大动画"),
    ("pastel-palettes", "Pastel＊Palettes"),
    ("hello-happy-world", "Hello, Happy World!"),
    ("roselia", "Roselia"),
    ("raise-a-suilen", "RAISE A SUILEN"),
    ("ave-mujica", "Ave Mujica"),
    ("jujutsu-kaisen", "咒术回战"),
)


def _stamp_definitions() -> list[AchievementDefinition]:
    return [
        AchievementDefinition(
            f"stamp-{key}",
            f"{name}·完整印章",
            "联动印章",
            AchievementTier.RARE,
            f"集齐发布快照中的全部 {name} 联动猪猪。",
            "印章使用固定成员快照，未来新增不会撤销。",
            AchievementCondition(AchievementConditionKind.SNAPSHOT, f"collection:{key}"),
            (_coin(1500), _cosmetic("badge", f"stamp-{key}")),
        )
        for key, name in _COLLECTIONS
    ]


def _hidden_definitions() -> list[AchievementDefinition]:
    rows = (
        (
            "hidden-kfc-thursday-catch",
            "疯狂星期四的邀约",
            "kfc_thursday_catch",
            AchievementTier.RARE,
            (_cosmetic("badge", "kfc-thursday"),),
        ),
        ("hidden-kfc-bucket-cook", "整桶出炉", "kfc_bucket_cook", AchievementTier.RARE, (_ticket("food-inspiration"),)),
        (
            "hidden-kfc-group-settlement",
            "全群为你买单",
            "kfc_group_settlement",
            AchievementTier.RARE,
            (_ticket("achievement-firework"),),
        ),
        (
            "hidden-roulette-all-faces",
            "六面俱到",
            "roulette_faces",
            AchievementTier.EPIC,
            (_cosmetic("title", "roulette-omniscient"),),
        ),
        ("hidden-domain-activate", "请君入厨", "domain_activated", AchievementTier.RARE, (_ticket("recook"),)),
        (
            "hidden-blue-red-pair",
            "顺逆之间",
            "blue_red_pair",
            AchievementTier.EPIC,
            (_cosmetic("title", "technique-observer"),),
        ),
        (
            "hidden-hollow-purple",
            "虚式尽头",
            "hollow_purple",
            AchievementTier.LEGENDARY,
            (_cosmetic("frame", "hollow-purple"), _ticket("achievement-firework")),
        ),
        (
            "hidden-sugar-1004-burst",
            "甜到 10.04 倍",
            "sugar_1004_burst",
            AchievementTier.EPIC,
            (_cosmetic("badge", "sugar-1004"),),
        ),
        (
            "hidden-dragon-group-settlement",
            "七星开宴",
            "dragon_group_settlement",
            AchievementTier.EPIC,
            (_cosmetic("badge", "dragon-feast"),),
        ),
        (
            "hidden-assam-auto-gift",
            "雾里赠你",
            "assam_auto_gift",
            AchievementTier.RARE,
            (_cosmetic("badge", "assam-mist"),),
        ),
        (
            "hidden-pearl-tea-double-copy",
            "一杯生三猪",
            "pearl_tea_double_copy",
            AchievementTier.EPIC,
            (_cosmetic("badge", "double-copy"),),
        ),
        (
            "hidden-aya-repair-return",
            "还能再修",
            "aya_repair_return",
            AchievementTier.RARE,
            (_cosmetic("badge", "aya-repair"),),
        ),
        (
            "hidden-domain-six-star-cook",
            "御厨子摘星",
            "domain_six_star_cook",
            AchievementTier.EPIC,
            (_coin(3000), _ticket("recook")),
        ),
        (
            "hidden-domain-gojo-cook",
            "最强之间，雨还在下",
            "domain_gojo_cook",
            AchievementTier.LEGENDARY,
            (_cosmetic("title", "rain-love"), _cosmetic("badge", "domain-gojo")),
        ),
        (
            "hidden-xiaoma-zero-six",
            "马失前蹄",
            "xiaoma_zero_six",
            AchievementTier.RARE,
            (_ticket("achievement-catch", 2),),
        ),
        (
            "hidden-xiaoma-five-six",
            "五骑虹光",
            "xiaoma_five_six",
            AchievementTier.LEGENDARY,
            (_cosmetic("frame", "five-rainbows"), _ticket("achievement-firework")),
        ),
        (
            "hidden-fifty-catches-no-six",
            "第五十夜仍无星",
            "fifty_no_six",
            AchievementTier.RARE,
            (_ticket("achievement-catch", 3),),
        ),
        (
            "hidden-sushi-platter-hundred",
            "百席寿司总大将",
            "sushi_platter_instances",
            AchievementTier.EPIC,
            (_cosmetic("title", "sushi-commander"),),
        ),
        (
            "hidden-natural-six-star-cook",
            "素手摘六星",
            "natural_six_star_cook",
            AchievementTier.LEGENDARY,
            (_cosmetic("title", "pure-chef"), _ticket("food-inspiration", 2)),
        ),
        (
            "hidden-super-millionaire-947947",
            "超级大富翁",
            "millionaire_947947",
            AchievementTier.LEGENDARY,
            (_cosmetic("title", "947947"), _cosmetic("frame", "gold-pig-coin")),
        ),
    )
    definitions: list[AchievementDefinition] = []
    for aid, name, metric, tier, special_rewards in rows:
        kind = (
            AchievementConditionKind.SET
            if metric in {"roulette_faces", "sushi_platter_instances"}
            else AchievementConditionKind.EVENT
        )
        target = 6 if metric == "roulette_faces" else 100 if metric == "sushi_platter_instances" else 1
        definitions.append(
            AchievementDefinition(
                aid,
                name,
                "隐藏彩蛋",
                tier,
                f"完成隐藏条件：{name}。",
                "线索隐藏，继续体验现有玩法。",
                AchievementCondition(kind, metric, target),
                special_rewards,
                hidden=True,
            )
        )
    return definitions


def _ultimate_definitions() -> list[AchievementDefinition]:
    return [
        AchievementDefinition(
            "ultimate-all-giants",
            "万猪之巅",
            "终极收藏",
            AchievementTier.ULTIMATE,
            "同时持有当前授权模板快照中每一种猪的双顶壮硕个体。",
            "体型百分位至少 0.92，体重百分位至少 0.88；管理员发放不计。",
            AchievementCondition(AchievementConditionKind.SNAPSHOT, "all_template_giants"),
            (
                _coin(50000),
                _cosmetic("frame", "all-giants-dynamic"),
                _cosmetic("title", "all-giants"),
                AchievementReward("chest", "achievement-choice"),
            ),
        ),
        AchievementDefinition(
            "ultimate-all-minis",
            "掌上万猪",
            "终极收藏",
            AchievementTier.ULTIMATE,
            "同时持有当前授权模板快照中每一种猪的双顶迷你个体。",
            "体型百分位至多 0.08，体重百分位至多 0.15；管理员发放不计。",
            AchievementCondition(AchievementConditionKind.SNAPSHOT, "all_template_minis"),
            (
                _coin(50000),
                _cosmetic("frame", "all-minis-dynamic"),
                _cosmetic("title", "all-minis"),
                AchievementReward("chest", "achievement-choice"),
            ),
        ),
    ]


ACHIEVEMENT_DEFINITIONS: tuple[AchievementDefinition, ...] = tuple(
    _regular_definitions() + _stamp_definitions() + _hidden_definitions() + _ultimate_definitions()
)
ACHIEVEMENT_BY_ID: Mapping[str, AchievementDefinition] = MappingProxyType(
    {definition.achievement_id: definition for definition in ACHIEVEMENT_DEFINITIONS}
)

if len(ACHIEVEMENT_BY_ID) != len(ACHIEVEMENT_DEFINITIONS):
    raise RuntimeError("Achievement identifiers must remain globally unique.")


__all__ = [
    "ACHIEVEMENT_BY_ID",
    "ACHIEVEMENT_DEFINITIONS",
    "AchievementCondition",
    "AchievementConditionKind",
    "AchievementDefinition",
    "AchievementReward",
    "AchievementTier",
    "AchievementUnlock",
    "TIER_LABELS",
    "TIER_POINTS",
]
