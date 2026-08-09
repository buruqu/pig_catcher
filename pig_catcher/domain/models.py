"""与框架无关的领域值对象。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .enums import ReceiptSendStatus
from .errors import ScopeValidationError, SelectorValidationError
from .short_codes import normalize_short_code

_PLATFORM_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _validate_identifier(value: str, *, field_name: str, max_length: int = 256) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ScopeValidationError(f"{field_name}不能为空。")
    if len(normalized) > max_length:
        raise ScopeValidationError(f"{field_name}长度不能超过 {max_length} 个字符。")
    if ":" in normalized or any(ord(char) < 32 for char in normalized):
        raise ScopeValidationError(f"{field_name}包含不允许的字符。")
    return normalized


@dataclass(frozen=True, slots=True)
class ScopeKey:
    """由平台和群 ID 组成的稳定游戏范围。"""

    platform: str
    group_id: str

    def __post_init__(self) -> None:
        platform = str(self.platform or "").strip().lower()
        if not _PLATFORM_PATTERN.fullmatch(platform):
            raise ScopeValidationError("平台标识只能包含字母、数字、点、下划线和横线。")
        group_id = _validate_identifier(self.group_id, field_name="群 ID")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "group_id", group_id)

    @property
    def value(self) -> str:
        """返回可持久化范围键。"""

        return f"{self.platform}:{self.group_id}"

    @classmethod
    def parse(cls, value: str) -> ScopeKey:
        """从持久化范围键恢复值对象。"""

        normalized = str(value or "").strip()
        if normalized.count(":") != 1:
            raise ScopeValidationError("群范围必须使用“平台:群ID”格式。")
        platform, group_id = normalized.split(":", 1)
        return cls(platform=platform, group_id=group_id)


@dataclass(frozen=True, slots=True)
class CommandIdentity:
    """一次群命令的稳定身份快照。"""

    scope: ScopeKey
    stream_id: str
    user_id: str
    display_name: str
    message_id: str = ""
    group_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "stream_id", _validate_identifier(self.stream_id, field_name="聊天流 ID"))
        object.__setattr__(self, "user_id", _validate_identifier(self.user_id, field_name="用户 ID"))
        display_name = str(self.display_name or "").strip() or self.user_id
        object.__setattr__(self, "display_name", display_name[:128])
        object.__setattr__(self, "message_id", str(self.message_id or "").strip()[:256])
        object.__setattr__(self, "group_name", str(self.group_name or "").strip()[:128])

    @property
    def player_id(self) -> str:
        """返回当前群范围内的稳定玩家键。"""

        return f"{self.scope.value}:{self.user_id}"


@dataclass(frozen=True, slots=True)
class AssetSelector:
    """名称与可选短编号组成的资产选择器。"""

    name: str
    short_code: str | None = None

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise SelectorValidationError("资产名称不能为空。")
        if len(name) > 80:
            raise SelectorValidationError("资产名称不能超过 80 个字符。")
        object.__setattr__(self, "name", name)
        if self.short_code is None:
            return
        object.__setattr__(self, "short_code", normalize_short_code(self.short_code))


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    """已经提交的幂等业务结果。"""

    receipt_id: str
    idempotency_key: str
    scope_id: str
    player_id: str | None
    command_name: str
    request_fingerprint: str
    result_type: str
    result_object_id: str
    result_json: str
    text_summary: str
    send_status: ReceiptSendStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ReceiptReservation:
    """幂等收据预留结果。"""

    receipt: CommandReceipt
    created: bool
