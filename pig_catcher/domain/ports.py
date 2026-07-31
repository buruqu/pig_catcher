"""可替换的时间、随机和幂等键接口。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Protocol

from .errors import MissingMessageIdError
from .models import CommandIdentity


class RandomSource(Protocol):
    """随机源协议。"""

    def random(self) -> float:
        """返回位于左闭右开区间 [0, 1) 的随机数。"""


class Clock(Protocol):
    """时钟协议。"""

    def now(self) -> datetime:
        """返回带时区时间。"""


class SystemRandomSource:
    """生产环境系统安全随机源。"""

    def __init__(self) -> None:
        self._random = secrets.SystemRandom()

    def random(self) -> float:
        return self._random.random()


class SystemClock:
    """生产环境 UTC 时钟。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


class MessageKeyFactory:
    """为改变状态的命令生成稳定幂等键。"""

    @staticmethod
    def build(identity: CommandIdentity, command_name: str) -> str:
        message_id = identity.message_id.strip()
        if not message_id:
            raise MissingMessageIdError("当前适配器没有提供稳定消息 ID，不能执行改变状态的命令。")
        material = "\x1f".join(
            (
                identity.scope.value,
                identity.user_id,
                str(command_name or "").strip(),
                message_id,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
