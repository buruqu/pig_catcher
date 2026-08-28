"""SQLite 迁移、事务、备份、维护和幂等收据。"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
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
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.migrations.v0001_initial import MIGRATION_0001
from pig_catcher.services import (
    FrameworkService,
    MaintenanceOptions,
    MaintenanceReport,
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
        "group_food_effects",
        "group_food_effect_usage",
        "pending_food_confirmations",
        "player_catch_quota_bonuses",
        "player_restrictions",
        "trade_offers",
        "anti_abuse_cases",
        "anti_abuse_case_members",
        "anti_abuse_notices",
        "anti_abuse_holds",
        "anti_abuse_events",
        "player_technique_permits",
        "group_technique_effects",
        "player_technique_progress",
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
    } <= names
    armed_columns = await database.fetch_all("PRAGMA table_info(armed_items)")
    assert "remaining_uses" in {str(row["name"]) for row in armed_columns}
    armed_table = await database.fetch_one(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'armed_items'"
    )
    assert armed_table is not None
    assert "CHECK (remaining_uses > 0)" in str(armed_table["sql"])
    pig_columns = await database.fetch_all("PRAGMA table_info(pig_templates)")
    assert "paired_food_template_id" in {str(row["name"]) for row in pig_columns}
    instance_tables = await database.fetch_all(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'table' AND name IN ('pig_instances', 'food_instances')
        ORDER BY name
        """
    )
    assert len(instance_tables) == 2
    for row in instance_tables:
        table_sql = str(row["sql"])
        assert "short_code TEXT NOT NULL COLLATE NOCASE UNIQUE" in table_sql
        assert "length(short_code) BETWEEN 4 AND 16" in table_sql
        assert "short_code NOT GLOB '*[^0-9A-Za-z]*'" in table_sql
    receipt_columns = await database.fetch_all("PRAGMA table_info(command_receipts)")
    receipt_column_map = {str(row["name"]): row for row in receipt_columns}
    assert int(receipt_column_map["catch_quota_cost"]["notnull"]) == 1
    assert str(receipt_column_map["catch_quota_cost"]["dflt_value"]) == "1"
    await database.close()


@pytest.mark.asyncio
async def test_schema_35_database_migrates_to_weekly_competitions(tmp_path: Path) -> None:
    path = tmp_path / "schema-35.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)")
    for migration in MIGRATIONS:
        if migration.version > 35:
            break
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, "test"))
    connection.execute("PRAGMA user_version = 35")
    connection.commit()
    connection.close()

    migrated = PigCatcherDatabase(path)
    await migrated.open()
    assert await migrated.schema_version() == SCHEMA_VERSION
    tables = await migrated.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'weekly_competition%'"
    )
    assert {str(row["name"]) for row in tables} == {
        "weekly_competitions",
        "weekly_competition_entries",
        "weekly_competition_settlements",
        "weekly_competition_awards",
    }
    assert await migrated.fetch_all("PRAGMA foreign_key_check") == []
    await migrated.close()


@pytest.mark.asyncio
async def test_schema35_marks_only_preexisting_players_for_backfill(tmp_path: Path) -> None:
    database_path = tmp_path / "pre-v35.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """
        CREATE TABLE schema_migrations(
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in (item for item in MIGRATIONS if item.version <= 34):
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (?, ?, '2026-08-26T00:00:00.000Z')
            """,
            (migration.version, migration.name),
        )
        connection.execute(f"PRAGMA user_version={migration.version}")
    connection.execute(
        """
        INSERT INTO scopes(
            scope_id, platform, group_id, group_name, stream_id,
            created_at, updated_at
        ) VALUES (
            'qq:old-group', 'qq', 'old-group', '旧群', 'old-stream',
            '2026-08-25T00:00:00.000Z', '2026-08-25T00:00:00.000Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO players(
            player_id, scope_id, platform_user_id, display_name,
            created_at, updated_at
        ) VALUES (
            'qq:old-group:old-user', 'qq:old-group', 'old-user', '旧玩家',
            '2026-08-25T00:00:00.000Z', '2026-08-25T00:00:00.000Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO player_statistics(player_id, updated_at)
        VALUES ('qq:old-group:old-user', '2026-08-25T00:00:00.000Z')
        """
    )
    connection.execute(
        """
        UPDATE players SET coin_balance=1123
        WHERE player_id='qq:old-group:old-user'
        """
    )
    connection.executemany(
        """
        INSERT INTO currency_ledger(
            ledger_entry_id, player_id, scope_id, amount, balance_after,
            reason_code, reason_text, source_object_type, source_object_id,
            idempotency_key, created_at
        ) VALUES (?, 'qq:old-group:old-user', 'qq:old-group', ?, ?, ?, ?, '', '', ?, ?)
        """,
        (
            (
                "old-normal-income",
                123,
                123,
                "catch-reward",
                "旧抓猪奖励",
                "old-normal-income",
                "2026-08-25T01:00:00.000Z",
            ),
            (
                "old-admin-adjustment",
                1000,
                1123,
                "admin-coin-adjustment",
                "旧管理员调账",
                "old-admin-adjustment",
                "2026-08-25T02:00:00.000Z",
            ),
        ),
    )
    connection.commit()
    connection.close()

    database = PigCatcherDatabase(database_path)
    await database.open()
    old_state = await database.fetch_one(
        "SELECT status FROM achievement_backfill_state WHERE player_id='qq:old-group:old-user'"
    )
    assert old_state is not None and old_state["status"] == "pending"
    old_metrics = await database.fetch_all(
        """
        SELECT metric_key, metric_value FROM achievement_metric_counters
        WHERE player_id='qq:old-group:old-user' ORDER BY metric_key
        """
    )
    assert [(str(row["metric_key"]), int(row["metric_value"])) for row in old_metrics] == [
        ("admin_coin_adjustment_net", 1000),
        ("ordinary_coins_earned", 123),
    ]
    await FrameworkService(database).touch_identity(
        CommandIdentity(
            scope=ScopeKey("qq", "new-group"),
            stream_id="new-stream",
            user_id="new-user",
            display_name="新玩家",
        )
    )
    new_state = await database.fetch_one(
        "SELECT status FROM achievement_backfill_state WHERE player_id='qq:new-group:new-user'"
    )
    assert new_state is None
    await database.close()


@pytest.mark.asyncio
async def test_v30_catch_usage_covering_index_exists_and_is_used(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()

    indexes = await database.fetch_all("PRAGMA index_list(command_receipts)")
    index_names = {str(row["name"]) for row in indexes}
    index_name = "idx_command_receipts_player_command_created_quota"
    assert index_name in index_names

    columns = await database.fetch_all(f"PRAGMA index_info({index_name})")
    assert [str(row["name"]) for row in columns] == [
        "player_id",
        "command_name",
        "created_at",
        "catch_quota_cost",
    ]

    plan = await database.fetch_all(
        """
        EXPLAIN QUERY PLAN
        WITH player_scope AS (
            SELECT scope_id
            FROM players
            WHERE player_id = ?
        ),
        effective_window AS (
            SELECT COALESCE(MAX(reset.created_at), ?) AS effective_start
            FROM audit_events AS reset
            WHERE (
                (
                    reset.action IN (
                        'daily-catch-quota-reset',
                        'catch-quota-window-reset',
                        'catch-quota-window-boost'
                    )
                    AND reset.created_at >= ?
                    AND reset.created_at < ?
                    AND (
                        reset.scope_id IS NULL
                        OR reset.scope_id = (SELECT scope_id FROM player_scope)
                    )
                )
                OR (
                    reset.action = 'player-catch-quota-window-reset'
                    AND reset.scope_id = (SELECT scope_id FROM player_scope)
                    AND reset.object_id = ?
                    AND reset.created_at >= ?
                    AND reset.created_at < ?
                )
            )
        )
        SELECT
            COALESCE(SUM(receipt.catch_quota_cost), 0) AS daily_count,
            COUNT(*) AS total_count,
            MAX(receipt.created_at) AS last_acquired_at
        FROM command_receipts AS receipt
        CROSS JOIN effective_window
        WHERE receipt.player_id = ?
          AND receipt.command_name = 'pig-catcher.catch'
          AND receipt.created_at >= effective_window.effective_start
          AND receipt.created_at < ?
        """,
        (
            "player-1",
            "2026-08-24T00:00:00.000Z",
            "2026-08-24T00:00:00.000Z",
            "2026-08-25T00:00:00.000Z",
            "player-1",
            "2026-08-24T00:00:00.000Z",
            "2026-08-25T00:00:00.000Z",
            "player-1",
            "2026-08-25T00:00:00.000Z",
        ),
    )
    plan_details = "\n".join(str(row["detail"]) for row in plan)
    assert f"USING COVERING INDEX {index_name}" in plan_details

    await database.close()


@pytest.mark.asyncio
async def test_v24_armed_item_queue_migrates_to_positive_only_rows(tmp_path: Path) -> None:
    path = tmp_path / "v24-armed-items.sqlite3"
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
    for migration in (item for item in MIGRATIONS if item.version <= 24):
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, 'now')",
            (migration.version, migration.name),
        )
        connection.execute(f"PRAGMA user_version = {migration.version}")
    now = "2026-08-13T12:00:00.000Z"
    connection.execute(
        """
        INSERT INTO scopes(
            scope_id, platform, group_id, group_name, stream_id, created_at, updated_at
        ) VALUES ('qq:100', 'qq', '100', '测试群', 'stream-100', ?, ?)
        """,
        (now, now),
    )
    for player_id, platform_user_id in (("qq:100:200", "200"), ("qq:100:201", "201")):
        connection.execute(
            """
            INSERT INTO players(
                player_id, scope_id, platform_user_id, display_name, created_at, updated_at
            ) VALUES (?, 'qq:100', ?, '测试成员', ?, ?)
            """,
            (player_id, platform_user_id, now, now),
        )
    connection.execute(
        """
        INSERT INTO armed_items(player_id, action_type, item_id, armed_at, remaining_uses)
        VALUES ('qq:100:200', 'catching', 'giant-corn', ?, 1)
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO armed_items(player_id, action_type, item_id, armed_at, remaining_uses)
        VALUES ('qq:100:201', 'cooking', 'precision-knife', ?, 0)
        """,
        (now,),
    )
    connection.commit()
    connection.close()

    database = PigCatcherDatabase(path)
    await database.open()
    assert await database.schema_version() == SCHEMA_VERSION
    rows = await database.fetch_all("SELECT player_id, remaining_uses FROM armed_items ORDER BY player_id")
    assert [(str(row["player_id"]), int(row["remaining_uses"])) for row in rows] == [("qq:100:200", 1)]
    table = await database.fetch_one("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'armed_items'")
    assert table is not None
    assert "CHECK (remaining_uses > 0)" in str(table["sql"])
    assert await database.integrity_check() == ("ok",)
    await database.close()


@pytest.mark.asyncio
async def test_v23_migrates_food_effects_and_repairs_intermediate_pig_cookie(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v17-food-effects.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in (item for item in MIGRATIONS if item.version <= 17):
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, 'now')",
            (migration.version, migration.name),
        )
        connection.execute(f"PRAGMA user_version = {migration.version}")

    now = "2026-08-10T07:29:16.657Z"
    connection.execute(
        """
        INSERT INTO scopes(
            scope_id, platform, group_id, group_name, stream_id,
            created_at, updated_at
        ) VALUES ('qq:100', 'qq', '100', '测试群', 'stream-100', ?, ?)
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO players(
            player_id, scope_id, platform_user_id, display_name,
            created_at, updated_at
        ) VALUES ('qq:100:200', 'qq:100', '200', '测试成员', ?, ?)
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO asset_manifest_imports(
            catalog_hash, catalog_id, manifest_version, source_label,
            storage_relpath, entry_count, status, created_at
        ) VALUES ('catalog', 'pytest', 4, 'pytest', 'pytest', 8, 'active', ?)
        """,
        (now,),
    )
    old_effects = {
        "糖醋排骨": ("exclusive-catch-quality", '{"multiplier":3.0}', 1),
        "猪鼻蛋包饭": ("next-six-star-cook", '{"six_star_percent":60}', 1),
        "小马猪蒙布朗": ("next-six-star-catch", '{"six_star_percent":60}', 1),
        "雾蓝键盘大福": (
            "next-high-star-catch",
            '{"five_star_percent":30,"four_star_percent":60,"six_star_percent":10,"uses":5}',
            5,
        ),
        "彩彩修车猪慕斯": ("next-five-star-cook", '{"uses":5}', 5),
        "猪保千猪排轮盘": ("even-catch-distribution", '{"uses":5}', 5),
        "一猪六吃": ("next-six-star-cook", '{"six_star_percent":50}', 1),
        "猪利猪": ("next-pig-rarity", '{"multiplier":2.4,"rarity":4}', 1),
        "猪籽军舰": ("next-food-rarity", '{"multiplier":2.4,"rarity":4}', 1),
        "猪猪玉子烧": ("next-cook-quality", '{"shift_percent":10,"uses":1}', 1),
        "猪饺": ("next-food-rarity", '{"multiplier":4.0,"rarity":3}', 1),
        "黑猪麻汤圆": ("next-pig-stature", '{"mode":"giant","strength":0.5}', 1),
        "猪猪白菜炖粉条": ("next-cook-quality", '{"shift_percent":24,"uses":1}', 1),
        "猪咪莓蛋糕": ("next-cook-quality", '{"shift_percent":18,"uses":2}', 2),
        "猪果冻": ("next-catch-quality", '{"multiplier":2.2,"uses":2}', 2),
        "猪皮奶": ("next-pig-rarity", '{"multiplier":6.0,"rarity":5}', 1),
    }
    for index, (name, (effect_id, params, granted_uses)) in enumerate(
        old_effects.items(),
        start=1,
    ):
        template_id = f"food-template-{index}"
        instance_id = f"food-instance-{index}"
        connection.execute(
            """
            INSERT INTO food_templates(
                template_id, catalog_hash, display_name, rarity, scope_type,
                description, image_relpath, image_sha256, image_fit,
                effect_id, effect_params_json, source_label, license,
                consent_status, created_at, updated_at
            ) VALUES (?, 'catalog', ?, 6, 'group', '测试', ?, 'hash',
                      'contain', ?, ?, 'pytest', 'test-only', 'granted', ?, ?)
            """,
            (template_id, name, f"{name}.png", effect_id, params, now, now),
        )
        connection.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                portion_weight, fat_category, official_value, effect_id,
                effect_params_json, ruleset_version, random_snapshot_json,
                state, acquired_at, updated_at
            ) VALUES (?, ?, 'qq:100', 'qq:100:200', ?, 1, 6, ?, 1.0,
                      'balanced', 25000, ?, ?, 15, '{}', 'active', ?, ?)
            """,
            (instance_id, f"F{index:07d}", template_id, name, effect_id, params, now, now),
        )
        connection.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                created_at, updated_at
            ) VALUES (?, 'qq:100:200', ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                f"effect-{index}",
                instance_id,
                effect_id,
                params,
                granted_uses,
                now,
                now,
            ),
        )
    chocolate_consumed_at = "2026-08-10T00:25:16.908Z"
    connection.execute(
        """
        INSERT INTO food_templates(
            template_id, catalog_hash, display_name, rarity, scope_type,
            description, image_relpath, image_sha256, image_fit,
            effect_id, effect_params_json, source_label, license,
            consent_status, created_at, updated_at
        ) VALUES (
            'chocolate-template', 'catalog', '向你道早猪猪巧克力螺', 6,
            'group', '测试', '巧克力螺.png', 'hash', 'contain',
            'weekly-window-catches', '{"count":5}', 'pytest', 'test-only',
            'granted', ?, ?
        )
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO food_instances(
            food_instance_id, short_code, scope_id, owner_player_id,
            template_id, template_version, rarity, display_name_snapshot,
            portion_weight, fat_category, official_value, effect_id,
            effect_params_json, ruleset_version, random_snapshot_json,
            state, acquired_at, disposed_at, updated_at
        ) VALUES (
            'chocolate-cornet-source', 'CHOC0001', 'qq:100', 'qq:100:200',
            'chocolate-template', 1, 6, '向你道早猪猪巧克力螺', 1.0,
            'balanced', 25000, 'weekly-window-catches', '{"count":5}',
            15, '{}', 'consumed', ?, ?, ?
        )
        """,
        (chocolate_consumed_at, chocolate_consumed_at, now),
    )
    connection.execute(
        """
        INSERT INTO player_catch_quota_bonuses(
            player_id, weekly_bonus, weekly_expires_at,
            weekly_source_food_instance_id, created_at, updated_at
        ) VALUES (
            'qq:100:200', 5, '2026-08-16T16:00:00.000Z',
            'chocolate-cornet-source', ?, ?
        )
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO command_receipts(
            receipt_id, idempotency_key, scope_id, player_id, command_name,
            request_fingerprint, result_type, text_summary, created_at, updated_at
        ) VALUES (
            'old-receipt', 'old-idempotency', 'qq:100', 'qq:100:200',
            'pig-catcher.catch', 'fingerprint', 'pig', '旧抓猪收据', ?, ?
        )
        """,
        (now, now),
    )

    # 先按正式路径执行 18-20，再模拟生产热加载曾短暂写入的猪利猪早期方案。
    # Schema 21 必须把模板、可用实例和未消费队列全部收敛到最终 +1 个百分点规则。
    for migration in (item for item in MIGRATIONS if 18 <= item.version <= 20):
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, 'now')",
            (migration.version, migration.name),
        )
        connection.execute(f"PRAGMA user_version = {migration.version}")
    connection.execute(
        """
        UPDATE food_templates
        SET effect_id = 'next-pig-rarity',
            effect_params_json = '{"multiplier":2.0,"rarity":6}'
        WHERE display_name = '猪利猪'
        """
    )
    connection.execute(
        """
        UPDATE food_instances
        SET effect_id = 'next-pig-rarity',
            effect_params_json = '{"multiplier":2.0,"rarity":6}'
        WHERE display_name_snapshot = '猪利猪'
        """
    )
    connection.execute(
        """
        UPDATE player_food_effects
        SET effect_id = 'next-pig-rarity',
            params_json = '{"multiplier":2.0,"rarity":6}'
        WHERE source_food_instance_id IN (
            SELECT food_instance_id
            FROM food_instances
            WHERE display_name_snapshot = '猪利猪'
        )
        """
    )
    for migration in (item for item in MIGRATIONS if 21 <= item.version <= 22):
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, 'now')",
            (migration.version, migration.name),
        )
        connection.execute(f"PRAGMA user_version = {migration.version}")
    connection.execute(
        """
        INSERT INTO group_food_effects(
            group_effect_entry_id, scope_id, source_player_id,
            source_food_instance_id, effect_id, params_json,
            granted_uses_per_player, starts_at, expires_at,
            created_at, updated_at
        ) VALUES (
            'active-ribs-group-effect', 'qq:100', 'qq:100:200',
            'food-instance-1', 'group-window-high-star-boost',
            '{"coin_per_player":1007,"dedicated_catches":10,"five_star_multiplier":1.007,"six_star_multiplier":1.007,"source_label":"糖醋排骨"}',
            10, '2026-08-10T07:29:16.657Z', '2099-08-11T07:29:16.657Z',
            ?, ?
        )
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO group_food_effects(
            group_effect_entry_id, scope_id, source_player_id,
            source_food_instance_id, effect_id, params_json,
            granted_uses_per_player, starts_at, expires_at,
            created_at, updated_at
        ) VALUES (
            'active-omelette-group-effect', 'qq:100', 'qq:100:200',
            'food-instance-2', 'group-window-high-star-boost',
            '{"coin_per_player":1004,"dedicated_catches":0,"five_star_multiplier":1.004,"six_star_multiplier":1.004,"source_label":"猪鼻蛋包饭"}',
            0, '2026-08-10T07:29:16.657Z', '2099-08-11T07:29:16.657Z',
            ?, ?
        )
        """,
        (now, now),
    )
    connection.commit()
    connection.close()

    database = PigCatcherDatabase(path)
    await database.open()
    assert await database.schema_version() == SCHEMA_VERSION
    templates = await database.fetch_all(
        "SELECT display_name, effect_id, effect_params_json FROM food_templates ORDER BY display_name"
    )
    migrated = {
        str(row["display_name"]): (
            str(row["effect_id"]),
            str(row["effect_params_json"]),
        )
        for row in templates
    }
    assert migrated["糖醋排骨"] == (
        "quota-reset",
        '{"count":1,"five_star_multiplier":1.007,"group_coin":1007,'
        '"group_dedicated_catches":10,"hidden_boost_chance_percent":10,'
        '"hidden_five_star_multiplier":10.04,"hidden_six_star_multiplier":10.04,'
        '"six_star_multiplier":1.007}',
    )
    assert migrated["猪鼻蛋包饭"] == (
        "group-window-high-star-boost",
        '{"coin_per_player":1004,"dedicated_catches":1,"dedicated_only":true,'
        '"five_star_multiplier":1.004,"personal_six_star_cook_percent":60,'
        '"personal_six_star_cook_uses":2,"six_star_multiplier":1.004,'
        '"source_label":"猪鼻蛋包饭"}',
    )
    assert migrated["一猪六吃"] == (
        "next-six-star-cook-bonus",
        '{"bonus_percent":15}',
    )
    effects = await database.fetch_all(
        """
        SELECT instance.display_name_snapshot, effect.effect_id,
               effect.params_json, effect.granted_uses
        FROM player_food_effects AS effect
        JOIN food_instances AS instance
          ON instance.food_instance_id = effect.source_food_instance_id
        ORDER BY instance.display_name_snapshot
        """
    )
    active = {
        str(row["display_name_snapshot"]): (
            str(row["effect_id"]),
            str(row["params_json"]),
            int(row["granted_uses"]),
        )
        for row in effects
    }
    assert active["糖醋排骨"] == (
        "quota-reset",
        '{"count":1,"five_star_multiplier":1.007,"group_coin":1007,'
        '"group_dedicated_catches":10,"hidden_boost_chance_percent":10,'
        '"hidden_five_star_multiplier":10.04,"hidden_six_star_multiplier":10.04,'
        '"six_star_multiplier":1.007}',
        1,
    )
    assert active["猪鼻蛋包饭"][2] == 2
    assert active["小马猪蒙布朗"][2] == 5
    assert active["雾蓝键盘大福"] == (
        "next-high-star-catch",
        '{"current_window_only":true,"five_star_percent":30.7692,'
        '"four_star_percent":61.5385,"six_star_percent":7.6923,"uses":5}',
        5,
    )
    assert active["彩彩修车猪慕斯"] == (
        "six-star-cook-failure-return",
        '{"return_chance_percent":75,"uses":3}',
        3,
    )
    assert active["猪保千猪排轮盘"] == (
        "even-catch-distribution",
        '{"last_use_six_star_percent":50,"uses":10}',
        10,
    )
    assert active["一猪六吃"] == (
        "next-six-star-cook-bonus",
        '{"bonus_percent":15}',
        1,
    )
    assert active["猪利猪"] == (
        "next-five-six-star-catch",
        '{"five_star_bonus_percent":5,"six_star_bonus_percent":3}',
        1,
    )
    assert active["猪籽军舰"] == (
        "next-food-rarity",
        '{"multiplier":2.0,"rarity":5}',
        1,
    )
    assert active["猪猪玉子烧"] == (
        "next-cook-quality",
        '{"shift_percent":15,"uses":1}',
        1,
    )
    assert active["猪饺"] == (
        "next-stackable-six-star-cook-bonus",
        '{"bonus_percent":1,"max_stacks":5}',
        1,
    )
    assert active["黑猪麻汤圆"] == (
        "next-giant-five-star-catch",
        '{"five_star_multiplier":3.0,"giant_template_multiplier":4.0,"stature_bias":0.5}',
        1,
    )
    assert active["猪猪白菜炖粉条"] == (
        "next-collaboration-catch",
        '{"five_star_percent":30,"four_star_percent":55,"three_star_percent":15}',
        1,
    )
    assert active["猪咪莓蛋糕"] == (
        "next-extreme-five-star-cook",
        '{"five_star_percent":85}',
        1,
    )
    assert active["猪果冻"] == (
        "next-catch-quality",
        '{"multiplier":3.0,"uses":3}',
        3,
    )
    assert active["猪皮奶"] == (
        "next-small-six-star-catch",
        '{"bonus_percent":15}',
        1,
    )
    roulette = await database.fetch_one(
        """
        SELECT state.available_spins, effect.consumed_uses,
               effect.granted_uses
        FROM player_roulette_state AS state
        JOIN player_food_effects AS effect
          ON effect.source_food_instance_id = state.source_food_instance_id
        WHERE state.player_id = 'qq:100:200'
        """
    )
    assert roulette is not None
    assert int(roulette["available_spins"]) == 3
    assert int(roulette["consumed_uses"]) == int(roulette["granted_uses"])
    group_effect_rows = await database.fetch_all(
        """
        SELECT group_effect_entry_id, params_json, granted_uses_per_player
        FROM group_food_effects
        ORDER BY group_effect_entry_id
        """
    )
    group_effects = {
        str(row["group_effect_entry_id"]): (
            str(row["params_json"]),
            int(row["granted_uses_per_player"]),
        )
        for row in group_effect_rows
    }
    assert group_effects["active-ribs-group-effect"] == (
        '{"coin_per_player":1007,"dedicated_catches":10,'
        '"five_star_multiplier":1.007,"hidden_boost_chance_percent":10,'
        '"hidden_five_star_multiplier":10.04,"hidden_six_star_multiplier":10.04,'
        '"six_star_multiplier":1.007,"source_label":"糖醋排骨"}',
        10,
    )
    assert group_effects["active-omelette-group-effect"] == (
        '{"coin_per_player":1004,"dedicated_catches":1,"dedicated_only":true,'
        '"five_star_multiplier":1.004,"personal_six_star_cook_percent":60,'
        '"personal_six_star_cook_uses":2,"six_star_multiplier":1.004,'
        '"source_label":"猪鼻蛋包饭"}',
        1,
    )
    quota = await database.fetch_one(
        "SELECT weekly_expires_at FROM player_catch_quota_bonuses WHERE player_id = 'qq:100:200'"
    )
    receipt = await database.fetch_one("SELECT catch_quota_cost FROM command_receipts WHERE receipt_id = 'old-receipt'")
    assert quota is not None and quota["weekly_expires_at"] == "2026-08-17T01:00:00.000Z"
    assert receipt is not None and receipt["catch_quota_cost"] == 1
    assert await database.integrity_check() == ("ok",)
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
        CREATE TABLE command_receipts (
            receipt_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            scope_id TEXT NOT NULL,
            player_id TEXT,
            command_name TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            result_type TEXT NOT NULL,
            result_object_id TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            text_summary TEXT NOT NULL,
            business_status TEXT NOT NULL DEFAULT 'committed',
            send_status TEXT NOT NULL DEFAULT 'pending',
            send_error TEXT NOT NULL DEFAULT '',
            claimed_at TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE player_food_effects (
            effect_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL,
            source_food_instance_id TEXT NOT NULL UNIQUE,
            effect_id TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            granted_uses INTEGER NOT NULL,
            consumed_uses INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE player_catch_quota_bonuses (
            player_id TEXT PRIMARY KEY,
            permanent_bonus INTEGER NOT NULL DEFAULT 0,
            weekly_bonus INTEGER NOT NULL DEFAULT 0,
            weekly_expires_at TEXT,
            weekly_source_food_instance_id TEXT,
            permanent_source_food_instance_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
async def test_released_v33_source_unique_constraint_is_repaired(tmp_path: Path) -> None:
    """Some live v33 databases were stamped before the table rebuild was shipped."""

    path = tmp_path / "released-v33.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (33, 'food-roulette-rebalance', '2026-08-24T17:46:47.946Z');
            CREATE TABLE players(player_id TEXT PRIMARY KEY);
            CREATE TABLE currency_ledger(
                ledger_entry_id TEXT PRIMARY KEY,
                player_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason_code TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
                CREATE TABLE food_instances(food_instance_id TEXT PRIMARY KEY);
                CREATE TABLE scopes(scope_id TEXT PRIMARY KEY);
                CREATE TABLE pig_instances(
                    pig_instance_id TEXT PRIMARY KEY,owner_player_id TEXT,scope_id TEXT,state TEXT,locked_trade_id TEXT
                );
        -- A released v33 database already has its template table; Schema 41 adds its display-only column.
        CREATE TABLE pig_templates(template_id TEXT PRIMARY KEY);
        INSERT INTO players(player_id) VALUES ('qq:100:200');
        INSERT INTO food_instances(food_instance_id) VALUES ('roulette-source');
        CREATE TABLE player_roulette_state(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            available_spins INTEGER NOT NULL DEFAULT 0 CHECK (available_spins >= 0),
            source_food_instance_id TEXT NOT NULL REFERENCES food_instances(food_instance_id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE player_food_effects (
            effect_entry_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            source_food_instance_id TEXT NOT NULL UNIQUE
                REFERENCES food_instances(food_instance_id),
            effect_id TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            granted_uses INTEGER NOT NULL CHECK (granted_uses >= 1),
            consumed_uses INTEGER NOT NULL DEFAULT 0 CHECK (
                consumed_uses >= 0 AND consumed_uses <= granted_uses
            ),
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_player_food_effects_active
        ON player_food_effects(
            player_id, effect_id, consumed_uses, expires_at, created_at
        );
        INSERT INTO player_food_effects(
            effect_entry_id, player_id, source_food_instance_id,
            effect_id, granted_uses, created_at, updated_at
        ) VALUES (
            'existing-effect', 'qq:100:200', 'roulette-source',
            'next-six-star-cook-bonus', 1, 'now', 'now'
        );
        PRAGMA user_version = 33;
        """
    )
    connection.commit()
    connection.close()

    database = PigCatcherDatabase(path)
    await database.open()
    assert await database.schema_version() == SCHEMA_VERSION
    table = await database.fetch_one("SELECT sql FROM sqlite_master WHERE type='table' AND name='player_food_effects'")
    assert table is not None
    assert "source_food_instance_id TEXT NOT NULL UNIQUE" not in str(table["sql"])
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, granted_uses, created_at, updated_at
            ) VALUES (
                'second-effect', 'qq:100:200', 'roulette-source',
                'next-guaranteed-six-star-catch', 1, 'now', 'now'
            )
            """
        )
    rows = await database.fetch_all("SELECT effect_id FROM player_food_effects ORDER BY effect_entry_id")
    assert [str(row["effect_id"]) for row in rows] == [
        "next-six-star-cook-bonus",
        "next-guaranteed-six-star-catch",
    ]
    assert await database.integrity_check() == ("ok",)
    assert await database.fetch_all("PRAGMA foreign_key_check") == []
    await database.close()


@pytest.mark.asyncio
async def test_current_schema_rejects_reintroduced_source_unique_index(tmp_path: Path) -> None:
    path = tmp_path / "malformed-current.sqlite3"
    database = PigCatcherDatabase(path)
    await database.open()
    await database.close()

    connection = sqlite3.connect(path)
    connection.execute("CREATE UNIQUE INDEX broken_source_unique ON player_food_effects(source_food_instance_id)")
    connection.commit()
    connection.close()

    malformed = PigCatcherDatabase(path)
    with pytest.raises(MigrationError, match="仍带 UNIQUE 约束"):
        await malformed.open()


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
    backup = tmp_path / "backups" / "copy.sqlite3"
    backup.parent.mkdir()
    backup.write_bytes(b"previous-backup")
    backup = await database.backup_to(backup)
    connection = sqlite3.connect(backup)
    assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    connection.close()
    assert not list(backup.parent.glob(f".{backup.name}.*.tmp"))
    await database.close()


@pytest.mark.asyncio
async def test_online_backup_rejects_live_database_as_destination(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()

    with pytest.raises(DatabaseError, match="主数据库"):
        await database.backup_to(database.path)

    assert await database.integrity_check() == ("ok",)
    await database.close()


@pytest.mark.asyncio
async def test_backup_failure_preserves_destination_and_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    destination = tmp_path / "backups" / "copy.sqlite3"
    destination.parent.mkdir()
    destination.write_bytes(b"known-good-previous-backup")

    async def reject_backup(_connection: object) -> None:
        raise DatabaseError("injected quick-check failure")

    monkeypatch.setattr(database, "_verify_backup", reject_backup)
    with pytest.raises(DatabaseError, match="injected quick-check failure"):
        await database.backup_to(destination)

    assert destination.read_bytes() == b"known-good-previous-backup"
    assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))

    destination.unlink()
    with pytest.raises(DatabaseError, match="injected quick-check failure"):
        await database.backup_to(destination)
    assert destination.exists() is False
    assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))
    await database.close()


@pytest.mark.asyncio
async def test_backup_can_finish_while_ordinary_transaction_is_active(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    destination = tmp_path / "backups" / "copy.sqlite3"

    async with database.transaction(immediate=False):
        backup = await asyncio.wait_for(database.backup_to(destination), timeout=2)

    assert backup == destination
    assert backup.is_file()
    await database.close()


@pytest.mark.asyncio
async def test_read_only_transactions_are_bounded_concurrent_and_query_only(
    tmp_path: Path,
) -> None:
    database = PigCatcherDatabase(
        tmp_path / "pig.sqlite3",
        max_concurrent_reads=2,
    )
    await database.open()
    async with database.transaction() as session:
        await session.execute("CREATE TABLE read_guard(value INTEGER NOT NULL)")

    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_reader(entered: asyncio.Event) -> None:
        async with database.transaction(immediate=False) as session:
            assert await session.fetch_one("SELECT 1") is not None
            entered.set()
            await release.wait()

    first = asyncio.create_task(hold_reader(first_entered))
    second = asyncio.create_task(hold_reader(second_entered))
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    await asyncio.wait_for(second_entered.wait(), timeout=1)

    blocked_third = asyncio.create_task(database.fetch_one("SELECT 2"))
    await asyncio.sleep(0.02)
    assert blocked_third.done() is False

    release.set()
    await first
    await second
    third_row = await asyncio.wait_for(blocked_third, timeout=1)
    assert third_row is not None and int(third_row[0]) == 2

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        async with database.transaction(immediate=False) as session:
            await session.execute("INSERT INTO read_guard(value) VALUES (1)")

    await database.close()


@pytest.mark.asyncio
async def test_close_waits_for_active_read_transaction(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_reader() -> None:
        async with database.transaction(immediate=False) as session:
            assert await session.fetch_one("SELECT 1") is not None
            entered.set()
            await release.wait()

    reader = asyncio.create_task(hold_reader())
    await entered.wait()
    close_task = asyncio.create_task(database.close())
    await asyncio.sleep(0)
    assert database.is_open is False
    assert close_task.done() is False

    release.set()
    await reader
    await asyncio.wait_for(close_task, timeout=1)


@pytest.mark.asyncio
async def test_close_waits_for_operation_then_backup_lock(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    await database._operation_lock.acquire()
    close_task = asyncio.create_task(database.close())
    await asyncio.sleep(0)
    assert database.is_open is False
    assert close_task.done() is False

    await asyncio.wait_for(database._backup_lock.acquire(), timeout=1)
    database._operation_lock.release()
    await asyncio.sleep(0)
    assert close_task.done() is False

    database._backup_lock.release()
    await asyncio.wait_for(close_task, timeout=1)
    assert database.is_open is False


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
    old_backups: list[Path] = []
    for index, name in enumerate(
        (
            "pig_catcher-pre-admin.sqlite3",
            "pig-catcher-quota-reset.sqlite3",
            "pig-catcher-old.sqlite3",
        )
    ):
        path = backups / name
        if index == 2:
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE pig_templates(image_relpath TEXT NOT NULL)")
            connection.execute("CREATE TABLE food_templates(image_relpath TEXT NOT NULL)")
            connection.commit()
            connection.close()
        else:
            path.write_bytes(b"old")
        os.utime(path, (index + 1, index + 1))
        old_backups.append(path)
    old_backups[0].with_name(f"{old_backups[0].name}-wal").write_bytes(b"wal")
    old_backups[0].with_name(f"{old_backups[0].name}-shm").write_bytes(b"shm")
    orphan_sidecar = backups / "already-removed.sqlite3-shm"
    orphan_sidecar.write_bytes(b"orphan")

    runner = MaintenanceRunner(
        database,
        storage,
        tmp_path,
        MaintenanceOptions(60, True, True, 1, 2, 1),
        logger=logging.getLogger("test.maintenance"),
    )
    report = await runner.run_once()
    assert report.integrity_results == ("ok",)
    assert report.integrity_check_performed is True
    assert report.ledger_reconciliation_performed is True
    assert report.removed_staging_directories == 1
    assert report.backup_path is not None
    assert report.backup_path.is_file()
    assert report.removed_backups == 2
    assert report.removed_backup_bytes == 18
    assert report.catalog_cleanup_skipped is False
    assert report.ledger_mismatch_count == 0
    assert report.active_asset_file_count == 0
    assert report.missing_asset_file_count == 0
    assert len(list(backups.glob("*.sqlite3"))) == 2
    assert not old_backups[0].with_name(f"{old_backups[0].name}-wal").exists()
    assert not old_backups[0].with_name(f"{old_backups[0].name}-shm").exists()
    assert not orphan_sidecar.exists()
    await database.close()


@pytest.mark.asyncio
async def test_maintenance_throttles_integrity_and_full_ledger_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MutableClock:
        def __init__(self) -> None:
            self.value = datetime(2026, 8, 24, tzinfo=UTC)

        def now(self) -> datetime:
            return self.value

    class CountingOperationsRepository:
        def __init__(self) -> None:
            self.balance_calls = 0

        async def balance_mismatch_count(self, _session: object) -> int:
            self.balance_calls += 1
            return 0

        async def active_asset_paths(self, _session: object) -> tuple[str, ...]:
            return ()

    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    clock = MutableClock()
    operations = CountingOperationsRepository()
    integrity_calls = 0
    original_integrity_check = database.integrity_check

    async def counting_integrity_check() -> tuple[str, ...]:
        nonlocal integrity_calls
        integrity_calls += 1
        return await original_integrity_check()

    monkeypatch.setattr(database, "integrity_check", counting_integrity_check)
    runner = MaintenanceRunner(
        database,
        AssetCatalogStorage(tmp_path),
        tmp_path,
        MaintenanceOptions(60, True, False, 24, 7, 24),
        logger=logging.getLogger("test.maintenance.throttle"),
        clock=clock,
        operations_repository=operations,  # type: ignore[arg-type]
    )

    first = await runner.run_once()
    clock.value += timedelta(hours=23)
    second = await runner.run_once()
    clock.value += timedelta(hours=1)
    third = await runner.run_once()

    assert first.integrity_check_performed is True
    assert first.ledger_reconciliation_performed is True
    assert second.integrity_check_performed is False
    assert second.ledger_reconciliation_performed is False
    assert third.integrity_check_performed is True
    assert third.ledger_reconciliation_performed is True
    assert integrity_calls == 2
    assert operations.balance_calls == 2
    await database.close()


@pytest.mark.asyncio
async def test_maintenance_prunes_excess_backups_before_due_backup_can_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    backups = tmp_path / "backups"
    backups.mkdir()
    for index in range(5):
        path = backups / f"pig-catcher-old-{index}.sqlite3"
        path.write_bytes(b"old")
        os.utime(path, (index + 1, index + 1))
    runner = MaintenanceRunner(
        database,
        AssetCatalogStorage(tmp_path),
        tmp_path,
        MaintenanceOptions(60, True, True, 1, 2, 24),
        logger=logging.getLogger("test.maintenance.backup-failure-prune"),
    )

    async def fail_backup(_now: datetime) -> Path:
        raise DatabaseError("injected disk-full failure")

    monkeypatch.setattr(runner, "_create_backup", fail_backup)
    with pytest.raises(DatabaseError, match="disk-full"):
        await runner.run_once()

    remaining = list(backups.glob("*.sqlite3"))
    assert len(remaining) == 1
    assert remaining[0].name == "pig-catcher-old-4.sqlite3"
    await database.close()


@pytest.mark.asyncio
async def test_maintenance_start_waits_for_initial_delay(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    storage = AssetCatalogStorage(tmp_path)
    runner = MaintenanceRunner(
        database,
        storage,
        tmp_path,
        MaintenanceOptions(
            60,
            True,
            False,
            24,
            7,
            24,
            initial_delay_seconds=60,
        ),
        logger=logging.getLogger("test.maintenance.initial-delay"),
    )
    calls = 0

    async def count_run_once() -> MaintenanceReport:
        nonlocal calls
        calls += 1
        raise AssertionError("首次维护延迟结束前不应执行")

    runner.run_once = count_run_once  # type: ignore[method-assign]
    runner.start()
    await asyncio.sleep(0)
    assert calls == 0
    await runner.stop()
    assert calls == 0


@pytest.mark.asyncio
async def test_asset_catalog_cleanup_keeps_references_and_latest_rollback(
    tmp_path: Path,
) -> None:
    storage = AssetCatalogStorage(tmp_path)
    storage.ensure_layout()
    hashes = tuple(character * 64 for character in "abcd")
    for index, catalog_hash in enumerate(hashes, start=1):
        catalog = storage.catalogs_root / catalog_hash
        catalog.mkdir()
        (catalog / "payload.bin").write_bytes(b"x" * index)
        os.utime(catalog, (index, index))
    invalid = storage.catalogs_root / "not-a-catalog"
    invalid.mkdir()
    recent_hash = "e" * 64
    recent = storage.catalogs_root / recent_hash
    recent.mkdir()
    (recent / "payload.bin").write_bytes(b"recent")

    removed_count, removed_bytes = await storage.cleanup_catalogs(
        [f"assets\\catalogs\\{hashes[0]}\\files\\pig.png"],
        retain_unreferenced=1,
    )

    assert removed_count == 2
    assert removed_bytes == 5
    assert (storage.catalogs_root / hashes[0]).is_dir()
    assert (storage.catalogs_root / hashes[3]).is_dir()
    assert not (storage.catalogs_root / hashes[1]).exists()
    assert not (storage.catalogs_root / hashes[2]).exists()
    assert recent.is_dir()
    assert invalid.is_dir()

    # 即使不保留额外回退版，刚发布的目录也必须度过保护期后才可删除。
    removed_count, removed_bytes = await storage.cleanup_catalogs(
        [],
        retain_unreferenced=0,
        minimum_age_hours=24,
    )
    assert removed_count == 2
    assert removed_bytes == 5
    assert recent.is_dir()


def test_maintenance_reads_asset_references_from_retained_backup(tmp_path: Path) -> None:
    backup = tmp_path / "backups" / "pig_catcher-pre-test.sqlite3"
    backup.parent.mkdir()
    connection = sqlite3.connect(backup)
    connection.execute("CREATE TABLE pig_templates(image_relpath TEXT NOT NULL)")
    connection.execute("CREATE TABLE food_templates(image_relpath TEXT NOT NULL)")
    expected = f"assets/catalogs/{'e' * 64}/files/pig.png"
    connection.execute("INSERT INTO pig_templates(image_relpath) VALUES (?)", (expected,))
    connection.commit()
    connection.close()

    runner = MaintenanceRunner(
        PigCatcherDatabase(tmp_path / "unused.sqlite3"),
        AssetCatalogStorage(tmp_path),
        tmp_path,
        MaintenanceOptions(60, True, False, 24, 7, 24),
        logger=logging.getLogger("test.maintenance.backup-assets"),
    )

    assert runner._backup_asset_paths([backup]) == ((expected,), 0)


@pytest.mark.asyncio
async def test_maintenance_skips_catalog_cleanup_when_retained_backup_is_unreadable(
    tmp_path: Path,
) -> None:
    database = PigCatcherDatabase(tmp_path / "pig.sqlite3")
    await database.open()
    storage = AssetCatalogStorage(tmp_path)
    storage.ensure_layout()
    protected_candidates = tuple(character * 64 for character in "ab")
    for catalog_hash in protected_candidates:
        catalog = storage.catalogs_root / catalog_hash
        catalog.mkdir()
        (catalog / "payload.bin").write_bytes(b"still needed by an unknown backup")

    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "pig_catcher-pre-unreadable.sqlite3").write_bytes(b"not-a-database")
    runner = MaintenanceRunner(
        database,
        storage,
        tmp_path,
        MaintenanceOptions(
            60,
            True,
            False,
            24,
            7,
            24,
            catalog_rollback_retention_count=0,
        ),
        logger=logging.getLogger("test.maintenance.unreadable-backup"),
    )

    report = await runner.run_once()

    assert report.catalog_cleanup_skipped is True
    assert report.removed_catalog_directories == 0
    assert report.removed_catalog_bytes == 0
    assert all((storage.catalogs_root / value).is_dir() for value in protected_candidates)
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
