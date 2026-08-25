"""渲染和图片发送失败时的纯文字降级。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
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
        max_concurrent_deliveries: int = 3,
        max_concurrent_image_sends: int = 4,
        queue_timeout_ms: int = 8000,
        image_send_queue_timeout_ms: int = 12000,
        render_timeout_ms: int = 15000,
        image_send_timeout_ms: int = 20000,
        text_send_timeout_ms: int = 5000,
    ) -> None:
        self.send = send
        self.logger = logger
        self.fallback_to_text = fallback_to_text
        self.max_concurrent_deliveries = max(1, int(max_concurrent_deliveries))
        self.max_concurrent_image_sends = max(
            1,
            int(max_concurrent_image_sends),
        )
        self.queue_timeout_seconds = max(0.1, int(queue_timeout_ms) / 1000)
        self.image_send_queue_timeout_seconds = max(
            0.1,
            int(image_send_queue_timeout_ms) / 1000,
        )
        self.render_timeout_seconds = max(0.1, int(render_timeout_ms) / 1000)
        self.image_send_timeout_seconds = max(0.1, int(image_send_timeout_ms) / 1000)
        self.text_send_timeout_seconds = max(0.1, int(text_send_timeout_ms) / 1000)
        self._render_slots = asyncio.Semaphore(self.max_concurrent_deliveries)
        self._image_send_slots = asyncio.Semaphore(
            self.max_concurrent_image_sends
        )

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
        queued_at = perf_counter()
        try:
            await asyncio.wait_for(
                self._render_slots.acquire(),
                timeout=self.queue_timeout_seconds,
            )
        except TimeoutError:
            self.logger.warning(
                "抓猪图片队列繁忙，等待 %.0f ms 后改用纯文字降级",
                (perf_counter() - queued_at) * 1000,
            )
            return await self._send_fallback(stream_id, fallback_text)
        queue_ms = (perf_counter() - queued_at) * 1000
        rendered: RenderedImage | None = None
        sent = False
        image_send_timed_out = False
        render_ms = 0.0
        send_ms = 0.0
        try:
            render_started = perf_counter()
            try:
                rendered = await asyncio.wait_for(
                    render(),
                    timeout=self.render_timeout_seconds,
                )
            except TimeoutError:
                self.logger.warning(
                    "抓猪图片生成超时（%.0f ms），准备使用纯文字降级",
                    self.render_timeout_seconds * 1000,
                )
            except Exception:
                self.logger.exception("抓猪图片生成失败，准备使用纯文字降级")
            render_ms = (perf_counter() - render_started) * 1000
        finally:
            self._render_slots.release()
        send_queue_ms = 0.0
        if rendered is not None:
            send_queued_at = perf_counter()
            try:
                await asyncio.wait_for(
                    self._image_send_slots.acquire(),
                    timeout=self.image_send_queue_timeout_seconds,
                )
            except TimeoutError:
                self.logger.warning(
                    "抓猪图片已生成，但发送队列繁忙（等待 %.0f ms），准备使用纯文字降级",
                    (perf_counter() - send_queued_at) * 1000,
                )
            else:
                send_queue_ms = (perf_counter() - send_queued_at) * 1000
                send_started = perf_counter()
                try:
                    sent = bool(
                        await asyncio.wait_for(
                            self.send.image(rendered.image_base64, stream_id),
                            timeout=self.image_send_timeout_seconds,
                        )
                    )
                except TimeoutError:
                    image_send_timed_out = True
                    self.logger.warning(
                        "抓猪图片发送等待超时（%.0f ms）；发送结果不确定，"
                        "为避免重复公示，本次不再补发文字",
                        self.image_send_timeout_seconds * 1000,
                    )
                except Exception:
                    self.logger.exception("抓猪图片发送失败，准备使用纯文字降级")
                finally:
                    send_ms = (perf_counter() - send_started) * 1000
                    self._image_send_slots.release()
        if sent:
            assert rendered is not None
            self.logger.info(
                "抓猪图片交付完成：渲染排队=%.0fms，渲染=%.0fms，发送排队=%.0fms，发送=%.0fms，图片=%s字节",
                queue_ms,
                render_ms,
                send_queue_ms,
                send_ms,
                rendered.byte_length,
            )
            return True
        if image_send_timed_out:
            return False
        if rendered is not None and send_ms > 0:
            self.logger.warning(
                "抓猪图片发送接口返回失败，准备使用纯文字降级：渲染排队=%.0fms，渲染=%.0fms，发送排队=%.0fms，发送=%.0fms",
                queue_ms,
                render_ms,
                send_queue_ms,
                send_ms,
            )
        return await self._send_fallback(stream_id, fallback_text)

    async def _send_fallback(self, stream_id: str, fallback_text: str) -> bool:
        if not self.fallback_to_text:
            return False
        try:
            return bool(
                await asyncio.wait_for(
                    self.send.text(fallback_text, stream_id),
                    timeout=self.text_send_timeout_seconds,
                )
            )
        except TimeoutError:
            self.logger.warning(
                "抓猪纯文字降级发送超时（%.0f ms）",
                self.text_send_timeout_seconds * 1000,
            )
            return False
        except Exception:
            self.logger.exception("抓猪纯文字降级发送失败")
            return False
