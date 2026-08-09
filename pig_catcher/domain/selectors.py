"""资产选择器解析。"""

from __future__ import annotations

from .errors import SelectorValidationError
from .models import AssetSelector


def parse_asset_selector(value: str) -> AssetSelector:
    """解析“名称”或“名称#短编号”。"""

    normalized = str(value or "").strip()
    if not normalized:
        raise SelectorValidationError("请提供资产名称。")
    name, separator, possible_code = normalized.rpartition("#")
    if not separator:
        return AssetSelector(name=normalized)
    if not name.strip():
        raise SelectorValidationError("短编号前必须包含资产名称。")
    return AssetSelector(name=name, short_code=possible_code)
