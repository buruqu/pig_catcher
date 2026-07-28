"""Explicit command parsers for inventory, store, and ledger queries."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import AssetKind, TradeStatus
from ..domain.errors import DomainValidationError
from ..domain.social import (
    normalize_ranking_type,
    normalize_trade_id,
    trade_status_from_label,
)

INVENTORY_SORTS = frozenset({"获得时间", "品质", "价值", "体型", "重量", "名称"})
FOOD_INVENTORY_SORTS = frozenset({"获得时间", "品质", "价值", "份量", "名称"})
STORE_CATEGORIES = frozenset({"全部", "抓猪", "做菜", "升级"})


@dataclass(frozen=True, slots=True)
class InventoryQuery:
    """猪猪背包页码、品质与排序。"""

    page: int = 1
    rarity: int | None = None
    sort: str = "获得时间"


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """猪猪图鉴品质与未发现筛选。"""

    rarity: int | None = None
    undiscovered_only: bool = False


@dataclass(frozen=True, slots=True)
class StoreQuery:
    """Store page and product category."""

    page: int = 1
    category: str = "全部"


@dataclass(frozen=True, slots=True)
class PurchaseQuery:
    """Store product name and positive quantity."""

    product_name: str
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class GiftQuery:
    """One exact asset selector after removing the structured @ marker."""

    selector: str


@dataclass(frozen=True, slots=True)
class TradeOfferQuery:
    """One exact asset selector and a positive agreed price."""

    selector: str
    price: int


@dataclass(frozen=True, slots=True)
class TradeListQuery:
    """Status-filtered personal trade list."""

    page: int = 1
    status: TradeStatus | None = None


@dataclass(frozen=True, slots=True)
class ShowcaseQuery:
    """Set or clear one pig or food showcase slot."""

    asset_kind: AssetKind
    selector: str
    clear: bool


@dataclass(frozen=True, slots=True)
class RankingQuery:
    """Current-group ranking type and page."""

    ranking_type: str = "综合"
    page: int = 1


def _positive_page(value: str) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("页码必须是正整数。") from exc
    if page < 1 or page > 100000:
        raise DomainValidationError("页码必须位于 1 至 100000。")
    return page


def _rarity(value: str) -> int:
    try:
        rarity = int(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("品质必须填写 1 至 6。") from exc
    if not 1 <= rarity <= 6:
        raise DomainValidationError("品质必须填写 1 至 6。")
    return rarity


def _parse_inventory_query(
    arguments: str,
    *,
    sorts: frozenset[str],
    asset_label: str,
) -> InventoryQuery:

    page = 1
    rarity: int | None = None
    sort = "获得时间"
    page_seen = False
    rarity_seen = False
    sort_seen = False
    for token in str(arguments or "").split():
        if token.startswith("品质="):
            if rarity_seen:
                raise DomainValidationError("品质筛选不能重复填写。")
            rarity = _rarity(token.partition("=")[2])
            rarity_seen = True
            continue
        if token.startswith("排序="):
            if sort_seen:
                raise DomainValidationError("排序方式不能重复填写。")
            candidate = token.partition("=")[2].strip()
            if candidate not in sorts:
                choices = "、".join(sorted(sorts))
                raise DomainValidationError(f"不支持排序“{candidate}”。可用：{choices}")
            sort = candidate
            sort_seen = True
            continue
        if page_seen:
            raise DomainValidationError(f"无法识别{asset_label}背包参数“{token}”。")
        page = _positive_page(token)
        page_seen = True
    return InventoryQuery(page=page, rarity=rarity, sort=sort)


def parse_inventory_query(arguments: str) -> InventoryQuery:
    """解析 `/猪猪背包 [页码] [品质=数字] [排序=方式]`。"""

    return _parse_inventory_query(
        arguments,
        sorts=INVENTORY_SORTS,
        asset_label="猪猪",
    )


def parse_food_inventory_query(arguments: str) -> InventoryQuery:
    """解析 `/美食背包 [页码] [品质=数字] [排序=方式]`。"""

    return _parse_inventory_query(
        arguments,
        sorts=FOOD_INVENTORY_SORTS,
        asset_label="美食",
    )


def parse_catalog_query(arguments: str) -> CatalogQuery:
    """解析 `/猪猪图鉴 [品质=数字|未收集]`。"""

    rarity: int | None = None
    undiscovered_only = False
    filter_seen = False
    for token in str(arguments or "").split():
        if token.startswith("品质="):
            if filter_seen:
                raise DomainValidationError("图鉴筛选不能重复填写。")
            value = token.partition("=")[2].strip()
            if value == "未收集":
                undiscovered_only = True
            else:
                rarity = _rarity(value)
            filter_seen = True
            continue
        if token.isdecimal():
            raise DomainValidationError("图鉴已改为按品质一次展示全部内容，不需要填写页码。")
        raise DomainValidationError(f"无法识别图鉴参数“{token}”。")
    return CatalogQuery(
        rarity=rarity,
        undiscovered_only=undiscovered_only,
    )


def parse_records_page(arguments: str) -> int:
    """解析 `/猪猪纪录 [页码]`。"""

    normalized = str(arguments or "").strip()
    return _positive_page(normalized) if normalized else 1


def parse_store_query(arguments: str) -> StoreQuery:
    """解析 `/猪猪商城 [页码] [分类=全部|抓猪|做菜|升级]`。"""

    page = 1
    category = "全部"
    page_seen = False
    category_seen = False
    for token in str(arguments or "").split():
        if token.startswith("分类="):
            candidate = token.partition("=")[2].strip()
        elif token in STORE_CATEGORIES:
            candidate = token
        else:
            candidate = ""
        if candidate:
            if category_seen:
                raise DomainValidationError("商城分类不能重复填写。")
            if candidate not in STORE_CATEGORIES:
                raise DomainValidationError("商城分类只能是：全部、抓猪、做菜、升级。")
            category = candidate
            category_seen = True
            continue
        if page_seen:
            raise DomainValidationError(f"无法识别商城参数“{token}”。")
        page = _positive_page(token)
        page_seen = True
    return StoreQuery(page=page, category=category)


def parse_purchase_query(arguments: str) -> PurchaseQuery:
    """解析 `/购买 <商品名称> [数量]`。"""

    tokens = str(arguments or "").split()
    if not tokens:
        raise DomainValidationError("请填写商品名称，例如：/购买 幸运猪哨 2")
    quantity = 1
    product_tokens = tokens
    if len(tokens) > 1:
        try:
            quantity = int(tokens[-1])
        except ValueError:
            quantity = 1
        else:
            product_tokens = tokens[:-1]
    product_name = " ".join(product_tokens).strip()
    if not product_name:
        raise DomainValidationError("商品名称不能为空。")
    if quantity < 1:
        raise DomainValidationError("购买数量必须是正整数。")
    return PurchaseQuery(product_name=product_name, quantity=quantity)


def parse_ledger_page(arguments: str) -> int:
    """解析 `/猪币账本 [页码]`。"""

    normalized = str(arguments or "").strip()
    return _positive_page(normalized) if normalized else 1


def parse_action_type(value: str) -> str:
    """把用户动作名映射为持久化动作类型。"""

    normalized = str(value or "").strip()
    mapping = {"抓猪": "catching", "做菜": "cooking"}
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise DomainValidationError("动作只能填写“抓猪”或“做菜”。") from exc


def _remove_mention_marker(
    arguments: str,
    *,
    display_name: str,
    user_id: str,
) -> str:
    normalized = str(arguments or "").strip()
    markers = tuple(
        marker
        for marker in (
            f"@{display_name.strip()}",
            f"@{user_id.strip()}",
        )
        if len(marker) > 1
    )
    for marker in markers:
        normalized = normalized.replace(marker, " ", 1)
    return " ".join(normalized.split())


def parse_gift_query(
    arguments: str,
    *,
    target_display_name: str,
    target_user_id: str,
) -> GiftQuery:
    """Parse `/猪猪赠送 <选择器> @用户` and its food equivalent."""

    selector = _remove_mention_marker(
        arguments,
        display_name=target_display_name,
        user_id=target_user_id,
    )
    if not selector:
        raise DomainValidationError("请填写要赠送的资产，例如：/猪猪赠送 猪#A1B2C3D4 @群友")
    return GiftQuery(selector=selector)


def parse_trade_offer_query(
    arguments: str,
    *,
    target_display_name: str,
    target_user_id: str,
) -> TradeOfferQuery:
    """Parse `/猪猪交易 <选择器> @用户 <猪币>` and its food equivalent."""

    normalized = _remove_mention_marker(
        arguments,
        display_name=target_display_name,
        user_id=target_user_id,
    )
    selector, separator, price_text = normalized.rpartition(" ")
    if not separator or not selector:
        raise DomainValidationError(
            "请填写资产、接收群友和价格，例如：/猪猪交易 猪#A1B2C3D4 @群友 100"
        )
    try:
        price = int(price_text)
    except ValueError as exc:
        raise DomainValidationError("交易价格必须是正整数猪币。") from exc
    if price <= 0:
        raise DomainValidationError("交易价格必须是正整数猪币。")
    return TradeOfferQuery(selector=selector.strip(), price=price)


def parse_trade_id(arguments: str) -> str:
    """Parse one copyable trade number."""

    return normalize_trade_id(arguments)


def parse_trade_list_query(arguments: str) -> TradeListQuery:
    """Parse `/我的交易 [全部|待处理|已完成|已拒绝|已取消|已过期] [页码]`."""

    page = 1
    status: TradeStatus | None = None
    status_seen = False
    page_seen = False
    for token in str(arguments or "").split():
        if token in {"全部", "待处理", "已完成", "已拒绝", "已取消", "已过期"}:
            if status_seen:
                raise DomainValidationError("交易状态不能重复填写。")
            status = trade_status_from_label(token)
            status_seen = True
            continue
        if page_seen:
            raise DomainValidationError(f"无法识别我的交易参数“{token}”。")
        page = _positive_page(token)
        page_seen = True
    return TradeListQuery(page=page, status=status)


def parse_showcase_query(arguments: str) -> ShowcaseQuery:
    """Parse `/设置展示 <猪猪|美食> <选择器|取消>`."""

    kind_text, separator, selector = str(arguments or "").strip().partition(" ")
    kind_map = {"猪猪": AssetKind.PIG, "美食": AssetKind.FOOD}
    if kind_text not in kind_map or not separator or not selector.strip():
        raise DomainValidationError(
            "格式：/设置展示 <猪猪|美食> <名称#短编号|取消>"
        )
    normalized_selector = selector.strip()
    return ShowcaseQuery(
        asset_kind=kind_map[kind_text],
        selector="" if normalized_selector == "取消" else normalized_selector,
        clear=normalized_selector == "取消",
    )


def parse_ranking_query(arguments: str) -> RankingQuery:
    """Parse `/猪猪排行 [综合|抓猪|美食|价值|巨物|数量|猪币] [页码]`."""

    ranking_type = "综合"
    page = 1
    type_seen = False
    page_seen = False
    for token in str(arguments or "").split():
        try:
            candidate = normalize_ranking_type(token)
        except DomainValidationError:
            candidate = ""
        if candidate:
            if type_seen:
                raise DomainValidationError("排行类型不能重复填写。")
            ranking_type = candidate
            type_seen = True
            continue
        if page_seen:
            raise DomainValidationError(f"无法识别排行参数“{token}”。")
        page = _positive_page(token)
        page_seen = True
    return RankingQuery(ranking_type=ranking_type, page=page)
