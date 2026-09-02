"""PiG Dream! 2.0 launch pack and first-day rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pig_catcher.config.model import LaunchCampaignSection
from pig_catcher.domain.item_bag import (
    BATTLE_PIG_CHOICE_COUPON,
    CODE_CHANGE_COUPON,
    FIVE_STAR_COLLAB_RANDOM_COUPON,
    FOOD_CHOICE_COUPON,
    PIG_CHOICE_COUPON,
)
from pig_catcher.domain.launch_campaign import (
    apply_first_day_high_star_weights,
    effective_window_limit,
    first_day_active,
)
from pig_catcher.services.launch_campaign import LaunchCampaignService

from .test_gameplay import MutableClock, SequenceRandom, _database_with_catalog, _food_entry, _identity


def _campaign() -> LaunchCampaignSection:
    return LaunchCampaignSection(enabled=True)


def test_first_day_window_and_weight_multiplier_end_exactly_at_midnight() -> None:
    config = _campaign()
    active = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    ended = datetime(2026, 9, 1, 16, 0, tzinfo=UTC)
    assert first_day_active(config, active)
    assert effective_window_limit(config, active, normal_limit=5) == 20
    assert effective_window_limit(config, ended, normal_limit=5) == 5
    adjusted = apply_first_day_high_star_weights((40, 30, 17, 8, 4, 1), config, active)
    assert sum(adjusted) == pytest.approx(100.0)
    assert sum(adjusted[3:]) > 13.0
    assert apply_first_day_high_star_weights((40, 30, 17, 8, 4, 1), config, ended) == (
        40,
        30,
        17,
        8,
        4,
        1,
    )


@pytest.mark.asyncio
async def test_starter_pack_is_atomic_complete_and_once_per_player(tmp_path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _food_entry(
                "food-r5-six-ways-pig",
                effect_id="next-six-star-cook-bonus",
                effect_params={"bonus_percent": 15},
                display_name="一猪六吃",
                rarity=5,
                group_id=None,
            )
        ],
    )
    identity = _identity(message_id="launch-first")
    service = LaunchCampaignService(
        database,
        _campaign(),
        clock=MutableClock(datetime(2026, 9, 1, 0, 0, tzinfo=UTC)),
        random_source=SequenceRandom(*([0.5] * 20)),
    )
    result = await service.claim_if_eligible(identity)
    assert result is not None and "50,000" in result.view.text()
    assert "编号修改券" in result.view.text() and "×3" in result.view.text()
    assert await service.claim_if_eligible(identity) is None
    player = await database.fetch_one(
        "SELECT coin_balance FROM players WHERE player_id=?", (identity.player_id,)
    )
    assert player is not None and int(player["coin_balance"]) == 50_000
    tickets = await database.fetch_all(
        "SELECT reward_id,quantity FROM achievement_reward_inventory WHERE player_id=?",
        (identity.player_id,),
    )
    assert {str(row["reward_id"]): int(row["quantity"]) for row in tickets} == {
        PIG_CHOICE_COUPON: 2,
        FOOD_CHOICE_COUPON: 1,
        BATTLE_PIG_CHOICE_COUPON: 1,
        FIVE_STAR_COLLAB_RANDOM_COUPON: 5,
        CODE_CHANGE_COUPON: 3,
    }
    foods = await database.fetch_one(
        "SELECT COUNT(*) AS amount FROM food_instances WHERE owner_player_id=? AND display_name_snapshot='一猪六吃'",
        (identity.player_id,),
    )
    assert foods is not None and int(foods["amount"]) == 6
    grants = await database.fetch_one("SELECT COUNT(*) AS amount FROM launch_campaign_grants")
    assert grants is not None and int(grants["amount"]) == 1
    await database.close()


@pytest.mark.asyncio
async def test_code_change_bonus_backfills_existing_players_once_and_future_players_get_it(tmp_path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _food_entry(
                "food-r5-six-ways-pig",
                effect_id="next-six-star-cook-bonus",
                effect_params={"bonus_percent": 15},
                display_name="一猪六吃",
                rarity=5,
                group_id=None,
            )
        ],
    )
    clock = MutableClock(datetime(2026, 9, 1, 0, 0, tzinfo=UTC))
    service = LaunchCampaignService(database, _campaign(), clock=clock)
    existing_one = _identity(message_id="existing-one")
    existing_two = _identity(
        user_id="existing-user-two", display_name="existing two", message_id="existing-two"
    )
    async with database.transaction() as session:
        for identity in (existing_one, existing_two):
            await service.framework.touch_identity(
                session, identity=identity, now="2026-09-01T00:00:00+00:00"
            )

    first = await service.grant_code_change_bonus_to_registered_players()
    second = await service.grant_code_change_bonus_to_registered_players()
    assert (first.registered_players, first.newly_granted, first.already_granted) == (2, 2, 0)
    assert (second.registered_players, second.newly_granted, second.already_granted) == (2, 0, 2)
    rows = await database.fetch_all(
        "SELECT player_id,quantity FROM reward_coupon_grants WHERE source_kind='launch-campaign' "
        "AND coupon_id=? ORDER BY player_id",
        (CODE_CHANGE_COUPON,),
    )
    assert [(str(row["player_id"]), int(row["quantity"])) for row in rows] == [
        (existing_one.player_id, 3),
        (existing_two.player_id, 3),
    ]
    audit = await database.fetch_one(
        "SELECT COUNT(*) AS amount FROM audit_events "
        "WHERE action='launch-event-code-change-bonus-granted'"
    )
    assert audit is not None and int(audit["amount"]) == 1

    future = _identity(user_id="future-user", display_name="future user", message_id="future-user")
    assert await service.claim_if_eligible(future) is not None
    future_quantity = await database.fetch_one(
        "SELECT quantity FROM achievement_reward_inventory WHERE player_id=? AND reward_id=?",
        (future.player_id, CODE_CHANGE_COUPON),
    )
    assert future_quantity is not None and int(future_quantity["quantity"]) == 3
    await database.close()
