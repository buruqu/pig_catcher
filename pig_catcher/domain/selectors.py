"""资产选择器解析。"""

from __future__ import annotations

import re
from uuid import uuid4

from .errors import SelectorValidationError
from .models import AssetSelector

_SHORT_CODE_PATTERN = re.compile(r"^[A-Fa-f0-9]{8}$")


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
    if not _SHORT_CODE_PATTERN.fullmatch(possible_code.strip()):
        raise SelectorValidationError("井号后的资产短编号必须是 8 位十六进制字符。")
    return AssetSelector(name=name, short_code=possible_code)


def new_short_code() -> str:
    """生成全库唯一约束兜底的 8 位展示编号候选。"""

    return uuid4().hex[:8].upper()
