"""第三轮查询命令的确定性参数解析。"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.errors import DomainValidationError

INVENTORY_SORTS = frozenset({"获得时间", "品质", "价值", "体型", "重量", "名称"})


@dataclass(frozen=True, slots=True)
class InventoryQuery:
    """猪猪背包页码、品质与排序。"""

    page: int = 1
    rarity: int | None = None
    sort: str = "获得时间"


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """猪猪图鉴页码、品质与未发现筛选。"""

    page: int = 1
    rarity: int | None = None
    undiscovered_only: bool = False


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


def parse_inventory_query(arguments: str) -> InventoryQuery:
    """解析 `/猪猪背包 [页码] [品质=数字] [排序=方式]`。"""

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
            if candidate not in INVENTORY_SORTS:
                choices = "、".join(sorted(INVENTORY_SORTS))
                raise DomainValidationError(f"不支持排序“{candidate}”。可用：{choices}")
            sort = candidate
            sort_seen = True
            continue
        if page_seen:
            raise DomainValidationError(f"无法识别背包参数“{token}”。")
        page = _positive_page(token)
        page_seen = True
    return InventoryQuery(page=page, rarity=rarity, sort=sort)


def parse_catalog_query(arguments: str) -> CatalogQuery:
    """解析 `/猪猪图鉴 [页码] [品质=数字|未收集]`。"""

    page = 1
    rarity: int | None = None
    undiscovered_only = False
    page_seen = False
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
        if page_seen:
            raise DomainValidationError(f"无法识别图鉴参数“{token}”。")
        page = _positive_page(token)
        page_seen = True
    return CatalogQuery(
        page=page,
        rarity=rarity,
        undiscovered_only=undiscovered_only,
    )


def parse_records_page(arguments: str) -> int:
    """解析 `/猪猪纪录 [页码]`。"""

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
