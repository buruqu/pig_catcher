"""新版粉蓝链/月栖时序、资源排他、恢复与原子结算回归。"""

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.errors import CookingTemplateError, DailyCatchLimitError, FoodEffectError
from pig_catcher.domain.feasts import (
    CLOVER_CATCH,
    CLOVER_COOK,
    CLOVER_FEAST,
    MOON_FEAST,
    discounted_price,
    moon_weights,
)
from pig_catcher.domain.food_effects import active_effect_from_row, apply_cooking_effects
from pig_catcher.rendering import pig_card_view
from pig_catcher.services import EconomyService, FrameworkService, GameplayService
from pig_catcher.services.battle_loot import claim_loot
from pig_catcher.services.command_state import iso_timestamp
from tests.test_economy import (
    FixedClock,
    SequenceRandom,
    _database_with_catalog,
    _grant_coins,
    _identity,
    _insert_food,
    _insert_pig,
)


async def setup(tmp_path):
    db = await _database_with_catalog(
        tmp_path, pig_rarities=(1, 2, 3, 4, 5, 6), food_rarities=(1, 2, 3, 4, 5, 6), manifest_version=4
    )
    clock = FixedClock()
    clock.value = clock.value.replace(hour=0)  # 北京08点食用，09点禁止，12点奖励。
    owner = _identity(message_id="seed")
    await FrameworkService(db).touch_identity(owner)
    return db, clock, owner


def economy(db, clock, *rolls):
    return EconomyService(
        db, CookingSection(cook_cooldown_seconds=0), EconomySection(), clock=clock, random_source=SequenceRandom(*rolls)
    )


def game(db, clock, roll=0):
    return GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(*([roll, 0, 0.5, 0.5, 0.5, 0.5, 0.5] * 100)),
    )


async def eat(db, clock, eid, code, params=None, rarity=6):
    who = _identity(message_id=f"eat-{code}")
    await _insert_food(
        db,
        player_id=who.player_id,
        scope_id=who.scope.value,
        template_id=f"food-{rarity}-{'group' if rarity == 6 else 'common'}",
        display_name=code,
        short_code=code,
        instance_id=f"food-{code}",
        rarity=rarity,
        official_value=25000,
        effect_id=eid,
        effect_params=params or {},
    )
    return await economy(db, clock).eat(who, code)


async def six_pig(db, owner, code):
    await _insert_pig(
        db,
        player_id=owner.player_id,
        scope_id=owner.scope.value,
        template_id="pig-6-group",
        rarity=6,
        display_name=code,
        official_value=25000,
        short_code=code,
        instance_id=f"pig-{code}",
    )


async def equip(db, clock, owner, iid, action="catching"):
    now = iso_timestamp(clock.now())
    async with db.transaction() as s:
        await s.execute(
            "INSERT INTO item_inventory(player_id,item_id,quantity,updated_at) VALUES(?,?,2,?)",
            (owner.player_id, iid, now),
        )
        await s.execute(
            "INSERT INTO armed_items(player_id,action_type,item_id,armed_at) VALUES(?,?,?,?)",
            (owner.player_id, action, iid, now),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_clover_ten_then_one_cook_and_rewards_are_atomic_durable(tmp_path, success):
    db, clock, owner = await setup(tmp_path)
    await eat(db, clock, "next-catch-quality", "OTHER", {"multiplier": 2}, rarity=4)
    await equip(db, clock, owner, "super-lucky-whistle")
    await eat(db, clock, CLOVER_FEAST, "CLOVER")
    with pytest.raises(FoodEffectError, match="尚未完成"):
        await eat(db, clock, CLOVER_FEAST, "TWICE")
    # Ordinary six-star cooking remains available before all ten catches finish; it cannot consume the future bonus.
    await six_pig(db, owner, "BEFORE")
    early = await economy(db, clock, 0, 0, 0.5).cook(_identity(message_id="early"), "BEFORE")
    assert early.weights[5] == 10
    catching = game(db, clock)
    for i in range(10):
        if i == 5:
            clock.value += timedelta(days=1)
            await db.close()
            await db.open()
        who = _identity(message_id=f"clover-{i}")
        result = await catching.catch(who)
        assert result.weights[5] == pytest.approx(4.07)
        assert result.weights[3:5] == pytest.approx((8, 4))
        assert result.daily_count == 0 and json.loads(result.receipt.result_json)["quota_exempt_catch"] is True
        replay = await catching.catch(who)
        assert not replay.receipt_created
    chain = await db.fetch_one("SELECT * FROM player_clover_chains")
    assert (chain["initial_used"], chain["star_sum"], chain["stage"]) == (10, 10, "cook")
    assert (await db.fetch_one("SELECT quantity FROM item_inventory WHERE item_id='super-lucky-whistle'"))[0] == 2
    assert (await db.fetch_one("SELECT consumed_uses FROM player_food_effects WHERE effect_id='next-catch-quality'"))[
        0
    ] == 0
    await six_pig(db, owner, "READY")
    await equip(db, clock, owner, "super-chef-spice", "cooking")
    service = economy(db, clock, 0.999 if success else 0, 0, 0.5, *([0, 0.5] * 7))
    who = _identity(message_id="ready-cook")
    result = await service.cook(who, "READY")
    assert result.weights[5] == pytest.approx(23.07)
    assert result.foods[0].rarity == (6 if success else 5)
    replay = await service.cook(who, "READY")
    assert not replay.receipt_created
    assert (await db.fetch_one("SELECT quantity FROM item_inventory WHERE item_id='super-chef-spice'"))[0] == 2
    foods = await db.fetch_all(
        "SELECT rarity,random_snapshot_json FROM food_instances WHERE random_snapshot_json LIKE '%clover-feast%'"
    )
    assert len(foods) == (7 if success else 0)
    assert all(1 <= row["rarity"] <= 5 for row in foods)
    if success:
        for i in range(3):
            reward = await catching.catch(_identity(message_id=f"reward-{i}"))
            assert reward.weights[5] == pytest.approx(4.07)
            assert json.loads(reward.receipt.result_json)["quota_exempt_catch"] is True
    chain = await db.fetch_one("SELECT * FROM player_clover_chains")
    assert chain["stage"] == "complete" and chain["star_sum"] == 10
    assert chain["reward_used"] == (3 if success else 0)
    await db.close()


@pytest.mark.asyncio
async def test_clover_mixed_stars_and_concurrent_tenth_catch(tmp_path):
    db, clock, owner = await setup(tmp_path)
    await eat(db, clock, CLOVER_FEAST, "CLOVER")
    rolls = [0, 0.4, 0.7, 0.85, 0.92, 0.99, 0, 0.99, 0.7, 0.99, 0]
    catching = GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(*(v for roll in rolls for v in (roll, 0, 0.5, 0.5, 0.5, 0.5, 0.5))),
    )
    for i in range(9):
        await catching.catch(_identity(message_id=f"mixed-{i}"))
    await asyncio.gather(
        catching.catch(_identity(message_id="tenth")), catching.catch(_identity(message_id="eleventh"))
    )
    chain = await db.fetch_one("SELECT initial_used,star_sum,stage FROM player_clover_chains")
    assert tuple(chain) == (10, 37, "cook")
    assert (await db.fetch_one("SELECT COUNT(*) FROM player_food_effects WHERE effect_id=?", (CLOVER_COOK,)))[0] == 1
    assert (await game(db, clock).profile(_identity(user_id="201", message_id="peer"))).feast_status == ()
    await db.close()


@pytest.mark.asyncio
async def test_schema70_updates_uneaten_and_locked_food_only(tmp_path):
    db, clock, owner = await setup(tmp_path)
    for i, state in enumerate(("active", "locked-for-trade", "consumed", "sold")):
        await _insert_food(
            db,
            player_id=owner.player_id,
            scope_id=owner.scope.value,
            template_id="food-6-group",
            display_name="旧粉蓝冰糕",
            short_code=f"OLD{i}",
            instance_id=f"old-{i}",
            rarity=6,
            official_value=25000,
            effect_id="window-six-star-resonance",
            effect_params={"legacy": True},
        )
        async with db.transaction() as s:
            await s.execute("UPDATE food_instances SET state=? WHERE food_instance_id=?", (state, f"old-{i}"))
    await db.close()
    with sqlite3.connect(db.path) as con:
        con.execute("DROP TABLE player_clover_chains")
        con.execute("DROP TABLE player_moon_feasts")
        con.execute("DELETE FROM schema_migrations WHERE version=70")
        con.execute("PRAGMA user_version=69")
    await db.open()
    rows = await db.fetch_all("SELECT state,effect_id,effect_params_json FROM food_instances ORDER BY food_instance_id")
    assert [r["effect_id"] for r in rows] == [
        CLOVER_FEAST,
        CLOVER_FEAST,
        "window-six-star-resonance",
        "window-six-star-resonance",
    ]
    assert rows[0]["effect_params_json"] == rows[1]["effect_params_json"] == "{}"
    assert json.loads(rows[2]["effect_params_json"]) == {"legacy": True}
    await db.close()
    await db.open()  # A second open cannot repeat or reset the migration.
    assert (
        await db.fetch_all("SELECT state,effect_id,effect_params_json FROM food_instances ORDER BY food_instance_id")
        == rows
    )
    await db.close()


def test_moon_triples_final_high_star_probabilities_without_decreasing_tiers():
    assert moon_weights((40, 30, 17, 8, 4, 1))[3:] == pytest.approx((24, 12, 3))
    result = moon_weights((5, 3, 2, 50, 30, 10))
    assert sum(result) == pytest.approx(100) and result[:3] == (0, 0, 0)
    assert result[3:] == pytest.approx((500 / 9, 100 / 3, 100 / 9))


@pytest.mark.asyncio
async def test_moon_full_blackout_fifteen_stacking_expiry_and_discount(tmp_path):
    db, clock, owner = await setup(tmp_path)
    await _grant_coins(db, _identity(message_id="coins"), 100000)
    service = economy(db, clock)
    base = await service.store(owner, page=1, category="全部")
    prices = {p.product_id: p.unit_price for p in base.products}
    await eat(db, clock, CLOVER_FEAST, "CLOVER")
    eaten = await eat(db, clock, MOON_FEAST, "MOON")
    assert "8.8折" in eaten.effect.summary
    assert (await service.store(owner, page=1, category="全部")).products == base.products
    clock.value += timedelta(hours=1)
    with pytest.raises(DailyCatchLimitError, match="禁止抓猪"):
        await game(db, clock).catch(_identity(message_id="blocked"))
    async with db.transaction() as s:
        with pytest.raises(DailyCatchLimitError, match="禁止抓猪"):
            await claim_loot(None, s, owner, int(clock.now().timestamp() * 1000), "test-loot")
    assert (await game(db, clock).profile(owner)).daily_limit == 0
    assert (await service.store(owner, page=1, category="全部")).products == base.products
    await eat(db, clock, "next-catch-quality", "ORDINARY", {"multiplier": 2}, rarity=4)
    await equip(db, clock, owner, "super-lucky-whistle")
    clock.value += timedelta(hours=3)
    now = iso_timestamp(clock.now())
    async with db.transaction() as s:
        await s.execute(
            "INSERT INTO upgrades(player_id,upgrade_type,level,updated_at) VALUES(?,'feed',10,?)",
            (owner.player_id, now),
        )
        await s.execute("UPDATE players SET experience=100000 WHERE player_id=?", (owner.player_id,))
    catch = game(db, clock)
    first = await catch.catch(_identity(message_id="target-0"))
    # Super whistle high-star baseline35%, ordinary ×2 ->70%, Moon ×3 saturates100%.
    assert sum(first.weights[3:]) == pytest.approx(100)
    rendered = pig_card_view(first.pig, mode_label="抓猪", catch=first)
    assert "等级、饲料及永久提升未参与" in rendered.probability_sources
    replay = await catch.catch(_identity(message_id="target-0"))
    assert replay.growth_modifiers_excluded and not replay.receipt_created
    assert json.loads(first.receipt.result_json)["quota_exempt_catch"] is True and first.daily_count == 0
    assert (await db.fetch_one("SELECT consumed_uses FROM player_food_effects WHERE effect_id=?", (CLOVER_CATCH,)))[
        0
    ] == 0
    assert (await db.fetch_one("SELECT consumed_uses FROM player_food_effects WHERE effect_id='next-catch-quality'"))[
        0
    ] == 1
    assert (await db.fetch_one("SELECT quantity FROM item_inventory WHERE item_id='super-lucky-whistle'"))[0] == 1
    # Remaining ticket/food effects are consumed only when actually participating; no level/feed influence.
    async with db.transaction() as s:
        await s.execute("DELETE FROM armed_items WHERE player_id=?", (owner.player_id,))
    for i in range(1, 15):
        result = await catch.catch(_identity(message_id=f"target-{i}"))
        assert result.weights[3:] == pytest.approx((24, 12, 3))
        assert json.loads(result.receipt.result_json)["quota_exempt_catch"] is True
    for category in ["全部", "派遣", "巡演", "对战"]:
        store = await service.store(owner, page=1, category=category)
        for product in store.products:
            if product.product_type == "upgrade":
                continue
            bought = await service.purchase(
                _identity(message_id=f"buy-{product.product_id}"), product.display_name, quantity=2
            )
            assert bought.unit_price == product.unit_price
    store = await service.store(owner, page=1, category="全部")
    assert all(
        p.unit_price == discounted_price(prices[p.product_id]) for p in store.products if p.product_type == "item"
    )
    # Discount lasts the whole reward window even after all15 uses; another player pays full price.
    peer = await service.store(_identity(message_id="peer", user_id="201"), page=1, category="全部")
    assert {p.product_id: p.unit_price for p in peer.products} == prices
    next_catch = await catch.catch(_identity(message_id="after-fifteen"))
    assert next_catch.weights[5] == pytest.approx(4.07)  # Preserved Clover resumes.
    clock.value += timedelta(hours=7)
    expired = await service.store(owner, page=1, category="全部")
    assert all(p.unit_price == prices[p.product_id] for p in expired.products if p.product_type == "item")
    await db.close()


def test_clover_maximum_cooking_probability_and_ordinary_pig_boundary():
    effect = active_effect_from_row(
        dict(
            effect_entry_id="x",
            effect_id=CLOVER_COOK,
            params_json=json.dumps({"chain_id": "f", "phase": "cook", "star_sum": 60}),
            granted_uses=1,
            consumed_uses=0,
            created_at="2026-01-01",
            source_food_rarity=6,
        )
    )
    assert apply_cooking_effects((0, 0, 0, 0, 90, 10), (effect,), source_rarity=6).weights[5] == pytest.approx(73.07)
    for rarity in range(1, 6):
        result = apply_cooking_effects((0, 0, 0, 0, 100, 0), (effect,), source_rarity=rarity)
        assert result.weights[5] == 0 and not result.consumed_entry_ids


@pytest.mark.asyncio
async def test_clover_missing_reward_pool_rolls_back_entire_cook(tmp_path):
    db, clock, owner = await setup(tmp_path)
    await eat(db, clock, CLOVER_FEAST, "CLOVER")
    catch = game(db, clock)
    for i in range(10):
        await catch.catch(_identity(message_id=f"catch-{i}"))
    await six_pig(db, owner, "SOURCE")
    async with db.transaction() as s:
        await s.execute("UPDATE food_templates SET enabled=0 WHERE rarity<6")
    with pytest.raises(CookingTemplateError, match="非六星菜"):
        await economy(db, clock, 0.999, 0, 0.5).cook(_identity(message_id="failed"), "SOURCE")
    assert (await db.fetch_one("SELECT state FROM pig_instances WHERE pig_instance_id='pig-SOURCE'"))[0] == "active"
    assert (await db.fetch_one("SELECT stage FROM player_clover_chains"))[0] == "cook"
    assert (await db.fetch_one("SELECT consumed_uses FROM player_food_effects WHERE effect_id=?", (CLOVER_COOK,)))[
        0
    ] == 0
    await db.close()
