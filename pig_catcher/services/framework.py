"""群范围和玩家身份应用服务。"""

from __future__ import annotations

from ..domain.models import CommandIdentity
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import PigCatcherDatabase
from ..infrastructure.repositories import FrameworkRepository


def _iso_timestamp(clock: Clock) -> str:
    return clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FrameworkService:
    """在单一事务中写入范围与玩家身份快照。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        repository: FrameworkRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or FrameworkRepository()
        self.clock = clock or SystemClock()

    async def touch_identity(self, identity: CommandIdentity) -> None:
        now = _iso_timestamp(self.clock)
        async with self.database.transaction() as session:
            await self.repository.touch_identity(session, identity=identity, now=now)
