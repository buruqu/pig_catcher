"""数据库备份、完整性检查和素材暂存清理。"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..assets import AssetCatalogStorage
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import PigCatcherDatabase
from ..infrastructure.repositories import (
    OperationsRepository,
    RegulationRepository,
    SocialRepository,
)
from .command_state import iso_timestamp


@dataclass(frozen=True, slots=True)
class MaintenanceOptions:
    """后台维护参数快照。"""

    interval_minutes: int
    run_integrity_check: bool
    auto_backup_enabled: bool
    backup_interval_hours: int
    backup_retention_count: int
    staging_max_age_hours: int
    initial_delay_seconds: int = 120
    full_check_interval_hours: int = 24
    catalog_rollback_retention_count: int = 1
    catalog_cleanup_grace_hours: int = 24


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """一次维护运行结果。"""

    integrity_results: tuple[str, ...]
    backup_path: Path | None
    removed_backups: int
    removed_backup_bytes: int
    removed_staging_directories: int
    removed_catalog_directories: int
    removed_catalog_bytes: int
    catalog_cleanup_skipped: bool
    expired_trade_offers: int
    expired_regulation_holds: int
    ledger_mismatch_count: int
    ledger_reconciliation_performed: bool
    integrity_check_performed: bool
    active_asset_file_count: int
    missing_asset_file_count: int

    @property
    def reclaimed_bytes(self) -> int:
        """返回本轮可确认已释放的备份和素材目录字节数。"""

        return self.removed_backup_bytes + self.removed_catalog_bytes


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
        social_repository: SocialRepository | None = None,
        operations_repository: OperationsRepository | None = None,
        regulation_repository: RegulationRepository | None = None,
    ) -> None:
        self.database = database
        self.storage = storage
        self.data_dir = Path(data_dir).resolve()
        self.options = options
        self.logger = logger
        self.clock = clock or SystemClock()
        self.social_repository = social_repository or SocialRepository()
        self.operations_repository = operations_repository or OperationsRepository()
        self.regulation_repository = regulation_repository or RegulationRepository()
        self.backups_dir = self.data_dir / "backups"
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_full_check_at: datetime | None = None

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
        if self.options.initial_delay_seconds > 0:
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.options.initial_delay_seconds,
                )
            except TimeoutError:
                pass
        while not self._stop_event.is_set():
            try:
                report = await self.run_once()
                if report.integrity_results and report.integrity_results != ("ok",):
                    self.logger.error("抓猪数据库完整性检查异常：%s", report.integrity_results)
                if report.ledger_mismatch_count:
                    self.logger.error(
                        "抓猪全库账本对账异常：%s 位玩家余额与流水不一致",
                        report.ledger_mismatch_count,
                    )
                if report.missing_asset_file_count:
                    self.logger.error(
                        "抓猪正式素材巡检异常：%s/%s 个启用素材文件缺失",
                        report.missing_asset_file_count,
                        report.active_asset_file_count,
                    )
                self.logger.info(
                    "抓猪生产巡检完成：完整性=%s，账本=%s，素材缺失=%s/%s，"
                    "过期报价=%s，过期监管限制=%s，备份=%s，清理备份=%s，"
                    "清理旧素材=%s，释放=%s 字节",
                    (
                        ",".join(report.integrity_results) or "未检查"
                        if report.integrity_check_performed
                        else "未到周期"
                    ),
                    (
                        (
                            f"异常 {report.ledger_mismatch_count}"
                            if report.ledger_mismatch_count
                            else "正常"
                        )
                        if report.ledger_reconciliation_performed
                        else "未到周期"
                    ),
                    report.missing_asset_file_count,
                    report.active_asset_file_count,
                    report.expired_trade_offers,
                    report.expired_regulation_holds,
                    report.backup_path.name if report.backup_path is not None else "未到周期",
                    report.removed_backups,
                    (
                        "跳过（保留备份不可读）"
                        if report.catalog_cleanup_skipped
                        else str(report.removed_catalog_directories)
                    ),
                    report.reclaimed_bytes,
                )
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
        run_full_checks = self._full_check_due(self.clock.now())
        async with self.database.transaction() as session:
            now = iso_timestamp(self.clock.now())
            expired_trade_offers = await self.social_repository.expire_stale_offers(
                session,
                now=now,
            )
            expired_regulation_holds = await self.regulation_repository.expire_holds(
                session,
                now=now,
            )
        # 日常过期状态写入保持短事务；全账本聚合和素材引用扫描使用只读事务，
        # 避免每日完整巡检长时间占用 SQLite 写锁。
        async with self.database.transaction(immediate=False) as session:
            ledger_mismatch_count = 0
            if run_full_checks:
                ledger_mismatch_count = await self.operations_repository.balance_mismatch_count(
                    session
                )
            active_asset_paths = await self.operations_repository.active_asset_paths(
                session
            )
        integrity_results: tuple[str, ...] = ()
        integrity_check_performed = run_full_checks and self.options.run_integrity_check
        if integrity_check_performed:
            integrity_results = await self.database.integrity_check()
        if run_full_checks:
            self._last_full_check_at = self.clock.now()
        missing_asset_file_count = await asyncio.to_thread(
            self._missing_asset_file_count,
            active_asset_paths,
        )
        removed_staging = await self.storage.cleanup_staging(
            older_than_hours=self.options.staging_max_age_hours
        )
        backup_path: Path | None = None
        backup_due = self.options.auto_backup_enabled and self._backup_due(
            self.clock.now()
        )
        removed_backups = 0
        removed_backup_bytes = 0
        if backup_due:
            # 先从超额旧备份中释放足够空间，再创建新的原子临时副本；至少保留
            # 一份既有备份，避免新备份失败时把最后恢复点一并移除。
            pre_count, pre_bytes = self._prune_backups(
                retention_count=max(1, self.options.backup_retention_count - 1)
            )
            removed_backups += pre_count
            removed_backup_bytes += pre_bytes
            backup_path = await self._create_backup(self.clock.now())
        post_count, post_bytes = self._prune_backups()
        removed_backups += post_count
        removed_backup_bytes += post_bytes
        backup_asset_paths, unreadable_backup_count = await asyncio.to_thread(
            self._backup_asset_paths,
            self._all_backup_files(),
        )
        # 备份检查可能耗时，删除前重新读取一次活动模板引用；再配合新目录
        # 保护期，封住外部素材导入恰好与维护并发的窗口。
        async with self.database.transaction(immediate=False) as session:
            latest_active_asset_paths = await self.operations_repository.active_asset_paths(
                session
            )
        catalog_cleanup_skipped = unreadable_backup_count > 0
        if catalog_cleanup_skipped:
            removed_catalogs, removed_catalog_bytes = 0, 0
            self.logger.warning(
                "有 %s 份保留备份无法读取素材引用，本轮跳过旧素材清理",
                unreadable_backup_count,
            )
        else:
            removed_catalogs, removed_catalog_bytes = await self.storage.cleanup_catalogs(
                (*latest_active_asset_paths, *backup_asset_paths),
                retain_unreferenced=self.options.catalog_rollback_retention_count,
                minimum_age_hours=self.options.catalog_cleanup_grace_hours,
            )
        return MaintenanceReport(
            integrity_results=integrity_results,
            backup_path=backup_path,
            removed_backups=removed_backups,
            removed_backup_bytes=removed_backup_bytes,
            removed_staging_directories=removed_staging,
            removed_catalog_directories=removed_catalogs,
            removed_catalog_bytes=removed_catalog_bytes,
            catalog_cleanup_skipped=catalog_cleanup_skipped,
            expired_trade_offers=expired_trade_offers,
            expired_regulation_holds=expired_regulation_holds,
            ledger_mismatch_count=ledger_mismatch_count,
            ledger_reconciliation_performed=run_full_checks,
            integrity_check_performed=integrity_check_performed,
            active_asset_file_count=len(active_asset_paths),
            missing_asset_file_count=missing_asset_file_count,
        )

    def _full_check_due(self, now: datetime) -> bool:
        previous = self._last_full_check_at
        if previous is None:
            return True
        age_seconds = max(0.0, now.timestamp() - previous.timestamp())
        return age_seconds >= self.options.full_check_interval_hours * 3600

    def _missing_asset_file_count(self, relative_paths: tuple[str, ...]) -> int:
        missing = 0
        for relative_path in relative_paths:
            normalized = Path(relative_path)
            candidate = (self.data_dir / normalized).resolve()
            if (
                not relative_path
                or normalized.is_absolute()
                or candidate == self.data_dir
                or not candidate.is_relative_to(self.data_dir)
                or not candidate.is_file()
            ):
                missing += 1
        return missing

    def _all_backup_files(self) -> list[Path]:
        if not self.backups_dir.is_dir():
            return []
        return sorted(
            (
                path
                for path in self.backups_dir.glob("*.sqlite3")
                if path.is_file() and not path.is_symlink()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _scheduled_backup_files(self) -> list[Path]:
        pattern = re.compile(r"^pig-catcher-\d{8}T\d{12}Z\.sqlite3$")
        return [path for path in self._all_backup_files() if pattern.fullmatch(path.name)]

    def _backup_due(self, now: datetime) -> bool:
        files = self._scheduled_backup_files()
        if not files:
            return True
        age_seconds = max(0.0, now.timestamp() - files[0].stat().st_mtime)
        return age_seconds >= self.options.backup_interval_hours * 3600

    async def _create_backup(self, now: datetime) -> Path:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.backups_dir / f"pig-catcher-{timestamp}.sqlite3"
        return await self.database.backup_to(destination)

    def _prune_backups(
        self,
        *,
        retention_count: int | None = None,
    ) -> tuple[int, int]:
        files = self._all_backup_files()
        keep = (
            self.options.backup_retention_count
            if retention_count is None
            else max(0, int(retention_count))
        )
        stale = files[keep:]
        removed_bytes = 0
        for path in stale:
            removed_bytes += self._safe_unlink_backup_file(path)
            for suffix in ("-wal", "-shm"):
                sidecar = path.with_name(f"{path.name}{suffix}")
                if sidecar.is_file() and not sidecar.is_symlink():
                    removed_bytes += self._safe_unlink_backup_file(sidecar)
        removed_bytes += self._prune_orphan_backup_sidecars()
        return len(stale), removed_bytes

    def _prune_orphan_backup_sidecars(self) -> int:
        """清理主备份已不存在的历史 WAL/SHM 小文件。"""

        removed_bytes = 0
        for suffix in ("-wal", "-shm"):
            for sidecar in self.backups_dir.glob(f"*.sqlite3{suffix}"):
                database_path = sidecar.with_name(sidecar.name[: -len(suffix)])
                if (
                    sidecar.is_file()
                    and not sidecar.is_symlink()
                    and not database_path.exists()
                ):
                    removed_bytes += self._safe_unlink_backup_file(sidecar)
        return removed_bytes

    def _safe_unlink_backup_file(self, path: Path) -> int:
        resolved_root = self.backups_dir.resolve()
        resolved = path.resolve()
        if (
            resolved.parent != resolved_root
            or not path.is_file()
            or path.is_symlink()
        ):
            raise RuntimeError(f"拒绝删除备份目录之外或非普通文件：{resolved}")
        size = path.stat().st_size
        path.unlink()
        return size

    def _backup_asset_paths(
        self,
        backup_files: list[Path],
    ) -> tuple[tuple[str, ...], int]:
        """只读提取保留备份素材路径；任一失败时由调用方关闭目录清理。"""

        paths: set[str] = set()
        unreadable_count = 0
        for backup_path in backup_files:
            try:
                uri = f"{backup_path.resolve().as_uri()}?mode=ro&immutable=1"
                with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
                    rows = connection.execute(
                        """
                        SELECT image_relpath FROM pig_templates
                        UNION
                        SELECT image_relpath FROM food_templates
                        """
                    ).fetchall()
                paths.update(str(row[0]) for row in rows if row and row[0])
            except (OSError, sqlite3.Error) as exc:
                unreadable_count += 1
                self.logger.warning(
                    "跳过无法读取素材引用的抓猪备份 %s：%s",
                    backup_path.name,
                    exc,
                )
        return tuple(sorted(paths)), unreadable_count
