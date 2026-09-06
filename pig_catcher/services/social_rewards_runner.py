"""Small durable-job poller, owned and awaited by the plugin lifecycle."""

import asyncio
from collections.abc import Awaitable, Callable
from logging import Logger

from .dispatch import DispatchResult
from .red_packets import RedPacketService
from .scheduled_rewards import ScheduledRewardService


class SocialRewardsRunner:
    def __init__(
        self,
        packets: RedPacketService,
        campaigns: ScheduledRewardService,
        *,
        logger: Logger,
        deliver: Callable[[str, DispatchResult], Awaitable[object]],
        interval: float = 15,
    ) -> None:
        self.packets = packets
        self.campaigns = campaigns
        self.logger = logger
        self.deliver = deliver
        self.interval = interval
        self._stop = asyncio.Event()
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        if self.task is None or self.task.done():
            self._stop.clear()
            self.task = asyncio.create_task(self._run(), name="pig-catcher-social-rewards")

    async def stop(self) -> None:
        self._stop.set()
        if self.task is not None:
            await self.task
            self.task = None

    async def tick(self) -> None:
        await self.packets.expire()
        try:
            count = await self.campaigns.process_due()
            if count:
                self.logger.info("抓猪定时生日福利已入账：%s人", count)
        finally:
            # A failed scope cannot suppress already committed notices in other scopes.
            for stream, result in await self.campaigns.pending_notices():
                await self.deliver(stream, result)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                self.logger.exception("抓猪红包/生日定时处理失败；未提交事务将于下一轮安全重试")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass
