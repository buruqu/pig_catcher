"""西西专属奖池、事务回放、队列独占与正常额度验收。"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from uuid import uuid4

import pytest

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.errors import (
    CookingTemplateError,
    DailyCatchLimitError,
    FoodEffectError,
    FoodNotFoundError,
    StoreProductError,
)
from pig_catcher.domain.feasts import CLOVER_CATCH
from pig_catcher.domain.food_effects import (
    ActiveFoodEffect,
    apply_catch_effects,
    apply_cooking_effects,
)
from pig_catcher.domain.xixi_feast import (
    DANIYA_BIRTHDAY_FEAST,
    XIXI_SIX_STAR_COOK,
    XIXI_STAR_CHEESE_LOTTERY,
    XIXI_TARGETED_CATCH,
    choose_star_cheese_prize,
    scoped_template_id,
    targeted_catch_templates,
)
from pig_catcher.infrastructure.repositories.economy import EconomyRepository
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.services import EconomyService, GameplayService

from .test_economy import FixedClock, SequenceRandom, _database_with_catalog, _food_entry, _identity, _pig_entry


@pytest.mark.parametrize(
    "roll,branch",
    [
        (0, "five-star"),
        (math.nextafter(0.1, 0), "five-star"),
        (0.1, "daniya-peach"),
        (math.nextafter(0.5, 0), "daniya-peach"),
        (0.5, "xixi-mandarin"),
        (math.nextafter(0.9, 0), "xixi-mandarin"),
        (0.9, "daniya-birthday-cake"),
        (math.nextafter(1, 0), "daniya-birthday-cake"),
    ],
)
def test_exact_branch_boundaries(roll, branch):
    assert choose_star_cheese_prize(roll) == branch


@pytest.mark.parametrize("roll", [-0.1, 1, float("nan"), float("inf")])
def test_invalid_roll_fails_closed(roll):
    with pytest.raises(FoodEffectError):
        choose_star_cheese_prize(roll)


def _effect(effect_id, params, entry="xixi", created="2026-07-28T04:00:00Z"):
    return ActiveFoodEffect(entry, effect_id, params, 1, 0, "", created, 6, "西西测试菜")


def test_fixed_cook_guarantees_low_star_preservation_and_fifo():
    peach = _effect(XIXI_SIX_STAR_COOK, {"six_star_percent": 60})
    ordinary = _effect("next-six-star-cook-bonus", {"bonus_percent": 10}, "ordinary")
    result = apply_cooking_effects((0, 0, 0, 0, 80, 20), [peach, ordinary], source_rarity=6)
    assert result.weights == (0, 0, 0, 0, 40, 60)
    assert result.consumed_entry_ids == ("xixi",)
    assert not apply_cooking_effects((0, 0, 70, 20, 10, 0), [peach], source_rarity=3).consumed_entry_ids
    cake = replace(peach, params={"six_star_percent": 100})
    assert apply_cooking_effects((0, 0, 0, 0, 90, 10), [cake], source_rarity=6).weights[-1] == 100
    preceding = _effect("next-six-star-cook", {"six_star_percent": 20}, "older", "2026-07-27T00:00:00Z")
    queued = apply_cooking_effects((0, 0, 0, 0, 90, 10), [cake, preceding], source_rarity=6)
    assert queued.consumed_entry_ids == ("older",)
    assert queued.weights[-1] == 20


def test_targeted_catch_is_exclusive_without_quota_grant():
    effect = _effect(XIXI_TARGETED_CATCH, {"mode": "pair"})
    result = apply_catch_effects((65, 20, 10, 4, 0.9, 0.1), [effect])
    assert result.targeted_mode == "pair" and result.weights[-1] == 100
    assert result.consumed_entry_ids == ("xixi",)
    assert scoped_template_id("qq-official:ABC123", "pig", "xixi") == "pig-qoabc123-xixi"


def test_xixi_fifo_retains_clover_and_earlier_exclusive_is_used_first():
    target = _effect(XIXI_TARGETED_CATCH, {"mode": "pair"}, created="2026-07-27T00:00:00Z")
    clover = _effect(CLOVER_CATCH, {"chain_id": "chain", "phase": "initial"}, "clover")
    first = apply_catch_effects((65, 20, 10, 4, 0.9, 0.1), [clover, target], random_value=lambda: 0.5)
    assert first.targeted_mode == "pair" and first.consumed_entry_ids == ("xixi",)
    preceding = _effect("next-six-star-catch", {"six_star_percent": 20, "uses": 1}, "older", "2026-07-26T00:00:00Z")
    first = apply_catch_effects((65, 20, 10, 4, 0.9, 0.1), [target, preceding])
    assert not first.targeted_mode and first.consumed_entry_ids == ("older",)


@pytest.fixture
async def db(tmp_path):
    entries = []
    effects = {
        "xixi-sun-star-cheese": (XIXI_STAR_CHEESE_LOTTERY, {}),
        "daniya-peach": (XIXI_SIX_STAR_COOK, {"six_star_percent": 60}),
        "xixi-mandarin": (XIXI_TARGETED_CATCH, {"mode": "pair"}),
        "daniya-birthday-cake": (DANIYA_BIRTHDAY_FEAST, {}),
        "daniya-bubble": ("", {}),
        "other-dish": ("", {}),
    }
    for suffix, (effect, params) in effects.items():
        entry = _food_entry(6, group_id="100", template_suffix=suffix, effect_id=effect, effect_params=params)
        entry.update(template_id=f"food-g100-{suffix}", display_name=suffix)
        if suffix in {"daniya-peach", "xixi-mandarin", "daniya-birthday-cake"}:
            entry["recipe_tags"] = ["special-reward-food", "zero-value"]
        entries.append(entry)
    for suffix, dish in [("xixi", "xixi-sun-star-cheese"), ("daniya", "daniya-bubble"), ("other", "other-dish")]:
        entry = _pig_entry(6, group_id="100", template_suffix=suffix, paired_food_template_id=f"food-g100-{dish}")
        entry.update(template_id=f"pig-g100-{suffix}", display_name=suffix)
        entries.append(entry)
    database = await _database_with_catalog(tmp_path, food_rarities=(1, 5), extra_entries=tuple(entries))
    yield database
    await database.close()


async def _seed_food(db, suffix, effect, params=None):
    actor = _identity(message_id="seed")
    instance = uuid4().hex
    code = uuid4().hex[:8].upper()
    async with db.transaction() as session:
        await FrameworkRepository().touch_identity(session, identity=actor, now="2026-07-28T04:00:00.000Z")
        await EconomyRepository().insert_food_instance(
            session,
            values={
                "food_instance_id": instance,
                "short_code": code,
                "scope_id": actor.scope.value,
                "owner_player_id": actor.player_id,
                "template_id": f"food-g100-{suffix}",
                "template_version": 1,
                "source_pig_instance_id": None,
                "rarity": 6,
                "display_name_snapshot": suffix,
                "portion_weight": 25,
                "fat_category": "balanced",
                "official_value": 0,
                "effect_id": effect,
                "effect_params_json": json.dumps(params or {}),
                "ruleset_version": 37,
                "random_snapshot_json": "{}",
                "acquired_at": "2026-07-28T04:00:00.000Z",
                "updated_at": "2026-07-28T04:00:00.000Z",
            },
        )
    return instance, f"{suffix}#{code}"


async def test_five_independent_draws_zero_value_and_eat_replay(db):
    instance, selector = await _seed_food(db, "xixi-sun-star-cheese", XIXI_STAR_CHEESE_LOTTERY)
    random = SequenceRandom(0, 0.1, 0.5, 0.9, 0.49, 0, 0.5, 0.5, 0.5, 0.5, 0.5)
    service = EconomyService(
        db, CookingSection(cooldown_seconds=0), EconomySection(), clock=FixedClock(), random_source=random
    )
    actor = _identity(message_id="star-cheese")
    result = await service.eat(actor, selector)
    assert result.reward_payload["branches"] == [
        "five-star",
        "daniya-peach",
        "xixi-mandarin",
        "daniya-birthday-cake",
        "daniya-peach",
    ]
    assert len(result.reward_payload["items"]) == 5 and not random.values
    for item in result.reward_payload["items"]:
        assert item["value"] == 0 if item["rarity"] == 6 else item["value"] > 0
    replay = await EconomyService(
        db, CookingSection(cooldown_seconds=0), EconomySection(), clock=FixedClock(), random_source=SequenceRandom()
    ).eat(actor, selector)
    assert replay.reward_payload == result.reward_payload and not replay.receipt_created
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances WHERE food_instance_id<>?", (instance,)))[0] == 5


async def test_missing_reward_template_rolls_back_whole_lottery(db):
    instance, selector = await _seed_food(db, "xixi-sun-star-cheese", XIXI_STAR_CHEESE_LOTTERY)
    async with db.transaction() as session:
        await session.execute("UPDATE food_templates SET enabled=0 WHERE template_id='food-g100-xixi-mandarin'")
    with pytest.raises(FoodEffectError):
        await EconomyService(
            db,
            CookingSection(cooldown_seconds=0),
            EconomySection(),
            clock=FixedClock(),
            random_source=SequenceRandom(0.1, 0.5, 0.1, 0.1, 0.1, 0.5),
        ).eat(_identity(message_id="missing"), selector)
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (instance,)))[0] == "active"
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 1


async def test_cake_gives_both_pigs_and_independent_durable_queues(db):
    _, selector = await _seed_food(db, "daniya-birthday-cake", DANIYA_BIRTHDAY_FEAST)
    service = EconomyService(
        db,
        CookingSection(cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(*([0.5] * 10)),
    )
    result = await service.eat(_identity(message_id="cake"), selector)
    assert {item["template_id"] for item in result.reward_payload["items"]} == {"pig-g100-daniya", "pig-g100-xixi"}
    stats = await db.fetch_one("SELECT total_catches,total_cooks FROM player_statistics")
    assert tuple(stats) == (0, 0)
    catch = await GameplayService(
        db,
        clock=FixedClock(),
        catching=CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0, 0.2, *([0.5] * 5)),
    ).catch(_identity(message_id="cake-catch"))
    assert catch.pig.template_id == "pig-g100-other" and not catch.quota_exempt_catch
    queued = await db.fetch_all("SELECT effect_id,consumed_uses FROM player_food_effects ORDER BY effect_id")
    assert {row["effect_id"]: row["consumed_uses"] for row in queued} == {XIXI_TARGETED_CATCH: 1, XIXI_SIX_STAR_COOK: 0}
    pig = result.reward_payload["items"][0]
    cooked = await EconomyService(
        db,
        cooking=CookingSection(cooldown_seconds=0),
        economy=EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0, 0.5, 0.5),
    ).cook(_identity(message_id="cake-cook"), f"{pig['name']}#{pig['short_code']}")
    assert cooked.weights[-1] == 100 and cooked.foods[0].template_id == "food-g100-daniya-bubble"
    assert all(row[0] == 1 for row in await db.fetch_all("SELECT consumed_uses FROM player_food_effects"))


@pytest.mark.parametrize("template_roll,target", [(0, "daniya"), (0.5, "xixi")])
async def test_mandarin_normal_quota_equal_pair_and_replay(db, template_roll, target):
    _, selector = await _seed_food(db, "xixi-mandarin", XIXI_TARGETED_CATCH, {"mode": "pair"})
    await EconomyService(
        db, CookingSection(cooldown_seconds=0), EconomySection(), clock=FixedClock(), random_source=SequenceRandom()
    ).eat(_identity(message_id="mandarin"), selector)
    service = GameplayService(
        db,
        clock=FixedClock(),
        catching=CatchingSection(daily_limit=1, cooldown_seconds=0),
        random_source=SequenceRandom(0, template_roll, *([0.5] * 5)),
    )
    actor = _identity(message_id="mandarin-catch")
    result = await service.catch(actor)
    assert result.pig.template_id == f"pig-g100-{target}" and not result.quota_exempt_catch
    assert not (await service.catch(actor)).receipt_created
    with pytest.raises(DailyCatchLimitError):
        await service.catch(_identity(message_id="no-extra"))


async def test_cake_low_star_cook_and_missing_target_preserve_queues(db):
    low = await GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=FixedClock(),
        random_source=SequenceRandom(0, 0, *([0.5] * 5)),
    ).catch(_identity(message_id="low-before-cake"))
    _, selector = await _seed_food(db, "daniya-birthday-cake", DANIYA_BIRTHDAY_FEAST)
    await EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(*([0.5] * 10)),
    ).eat(_identity(message_id="cake-preserve"), selector)
    await EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0, 0, 0.5),
    ).cook(_identity(message_id="low-cook"), low.pig.selector)
    assert all(row[0] == 0 for row in await db.fetch_all("SELECT consumed_uses FROM player_food_effects"))
    async with db.transaction() as session:
        await session.execute("UPDATE pig_templates SET enabled=0 WHERE template_id='pig-g100-other'")
    with pytest.raises(FoodEffectError):
        await GameplayService(
            db,
            CatchingSection(cooldown_seconds=0),
            clock=FixedClock(),
            random_source=SequenceRandom(),
        ).catch(_identity(message_id="missing-other"))
    assert all(row[0] == 0 for row in await db.fetch_all("SELECT consumed_uses FROM player_food_effects"))


async def test_peach_cook_snapshot_preserves_other_effects_equipment_and_replay(db):
    _, cake = await _seed_food(db, "daniya-birthday-cake", DANIYA_BIRTHDAY_FEAST)
    pigs = await EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(*([0.5] * 10)),
    ).eat(_identity(message_id="pig-seed-cake"), cake)
    async with db.transaction() as session:
        await session.execute("DELETE FROM player_food_effects")
    food_id, peach = await _seed_food(db, "daniya-peach", XIXI_SIX_STAR_COOK, {"six_star_percent": 60})
    actor = _identity(message_id="peach-cook")
    service = EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.5, 0, 0.5),
    )
    await service.eat(_identity(message_id="peach-eat"), peach)
    async with db.transaction() as session:
        for effect_id, params in [
            ("next-six-star-cook-bonus", {"bonus_percent": 10}),
            ("next-six-star-cook-duplicate", {}),
        ]:
            await EconomyRepository().insert_food_effect(
                session,
                effect_entry_id=uuid4().hex,
                player_id=actor.player_id,
                source_food_instance_id=food_id,
                effect_id=effect_id,
                params_json=json.dumps(params),
                granted_uses=1,
                expires_at=None,
                now="2026-07-28T04:00:01.000Z",
            )
        await session.execute(
            "INSERT INTO item_inventory(player_id,item_id,quantity,updated_at) VALUES(?,'super-chef-spice',1,?)",
            (actor.player_id, "2026-07-28T04:00:00.000Z"),
        )
        await session.execute(
            "INSERT INTO armed_items(player_id,action_type,item_id,armed_at) VALUES(?,'cooking','super-chef-spice',?)",
            (actor.player_id, "2026-07-28T04:00:00.000Z"),
        )
    pig = pigs.reward_payload["items"][0]
    selector = f"{pig['name']}#{pig['short_code']}"
    cooked = await service.cook(actor, selector)
    assert cooked.weights == (0, 0, 0, 0, 40, 60) and len(cooked.foods) == 1
    snapshot = json.loads(
        (
            await db.fetch_one(
                "SELECT random_snapshot_json FROM food_instances WHERE food_instance_id=?",
                (cooked.foods[0].food_instance_id,),
            )
        )[0]
    )
    assert snapshot["weights"][-1] == 60
    assert (await db.fetch_one("SELECT quantity FROM item_inventory WHERE item_id='super-chef-spice'"))[0] == 1
    assert {
        row["effect_id"]: row["consumed_uses"]
        for row in await db.fetch_all("SELECT effect_id,consumed_uses FROM player_food_effects")
    } == {XIXI_SIX_STAR_COOK: 1, "next-six-star-cook-bonus": 0, "next-six-star-cook-duplicate": 0}
    replay = await EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(),
    ).cook(actor, selector)
    assert not replay.receipt_created and replay.foods[0].food_instance_id == cooked.foods[0].food_instance_id


async def test_scope_inventory_and_reward_authorization_are_enforced(db):
    food_id, selector = await _seed_food(db, "xixi-sun-star-cheese", XIXI_STAR_CHEESE_LOTTERY)
    service = EconomyService(db, CookingSection(), EconomySection(), clock=FixedClock(), random_source=SequenceRandom())
    with pytest.raises(FoodNotFoundError):
        await service.eat(_identity(group_id="101", message_id="foreign-food"), selector)
    async with db.transaction() as session:
        await session.execute("UPDATE scope_food_templates SET authorized=0 WHERE template_id='food-g100-daniya-peach'")
    with pytest.raises(FoodEffectError):
        await EconomyService(
            db,
            CookingSection(),
            EconomySection(),
            clock=FixedClock(),
            random_source=SequenceRandom(*([0.1] * 5)),
        ).eat(_identity(message_id="unauthorized-reward"), selector)
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "active"


async def test_missing_paired_recipe_preserves_cake_cook_and_pig(db):
    _, cake = await _seed_food(db, "daniya-birthday-cake", DANIYA_BIRTHDAY_FEAST)
    result = await EconomyService(
        db,
        CookingSection(),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(*([0.5] * 10)),
    ).eat(_identity(message_id="missing-paired-cake"), cake)
    async with db.transaction() as session:
        await session.execute("UPDATE food_templates SET enabled=0 WHERE template_id='food-g100-daniya-bubble'")
    pig = result.reward_payload["items"][0]
    with pytest.raises(CookingTemplateError):
        await EconomyService(
            db,
            CookingSection(),
            EconomySection(),
            clock=FixedClock(),
            random_source=SequenceRandom(0),
        ).cook(_identity(message_id="missing-paired-cook"), f"{pig['name']}#{pig['short_code']}")
    assert (await db.fetch_one("SELECT state FROM pig_instances WHERE pig_instance_id=?", (pig["instance_id"],)))[
        0
    ] == "active"
    assert all(row[0] == 0 for row in await db.fetch_all("SELECT consumed_uses FROM player_food_effects"))


async def test_mandarin_preserves_catch_equipment_and_other_buff(db):
    food_id, selector = await _seed_food(db, "xixi-mandarin", XIXI_TARGETED_CATCH, {"mode": "pair"})
    actor = _identity(message_id="buff-preserved")
    await EconomyService(db, CookingSection(), EconomySection(), clock=FixedClock()).eat(actor, selector)
    async with db.transaction() as session:
        await EconomyRepository().insert_food_effect(
            session,
            effect_entry_id="ordinary-catch",
            player_id=actor.player_id,
            source_food_instance_id=food_id,
            effect_id="next-catch-quality",
            params_json='{"multiplier":2}',
            granted_uses=1,
            expires_at=None,
            now="2026-07-28T04:00:01.000Z",
        )
        await session.execute(
            "INSERT INTO item_inventory(player_id,item_id,quantity,updated_at) VALUES(?,'super-lucky-whistle',1,?)",
            (actor.player_id, "2026-07-28T04:00:00.000Z"),
        )
        await session.execute(
            "INSERT INTO armed_items(player_id,action_type,item_id,armed_at) "
            "VALUES(?,'catching','super-lucky-whistle',?)",
            (actor.player_id, "2026-07-28T04:00:00.000Z"),
        )
    result = await GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=FixedClock(),
        random_source=SequenceRandom(0, 0.2, *([0.5] * 5)),
    ).catch(_identity(message_id="preserved-catch"))
    assert result.pig.template_id == "pig-g100-daniya" and result.exclusive_effect_active
    assert (await db.fetch_one("SELECT consumed_uses FROM player_food_effects WHERE effect_entry_id='ordinary-catch'"))[
        0
    ] == 0
    assert (await db.fetch_one("SELECT quantity FROM item_inventory WHERE item_id='super-lucky-whistle'"))[0] == 1


@pytest.mark.parametrize(
    "suffix,effect,params",
    [
        ("daniya-peach", XIXI_SIX_STAR_COOK, {"six_star_percent": 60}),
        ("xixi-mandarin", XIXI_TARGETED_CATCH, {"mode": "pair"}),
        ("daniya-birthday-cake", DANIYA_BIRTHDAY_FEAST, {}),
    ],
)
async def test_zero_value_food_sale_disposes_without_coin_ledger_and_replays(db, suffix, effect, params):
    food_id, selector = await _seed_food(db, suffix, effect, params)
    actor = _identity(message_id="zero-sale")
    service = EconomyService(db, CookingSection(), EconomySection(), clock=FixedClock())
    result = await service.sell_food(actor, selector)
    assert result.official_value == result.balance_after == 0
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "sold"
    assert (await db.fetch_one("SELECT COUNT(*) FROM currency_ledger"))[0] == 0
    replay = await service.sell_food(actor, selector)
    assert not replay.receipt_created and replay.receipt.receipt_id == result.receipt.receipt_id
    assert (await db.fetch_one("SELECT COUNT(*) FROM player_food_effects"))[0] == 0


async def test_six_star_reward_food_is_outside_supported_batch_sale(db):
    food_id, _ = await _seed_food(db, "daniya-peach", XIXI_SIX_STAR_COOK, {"six_star_percent": 60})
    service = EconomyService(db, CookingSection(), EconomySection(), clock=FixedClock())
    for args in ({"max_rarity": 6}, {"rarity": 6}):
        with pytest.raises(StoreProductError):
            await service.batch_sell_low_rarity(_identity(message_id="six-batch"), asset_kind="food", **args)
    assert (await db.fetch_one("SELECT state FROM food_instances WHERE food_instance_id=?", (food_id,)))[0] == "active"
    assert (await db.fetch_one("SELECT COUNT(*) FROM currency_ledger"))[0] == 0


@pytest.mark.parametrize(
    "scope,prefix",
    [
        ("qq:1092931381", "g1092931381"),
        ("qq:237716658", "g237716658"),
        ("qq-official:5E5854406D0297D6FEAE696A13E3A339", "qo5e5854406d0297d6feae696a13e3a339"),
        ("qq-official:9EA2810F378FBD7DC3219C56CEAB3520", "qo9ea2810f378fbd7dc3219c56ceab3520"),
    ],
)
def test_four_scope_ids_match_current_catalog_and_filter_current_pair(scope, prefix):
    pair = [{"template_id": f"pig-{prefix}-{name}", "rarity": 6} for name in ("daniya", "xixi")]
    other = {"template_id": f"pig-{prefix}-other", "rarity": 6}
    assert scoped_template_id(scope, "food", "daniya-peach") == f"food-{prefix}-daniya-peach"
    assert targeted_catch_templates(pair + [other], scope, "pair") == pair
    assert targeted_catch_templates(pair + [other], scope, "other-six") == [other]
    with pytest.raises(FoodEffectError):
        targeted_catch_templates(pair[:1] + [other], scope, "pair")


async def test_pair_missing_one_target_fails_atomically_after_restart(db):
    _, selector = await _seed_food(db, "xixi-mandarin", XIXI_TARGETED_CATCH, {"mode": "pair"})
    await EconomyService(db, CookingSection(), EconomySection(), clock=FixedClock()).eat(
        _identity(message_id="pair-missing-eat"),
        selector,
    )
    async with db.transaction() as session:
        await session.execute("UPDATE pig_templates SET enabled=0 WHERE template_id='pig-g100-xixi'")
    await db.close()
    await db.open()
    with pytest.raises(FoodEffectError):
        await GameplayService(
            db,
            CatchingSection(cooldown_seconds=0),
            clock=FixedClock(),
            random_source=SequenceRandom(),
        ).catch(_identity(message_id="pair-missing-catch"))
    assert (await db.fetch_one("SELECT consumed_uses FROM player_food_effects"))[0] == 0
    assert (await db.fetch_one("SELECT total_catches FROM player_statistics"))[0] == 0
    assert (await db.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 0


async def test_targeted_six_catch_preserves_resonance_and_does_not_grant_seven_foods(db):
    food_id, selector = await _seed_food(db, "xixi-mandarin", XIXI_TARGETED_CATCH, {"mode": "pair"})
    actor = _identity(message_id="resonance-target-eat")
    await EconomyService(db, CookingSection(), EconomySection(), clock=FixedClock()).eat(actor, selector)
    async with db.transaction() as session:
        await EconomyRepository().create_window_resonance(
            session,
            player_id=actor.player_id,
            scope_id=actor.scope.value,
            source_food_instance_id=food_id,
            window_start="2026-07-28T03:00:00.000Z",
            window_end="2026-07-28T11:00:00.000Z",
            now="2026-07-28T04:00:00.000Z",
        )
        await session.execute(
            "UPDATE player_window_resonance SET cook_bonus_basis_points=1200,catch_bonus_basis_points=2500"
        )
    result = await GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=FixedClock(),
        random_source=SequenceRandom(0, 0.5, *([0.5] * 5)),
    ).catch(_identity(message_id="resonance-target-catch"))
    assert result.pig.template_id == "pig-g100-xixi"
    assert tuple(
        await db.fetch_one("SELECT cook_bonus_basis_points,catch_bonus_basis_points FROM player_window_resonance")
    ) == (1200, 2500)
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 1
    assert not any("获得7道" in text or "累计已清零" in text for text in result.effect_summaries)
