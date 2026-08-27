"""第三轮安全边界、完整事实、失败重试与旧系统兼容。"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from pig_catcher.domain.battle import dumps, loads, loot_weights, randbelow
from pig_catcher.domain.battle_catalog import BattleError
from pig_catcher.domain.errors import MigrationError
from pig_catcher.domain.models import ScopeKey
from pig_catcher.domain.special_content import SUKUNA_PIG_TEMPLATE_ID
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.migrations.v0039_battles import GUARDS, TABLES
from pig_catcher.infrastructure.repositories.dispatch import iso_ms, timestamp_ms
from pig_catcher.infrastructure.repositories.economy import EconomyRepository
from pig_catcher.infrastructure.repositories.gameplay import GameplayRepository
from pig_catcher.services.battle import BattleService
from pig_catcher.services.weekly_competitions import WeeklyCompetitionService

from .test_battle import BattleWorld
from .test_battle import world as world
from .test_dispatch import NOW, seed_pigs
from .test_economy import _insert_food


async def test_two_concurrent_different_invites_have_one_group_slot(world):
    second_db = PigCatcherDatabase(world.db.path)
    await second_db.open()
    try:
        second = BattleService(second_db, clock=world.clock)
        outcomes = await asyncio.gather(
            world.invite(), world.invite(actor=world.b, target=world.a, service=second), return_exceptions=True
        )
        assert sum(not isinstance(r, Exception) for r in outcomes) == 1
        assert sum(isinstance(r, BattleError) for r in outcomes) == 1
        assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_matches"))[0] == 1
    finally:
        await second_db.close()


async def test_transaction_failure_cannot_reroll_or_partially_commit(world, monkeypatch):
    await world.start()
    await world.send(section="count")
    before = (await world.match())["state_json"]
    original = world.service.receipts.reserve

    async def fail(*args, **kwargs):
        raise RuntimeError("simulated receipt storage failure")

    monkeypatch.setattr(world.service.receipts, "reserve", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        await world.send(section="move", mid="retry-move")
    assert (await world.match())["state_json"] == before
    assert not await world.db.fetch_all("SELECT * FROM battle_moves")
    monkeypatch.setattr(world.service.receipts, "reserve", original)
    result = await world.send(section="move", mid="retry-move")
    assert result.receipt and await world.db.fetch_all("SELECT * FROM battle_moves")
    counts = (await world.db.fetch_one("SELECT COUNT(*) FROM battle_moves"))[0]
    await world.send(section="move", mid="retry-move")
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_moves"))[0] == counts


async def test_long_chain_continues_exact_cursor_after_restart(world):
    await world.start()
    match = await world.match()
    state = loads(match["state_json"])
    state["sides"][0]["turn"].update(raw=5, effective=5, pending=70)
    async with world.db.transaction() as session:
        await session.execute(
            "UPDATE battle_matches SET state_json=? WHERE battle_id=?", (dumps(state), match["battle_id"])
        )
    result = await world.send(section="move", mid="chunk1")
    assert "32招" in result.view.text()
    first = loads((await world.match())["state_json"])
    assert first["sides"][0]["turn"]["draws"] == 32 and first["sides"][0]["turn"]["pending"] > 0
    second_db = PigCatcherDatabase(world.db.path)
    await second_db.open()
    try:
        await world.send(section="move", service=BattleService(second_db, clock=world.clock))
        records = await world.db.fetch_all("SELECT ordinal FROM battle_moves ORDER BY ordinal")
        assert [r[0] for r in records] == list(range(1, 65))
    finally:
        await second_db.close()


async def test_ordinary_batch_selector_ignores_protected_fighter(world):
    protected = (
        await world.db.fetch_one("SELECT pig_instance_id FROM battle_profiles WHERE player_id=?", (world.a.player_id,))
    )[0]
    async with world.db.transaction() as session:
        count, _ = await EconomyRepository().batch_sell_low_rarity(
            session,
            player_id=world.a.player_id,
            scope_id=world.a.scope.value,
            asset_kind="pig",
            max_rarity=5,
            rarity=5,
            keep_highest=False,
            display_name="宿傩猪",
            now=iso_ms(timestamp_ms(NOW)),
        )
    assert count == 1
    assert (await world.db.fetch_one("SELECT state FROM pig_instances WHERE pig_instance_id=?", (protected,)))[
        0
    ] == "active"
    async with world.db.transaction() as session:
        row = await GameplayRepository().get_pig_by_instance_id(session, pig_instance_id=protected)
        assert row["battle_protected"]


@pytest.mark.parametrize(
    "scope", [ScopeKey("qq", "200"), ScopeKey("qq.official.bot1", "G13"), ScopeKey("qq.official.bot2", "GCE")]
)
async def test_four_scopes_independent_battle_slots_and_identity(world, scope):
    await world.start()
    a = replace(world.a, scope=scope, stream_id="other-stream")
    b = replace(world.b, scope=scope, stream_id="other-stream")
    for actor in (a, b):
        await seed_pigs(world.db, actor, template_id=SUKUNA_PIG_TEMPLATE_ID, count=1)
        await world.assign(actor)
    other = BattleWorld(world.db, world.clock, world.service, a, b)
    await other.start()
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_matches WHERE status='active'"))[0] == 2
    first = await world.db.fetch_one("SELECT battle_id FROM battle_matches WHERE scope_id=?", (world.a.scope.value,))
    with pytest.raises(BattleError, match="本群没有"):
        await other.send(first[0], "history")


async def test_multiple_loot_grants_fifo_and_failed_claim_unchanged(world, monkeypatch):
    await world.fight()
    first = await world.db.fetch_one("SELECT * FROM battle_loot")
    world.clock.value += timedelta(days=1)
    await world.fight()
    second = await world.db.fetch_one("SELECT * FROM battle_loot ORDER BY created_ms DESC LIMIT 1")
    loser = world.a if first["actor_id"] == world.a.player_id else world.b
    assert first["actor_id"] == second["actor_id"]
    original = world.service.receipts.reserve

    async def fail(*args, **kwargs):
        raise RuntimeError("simulated failed delivery receipt")

    monkeypatch.setattr(world.service.receipts, "reserve", fail)
    with pytest.raises(RuntimeError):
        await world.send(section="loot", actor=loser, mid="retry-loot")
    assert not await world.db.fetch_all("SELECT * FROM battle_loot_deliveries")
    assert (await world.db.fetch_one("SELECT SUM(used) FROM battle_loot"))[0] == 0
    monkeypatch.setattr(world.service.receipts, "reserve", original)
    result = await world.send(section="loot", actor=loser, mid="retry-loot")
    assert result.view.battle_id == first["battle_id"]
    for _ in range(4):
        assert (await world.send(section="loot", actor=loser)).view.battle_id == first["battle_id"]
    assert (await world.send(section="loot", actor=loser)).view.battle_id == second["battle_id"]


async def test_loot_timestamp_tie_uses_monotonic_match_sequence(world):
    first = await world.fight()
    grant = dict(await world.db.fetch_one("SELECT * FROM battle_loot"))
    # A valid synthetic historical match with the same second but a lower random ID:
    # delivery order must follow the group's match sequence, never the random name.
    clone = {key: value for key, value in first.items() if key != "sequence"}
    clone["battle_id"] = "B000000000000"
    assert clone["battle_id"] < first["battle_id"]
    async with world.db.transaction() as session:
        await session.execute(
            f"INSERT INTO battle_matches({','.join(clone)}) VALUES({','.join('?' for _ in clone)})",
            tuple(clone.values()),
        )
        await session.execute(
            "INSERT INTO battle_loot VALUES(?,?,?,?,?,?)",
            (clone["battle_id"], grant["actor_id"], grant["recipient_id"], grant["scope_id"], 0, grant["created_ms"]),
        )
    loser = world.a if grant["actor_id"] == world.a.player_id else world.b
    assert (await world.send(section="loot", actor=loser)).view.battle_id == first["battle_id"]


async def test_no_grant_before_natural_end_and_finished_facts_are_immutable(world):
    await world.start()
    match = await world.match()
    with pytest.raises(sqlite3.IntegrityError, match="自然力竭"):
        async with world.db.transaction() as session:
            await session.execute(
                "INSERT INTO battle_loot VALUES(?,?,?,?,?,?)",
                (match["battle_id"], world.a.player_id, world.b.player_id, world.a.scope.value, 0, 1),
            )
    await world.fight(already_started=True)
    for statement in (
        "UPDATE battle_matches SET state_json='{}'",
        "UPDATE battle_loot SET used=used+2",
        "DELETE FROM battle_rounds",
        "DELETE FROM battle_moves",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            async with world.db.transaction() as session:
                await session.execute(statement)
    assert (await world.db.fetch_one("SELECT used FROM battle_loot"))[0] == 0
    assert await world.db.fetch_all("SELECT * FROM battle_rounds")


async def test_six_star_loot_replay_rechecks_revoked_media_without_redelivery(world):
    seed = next(
        f"privacy-{i}" for i in range(100) if randbelow(f"privacy-{i}", "loot:1:rarity", 1 << 53) / (1 << 53) > 0.8
    )
    world.service.seed_factory = lambda: seed
    await world.fight()
    grant = await world.db.fetch_one("SELECT * FROM battle_loot")
    loser = world.a if grant["actor_id"] == world.a.player_id else world.b
    first = await world.send(section="loot", actor=loser, mid="private-loot")
    assert first.view.pigs[0].rarity == 6 and first.view.pigs[0].image_relpath
    async with world.db.transaction() as session:
        await session.execute("UPDATE scope_pig_templates SET consent_status='revoked' WHERE template_id='star-6'")
    replay = await world.send(section="loot", actor=loser, mid="private-loot")
    assert not replay.view.pigs[0].image_relpath
    assert replay.receipt.receipt_id == first.receipt.receipt_id
    assert (await world.db.fetch_one("SELECT used FROM battle_loot"))[0] == 1


async def test_temporary_food_items_and_group_technique_untouched(world):
    w = world
    await w.fight()
    grant = await w.db.fetch_one("SELECT * FROM battle_loot")
    loser = w.a if grant["actor_id"] == w.a.player_id else w.b
    food = await w.db.fetch_one("SELECT * FROM food_templates")
    await _insert_food(
        w.db,
        player_id=loser.player_id,
        scope_id=loser.scope.value,
        template_id=food["template_id"],
        display_name="测试排队效果",
        official_value=100,
        short_code="BUFF2026",
        instance_id="fixture-food",
        rarity=5,
    )
    now = iso_ms(timestamp_ms(NOW))
    async with w.db.transaction() as session:
        await session.execute(
            "INSERT INTO item_inventory VALUES(?,?,?,?)", (loser.player_id, "super-lucky-whistle", 5, now)
        )
        await GameplayRepository().arm_item(
            session,
            player_id=loser.player_id,
            action_type="catching",
            item_id="super-lucky-whistle",
            remaining_uses=5,
            now=now,
        )
        await session.execute(
            "INSERT INTO player_food_effects VALUES(?,?,?,?,?,5,0,NULL,?,?)",
            ("effect", loser.player_id, "fixture-food", "catch-rarity-boost", "{}", now, now),
        )
        await session.execute(
            "INSERT INTO group_technique_effects VALUES(?,?,?, ?,5,5,'active',?,?)",
            ("tech", loser.scope.value, "lapse-blue", loser.player_id, now, now),
        )
        await session.execute("INSERT INTO player_six_star_progress VALUES(?,5,NULL,?,?)", (loser.player_id, now, now))
        await session.execute("INSERT INTO upgrades VALUES(?,'feed',5,?)", (loser.player_id, now))
    tables = (
        "armed_items",
        "item_inventory",
        "player_food_effects",
        "group_technique_effects",
        "player_six_star_progress",
    )
    before = {table: [tuple(row) for row in await w.db.fetch_all(f"SELECT * FROM {table}")] for table in tables}
    result = await w.send(section="loot", actor=loser)
    for table in tables:
        assert [tuple(row) for row in await w.db.fetch_all(f"SELECT * FROM {table}")] == before[table]
    snapshot = loads((await w.db.fetch_one("SELECT snapshot_json FROM battle_loot_deliveries"))[0])
    assert snapshot["weights"] == pytest.approx(loot_weights(level=1, feed=5, cloud=5, six_available=True))
    assert not await WeeklyCompetitionService(w.db, clock=w.clock).process_receipt(result.receipt)


@pytest.mark.parametrize("guard", GUARDS)
async def test_every_battle_guard_is_required(tmp_path, guard):
    db = PigCatcherDatabase(tmp_path / "guard.sqlite3")
    await db.open()
    async with db.transaction() as session:
        kind = (await session.fetch_one("SELECT type FROM sqlite_master WHERE name=?", (guard,)))[0]
        await session.execute(f'DROP {kind} "{guard}"')
    await db.close()
    with pytest.raises(MigrationError, match="约束"):
        await db.open()


async def test_schema38_migration_preserves_every_old_table(world, tmp_path):
    path = tmp_path / "legacy38.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)")
    for migration in MIGRATIONS:
        if migration.version > 38:
            break
        for sql in migration.statements:
            conn.execute(sql)
        conn.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, "test"))
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('schema_migrations','sqlite_sequence')"
        )
    ]
    original = {}
    for table in tables:
        rows = [tuple(r) for r in await world.db.fetch_all(f'SELECT * FROM "{table}" ORDER BY rowid')]
        original[table] = rows
        if rows:
            conn.executemany(f'INSERT INTO "{table}" VALUES(' + ",".join("?" for _ in rows[0]) + ")", rows)
    conn.execute("PRAGMA user_version=38")
    conn.commit()
    conn.close()
    db = PigCatcherDatabase(path)
    await db.open()
    try:
        assert await db.schema_version() == 40
        for table, rows in original.items():
            assert [tuple(r) for r in await db.fetch_all(f'SELECT * FROM "{table}" ORDER BY rowid')] == rows
        assert set(TABLES) <= {r[0] for r in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
        assert await db.fetch_all("PRAGMA foreign_key_check") == []
    finally:
        await db.close()
