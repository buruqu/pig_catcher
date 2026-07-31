"""可审计、按群精确执行的抓猪额度重置服务。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ..domain.errors import DomainValidationError
from ..domain.models import ScopeKey
from ..domain.ports import Clock, SystemClock
from ..domain.quota import CatchQuotaWindow, catch_quota_window
from ..infrastructure.database import PigCatcherDatabase
from ..infrastructure.repositories import QuotaRepository
from ..version import RULESET_VERSION
from .command_state import iso_timestamp


@dataclass(frozen=True, slots=True)
class CatchQuotaResetResult:
    """一次指定群当前窗口重置的审计结果。"""

    audit_event_id: str
    scope_id: str
    window: CatchQuotaWindow
    cleared_catches: int
    affected_players: int
    backup_path: Path
    created_at: str


class CatchQuotaResetService:
    """通过审计时间戳归零有效计数，不删除任何历史回执。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        refresh_hours: list[int] | tuple[int, ...],
        timezone_name: str,
        window_limit: int,
        repository: QuotaRepository | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.refresh_hours = tuple(refresh_hours)
        self.timezone_name = timezone_name
        self.window_limit = int(window_limit)
        self.repository = repository or QuotaRepository()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)

    async def backup_and_reset_current_window(
        self,
        *,
        data_dir: Path,
        group_id: str,
        platform: str = "qq",
        actor_user_id: str,
        source: str,
    ) -> CatchQuotaResetResult:
        """先在线备份，再精确重置一个群的当前额度窗口。"""

        scope_id = ScopeKey(platform=platform, group_id=group_id).value
        if not await self._scope_exists(scope_id):
            raise DomainValidationError(f"数据库中不存在群范围 {scope_id}，拒绝重置。")
        now = self.clock.now()
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        scope_fingerprint = sha256(scope_id.encode("utf-8")).hexdigest()[:12]
        backup_path = (
            Path(data_dir).resolve()
            / "backups"
            / f"pig_catcher-pre-quota-reset-{platform}-{scope_fingerprint}-{timestamp}.sqlite3"
        )
        await self.database.backup_to(backup_path)
        return await self.reset_current_window(
            group_id=group_id,
            platform=platform,
            actor_user_id=actor_user_id,
            source=source,
            backup_path=backup_path,
            now=now,
        )

    async def reset_current_window(
        self,
        *,
        group_id: str,
        platform: str = "qq",
        actor_user_id: str,
        source: str,
        backup_path: Path,
        now: datetime | None = None,
    ) -> CatchQuotaResetResult:
        """写入群级窗口重置事件并返回重置前的有效用量。"""

        scope_id = ScopeKey(platform=platform, group_id=group_id).value
        now_datetime = now or self.clock.now()
        window = catch_quota_window(
            now_datetime,
            refresh_hours=self.refresh_hours,
            timezone_name=self.timezone_name,
        )
        now_text = iso_timestamp(now_datetime)
        window_start = iso_timestamp(window.start)
        window_end = iso_timestamp(window.end)
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            if not await self.repository.scope_exists(session, scope_id=scope_id):
                raise DomainValidationError(f"数据库中不存在群范围 {scope_id}，拒绝重置。")
            effective_start = await self.repository.effective_window_start(
                session,
                scope_id=scope_id,
                window_start=window_start,
                window_end=window_end,
            )
            cleared_catches, affected_players = await self.repository.usage_since(
                session,
                scope_id=scope_id,
                effective_start=effective_start,
                window_end=window_end,
            )
            detail = {
                "source": str(source or "").strip() or "manual",
                "ruleset_version": RULESET_VERSION,
                "window_start": window_start,
                "window_end": window_end,
                "previous_effective_start": effective_start,
                "window_limit": self.window_limit,
                "refresh_hours": list(self.refresh_hours),
                "cleared_catches": cleared_catches,
                "affected_players": affected_players,
                "cooldown_cleared": True,
                "backup_path": str(Path(backup_path).resolve()),
            }
            await self.repository.insert_reset_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=scope_id,
                actor_user_id=str(actor_user_id or "").strip() or "local-operator",
                object_id=window_start,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=now_text,
            )
        return CatchQuotaResetResult(
            audit_event_id=audit_event_id,
            scope_id=scope_id,
            window=window,
            cleared_catches=cleared_catches,
            affected_players=affected_players,
            backup_path=Path(backup_path).resolve(),
            created_at=now_text,
        )

    async def _scope_exists(self, scope_id: str) -> bool:
        async with self.database.transaction(immediate=False) as session:
            return await self.repository.scope_exists(session, scope_id=scope_id)
