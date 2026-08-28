"""普通高星菜补给包的平衡定义、原子发放、来源隔离和既有消费路由。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from pig_catcher.domain.activity_achievements import ACTIVITY_REWARDS
from pig_catcher.domain.dispatch import MATERIAL_SCALE
from pig_catcher.domain.errors import AssetStateConflictError, FoodEffectError, ReceiptConflictError
from pig_catcher.domain.food_supplies import (
    FOOD_SUPPLY_PACK,
    FOOD_SUPPLY_PACKS,
    FoodSupplyReward,
    resolve_food_supply_pack,
    resolve_supply_pack,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.infrastructure.repositories.economy import EconomyRepository
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.infrastructure.repositories.materials import MaterialRepository
from pig_catcher.services.achievement_rewards import AchievementRewardService
from pig_catcher.services.achievements import AchievementService
from pig_catcher.services.food_supplies import FoodSupplyGrant, grant_food_supply_pack

from .test_economy import FixedClock, _database_with_catalog

NOW = "2026-07-28T04:00:00.000Z"
EXPECTED = {
    "sausage-pig": (4, {("material", "travel-supplies"): 18, ("ticket", "dispatch-bill"): 1}),
    "pig-fries": (4, {("material", "training-ore"): 12, ("material", "machine-parts"): 8}),
    "pig-cola": (4, {("material", "stage-components"): 12, ("ticket", "tour-steady-stage"): 1}),
    "pig-chocolate": (
        5,
        {("material", "training-ore"): 40, ("material", "agility-fiber"): 24, ("ticket", "training-rebate"): 2},
    ),
    "pig-burger-meal": (
        5,
        {
            ("chest", "materials-choice"): 48,
            ("ticket", "dispatch-luggage"): 1,
            ("ticket", "tour-date"): 1,
            ("ticket", "training-rebate"): 1,
        },
    ),
}


@pytest.fixture
async def db(tmp_path: Path):
    database = await _database_with_catalog(tmp_path, food_rarities=(4, 5))
    try:
        yield database
    finally:
        await database.close()


def _identity(scope: str = "qq:100", user: str = "200", message: str = "supply-eat") -> CommandIdentity:
    return CommandIdentity(ScopeKey.parse(scope), "supply-test-stream", user, "补给玩家", message)


async def _food(database, pack_id="sausage-pig", *, identity=None) -> tuple[str, CommandIdentity]:
    actor = identity or _identity()
    pack = resolve_supply_pack(pack_id)
    instance_id = uuid4().hex
    async with database.transaction() as session:
        await FrameworkRepository().touch_identity(session, identity=actor, now=NOW)
        await EconomyRepository().insert_food_instance(
            session,
            values={
                "food_instance_id": instance_id,
                "short_code": uuid4().hex[:8].upper(),
                "scope_id": actor.scope.value,
                "owner_player_id": actor.player_id,
                "template_id": f"food-{pack.food_rarity}-common",
                "template_version": 1,
                "source_pig_instance_id": None,
                "rarity": pack.food_rarity,
                "display_name_snapshot": pack.food_name,
                "portion_weight": 3.0,
                "fat_category": "balanced",
                "official_value": 800,
                "effect_id": FOOD_SUPPLY_PACK,
                "effect_params_json": json.dumps({"pack_id": pack_id}),
                "ruleset_version": 37,
                "random_snapshot_json": "{}",
                "acquired_at": NOW,
                "updated_at": NOW,
            },
        )
    return instance_id, actor


async def _consume(session, food_id: str, actor: CommandIdentity) -> None:
    assert await EconomyRepository().consume_food(
        session, food_instance_id=food_id, player_id=actor.player_id, scope_id=actor.scope.value, now=NOW
    )


async def _grant(database, food_id, actor, pack_id, *, source="eat-key", consume=True):
    async with database.transaction() as session:
        if consume:
            await _consume(session, food_id, actor)
        return await grant_food_supply_pack(
            session, identity=actor, food_instance_id=food_id, source_key=source, pack_id=pack_id, now=NOW
        )


async def _quantities(database, actor) -> dict[tuple[str, str], int]:
    async with database.transaction(immediate=False) as session:
        materials = await MaterialRepository().balances(session, actor.player_id)
        rewards = await AchievementRepository().reward_rows(session, player_id=actor.player_id)
    return {
        **{("material", key): value for key, value in materials.items()},
        **{(str(row["reward_type"]), str(row["reward_id"])): int(row["quantity"]) for row in rewards},
    }


@pytest.mark.parametrize("pack_id", EXPECTED)
def test_supply_catalog_matches_reviewed_quantities(pack_id):
    pack = resolve_food_supply_pack({"pack_id": pack_id})
    rarity, quantities = EXPECTED[pack_id]
    assert pack.food_rarity == rarity
    assert {(reward.kind, reward.reward_id): reward.quantity for reward in pack.rewards} == quantities
    assert all(reward.name and reward.use_hint for reward in pack.rewards)
    assert "不自动使用" in pack.summary and "不改变抓猪或做菜概率" in pack.summary
    assert len({item.summary for item in FOOD_SUPPLY_PACKS.values()}) == 5
    if pack_id == "pig-cola":
        assert "安可稳场券" in pack.summary


@pytest.mark.parametrize("params", [{}, {"pack_id": "missing"}, {"pack_id": 1}, {"pack_id": "pig-cola", "coins": 99}])
def test_supply_params_reject_unknown_arbitrary_grants(params):
    with pytest.raises(FoodEffectError):
        resolve_food_supply_pack(params)


@pytest.mark.parametrize(
    ("kind", "key", "quantity"),
    [
        ("material", "training-ore", 0),
        ("material", "training-ore", True),
        ("material", "training-ore", 1.5),
        ("material", "training-ore", 10001),
        ("material", "fake-material", 1),
        ("ticket", "fake-ticket", 1),
        ("chest", "dispatch-bill", 1),
        ("coin", "pig-coin", 1),
    ],
)
def test_supply_declarations_reject_invalid_rewards(kind, key, quantity):
    with pytest.raises(FoodEffectError):
        FoodSupplyReward(kind, key, quantity)


@pytest.mark.parametrize("pack_id", EXPECTED)
async def test_supply_grants_existing_inventory_without_natural_activity(db, pack_id):
    food_id, actor = await _food(db, pack_id)
    result = await _grant(db, food_id, actor, pack_id)
    assert not result.replayed
    assert await _quantities(db, actor) == EXPECTED[pack_id][1]
    assert {(item.kind, item.reward_id): item.balance_after for item in result.items} == EXPECTED[pack_id][1]
    assert FoodSupplyGrant.from_payload(result.payload()) == result
    assert all(item.name in result.summary for item in result.items)
    for table in (
        "activity_facts",
        "achievement_events",
        "achievement_unlocks",
        "achievement_activity_queue",
        "achievement_coupon_selection",
        "achievement_coupon_uses",
        "player_food_effects",
        "currency_ledger",
    ):
        assert (await db.fetch_one(f"SELECT COUNT(*) FROM {table}"))[0] == 0
    records = await db.fetch_all("SELECT * FROM material_ledger")
    assert len(records) == sum(kind == "material" for kind, _ in EXPECTED[pack_id][1])
    assert all(row["source_kind"] == FOOD_SUPPLY_PACK and row["source_id"] == food_id for row in records)
    async with db.transaction(immediate=False) as session:
        assert await MaterialRepository().reconcile(session) == []
    operation = await db.fetch_one("SELECT * FROM achievement_operations")
    audit = await db.fetch_one("SELECT * FROM audit_events")
    assert operation["operation_type"] == FOOD_SUPPLY_PACK
    assert audit["action"] == "food-supply-pack-granted"
    assert operation["result_json"] == audit["detail_json"]
    food = await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,))
    assert food[0] == "consumed"


async def test_supply_replay_survives_spending_and_restart(db):
    food_id, actor = await _food(db, "pig-burger-meal")
    initial = await _grant(db, food_id, actor, "pig-burger-meal")
    async with db.transaction() as session:
        assert await AchievementRepository().consume_reward(
            session, player_id=actor.player_id, reward_type="chest", reward_id="materials-choice", quantity=7, now=NOW
        )
    for restart in (False, True):
        if restart:
            await db.close()
            await db.open()
        replay = await _grant(db, food_id, actor, "pig-burger-meal", consume=False)
        assert replay.replayed
        assert replay.payload() == initial.payload()
        assert (await _quantities(db, actor))["chest", "materials-choice"] == 41
    assert (await db.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 1
    assert (await db.fetch_one("SELECT COUNT(*) FROM audit_events"))[0] == 1


@pytest.mark.parametrize("conflict", ["new-source", "new-food", "new-pack"])
async def test_supply_food_and_source_keys_each_prevent_duplicate_grants(db, conflict):
    food_id, actor = await _food(db)
    second_id, _ = await _food(db)
    await _grant(db, food_id, actor, "sausage-pig")
    with pytest.raises(ReceiptConflictError):
        async with db.transaction() as session:
            selected_food = second_id if conflict == "new-food" else food_id
            if conflict == "new-food":
                await _consume(session, second_id, actor)
            await grant_food_supply_pack(
                session,
                identity=actor,
                food_instance_id=selected_food,
                source_key="changed-key" if conflict == "new-source" else "eat-key",
                pack_id="pig-cola" if conflict == "new-pack" else "sausage-pig",
                now=NOW,
            )
    assert await _quantities(db, actor) == EXPECTED["sausage-pig"][1]
    second_food = await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (second_id,))
    assert second_food[0] == "active"


async def test_supply_stacks_separate_foods_and_keeps_material_remainders(db):
    food_id, actor = await _food(db)
    other_id, _ = await _food(db)
    async with db.transaction() as session:
        await MaterialRepository().change(
            session,
            player_id=actor.player_id,
            scope_id=actor.scope.value,
            material_id="travel-supplies",
            delta_units=7_000_000,
            source_kind="test-seed",
            source_id="test-seed",
            entry_key="fractional-supplies",
            now=NOW,
        )
    await _grant(db, food_id, actor, "sausage-pig", source="first")
    second = await _grant(db, other_id, actor, "sausage-pig", source="second")
    assert {item.reward_id: item.balance_after for item in second.items} == {"travel-supplies": 36, "dispatch-bill": 2}
    row = await db.fetch_one("SELECT * FROM material_balances WHERE player_id=?", (actor.player_id,))
    assert row["quantity"] == 36 and row["remainder_units"] == 7_000_000
    async with db.transaction(immediate=False) as session:
        assert await MaterialRepository().reconcile(session) == []


@pytest.mark.parametrize("failure", ["in-reward", "after-helper"])
async def test_supply_rolls_back_food_material_coupon_and_audit(db, monkeypatch, failure):
    food_id, actor = await _food(db)
    original = AchievementRepository.grant_reward

    async def broken_grant(self, session, **kwargs):
        await original(self, session, **kwargs)
        raise RuntimeError("injected reward failure")

    if failure == "in-reward":
        monkeypatch.setattr(AchievementRepository, "grant_reward", broken_grant)
    with pytest.raises(RuntimeError, match="injected"):
        async with db.transaction() as session:
            await _consume(session, food_id, actor)
            await grant_food_supply_pack(
                session, identity=actor, food_instance_id=food_id, source_key="eat-key", pack_id="sausage-pig", now=NOW
            )
            raise RuntimeError("injected post-helper failure")
    assert await _quantities(db, actor) == {}
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "active"
    for table in ("material_ledger", "achievement_operations", "audit_events"):
        assert (await db.fetch_one(f"SELECT COUNT(*) FROM {table}"))[0] == 0
    monkeypatch.setattr(AchievementRepository, "grant_reward", original)
    result = await _grant(db, food_id, actor, "sausage-pig")
    assert not result.replayed
    assert await _quantities(db, actor) == EXPECTED["sausage-pig"][1]


async def test_supply_same_message_and_same_user_in_four_scopes_remain_independent(db):
    scopes = (
        "qq:1092931381",
        "qq:237716658",
        "qq-official:5E5854406D0297D6FEAE696A13E3A339",
        "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
    )
    for scope in scopes:
        food_id, actor = await _food(db, "pig-chocolate", identity=_identity(scope))
        await _grant(db, food_id, actor, "pig-chocolate", source="shared-message")
    for scope in scopes:
        assert await _quantities(db, _identity(scope)) == EXPECTED["pig-chocolate"][1]
    assert (await db.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 4
    assert (await db.fetch_one("SELECT COUNT(*) FROM audit_events"))[0] == 4


@pytest.mark.parametrize("invalid_actor", [_identity("qq:101"), _identity("qq:100", "201")])
async def test_supply_rejects_other_groups_or_owners(db, invalid_actor):
    food_id, actor = await _food(db)
    await _food(db, identity=invalid_actor)
    with pytest.raises(AssetStateConflictError):
        async with db.transaction() as session:
            await _consume(session, food_id, actor)
            await grant_food_supply_pack(
                session,
                identity=invalid_actor,
                food_instance_id=food_id,
                source_key="eat-key",
                pack_id="sausage-pig",
                now=NOW,
            )
    assert await _quantities(db, actor) == await _quantities(db, invalid_actor) == {}
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "active"


@pytest.mark.parametrize("mutation", ["not-consumed", "wrong-rarity", "wrong-effect", "wrong-pack", "bad-json"])
async def test_supply_requires_matching_consumed_food_snapshot(db, mutation):
    food_id, actor = await _food(db)
    with pytest.raises((AssetStateConflictError, FoodEffectError)):
        async with db.transaction() as session:
            if mutation != "not-consumed":
                await _consume(session, food_id, actor)
            if mutation == "wrong-rarity":
                await session.execute("UPDATE food_instances SET rarity=5 WHERE food_instance_id=?", (food_id,))
            elif mutation == "wrong-effect":
                await session.execute("UPDATE food_instances SET effect_id='' WHERE food_instance_id=?", (food_id,))
            elif mutation in {"wrong-pack", "bad-json"}:
                await session.execute(
                    "UPDATE food_instances SET effect_params_json=? WHERE food_instance_id=?",
                    (json.dumps({"pack_id": "pig-cola"}) if mutation == "wrong-pack" else "broken-json", food_id),
                )
            await grant_food_supply_pack(
                session, identity=actor, food_instance_id=food_id, source_key="eat-key", pack_id="sausage-pig", now=NOW
            )
    assert await _quantities(db, actor) == {}


async def test_supply_parallel_replays_grant_only_once(db):
    food_id, actor = await _food(db, "pig-burger-meal")
    async with db.transaction() as session:
        await _consume(session, food_id, actor)
    results = await asyncio.gather(
        *(_grant(db, food_id, actor, "pig-burger-meal", consume=False) for _ in range(5))
    )
    assert sum(not item.replayed for item in results) == 1
    assert await _quantities(db, actor) == EXPECTED["pig-burger-meal"][1]


@pytest.mark.parametrize(
    ("pack_id", "ticket_id"),
    [
        ("sausage-pig", "dispatch-bill"),
        ("pig-cola", "tour-steady-stage"),
        ("pig-chocolate", "training-rebate"),
        ("pig-burger-meal", "dispatch-luggage"),
    ],
)
async def test_supply_tickets_work_in_existing_selection_command(db, pack_id, ticket_id):
    food_id, actor = await _food(db, pack_id)
    await _grant(db, food_id, actor, pack_id)
    service = AchievementRewardService(AchievementService(db, clock=FixedClock()))
    before = await _quantities(db, actor)
    selected = await service.execute(
        replace(actor, message_id="select-ticket"), "使用 " + ACTIVITY_REWARDS[ticket_id]["name"]
    )
    assert "尚未扣券" in selected.view.banner
    assert await _quantities(db, actor) == before
    row = await db.fetch_one("SELECT ticket_id FROM achievement_coupon_selection WHERE player_id=?", (actor.player_id,))
    assert row[0] == ticket_id


async def test_supply_choice_reuses_exact_quantity_confirmation_and_bag(db):
    food_id, actor = await _food(db, "pig-burger-meal")
    await _grant(db, food_id, actor, "pig-burger-meal")
    service = AchievementRewardService(AchievementService(db, clock=FixedClock()))
    bag = await service.execute(actor, "查看")
    assert all(ACTIVITY_REWARDS[key]["name"] in bag.view.text() for _, key in EXPECTED["pig-burger-meal"][1])
    await service.execute(replace(actor, message_id="preview-material"), "材料 基础材料自选份 训练矿石 17")
    assert (await _quantities(db, actor))["chest", "materials-choice"] == 48
    confirmation = replace(actor, message_id="confirm-material")
    await service.execute(confirmation, "确认")
    await service.execute(confirmation, "确认")
    current = await _quantities(db, actor)
    assert current["chest", "materials-choice"] == 31
    assert current["material", "training-ore"] == 17
    ledger = await db.fetch_one("SELECT source_kind,delta_units FROM material_ledger")
    assert ledger["source_kind"] == "achievement-choice" and ledger["delta_units"] == 17 * MATERIAL_SCALE
    assert (await db.fetch_one("SELECT COUNT(*) FROM activity_facts"))[0] == 0
