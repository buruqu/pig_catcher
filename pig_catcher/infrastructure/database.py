"""插件自有 SQLite 生命周期、迁移与事务。"""

from __future__ import annotations

import asyncio
import sqlite3
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
    """使用短连接和串行事务保护的 SQLite 管理器。"""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._lifecycle_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def _connect(self) -> aiosqlite.Connection:
        connection = await aiosqlite.connect(self.path, isolation_level=None)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        await connection.execute("PRAGMA synchronous = NORMAL")
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
        """等待活动事务结束并停止接受新事务。"""

        async with self._lifecycle_lock:
            async with self._operation_lock:
                self._is_open = False

    async def _migrate(self, connection: aiosqlite.Connection) -> None:
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
            foreign_key_rows = await (
                await connection.execute("PRAGMA foreign_key_check")
            ).fetchall()
            if foreign_key_rows:
                first = tuple(foreign_key_rows[0])
                raise MigrationError(
                    f"迁移后外键检查失败，共 {len(foreign_key_rows)} 条，首条={first}。"
                )
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.execute("PRAGMA foreign_keys = ON")

    def _require_open(self) -> None:
        if not self._is_open:
            raise DatabaseNotOpenError("抓猪数据库尚未打开。")

    @asynccontextmanager
    async def transaction(self, *, immediate: bool = True) -> AsyncIterator[DatabaseSession]:
        """开启由调用方拥有的显式事务。"""

        async with self._operation_lock:
            self._require_open()
            connection = await self._connect()
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
        """使用 SQLite 在线备份 API 生成一致副本。"""

        destination = Path(destination)
        async with self._operation_lock:
            self._require_open()
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = await self._connect()
            target = await aiosqlite.connect(destination)
            try:
                await source.backup(target)
                await target.commit()
            except sqlite3.Error as exc:
                raise DatabaseError(f"数据库备份失败：{exc}") from exc
            finally:
                await target.close()
                await source.close()
        return destination
