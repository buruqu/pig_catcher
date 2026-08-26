"""插件自有 SQLite 生命周期、迁移与事务。"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from ..domain.errors import DatabaseError, DatabaseNotOpenError, MigrationError
from ..version import SCHEMA_VERSION
from .migrations import MIGRATIONS

_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL
)
"""


def safe_database_path(data_dir: Path, filename: str) -> Path:
    """把数据库文件限制在插件数据目录根部。"""

    base = Path(data_dir).resolve()
    normalized = str(filename or "").strip()
    candidate_name = Path(normalized)
    if (
        not normalized
        or candidate_name.name != normalized
        or candidate_name.is_absolute()
        or ".." in candidate_name.parts
    ):
        raise DatabaseError("数据库配置必须是插件数据目录内的单个文件名。")
    if candidate_name.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise DatabaseError("数据库文件必须使用 .db、.sqlite 或 .sqlite3 后缀。")
    return base / normalized


class DatabaseSession:
    """显式事务中的受限数据库会话。"""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def execute(self, sql: str, parameters: Sequence[Any] | Mapping[str, Any] = ()) -> aiosqlite.Cursor:
        return await self._connection.execute(sql, parameters)

    async def executemany(self, sql: str, parameters: Iterable[Sequence[Any]]) -> aiosqlite.Cursor:
        return await self._connection.executemany(sql, parameters)

    async def fetch_one(
        self,
        sql: str,
        parameters: Sequence[Any] | Mapping[str, Any] = (),
    ) -> aiosqlite.Row | None:
        cursor = await self._connection.execute(sql, parameters)
        return await cursor.fetchone()

    async def fetch_all(
        self,
        sql: str,
        parameters: Sequence[Any] | Mapping[str, Any] = (),
    ) -> list[aiosqlite.Row]:
        cursor = await self._connection.execute(sql, parameters)
        return list(await cursor.fetchall())


class PigCatcherDatabase:
    """使用串行写事务和有界并发只读事务的 SQLite 管理器。"""

    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 5000,
        max_concurrent_reads: int = 4,
    ) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.max_concurrent_reads = max(1, int(max_concurrent_reads))
        self._lifecycle_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._backup_lock = asyncio.Lock()
        self._read_semaphore = asyncio.Semaphore(self.max_concurrent_reads)
        self._read_condition = asyncio.Condition()
        self._active_reads = 0
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def _connect(self, *, query_only: bool = False) -> aiosqlite.Connection:
        connection = await aiosqlite.connect(self.path, isolation_level=None)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        await connection.execute("PRAGMA synchronous = NORMAL")
        if query_only:
            await connection.execute("PRAGMA query_only = ON")
        return connection

    async def open(self) -> None:
        """创建目录、启用 WAL 并执行全部待处理迁移。"""

        async with self._lifecycle_lock:
            if self._is_open:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            async with self._operation_lock:
                connection = await self._connect()
                try:
                    await connection.execute("PRAGMA journal_mode = WAL")
                    await self._migrate(connection)
                except (sqlite3.Error, MigrationError) as exc:
                    raise MigrationError(f"抓猪数据库初始化失败：{exc}") from exc
                finally:
                    await connection.close()
            self._is_open = True

    async def close(self) -> None:
        """停止接受新操作，并依次等待活动备份与事务结束。"""

        async with self._lifecycle_lock:
            self._is_open = False
            # 固定采用 operation -> backup 的锁序。这样即使未来调用方误在
            # 写事务上下文中请求备份，关闭流程也不会反向等待该事务。
            async with self._operation_lock:
                async with self._backup_lock:
                    async with self._read_condition:
                        await self._read_condition.wait_for(lambda: self._active_reads == 0)

    async def _migrate(self, connection: aiosqlite.Connection) -> None:
        version_row = await (await connection.execute("PRAGMA user_version")).fetchone()
        current_version = int(version_row[0]) if version_row is not None else 0
        if current_version > SCHEMA_VERSION:
            raise MigrationError(f"数据库版本 {current_version} 高于当前插件支持的 {SCHEMA_VERSION}，拒绝降级打开。")
        if current_version == SCHEMA_VERSION:
            try:
                migration_row = await (
                    await connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
                ).fetchone()
            except sqlite3.Error:
                migration_row = None
            recorded_version = int(migration_row[0]) if migration_row is not None else -1
            if recorded_version == SCHEMA_VERSION:
                await self._validate_current_schema(connection)
                return

        # SQLite table-rebuild migrations require foreign-key enforcement to be
        # disabled before the transaction begins. Every rebuilt schema is checked
        # again with foreign_key_check before commit, then enforcement is restored.
        await connection.execute("PRAGMA foreign_keys = OFF")
        await connection.execute("BEGIN IMMEDIATE")
        try:
            await connection.execute(_SCHEMA_MIGRATIONS_SQL)
            version_row = await (await connection.execute("PRAGMA user_version")).fetchone()
            user_version = int(version_row[0]) if version_row is not None else 0
            migration_row = await (
                await connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
            ).fetchone()
            recorded_version = int(migration_row[0]) if migration_row is not None else 0
            if user_version != recorded_version:
                raise MigrationError(f"PRAGMA user_version={user_version} 与迁移记录={recorded_version} 不一致。")
            if user_version > SCHEMA_VERSION:
                raise MigrationError(f"数据库版本 {user_version} 高于当前插件支持的 {SCHEMA_VERSION}，拒绝降级打开。")
            for migration in MIGRATIONS:
                if migration.version <= user_version:
                    continue
                if migration.version != user_version + 1:
                    raise MigrationError(f"迁移版本不连续：期望 {user_version + 1}，得到 {migration.version}。")
                for statement in migration.statements:
                    await connection.execute(statement)
                await connection.execute(
                    """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (migration.version, migration.name),
                )
                await connection.execute(f"PRAGMA user_version = {migration.version}")
                user_version = migration.version
            if user_version != SCHEMA_VERSION:
                raise MigrationError(f"迁移结束版本 {user_version} 与代码版本 {SCHEMA_VERSION} 不一致。")
            await self._validate_current_schema(connection)
            foreign_key_rows = await (await connection.execute("PRAGMA foreign_key_check")).fetchall()
            if foreign_key_rows:
                first = tuple(foreign_key_rows[0])
                raise MigrationError(f"迁移后外键检查失败，共 {len(foreign_key_rows)} 条，首条={first}。")
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    async def _validate_current_schema(connection: aiosqlite.Connection) -> None:
        """Reject a stamped database whose critical current structures did not converge."""

        required_tables = {
            "player_food_effects",
            "player_roulette_state",
            "achievement_definition_snapshots",
            "achievement_profiles",
            "achievement_progress",
            "achievement_events",
            "achievement_unlocks",
            "achievement_reward_inventory",
            "achievement_metric_counters",
            "achievement_scope_targets",
            "achievement_backfill_state",
            "achievement_milestone_claims",
            "achievement_operations",
            "achievement_ticket_effects",
            "weekly_competitions",
            "weekly_competition_entries",
            "weekly_competition_settlements",
            "weekly_competition_awards",
        }
        table_rows = await (
            await connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ("
                + ",".join("?" for _ in required_tables)
                + ")",
                tuple(sorted(required_tables)),
            )
        ).fetchall()
        present_tables = {str(row[0]) for row in table_rows}
        missing_tables = required_tables - present_tables
        if missing_tables:
            raise MigrationError("数据库版本已是当前版，但缺少关键表：" + "、".join(sorted(missing_tables)))

        index_rows = await (await connection.execute("PRAGMA index_list(player_food_effects)")).fetchall()
        source_index_found = False
        for row in index_rows:
            index_name = str(row[1])
            is_unique = bool(row[2])
            escaped_name = index_name.replace('"', '""')
            column_rows = await (await connection.execute(f'PRAGMA index_info("{escaped_name}")')).fetchall()
            columns = tuple(str(column[2]) for column in column_rows)
            if "source_food_instance_id" in columns and is_unique:
                raise MigrationError(
                    "player_food_effects.source_food_instance_id 仍带 UNIQUE 约束，会导致轮盘多奖励结算失败。"
                )
            if columns and columns[0] == "source_food_instance_id" and not is_unique:
                source_index_found = True
        if not source_index_found:
            raise MigrationError("player_food_effects 缺少来源查询索引。")

    def _require_open(self) -> None:
        if not self._is_open:
            raise DatabaseNotOpenError("抓猪数据库尚未打开。")

    @asynccontextmanager
    async def transaction(self, *, immediate: bool = True) -> AsyncIterator[DatabaseSession]:
        """开启由调用方拥有的显式事务。"""

        if immediate:
            async with self._operation_lock:
                self._require_open()
                async with self._connection_transaction(
                    immediate=True,
                    query_only=False,
                ) as session:
                    yield session
            return

        async with self._read_semaphore:
            async with self._read_condition:
                self._require_open()
                self._active_reads += 1
            try:
                async with self._connection_transaction(
                    immediate=False,
                    query_only=True,
                ) as session:
                    yield session
            finally:
                async with self._read_condition:
                    self._active_reads -= 1
                    if self._active_reads == 0:
                        self._read_condition.notify_all()

    @asynccontextmanager
    async def _connection_transaction(
        self,
        *,
        immediate: bool,
        query_only: bool,
    ) -> AsyncIterator[DatabaseSession]:
        connection = await self._connect(query_only=query_only)
        try:
            await connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield DatabaseSession(connection)
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def fetch_one(
        self,
        sql: str,
        parameters: Sequence[Any] | Mapping[str, Any] = (),
    ) -> aiosqlite.Row | None:
        """在只读事务中查询单行。"""

        async with self.transaction(immediate=False) as session:
            return await session.fetch_one(sql, parameters)

    async def fetch_all(
        self,
        sql: str,
        parameters: Sequence[Any] | Mapping[str, Any] = (),
    ) -> list[aiosqlite.Row]:
        """在只读事务中查询多行。"""

        async with self.transaction(immediate=False) as session:
            return await session.fetch_all(sql, parameters)

    async def schema_version(self) -> int:
        row = await self.fetch_one("PRAGMA user_version")
        return int(row[0]) if row is not None else 0

    async def integrity_check(self) -> tuple[str, ...]:
        """执行只读快速完整性检查。"""

        rows = await self.fetch_all("PRAGMA quick_check")
        return tuple(str(row[0]) for row in rows)

    async def backup_to(self, destination: Path) -> Path:
        """在线备份到同目录临时文件，校验后原子替换目标。"""

        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise DatabaseError("数据库备份目标不能是正在使用的主数据库。")
        async with self._backup_lock:
            self._require_open()
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            os.close(temporary_fd)
            temporary_path = Path(temporary_name)
            source: aiosqlite.Connection | None = None
            target: aiosqlite.Connection | None = None
            try:
                source = await self._connect()
                target = await aiosqlite.connect(temporary_path)
                await source.backup(target)
                await target.commit()
                await self._verify_backup(target)
                await target.close()
                target = None
                await source.close()
                source = None
                os.replace(temporary_path, destination)
            except DatabaseError:
                raise
            except (OSError, sqlite3.Error) as exc:
                raise DatabaseError(f"数据库备份失败：{exc}") from exc
            finally:
                try:
                    if target is not None:
                        await target.close()
                finally:
                    try:
                        if source is not None:
                            await source.close()
                    finally:
                        try:
                            temporary_path.unlink(missing_ok=True)
                        except OSError:
                            pass
        return destination

    @staticmethod
    async def _verify_backup(connection: aiosqlite.Connection) -> None:
        rows = await (await connection.execute("PRAGMA quick_check")).fetchall()
        results = tuple(str(row[0]) for row in rows)
        if results != ("ok",):
            raise DatabaseError(f"数据库备份完整性检查失败：{results}")
