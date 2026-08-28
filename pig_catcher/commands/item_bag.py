"""道具背包及奖励券的薄解析层；不抢占吃菜的 /是 与 /否。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..domain.errors import DomainValidationError
from ..domain.item_bag import CODE_CHANGE_COUPON, COUPON_HELP, coupon_definition

ITEM_BAG_PATTERN = r"^/道具背包(?:\s+(?P<arguments>.*?))?\s*$"
REWARD_COUPON_PATTERN = r"^/使用奖励券(?:\s+(?P<arguments>.*?))?\s*$"


@dataclass(frozen=True, slots=True)
class ItemBagRequest:
    action: str
    args: dict[str, Any] = field(default_factory=dict)


def parse_item_bag_request(text: str, *, section: str = "bag") -> ItemBagRequest:
    normalized = str(text or "").strip()
    if len(normalized) > 300:
        raise DomainValidationError("指令过长，请 /道具背包 查看用法。")
    if section == "bag":
        if not normalized:
            return ItemBagRequest("bag", {"page": 1})
        if not re.fullmatch(r"[0-9]{1,6}", normalized) or int(normalized) < 1:
            raise DomainValidationError("页码必须是正整数，例如 /道具背包 2。")
        return ItemBagRequest("bag", {"page": int(normalized)})
    if section != "coupon":
        raise DomainValidationError("未知道具命令入口。")
    if normalized in {"确认", "取消"}:
        return ItemBagRequest("confirm" if normalized == "确认" else "cancel")
    parts = normalized.split(None, 1)
    if not parts:
        raise DomainValidationError("\n".join(COUPON_HELP))
    coupon = coupon_definition(parts[0])
    if len(parts) != 2:
        raise DomainValidationError("\n".join(COUPON_HELP))
    if coupon.coupon_id == CODE_CHANGE_COUPON:
        words = parts[1].split(None, 1)
        if len(words) != 2:
            raise DomainValidationError(COUPON_HELP[0])
        selector_and_code = words[1].rsplit(None, 1)
        if len(selector_and_code) != 2:
            raise DomainValidationError(COUPON_HELP[0])
        return ItemBagRequest(
            "rename",
            {"asset_kind": words[0], "selector": selector_and_code[0], "new_code": selector_and_code[1]},
        )
    return ItemBagRequest("choose-pig", {"selector": parts[1].strip()})
