"""数据库备份、完整性检查和素材暂存清理。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..assets import AssetCatalogStorage
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import PigCatcherDatabase


@dataclass(frozen=True, slots=True)
class MaintenanceOptions:
    """后台维护参数快照。"""

    interval_minutes: int
    run_integrity_check: bool
    auto_backup_enabled: bool
    backup_interval_hours: int
    backup_retention_count: int
    staging_max_age_hours: int


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """一次维护运行结果。"""

    integrity_results: tuple[str, ...]
    backup_path: Path | None
    removed_backups: int
    removed_staging_directories: int


class MaintenanceRunner:
    """可安全启停且卸载时可等待的维护循环。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        storage: AssetCatalogStorage,
        data_dir: Path,
        options: MaintenanceOptions,
        *,
        logger: logging.Logger,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.storage = storage
        self.data_dir = Path(data_dir).resolve()
        self.options = options
        self.logger = logger
        self.clock = clock or SystemClock()
        self.backups_dir = self.data_dir / "backups"
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="pig-catcher-maintenance")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        await task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                report = await self.run_once()
                if report.integrity_results and report.integrity_results != ("ok",):
                    self.logger.error("抓猪数据库完整性检查异常：%s", report.integrity_results)
            except Exception:
                self.logger.exception("抓猪插件后台维护失败")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.options.interval_minutes * 60,
                )
            except TimeoutError:
                continue

    async def run_once(self) -> MaintenanceReport:
        integrity_results: tuple[str, ...] = ()
        if self.options.run_integrity_check:
            integrity_results = await self.database.integrity_check()
        removed_staging = await self.storage.cleanup_staging(older_than_hours=self.options.staging_max_age_hours)
        backup_path: Path | None = None
        removed_backups = 0
        if self.options.auto_backup_enabled and self._backup_due(self.clock.now()):
            backup_path = await self._create_backup(self.clock.now())
            removed_backups = self._prune_backups()
        return MaintenanceReport(
            integrity_results=integrity_results,
            backup_path=backup_path,
            removed_backups=removed_backups,
            removed_staging_directories=removed_staging,
        )

    def _backup_files(self) -> list[Path]:
        if not self.backups_dir.is_dir():
            return []
        return sorted(
            (
                path
                for path in self.backups_dir.glob("pig-catcher-*.sqlite3")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _backup_due(self, now: datetime) -> bool:
        files = self._backup_files()
        if not files:
            return True
        age_seconds = max(0.0, now.timestamp() - files[0].stat().st_mtime)
        return age_seconds >= self.options.backup_interval_hours * 3600

    async def _create_backup(self, now: datetime) -> Path:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.backups_dir / f"pig-catcher-{timestamp}.sqlite3"
        return await self.database.backup_to(destination)

    def _prune_backups(self) -> int:
        files = self._backup_files()
        stale = files[self.options.backup_retention_count :]
        for path in stale:
            resolved = path.resolve()
            if resolved.parent != self.backups_dir.resolve():
                raise RuntimeError(f"拒绝删除备份目录之外的文件：{resolved}")
            path.unlink()
        return len(stale)
