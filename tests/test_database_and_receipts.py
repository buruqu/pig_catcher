"""SQLite 迁移、事务、备份、维护和幂等收据。"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.domain.errors import (
    DatabaseError,
    DatabaseNotOpenError,
    MigrationError,
    ReceiptConflictError,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure import PigCatcherDatabase, safe_database_path
from pig_catcher.infrastructure.migrations.v0001_initial import MIGRATION_0001
from pig_catcher.services import (
    FrameworkService,
    MaintenanceOptions,
    MaintenanceRunner,
    ReceiptService,
)
from pig_catcher.version import SCHEMA_VERSION


@pytest.mark.asyncio
async def test_empty_database_migrates_and_passes_integrity_check(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    assert await database.schema_version() == SCHEMA_VERSION
    assert await database.integrity_check() == ("ok",)
    rows = await database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    names = {str(row["name"]) for row in rows}
    assert {
        "scopes",
        "players",
        "pig_templates",
        "food_templates",
        "command_receipts",
        "currency_ledger",
        "trade_offers",
    } <= names
    await database.close()


@pytest.mark.asyncio
async def test_existing_v1_database_migrates_media_and_collection_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v1.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        )
        """
    )
    for statement in MIGRATION_0001.statements:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (1, ?, 'now')",
        (MIGRATION_0001.name,),
    )
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    database = PigCatcherDatabase(path)
    await database.open()
    assert await database.schema_version() == SCHEMA_VERSION
    columns = await database.fetch_all("PRAGMA table_info(pig_templates)")
    names = {str(row["name"]) for row in columns}
    assert {
        "media_format",
        "is_animated",
        "frame_count",
        "collection_id",
        "character_name",
    } <= names
    await database.close()


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    with pytest.raises(RuntimeError):
        async with database.transaction() as session:
            await session.execute(
                """
                INSERT INTO scopes(
                    scope_id, platform, group_id, group_name, stream_id, created_at, updated_at
                ) VALUES ('qq:1', 'qq', '1', '', '', 'now', 'now')
                """
            )
            raise RuntimeError("rollback")
    assert await database.fetch_one("SELECT scope_id FROM scopes WHERE scope_id = 'qq:1'") is None
    await database.close()


@pytest.mark.asyncio
async def test_closed_database_rejects_operations(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    with pytest.raises(DatabaseNotOpenError):
        await database.fetch_one("SELECT 1")


@pytest.mark.asyncio
async def test_database_rejects_future_schema(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT UNIQUE, applied_at TEXT)")
    future_version = SCHEMA_VERSION + 1
    connection.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, 'future', 'now')",
        (future_version,),
    )
    connection.execute(f"PRAGMA user_version = {future_version}")
    connection.commit()
    connection.close()
    database = PigCatcherDatabase(path)
    with pytest.raises(MigrationError, match="高于"):
        await database.open()


@pytest.mark.parametrize(
    "filename",
    ["../pig.sqlite3", "nested/pig.sqlite3", "pig.txt", "", "C:\\escape.sqlite3"],
)
def test_database_path_is_confined(filename: str, tmp_path: Path) -> None:
    with pytest.raises(DatabaseError):
        safe_database_path(tmp_path, filename)


@pytest.mark.asyncio
async def test_online_backup_is_readable(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    backup = await database.backup_to(tmp_path / "backups" / "copy.sqlite3")
    connection = sqlite3.connect(backup)
    assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    connection.close()
    await database.close()


@pytest.mark.asyncio
async def test_maintenance_cleans_staging_and_prunes_backups(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    storage = AssetCatalogStorage(tmp_path)
    storage.ensure_layout()
    stale = storage.staging_root / "stale"
    stale.mkdir()
    os.utime(stale, (1, 1))
    backups = tmp_path / "backups"
    backups.mkdir()
    for index in range(3):
        path = backups / f"pig-catcher-old-{index}.sqlite3"
        path.write_bytes(b"old")
        os.utime(path, (index + 1, index + 1))

    runner = MaintenanceRunner(
        database,
        storage,
        tmp_path,
        MaintenanceOptions(60, True, True, 1, 2, 1),
        logger=logging.getLogger("test.maintenance"),
    )
    report = await runner.run_once()
    assert report.integrity_results == ("ok",)
    assert report.removed_staging_directories == 1
    assert report.backup_path is not None
    assert report.backup_path.is_file()
    assert len(list(backups.glob("pig-catcher-*.sqlite3"))) == 2
    await database.close()


def _identity(message_id: str = "message-1") -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", "100"),
        stream_id="stream-100",
        user_id="200",
        display_name="测试成员",
        message_id=message_id,
        group_name="测试群",
    )


@pytest.mark.asyncio
async def test_receipt_reservation_is_idempotent_and_detects_conflicts(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    identity = _identity()
    await FrameworkService(database).touch_identity(identity)
    receipts = ReceiptService(database)
    arguments = {
        "idempotency_key": "same-key",
        "scope_id": identity.scope.value,
        "player_id": identity.player_id,
        "command_name": "catch",
        "request_payload": {"feed": 0},
        "result_type": "pig",
        "result_object_id": "pig-1",
        "result_payload": {"rarity": 1},
        "text_summary": "抓到一只猪",
    }
    first = await receipts.reserve(**arguments)
    second = await receipts.reserve(**arguments)
    assert first.created is True
    assert second.created is False
    assert first.receipt.receipt_id == second.receipt.receipt_id
    with pytest.raises(ReceiptConflictError):
        await receipts.reserve(**{**arguments, "request_payload": {"feed": 1}})
    await database.close()


@pytest.mark.asyncio
async def test_receipt_send_can_be_claimed_only_once_even_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "pig.sqlite3"
    database = PigCatcherDatabase(path)
    await database.open()
    identity = _identity()
    await FrameworkService(database).touch_identity(identity)
    receipts = ReceiptService(database)
    reservation = await receipts.reserve(
        idempotency_key="send-once",
        scope_id=identity.scope.value,
        player_id=identity.player_id,
        command_name="cook",
        request_payload={"pig": "A19F2C3D"},
        result_type="food",
        result_object_id="food-1",
        text_summary="做菜成功",
    )
    assert await receipts.claim_send(reservation.receipt.receipt_id) is True
    assert await receipts.claim_send(reservation.receipt.receipt_id) is False
    await database.close()

    reopened = PigCatcherDatabase(path)
    await reopened.open()
    after_restart = ReceiptService(reopened)
    assert await after_restart.claim_send(reservation.receipt.receipt_id) is False
    assert await after_restart.mark_sent(reservation.receipt.receipt_id) is True
    receipt = await after_restart.get_by_key("send-once")
    assert receipt is not None
    assert receipt.send_status.value == "sent"
    await reopened.close()


@pytest.mark.asyncio
async def test_failed_send_is_terminal_for_automatic_claim(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    identity = _identity()
    await FrameworkService(database).touch_identity(identity)
    receipts = ReceiptService(database)
    reservation = await receipts.reserve(
        idempotency_key="failed-send",
        scope_id=identity.scope.value,
        player_id=identity.player_id,
        command_name="sell",
        request_payload={"asset": "A19F2C3D"},
        result_type="sale",
        text_summary="售卖成功",
    )
    assert await receipts.claim_send(reservation.receipt.receipt_id)
    assert await receipts.mark_failed(reservation.receipt.receipt_id, "adapter down")
    assert not await receipts.claim_send(reservation.receipt.receipt_id)
    await database.close()


@pytest.mark.asyncio
async def test_concurrent_receipt_reservation_across_connections_commits_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pig.sqlite3"
    first_database = PigCatcherDatabase(path)
    second_database = PigCatcherDatabase(path)
    await first_database.open()
    await second_database.open()
    identity = _identity()
    await FrameworkService(first_database).touch_identity(identity)
    arguments = {
        "idempotency_key": "concurrent-key",
        "scope_id": identity.scope.value,
        "player_id": identity.player_id,
        "command_name": "catch",
        "request_payload": {"feed": 0},
        "result_type": "pig",
        "result_object_id": "pig-concurrent",
        "text_summary": "并发抓猪结果",
    }
    results = await asyncio.gather(
        ReceiptService(first_database).reserve(**arguments),
        ReceiptService(second_database).reserve(**arguments),
    )
    assert sorted(result.created for result in results) == [False, True]
    assert results[0].receipt.receipt_id == results[1].receipt.receipt_id
    await second_database.close()
    await first_database.close()
