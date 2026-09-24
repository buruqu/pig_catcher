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
        weekly_tick: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.packets = packets
        self.campaigns = campaigns
        self.logger = logger
        self.deliver = deliver
        self.interval = interval
        self.weekly_tick = weekly_tick
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
        errors = []
        # 独立运营任务互不阻塞：一项故障仍需让其他已提交的公告得到发送机会。
        if self.weekly_tick is not None:
            try:
                await self.weekly_tick()
            except Exception as exc:
                self.logger.exception("抓猪周榜交接失败；已提交奖励不会重复发放")
                errors.append(exc)
        await self.packets.expire()
        try:
            await self.campaigns.ensure_mid_autumn_scheduled()
            count = await self.campaigns.process_due()
            if count:
                self.logger.info("抓猪定时活动福利已入账：%s人", count)
        finally:
            # A failed scope cannot suppress already committed notices in other scopes.
            for stream, result in await self.campaigns.pending_notices():
                await self.deliver(stream, result)
        if errors:
            raise errors[0]

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                self.logger.exception("抓猪红包/活动定时处理失败；未提交事务将于下一轮安全重试")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass
