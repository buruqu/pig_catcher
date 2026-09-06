"""管理员可发放资源目录：复用各系统正式定义，不创建影子库存或虚构商品。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .activity_achievements import ACTIVITY_REWARDS
from .dispatch import MATERIALS
from .errors import DomainValidationError
from .feature_shop import FEATURE_SHOP_PRODUCTS
from .gameplay import ITEM_DEFINITIONS
from .item_bag import REWARD_NAMES

MAX_GRANT_QUANTITY = 10_000
MAX_GRANT_ASSETS = 1_000


@dataclass(frozen=True, slots=True)
class GrantResource:
    resource_id: str
    name: str
    storage: str
    category: str
    usage: str
    reward_type: str = ""


def _resources() -> tuple[GrantResource, ...]:
    resources = [
        GrantResource(item.item_id, item.display_name, "item", "商城道具", "/道具背包 → /使用道具")
        for item in ITEM_DEFINITIONS
    ]
    resources.extend(
        GrantResource(product.product_id, product.display_name, "feature", f"{product.category}器具", "/道具背包")
        for product in FEATURE_SHOP_PRODUCTS
    )
    for reward_id, name in REWARD_NAMES.items():
        definition = ACTIVITY_REWARDS.get(reward_id)
        reward_type = (
            definition["kind"]
            if definition
            else ("chest" if reward_id in {"achievement-choice", "regular-five-star-memorial"} else "ticket")
        )
        if reward_type not in {"ticket", "chest"}:
            continue
        resources.append(GrantResource(reward_id, name, "reward", "券与礼盒", "/道具背包", reward_type))
    resources.extend(
        GrantResource(material_id, name, "material", "材料", "/派遣背包") for material_id, name in MATERIALS.items()
    )
    resources.extend(
        (
            GrantResource("feed", "猪饲料", "upgrade", "永久升级", "/猪猪商城；每份加1级，上限10级"),
            GrantResource("cookware", "厨具", "upgrade", "永久升级", "/猪猪商城；每份加1级，上限10级"),
        )
    )
    return tuple(resources)


GRANT_RESOURCES = _resources()
GRANT_RESOURCES_BY_ID = MappingProxyType({item.resource_id: item for item in GRANT_RESOURCES})
if len(GRANT_RESOURCES_BY_ID) != len(GRANT_RESOURCES):
    raise ValueError("管理员资源目录存在重复ID。")


def normalize_grant_name(value: str) -> str:
    return "".join(str(value).split()).casefold().replace("卷", "券").replace("5星", "五星")


def grant_resource_candidates(selector: str, kind: str = "") -> tuple[GrantResource, ...]:
    normalized = normalize_grant_name(selector)
    kinds = {
        "": {"item", "feature", "reward", "material", "upgrade"},
        "道具": {"item", "feature", "reward", "material", "upgrade"},
        "商城道具": {"item", "feature", "upgrade"},
        "券": {"reward"},
        "材料": {"material"},
        "升级": {"upgrade"},
        "猪猪": set(),
        "美食": set(),
    }
    if kind not in kinds:
        raise DomainValidationError("发放类别只能是：猪猪、美食、道具、券、商城道具、材料、升级。")
    return tuple(
        item
        for item in GRANT_RESOURCES
        if item.storage in kinds[kind]
        and normalized in {normalize_grant_name(item.name), normalize_grant_name(item.resource_id)}
    )


def validate_grant_quantity(quantity: int) -> int:
    if type(quantity) is not int or not 1 <= quantity <= MAX_GRANT_QUANTITY:
        raise DomainValidationError(f"每人发放数量必须是1至{MAX_GRANT_QUANTITY}的整数。")
    return quantity
