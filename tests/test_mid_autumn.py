"""Mid-Autumn mooncake recipes and effect boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.food_effects import (
    MID_AUTUMN_FIXED_SIX_STAR_CATCH,
    MID_AUTUMN_SIX_STAR_COOK_BONUS,
    ActiveFoodEffect,
    apply_catch_effects,
    apply_cooking_effects,
)
from pig_catcher.domain.mid_autumn import (
    JADE_RABBIT_PIG_ID,
    LANTERN_PIG_ID,
    OSMANTHUS_MOONCAKE_ID,
    OSMANTHUS_PIG_ID,
    RED_BEAN_MOONCAKE_ID,
    SNOW_SKIN_MOONCAKE_ID,
    mid_autumn_boost_active,
    mooncake_cook_chance,
)
from pig_catcher.domain.rules import BASE_CATCH_WEIGHTS, cooking_weights
from pig_catcher.infrastructure.repositories import FrameworkRepository
from pig_catcher.services import EconomyService, GameplayService
from pig_catcher.services.command_state import iso_timestamp

from .test_economy import (
    FixedClock,
    _database_with_catalog,
    _food_entry,
    _identity,
    _insert_food,
    _insert_pig,
    _pig_entry,
)


class ConstantRandom:
    def random(self) -> float:
        return 0.49


class HalfRandom:
    def random(self) -> float:
        return 0.5


def _effect(effect_id: str, entry_id: str = "effect") -> ActiveFoodEffect:
    return ActiveFoodEffect(entry_id, effect_id, {}, 1, 0, "", "2026-09-24T00:00:00Z")


def test_mid_autumn_beijing_window_and_exact_probability() -> None:
    assert not mid_autumn_boost_active(datetime(2026, 9, 24, 15, 59, 59, tzinfo=UTC))
    assert mid_autumn_boost_active(datetime(2026, 9, 24, 16, 0, tzinfo=UTC))
    assert mid_autumn_boost_active(datetime(2026, 9, 30, 15, 59, 59, tzinfo=UTC))
    assert not mid_autumn_boost_active(datetime(2026, 9, 30, 16, 0, tzinfo=UTC))
    assert mooncake_cook_chance(datetime(2026, 9, 24, 16, 0, tzinfo=UTC)) == 0.5
    assert mooncake_cook_chance(datetime(2026, 9, 30, 16, 0, tzinfo=UTC)) == 0.1

    bean = _effect(MID_AUTUMN_FIXED_SIX_STAR_CATCH)
    ordinary = apply_catch_effects(BASE_CATCH_WEIGHTS, [bean])
    festival = apply_catch_effects(BASE_CATCH_WEIGHTS, [bean], mid_autumn_active=True)
    assert ordinary.weights[5] == pytest.approx(25.0)
    assert festival.weights == (0, 0, 0, 0, 0, 100)
    assert ordinary.consumed_entry_ids == festival.consumed_entry_ids == ("effect",)

    osmanthus = _effect(MID_AUTUMN_SIX_STAR_COOK_BONUS)
    ordinary_cook = apply_cooking_effects(cooking_weights(6), [osmanthus], source_rarity=6)
    festival_cook = apply_cooking_effects(cooking_weights(6), [osmanthus], source_rarity=6, mid_autumn_active=True)
    assert ordinary_cook.weights[5] == pytest.approx(35.0)
    assert festival_cook.weights[5] == pytest.approx(60.0)
    assert ordinary_cook.consumed_entry_ids == festival_cook.consumed_entry_ids == ("effect",)
    incompatible = apply_cooking_effects(cooking_weights(5), [osmanthus], source_rarity=5)
    assert incompatible.consumed_entry_ids == ()


@pytest.mark.asyncio
async def test_only_matched_pig_produces_its_mooncake(tmp_path: Path) -> None:
    pairs = (
        (LANTERN_PIG_ID, 5, "灯笼照月猪", RED_BEAN_MOONCAKE_ID, MID_AUTUMN_FIXED_SIX_STAR_CATCH),
        (OSMANTHUS_PIG_ID, 5, "桂花猪", OSMANTHUS_MOONCAKE_ID, MID_AUTUMN_SIX_STAR_COOK_BONUS),
        (JADE_RABBIT_PIG_ID, 5, "玉兔猪", SNOW_SKIN_MOONCAKE_ID, "mid-autumn-coin-reward"),
    )
    entries = []
    for pig_id, rarity, pig_name, food_id, effect_id in pairs:
        pig = _pig_entry(rarity, template_suffix=pig_id)
        pig.update(template_id=pig_id, image=f"{pig_id}.png", display_name=pig_name)
        food = _food_entry(rarity, template_suffix=food_id, effect_id=effect_id)
        food.update(template_id=food_id, image=f"{food_id}.png")
        entries.extend((pig, food))
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(5,),
        food_rarities=(1, 2, 3, 4, 5),
        extra_entries=tuple(entries),
    )
    try:
        clock = FixedClock()
        clock.value = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)
        identity = _identity(message_id="register")
        async with database.transaction() as session:
            await FrameworkRepository().touch_identity(session, identity=identity, now=iso_timestamp(clock.now()))
        service = EconomyService(
            database,
            CookingSection(cook_cooldown_seconds=0),
            EconomySection(),
            random_source=ConstantRandom(),
            clock=clock,
        )
        for index, (pig_id, rarity, pig_name, food_id, _) in enumerate(pairs):
            code = f"A{index:07d}"
            await _insert_pig(
                database,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                template_id=pig_id,
                rarity=rarity,
                display_name=pig_name,
                official_value=100,
                short_code=code,
                instance_id=f"special-pig-{index}",
            )
            result = await service.cook(_identity(message_id=f"special-cook-{index}"), f"{pig_name}#{code}")
            assert result.foods[0].template_id == food_id
        for index in range(3):
            rarity = 5
            code = f"B{index:07d}"
            await _insert_pig(
                database,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                template_id=f"pig-{rarity}-common",
                rarity=rarity,
                display_name=f"{rarity}星测试猪",
                official_value=100,
                short_code=code,
                instance_id=f"ordinary-pig-{index}",
            )
            result = await service.cook(_identity(message_id=f"ordinary-cook-{index}"), f"{rarity}星测试猪#{code}")
            assert result.foods[0].template_id not in {pair[3] for pair in pairs}
        await _insert_pig(
            database,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id=LANTERN_PIG_ID,
            rarity=5,
            display_name="灯笼照月猪",
            official_value=100,
            short_code="C0000001",
            instance_id="festival-pig-up-miss",
        )
        half = EconomyService(
            database, CookingSection(cook_cooldown_seconds=0), EconomySection(),
            random_source=HalfRandom(), clock=clock,
        )
        missed = await half.cook(_identity(message_id="festival-up-miss"), "灯笼照月猪#C0000001")
        assert missed.foods[0].template_id != RED_BEAN_MOONCAKE_ID
        clock.value = datetime(2026, 9, 30, 16, 0, tzinfo=UTC)
        await _insert_pig(
            database,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id=LANTERN_PIG_ID,
            rarity=5,
            display_name="灯笼照月猪",
            official_value=100,
            short_code="C0000002",
            instance_id="festival-pig-normal-miss",
        )
        normal = await service.cook(_identity(message_id="festival-normal-miss"), "灯笼照月猪#C0000002")
        assert normal.foods[0].template_id != RED_BEAN_MOONCAKE_ID
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_snow_skin_mooncake_awards_coins_once_at_eating_time(tmp_path: Path) -> None:
    food = _food_entry(5, effect_id="mid-autumn-coin-reward")
    food.update(template_id=SNOW_SKIN_MOONCAKE_ID, image="snow-skin.png", display_name="冰皮猪月饼")
    database = await _database_with_catalog(tmp_path, food_rarities=(1,), extra_entries=(food,))
    try:
        clock = FixedClock()
        identity = _identity(message_id="register")
        async with database.transaction() as session:
            await FrameworkRepository().touch_identity(session, identity=identity, now=iso_timestamp(clock.now()))
        service = EconomyService(database, CookingSection(), EconomySection(), clock=clock)
        for index, (moment, expected) in enumerate(
            (
                (datetime(2026, 9, 24, 15, 59, tzinfo=UTC), 10_000),
                (datetime(2026, 9, 24, 16, 0, tzinfo=UTC), 20_000),
            )
        ):
            clock.value = moment
            code = f"C{index:07d}"
            await _insert_food(
                database,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                template_id=SNOW_SKIN_MOONCAKE_ID,
                display_name="冰皮猪月饼",
                official_value=100,
                short_code=code,
                instance_id=f"mooncake-{index}",
                rarity=5,
                effect_id="mid-autumn-coin-reward",
            )
            actor = _identity(message_id=f"eat-{index}")
            eaten = await service.eat(actor, f"冰皮猪月饼#{code}")
            replay = await service.eat(actor, f"冰皮猪月饼#{code}")
            assert eaten.effect.coin_bonus == replay.effect.coin_bonus == expected
            assert replay.receipt_created is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_eaten_mooncakes_affect_next_real_catch_and_six_star_cook(tmp_path: Path) -> None:
    bean = _food_entry(5, effect_id=MID_AUTUMN_FIXED_SIX_STAR_CATCH)
    bean.update(template_id=RED_BEAN_MOONCAKE_ID, image="bean.png", display_name="豆沙猪月饼")
    osmanthus = _food_entry(5, effect_id=MID_AUTUMN_SIX_STAR_COOK_BONUS)
    osmanthus.update(template_id=OSMANTHUS_MOONCAKE_ID, image="osmanthus.png", display_name="桂花猪月饼")
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 2, 3, 4, 5, 6),
        food_rarities=(1, 2, 3, 4, 5, 6),
        extra_entries=(bean, osmanthus),
        manifest_version=4,
    )
    try:
        clock = FixedClock()
        clock.value = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)
        identity = _identity(message_id="register")
        async with database.transaction() as session:
            await FrameworkRepository().touch_identity(session, identity=identity, now=iso_timestamp(clock.now()))
        economy = EconomyService(
            database,
            CookingSection(cook_cooldown_seconds=0),
            EconomySection(),
            clock=clock,
            random_source=ConstantRandom(),
        )
        gameplay = GameplayService(
            database, CatchingSection(cooldown_seconds=0), clock=clock, random_source=ConstantRandom()
        )
        await _insert_food(
            database,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id=RED_BEAN_MOONCAKE_ID,
            display_name="豆沙猪月饼",
            official_value=100,
            short_code="D0000001",
            instance_id="bean-instance",
            rarity=5,
            effect_id=MID_AUTUMN_FIXED_SIX_STAR_CATCH,
        )
        await economy.eat(_identity(message_id="eat-bean"), "豆沙猪月饼#D0000001")
        caught = await gameplay.catch(_identity(message_id="festival-catch"))
        replay = await gameplay.catch(_identity(message_id="festival-catch"))
        assert caught.pig.rarity == 6
        assert caught.weights[5] == pytest.approx(100.0)
        assert caught.quota_exempt_catch is False
        assert replay.receipt_created is False

        await _insert_food(
            database,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id=OSMANTHUS_MOONCAKE_ID,
            display_name="桂花猪月饼",
            official_value=100,
            short_code="D0000002",
            instance_id="osmanthus-instance",
            rarity=5,
            effect_id=MID_AUTUMN_SIX_STAR_COOK_BONUS,
        )
        await economy.eat(_identity(message_id="eat-osmanthus"), "桂花猪月饼#D0000002")
        cooked = await economy.cook(_identity(message_id="festival-cook"), caught.pig.selector)
        assert cooked.weights[5] == pytest.approx(60.0)
        assert cooked.foods[0].rarity == 6
    finally:
        await database.close()
