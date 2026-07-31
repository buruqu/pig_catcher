"""渲染和图片发送失败时的纯文字降级。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from .models import RenderedImage


class SendCapability(Protocol):
    """MaiBot `send` 能力的最小接口。"""

    async def image(self, image_base64: str, stream_id: str) -> object:
        """发送 Base64 图片。"""

    async def text(self, text: str, stream_id: str) -> object:
        """发送纯文字。"""


class RenderDelivery:
    """把图片渲染与发送故障隔离在业务事务之外。"""

    def __init__(
        self,
        send: SendCapability,
        *,
        logger: logging.Logger,
        fallback_to_text: bool,
    ) -> None:
        self.send = send
        self.logger = logger
        self.fallback_to_text = fallback_to_text

    async def send_image_or_text(
        self,
        *,
        stream_id: str,
        render: Callable[[], Awaitable[RenderedImage]],
        fallback_text: str,
        rendering_enabled: bool = True,
    ) -> bool:
        """成功发送任一表现形式时返回 True，否则返回 False。"""

        if not rendering_enabled:
            return await self._send_fallback(stream_id, fallback_text)
        try:
            rendered = await render()
        except Exception:
            self.logger.exception("抓猪图片生成失败，准备使用纯文字降级")
            return await self._send_fallback(stream_id, fallback_text)
        try:
            sent = bool(await self.send.image(rendered.image_base64, stream_id))
        except Exception:
            self.logger.exception("抓猪图片发送失败，准备使用纯文字降级")
            return await self._send_fallback(stream_id, fallback_text)
        if sent:
            return True
        self.logger.warning("抓猪图片发送接口返回失败，准备使用纯文字降级")
        return await self._send_fallback(stream_id, fallback_text)

    async def _send_fallback(self, stream_id: str, fallback_text: str) -> bool:
        if not self.fallback_to_text:
            return False
        try:
            return bool(await self.send.text(fallback_text, stream_id))
        except Exception:
            self.logger.exception("抓猪纯文字降级发送失败")
            return False
