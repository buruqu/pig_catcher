"""派遣、巡演与对战专属商城的版本化商品目录。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from .battle_catalog import TOOLS as BATTLE_TOOLS
from .dispatch import TOOLS as DISPATCH_TOOLS
from .errors import StoreProductError
from .tour_catalog import TOOLS as TOUR_TOOLS


class FeatureShopSystem(StrEnum):
    """独立功能商城；不会混入主商城的“全部”分类。"""

    DISPATCH = "dispatch"
    TOUR = "tour"
    BATTLE = "battle"


FEATURE_SHOP_SYSTEM_LABELS: Final[Mapping[FeatureShopSystem, str]] = MappingProxyType(
    {
        FeatureShopSystem.DISPATCH: "派遣",
        FeatureShopSystem.TOUR: "巡演",
        FeatureShopSystem.BATTLE: "对战",
    }
)

FEATURE_SHOP_TARGET_INVENTORIES: Final[Mapping[FeatureShopSystem, str]] = MappingProxyType(
    {
        FeatureShopSystem.DISPATCH: "dispatch_tools",
        FeatureShopSystem.TOUR: "tour_tools",
        FeatureShopSystem.BATTLE: "battle_tools",
    }
)


@dataclass(frozen=True, slots=True)
class FeatureShopProduct:
    """一个可用猪币购买、直接进入既有功能器具库存的商品。"""

    product_id: str
    system: FeatureShopSystem
    tool_id: str
    display_name: str
    unit_price: int
    effect_summary: str
    target_inventory: str

    @property
    def category(self) -> str:
        return FEATURE_SHOP_SYSTEM_LABELS[self.system]


_PRICE_TABLE: Final[Mapping[FeatureShopSystem, Mapping[str, int]]] = MappingProxyType(
    {
        FeatureShopSystem.DISPATCH: MappingProxyType(
            {
                "region-map": 520,
                "souvenir-camera": 720,
                "encounter-compass": 1480,
                "sorting-box": 420,
            }
        ),
        FeatureShopSystem.TOUR: MappingProxyType(
            {
                "cable": 520,
                "cue": 760,
                "recorder": 880,
                "confetti": 220,
            }
        ),
        FeatureShopSystem.BATTLE: MappingProxyType(
            {
                "wristband": 880,
                "bandage": 820,
                "confetti": 220,
            }
        ),
    }
)


def _build_products() -> tuple[FeatureShopProduct, ...]:
    definitions = {
        FeatureShopSystem.DISPATCH: DISPATCH_TOOLS,
        FeatureShopSystem.TOUR: TOUR_TOOLS,
        FeatureShopSystem.BATTLE: BATTLE_TOOLS,
    }
    products: list[FeatureShopProduct] = []
    for system, tools in definitions.items():
        prices = _PRICE_TABLE[system]
        tool_ids = {tool.tool_id for tool in tools}
        if tool_ids != set(prices):
            raise ValueError(f"{FEATURE_SHOP_SYSTEM_LABELS[system]}商城价格目录与器具定义不一致")
        for tool in tools:
            summary = tool.description if system is FeatureShopSystem.BATTLE else tool.summary
            products.append(
                FeatureShopProduct(
                    product_id=f"feature-{system.value}-{tool.tool_id}",
                    system=system,
                    tool_id=tool.tool_id,
                    display_name=tool.name,
                    unit_price=prices[tool.tool_id],
                    effect_summary=summary,
                    target_inventory=FEATURE_SHOP_TARGET_INVENTORIES[system],
                )
            )
    return tuple(products)


FEATURE_SHOP_PRODUCTS: Final[tuple[FeatureShopProduct, ...]] = _build_products()

if len({product.product_id for product in FEATURE_SHOP_PRODUCTS}) != len(FEATURE_SHOP_PRODUCTS):
    raise ValueError("功能商城商品 ID 重复")
if len({product.display_name for product in FEATURE_SHOP_PRODUCTS}) != len(FEATURE_SHOP_PRODUCTS):
    raise ValueError("功能商城商品名称重复，/购买 无法安全定位")
if len({(product.system, product.tool_id) for product in FEATURE_SHOP_PRODUCTS}) != len(FEATURE_SHOP_PRODUCTS):
    raise ValueError("功能商城系统器具键重复")
if any(product.unit_price <= 0 for product in FEATURE_SHOP_PRODUCTS):
    raise ValueError("功能商城价格必须为正整数")

FEATURE_SHOP_PRODUCTS_BY_ID: Final[Mapping[str, FeatureShopProduct]] = MappingProxyType(
    {product.product_id: product for product in FEATURE_SHOP_PRODUCTS}
)
FEATURE_SHOP_PRODUCTS_BY_NAME: Final[Mapping[str, FeatureShopProduct]] = MappingProxyType(
    {product.display_name: product for product in FEATURE_SHOP_PRODUCTS}
)
FEATURE_SHOP_PRODUCTS_BY_SYSTEM: Final[Mapping[FeatureShopSystem, tuple[FeatureShopProduct, ...]]] = (
    MappingProxyType(
        {
            system: tuple(product for product in FEATURE_SHOP_PRODUCTS if product.system is system)
            for system in FeatureShopSystem
        }
    )
)


def feature_shop_system(value: FeatureShopSystem | str) -> FeatureShopSystem:
    """解析中文商城名或稳定英文系统 ID。"""

    if isinstance(value, FeatureShopSystem):
        return value
    normalized = str(value or "").strip().casefold()
    for system, label in FEATURE_SHOP_SYSTEM_LABELS.items():
        if normalized in {system.value, label.casefold()}:
            return system
    raise StoreProductError("功能商城只能选择：派遣、巡演、对战。")


def build_feature_shop_products(system: FeatureShopSystem | str) -> tuple[FeatureShopProduct, ...]:
    """返回一个独立功能商城的稳定商品顺序。"""

    return FEATURE_SHOP_PRODUCTS_BY_SYSTEM[feature_shop_system(system)]


def feature_shop_product_by_id(product_id: str) -> FeatureShopProduct:
    normalized = str(product_id or "").strip()
    try:
        return FEATURE_SHOP_PRODUCTS_BY_ID[normalized]
    except KeyError as exc:
        raise StoreProductError(f"功能商城中没有商品 ID“{normalized}”。") from exc


def feature_shop_product_by_name(display_name: str) -> FeatureShopProduct:
    normalized = str(display_name or "").strip()
    try:
        return FEATURE_SHOP_PRODUCTS_BY_NAME[normalized]
    except KeyError as exc:
        choices = "、".join(product.display_name for product in FEATURE_SHOP_PRODUCTS)
        raise StoreProductError(f"功能商城中没有“{normalized}”。可购买器具：{choices}") from exc
