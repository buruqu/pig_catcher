"""绿芯派五分支、原料专属排除、四群授权、原子发奖和不可重抽验收。"""

from __future__ import annotations

import asyncio
import json
import math
from collections import Counter
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from pig_catcher.domain.economy import generate_food_attributes
from pig_catcher.domain.errors import AssetStateConflictError, FoodEffectError, ReceiptConflictError
from pig_catcher.domain.food_lottery import HINA_PIG_TEMPLATE_ID, LOTTERY_PRIZES, YILU_LOTTERY, choose_lottery_prize
from pig_catcher.domain.gameplay import generate_pig_attributes
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.special_content import SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS
from pig_catcher.infrastructure.repositories.economy import EconomyRepository
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.infrastructure.repositories.gameplay import GameplayRepository
from pig_catcher.services import food_lottery as lottery_module
from pig_catcher.services.food_lottery import FoodLotteryGrant, grant_food_lottery

from .test_economy import SequenceRandom, _database_with_catalog, _food_entry, _pig_entry

NOW = "2026-08-28T03:00:00.000Z"
SCOPES = (
    "qq:1092931381",
    "qq:237716658",
    "qq-official:5E5854406D0297D6FEAE696A13E3A339",
    "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
)
BRANCHES = (
    (0.1, "five-star-feast"),
    (0.5, "six-star-taste"),
    (0.85, "six-star-double"),
    (0.95, "hina-guest"),
    (0.999, "six-star-jackpot"),
)


def _identity(scope=SCOPES[0], user="200", message="lottery-eat"):
    return CommandIdentity(ScopeKey.parse(scope), "lottery-test-stream", user, "绿芯玩家", message)


def _six_template(scope, variant="yilu"):
    return f"food-lottery-scope-{SCOPES.index(scope)}-{variant}"


@pytest.fixture
async def db(tmp_path: Path):
    entries = []
    hina = _pig_entry(5, template_suffix="hina")
    hina.update(template_id=HINA_PIG_TEMPLATE_ID, display_name="天才猪")
    entries.append(hina)
    entries.append(_food_entry(5, template_suffix="ordinary-second"))
    disabled = _food_entry(5, template_suffix="disabled")
    disabled["template_id"] = "food-five-disabled"
    entries.append(disabled)
    for index, template_id in enumerate(sorted(SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS)):
        entry = _food_entry(5, template_suffix=f"exclusive-{index}")
        entry["template_id"] = template_id
        entries.append(entry)
    for index, scope in enumerate(SCOPES):
        for variant in ("yilu", "first", "second"):
            entry = _food_entry(6, group_id=f"scope-{index}", template_suffix=f"{index}-{variant}")
            entry.update(
                template_id=_six_template(scope, variant),
                group_scope_id=scope,
                display_name="熠～噜猪绿芯小猪派" if variant == "yilu" else f"群{index}专属菜{variant}",
                effect_id=YILU_LOTTERY if variant == "yilu" else "",
            )
            entries.append(entry)
    database = await _database_with_catalog(tmp_path, food_rarities=(5,), extra_entries=tuple(entries))
    async with database.transaction() as session:
        await session.execute("UPDATE food_templates SET enabled=0 WHERE template_id='food-five-disabled'")
    try:
        yield database
    finally:
        await database.close()


async def _food(database, *, identity=None, short_code=None):
    actor = identity or _identity()
    food_id = uuid4().hex
    async with database.transaction() as session:
        await FrameworkRepository().touch_identity(session, identity=actor, now=NOW)
        await EconomyRepository().insert_food_instance(
            session,
            values={
                "food_instance_id": food_id,
                "short_code": short_code or uuid4().hex[:8].upper(),
                "scope_id": actor.scope.value,
                "owner_player_id": actor.player_id,
                "template_id": _six_template(actor.scope.value),
                "template_version": 1,
                "source_pig_instance_id": None,
                "rarity": 6,
                "display_name_snapshot": "熠～噜猪绿芯小猪派",
                "portion_weight": 25.5,
                "fat_category": "balanced",
                "official_value": 25000,
                "effect_id": YILU_LOTTERY,
                "effect_params_json": "{}",
                "ruleset_version": 37,
                "random_snapshot_json": "{}",
                "acquired_at": NOW,
                "updated_at": NOW,
            },
        )
    return food_id, actor


async def _consume(session, food_id, actor):
    assert await EconomyRepository().consume_food(
        session, food_instance_id=food_id, player_id=actor.player_id, scope_id=actor.scope.value, now=NOW
    )


def _random(roll, template_roll=0.0):
    prize = choose_lottery_prize(roll)
    values = [0.5] * 5 if prize.kind == "pig" else [template_roll, 0.5] * prize.quantity
    return SequenceRandom(roll, *values)


async def _grant(database, food_id, actor, random_source, *, source="eat-key", consume=True):
    async with database.transaction() as session:
        if consume:
            await _consume(session, food_id, actor)
        return await grant_food_lottery(
            session,
            identity=actor,
            food_instance_id=food_id,
            source_key=source,
            now=NOW,
            random_source=random_source,
        )


@pytest.mark.parametrize(
    ("roll", "expected"),
    [
        (0.0, "five-star-feast"),
        (math.nextafter(0.3, 0), "five-star-feast"),
        (0.3, "six-star-taste"),
        (math.nextafter(0.80113, 0), "six-star-taste"),
        (0.80113, "six-star-double"),
        (math.nextafter(0.89583, 0), "six-star-double"),
        (0.89583, "hina-guest"),
        (math.nextafter(0.99053, 0), "hina-guest"),
        (0.99053, "six-star-jackpot"),
        (math.nextafter(1.0, 0), "six-star-jackpot"),
    ],
)
def test_lottery_branch_boundaries(roll, expected):
    assert choose_lottery_prize(roll).prize_id == expected


def test_lottery_integer_probability_mass_matches_requested_five_branches():
    counts = Counter(choose_lottery_prize((index + 0.5) / 100_000).prize_id for index in range(100_000))
    assert counts == {prize.prize_id: prize.weight for prize in LOTTERY_PRIZES}
    assert sum(counts.values()) == 100_000


@pytest.mark.parametrize("roll", [-0.001, 1.0, float("nan"), float("inf"), float("-inf")])
def test_lottery_invalid_random_values_fail_closed(roll):
    with pytest.raises(FoodEffectError):
        choose_lottery_prize(roll)


@pytest.mark.parametrize(("roll", "prize_id"), BRANCHES)
async def test_lottery_branches_grant_exact_assets_and_no_fake_gameplay(db, roll, prize_id):
    food_id, actor = await _food(db)
    random = _random(roll)
    result = await _grant(db, food_id, actor, random)
    prize = choose_lottery_prize(roll)
    assert result.prize_id == prize_id and result.animation == prize.animation
    assert len(result.items) == prize.quantity and not result.replayed
    assert random.values == []
    assert FoodLotteryGrant.from_payload(result.payload()) == result
    assert all(item.rarity == prize.rarity and item.kind == prize.kind for item in result.items)
    assert len({item.instance_id for item in result.items}) == prize.quantity
    assert len({item.short_code.casefold() for item in result.items}) == prize.quantity
    assert all(
        item.short_code.isalnum() and item.short_code == item.short_code.upper() and len(item.short_code) == 8
        for item in result.items
    )
    assert all(item.name and item.value > 0 and item.asset_path and item.media_format == "PNG" for item in result.items)
    table = "pig_instances" if prize.kind == "pig" else "food_instances"
    id_column = "pig_instance_id" if prize.kind == "pig" else "food_instance_id"
    for item in result.items:
        row = await db.fetch_one(f"SELECT * FROM {table} WHERE {id_column}=?", (item.instance_id,))
        assert row["state"] == "active" and row["scope_id"] == actor.scope.value
        assert row["owner_player_id"] == actor.player_id and row["official_value"] == item.value
        snapshot = json.loads(row["random_snapshot_json"])
        assert snapshot["source"] == YILU_LOTTERY
        assert snapshot["source_food_instance_id"] == food_id
        assert snapshot["prize_id"] == prize_id
        assert snapshot["statistics_incremented"] is False and snapshot["gameplay_rewards_applied"] is False
        if item.kind == "food":
            assert row["source_pig_instance_id"] is None
            expected = generate_food_attributes(
                rarity=item.rarity,
                template_id=item.template_id,
                source_weight=60.0,
                source_weight_percentile=0.5,
                portion_roll=0.5,
            )
            assert row["portion_weight"] == expected.portion_weight and item.value == expected.official_value
        else:
            assert item.template_id == HINA_PIG_TEMPLATE_ID
            expected = generate_pig_attributes(
                rarity=5,
                length_min=30,
                length_max=70,
                weight_min=20,
                weight_max=120,
                fat_profile="balanced",
                random_values=(0.5,) * 5,
            )
            assert row["size_value"] == expected.size_value and row["weight_value"] == expected.weight_value
            assert row["official_value"] == expected.official_value
    for name in ("currency_ledger", "activity_facts", "achievement_events", "group_records", "giant_sightings"):
        assert (await db.fetch_one(f"SELECT COUNT(*) FROM {name}"))[0] == 0
    player = await db.fetch_one("SELECT coin_balance,experience FROM players WHERE player_id=?", (actor.player_id,))
    stats = await db.fetch_one(
        "SELECT total_catches,total_cooks FROM player_statistics WHERE player_id=?", (actor.player_id,)
    )
    assert tuple(player) == tuple(stats) == (0, 0)
    catalog = "pig_catalog_entries" if prize.kind == "pig" else "food_catalog_entries"
    assert (await db.fetch_one(f"SELECT SUM(acquired_count) FROM {catalog} WHERE player_id=?", (actor.player_id,)))[
        0
    ] == prize.quantity
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[
        0
    ] == "consumed"


@pytest.mark.parametrize("scope", SCOPES)
async def test_lottery_six_star_pool_is_authorized_per_exact_scope(db, scope):
    food_id, actor = await _food(db, identity=_identity(scope))
    result = await _grant(db, food_id, actor, _random(0.999, template_roll=0.999), source="shared-message")
    assert len(result.items) == 6
    assert all(item.template_id.startswith(f"food-lottery-scope-{SCOPES.index(scope)}-") for item in result.items)
    operation = await db.fetch_one(
        "SELECT result_json FROM achievement_operations WHERE player_id=?", (actor.player_id,)
    )
    pool = json.loads(operation[0])["template_pool_ids"]
    assert set(pool) == {_six_template(scope, variant) for variant in ("yilu", "first", "second")}


async def test_lottery_same_user_and_message_are_independent_in_four_groups(db):
    for scope in SCOPES:
        food_id, actor = await _food(db, identity=_identity(scope))
        result = await _grant(db, food_id, actor, _random(0.999), source="same-message")
        assert len(result.items) == 6 and not result.replayed
    for scope in SCOPES:
        owned = await db.fetch_all(
            "SELECT scope_id,template_id FROM food_instances WHERE owner_player_id=?", (_identity(scope).player_id,)
        )
        assert len(owned) == 7
        assert all(row["scope_id"] == scope for row in owned)
        assert all(row["template_id"].startswith(f"food-lottery-scope-{SCOPES.index(scope)}-") for row in owned)
    assert (await db.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 4
    assert (await db.fetch_one("SELECT COUNT(*) FROM audit_events"))[0] == 4


async def test_lottery_five_star_pool_never_contains_source_exclusives_or_disabled_food(db):
    food_id, actor = await _food(db)
    random = SequenceRandom(0.1, *[value for index in range(10) for value in (0.05 + index / 10, 0.5)])
    result = await _grant(db, food_id, actor, random)
    assert {item.template_id for item in result.items} == {"food-5-common", "food-5-common-ordinary-second"}
    operation = await db.fetch_one("SELECT result_json FROM achievement_operations")
    pool = set(json.loads(operation[0])["template_pool_ids"])
    assert pool.isdisjoint(SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS | {"food-five-disabled"})
    assert len(pool) == 2


@pytest.mark.parametrize("restriction", ["disabled", "template-revoked", "scope-revoked", "scope-unauthorized"])
async def test_lottery_rejects_revoked_reward_pool_without_consuming_source(db, restriction):
    food_id, actor = await _food(db)
    async with db.transaction() as session:
        if restriction == "disabled":
            await session.execute("UPDATE food_templates SET enabled=0 WHERE rarity=6")
        elif restriction == "template-revoked":
            await session.execute("UPDATE food_templates SET consent_status='revoked' WHERE rarity=6")
        elif restriction == "scope-revoked":
            await session.execute(
                "UPDATE scope_food_templates SET consent_status='revoked' WHERE scope_id=?", (actor.scope.value,)
            )
        else:
            await session.execute("UPDATE scope_food_templates SET authorized=0 WHERE scope_id=?", (actor.scope.value,))
    with pytest.raises(FoodEffectError, match="授权"):
        await _grant(db, food_id, actor, SequenceRandom(0.5))
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "active"
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 1
    assert (await db.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 0


@pytest.mark.parametrize("branch", [0.1, 0.95])
async def test_lottery_missing_ordinary_food_or_exact_hina_does_not_substitute_other_assets(db, branch):
    food_id, actor = await _food(db)
    async with db.transaction() as session:
        if branch == 0.1:
            await session.execute(
                "UPDATE food_templates SET enabled=0 WHERE template_id IN "
                "('food-5-common','food-5-common-ordinary-second')"
            )
        else:
            await session.execute("UPDATE pig_templates SET enabled=0 WHERE template_id=?", (HINA_PIG_TEMPLATE_ID,))
    with pytest.raises(FoodEffectError):
        await _grant(db, food_id, actor, SequenceRandom(branch))
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 1
    assert (await db.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 0


async def test_lottery_may_draw_itself_repeatedly_without_chain_consumption(db):
    food_id, actor = await _food(db)
    async with db.transaction() as session:
        await session.execute(
            "UPDATE food_templates SET enabled=0 WHERE rarity=6 AND template_id<>?", (_six_template(actor.scope.value),)
        )
    result = await _grant(db, food_id, actor, _random(0.999))
    assert all(item.template_id == _six_template(actor.scope.value) for item in result.items)
    assert [item.is_new for item in result.items] == [True, False, False, False, False, False]
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances WHERE state='active'"))[0] == 6
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances WHERE state='consumed'"))[0] == 1
    assert (await db.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 1


async def test_lottery_replay_after_restart_or_reward_sale_never_reads_random_or_grants_again(db):
    food_id, actor = await _food(db)
    first = await _grant(db, food_id, actor, _random(0.1))
    async with db.transaction() as session:
        await session.execute(
            "UPDATE food_instances SET state='sold',disposed_at=? WHERE food_instance_id=?",
            (NOW, first.items[0].instance_id),
        )
    for restart in (False, True):
        if restart:
            await db.close()
            await db.open()
        replay = await _grant(db, food_id, actor, SequenceRandom(), consume=False)
        assert replay.replayed and replay.payload() == first.payload()
        assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 11
        assert (await db.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 1


@pytest.mark.parametrize("conflict", ["new-source", "new-food"])
async def test_lottery_two_unique_boundaries_prevent_source_reuse(db, conflict):
    food_id, actor = await _food(db)
    other_id, _ = await _food(db)
    await _grant(db, food_id, actor, _random(0.5))
    with pytest.raises(ReceiptConflictError):
        if conflict == "new-source":
            await _grant(db, food_id, actor, SequenceRandom(), source="new-key", consume=False)
        else:
            await _grant(db, other_id, actor, SequenceRandom())
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (other_id,)))[0] == "active"
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 3


@pytest.mark.parametrize(("branch", "failure"), [(0.1, "mid-food"), (0.95, "pig-insert"), (0.999, "after-helper")])
async def test_lottery_failure_rolls_back_every_reward_source_and_catalog(db, monkeypatch, branch, failure):
    food_id, actor = await _food(db)
    original_food = EconomyRepository.insert_food_instance
    original_pig = GameplayRepository.insert_pig_instance
    calls = 0

    async def broken_food(self, session, *, values):
        nonlocal calls
        await original_food(self, session, values=values)
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second food failure")

    async def broken_pig(self, session, *, values):
        await original_pig(self, session, values=values)
        raise RuntimeError("injected pig failure")

    if failure == "mid-food":
        monkeypatch.setattr(EconomyRepository, "insert_food_instance", broken_food)
    elif failure == "pig-insert":
        monkeypatch.setattr(GameplayRepository, "insert_pig_instance", broken_pig)
    with pytest.raises(RuntimeError, match="injected"):
        async with db.transaction() as session:
            await _consume(session, food_id, actor)
            await grant_food_lottery(
                session,
                identity=actor,
                food_instance_id=food_id,
                source_key="eat-key",
                now=NOW,
                random_source=_random(branch),
            )
            raise RuntimeError("injected post-grant failure")
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "active"
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 1
    for table in (
        "pig_instances",
        "food_catalog_entries",
        "pig_catalog_entries",
        "audit_events",
        "achievement_operations",
    ):
        assert (await db.fetch_one(f"SELECT COUNT(*) FROM {table}"))[0] == 0


@pytest.mark.parametrize("invalid", ["other-scope", "other-user", "not-consumed", "wrong-effect", "wrong-rarity"])
async def test_lottery_validates_food_owner_and_effect_before_any_roll(db, invalid):
    food_id, actor = await _food(db)
    with pytest.raises((AssetStateConflictError, FoodEffectError)):
        async with db.transaction() as session:
            if invalid != "not-consumed":
                await _consume(session, food_id, actor)
            if invalid == "wrong-effect":
                await session.execute("UPDATE food_instances SET effect_id='' WHERE food_instance_id=?", (food_id,))
            elif invalid == "wrong-rarity":
                await session.execute("UPDATE food_instances SET rarity=5 WHERE food_instance_id=?", (food_id,))
            acting = (
                _identity(SCOPES[1])
                if invalid == "other-scope"
                else replace(actor, user_id="201")
                if invalid == "other-user"
                else actor
            )
            await grant_food_lottery(
                session,
                identity=acting,
                food_instance_id=food_id,
                source_key="eat-key",
                now=NOW,
                random_source=SequenceRandom(),
            )
    assert (await db.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 0


async def test_lottery_code_collisions_are_case_insensitive_and_consumed_code_is_reusable(db, monkeypatch):
    food_id, actor = await _food(db, short_code="SOURCE01")
    async with db.transaction() as session:
        await GameplayRepository().insert_pig_instance(
            session,
            values={
                "pig_instance_id": "code-blocker-pig",
                "short_code": "SHAREDAA",
                "scope_id": actor.scope.value,
                "owner_player_id": actor.player_id,
                "template_id": "pig-1-common",
                "template_version": 1,
                "rarity": 1,
                "display_name_snapshot": "编号测试猪",
                "size_value": 50,
                "size_percentile": 0.5,
                "weight_value": 70,
                "weight_percentile": 0.5,
                "fat_ratio": 50,
                "official_value": 100,
                "ruleset_version": 37,
                "random_snapshot_json": "{}",
                "acquired_at": NOW,
                "updated_at": NOW,
            },
        )
    codes = iter(("sharedaa", "source01", "SoUrCe01", "UNIQUEB2"))
    monkeypatch.setattr(lottery_module, "new_short_code", lambda: next(codes))
    result = await _grant(db, food_id, actor, _random(0.85))
    assert [item.short_code for item in result.items] == ["SOURCE01", "UNIQUEB2"]
    historical = await db.fetch_one("SELECT short_code,state FROM food_instances WHERE food_instance_id=?", (food_id,))
    assert tuple(historical) == ("SOURCE01", "consumed")
    assert all(item.instance_id != food_id for item in result.items)


async def test_lottery_exhausted_code_generator_rolls_back_source(db, monkeypatch):
    food_id, actor = await _food(db)
    await _food(db, short_code="BLOCK001")
    monkeypatch.setattr(lottery_module, "new_short_code", lambda: "block001")
    with pytest.raises(AssetStateConflictError, match="编号"):
        await _grant(db, food_id, actor, SequenceRandom(0.5, 0.0))
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "active"
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 2


async def test_lottery_parallel_replays_draw_only_once(db):
    food_id, actor = await _food(db)
    async with db.transaction() as session:
        await _consume(session, food_id, actor)
    sources = [_random(0.999) for _ in range(5)]
    results = await asyncio.gather(*(_grant(db, food_id, actor, random, consume=False) for random in sources))
    assert sum(not result.replayed for result in results) == 1
    assert sum(not random.values for random in sources) == 1
    assert all(result.payload() == results[0].payload() for result in results)
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 7
