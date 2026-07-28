"""改变状态命令共享的时间、分页和幂等回执校验。"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ..domain.errors import GameplayError, ReceiptConflictError
from ..domain.models import CommandIdentity, CommandReceipt
from .receipts import request_fingerprint


def iso_timestamp(value: datetime) -> str:
    """把有无时区的时间统一保存为 UTC ISO-8601。"""

    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def valid_page_count(page: int, total: int, page_size: int) -> int:
    """返回总页数并拒绝超出范围的页码。"""

    pages = max(1, math.ceil(total / page_size))
    if page > pages:
        raise GameplayError(f"页码超出范围，当前共有 {pages} 页。")
    return pages


def receipt_payload(receipt: CommandReceipt) -> dict[str, Any]:
    """解析已经提交的结构化命令结果。"""

    try:
        payload = json.loads(receipt.result_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReceiptConflictError("幂等回执中的业务结果无法解析。") from exc
    if not isinstance(payload, dict):
        raise ReceiptConflictError("幂等回执中的业务结果不是对象。")
    return payload


def validate_existing_receipt(
    receipt: CommandReceipt,
    *,
    identity: CommandIdentity,
    command_name: str,
    request_payload: Mapping[str, Any],
) -> None:
    """确保重复消息仍属于同一群、玩家、命令和业务参数。"""

    if (
        receipt.scope_id != identity.scope.value
        or receipt.player_id != identity.player_id
        or receipt.command_name != command_name
    ):
        raise ReceiptConflictError("同一消息 ID 已被其他群、成员或命令使用。")
    if receipt.request_fingerprint != request_fingerprint(request_payload):
        raise ReceiptConflictError("同一消息 ID 对应了不同的业务参数。")
