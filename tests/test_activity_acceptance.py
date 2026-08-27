"""Isolated fourth-round gates: migrations, recovery, scope, budgets and delivery."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from pig_catcher.commands.tour import TourRequest
from pig_catcher.domain.achievements import ACHIEVEMENT_BY_ID
from pig_catcher.domain.activity_achievements import ACTIVITY_IDS, ACTIVITY_REWARDS, FIXED_SETS
from pig_catcher.domain.activity_progress import reduce_fact
from pig_catcher.domain.errors import MigrationError
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.migrations.v0040_activity_achievements import GUARDS, TABLES
from pig_catcher.infrastructure.repositories.dispatch import DispatchRepository
from pig_catcher.infrastructure.repositories.economy import EconomyRepository
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.infrastructure.repositories.materials import MaterialRepository
from pig_catcher.services.achievements import AchievementService
from pig_catcher.services.activity_achievements import ActivityAchievements

from .helpers import build_message, create_test_plugin
from .test_activity_achievements import _RUNTIME, complete_state, trip
from .test_battle import world as _battle_fixture
from .test_dispatch import world as _dispatch_fixture
from .test_plugin import _command_kwargs
from .test_tour import world as _tour_fixture

battle_world = _battle_fixture
dispatch_world = _dispatch_fixture
tour_world = _tour_fixture


async def legacy_fixture(current, destination, version):
    """Build a one-time synthetic old database; never point this at live data."""
    connection = sqlite3.connect(destination)
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)")
    for migration in MIGRATIONS:
        if migration.version > version:
            break
        for sql in migration.statements:
            connection.execute(sql)
        connection.execute(
            "INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, "fixture")
        )
    # A faithful copy must not replay currency/counter triggers while copying
    # their already-materialized projections. Restore every original guard.
    triggers = list(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
    for name, _ in triggers:
        connection.execute(f'DROP TRIGGER "{name}"')
    original = {}
    tables = list(
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('schema_migrations','sqlite_sequence')"
        )
    )
    for (table,) in tables:
        columns = [r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')]
        names = ",".join(f'"{column}"' for column in columns)
        rows = [tuple(r) for r in await current.fetch_all(f'SELECT {names} FROM "{table}" ORDER BY rowid')]
        original[table] = rows
        if rows:
            connection.executemany(f'INSERT INTO "{table}" VALUES(' + ",".join("?" for _ in columns) + ")", rows)
    for _, sql in triggers:
        connection.execute(sql)
    connection.execute(f"PRAGMA user_version={version}")
    assert list(connection.execute("PRAGMA foreign_key_check")) == []
    connection.commit()
    connection.close()
    return original


@pytest.mark.parametrize("version", [34, 35, 36, 37, 38, 39])
async def test_all_legacy_baselines_migrate_without_rewriting_any_existing_row(tour_world, tmp_path, version):
    w = tour_world
    await w.fund()
    await w.send("一键")
    await w.send("确认")
    path = tmp_path / f"legacy{version}.sqlite3"
    original = await legacy_fixture(w.db, path, version)
    before = path.read_bytes()
    backup = tmp_path / f"rollback{version}.sqlite3"
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    migrated = PigCatcherDatabase(path)
    await migrated.open()
    try:
        assert await migrated.schema_version() == 40
        assert await migrated.integrity_check() == ("ok",)
        assert await migrated.fetch_all("PRAGMA foreign_key_check") == []
        for table, rows in original.items():
            assert [tuple(r) for r in await migrated.fetch_all(f'SELECT * FROM "{table}" ORDER BY rowid')] == rows
        queue = await migrated.fetch_all("SELECT * FROM achievement_activity_queue")
        assert all(row["historical"] == 1 and row["processed_at"] is None for row in queue)
        if version >= 38:
            service = AchievementService(migrated, clock=w.clock)
            await service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id="migration-check")
            assert (
                await service.notification_players(scope_id=w.identity.scope.value, receipt_id="migration-check") == ()
            )
            assert await service.claim_backfill_summary(player_id=w.identity.player_id) is not None
            assert await service.claim_backfill_summary(player_id=w.identity.player_id) is None
    finally:
        await migrated.close()
    # Rollback is opening the pre-upgrade backup with the old build, never a
    # destructive in-place downgrade of a database that has new transactions.
    with sqlite3.connect(backup) as restored:
        assert restored.execute("PRAGMA user_version").fetchone()[0] == version
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table, rows in original.items():
            assert list(restored.execute(f'SELECT * FROM "{table}" ORDER BY rowid')) == rows
    assert before  # source was a real nonempty synthetic snapshot


@pytest.mark.parametrize("name", TABLES + GUARDS)
async def test_current_schema_rejects_missing_activity_guards(tour_world, tmp_path, name):
    path = tmp_path / f"missing-{name}.sqlite3"
    await tour_world.db.backup_to(path)
    with sqlite3.connect(path) as connection:
        kind = connection.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        connection.execute(f'DROP {kind.upper()} "{name}"')
    broken = PigCatcherDatabase(path)
    with pytest.raises(MigrationError):
        await broken.open()


async def test_fact_version_fails_closed_without_consuming_pending_evidence(dispatch_world):
    w = dispatch_world
    async with w.db.transaction() as session:
        await session.execute(
            "INSERT INTO activity_facts VALUES(?,?,?,?,?,?,?,?,?)",
            ("future", w.identity.player_id, w.identity.scope.value, "dispatch", "s", "completed", 99, 1, "{}"),
        )
    with pytest.raises(ValueError, match="version"):
        await AchievementService(w.db, clock=w.clock).process_activity_facts(
            scope_id=w.identity.scope.value, receipt_id="future-fact"
        )
    assert (await w.db.fetch_one("SELECT processed_at FROM achievement_activity_queue WHERE fact_key='future'"))[
        0
    ] is None
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_unlocks"))[0] == 0


async def test_outbox_restart_and_backup_restore_never_duplicate_rewards(tour_world, tmp_path):
    w = tour_world
    await w.send("一键")
    receipt = (await w.send("确认")).receipt
    before = tmp_path / "before-rewards.sqlite3"
    await w.db.backup_to(before)
    service = AchievementService(w.db, clock=w.clock)
    original = await service.process_receipt(receipt)
    restored = PigCatcherDatabase(before)
    await restored.open()
    try:
        replay = AchievementService(restored, clock=w.clock)
        assert {u.achievement_id for u in await replay.process_receipt(receipt)} == {u.achievement_id for u in original}
        assert await replay.process_receipt(receipt) == ()
        for table, columns in (
            ("achievement_reward_inventory", "player_id,reward_type,reward_id,quantity"),
            ("material_balances", "player_id,material_id,quantity,remainder_units"),
            ("achievement_profiles", "player_id,achievement_points"),
        ):
            assert [tuple(r) for r in await restored.fetch_all(f"SELECT {columns} FROM {table} ORDER BY 1,2")] == [
                tuple(r) for r in await w.db.fetch_all(f"SELECT {columns} FROM {table} ORDER BY 1,2")
            ]
    finally:
        await restored.close()


async def test_four_actual_scopes_never_share_progress_materials_or_coupon_stock(dispatch_world):
    w = dispatch_world
    scopes = (
        ScopeKey("qq", "1092931381"),
        ScopeKey("qq-official", "5E5854406D0297D6FEAE696A13E3A339"),
        ScopeKey("qq", "237716658"),
        ScopeKey("qq-official", "9EA2810F378FBD7DC3219C56CEAB3520"),
    )
    actors = [
        replace(w.identity, scope=scope, user_id="same-fixture-user", stream_id=f"scope-{i}")
        for i, scope in enumerate(scopes)
    ]
    service = AchievementService(w.db, clock=w.clock)
    async with w.db.transaction() as session:
        for actor in actors:
            await FrameworkRepository().touch_identity(session, identity=actor, now=w.clock.now().isoformat())
            await DispatchRepository().fact(
                session,
                player_id=actor.player_id,
                scope_id=actor.scope.value,
                source_id="same-trip-id",
                subevent="completed",
                at_ms=1,
                payload=trip(),
            )
    await service.process_activity_facts(scope_id=actors[0].scope.value, receipt_id="same-receipt")
    assert (await w.db.fetch_one("SELECT COUNT(DISTINCT player_id) FROM achievement_unlocks"))[0] == 1
    for actor in actors[1:]:
        assert await w.db.fetch_one("SELECT * FROM achievement_profiles WHERE player_id=?", (actor.player_id,)) is None
        await service.process_activity_facts(scope_id=actor.scope.value, receipt_id="same-receipt")
    rows = await w.db.fetch_all(
        "SELECT player_id FROM achievement_unlocks WHERE achievement_id='dispatch-first-return'"
    )
    assert {r[0] for r in rows} == {a.player_id for a in actors}
    async with w.db.transaction() as session:
        assert await MaterialRepository().reconcile(session) == []
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_activity_queue WHERE processed_at IS NULL"))[0] == 0


async def test_parallel_dispatch_hours_use_union_and_recall_cannot_fake_completion(dispatch_world):
    w = dispatch_world
    await w.team()
    await w.start(hours=24)
    await w.advance(24)
    await w.start(hours=8, slot=1)
    await w.team(slot=2)
    await w.start(hours=8, slot=2)
    await w.advance(4)
    await w.send("召回 2")
    await w.send("确认")
    await w.advance(4)
    await AchievementService(w.db, clock=w.clock).process_activity_facts(
        scope_id=w.identity.scope.value, receipt_id="returns"
    )
    state = json.loads((await w.db.fetch_one("SELECT state_json FROM achievement_activity_state"))[0])
    assert state["values"]["dispatch.effective_hours"] == 32
    assert state["values"]["dispatch.completed_trips"] == 2
    assert state["values"]["dispatch.low_full_team_trips"] == 2


async def test_three_real_joint_partners_credit_both_players_only_after_acceptance(tour_world):
    w = tour_world
    service = AchievementService(w.db, clock=w.clock)
    for index in range(3):
        if index:
            w.clock.value += timedelta(days=1)
        partner = replace(w.identity, user_id=f"partner-{index}", display_name=f"伙伴{index}")
        await w.form(partner)
        await w.service.execute(
            replace(w.identity, message_id=uuid4().hex),
            TourRequest("joint_invite", {"target_user_id": partner.user_id}),
        )
        await service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id=f"pending-{index}")
        before = await w.db.fetch_one(
            "SELECT state_json FROM achievement_activity_state WHERE player_id=?", (w.identity.player_id,)
        )
        if before:
            assert len(json.loads(before[0]).get("sets", {}).get("tour.coop_partners", [])) == index
        accepted = await w.send("接受", "joint", identity=partner)
        await service.process_receipt(accepted.receipt)
        partner_state = json.loads(
            (
                await w.db.fetch_one(
                    "SELECT state_json FROM achievement_activity_state WHERE player_id=?", (partner.player_id,)
                )
            )[0]
        )
        assert partner_state["sets"]["tour.coop_partners"] == [w.identity.player_id]
    own = json.loads(
        (
            await w.db.fetch_one(
                "SELECT state_json FROM achievement_activity_state WHERE player_id=?", (w.identity.player_id,)
            )
        )[0]
    )
    assert len(own["sets"]["tour.coop_partners"]) == 3


async def test_full_personal_training_uses_real_five_level_costs(battle_world):
    w = battle_world
    await w.fund()
    for _ in range(5):
        await w.send("强化")
        await w.send("确认")
    service = AchievementService(w.db, clock=w.clock)
    await service.process_activity_facts(scope_id=w.a.scope.value, receipt_id="training")
    state = json.loads(
        (await w.db.fetch_one("SELECT state_json FROM achievement_activity_state WHERE player_id=?", (w.a.player_id,)))[
            0
        ]
    )
    assert state["values"]["battle.own_full_training"] == 1
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM battle_upgrades"))[0] == 5
    assert await w.db.fetch_one("SELECT * FROM achievement_unlocks WHERE player_id=?", (w.b.player_id,)) is None
    async with w.db.transaction() as session:
        assert await MaterialRepository().reconcile(session) == []


async def test_activity_reward_coin_chain_settles_immediately(dispatch_world):
    w = dispatch_world
    async with w.db.transaction() as session:
        await EconomyRepository().apply_currency_change(
            session,
            player_id=w.identity.player_id,
            scope_id=w.identity.scope.value,
            amount=89950,
            reason_code="test-ordinary",
            reason_text="fixture",
            source_object_type="test",
            source_object_id="seed",
            ledger_entry_id="chain-seed",
            idempotency_key="chain-seed",
            now=w.clock.now().isoformat(),
        )
        await DispatchRepository().fact(
            session,
            player_id=w.identity.player_id,
            scope_id=w.identity.scope.value,
            source_id="trip",
            subevent="completed",
            at_ms=1,
            payload=trip(),
        )
    service = AchievementService(w.db, clock=w.clock)
    unlocks = await service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id="chain")
    assert {"dispatch-first-return", "earned-coins-100000"} <= {u.achievement_id for u in unlocks}
    assert await service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id="chain-again") == ()


async def test_exact_pack_budget_and_new_graduation_does_not_expand_legacy49(dispatch_world):
    w = dispatch_world
    service = AchievementService(w.db, clock=w.clock)
    await service.initialize()
    state = {"values": {}, "sets": {}}
    for entry in _RUNTIME["entries"]:
        proof = complete_state(entry["code"])
        for key, value in proof.get("values", {}).items():
            state["values"][key] = max(state["values"].get(key, 0), value)
        for key, value in proof.get("sets", {}).items():
            state["sets"][key] = sorted(set(state["sets"].get(key, ())) | set(value))
    async with w.db.transaction() as session:
        await service.repository.ensure_profile(session, player_id=w.identity.player_id, now="now")
        await service.repository.insert_event(
            session,
            event_id="budget",
            receipt_id="budget",
            player_id=w.identity.player_id,
            scope_id=w.identity.scope.value,
            event_type="fixture",
            payload_json="{}",
            now="now",
        )
        unlocks = await ActivityAchievements(service).settle(
            session, w.identity.player_id, w.identity.scope.value, state, "budget", "budget", "now"
        )
    assert ACTIVITY_IDS <= {u.achievement_id for u in unlocks}
    assert sum(ACHIEVEMENT_BY_ID[aid].points for aid in ACTIVITY_IDS) == 1665
    coins = await w.db.fetch_all(
        "SELECT amount,source_object_id FROM currency_ledger WHERE reason_code='achievement-reward'"
    )
    assert sum(r[0] for r in coins if r[1] in {"achievement:" + aid for aid in ACTIVITY_IDS}) == 68900
    assert (
        await w.db.fetch_one(
            "SELECT COUNT(*) FROM achievement_unlocks WHERE achievement_id IN ("
            + ",".join("?" for _ in FIXED_SETS["three-systems-public-v1"])
            + ")",
            tuple(FIXED_SETS["three-systems-public-v1"]),
        )
    )[0] == 30
    assert (
        await w.db.fetch_one("SELECT * FROM achievement_reward_inventory WHERE reward_id='regular-five-star-memorial'")
        is None
    )
    expected = {
        "materials-choice": 372,
        "training-choice": 112,
        "dispatch-luggage": 8,
        "dispatch-bill": 6,
        "training-rebate": 2,
        "tour-date": 1,
        "tour-steady-stage": 3,
        "dispatch-story": 3,
        "tour-encore-photo": 5,
        "battle-banner": 3,
    }
    for rid, amount in expected.items():
        assert (await w.db.fetch_one("SELECT quantity FROM achievement_reward_inventory WHERE reward_id=?", (rid,)))[
            0
        ] == amount
    assert sum(d["kind"] in {"title", "frame", "badge"} for d in ACTIVITY_REWARDS.values()) == 38
    async with w.db.transaction() as session:
        assert await MaterialRepository().reconcile(session) == []


async def test_queue_is_bounded_indexed_and_small_projection_not_full_payload_copy(dispatch_world):
    w = dispatch_world
    async with w.db.transaction() as session:
        for index in range(1030):
            await DispatchRepository().fact(
                session,
                player_id=w.identity.player_id,
                scope_id=w.identity.scope.value,
                source_id="many",
                subevent=f"note:{index}",
                at_ms=index,
                payload={"ignored": "x" * 100},
            )
    service = AchievementService(w.db, clock=w.clock)
    await service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id="bounded1")
    remaining = (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_activity_queue WHERE processed_at IS NULL"))[0]
    assert 774 <= remaining <= 966  # 64..256 facts, bounded even on slower machines
    for index in range(17):
        await service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id=f"bounded-next-{index}")
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_activity_queue WHERE processed_at IS NULL"))[0] == 0
    assert len((await w.db.fetch_one("SELECT state_json FROM achievement_activity_state"))[0]) < 100
    plan = await w.db.fetch_all(
        "EXPLAIN QUERY PLAN SELECT * FROM achievement_activity_queue "
        "WHERE scope_id=? AND processed_at IS NULL ORDER BY sequence LIMIT 256",
        (w.identity.scope.value,),
    )
    assert any("idx_achievement_activity_pending" in row[3] for row in plan)


async def test_query_catches_up_then_sends_one_merged_image_without_repeat(tmp_path):
    plugin, context = await create_test_plugin(tmp_path, config_updates={"features": {"achievements_enabled": True}})
    message = build_message(message_id="query-notification")
    identity = CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", "验收群友", "query-notification", "群")
    async with plugin.database.transaction() as session:
        await FrameworkRepository().touch_identity(session, identity=identity, now="2026-08-28T00:00:00+00:00")
        await DispatchRepository().fact(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            source_id="trip",
            subevent="completed",
            at_ms=1,
            payload=trip(),
        )
    try:
        assert (
            await plugin.handle_achievements(stream_id=identity.stream_id, **_command_kwargs(message, arguments=""))
        )[0]
        assert len(context.send.images) == 2
        assert (
            await plugin.database.fetch_one(
                "SELECT notification_status FROM achievement_unlocks WHERE achievement_id='dispatch-first-return'"
            )
        )[0] == "sent"
        assert (
            await plugin.handle_achievements(stream_id=identity.stream_id, **_command_kwargs(message, arguments=""))
        )[0]
        assert len(context.send.images) == 3  # repeated query is allowed, repeated unlock is not
    finally:
        await plugin.on_unload()


def test_future_fact_version_does_not_mutate_reducer_state():
    state = {}
    with pytest.raises(ValueError):
        reduce_fact(state, {"source_type": "tour", "definition_version": 99}, {})
    assert state == {}


def test_large_unlock_batch_remains_one_bounded_summary():
    from pig_catcher.domain.achievements import AchievementUnlock
    from pig_catcher.rendering.adapters import achievement_unlock_view
    from pig_catcher.services.achievements import format_achievement_unlocks

    unlocks = tuple(
        AchievementUnlock(d.achievement_id, d.name, d.tier, d.points, d.rewards, "now")
        for aid in sorted(ACTIVITY_IDS)
        for d in [ACHIEVEMENT_BY_ID[aid]]
    )
    view = achievement_unlock_view("群友", unlocks)
    assert len(view.entries) == 8 and view.additional_count == 40 and view.total_points == 1665
    assert "另有40项" in format_achievement_unlocks(unlocks)
    assert len(format_achievement_unlocks(unlocks)) < 1600


async def test_every_new_cosmetic_is_equipable_after_earning(dispatch_world):
    w = dispatch_world
    service = AchievementService(w.db, clock=w.clock)
    await service.initialize()
    async with w.db.transaction() as session:
        await service.repository.ensure_profile(session, player_id=w.identity.player_id, now="now")
        await service.repository.insert_event(
            session,
            event_id="cosmetics",
            receipt_id="cosmetics",
            player_id=w.identity.player_id,
            scope_id=w.identity.scope.value,
            event_type="fixture",
            payload_json="{}",
            now="now",
        )
        for aid in ACTIVITY_IDS:
            definition = ACHIEVEMENT_BY_ID[aid]
            await service.repository.upsert_progress(
                session,
                player_id=w.identity.player_id,
                achievement_id=aid,
                definition_version=1,
                progress_value=definition.condition.target,
                state_json="{}",
                unlocked_at="now",
                now="now",
            )
            await service.repository.insert_unlock(
                session,
                unlock_id=aid,
                player_id=w.identity.player_id,
                scope_id=w.identity.scope.value,
                achievement_id=aid,
                definition_version=1,
                source_event_id="cosmetics",
                source_receipt_id="cosmetics",
                points_awarded=definition.points,
                rewards_json="[]",
                notification_status="sent",
                now="now",
            )
            for r in definition.rewards:
                if r.reward_type in {"title", "frame", "badge"}:
                    await service.repository.grant_reward(
                        session,
                        player_id=w.identity.player_id,
                        reward_type=r.reward_type,
                        reward_id=r.reward_id,
                        quantity=1,
                        now="now",
                    )
    equipped = 0
    for aid in ACTIVITY_IDS:
        definition = ACHIEVEMENT_BY_ID[aid]
        cosmetic_rewards = [r for r in definition.rewards if r.reward_type in {"title", "frame", "badge"}]
        if cosmetic_rewards:
            assert await service.equip_cosmetics_by_achievement(w.identity, definition.name)
            cosmetic = await service.cosmetics_for_player(w.identity.player_id)
            for r in cosmetic_rewards:
                assert {"title": cosmetic.title_id, "frame": cosmetic.frame_id, "badge": cosmetic.badge_name}[
                    r.reward_type
                ] == (r.reward_id if r.reward_type == "frame" else ACTIVITY_REWARDS[r.reward_id]["name"])
            equipped += len(cosmetic_rewards)
    assert equipped == 38
