from datetime import timedelta

import pytest

from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.errors import FoodEffectError
from pig_catcher.domain.food_effects import EXCLUSIVE_CATCH_EFFECTS, QUOTA_EXEMPT_CATCH_EFFECTS
from pig_catcher.domain.mirror_food import HISTORY_MIRROR_CATCH, mirrored_history_weights
from pig_catcher.services import FrameworkService, GameplayService
from tests.test_economy import FixedClock, SequenceRandom, _database_with_catalog, _identity
from tests.test_food_social_balance_v68 import eat_fixture


def test_history_is_fixed_inverse_not_shuffled_or_extra_quota():
    assert mirrored_history_weights([1, 1, 1, 2, 2, 3, 4, 5, 6, 6]) == [20, 10, 10, 10, 20, 30]
    assert HISTORY_MIRROR_CATCH in EXCLUSIVE_CATCH_EFFECTS
    assert HISTORY_MIRROR_CATCH not in QUOTA_EXEMPT_CATCH_EFFECTS
    with pytest.raises(FoodEffectError):
        mirrored_history_weights([1] * 9)


@pytest.mark.asyncio
async def test_water_mirror_two_per_registered_player_idempotent_and_no_recursion(tmp_path):
    db = await _database_with_catalog(tmp_path, pig_rarities=(1, 6), food_rarities=(6,), manifest_version=4)
    clock = FixedClock()
    owner = _identity(message_id="owner")
    await FrameworkService(db).touch_identity(owner)
    eaten = await eat_fixture(db, clock, "MIRROR", "流形水镜冻", "group-water-mirror")
    assert "2次" in eaten.effect.summary
    game = GameplayService(
        db, CatchingSection(cooldown_seconds=0), clock=clock, random_source=SequenceRandom(*([0.01] * 100))
    )
    for index in range(3):
        result = await game.catch(_identity(message_id=f"mirror-catch-{index}"))
        assert any("复制了" in s for s in result.effect_summaries) == (index < 2)
    retry = await game.catch(_identity(message_id="mirror-catch-0"))
    assert not retry.receipt_created
    assert (await db.fetch_one("SELECT COUNT(*) FROM water_mirror_claims"))[0] == 2
    assert (await db.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 5
    assert (await db.fetch_one("SELECT remaining FROM water_mirror_targets"))[0] == 0
    await db.close()


@pytest.mark.asyncio
async def test_parfait_requires_ten_catches_then_freezes_reverse_history(tmp_path):
    db = await _database_with_catalog(tmp_path, pig_rarities=(1, 6), food_rarities=(6,), manifest_version=4)
    clock = FixedClock()
    owner = _identity(message_id="owner")
    await FrameworkService(db).touch_identity(owner)
    with pytest.raises(FoodEffectError, match="最近10次"):
        await eat_fixture(db, clock, "SHORT", "翠玉抹茶芭菲", HISTORY_MIRROR_CATCH)
    game = GameplayService(
        db, CatchingSection(cooldown_seconds=0), clock=clock, random_source=SequenceRandom(*([0.01] * 200))
    )
    for index in range(10):
        clock.value += timedelta(hours=3)
        await game.catch(_identity(message_id=f"history-{index}"))
    before = await game.profile(_identity(message_id="before-mirror"))
    await eat_fixture(db, clock, "FULL", "翠玉抹茶芭菲", HISTORY_MIRROR_CATCH)
    result = await game.catch(_identity(message_id="mirrored"))
    assert result.weights == pytest.approx((0, 0, 0, 0, 0, 100))
    assert result.pig.rarity == 6
    assert result.daily_count == before.daily_count + 1  # no dedicated quota
    assert any("9" in s for s in result.effect_summaries)
    await db.close()
