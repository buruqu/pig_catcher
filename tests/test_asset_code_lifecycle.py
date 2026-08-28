"""Schema42 的活跃短编号、历史引用、并发以及改号券底层回归。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.enums import AssetKind
from pig_catcher.domain.errors import AssetStateConflictError, DomainValidationError, MigrationError
from pig_catcher.domain.special_content import TECHNIQUE_MALEVOLENT_KITCHEN
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.database import DatabaseSession
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.migrations.v0042_asset_code_lifecycle import (
    GUARDS,
    MIGRATION_0042,
)
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.infrastructure.repositories.asset_codes import AssetCodeRepository
from pig_catcher.infrastructure.repositories.gameplay import GameplayRepository
from pig_catcher.infrastructure.repositories.social import SocialRepository
from pig_catcher.infrastructure.repositories.techniques import TechniqueRepository
from pig_catcher.services import FrameworkService
from pig_catcher.services.economy import EconomyService
from pig_catcher.services.gameplay import GameplayService

from .test_economy import (
    FixedClock,
    SequenceRandom,
    _catch_one_star,
    _database_with_catalog,
    _food_entry,
    _identity,
    _insert_food,
    _insert_pig,
    _pig_entry,
)

NOW = "2026-08-28T00:00:00.000Z"
SCOPE = "qq:codes"
OWNER = "qq:codes:one"
PEER = "qq:codes:two"
OTHER_SCOPE = "qq:other-codes"
OTHER_OWNER = "qq:other-codes:one"


def _seed_world(connection: sqlite3.Connection) -> None:
    for scope_id, group_id in ((SCOPE, "codes"), (OTHER_SCOPE, "other-codes")):
        connection.execute(
            """
            INSERT INTO scopes(scope_id, platform, group_id, group_name, stream_id, created_at, updated_at)
            VALUES (?, 'qq', ?, '编号测试群', 'test-stream', ?, ?)
            """,
            (scope_id, group_id, NOW, NOW),
        )
    for player_id, scope_id, user_id in (
        (OWNER, SCOPE, "one"),
        (PEER, SCOPE, "two"),
        (OTHER_OWNER, OTHER_SCOPE, "one"),
    ):
        connection.execute(
            """
            INSERT INTO players(player_id, scope_id, platform_user_id, display_name, created_at, updated_at)
            VALUES (?, ?, ?, '编号玩家', ?, ?)
            """,
            (player_id, scope_id, user_id, NOW, NOW),
        )
    connection.execute(
        """
        INSERT INTO asset_manifest_imports(
            catalog_hash, catalog_id, manifest_version, source_label, storage_relpath,
            entry_count, status, created_at
        ) VALUES ('code-catalog', 'code-catalog', 4, 'pytest', 'assets/code-catalog', 2, 'active', ?)
        """,
        (NOW,),
    )
    connection.execute(
        """
        INSERT INTO pig_templates(
            template_id, catalog_hash, display_name, rarity, scope_type, description,
            image_relpath, image_sha256, image_fit, length_min, length_max, weight_min, weight_max,
            fat_profile, source_label, license, consent_status, created_at, updated_at
        ) VALUES ('code-pig', 'code-catalog', '编号猪', 1, 'common', '测试', 'pig.png', ?, 'contain',
                  10, 50, 5, 100, 'balanced', 'pytest', 'test', 'not-required', ?, ?)
        """,
        ("a" * 64, NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO food_templates(
            template_id, catalog_hash, display_name, rarity, scope_type, description,
            image_relpath, image_sha256, image_fit, source_label, license, consent_status, created_at, updated_at
        ) VALUES ('code-food', 'code-catalog', '编号菜', 1, 'common', '测试', 'food.png', ?, 'contain',
                  'pytest', 'test', 'not-required', ?, ?)
        """,
        ("b" * 64, NOW, NOW),
    )


def _asset_values(
    kind: str,
    instance_id: str,
    code: str,
    *,
    state: str = "active",
    scope: str = SCOPE,
    owner: str = OWNER,
    favorite: int = 0,
) -> dict[str, object]:
    result: dict[str, object] = {
        f"{kind}_instance_id": instance_id,
        "short_code": code,
        "scope_id": scope,
        "owner_player_id": owner,
        "template_id": f"code-{kind}",
        "template_version": 1,
        "rarity": 1,
        "display_name_snapshot": "编号猪" if kind == "pig" else "编号菜",
        "official_value": 99,
        "ruleset_version": 37,
        "random_snapshot_json": '{"immutable_probe":true}',
        "state": state,
        "locked_trade_id": "trade-test" if state == "locked-for-trade" else None,
        "acquired_at": NOW,
        "disposed_at": NOW if state not in {"active", "locked-for-trade"} else None,
        "updated_at": NOW,
        "is_favorite": favorite,
    }
    if kind == "pig":
        result.update(
            size_value=40,
            size_percentile=0.5,
            weight_value=60,
            weight_percentile=0.5,
            fat_ratio=50,
            display_variant="pig",
        )
    else:
        result.update(
            source_pig_instance_id=None,
            portion_weight=30,
            fat_category="balanced",
            effect_id="",
            effect_params_json="{}",
        )
    return result


def _insert_sql(kind: str, values: dict[str, object]) -> str:
    return f"INSERT INTO {kind}_instances({','.join(values)}) VALUES ({','.join(':' + key for key in values)})"


def _insert(connection: sqlite3.Connection, kind: str, instance_id: str, code: str, **kwargs: object) -> None:
    values = _asset_values(kind, instance_id, code, **kwargs)
    connection.execute(_insert_sql(kind, values), values)


async def _insert_async(session: DatabaseSession, kind: str, instance_id: str, code: str, **kwargs: object) -> None:
    values = _asset_values(kind, instance_id, code, **kwargs)
    await session.execute(_insert_sql(kind, values), values)


def _create_v41(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)")
    for migration in MIGRATIONS:
        if migration.version > 41:
            break
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, NOW))
    connection.execute("PRAGMA user_version=41")
    _seed_world(connection)
    connection.commit()
    return connection


def _upgrade_42(connection: sqlite3.Connection, *, fail_after_statements: bool = False) -> None:
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in MIGRATION_0042.statements:
            connection.execute(statement)
        if fail_after_statements:
            connection.execute("SELECT 1 FROM deliberately_missing_table")
        connection.execute("INSERT INTO schema_migrations VALUES(42,?,?)", (MIGRATION_0042.name, NOW))
        connection.execute("PRAGMA user_version=42")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


def _snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    names = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "AND name!='schema_migrations' ORDER BY name"
    ).fetchall()
    return {str(row[0]): connection.execute(f'SELECT * FROM "{row[0]}" ORDER BY rowid').fetchall() for row in names}


async def _database(tmp_path: Path) -> PigCatcherDatabase:
    path = tmp_path / "codes.sqlite3"
    connection = _create_v41(path)
    connection.close()
    database = PigCatcherDatabase(path)
    await database.open()
    return database


def test_v41_migration_preserves_every_asset_field_history_and_existing_guards(tmp_path: Path) -> None:
    connection = _create_v41(tmp_path / "history.sqlite3")
    for kind, states in (
        ("pig", ("active", "locked-for-trade", "sold", "consumed-for-cooking", "admin-removed")),
        ("food", ("active", "locked-for-trade", "sold", "consumed", "admin-removed")),
    ):
        for index, state in enumerate(states):
            _insert(connection, kind, f"{kind}-{index}", f"{kind}MiXeD{index}", state=state, favorite=index % 2)
    connection.execute("UPDATE food_instances SET source_pig_instance_id='pig-3' WHERE food_instance_id='food-0'")
    connection.execute("INSERT INTO battle_training(pig_instance_id,level) VALUES ('pig-0',3)")
    connection.execute(
        "INSERT INTO asset_occupancies VALUES ('pig-0',?,?,'dispatch','old-trip',123456789,?)", (OWNER, SCOPE, NOW)
    )
    connection.commit()
    before = _snapshot(connection)
    guards_before = set(connection.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('index','trigger')"))

    _upgrade_42(connection)

    assert _snapshot(connection) == before
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 42
    assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    guards_after = set(connection.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('index','trigger')"))
    removed = guards_before - guards_after
    assert {(row[0], row[1]) for row in removed} == {
        ("index", "sqlite_autoindex_pig_instances_2"),
        ("index", "sqlite_autoindex_food_instances_2"),
    }
    assert set(GUARDS) <= {row[1] for row in guards_after}
    # UUID 外键仍指向旧原料，绝不把菜的来源重连到另一个复用编号的猪。
    assert (
        connection.execute(
            "SELECT source_pig_instance_id FROM food_instances WHERE food_instance_id='food-0'"
        ).fetchone()[0]
        == "pig-3"
    )
    with pytest.raises(sqlite3.IntegrityError, match="正在活动"):
        connection.execute("UPDATE pig_instances SET state='sold' WHERE pig_instance_id='pig-0'")
    connection.close()


def test_v41_cross_kind_conflict_aborts_without_rewriting_or_deleting_legacy_rows(tmp_path: Path) -> None:
    connection = _create_v41(tmp_path / "conflict.sqlite3")
    _insert(connection, "pig", "old-pig", "MiXeD123")
    _insert(connection, "food", "old-food", "mixed123", state="locked-for-trade")
    connection.commit()
    before = _snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError, match="asset_code_v42_guard"):
        _upgrade_42(connection)
    assert _snapshot(connection) == before
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 41
    assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 41
    connection.close()


def test_v41_cross_kind_disposed_history_does_not_block_migration(tmp_path: Path) -> None:
    connection = _create_v41(tmp_path / "disposed-history.sqlite3")
    _insert(connection, "pig", "old-pig", "MiXeD123", state="sold")
    _insert(connection, "food", "current-food", "mixed123")
    connection.commit()
    before = _snapshot(connection)
    _upgrade_42(connection)
    assert _snapshot(connection) == before
    connection.close()


def test_late_migration_failure_restores_tables_data_and_all_triggers(tmp_path: Path) -> None:
    connection = _create_v41(tmp_path / "rollback.sqlite3")
    _insert(connection, "pig", "held-pig", "PROTECT1")
    connection.execute("INSERT INTO tour_protections VALUES ('held-pig',?,?,1)", (OWNER, SCOPE))
    connection.commit()
    before = _snapshot(connection)
    schema_before = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="deliberately_missing_table"):
        _upgrade_42(connection, fail_after_statements=True)
    assert _snapshot(connection) == before
    assert connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == schema_before
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 41
    with pytest.raises(sqlite3.IntegrityError, match="乐队保护"):
        connection.execute("UPDATE pig_instances SET state='sold' WHERE pig_instance_id='held-pig'")
    connection.close()


@pytest.mark.parametrize("source_kind", ("pig", "food"))
@pytest.mark.parametrize("target_kind", ("pig", "food"))
@pytest.mark.parametrize("state", ("active", "locked-for-trade"))
@pytest.mark.parametrize("different_scope", (False, True))
def test_active_code_is_unique_across_kinds_case_and_groups(
    tmp_path: Path, source_kind: str, target_kind: str, state: str, different_scope: bool
) -> None:
    connection = _create_v41(tmp_path / "unique.sqlite3")
    _upgrade_42(connection)
    _insert(connection, source_kind, "original", "ABcd1234", state=state)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(
            connection,
            target_kind,
            "collision",
            "abcd1234",
            owner=OTHER_OWNER if different_scope else PEER,
            scope=OTHER_SCOPE if different_scope else SCOPE,
        )
    assert connection.execute(f"SELECT short_code FROM {source_kind}_instances").fetchall() == [("ABcd1234",)]
    connection.close()


@pytest.mark.parametrize(
    "kind,state",
    (
        ("pig", "sold"),
        ("pig", "consumed-for-cooking"),
        ("pig", "admin-removed"),
        ("food", "sold"),
        ("food", "consumed"),
        ("food", "admin-removed"),
    ),
)
@pytest.mark.parametrize("target_kind", ("pig", "food"))
def test_disposed_code_is_reusable_but_original_uuid_and_code_remain(
    tmp_path: Path, kind: str, state: str, target_kind: str
) -> None:
    connection = _create_v41(tmp_path / "reuse.sqlite3")
    _insert(connection, kind, "old-instance", "oLdCode1", state=state)
    connection.commit()
    _upgrade_42(connection)
    _insert(connection, target_kind, "new-instance", "OLDCODE1")
    assert connection.execute(
        f"SELECT short_code,state FROM {kind}_instances WHERE {kind}_instance_id='old-instance'"
    ).fetchone() == ("oLdCode1", state)
    assert connection.execute(
        f"SELECT short_code,state FROM {target_kind}_instances WHERE {target_kind}_instance_id='new-instance'"
    ).fetchone() == ("OLDCODE1", "active")
    connection.close()


@pytest.mark.parametrize("kind", ("pig", "food"))
@pytest.mark.parametrize("target_kind", ("pig", "food"))
def test_reactivation_cannot_steal_a_reused_code(tmp_path: Path, kind: str, target_kind: str) -> None:
    connection = _create_v41(tmp_path / "revive.sqlite3")
    _upgrade_42(connection)
    _insert(connection, kind, "disposed", "AGAIN001", state="sold")
    _insert(connection, target_kind, "current", "again001")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"UPDATE {kind}_instances SET state='active', disposed_at=NULL WHERE {kind}_instance_id='disposed'"
        )
    assert connection.execute(f"SELECT state FROM {kind}_instances WHERE {kind}_instance_id='disposed'").fetchone() == (
        "sold",
    )
    connection.close()


@pytest.mark.parametrize("kind", ("pig", "food"))
@pytest.mark.parametrize("target_kind", ("pig", "food"))
def test_direct_sql_code_update_cannot_bypass_active_unique_guards(tmp_path: Path, kind: str, target_kind: str) -> None:
    connection = _create_v41(tmp_path / "update.sqlite3")
    _upgrade_42(connection)
    _insert(connection, kind, "current", "AABBCCDD")
    _insert(connection, target_kind, "renaming", "QWERTY11")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"UPDATE {target_kind}_instances SET short_code='aabbccdd' WHERE {target_kind}_instance_id='renaming'"
        )
    connection.close()


@pytest.mark.asyncio
async def test_occupied_lookup_tracks_lifecycle_and_rolls_back_release(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    repository = GameplayRepository()
    async with database.transaction() as session:
        await _insert_async(session, "pig", "pig-one", "ACTIVE01")
        assert await repository.short_code_exists(session, "active01")
    with pytest.raises(RuntimeError, match="rollback"):
        async with database.transaction() as session:
            await session.execute(
                "UPDATE pig_instances SET state='sold', disposed_at=? WHERE pig_instance_id='pig-one'", (NOW,)
            )
            assert not await repository.short_code_exists(session, "ACTIVE01")
            await _insert_async(session, "food", "food-in-rollback", "ACTIVE01")
            assert await repository.short_code_exists(session, "active01")
            raise RuntimeError("rollback")
    async with database.transaction() as session:
        assert await repository.short_code_exists(session, "active01")
        assert await session.fetch_one("SELECT 1 FROM food_instances") is None
        assert (await session.fetch_one("SELECT state FROM pig_instances"))[0] == "active"
        await session.execute("UPDATE pig_instances SET state='consumed-for-cooking', disposed_at=?", (NOW,))
        assert not await repository.short_code_exists(session, "active01")
        await _insert_async(session, "food", "food-final", "active01")
    await database.close()
    await database.open()
    async with database.transaction() as session:
        assert await repository.short_code_exists(session, "ACTIVE01")
    assert await database.fetch_all("PRAGMA foreign_key_check") == []
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_kind", ("pig", "food"))
async def test_cross_manager_concurrent_allocation_has_only_one_winner(tmp_path: Path, other_kind: str) -> None:
    database = await _database(tmp_path)
    peer_database = PigCatcherDatabase(database.path)
    await peer_database.open()

    async def allocate(db: PigCatcherDatabase, kind: str, instance_id: str, code: str) -> None:
        async with db.transaction() as session:
            await _insert_async(session, kind, instance_id, code)

    outcomes = await asyncio.gather(
        allocate(database, "pig", "first-racer", "RaCe1234"),
        allocate(peer_database, other_kind, "second-racer", "RACE1234"),
        return_exceptions=True,
    )
    assert sum(value is None for value in outcomes) == 1
    assert sum(isinstance(value, sqlite3.IntegrityError) for value in outcomes) == 1
    rows = await database.fetch_all(
        "SELECT short_code FROM pig_instances UNION ALL SELECT short_code FROM food_instances"
    )
    assert len(rows) == 1
    await peer_database.close()
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", (AssetKind.PIG, AssetKind.FOOD))
async def test_rename_owned_asset_reuses_disposed_code_preserves_favorite_and_all_other_fields(
    tmp_path: Path, kind: AssetKind
) -> None:
    database = await _database(tmp_path)
    repository = AssetCodeRepository()
    async with database.transaction() as session:
        await _insert_async(session, "food", "old-history", "NeW947", state="consumed")
        await _insert_async(session, kind.value, "owned", "OLD947", favorite=1)
        before = dict(
            await session.fetch_one(f"SELECT * FROM {kind.value}_instances WHERE {kind.value}_instance_id='owned'")
        )
        result = await repository.rename_owned_asset(
            session,
            asset_kind=kind,
            asset_instance_id="owned",
            owner_player_id=OWNER,
            scope_id=SCOPE,
            new_short_code="new947",
            now="2026-08-28T01:00:00.000Z",
        )
        assert result == {
            "asset_kind": kind.value,
            "asset_instance_id": "owned",
            "old_short_code": "OLD947",
            "new_short_code": "NEW947",
            "display_name": "编号猪" if kind is AssetKind.PIG else "编号菜",
        }
        after = dict(
            await session.fetch_one(f"SELECT * FROM {kind.value}_instances WHERE {kind.value}_instance_id='owned'")
        )
        assert after == before | {"short_code": "NEW947", "updated_at": "2026-08-28T01:00:00.000Z"}
        assert not await repository.code_is_occupied(session, "old947")
        assert await repository.code_is_occupied(session, "new947")
        history = await session.fetch_one(
            "SELECT short_code,state FROM food_instances WHERE food_instance_id='old-history'"
        )
        assert tuple(history) == ("NeW947", "consumed")
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    (
        "locked-trade",
        "disposed",
        "owner",
        "scope",
        "dispatch",
        "tour",
        "battle",
        "tour-protected",
        "battle-protected",
    ),
)
async def test_rename_rejects_every_wrong_owner_state_and_activity_lock(tmp_path: Path, problem: str) -> None:
    database = await _database(tmp_path)
    async with database.transaction() as session:
        await _insert_async(
            session,
            "pig",
            "rename-pig",
            "GUARD947",
            state={"locked-trade": "locked-for-trade", "disposed": "sold"}.get(problem, "active"),
        )
        if problem in {"dispatch", "tour", "battle"}:
            await session.execute(
                "INSERT INTO asset_occupancies VALUES ('rename-pig',?,?,?,?,?,?)",
                (OWNER, SCOPE, problem, "activity-test", 123456789, NOW),
            )
        if problem.endswith("-protected"):
            protection = problem.removesuffix("-protected")
            await session.execute(f"INSERT INTO {protection}_protections VALUES ('rename-pig',?,?,1)", (OWNER, SCOPE))
    with pytest.raises(AssetStateConflictError):
        async with database.transaction() as session:
            await AssetCodeRepository().rename_owned_asset(
                session,
                asset_kind=AssetKind.PIG,
                asset_instance_id="rename-pig",
                owner_player_id=PEER if problem == "owner" else OWNER,
                scope_id=OTHER_SCOPE if problem == "scope" else SCOPE,
                new_short_code="NEW9947",
                now=NOW,
            )
    assert (await database.fetch_one("SELECT short_code FROM pig_instances"))[0] == "GUARD947"
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("new_code", ("old947", "OLD947", "BAD", "啊947", "a" * 17, "a_947", "taken947"))
async def test_rename_rejects_same_invalid_or_occupied_code(tmp_path: Path, new_code: str) -> None:
    database = await _database(tmp_path)
    async with database.transaction() as session:
        await _insert_async(session, "pig", "rename-pig", "OLD947")
        await _insert_async(session, "food", "occupied-food", "TAKEN947", state="locked-for-trade")
    with pytest.raises(DomainValidationError):
        async with database.transaction() as session:
            await AssetCodeRepository().rename_owned_asset(
                session,
                asset_kind=AssetKind.PIG,
                asset_instance_id="rename-pig",
                owner_player_id=OWNER,
                scope_id=SCOPE,
                new_short_code=new_code,
                now=NOW,
            )
    assert (await database.fetch_one("SELECT short_code FROM pig_instances"))[0] == "OLD947"
    await database.close()


@pytest.mark.asyncio
async def test_rename_is_rolled_back_with_callers_coupon_transaction(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    async with database.transaction() as session:
        await _insert_async(session, "food", "rename-food", "OLD947")
    with pytest.raises(RuntimeError, match="coupon-failed"):
        async with database.transaction() as session:
            await AssetCodeRepository().rename_owned_asset(
                session,
                asset_kind=AssetKind.FOOD,
                asset_instance_id="rename-food",
                owner_player_id=OWNER,
                scope_id=SCOPE,
                new_short_code="NEW947",
                now=NOW,
            )
            raise RuntimeError("coupon-failed")
    assert (await database.fetch_one("SELECT short_code FROM food_instances"))[0] == "OLD947"
    await database.close()


@pytest.mark.asyncio
async def test_catch_cook_and_eat_receipts_follow_uuid_after_code_reuse_and_restart(tmp_path: Path) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    first_gameplay, first_catch = await _catch_one_star(
        database, clock=clock, message_id="reuse-catch-1", short_code="REUSEP01"
    )
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.0, 0.0, 0.5),
        short_code_factory=lambda: "REUSEF01",
    )
    first_cook_id = _identity(message_id="reuse-cook-1")
    first_cook = await economy.cook(first_cook_id, first_catch.pig.selector)
    eat_id = _identity(message_id="reuse-eat-1")
    first_eat = await economy.eat(eat_id, first_cook.foods[0].selector)
    assert first_eat.receipt_created
    _, second_catch = await _catch_one_star(database, clock=clock, message_id="reuse-catch-2", short_code="REUSEP01")
    second_cook = await economy.cook(_identity(message_id="reuse-cook-2"), second_catch.pig.selector)
    _, third_catch = await _catch_one_star(database, clock=clock, message_id="reuse-catch-3", short_code="REUSEP01")
    await database.close()
    await database.open()

    # 所有随机序列都已用完。重放若走错当前编号实例，会消耗新资产或在随机处报错。
    replayed_catch = await first_gameplay.catch(_identity(message_id="reuse-catch-1"))
    replayed_cook = await economy.cook(first_cook_id, first_catch.pig.selector)
    replayed_eat = await economy.eat(eat_id, first_cook.foods[0].selector)
    assert not replayed_catch.receipt_created
    assert not replayed_cook.receipt_created
    assert not replayed_eat.receipt_created
    assert replayed_catch.pig.pig_instance_id == first_catch.pig.pig_instance_id
    assert replayed_cook.foods[0].food_instance_id == first_cook.foods[0].food_instance_id
    assert replayed_eat.food.food_instance_id == first_cook.foods[0].food_instance_id
    assert replayed_catch.receipt.text_summary == first_catch.receipt.text_summary
    assert replayed_cook.receipt.text_summary == first_cook.receipt.text_summary
    assert replayed_eat.receipt.text_summary == first_eat.receipt.text_summary
    current_pig = await first_gameplay.pig_detail(_identity(message_id="current-pig"), "1星测试猪#reusep01")
    current_food = await economy.food_detail(_identity(message_id="current-food"), "1星测试菜#reusef01")
    assert current_pig.pig_instance_id == third_catch.pig.pig_instance_id
    assert current_food.food_instance_id == second_cook.foods[0].food_instance_id
    assert (await database.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 3
    assert (await database.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 2
    assert (await database.fetch_one("SELECT COUNT(*) FROM command_receipts"))[0] == 6
    assert await database.fetch_all("PRAGMA foreign_key_check") == []
    await database.close()


@pytest.mark.asyncio
async def test_sale_releases_code_but_repeat_sale_does_not_consume_new_asset(tmp_path: Path) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    _, first = await _catch_one_star(database, clock=clock, message_id="sale-old", short_code="SALES947")
    economy = EconomyService(database, CookingSection(cook_cooldown_seconds=0), EconomySection(), clock=clock)
    sale_id = _identity(message_id="sale-once")
    sold = await economy.sell_pig(sale_id, first.pig.selector)
    _, second = await _catch_one_star(database, clock=clock, message_id="sale-new", short_code="sales947")
    ledger_before = await database.fetch_all(
        "SELECT ledger_entry_id,amount FROM currency_ledger ORDER BY ledger_entry_id"
    )
    replay = await economy.sell_pig(sale_id, first.pig.selector)
    assert not replay.receipt_created
    assert replay.receipt.text_summary == sold.receipt.text_summary
    assert ledger_before == await database.fetch_all(
        "SELECT ledger_entry_id,amount FROM currency_ledger ORDER BY ledger_entry_id"
    )
    state = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id=?", (second.pig.pig_instance_id,)
    )
    assert state[0] == "active"
    await database.close()


@pytest.mark.asyncio
async def test_aya_return_never_frees_source_code_before_food_allocation(tmp_path: Path) -> None:
    source = _pig_entry(6, group_id="100", template_suffix="code-aya", paired_food_template_id="code-aya-paired")
    paired = _food_entry(6, group_id="100", template_suffix="code-aya-paired")
    paired["template_id"] = "code-aya-paired"
    five = _food_entry(5, template_suffix="code-aya-five")
    repair = _food_entry(
        6,
        group_id="100",
        template_suffix="code-aya-repair",
        effect_id="six-star-cook-failure-return",
        effect_params={"uses": 3, "return_chance_percent": 75},
    )
    repair["display_name"] = "彩彩修车猪慕斯"
    database = await _database_with_catalog(
        tmp_path, pig_rarities=(), food_rarities=(), extra_entries=(source, paired, five, repair)
    )
    owner = _identity(message_id="aya-code-owner")
    await FrameworkService(database).touch_identity(owner)
    await _insert_pig(
        database,
        player_id=owner.player_id,
        scope_id=owner.scope.value,
        template_id=str(source["template_id"]),
        rarity=6,
        display_name="6星测试猪",
        official_value=25_000,
        short_code="AYAPIG01",
        instance_id="aya-code-pig",
    )
    await _insert_food(
        database,
        player_id=owner.player_id,
        scope_id=owner.scope.value,
        template_id=str(repair["template_id"]),
        rarity=6,
        display_name="彩彩修车猪慕斯",
        official_value=25_000,
        short_code="AYADISH1",
        instance_id="aya-code-dish",
        effect_id="six-star-cook-failure-return",
        effect_params={"uses": 3, "return_chance_percent": 75},
    )
    codes = iter(("AYAPIG01", "AYANEW01", "AYAPIG01", "AYANEW02"))
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.0, 0.5, 0.0, 0.5, 0.95, 0.0, 0.5),
        short_code_factory=codes.__next__,
    )
    await economy.eat(_identity(message_id="aya-code-eat"), "彩彩修车猪慕斯#AYADISH1")
    first = await economy.cook(_identity(message_id="aya-code-failed"), "6星测试猪#AYAPIG01")
    assert first.foods[0].short_code == "AYANEW01"
    async with database.transaction() as session:
        assert await GameplayRepository().short_code_exists(session, "ayapig01")
    second = await economy.cook(_identity(message_id="aya-code-success"), "6星测试猪#AYAPIG01")
    assert second.foods[0].short_code == "AYANEW02"
    async with database.transaction() as session:
        assert not await GameplayRepository().short_code_exists(session, "ayapig01")
    assert (await database.fetch_one("SELECT state FROM pig_instances WHERE pig_instance_id='aya-code-pig'"))[0] == (
        "consumed-for-cooking"
    )
    await database.close()


@pytest.mark.asyncio
async def test_domain_reserves_source_and_both_food_codes_without_destroying_uuid_history(tmp_path: Path) -> None:
    database = await _database_with_catalog(tmp_path, pig_rarities=(6,), food_rarities=(6,), manifest_version=4)
    activator = _identity(user_id="100", message_id="domain-code-owner")
    await FrameworkService(database).touch_identity(activator)
    async with database.transaction() as session:
        await TechniqueRepository().grant_permit(
            session, player_id=activator.player_id, technique_id=TECHNIQUE_MALEVOLENT_KITCHEN, uses=1, now=NOW
        )
    codes = iter(("DOMAIN01", "DOMAIN01", "DMFOOD01", "DOMAIN01", "DMFOOD01", "DMFOOD02"))
    gameplay = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        clock=FixedClock(),
        random_source=SequenceRandom(0.999, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.99, 0.0, 0.5, 0.5),
        short_code_factory=codes.__next__,
    )
    await gameplay.activate_group_technique(
        _identity(user_id="100", message_id="domain-code-start"), technique_id=TECHNIQUE_MALEVOLENT_KITCHEN
    )
    catch_id = _identity(message_id="domain-code-catch")
    caught = await gameplay.catch(catch_id)
    assert caught.technique_resolution is not None
    assert {food.short_code for food in caught.technique_resolution.generated_foods} == {"DMFOOD01", "DMFOOD02"}
    assert (await database.fetch_one("SELECT state FROM pig_instances"))[0] == "consumed-for-cooking"
    async with database.transaction() as session:
        assert not await GameplayRepository().short_code_exists(session, "domain01")
        # 手工重用已消耗猪的编号，领域的两份成品仍引用原来的 UUID。
        await session.execute("UPDATE food_instances SET short_code='DOMAIN01' WHERE short_code='DMFOOD01'")
    replay = await gameplay.catch(catch_id)
    assert not replay.receipt_created
    assert replay.technique_resolution == caught.technique_resolution
    assert replay.receipt.text_summary == caught.receipt.text_summary
    assert {row[0] for row in await database.fetch_all("SELECT source_pig_instance_id FROM food_instances")} == {
        caught.pig.pig_instance_id,
    }
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ("missing-trigger", "missing-index", "nonunique", "permanent", "binary", "wrong-state")
)
async def test_stamped_current_database_rejects_broken_code_guards(tmp_path: Path, fault: str) -> None:
    database = await _database(tmp_path)
    await database.close()
    connection = sqlite3.connect(database.path)
    if fault == "missing-trigger":
        connection.execute("DROP TRIGGER pig_active_short_code_insert")
    elif fault == "permanent":
        connection.execute("CREATE UNIQUE INDEX bogus_permanent_code ON pig_instances(short_code COLLATE NOCASE)")
    else:
        connection.execute("DROP INDEX idx_pig_active_short_code")
        if fault == "nonunique":
            connection.execute(
                "CREATE INDEX idx_pig_active_short_code ON pig_instances(short_code COLLATE NOCASE) "
                "WHERE state IN ('active', 'locked-for-trade')"
            )
        elif fault == "binary":
            connection.execute(
                "CREATE UNIQUE INDEX idx_pig_active_short_code ON pig_instances(short_code COLLATE BINARY) "
                "WHERE state IN ('active', 'locked-for-trade')"
            )
        elif fault == "wrong-state":
            connection.execute(
                "CREATE UNIQUE INDEX idx_pig_active_short_code ON pig_instances(short_code COLLATE NOCASE) "
                "WHERE state='active'"
            )
    connection.commit()
    connection.close()
    with pytest.raises(MigrationError):
        await database.open()
    assert not database.is_open


@pytest.mark.asyncio
async def test_concurrent_coupon_renames_have_one_winner_and_preserve_loser(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    peer_database = PigCatcherDatabase(database.path)
    await peer_database.open()
    async with database.transaction() as session:
        await _insert_async(session, "pig", "rename-first", "ORIG9471")
        await _insert_async(session, "food", "rename-second", "ORIG9472")

    async def rename(db: PigCatcherDatabase, kind: AssetKind, instance_id: str) -> dict[str, str]:
        async with db.transaction() as session:
            return await AssetCodeRepository().rename_owned_asset(
                session,
                asset_kind=kind,
                asset_instance_id=instance_id,
                owner_player_id=OWNER,
                scope_id=SCOPE,
                new_short_code="New947",
                now=NOW,
            )

    results = await asyncio.gather(
        rename(database, AssetKind.PIG, "rename-first"),
        rename(peer_database, AssetKind.FOOD, "rename-second"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, DomainValidationError) for result in results) == 1
    codes = {
        row[0]
        for row in await database.fetch_all(
            "SELECT short_code FROM pig_instances UNION ALL SELECT short_code FROM food_instances"
        )
    }
    assert len(codes) == 2 and "NEW947" in codes
    assert len(codes & {"ORIG9471", "ORIG9472"}) == 1
    await peer_database.close()
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", (AssetKind.PIG, AssetKind.FOOD))
async def test_gifts_trade_locks_unlocks_and_acceptance_never_release_code(tmp_path: Path, kind: AssetKind) -> None:
    database = await _database(tmp_path)
    social = SocialRepository()
    codes = AssetCodeRepository()
    async with database.transaction() as session:
        await _insert_async(session, kind.value, "transferred", "SOCIAL947")
        assert await social.transfer_active_asset(
            session,
            asset_kind=kind,
            asset_instance_id="transferred",
            scope_id=SCOPE,
            from_player_id=OWNER,
            to_player_id=PEER,
            now=NOW,
        )
        assert await codes.code_is_occupied(session, "social947")
        assert await social.lock_asset_for_trade(
            session,
            asset_kind=kind,
            asset_instance_id="transferred",
            scope_id=SCOPE,
            owner_player_id=PEER,
            trade_id="trade-one",
            now=NOW,
        )
        assert await codes.code_is_occupied(session, "social947")
        assert await social.unlock_trade_asset(
            session, asset_kind=kind, asset_instance_id="transferred", trade_id="trade-one", now=NOW
        )
        assert await codes.code_is_occupied(session, "social947")
        assert await social.lock_asset_for_trade(
            session,
            asset_kind=kind,
            asset_instance_id="transferred",
            scope_id=SCOPE,
            owner_player_id=PEER,
            trade_id="trade-two",
            now=NOW,
        )
        assert await social.accept_trade_asset(
            session,
            asset_kind=kind,
            asset_instance_id="transferred",
            scope_id=SCOPE,
            sender_player_id=PEER,
            recipient_player_id=OWNER,
            trade_id="trade-two",
            now=NOW,
        )
        assert await codes.code_is_occupied(session, "social947")
        row = await session.fetch_one(f"SELECT short_code,state,owner_player_id FROM {kind.value}_instances")
        assert tuple(row) == ("SOCIAL947", "active", OWNER)
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("pig", "food"))
async def test_legacy_achievement_reforge_selects_live_uuid_among_reused_history(tmp_path: Path, kind: str) -> None:
    database = await _database(tmp_path)
    async with database.transaction() as session:
        await _insert_async(session, kind, "old-original-history", "ORIG947", state="sold")
        await _insert_async(session, kind, "old-target-history", "NeW947", state="sold")
        await _insert_async(session, kind, "live-reforge", "ORIG947", favorite=1)
        changed_id = await AchievementRepository().reforge_short_code(
            session,
            player_id=OWNER,
            asset_kind=kind,
            old_code="orig947",
            new_code="new947",
            now=NOW,
        )
        assert changed_id == "live-reforge"
        rows = await session.fetch_all(
            f"SELECT {kind}_instance_id, short_code, state, is_favorite FROM {kind}_instances "
            f"ORDER BY {kind}_instance_id"
        )
        assert [tuple(row) for row in rows] == [
            ("live-reforge", "NEW947", "active", 1),
            ("old-original-history", "ORIG947", "sold", 0),
            ("old-target-history", "NeW947", "sold", 0),
        ]
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("protection", ("dispatch", "tour", "battle"))
async def test_legacy_achievement_reforge_cannot_bypass_activity_protection(tmp_path: Path, protection: str) -> None:
    database = await _database(tmp_path)
    async with database.transaction() as session:
        await _insert_async(session, "pig", "legacy-locked", "LEGACY947")
        if protection == "dispatch":
            await session.execute(
                "INSERT INTO asset_occupancies VALUES ('legacy-locked',?,?,'dispatch','trip',1,?)", (OWNER, SCOPE, NOW)
            )
        else:
            await session.execute(
                f"INSERT INTO {protection}_protections VALUES ('legacy-locked',?,?,1)", (OWNER, SCOPE)
            )
        assert (
            await AchievementRepository().reforge_short_code(
                session,
                player_id=OWNER,
                asset_kind="pig",
                old_code="legacy947",
                new_code="NEVER947",
                now=NOW,
            )
            is None
        )
        assert (await session.fetch_one("SELECT short_code FROM pig_instances"))[0] == "LEGACY947"
    await database.close()


@pytest.mark.asyncio
async def test_occupied_lookup_uses_bounded_active_indexes_not_disposed_history_scans(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    rows = await database.fetch_all(
        """
        EXPLAIN QUERY PLAN
        SELECT 1 FROM pig_instances
        WHERE short_code COLLATE NOCASE = ? AND state IN ('active', 'locked-for-trade')
        UNION ALL
        SELECT 1 FROM food_instances
        WHERE short_code COLLATE NOCASE = ? AND state IN ('active', 'locked-for-trade')
        LIMIT 1
        """,
        ("PLAN947", "PLAN947"),
    )
    details = "\n".join(str(row["detail"]) for row in rows)
    assert "USING INDEX idx_pig_active_short_code" in details
    assert "USING INDEX idx_food_active_short_code" in details
    assert "SCAN pig_instances" not in details and "SCAN food_instances" not in details
    await database.close()
