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
        "player_food_effects",
        "player_catch_quota_bonuses",
        "player_restrictions",
        "trade_offers",
    } <= names
    pig_columns = await database.fetch_all("PRAGMA table_info(pig_templates)")
    assert "paired_food_template_id" in {str(row["name"]) for row in pig_columns}
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
    effect_table = await database.fetch_one(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = 'player_food_effects'
        """
    )
    assert effect_table is not None
    await database.close()


@pytest.mark.asyncio
async def test_legacy_v9_social_ban_splits_into_two_permanent_blacklists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-v9.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations(version, name, applied_at)
        VALUES (9, 'player_restrictions', '2026-08-05T00:00:00Z');
        CREATE TABLE scopes (
            scope_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL DEFAULT '',
            stream_id TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE players (
            player_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id) ON DELETE CASCADE,
            platform_user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            coin_balance INTEGER NOT NULL DEFAULT 0,
            experience INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE player_restrictions (
            restriction_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            restriction_type TEXT NOT NULL CHECK (
                restriction_type IN ('social-transfer-ban', 'catch-window-limit')
            ),
            limit_value INTEGER,
            starts_at TEXT NOT NULL,
            expires_at TEXT,
            reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(player_id, restriction_type)
        );
        CREATE TABLE pig_templates (
            template_id TEXT PRIMARY KEY,
            catalog_hash TEXT NOT NULL,
            template_version INTEGER NOT NULL DEFAULT 1,
            display_name TEXT NOT NULL,
            rarity INTEGER NOT NULL,
            scope_type TEXT NOT NULL,
            description TEXT NOT NULL,
            image_relpath TEXT NOT NULL,
            image_sha256 TEXT NOT NULL,
            image_fit TEXT NOT NULL,
            length_min REAL NOT NULL,
            length_max REAL NOT NULL,
            weight_min REAL NOT NULL,
            weight_max REAL NOT NULL,
            fat_profile TEXT NOT NULL,
            recipe_tags_json TEXT NOT NULL DEFAULT '[]',
            source_label TEXT NOT NULL,
            license TEXT NOT NULL,
            consent_status TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE pig_instances (
            pig_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL UNIQUE,
            scope_id TEXT NOT NULL,
            owner_player_id TEXT NOT NULL,
            template_id TEXT NOT NULL,
            template_version INTEGER NOT NULL,
            rarity INTEGER NOT NULL,
            display_name_snapshot TEXT NOT NULL,
            size_value REAL NOT NULL,
            size_percentile REAL NOT NULL,
            weight_value REAL NOT NULL,
            weight_percentile REAL NOT NULL,
            fat_ratio REAL NOT NULL,
            official_value INTEGER NOT NULL,
            ruleset_version INTEGER NOT NULL,
            random_snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL,
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE food_templates (
            template_id TEXT PRIMARY KEY,
            catalog_hash TEXT NOT NULL,
            template_version INTEGER NOT NULL DEFAULT 1,
            display_name TEXT NOT NULL,
            rarity INTEGER NOT NULL,
            scope_type TEXT NOT NULL,
            description TEXT NOT NULL,
            image_relpath TEXT NOT NULL,
            image_sha256 TEXT NOT NULL,
            image_fit TEXT NOT NULL,
            recipe_tags_json TEXT NOT NULL DEFAULT '[]',
            effect_id TEXT NOT NULL DEFAULT '',
            effect_params_json TEXT NOT NULL DEFAULT '{}',
            source_label TEXT NOT NULL,
            license TEXT NOT NULL,
            consent_status TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE food_instances (
            food_instance_id TEXT PRIMARY KEY,
            short_code TEXT NOT NULL UNIQUE,
            scope_id TEXT NOT NULL,
            owner_player_id TEXT NOT NULL,
            template_id TEXT NOT NULL,
            template_version INTEGER NOT NULL,
            source_pig_instance_id TEXT,
            rarity INTEGER NOT NULL,
            display_name_snapshot TEXT NOT NULL,
            portion_weight REAL NOT NULL,
            fat_category TEXT NOT NULL,
            official_value INTEGER NOT NULL,
            effect_id TEXT NOT NULL DEFAULT '',
            effect_params_json TEXT NOT NULL DEFAULT '{}',
            ruleset_version INTEGER NOT NULL,
            random_snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL,
            locked_trade_id TEXT,
            acquired_at TEXT NOT NULL,
            disposed_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE currency_ledger (
            ledger_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason_code TEXT NOT NULL,
            reason_text TEXT NOT NULL,
            source_object_type TEXT NOT NULL DEFAULT '',
            source_object_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT UNIQUE,
            created_at TEXT NOT NULL
        );
        INSERT INTO scopes(
            scope_id, platform, group_id, created_at, updated_at
        ) VALUES ('qq-official:group', 'qq-official', 'group', 'now', 'now');
        INSERT INTO players(
            player_id, scope_id, platform_user_id, display_name,
            created_at, updated_at
        ) VALUES (
            'qq-official:group:user', 'qq-official:group', 'user', '成员',
            'now', 'now'
        );
        INSERT INTO player_restrictions(
            restriction_id, player_id, restriction_type, limit_value,
            starts_at, expires_at, reason, source, created_by,
            created_at, updated_at
        ) VALUES (
            'legacy-ban', 'qq-official:group:user', 'social-transfer-ban', NULL,
            '2026-08-05T00:00:00Z', '2026-08-12T00:00:00Z',
            'legacy', 'pytest', 'admin', 'now', 'now'
        );
        PRAGMA user_version = 9;
        """
    )
    connection.commit()
    connection.close()

    database = PigCatcherDatabase(path)
    await database.open()
    assert await database.schema_version() == SCHEMA_VERSION
    rows = await database.fetch_all(
        """
        SELECT restriction_type, expires_at
        FROM player_restrictions
        ORDER BY restriction_type
        """
    )
    assert [(row["restriction_type"], row["expires_at"]) for row in rows] == [
        ("gift-transfer-ban", None),
        ("trade-ban", None),
    ]
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
    assert report.ledger_mismatch_count == 0
    assert report.active_asset_file_count == 0
    assert report.missing_asset_file_count == 0
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
