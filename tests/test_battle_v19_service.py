"""公开战前形态流程、Schema 73 和旧对局续玩的服务边界验收。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from pig_catcher.config.model import CatchingSection
from pig_catcher.domain import battle
from pig_catcher.domain.battle_catalog import DANIYA_PIG_TEMPLATE_IDS, BattleError, fighter_form_moves
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.special_content import SUKUNA_PIG_TEMPLATE_ID
from pig_catcher.domain.xixi_battle_catalog import XIXI_PIG_TEMPLATE_IDS
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.services.battle import BattleService

from .test_battle import BattleWorld
from .test_battle import world as legacy_fixture
from .test_dispatch import NOW, seed_pigs
from .test_economy import _food_entry
from .test_gameplay import MutableClock, _database_with_catalog, _pig_entry


@pytest.fixture
async def legacy_world(tmp_path):
    async for w in legacy_fixture.__wrapped__(tmp_path):
        yield w


@pytest.fixture
async def route_world(tmp_path):
    group = "1092931381"
    food_id = "food-g1092931381-xixi-sun-star-cheese"
    food = _food_entry(6, group_id=group, template_suffix="xixi-pair")
    food.update(template_id=food_id, display_name="西西昭日星酪")
    entries = [
        _pig_entry(
            XIXI_PIG_TEMPLATE_IDS[0], rarity=6, display_name="西西猪", group_id=group, paired_food_template_id=food_id
        ),
        _pig_entry(SUKUNA_PIG_TEMPLATE_ID, rarity=5, display_name="宿傩猪"),
        _pig_entry(DANIYA_PIG_TEMPLATE_IDS[0], rarity=5, display_name="达妮娅猪"),
        food,
    ]
    db = await _database_with_catalog(tmp_path, entries, manifest_version=4)
    a = CommandIdentity(ScopeKey("qq", group), "route-stream", "200", "挑战者", "seed", "路线验收群")
    b = replace(a, user_id="201", display_name="应战者")
    for actor, template in (
        (a, XIXI_PIG_TEMPLATE_IDS[0]),
        (a, SUKUNA_PIG_TEMPLATE_ID),
        (b, DANIYA_PIG_TEMPLATE_IDS[0]),
    ):
        await seed_pigs(db, actor, template_id=template, count=1)
    clock = MutableClock(NOW)
    w = BattleWorld(
        db,
        clock,
        BattleService(
            db, clock=clock, seed_factory=lambda: "service-v19", catching=CatchingSection(cooldown_seconds=0)
        ),
        a,
        b,
    )
    await w.assign(a, "西西猪")
    await w.assign(b, "达妮娅猪")
    try:
        yield w
    finally:
        await db.close()


async def profile(w, actor=None):
    return dict(await w.db.fetch_one("SELECT * FROM battle_profiles WHERE player_id=?", ((actor or w.a).player_id,)))


async def test_public_route_requires_explicit_choice_and_prototype_is_unavailable(route_world):
    w = route_world
    assert (await profile(w))["battle_form_id"] == ""
    assert "尚未选择" in (await w.send()).view.text()
    with pytest.raises(BattleError, match="形态 西天帝"):
        await w.invite()
    with pytest.raises(BattleError, match="尚未设计"):
        await w.send("形态 原型")
    assert not await w.db.fetch_all("SELECT * FROM battle_matches")
    assert not await w.db.fetch_all("SELECT * FROM battle_daily_uses")
    assert (await profile(w))["battle_form_id"] == ""


async def test_public_route_persists_replays_and_same_choice_keeps_revision(route_world):
    w = route_world
    old = await profile(w)
    chosen = await w.send("形态 西天帝", mid="choose-route")
    current = await profile(w)
    assert current["battle_form_id"] == "xixi-celestial"
    assert current["revision"] == old["revision"] + 1
    assert "西天帝路线" in chosen.view.text()
    replay = await w.send("形态 西天帝", mid="choose-route")
    assert replay.receipt.receipt_id == chosen.receipt.receipt_id
    await w.send("形态 西天帝", mid="choose-same-again")
    assert await profile(w) == current
    assert (
        await w.db.fetch_one(
            "SELECT COUNT(*) FROM activity_facts WHERE player_id=? AND subevent_id='battle-form-selected'",
            (w.a.player_id,),
        )
    )[0] == 1
    await w.db.close()
    await w.db.open()
    restored_service = BattleService(w.db, clock=w.clock, seed_factory=lambda: "restored")
    restored = await w.send("形态 西天帝", mid="choose-route", service=restored_service)
    assert restored.receipt.receipt_id == chosen.receipt.receipt_id
    assert await profile(w) == current


async def test_pending_same_route_choice_preserves_snapshot_but_config_change_invalidates(route_world):
    w = route_world
    await w.send("形态 西天帝")
    await w.invite()
    before = await profile(w)
    await w.send("形态 西天帝")
    assert await profile(w) == before
    await w.send("器具 无")
    with pytest.raises(BattleError, match="设置发生变化"):
        await w.send("接受", "challenge", actor=w.b)
    assert (await w.match())["status"] == "pending"
    assert not await w.db.fetch_all("SELECT * FROM battle_daily_uses")
    assert not await w.db.fetch_all("SELECT * FROM asset_occupancies WHERE purpose='battle'")


async def test_active_match_locks_route_and_new_state_is_v19(route_world):
    w = route_world
    await w.send("形态 西天帝")
    await w.invite()
    await w.send("形态 西天帝")  # 同路线不使待应战快照失效
    await w.send("接受", "challenge", actor=w.b)
    match = await w.match()
    state = battle.loads(match["state_json"])
    assert match["definition_version"] == state["version"] == 19
    assert state["sides"][0]["xixi_form"] == "xixi-celestial"
    assert state["sides"][0]["snapshot"]["battle_form_id"] == "xixi-celestial"
    for text in ("形态 西天帝", "形态 原型", "设置 宿傩猪", "解除保护"):
        with pytest.raises(BattleError, match="正在对战"):
            await w.send(text)
    assert (await w.match())["state_json"] == match["state_json"]


async def test_retiring_or_replacing_fighter_clears_route(route_world):
    w = route_world
    await w.send("形态 西天帝")
    await w.send("解除保护")
    await w.send("确认")
    retired = await profile(w)
    assert retired["pig_instance_id"] is None and retired["battle_form_id"] == ""
    await w.assign(w.a, "西西猪")
    await w.send("形态 西天帝")
    await w.assign(w.a, "宿傩猪")
    assert (await profile(w))["battle_form_id"] == ""
    await w.assign(w.a, "西西猪")
    with pytest.raises(BattleError, match="形态 西天帝"):
        await w.invite()


async def legacy_match(w, version, *, active=False):
    """构造冻结旧版现场，保持真实服务生成的成员/邀请指纹和FK关系。"""
    await w.assign(w.b, "达妮娅猪")
    await w.invite()
    if active:
        await w.send("接受", "challenge", actor=w.b)
    match = await w.match()
    current = battle.loads(match["state_json"])
    state = battle.new_state([p["snapshot"] for p in current["sides"]], seed=match["random_seed"], version=version)
    state["status"] = "active" if active else "pending"
    async with w.db.transaction() as session:
        await session.execute(
            "UPDATE battle_matches SET definition_version=?,state_json=? WHERE battle_id=?",
            (version, battle.dumps(state), match["battle_id"]),
        )
    return state


async def test_accepting_pending_v18_uses_original_state_version_and_daniya_directory(legacy_world):
    w = legacy_world
    await legacy_match(w, 18)
    await w.send("接受", "challenge", actor=w.b)
    match = await w.match()
    state = battle.loads(match["state_json"])
    assert match["definition_version"] == state["version"] == 18
    assert state["sides"][1]["snapshot"]["fighter_id"] == "daniya"
    old_ids = {m.move_id for m in fighter_form_moves("daniya", state["sides"][1]["daniya_form"], 18)}
    assert "daniya-staging-virtual-particle" in old_ids
    assert "daniya-staging-curtain" not in old_ids
    assert "daniya_v19" not in state["sides"][1]


@pytest.mark.parametrize("version", [17, 18])
async def test_resumed_old_active_match_moves_keep_legacy_resources_and_catalog(legacy_world, version, monkeypatch):
    w = legacy_world
    state = await legacy_match(w, version, active=True)
    p = state["sides"][1]
    p["turn"].update(raw=1, effective=1, pending=1, done=False)
    old_moves = fighter_form_moves("daniya", p["daniya_form"], version)
    index = next(i for i, m in enumerate(old_moves) if m.move_id == "daniya-staging-virtual-particle")
    original = battle.choose

    def forced(seed, key, wheel, **kwargs):
        if ":1:move:" in key and ":nested" not in key:
            return index, 0
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", forced)
    match = await w.match()
    async with w.db.transaction() as session:
        await session.execute(
            "UPDATE battle_matches SET state_json=? WHERE battle_id=?", (battle.dumps(state), match["battle_id"])
        )
    await w.send(section="move", actor=w.b)
    saved = battle.loads((await w.match())["state_json"])
    event = saved["sides"][1]["turn"]["events"][0]
    assert saved["version"] == version and event["move_id"] == "daniya-staging-virtual-particle"
    assert event["gain"] == 12
    assert not event.get("daniya_v19") and not event.get("xixi")
    assert "daniya_permanent_domain_units" not in saved["sides"][1]
    assert "xixi_hextech" not in saved["sides"][1]
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM battle_moves"))[0] == 1


def quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def populated_schema72(path, source_path):
    """先真实执行1..72迁移，再复制隔离fixture数据；不是修改73版本戳冒充72。"""
    with sqlite3.connect(path) as connection, sqlite3.connect(source_path) as source:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)"
        )
        for migration in MIGRATIONS:
            if migration.version > 72:
                break
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, "fixture")
            )
        connection.execute("PRAGMA user_version=72")
        # 载入已有合法快照时暂停行级写入触发器，否则历史completed配额无法重放。
        triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
        for name, _sql in triggers:
            connection.execute(f"DROP TRIGGER {quote_identifier(name)}")
        names = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations'"
            )
        ]
        for table in names:
            quoted = quote_identifier(table)
            columns = [r[1] for r in connection.execute(f"PRAGMA table_info({quoted})")]
            column_sql = ",".join(quote_identifier(c) for c in columns)
            rows = source.execute(f"SELECT {column_sql} FROM {quoted}").fetchall()
            if rows:
                placeholders = ",".join("?" for _ in columns)
                connection.executemany(f"INSERT INTO {quoted}({column_sql}) VALUES({placeholders})", rows)
        for _name, sql in triggers:
            connection.execute(sql)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


async def test_populated_schema72_upgrade_preserves_state_and_supports_v19_loot(legacy_world, tmp_path):
    w = legacy_world
    await legacy_match(w, 18, active=True)
    old_match = await w.match()
    async with w.db.transaction() as session:
        await session.execute(
            "INSERT INTO battle_matches(battle_id,scope_id,initiator_id,opponent_id,status,definition_version,"
            "random_seed,state_json,invitation_json,expires_ms,created_ms,winner_id) "
            "VALUES(?,?,?,?,'completed',18,?,?,?,?,?,?)",
            ("old-loot", w.a.scope.value, w.a.player_id, w.b.player_id, "seed", "{}", "[]", 123, 123, w.a.player_id),
        )
        await session.execute(
            "INSERT INTO battle_training(pig_instance_id,level) VALUES(?,2)", ((await profile(w))["pig_instance_id"],)
        )
        await session.execute(
            "INSERT INTO battle_loot(battle_id,actor_id,recipient_id,scope_id,used,created_ms,total_uses) "
            "VALUES(?,?,?,?,1,?,3)",
            ("old-loot", w.b.player_id, w.a.player_id, w.a.scope.value, 123),
        )
    path = tmp_path / "populated72.sqlite3"
    populated_schema72(path, w.db.path)
    with sqlite3.connect(path) as old:
        preserved = {
            table: old.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in (
                "battle_matches",
                "battle_training",
                "battle_loot",
                "asset_occupancies",
                "battle_daily_uses",
                "command_receipts",
                "pig_instances",
            )
        }
        old_profiles = old.execute("SELECT * FROM battle_profiles ORDER BY player_id").fetchall()
        assert old.execute("PRAGMA user_version").fetchone()[0] == 72
    upgraded = PigCatcherDatabase(path)
    await upgraded.open()
    try:
        assert await upgraded.schema_version() == 73
        for table, expected in preserved.items():
            assert [tuple(r) for r in await upgraded.fetch_all(f"SELECT * FROM {table} ORDER BY rowid")] == expected
        upgraded_profiles = await upgraded.fetch_all("SELECT * FROM battle_profiles ORDER BY player_id")
        assert [tuple(r)[:-1] for r in upgraded_profiles] == old_profiles
        assert all(r["battle_form_id"] == "" for r in upgraded_profiles)
        assert (
            await upgraded.fetch_one(
                "SELECT state_json FROM battle_matches WHERE battle_id=?", (old_match["battle_id"],)
            )
        )[0] == old_match["state_json"]
        async with upgraded.transaction() as session:
            await session.execute(
                "INSERT INTO battle_matches(battle_id,scope_id,initiator_id,opponent_id,status,definition_version,"
                "random_seed,state_json,invitation_json,expires_ms,created_ms,winner_id) "
                "VALUES(?,?,?,?,'completed',19,?,?,?,?,?,?)",
                ("new-v19", w.a.scope.value, w.a.player_id, w.b.player_id, "seed", "{}", "[]", 999, 456, w.a.player_id),
            )
            await session.execute(
                "INSERT INTO battle_loot(battle_id,actor_id,recipient_id,scope_id,created_ms,total_uses) "
                "VALUES(?,?,?,?,?,3)",
                ("new-v19", w.b.player_id, w.a.player_id, w.a.scope.value, 456),
            )
        assert (await upgraded.fetch_one("SELECT total_uses FROM battle_loot WHERE battle_id='new-v19'"))[0] == 3
        with pytest.raises(sqlite3.IntegrityError, match="次数"):
            async with upgraded.transaction() as session:
                await session.execute(
                    "INSERT INTO battle_matches(battle_id,scope_id,initiator_id,opponent_id,status,definition_version,"
                    "random_seed,state_json,invitation_json,expires_ms,created_ms,winner_id) "
                    "VALUES(?,?,?,?,'completed',19,?,?,?,?,?,?)",
                    (
                        "bad-v19",
                        w.a.scope.value,
                        w.a.player_id,
                        w.b.player_id,
                        "seed",
                        "{}",
                        "[]",
                        999,
                        789,
                        w.a.player_id,
                    ),
                )
                await session.execute(
                    "INSERT INTO battle_loot(battle_id,actor_id,recipient_id,scope_id,created_ms,total_uses) "
                    "VALUES(?,?,?,?,?,5)",
                    ("bad-v19", w.b.player_id, w.a.player_id, w.a.scope.value, 789),
                )
        assert await upgraded.fetch_all("PRAGMA foreign_key_check") == []
        assert await upgraded.integrity_check() == ("ok",)
    finally:
        await upgraded.close()


async def test_new_match_after_legacy_invitation_uses_v19(legacy_world):
    w = legacy_world
    await legacy_match(w, 18)
    await w.send("取消", "challenge")
    w.clock.value += timedelta(seconds=61)
    await w.invite()
    match = await w.match()
    assert match["definition_version"] == battle.loads(match["state_json"])["version"] == 19
