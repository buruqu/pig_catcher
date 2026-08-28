"""派遣纯规则、原子材料、占用、重启和所有关键操作的隔离离线验收。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pig_catcher.commands.dispatch import DispatchRequest, parse_dispatch_request
from pig_catcher.config.model import CookingSection, EconomySection, RankingSection, TradingSection
from pig_catcher.domain.dispatch import (
    MATERIAL_SCALE,
    REGIONS_BY_ID,
    SPECIALTIES,
    DispatchError,
    block_yield,
    encounter_options,
    exploration_step,
    normalized_attribute,
    proficiency,
    random_at,
    safe_display_name,
    specialties,
    team_bonus,
    team_slots,
)
from pig_catcher.domain.enums import AssetKind
from pig_catcher.domain.errors import AssetStateConflictError, MigrationError, ReceiptConflictError
from pig_catcher.domain.models import AssetSelector, CommandIdentity, ScopeKey
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.repositories import EconomyRepository, FrameworkRepository, GameplayRepository
from pig_catcher.infrastructure.repositories.dispatch import DispatchRepository, iso_ms, timestamp_ms
from pig_catcher.infrastructure.repositories.materials import MaterialRepository
from pig_catcher.services.dispatch import DispatchService
from pig_catcher.services.economy import EconomyService
from pig_catcher.services.social import SocialService

from .test_gameplay import MutableClock, _database_with_catalog, _pig_entry

NOW = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
SAFE_SEED = next(
    f"safe-{i}" for i in range(1000) if all(random_at(f"safe-{i}", b, "encounter") >= 0.1 for b in range(1, 7))
)


@dataclass
class World:
    db: PigCatcherDatabase
    clock: MutableClock
    service: DispatchService
    identity: CommandIdentity

    async def send(self, text: str = "", section: str = "dispatch", *, message_id: str | None = None):
        return await self.service.execute(
            replace(self.identity, message_id=message_id or uuid4().hex), parse_dispatch_request(text, section=section)
        )

    async def team(self, slot: int = 1, names: str = "1星测试猪、1星测试猪、1星测试猪") -> None:
        await self.send(f"编队 {slot} {names}")
        await self.send("确认")

    async def start(self, route: str = "青草近郊", hours: int = 4, slot: int = 1, tool: str = "") -> dict:
        await self.send(f"出发 {slot} {route} {hours}小时 {tool}".strip())
        result = await self.send("确认")
        row = await self.db.fetch_one(
            "SELECT * FROM dispatch_trips WHERE player_id=? AND slot=? ORDER BY sequence DESC LIMIT 1",
            (self.identity.player_id, slot),
        )
        assert result.receipt is not None and row is not None
        return dict(row)

    async def advance(self, hours: float) -> None:
        self.clock.value += timedelta(hours=hours)
        await self.send()

    async def material(self, key: str, quantity: int) -> None:
        async with self.db.transaction() as session:
            await MaterialRepository().change(
                session,
                player_id=self.identity.player_id,
                scope_id=self.identity.scope.value,
                material_id=key,
                delta_units=quantity * MATERIAL_SCALE,
                source_kind="test-seed",
                source_id="fixture",
                entry_key=uuid4().hex,
                now=iso_ms(timestamp_ms(self.clock.now())),
            )


async def seed_pigs(
    db: PigCatcherDatabase,
    identity: CommandIdentity,
    *,
    template_id: str = "low",
    count: int = 9,
    value: int = 100,
    now: datetime = NOW,
) -> list[str]:
    ids = []
    async with db.transaction() as session:
        at = iso_ms(timestamp_ms(now))
        await FrameworkRepository().touch_identity(session, identity=identity, now=at)
        template = await session.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (template_id,))
        assert template is not None
        for i in range(count):
            pig_id = uuid4().hex
            ids.append(pig_id)
            await GameplayRepository().insert_pig_instance(
                session,
                values={
                    "pig_instance_id": pig_id,
                    "short_code": uuid4().hex[:8].upper(),
                    "scope_id": identity.scope.value,
                    "owner_player_id": identity.player_id,
                    "template_id": template_id,
                    "template_version": 1,
                    "rarity": template["rarity"],
                    "display_name_snapshot": template["display_name"],
                    "size_value": 50,
                    "size_percentile": 0.5,
                    "weight_value": 70,
                    "weight_percentile": 0.5,
                    "fat_ratio": 1,
                    "official_value": value + i,
                    "ruleset_version": 32,
                    "random_snapshot_json": "{}",
                    "acquired_at": at,
                    "updated_at": at,
                },
            )
    return ids


@pytest.fixture
async def world(tmp_path: Path):
    db = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("low", rarity=1),
            _pig_entry("medium", rarity=3),
            _pig_entry("high", rarity=5),
            _pig_entry("six", rarity=6, group_id="100"),
        ],
    )
    identity = CommandIdentity(ScopeKey("qq", "100"), "stream-100", "200", "远行测试员", "fixture", "测试群")
    await seed_pigs(db, identity)
    clock = MutableClock(NOW)
    service = DispatchService(db, clock=clock, seed_factory=lambda: SAFE_SEED)
    async with db.transaction() as session:
        await EconomyRepository().apply_currency_change(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            amount=10000,
            reason_code="fixture",
            reason_text="离线测试",
            source_object_type="test",
            source_object_id="seed",
            ledger_entry_id=uuid4().hex,
            idempotency_key="seed",
            now=iso_ms(timestamp_ms(NOW)),
        )
    yield World(db, clock, service, identity)
    await db.close()


def test_formal_specialties_cover_all_223_pigs_and_91_low_star_templates():
    path = Path(__file__).resolve().parents[1] / "catalogs/formal/pig-and-food-definitions.json"
    pigs = [entry for entry in json.loads(path.read_text(encoding="utf-8"))["entries"] if entry["kind"] == "pig"]
    assert set(SPECIALTIES) == {entry["template_id"] for entry in pigs}
    assert len(pigs) == 223 and sum(entry["rarity"] <= 3 for entry in pigs) == 91
    assert specialties("future-unknown-template") == ("后勤",)


@pytest.mark.parametrize("count,ratio", [(1, 4), (2, 7), (3, 10)])
@pytest.mark.parametrize("bonus", [0, 25_000, 175_000, 300_000])
def test_fixed_point_yield_scales_members_and_never_rounds_up(count, ratio, bonus):
    main, supplies = block_yield(count, bonus)
    assert main == 6 * ratio * (1_000_000 + bonus)
    assert supplies == 2 * ratio * (1_000_000 + bonus)
    assert 6 * main == sum(block_yield(count, bonus)[0] for _ in range(6))


@pytest.mark.parametrize("hours,level", [(0, 0), (11, 0), (12, 1), (36, 2), (72, 3), (120, 4), (192, 5), (500, 5)])
def test_proficiency_thresholds(hours, level):
    assert proficiency(hours) == level


def test_attribute_neutral_and_team_rules():
    assert normalized_attribute(10, 10, 10) == 0.5
    assert normalized_attribute(float("nan"), 1, 2) == 0.5
    assert normalized_attribute(100, 1, 2) == 1
    assert normalized_attribute(None, 1, 2) == 0.5
    members = [
        {"pig_instance_id": str(i), "rarity": 1, "tags": ["搬运", "采掘"], "size_q": 1, "weight_q": 1, "proficiency": 5}
        for i in range(3)
    ]
    assert sum(team_bonus(members, REGIONS_BY_ID["echo-mine"]).values()) == 300_000
    with pytest.raises(DispatchError):
        team_bonus([{**m, "rarity": 5} for m in members], REGIONS_BY_ID["grassland"])
    with pytest.raises(DispatchError):
        team_bonus([members[0], members[0]], REGIONS_BY_ID["grassland"])
    assert [team_slots(h * 3600) for h in (0, 11, 12, 71, 72)] == [1, 1, 2, 2, 3]


def test_exploration_fraction_and_tenth_pity():
    fraction, misses = 0, 0
    for _ in range(9):
        fraction, misses, hit, forced = exploration_step(fraction, 3, misses, 0.99)
        assert not hit and not forced
    assert exploration_step(0, 3, misses, 0.99) == (0, 0, True, True)
    assert exploration_step(0, 1, 0, 0.01) == (4, 0, False, False)
    assert exploration_step(8, 1, 0, 0.01) == (2, 0, True, False)
    assert exploration_step(0, 3, 0, 0.1) == (0, 1, False, False)


@pytest.mark.parametrize(
    "text,section",
    [
        ("出发 1 青草近郊 6", "dispatch"),
        ("编队 1 a、a、a、a", "dispatch"),
        ("出发 1 青草近郊 4 整理箱 旅行补给 0 机关零件", "dispatch"),
        ("转换 旅行补给 训练矿石 1", "bag"),
        ("转换 训练矿石 训练矿石 1", "bag"),
        ("制作 区域地图 0", "bag"),
        ("召回 0", "dispatch"),
        ("是", "dispatch"),
        ("999999999999999", "journal"),
    ],
)
def test_parser_rejects_ambiguous_and_unsafe_input(text, section):
    with pytest.raises(DispatchError):
        parse_dispatch_request(text, section=section)


async def test_complete_trip_is_atomic_persistent_and_no_gameplay_effects_consumed(world: World):
    await world.team()
    before = dict(
        await world.db.fetch_one("SELECT * FROM player_statistics WHERE player_id=?", (world.identity.player_id,))
    )
    trip = await world.start("回声矿洞", 8)
    assert len(await world.db.fetch_all("SELECT * FROM asset_occupancies")) == 3
    await world.advance(8)
    async with world.db.transaction(immediate=False) as session:
        balances = await MaterialRepository().balances(session, world.identity.player_id)
        assert await MaterialRepository().reconcile(session) == []
    assert balances == {"training-ore": 13, "travel-supplies": 4}
    assert await world.db.fetch_all("SELECT * FROM asset_occupancies") == []
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == 9960
    assert (
        dict(await world.db.fetch_one("SELECT * FROM player_statistics WHERE player_id=?", (world.identity.player_id,)))
        == before
    )
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM pig_instances WHERE state='active'"))[0] == 9
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM command_receipts WHERE catch_quota_cost<>0"))[0] == 0
    result = await world.send(trip["trip_id"], "journal")
    assert "平安归来" in result.view.title and len(result.view.pigs) == 3
    assert "群友ID" not in result.view.text()
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM activity_facts WHERE source_id=?", (trip["trip_id"],)))[
        0
    ] == 4


async def test_preview_confirmation_expiry_and_changed_favorite(world: World):
    await world.send("编队 1 1星测试猪")
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM dispatch_teams"))[0] == 0
    world.clock.value += timedelta(minutes=2)
    assert "已过期" in (await world.send("确认")).view.title
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM dispatch_teams"))[0] == 0
    await world.team(names="1星测试猪")
    await world.send("出发 1 青草近郊 4")
    team = await world.db.fetch_one("SELECT * FROM dispatch_teams")
    pig_id = json.loads(team["member_ids_json"])[0]
    async with world.db.transaction() as session:
        await session.execute("UPDATE pig_instances SET is_favorite=1 WHERE pig_instance_id=?", (pig_id,))
    with pytest.raises(DispatchError, match="变化"):
        await world.send("确认")
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM dispatch_trips"))[0] == 0
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == 10000
    await world.send("取消")
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM dispatch_pending"))[0] == 0


async def test_same_message_and_concurrent_confirm_charge_once(world: World):
    await world.team()
    await world.send("出发 1 回声矿洞 4")
    result1, result2 = await asyncio.gather(
        world.send("确认", message_id="confirm-once"), world.send("确认", message_id="confirm-once")
    )
    assert result1.receipt.receipt_id == result2.receipt.receipt_id
    assert result1.view == result2.view
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM dispatch_trips"))[0] == 1
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == 9980
    await world.advance(4)
    first = await world.send("返程", message_id="return-once")
    ledger = await world.db.fetch_all("SELECT * FROM material_ledger")
    await world.send("返程", message_id="return-once")
    assert len(await world.db.fetch_all("SELECT * FROM material_ledger")) == len(ledger)
    assert len(first.view.panels) == 1
    assert not (await world.send("返程")).view.panels


async def test_restart_replay_keeps_random_and_return_materials(world: World):
    await world.team()
    trip = await world.start(hours=24)
    await world.advance(8)
    saved = await world.db.fetch_one("SELECT progress_json,random_seed FROM dispatch_trips")
    await world.db.close()
    await world.db.open()
    world.service = DispatchService(world.db, clock=world.clock, seed_factory=lambda: "must-not-reroll")
    await world.send()
    assert tuple(await world.db.fetch_one("SELECT progress_json,random_seed FROM dispatch_trips")) == tuple(saved)
    await world.advance(16)
    row = await world.db.fetch_one("SELECT * FROM dispatch_trips WHERE trip_id=?", (trip["trip_id"],))
    assert row["status"] == "completed" and row["processed_blocks"] == 6
    assert (await world.db.fetch_one("SELECT effective_seconds FROM dispatch_profiles"))[0] == 24 * 3600
    assert (
        await world.db.fetch_one(
            "SELECT quantity,remainder_units FROM material_balances WHERE material_id='travel-supplies'"
        )
    )[:] == (56, 4_000_000)


async def test_recall_keeps_full_blocks_does_not_refund_and_unlocks(world: World):
    await world.team()
    trip = await world.start("回声矿洞", 24)
    await world.advance(5)
    await world.send("召回 1")
    result = await world.send("确认")
    assert "已召回" in result.view.title
    row = await world.db.fetch_one("SELECT * FROM dispatch_trips WHERE trip_id=?", (trip["trip_id"],))
    assert row["processed_blocks"] == 1 and row["status"] == "recalled"
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == 9880
    assert await world.db.fetch_all("SELECT * FROM asset_occupancies") == []
    assert (await world.db.fetch_one("SELECT effective_seconds FROM dispatch_profiles"))[0] == 4 * 3600
    assert all(
        row["hours"] == 4 and row["normal_hours"] == 0
        for row in await world.db.fetch_all("SELECT * FROM dispatch_contributions")
    )


async def test_recall_before_first_block_loses_progress_but_no_pig(world: World):
    await world.team()
    await world.start()
    await world.advance(3.9)
    await world.send("召回 1")
    await world.send("确认")
    assert not await world.db.fetch_all("SELECT * FROM material_ledger")
    assert not await world.db.fetch_all("SELECT * FROM asset_occupancies")
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM pig_instances WHERE state='active'"))[0] == 9


async def test_team_slot_union_shared_pity_and_no_early_unlock(world: World):
    await world.team()
    await world.start(hours=12)
    with pytest.raises(DispatchError, match="解锁"):
        await world.team(2)
    await world.advance(12)
    await world.start(hours=12)
    await world.team(2)
    await world.start(hours=12, slot=2)
    await world.advance(12)
    profile = await world.db.fetch_one("SELECT * FROM dispatch_profiles")
    assert profile["effective_seconds"] == 24 * 3600  # 不是36h
    assert len(await world.db.fetch_all("SELECT * FROM dispatch_trips")) == 3
    assert (await world.db.fetch_one("SELECT misses FROM dispatch_route_progress"))[0] == 9
    await world.start(hours=4)
    await world.advance(4)
    events = json.loads(
        (await world.db.fetch_one("SELECT progress_json FROM dispatch_trips ORDER BY sequence DESC LIMIT 1"))[0]
    )["events"]
    assert events[0]["forced"] is True


async def test_favorite_and_busy_selection_and_six_star_composition(world: World):
    rows = await world.db.fetch_all("SELECT * FROM pig_instances ORDER BY official_value")
    async with world.db.transaction() as session:
        await session.execute(
            "UPDATE pig_instances SET is_favorite=1 WHERE pig_instance_id=?", (rows[0]["pig_instance_id"],)
        )
    await world.team(names="1星测试猪")
    ids = json.loads((await world.db.fetch_one("SELECT member_ids_json FROM dispatch_teams"))[0])
    assert ids == [rows[1]["pig_instance_id"]]
    await world.team(names=f"1星测试猪#{rows[0]['short_code']}")
    await world.start()
    assert (
        await world.db.fetch_one(
            "SELECT is_favorite FROM pig_instances WHERE pig_instance_id=?", (rows[0]["pig_instance_id"],)
        )
    )[0] == 1
    await world.advance(4)
    await seed_pigs(world.db, world.identity, template_id="six", count=1, now=world.clock.now())
    await seed_pigs(world.db, world.identity, template_id="high", count=1, now=world.clock.now())
    with pytest.raises(DispatchError, match="低|1至3"):
        await world.team(names="6星测试猪")
    with pytest.raises(DispatchError):
        await world.team(names="6星测试猪、5星测试猪、1星测试猪")
    await world.team(names="6星测试猪、1星测试猪")
    await world.start()


async def test_craft_and_conversion_are_atomic_idempotent_and_ledgered(world: World):
    await world.material("travel-supplies", 20)
    with pytest.raises(DispatchError, match="不足"):
        await world.send("制作 区域地图 2", "bag")
    assert (await world.db.fetch_one("SELECT quantity FROM material_balances WHERE material_id='travel-supplies'"))[
        0
    ] == 20
    await world.material("machine-parts", 10)
    first = await world.send("制作 区域地图 2", "bag", message_id="craft")
    second = await world.send("制作 区域地图 2", "bag", message_id="craft")
    assert first.receipt.receipt_id == second.receipt.receipt_id
    assert (await world.db.fetch_one("SELECT quantity FROM dispatch_tools"))[0] == 2
    await world.send("转换 机关零件 灵巧纤维 2", "bag")
    async with world.db.transaction(immediate=False) as session:
        balances = await MaterialRepository().balances(session, world.identity.player_id)
        assert await MaterialRepository().reconcile(session) == []
    assert balances["machine-parts"] == 2 and balances["agility-fiber"] == 2
    with pytest.raises(ReceiptConflictError):
        await world.send("制作 区域地图 1", "bag", message_id="craft")


async def test_map_replaces_only_general_supplies_not_grass_primary(world: World):
    await world.material("travel-supplies", 3)
    await world.material("machine-parts", 1)
    await world.send("制作 区域地图", "bag")
    await world.team()
    await world.start(hours=24, tool="区域地图 训练矿石")
    await world.advance(24)
    async with world.db.transaction(immediate=False) as session:
        balances = await MaterialRepository().balances(session, world.identity.player_id)
    assert balances["training-ore"] == 4 and balances["travel-supplies"] == 52
    assert (await world.db.fetch_one("SELECT quantity FROM dispatch_tools"))[0] == 0


async def test_sorting_box_honors_keep_and_three_to_one(world: World):
    await world.material("travel-supplies", 2)
    await world.material("machine-parts", 16)
    await world.send("制作 整理箱", "bag")
    await world.team()
    await world.start(tool="整理箱 机关零件 5 灵巧纤维")
    await world.advance(4)
    async with world.db.transaction(immediate=False) as session:
        balance = await MaterialRepository().balances(session, world.identity.player_id)
    assert balance["machine-parts"] == 6 and balance["agility-fiber"] == 3


async def test_compass_choice_is_persistent_not_blocking_and_auto_follows_preference(world: World):
    for key, quantity in (("travel-supplies", 8), ("machine-parts", 4), ("travel-notes", 1)):
        await world.material(key, quantity)
    await world.send("制作 奇遇罗盘", "bag")
    await world.team()
    async with world.db.transaction() as session:
        await session.execute(
            "INSERT INTO dispatch_route_progress VALUES(?,?,0,9)", (world.identity.player_id, "grassland")
        )
    trip = await world.start(tool="奇遇罗盘 2")
    await world.advance(4)
    choice = await world.db.fetch_one("SELECT * FROM dispatch_choices")
    assert choice is not None and choice["selected"] is None
    assert await world.db.fetch_all("SELECT * FROM asset_occupancies") == []
    options = json.loads(choice["options_json"])
    assert options[0]["kind"] != options[1]["kind"]
    await world.db.close()
    await world.db.open()
    assert "候选2" in (await world.send("", "encounters")).view.text()
    await world.start()
    assert (await world.db.fetch_one("SELECT selected FROM dispatch_choices"))[0] == 2
    before = len(await world.db.fetch_all("SELECT * FROM material_ledger"))
    await world.send(f"{choice['choice_id']} 1", "encounters")
    assert len(await world.db.fetch_all("SELECT * FROM material_ledger")) == before
    assert "已选" in (await world.send(trip["trip_id"], "journal")).view.text()


async def test_cross_group_and_other_player_cannot_confirm_view_or_claim(world: World):
    await world.team()
    trip = await world.start()
    for scope, user in (
        (ScopeKey("qq", "101"), "200"),
        (ScopeKey("qq-official", "100"), "200"),
        (ScopeKey("qq", "100"), "201"),
    ):
        other = replace(world.identity, scope=scope, user_id=user, message_id=uuid4().hex)
        with pytest.raises(DispatchError):
            await world.service.execute(other, DispatchRequest("confirm"))
        with pytest.raises(DispatchError):
            await world.service.execute(other, DispatchRequest("detail", {"trip_id": trip["trip_id"]}))
        assert "0 / 0" in (await world.service.execute(other, DispatchRequest("overview"))).view.text()


async def test_busy_pigs_excluded_before_automatic_and_batch_selection(world: World):
    await world.team()
    trip = await world.start()
    busy = {row["pig_instance_id"] for row in await world.db.fetch_all("SELECT * FROM asset_occupancies")}
    async with world.db.transaction() as session:
        game, economy = GameplayRepository(), EconomyRepository()
        rows = await game.find_active_pigs(
            session, player_id=world.identity.player_id, selector=AssetSelector("1星测试猪"), available_only=True
        )
        assert not busy.intersection(row["pig_instance_id"] for row in rows)
        rows = await game.list_cookable_pigs(
            session, player_id=world.identity.player_id, scope_id=world.identity.scope.value, rarity=None
        )
        assert len(rows) == 6
        chosen = await economy.cheapest_active_asset_id(
            session, player_id=world.identity.player_id, scope_id=world.identity.scope.value, asset_kind="pig"
        )
        assert chosen not in busy
        count, _ = await economy.batch_sell_low_rarity(
            session,
            player_id=world.identity.player_id,
            scope_id=world.identity.scope.value,
            asset_kind="pig",
            max_rarity=3,
            now=iso_ms(timestamp_ms(NOW)),
        )
        assert count == 6
    for pig_id in busy:
        async with world.db.transaction() as session:
            with pytest.raises(sqlite3.IntegrityError, match="活动"):
                await session.execute("UPDATE pig_instances SET state='sold' WHERE pig_instance_id=?", (pig_id,))
    assert trip["status"] == "traveling"


async def test_existing_sale_and_gift_reject_busy_but_auto_release_on_due(world: World):
    await world.team(names="1星测试猪")
    trip = await world.start()
    member = json.loads(trip["snapshot_json"])["members"][0]
    selector = f"{member['name']}#{member['short_code']}"
    economy = EconomyService(world.db, CookingSection(cook_cooldown_seconds=0), EconomySection(), clock=world.clock)
    with pytest.raises(AssetStateConflictError, match="派遣"):
        await economy.sell_pig(replace(world.identity, message_id="busy-sale"), selector)
    social = SocialService(world.db, TradingSection(), RankingSection(), clock=world.clock)
    other = replace(world.identity, user_id="201", display_name="收件人", message_id="other")
    with pytest.raises(AssetStateConflictError, match="派遣"):
        await social.gift(
            replace(world.identity, message_id="busy-gift"), other, asset_kind=AssetKind.PIG, selector_text=selector
        )
    world.clock.value += timedelta(hours=4)
    await economy.sell_pig(replace(world.identity, message_id="after-return-sale"), selector)
    assert (await world.db.fetch_one("SELECT status FROM dispatch_trips"))[0] == "completed"
    assert (
        await world.db.fetch_one(
            "SELECT state FROM pig_instances WHERE pig_instance_id=?", (member["pig_instance_id"],)
        )
    )[0] == "sold"


async def test_material_ledger_is_immutable_and_fraction_carries_across_credits(world: World):
    repo = MaterialRepository()
    async with world.db.transaction() as session:
        for i in range(6):
            await repo.change(
                session,
                player_id=world.identity.player_id,
                scope_id=world.identity.scope.value,
                material_id="training-ore",
                delta_units=2_400_000,
                source_kind="test-fraction",
                source_id="fixed",
                entry_key=f"part-{i}",
                now=iso_ms(timestamp_ms(NOW)),
            )
        assert await repo.reconcile(session) == []
    assert tuple(await world.db.fetch_one("SELECT quantity,remainder_units FROM material_balances")) == (1, 4_400_000)
    for sql in ("UPDATE material_ledger SET delta_units=999", "DELETE FROM material_ledger"):
        with pytest.raises(sqlite3.IntegrityError):
            async with world.db.transaction() as session:
                await session.execute(sql)


def test_safe_nickname_and_camera_prefers_unowned_without_probability_change():
    assert safe_display_name("A" * 32) == "未命名群友"
    assert safe_display_name("123456789") == "未命名群友"
    assert safe_display_name("撅撅") == "撅撅"
    region = REGIONS_BY_ID["grassland"]
    known = {f"dispatch-souvenir-grassland-{i}" for i in range(1, 4)}
    seed = next(
        str(i)
        for i in range(100)
        if encounter_options(region, str(i), 1, set(), camera=False, known=set())[0]["kind"] == "souvenir"
    )
    selected = encounter_options(region, seed, 1, set(), camera=True, known=known)[0]
    assert selected["souvenir_id"] == "dispatch-souvenir-grassland-4"


async def test_schema_36_migrates_to_dispatch_without_changing_existing_tables(tmp_path: Path, world: World):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)")
    for migration in MIGRATIONS:
        if migration.version > 36:
            break
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, "test"))
    connection.execute("PRAGMA user_version=36")
    original_rows = {}
    original_columns = {}
    for table in (
        "scopes",
        "players",
        "asset_manifest_imports",
        "pig_templates",
        "scope_pig_templates",
        "pig_instances",
        "currency_ledger",
    ):
        original_columns[table] = ",".join(f'"{r[1]}"' for r in connection.execute(f'PRAGMA table_info("{table}")'))
        rows = await world.db.fetch_all(f"SELECT {original_columns[table]} FROM {table} ORDER BY rowid")
        original_rows[table] = [tuple(row) for row in rows]
        if rows:
            placeholders = ",".join("?" for _ in rows[0])
            connection.executemany(f"INSERT INTO {table} VALUES({placeholders})", original_rows[table])
    assert len(original_rows["pig_instances"]) == 9
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    old_schema = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
    connection.commit()
    connection.close()
    db = PigCatcherDatabase(path)
    await db.open()
    from pig_catcher.version import SCHEMA_VERSION

    assert await db.schema_version() == SCHEMA_VERSION
    current = {
        row["name"]: row["sql"] for row in await db.fetch_all("SELECT name,sql FROM sqlite_master WHERE type='table'")
    }
    assert all(
        current[name].replace(", display_tags_json TEXT NOT NULL DEFAULT '[]'", "") == value
        for name, value in old_schema.items()
    )
    for table, rows in original_rows.items():
        assert [
            tuple(row) for row in await db.fetch_all(f"SELECT {original_columns[table]} FROM {table} ORDER BY rowid")
        ] == rows
    assert await db.fetch_all("PRAGMA foreign_key_check") == []
    assert await db.integrity_check() == ("ok",)
    await db.close()


async def test_late_claim_order_cannot_change_multi_team_pity_or_rewards(world: World, tmp_path: Path):
    await world.team()
    await world.start(hours=12)
    await world.advance(12)
    first = await world.start(hours=24)
    await world.team(2)
    second = await world.start(hours=24, slot=2)
    copy_path = tmp_path / "claim-order-copy.sqlite3"
    await world.db.backup_to(copy_path)
    second_db = PigCatcherDatabase(copy_path)
    await second_db.open()
    try:
        world.clock.value += timedelta(hours=30)
        other = DispatchService(second_db, clock=world.clock, seed_factory=lambda: "unused")
        await world.send(second["trip_id"], "journal")
        await other.execute(world.identity, DispatchRequest("detail", {"trip_id": first["trip_id"]}))
        for table in (
            "material_balances",
            "material_ledger",
            "dispatch_route_progress",
            "dispatch_profiles",
            "activity_facts",
        ):
            a = [tuple(row) for row in await world.db.fetch_all(f"SELECT * FROM {table} ORDER BY rowid")]
            b = [tuple(row) for row in await second_db.fetch_all(f"SELECT * FROM {table} ORDER BY rowid")]
            assert a == b, table
        row = await world.db.fetch_one("SELECT progress_json FROM dispatch_trips WHERE trip_id=?", (first["trip_id"],))
        assert json.loads(row["progress_json"])["recorded_ms"] > first["ends_ms"]
    finally:
        await second_db.close()


async def test_staggered_teams_count_union_not_sum(world: World):
    await world.team()
    await world.start(hours=12)
    await world.advance(12)
    await world.start(hours=24)
    await world.advance(1)
    await world.team(2)
    await world.start(hours=24, slot=2)
    await world.advance(24)
    assert (await world.db.fetch_one("SELECT effective_seconds FROM dispatch_profiles"))[0] == 37 * 3600


async def test_duplicate_compass_selection_from_independent_connections_grants_once(world: World):
    for key, qty in (("travel-supplies", 8), ("machine-parts", 4), ("travel-notes", 1)):
        await world.material(key, qty)
    await world.send("制作 奇遇罗盘", "bag")
    await world.team()
    async with world.db.transaction() as session:
        await session.execute(
            "INSERT INTO dispatch_route_progress VALUES(?,?,0,9)", (world.identity.player_id, "grassland")
        )
    await world.start(tool="奇遇罗盘")
    await world.advance(4)
    choice = await world.db.fetch_one("SELECT choice_id FROM dispatch_choices")
    other_db = PigCatcherDatabase(world.db.path)
    await other_db.open()
    try:
        other = DispatchService(other_db, clock=world.clock)
        request = DispatchRequest("choose", {"choice_id": choice[0], "selected": 1})
        identity = replace(world.identity, message_id="one-choice")
        first, second = await asyncio.gather(world.service.execute(identity, request), other.execute(identity, request))
        assert first.receipt.receipt_id == second.receipt.receipt_id
        assert (await world.db.fetch_one("SELECT COUNT(*) FROM activity_facts WHERE subevent_id LIKE 'choice:%'"))[
            0
        ] == 1
    finally:
        await other_db.close()


async def test_departure_failure_after_debit_rolls_back_every_resource(world: World):
    await world.material("travel-supplies", 3)
    await world.material("machine-parts", 1)
    await world.send("制作 区域地图", "bag")
    await world.team()
    await world.send("出发 1 回声矿洞 24小时 区域地图 训练矿石")

    def broken_seed():
        raise RuntimeError("simulate crash after payment before trip insert")

    world.service.seed_factory = broken_seed
    with pytest.raises(RuntimeError, match="simulate crash"):
        await world.send("确认", message_id="crash-depart")
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == 10000
    assert (await world.db.fetch_one("SELECT quantity FROM dispatch_tools"))[0] == 1
    assert not await world.db.fetch_all("SELECT * FROM dispatch_trips")
    assert not await world.db.fetch_all("SELECT * FROM asset_occupancies")
    world.service.seed_factory = lambda: SAFE_SEED
    await world.send("确认", message_id="crash-depart")
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == 9880
    assert (await world.db.fetch_one("SELECT quantity FROM dispatch_tools"))[0] == 0


async def test_db_busy_retry_preserves_confirmation_and_cannot_half_depart(world: World):
    await world.team()
    await world.send("出发 1 回声矿洞 4小时")
    other_db = PigCatcherDatabase(world.db.path, busy_timeout_ms=20)
    await other_db.open()
    other = DispatchService(other_db, clock=world.clock)
    blocker = sqlite3.connect(world.db.path, isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        identity = replace(world.identity, message_id="busy-confirm")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await other.execute(identity, DispatchRequest("confirm"))
        blocker.rollback()
        assert not await world.db.fetch_all("SELECT * FROM dispatch_trips")
        await other.execute(identity, DispatchRequest("confirm"))
        assert (await world.db.fetch_one("SELECT COUNT(*) FROM dispatch_trips"))[0] == 1
        assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == 9980
    finally:
        blocker.close()
        await other_db.close()


async def test_completed_pig_proficiency_transfers_but_personal_contribution_does_not(world: World):
    await world.team(names="1星测试猪")
    trip = await world.start(hours=24)
    member = json.loads(trip["snapshot_json"])["members"][0]
    await world.advance(24)
    detail = (await world.send(trip["trip_id"], "journal")).view
    assert "Lv.1" in detail.pigs[0].summary and "24h" in detail.pigs[0].summary
    recipient = replace(world.identity, user_id="201", display_name="接班旅行家", message_id="recipient")
    social = SocialService(world.db, TradingSection(), RankingSection(), clock=world.clock)
    await social.gift(
        replace(world.identity, message_id="travel-veteran-gift"),
        recipient,
        asset_kind=AssetKind.PIG,
        selector_text=f"{member['name']}#{member['short_code']}",
    )
    async with world.db.transaction(immediate=False) as session:
        transferred = await DispatchRepository().member(session, recipient.player_id, member["pig_instance_id"])
    assert transferred["proficiency"] == 1 and transferred["hours"] == 24
    assert (
        await world.db.fetch_one(
            "SELECT COUNT(*) FROM dispatch_contributions WHERE player_id=?", (recipient.player_id,)
        )
    )[0] == 0


async def test_historical_and_replayed_card_hides_revoked_six_star_media(world: World):
    await seed_pigs(world.db, world.identity, template_id="six", count=1)
    await world.team(names="6星测试猪、1星测试猪")
    await world.send("出发 1 青草近郊 4小时")
    first = await world.send("确认", message_id="six-depart")
    assert first.view.pigs[0].image_relpath
    trip = await world.db.fetch_one("SELECT trip_id FROM dispatch_trips")
    async with world.db.transaction() as session:
        await session.execute("UPDATE scope_pig_templates SET consent_status='revoked' WHERE template_id='six'")
    replay = await world.send("确认", message_id="six-depart")
    assert not replay.view.pigs[0].image_relpath
    detail = await world.send(trip[0], "journal")
    assert not detail.view.pigs[0].image_relpath


async def test_new_facts_separate_base_bonus_conversion_and_no_new_achievements(world: World):
    await world.team()
    trip = await world.start("回声矿洞", 4)
    await world.advance(4)
    ledgers = await world.db.fetch_all("SELECT * FROM material_ledger WHERE source_id=?", (trip["trip_id"],))
    by_kind = {}
    for row in ledgers:
        by_kind[row["source_kind"]] = by_kind.get(row["source_kind"], 0) + row["delta_units"]
    assert by_kind["dispatch-base"] == 8 * MATERIAL_SCALE
    assert by_kind["dispatch-bonus"] == MATERIAL_SCALE
    block = json.loads(
        (
            await world.db.fetch_one(
                "SELECT payload_json FROM activity_facts WHERE source_id=? AND subevent_id='block:1'",
                (trip["trip_id"],),
            )
        )[0]
    )
    assert block["base_primary_units"] + block["bonus_primary_units"] == block["primary_units"]
    assert "exploration_before" in block and "forced" in block
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM achievement_unlocks"))[0] == 0


async def test_same_pig_two_teams_can_never_depart_together(world: World):
    await world.team(names="1星测试猪")
    async with world.db.transaction() as session:
        await session.execute("UPDATE dispatch_profiles SET effective_seconds=43200")
    await world.team(2, "1星测试猪")
    await world.start(slot=1)
    with pytest.raises(DispatchError, match="派遣"):
        await world.start(slot=2)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM asset_occupancies"))[0] == 1


@pytest.mark.parametrize(
    "scope",
    [
        ScopeKey("qq", "1092931381"),
        ScopeKey("qq", "237716658"),
        ScopeKey("qq-official", "5E5854406D0297D6FEAE696A13E3A339"),
        ScopeKey("qq-official", "9EA2810F378FBD7DC3219C56CEAB3520"),
    ],
)
async def test_all_four_target_group_scopes_use_same_dispatch_rules(world: World, scope):
    identity = replace(world.identity, scope=scope, message_id="new-group")
    await seed_pigs(world.db, identity, count=3)
    group = World(world.db, world.clock, world.service, identity)
    await group.team()
    await group.start()
    await group.advance(4)
    row = await world.db.fetch_one(
        "SELECT quantity,remainder_units FROM material_balances WHERE player_id=?", (identity.player_id,)
    )
    assert tuple(row) == (9, 4_000_000)
    assert not await world.db.fetch_all(
        "SELECT * FROM material_balances WHERE player_id=?", (world.identity.player_id,)
    )


async def test_recall_confirmation_at_exact_return_time_keeps_normal_completion(world: World):
    await world.team()
    await world.start()
    await world.advance(4 - 1 / 60)
    await world.send("召回 1")
    await world.advance(1 / 60)
    result = await world.send("确认")
    assert "平安归来" in result.view.title
    assert (await world.db.fetch_one("SELECT status FROM dispatch_trips"))[0] == "completed"
    assert all(row[0] == 4 for row in await world.db.fetch_all("SELECT normal_hours FROM dispatch_contributions"))
    assert not await world.db.fetch_all("SELECT * FROM activity_facts WHERE subevent_id='recalled'")


async def test_pending_is_last_operation_only_and_old_receipt_cannot_replace_it(world: World):
    await world.send("编队 1 1星测试猪", message_id="first-preview")
    await world.send("编队 1 1星测试猪、1星测试猪")
    await world.send("编队 1 1星测试猪", message_id="first-preview")
    await world.send("确认")
    assert len(json.loads((await world.db.fetch_one("SELECT member_ids_json FROM dispatch_teams"))[0])) == 2


@pytest.mark.parametrize("operation", ["DELETE FROM activity_facts", "UPDATE activity_facts SET occurred_ms=0"])
async def test_dispatch_facts_cannot_be_changed_or_deleted(world: World, operation):
    await world.team()
    await world.start()
    with pytest.raises(sqlite3.IntegrityError, match="活动事实"):
        async with world.db.transaction() as session:
            await session.execute(operation)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM activity_facts"))[0] == 1


async def test_occupancy_guards_owner_scope_and_physical_deletion(world: World):
    await world.team(names="1星测试猪")
    await world.start()
    other = replace(world.identity, scope=ScopeKey("qq", "different"), message_id="other-owner")
    await seed_pigs(world.db, other, count=1)
    for statement, args in (
        ("UPDATE asset_occupancies SET player_id=?", (other.player_id,)),
        ("UPDATE asset_occupancies SET scope_id=?", (other.scope.value,)),
        (
            "UPDATE pig_instances SET scope_id=? "
            "WHERE pig_instance_id IN (SELECT pig_instance_id FROM asset_occupancies)",
            (other.scope.value,),
        ),
        ("DELETE FROM pig_instances WHERE pig_instance_id IN (SELECT pig_instance_id FROM asset_occupancies)", ()),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            async with world.db.transaction() as session:
                await session.execute(statement, args)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM asset_occupancies"))[0] == 1


async def test_material_reconcile_finds_missing_balance_rows(world: World):
    await world.material("training-ore", 3)
    async with world.db.transaction() as session:
        await session.execute("DELETE FROM material_balances")  # 故障注入，不伪装修复为零余额。
        issues = await MaterialRepository().reconcile(session)
    assert len(issues) == 1 and issues[0]["total"] == 3 * MATERIAL_SCALE


async def test_current_schema_rejects_missing_dispatch_guard(world: World, tmp_path: Path):
    path = tmp_path / "missing-guard.sqlite3"
    await world.db.backup_to(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER occupied_pig_no_dispose")
    database = PigCatcherDatabase(path)
    with pytest.raises(MigrationError, match="活动占用"):
        await database.open()
    await database.close()


async def test_unread_returns_use_partial_index_not_full_history(world: World):
    plan = await world.db.fetch_all(
        "EXPLAIN QUERY PLAN SELECT * FROM dispatch_trips "
        "WHERE player_id=? AND status!='traveling' AND viewed=0 ORDER BY sequence LIMIT 3",
        (world.identity.player_id,),
    )
    assert any("idx_dispatch_unread_returns" in row["detail"] for row in plan)


def test_regions_have_distinct_authored_encounter_stories():
    for region in REGIONS_BY_ID.values():
        stories = {}
        for i in range(500):
            option = encounter_options(region, f"variety-{i}", 1, set(), camera=False, known=set())[0]
            stories[option["kind"]] = option["story"]
        assert len(stories) == len(set(stories.values())) == 5


async def test_encounter_reward_ledger_posts_at_return_but_keeps_travel_event_time(world: World):
    region = REGIONS_BY_ID["echo-mine"]
    seed = next(
        f"material-hit-{i}"
        for i in range(10_000)
        if random_at(f"material-hit-{i}", 1, "encounter") < 0.1
        and encounter_options(region, f"material-hit-{i}", 1, {"后勤"}, camera=False, known=set())[0]["kind"]
        == "materials"
    )
    world.service.seed_factory = lambda: seed
    await world.team()
    trip = await world.start("回声矿洞", 12)
    await world.advance(4)
    assert not await world.db.fetch_all("SELECT * FROM material_ledger")
    await world.advance(8)
    entries = await world.db.fetch_all("SELECT occurred_at FROM material_ledger WHERE source_id=?", (trip["trip_id"],))
    assert entries and all(row[0] == iso_ms(trip["ends_ms"]) for row in entries)
    fact = await world.db.fetch_one(
        "SELECT occurred_ms,payload_json FROM activity_facts WHERE source_id=? AND subevent_id='event:1'",
        (trip["trip_id"],),
    )
    assert fact["occurred_ms"] == trip["starts_ms"] + 4 * 3600_000
    assert json.loads(fact["payload_json"])["credited_ms"] == trip["ends_ms"]
