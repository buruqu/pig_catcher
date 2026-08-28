"""高星美食的声明式补给套餐，不向抓猪、做菜或三系统事实队列塞入伪事件。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .activity_achievements import ACTIVITY_REWARDS
from .dispatch import MATERIALS
from .errors import FoodEffectError

FOOD_SUPPLY_PACK = "food-supply-pack"
FOOD_SUPPLY_VERSION = 1


@dataclass(frozen=True, slots=True)
class FoodSupplyReward:
    kind: str
    reward_id: str
    quantity: int

    def __post_init__(self) -> None:
        if type(self.quantity) is not int or not 1 <= self.quantity <= 10_000:
            raise FoodEffectError("美食补给数量必须是1至10000的整数。")
        if self.kind == "material":
            if self.reward_id not in MATERIALS:
                raise FoodEffectError("美食补给引用了未登记的材料。")
        elif self.kind not in {"ticket", "chest"} or (
            self.reward_id not in ACTIVITY_REWARDS or ACTIVITY_REWARDS[self.reward_id]["kind"] != self.kind
        ):
            raise FoodEffectError("美食补给引用了未登记的券或自选份。")

    @property
    def name(self) -> str:
        return MATERIALS[self.reward_id] if self.kind == "material" else ACTIVITY_REWARDS[self.reward_id]["name"]

    @property
    def use_hint(self) -> str:
        if self.kind == "material":
            return "已存入材料背包；/派遣背包 查看。可用于器具、巡演或战斗养成，不计派遣自然产出。"
        if self.kind == "chest":
            choices = "、".join(MATERIALS[key] for key in ACTIVITY_REWARDS[self.reward_id]["choices"])
            return (
                f"/成就奖励 材料 {self.name} 训练矿石 数量，再 /成就奖励 确认；"
                f"每份兑换1份自选材料，可选{choices}。"
            )
        return f"/使用成就券 {self.name}。{ACTIVITY_REWARDS[self.reward_id]['effect']}"


@dataclass(frozen=True, slots=True)
class FoodSupplyPack:
    pack_id: str
    food_name: str
    food_rarity: int
    title: str
    rewards: tuple[FoodSupplyReward, ...]

    def __post_init__(self) -> None:
        if self.food_rarity not in {4, 5} or not self.rewards:
            raise FoodEffectError("美食补给套餐必须属于四星或五星菜，且至少包含一项奖励。")
        keys = {(reward.kind, reward.reward_id) for reward in self.rewards}
        if len(keys) != len(self.rewards):
            raise FoodEffectError("同一美食补给套餐不能重复声明相同奖励。")

    @property
    def summary(self) -> str:
        items = "、".join(f"{reward.name}×{reward.quantity}" for reward in self.rewards)
        return f"立即获得{items}。补给可累计，券不自动使用，不改变抓猪或做菜概率。"


# 满编派遣4小时基础产出6主材+2补给；近郊合计8补给。四星约2至4块，五星约8至11块主材。
# 保留大额后续强化与自然派遣成就门槛；不发稀有手记，也不自动消耗奖励券。
_PACKS = (
    FoodSupplyPack(
        "sausage-pig",
        "香肠猪",
        4,
        "远行便当",
        (FoodSupplyReward("material", "travel-supplies", 18), FoodSupplyReward("ticket", "dispatch-bill", 1)),
    ),
    FoodSupplyPack(
        "pig-fries",
        "猪条",
        4,
        "工坊工具餐",
        (FoodSupplyReward("material", "training-ore", 12), FoodSupplyReward("material", "machine-parts", 8)),
    ),
    FoodSupplyPack(
        "pig-cola",
        "猪可乐",
        4,
        "舞台补给站",
        (FoodSupplyReward("material", "stage-components", 12), FoodSupplyReward("ticket", "tour-steady-stage", 1)),
    ),
    FoodSupplyPack(
        "pig-chocolate",
        "猪克力",
        5,
        "强化能量餐",
        (
            FoodSupplyReward("material", "training-ore", 40),
            FoodSupplyReward("material", "agility-fiber", 24),
            FoodSupplyReward("ticket", "training-rebate", 2),
        ),
    ),
    FoodSupplyPack(
        "pig-burger-meal",
        "猪堡套餐",
        5,
        "三线豪华套餐",
        (
            FoodSupplyReward("chest", "materials-choice", 48),
            FoodSupplyReward("ticket", "dispatch-luggage", 1),
            FoodSupplyReward("ticket", "tour-date", 1),
            FoodSupplyReward("ticket", "training-rebate", 1),
        ),
    ),
)
FOOD_SUPPLY_PACKS = MappingProxyType({pack.pack_id: pack for pack in _PACKS})
if len(FOOD_SUPPLY_PACKS) != len(_PACKS):
    raise ValueError("美食补给套餐编号重复。")


def resolve_supply_pack(pack_id: str) -> FoodSupplyPack:
    if not isinstance(pack_id, str) or pack_id not in FOOD_SUPPLY_PACKS:
        raise FoodEffectError("美食补给套餐未注册，当前不会消耗这份美食。")
    return FOOD_SUPPLY_PACKS[pack_id]


def resolve_food_supply_pack(params: Mapping[str, object]) -> FoodSupplyPack:
    """补给金额由版本化套餐定义，不接受素材参数传入任意奖励或数量。"""

    if not isinstance(params, Mapping) or set(params) != {"pack_id"}:
        raise FoodEffectError("美食补给参数必须且只能包含 pack_id。")
    value = params["pack_id"]
    if not isinstance(value, str):
        raise FoodEffectError("美食补给 pack_id 必须为文本。")
    return resolve_supply_pack(value)
