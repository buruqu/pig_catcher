"""September food quota snapshot and independent cooking bonus regression tests."""

from datetime import timedelta
from pathlib import Path

import pytest

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.errors import DailyCatchLimitError
from pig_catcher.rendering import food_card_view
from pig_catcher.services import EconomyService, FrameworkService, GameplayService, format_cooking_summary
from pig_catcher.services.command_state import iso_timestamp
from tests.test_economy import (
    FixedClock,
    SequenceRandom,
    _database_with_catalog,
    _identity,
    _insert_food,
    _insert_pig,
)


async def eat_fixture(db, clock, code, name, effect_id, params=None):
    identity = _identity(message_id=f"eat-{code}")
    if effect_id == "catch-window-transfer":
        params = {"fixed_weights": [0, 0, 0, 42, 40, 18]}
    await _insert_food(
        db,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id="food-6-group",
        display_name=name,
        official_value=25_000,
        short_code=code,
        instance_id=f"food-{code}",
        rarity=6,
        effect_id=effect_id,
        effect_params=params,
    )
    service = EconomyService(db, CookingSection(cook_cooldown_seconds=0), EconomySection(), clock=clock)
    return await service.eat(identity, f"{name}#{code}")


@pytest.mark.asyncio
async def test_moon_moves_only_named_quota_and_leaves_other_extras(tmp_path: Path):
    db = await _database_with_catalog(tmp_path, pig_rarities=(1, 4, 5, 6), food_rarities=(3, 5, 6), manifest_version=4)
    clock = FixedClock()
    clock.value = clock.value.replace(hour=0)  # 北京08点食用，09点搬出，12点搬入。
    owner = _identity(message_id="seed")
    await FrameworkService(db).touch_identity(owner)
    now = iso_timestamp(clock.now())
    async with db.transaction() as session:
        await session.execute(
            "INSERT INTO player_catch_quota_bonuses(player_id,permanent_bonus,weekly_bonus,weekly_expires_at,"
            "created_at,updated_at) VALUES (?,5,5,?,?,?)",
            (owner.player_id, iso_timestamp(clock.now() + timedelta(days=7)), now, now),
        )
    await eat_fixture(db, clock, "SUSHI", "猪寿司拼盘", "today-window-catches", {"count": 2})
    eaten = await eat_fixture(db, clock, "MOON", "月栖萤光卷", "catch-window-transfer")
    assert eaten.reward_payload["transferred_uses"] == 17
    clock.value += timedelta(hours=1)
    game = GameplayService(
        db, CatchingSection(cooldown_seconds=0), clock=clock, random_source=SequenceRandom(*([0.01] * 1500))
    )
    assert (await game.profile(_identity(message_id="blocked-profile"))).daily_limit == 0
    with pytest.raises(DailyCatchLimitError, match="封存本时段"):
        await game.catch(_identity(message_id="blocked"))
    await eat_fixture(db, clock, "EXTRA", "其他额度菜", "extra-catches", {"count": 4})
    await eat_fixture(
        db, clock, "DEDICATED", "其他专属抓猪", "next-six-star-catch", {"six_star_percent": 50, "uses": 2}
    )
    for index in range(2):
        result = await game.catch(_identity(message_id=f"dedicated-{index}"))
        assert result.daily_count == 0 and result.daily_limit == 4
    for index in range(4):
        result = await game.catch(_identity(message_id=f"blocked-extra-{index}"))
        assert result.pig.rarity == 1
        assert not any("月栖萤光卷平移时段" in line for line in result.effect_summaries)
    state = await db.fetch_one("SELECT transferred_uses FROM player_catch_window_transfers")
    assert state[0] == 17
    clock.value += timedelta(hours=3)
    await eat_fixture(db, clock, "TARGETEXTRA", "其他额度菜", "extra-catches", {"count": 4})
    assert (await game.profile(_identity(message_id="target-profile"))).daily_limit == 38
    for index in range(34):
        result = await game.catch(_identity(message_id=f"moon-{index}"))
        assert result.weights == pytest.approx((0, 0, 0, 42, 40, 18))
        assert result.pig.rarity == 4
    for index in range(4):
        result = await game.catch(_identity(message_id=f"target-extra-{index}"))
        assert result.pig.rarity == 1
        assert not any("月栖萤光卷平移时段" in line for line in result.effect_summaries)
    with pytest.raises(DailyCatchLimitError):
        await game.catch(_identity(message_id="exhausted"))
    state = await db.fetch_one("SELECT target_catches_used FROM player_catch_window_transfers")
    assert state[0] == 34
    # 同消息重试不再增加结算次数。
    retry = await game.catch(_identity(message_id="moon-0"))
    assert not retry.receipt_created
    await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probability_effect,params,expected",
    [
        ("next-six-star-cook", {"six_star_percent": 60, "uses": 2}, 60),
        ("next-six-star-cook-bonus", {"bonus_percent": 15}, 25),
    ],
)
async def test_mousse_preserved_on_failure_then_duplicates_with_probability_food(
    tmp_path: Path,
    probability_effect,
    params,
    expected,
):
    db = await _database_with_catalog(tmp_path, pig_rarities=(6,), food_rarities=(5, 6), manifest_version=4)
    clock = FixedClock()
    owner = _identity(message_id="seed")
    await FrameworkService(db).touch_identity(owner)
    for code in ("MOUSSE1", "MOUSSE2"):
        await eat_fixture(db, clock, code, "彩彩修车猪慕斯", "next-six-star-cook-duplicate")
    # 先失败，不能用掉任何加餐机会。
    for index in range(3):
        await _insert_pig(
            db,
            player_id=owner.player_id,
            scope_id=owner.scope.value,
            template_id="pig-6-group",
            rarity=6,
            display_name="六星测试猪",
            official_value=25_000,
            short_code=f"PIG{index}",
            instance_id=f"pig-{index}",
        )
    failing = EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        random_source=SequenceRandom(*([0.01] * 10)),
    )
    failed = await failing.cook(_identity(message_id="failure"), "六星测试猪#PIG0")
    assert len(failed.foods) == 1 and failed.foods[0].rarity == 5
    assert any("机会保留" in line for line in failed.effect_summaries)
    await eat_fixture(db, clock, "PROB", "概率美食", probability_effect, params)
    successful = EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        random_source=SequenceRandom(*([0.999] * 20)),
    )
    result = await successful.cook(_identity(message_id="success"), "六星测试猪#PIG1")
    assert result.weights[5] == pytest.approx(expected)
    assert len(result.foods) == 2
    assert all(food.rarity == 6 for food in result.foods)
    assert len({food.short_code for food in result.foods}) == 2
    assert result.foods[0].official_value == result.foods[1].official_value
    assert result.coin_reward == 1500 and result.experience_reward == 800
    view = food_card_view(result.foods[0], mode_label="做菜成功", cooking=result)
    assert view.bonus_label == "彩彩慕斯加餐"
    assert result.foods[1].selector in view.bonus_selector
    assert result.foods[1].selector in format_cooking_summary(result)
    # 重建服务模拟请求重试；收据必须幂等。
    retry = await EconomyService(db, CookingSection(), EconomySection(), clock=clock).cook(
        _identity(message_id="success"), "六星测试猪#PIG1"
    )
    assert not retry.receipt_created
    assert [food.short_code for food in retry.foods] == [food.short_code for food in result.foods]
    row = await db.fetch_one(
        "SELECT SUM(granted_uses-consumed_uses) FROM player_food_effects WHERE effect_id='next-six-star-cook-duplicate'"
    )
    assert row[0] == 1
    await db.close()


@pytest.mark.asyncio
async def test_mousse_alone_allows_batch_and_survives_low_rarity(tmp_path: Path):
    db = await _database_with_catalog(tmp_path, pig_rarities=(1, 6), food_rarities=(1, 5, 6), manifest_version=4)
    clock = FixedClock()
    owner = _identity(message_id="seed")
    await FrameworkService(db).touch_identity(owner)
    await eat_fixture(db, clock, "MOUSSE", "彩彩修车猪慕斯", "next-six-star-cook-duplicate")
    for index in range(3):
        await _insert_pig(
            db,
            player_id=owner.player_id,
            scope_id=owner.scope.value,
            template_id="pig-1-common",
            rarity=1,
            display_name="普通测试猪",
            official_value=20,
            short_code=f"LOW{index}",
            instance_id=f"pig-{index}",
        )
    service = EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        random_source=SequenceRandom(*([0.01] * 50)),
    )
    await service.batch_cook(_identity(message_id="batch"), 1)
    row = await db.fetch_one(
        "SELECT SUM(granted_uses-consumed_uses) FROM player_food_effects WHERE effect_id='next-six-star-cook-duplicate'"
    )
    assert row[0] == 1
    await db.close()
