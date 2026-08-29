"""隔离SQLite下完整对战、资源账本、保护、重启、并发及战利品集成验收。"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pig_catcher.commands.battle import BattleRequest, parse_battle_request
from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.battle import loads
from pig_catcher.domain.battle_catalog import ACTION_TTL_MS, BattleError
from pig_catcher.domain.dispatch import MATERIAL_SCALE
from pig_catcher.domain.errors import (
    AssetStateConflictError,
    CatchCooldownError,
    DailyCatchLimitError,
    ReceiptConflictError,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.special_content import GOJO_PIG_TEMPLATE_ID, SUKUNA_PIG_TEMPLATE_ID
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.repositories.activity_locks import require_unoccupied
from pig_catcher.infrastructure.repositories.dispatch import iso_ms, timestamp_ms
from pig_catcher.infrastructure.repositories.economy import EconomyRepository
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.infrastructure.repositories.materials import MaterialRepository
from pig_catcher.infrastructure.repositories.restrictions import GIFT_TRANSFER_BAN, RestrictionRepository
from pig_catcher.services.administration import AdministrationService
from pig_catcher.services.battle import BattleService
from pig_catcher.services.gameplay import GameplayService

from .test_dispatch import NOW, seed_pigs
from .test_economy import _food_entry
from .test_gameplay import MutableClock, _database_with_catalog, _pig_entry


@dataclass
class BattleWorld:
    db: PigCatcherDatabase
    clock: MutableClock
    service: BattleService
    a: CommandIdentity
    b: CommandIdentity

    async def send(self, text="", section="profile", *, actor=None, mid=None, service=None):
        identity = replace(actor or self.a, message_id=mid or uuid4().hex)
        return await (service or self.service).execute(identity, parse_battle_request(text, section=section))

    async def invite(self, *, actor=None, target=None, mid=None, service=None):
        return await (service or self.service).execute(
            replace(actor or self.a, message_id=mid or uuid4().hex),
            BattleRequest("invite", {"target_user_id": (target or self.b).user_id}),
        )

    async def assign(self, actor=None, name="宿傩猪"):
        await self.send("设置 " + name, actor=actor)
        await self.send("确认", actor=actor)

    async def start(self):
        await self.invite()
        return await self.send("接受", "challenge", actor=self.b)

    async def match(self):
        return dict(await self.db.fetch_one("SELECT * FROM battle_matches ORDER BY sequence DESC LIMIT 1"))

    async def fight(self, *, already_started=False):
        if not already_started:
            await self.start()
        for _ in range(200):
            match = await self.match()
            state = loads(match["state_json"])
            if state["status"] == "completed":
                return match
            for index, actor in enumerate((self.a, self.b)):
                match = await self.match()
                current = loads(match["state_json"])
                if current["status"] != "active" or current["round"] != state["round"]:
                    break
                turn = current["sides"][index]["turn"]
                if turn["raw"] is None:
                    await self.send(section="count", actor=actor)
                elif not turn["done"]:
                    await self.send(section="move", actor=actor)
                elif all(item["turn"]["done"] for item in current["sides"]) and not turn.get("ready", False):
                    await self.send(section="ready", actor=actor)
        raise AssertionError("fixture match did not end in 200 actions")

    async def fund(self, actor=None):
        identity = actor or self.a
        async with self.db.transaction() as session:
            await EconomyRepository().apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=100000,
                reason_code="test",
                reason_text="fixture",
                source_object_type="test",
                source_object_id=uuid4().hex,
                ledger_entry_id=uuid4().hex,
                idempotency_key=uuid4().hex,
                now=iso_ms(timestamp_ms(self.clock.now())),
            )
            for material in ("training-ore", "machine-parts", "agility-fiber", "travel-supplies"):
                await MaterialRepository().change(
                    session,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                    material_id=material,
                    delta_units=3000 * MATERIAL_SCALE,
                    source_kind="dispatch-base",
                    source_id="offline-natural",
                    entry_key=uuid4().hex,
                    now=iso_ms(timestamp_ms(self.clock.now())),
                )


@pytest.fixture
async def world(tmp_path: Path):
    entries = [_pig_entry(f"star-{star}", rarity=star, group_id="100" if star == 6 else None) for star in range(1, 7)]
    entries.extend(
        [
            _pig_entry(SUKUNA_PIG_TEMPLATE_ID, rarity=5, display_name="宿傩猪"),
            _pig_entry(GOJO_PIG_TEMPLATE_ID, rarity=5, display_name="五条猪"),
        ]
    )
    db = await _database_with_catalog(tmp_path, [*entries, _food_entry(5)])
    a = CommandIdentity(ScopeKey("qq", "100"), "s100", "200", "挑战者", "fixture", "对战测试群")
    b = replace(a, user_id="201", display_name="应战者")
    for person, template in ((a, SUKUNA_PIG_TEMPLATE_ID), (b, GOJO_PIG_TEMPLATE_ID)):
        await seed_pigs(db, person, template_id=template, count=2)
    clock = MutableClock(NOW)
    w = BattleWorld(
        db,
        clock,
        BattleService(
            db, clock=clock, seed_factory=lambda: "battle-integration", catching=CatchingSection(cooldown_seconds=0)
        ),
        a,
        b,
    )
    await w.assign(a)
    await w.assign(b, "五条猪")
    try:
        yield w
    finally:
        await db.close()


async def test_full_battle_and_five_normal_catches_auto_deliver_to_winner(world):
    match = await world.fight()
    assert match["status"] == "completed"
    assert len(await world.db.fetch_all("SELECT * FROM battle_daily_uses")) == 2
    assert not await world.db.fetch_all("SELECT * FROM asset_occupancies WHERE purpose='battle'")
    state = loads(match["state_json"])
    winner, loser = (world.a, world.b) if state["winner"] == 0 else (world.b, world.a)
    assert f"{winner.display_name} 获胜" in (await world.send(section="status")).view.banner
    assert (await world.db.fetch_one("SELECT recipient_id FROM battle_loot"))[0] == winner.player_id
    for ordinal in range(1, 6):
        identity = replace(loser, message_id=f"loot-{ordinal}")
        result = await world.service.execute_pending_loot(identity)
        assert result is not None
        assert f"{5 - ordinal}/5" in result.view.text()
        assert "最终概率" in result.view.text() and winner.display_name in result.view.text()
        repeated = await world.service.execute_pending_loot(identity)
        assert repeated is not None
        assert repeated.receipt.receipt_id == result.receipt.receipt_id
    deliveries = await world.db.fetch_all(
        "SELECT p.* FROM battle_loot_deliveries d JOIN pig_instances p USING(pig_instance_id)"
    )
    assert len(deliveries) == 5 and all(row["owner_player_id"] == winner.player_id for row in deliveries)
    assert (await world.db.fetch_one("SELECT used FROM battle_loot"))[0] == 5
    catch_receipts = await world.db.fetch_one(
        "SELECT COUNT(*) FROM command_receipts WHERE command_name='pig-catcher.catch'"
    )
    assert catch_receipts[0] == 5
    assert (await world.db.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == 0
    assert (await world.db.fetch_one("SELECT SUM(experience) FROM players"))[0] == 0
    facts = await world.db.fetch_all(
        "SELECT * FROM activity_facts WHERE source_type='battle' AND subevent_id LIKE 'loot:%'"
    )
    assert len(facts) == 10 and all(loads(row["payload_json"])["battle_id"] == match["battle_id"] for row in facts)
    assert await world.service.execute_pending_loot(replace(loser, message_id="loot-sixth")) is None
    with pytest.raises(BattleError, match="没有待交付"):
        await world.send(section="loot", actor=loser)
    assert await world.db.fetch_all("PRAGMA foreign_key_check") == []
    assert [r[0] for r in await world.db.fetch_all("PRAGMA integrity_check")] == ["ok"]


async def test_auto_loot_does_not_replace_an_existing_normal_catch_receipt(world):
    normal = GameplayService(world.db, world.service.catching, clock=world.clock)
    before: dict[str, str] = {}
    for actor in (world.a, world.b):
        identity = replace(actor, message_id="ordinary-before-battle")
        before[actor.player_id] = (await normal.catch(identity)).receipt.receipt_id
    await world.fight()
    grant = await world.db.fetch_one("SELECT * FROM battle_loot")
    loser = world.a if grant["actor_id"] == world.a.player_id else world.b
    identity = replace(loser, message_id="ordinary-before-battle")
    assert await world.service.execute_pending_loot(identity) is None
    assert (await normal.catch(identity)).receipt.receipt_id == before[loser.player_id]
    assert (await world.db.fetch_one("SELECT used FROM battle_loot"))[0] == 0


async def test_round_waits_for_both_win_declarations_and_actions_stay_with_fighter(world):
    await world.start()
    for side, actor in enumerate((world.a, world.b)):
        count_result = await world.send(section="count", actor=actor)
        assert count_result.view.fighters[side].count_wheel is not None
        assert not count_result.view.wheels
        while True:
            state = loads((await world.match())["state_json"])
            if state["sides"][side]["turn"]["done"]:
                break
            move_result = await world.send(section="move", actor=actor)
            assert move_result.view.fighters[side].move_wheel is not None
            assert move_result.view.fighters[side].action_lines
            assert not move_result.view.wheels
    state = loads((await world.match())["state_json"])
    assert all(item["turn"]["done"] and not item["turn"].get("ready", False) for item in state["sides"])
    assert not await world.db.fetch_all("SELECT * FROM battle_rounds")

    first = await world.send(section="ready", actor=world.a, mid="ready-a")
    assert first.view.title == "会赢的 · 等待对方"
    assert first.view.fighters[0].ready == "已输入 /会赢的"
    assert first.view.fighters[1].ready == "等待 /会赢的"
    expires = (await world.match())["expires_ms"]
    duplicate = await world.send(section="ready", actor=world.a, mid="ready-a-again")
    assert duplicate.view.title == "会赢的 · 等待对方"
    assert (await world.match())["expires_ms"] == expires
    assert not await world.db.fetch_all("SELECT * FROM battle_rounds")

    settled = await world.send(section="ready", actor=world.b, mid="ready-b")
    assert settled.view.title == "会赢的 · 回合结算"
    assert len(await world.db.fetch_all("SELECT * FROM battle_rounds")) == 1
    assert (
        await world.db.fetch_one(
            "SELECT COUNT(*) FROM activity_facts WHERE subevent_id LIKE 'ready:%'"
        )
    )[0] == 2


async def test_confirm_binding_costs_all_levels_and_inherited_protection(world):
    await world.fund()
    for level in range(1, 6):
        await world.send("强化")
        result = await world.send("确认", mid=f"upgrade-{level}")
        await world.send("确认", mid=f"upgrade-{level}")
        assert f"+{level}" in result.view.text()
    assert (await world.db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (world.a.player_id,)))[
        0
    ] == 100000 - 16600
    assert (
        await world.db.fetch_one(
            "SELECT quantity FROM material_balances WHERE player_id=? AND material_id='training-ore'",
            (world.a.player_id,),
        )
    )[0] == 1050
    assert len(await world.db.fetch_all("SELECT * FROM battle_upgrades")) == 5
    async with world.db.transaction() as session:
        assert await MaterialRepository().reconcile(session) == []
    with pytest.raises(BattleError, match=r"强化\+5"):
        await world.send("强化")
    pig = await world.db.fetch_one("SELECT p.* FROM pig_instances p JOIN battle_training b USING(pig_instance_id)")
    async with world.db.transaction() as session:
        with pytest.raises(AssetStateConflictError, match="战斗保护"):
            await require_unoccupied(session, pig["pig_instance_id"])
    with pytest.raises(sqlite3.IntegrityError, match="战斗保护"):
        async with world.db.transaction() as session:
            await session.execute(
                "UPDATE pig_instances SET state='sold' WHERE pig_instance_id=?", (pig["pig_instance_id"],)
            )
    await world.send("解除保护")
    await world.send("确认")
    async with world.db.transaction() as session:
        await session.execute(
            "UPDATE pig_instances SET owner_player_id=? WHERE pig_instance_id=?",
            (world.b.player_id, pig["pig_instance_id"]),
        )
    protected = await world.db.fetch_one(
        "SELECT * FROM battle_protections WHERE pig_instance_id=?", (pig["pig_instance_id"],)
    )
    assert protected["player_id"] == world.b.player_id and protected["protected"] == 1
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_upgrades WHERE player_id=?", (world.b.player_id,)))[
        0
    ] == 0
    assert (await world.db.fetch_one("SELECT level FROM battle_training"))[0] == 5


async def test_insufficient_materials_rolls_back_coins_and_all_resources(world):
    await world.fund()
    async with world.db.transaction() as session:
        await MaterialRepository().change(
            session,
            player_id=world.a.player_id,
            scope_id=world.a.scope.value,
            material_id="agility-fiber",
            delta_units=-3000 * MATERIAL_SCALE,
            source_kind="fixture",
            source_id="drain",
            entry_key="drain",
            now=iso_ms(timestamp_ms(NOW)),
        )
    await world.send("强化")
    before = [tuple(r) for r in await world.db.fetch_all("SELECT * FROM currency_ledger")]
    with pytest.raises(Exception, match="不足"):
        await world.send("确认")
    assert [tuple(r) for r in await world.db.fetch_all("SELECT * FROM currency_ledger")] == before
    assert not await world.db.fetch_all("SELECT * FROM battle_training")


async def test_group_mutex_roles_daily_quota_and_scopes(world):
    await world.invite()
    assert not await world.db.fetch_all("SELECT * FROM battle_daily_uses")
    with pytest.raises(BattleError, match="本群已有"):
        await world.invite(actor=world.b, target=world.a)
    with pytest.raises(BattleError, match="受邀者"):
        await world.send("接受", "challenge")
    await world.send("接受", "challenge", actor=world.b, mid="accept-once")
    await world.send("接受", "challenge", actor=world.b, mid="accept-once")
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_daily_uses"))[0] == 2
    outsider = replace(world.a, user_id="202", display_name="观众")
    assert (await world.send(section="status", actor=outsider)).view.fighters
    with pytest.raises(BattleError, match="只有本场双方"):
        await world.send(section="move", actor=outsider)
    with pytest.raises(BattleError, match="没有等待"):
        await world.send(section="count", actor=replace(world.a, scope=ScopeKey("qq.official.bot2", "100")))
    await world.fight(already_started=True)
    world.clock.value += timedelta(seconds=61)
    with pytest.raises(BattleError, match="次数已用完"):
        await world.invite()
    # 同一天还能交换主动/应战身份；不是每人总共只能一场。
    await world.invite(actor=world.b, target=world.a)
    await world.send("接受", "challenge", actor=world.a)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_daily_uses"))[0] == 4


async def test_admin_generation_reset_reopens_both_roles_without_deleting_original_usage(world):
    await world.fight()
    with pytest.raises(BattleError, match="次数已用完"):
        await world.invite()
    admin = AdministrationService(
        world.db,
        refresh_hours=(0, 8, 16),
        timezone_name="Asia/Shanghai",
        clock=world.clock,
        id_factory=lambda: "battle-reset-audit",
    )
    reset = await admin.reset_battle_quota(
        replace(world.a, message_id="reset-all-battle-roles"),
        command_name="pig-catcher.admin-reset-battle-quota",
        all_players=True,
    )
    assert reset.affected_players == 2
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_daily_uses"))[0] == 2
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_daily_quota_state"))[0] == 2

    world.clock.value += timedelta(seconds=61)
    await world.invite()
    await world.send("接受", "challenge", actor=world.b)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_daily_reuses"))[0] == 2
    assert {
        row[0]
        for row in await world.db.fetch_all(
            "SELECT role FROM battle_daily_reuses WHERE generation=1"
        )
    } == {"initiator", "opponent"}


async def test_expiry_accept_day_and_no_non_natural_loot(world):
    world.clock.value = NOW.replace(hour=15, minute=59)
    await world.invite()
    world.clock.value += timedelta(minutes=2)
    await world.send("接受", "challenge", actor=world.b)
    assert (await world.db.fetch_one("SELECT accepted_day FROM battle_matches"))[0] == "2026-08-28"
    await world.send("认输", "challenge")
    await world.send("确认认输", "challenge")
    assert (await world.match())["status"] == "surrendered"
    assert not await world.db.fetch_all("SELECT * FROM battle_loot")
    assert not await world.db.fetch_all("SELECT * FROM asset_occupancies")
    await world.invite(actor=world.b, target=world.a)
    world.clock.value += timedelta(minutes=5)
    result = await world.send(section="status")
    assert "超时结束" in result.view.text()
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_daily_uses"))[0] == 2


async def test_timeout_queries_do_not_extend_and_old_operations_release(world):
    await world.start()
    expires = (await world.match())["expires_ms"]
    world.clock.value += timedelta(minutes=5)
    await world.send(section="status")
    assert (await world.match())["expires_ms"] == expires
    await world.send(section="count", mid="count-one")
    extended = (await world.match())["expires_ms"]
    assert extended > expires
    world.clock.value += timedelta(minutes=1)
    await world.send(section="count", mid="count-two")
    await world.send(section="count", mid="count-one")
    assert (await world.match())["expires_ms"] == extended
    world.clock.value += timedelta(milliseconds=ACTION_TTL_MS)
    async with world.db.transaction() as session:
        await FrameworkRepository().touch_identity(
            session, identity=world.a, now=iso_ms(timestamp_ms(world.clock.now()))
        )
    assert (await world.match())["status"] == "expired"
    assert not await world.db.fetch_all("SELECT * FROM asset_occupancies")
    assert not await world.db.fetch_all("SELECT * FROM battle_loot")


async def test_duplicate_concurrent_accept_and_restart_resume(world):
    await world.invite()
    db2 = PigCatcherDatabase(world.db.path)
    await db2.open()
    other = BattleService(db2, clock=world.clock, catching=world.service.catching)
    try:
        results = await asyncio.gather(
            world.send("接受", "challenge", actor=world.b, mid="same-accept"),
            world.send("接受", "challenge", actor=world.b, mid="same-accept", service=other),
        )
        assert results[0].receipt.receipt_id == results[1].receipt.receipt_id
        await world.send(section="count", mid="same-count")
        initial = loads((await world.match())["state_json"])
        await world.send(section="count", mid="same-count", service=other)
        assert initial == loads((await world.match())["state_json"])
        await world.send(section="move", service=other, mid="same-moves")
        once = loads((await world.match())["state_json"])
        await world.send(section="move", mid="same-moves")
        assert once == loads((await world.match())["state_json"])
        assert (await world.db.fetch_one("SELECT COUNT(*) FROM battle_daily_uses"))[0] == 2
    finally:
        await db2.close()


async def test_tools_reserve_refund_and_actual_numeric_use(world):
    await world.fund()
    await world.fund(world.b)
    await world.send("制作 练习护腕 2")
    await world.send("器具 练习护腕")
    await world.send("制作 应急绷带", actor=world.b)
    await world.send("器具 应急绷带", actor=world.b)
    await world.start()
    assert (await world.db.fetch_one("SELECT quantity FROM battle_tools WHERE player_id=?", (world.a.player_id,)))[
        0
    ] == 1
    with pytest.raises(BattleError, match="正在对战"):
        await world.send("强化")
    await world.send(section="count")
    await world.send(section="move")
    state = loads((await world.match())["state_json"])
    assert state["sides"][0]["tool_used"]
    await world.send("认输", "challenge")
    await world.send("确认认输", "challenge")
    assert (await world.db.fetch_one("SELECT quantity FROM battle_tools WHERE player_id=?", (world.b.player_id,)))[
        0
    ] == 1
    assert (await world.db.fetch_one("SELECT quantity FROM battle_tools WHERE player_id=?", (world.a.player_id,)))[
        0
    ] == 1


async def test_blacklist_pauses_remaining_loot_and_no_normal_stats(world):
    await world.fight()
    grant = await world.db.fetch_one("SELECT * FROM battle_loot")
    loser = world.a if grant["actor_id"] == world.a.player_id else world.b
    async with world.db.transaction() as session:
        await RestrictionRepository().upsert_restriction(
            session,
            restriction_id="fixture-ban",
            player_id=grant["recipient_id"],
            restriction_type=GIFT_TRANSFER_BAN,
            limit_value=None,
            starts_at=iso_ms(timestamp_ms(NOW)),
            expires_at=None,
            reason="fixture",
            source="test",
            created_by="test",
            now=iso_ms(timestamp_ms(NOW)),
        )
    with pytest.raises(BattleError, match="限制"):
        await world.send(section="loot", actor=loser)
    assert (await world.db.fetch_one("SELECT used FROM battle_loot"))[0] == 0
    world.clock.value += timedelta(days=8)
    async with world.db.transaction() as session:
        await session.execute("DELETE FROM player_restrictions WHERE restriction_id='fixture-ban'")
    await world.send(section="loot", actor=loser)
    assert (await world.db.fetch_one("SELECT used FROM battle_loot"))[0] == 1


async def test_loot_uses_shared_cooldown_and_hard_cap_but_no_normal_quota(world):
    await world.fight()
    grant = await world.db.fetch_one("SELECT * FROM battle_loot")
    loser = world.a if grant["actor_id"] == world.a.player_id else world.b
    world.service.catching = CatchingSection(cooldown_seconds=20)
    await world.send(section="loot", actor=loser)
    with pytest.raises(CatchCooldownError):
        await world.send(section="loot", actor=loser)
    normal = GameplayService(world.db, world.service.catching, clock=world.clock)
    with pytest.raises(CatchCooldownError):
        await normal.catch(replace(loser, message_id="ordinary-cooldown"))
    world.clock.value += timedelta(seconds=21)
    async with world.db.transaction() as session:
        await RestrictionRepository().upsert_restriction(
            session,
            restriction_id="fixture-limit",
            player_id=loser.player_id,
            restriction_type="catch-window-limit",
            limit_value=1,
            starts_at=iso_ms(timestamp_ms(NOW)),
            expires_at=None,
            reason="fixture",
            source="test",
            created_by="test",
            now=iso_ms(timestamp_ms(NOW)),
        )
    with pytest.raises(DailyCatchLimitError):
        await world.send(section="loot", actor=loser)
    assert (await world.db.fetch_one("SELECT used FROM battle_loot"))[0] == 1


async def test_normal_quota_exhaustion_preserves_extra_catches_and_reverse_cooldown(world):
    await world.fight()
    grant = await world.db.fetch_one("SELECT * FROM battle_loot")
    loser = world.a if grant["actor_id"] == world.a.player_id else world.b
    normal = GameplayService(world.db, world.service.catching, clock=world.clock)
    for ordinal in range(5):
        await normal.catch(replace(loser, message_id=f"ordinary-{ordinal}"))
    with pytest.raises(DailyCatchLimitError):
        await normal.catch(replace(loser, message_id="ordinary-limit"))
    world.service.catching = CatchingSection(cooldown_seconds=20)
    with pytest.raises(CatchCooldownError):
        await world.send(section="loot", actor=loser)
    world.clock.value += timedelta(seconds=21)
    assert "4/5" in (await world.send(section="loot", actor=loser)).view.text()
    with pytest.raises(DailyCatchLimitError):
        await normal.catch(replace(loser, message_id="ordinary-still-limit"))


async def test_confirmation_timeout_and_changed_instance(world):
    await world.send("强化")
    world.clock.value += timedelta(seconds=121)
    assert "过期" in (await world.send("确认")).view.title
    assert not await world.db.fetch_all("SELECT * FROM battle_training")
    await world.invite()
    await world.send("器具 无", actor=world.b)
    with pytest.raises(BattleError, match="发生变化"):
        await world.send("接受", "challenge", actor=world.b)
    assert not await world.db.fetch_all("SELECT * FROM battle_daily_uses")
    await world.fund()
    await world.service.execute(
        replace(world.a, message_id="conflict"), BattleRequest("craft", {"tool_id": "wristband", "quantity": 1})
    )
    with pytest.raises(ReceiptConflictError):
        await world.service.execute(
            replace(world.a, message_id="conflict"), BattleRequest("craft", {"tool_id": "wristband", "quantity": 2})
        )
