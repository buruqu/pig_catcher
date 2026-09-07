"""Season 2 scores actual cooking receipts, not gifts or current inventory."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pig_catcher.config.model import CookingSection, EconomySection
from pig_catcher.domain.models import ScopeKey
from pig_catcher.domain.weekly_competitions import (
    WEEKLY_COMPETITIONS_BY_SEASON,
    WEEKLY_REWARD_NAMES,
    WEEKLY_SUSHI_MEDAL_ID,
)
from pig_catcher.services import EconomyService, FrameworkService, WeeklyCompetitionService
from pig_catcher.services.achievement_badges import AchievementBadgeService
from pig_catcher.services.achievements import AchievementService

from .test_economy import _database_with_catalog, _food_entry
from .test_weekly_competitions import MutableClock, _identity, _seed_catch

SUSHI = "food-r5-pig-sushi-platter"
START = datetime(2026, 9, 7, 16, 2, tzinfo=UTC)


class SushiRoll:
    def random(self):
        return 0.999


async def setup(tmp_path):
    food = {**_food_entry(5, template_suffix="sushi"), "template_id": SUSHI, "display_name": "猪寿司拼盘"}
    database = await _database_with_catalog(tmp_path, pig_rarities=(4,), food_rarities=(5,), extra_entries=(food,))
    async with database.transaction() as session:
        await session.execute("UPDATE food_templates SET enabled=0 WHERE template_id<>?", (SUSHI,))
    clock = MutableClock(START + timedelta(hours=1))
    economy = EconomyService(
        database, CookingSection(cook_cooldown_seconds=0), EconomySection(), clock=clock, random_source=SushiRoll()
    )
    return database, clock, economy, WeeklyCompetitionService(database, clock=clock)


async def cook(database, clock, economy, identity, serial, *, count=1):
    for index in range(count):
        await _seed_catch(
            database,
            identity,
            serial=serial + index,
            value=100,
            occurred_at=clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )
    identity = replace(identity, message_id=f"sushi-cook-{serial}")
    if count > 1:
        return await economy.batch_cook(identity, rarity=4)
    return await economy.cook(identity, f"周榜测试猪#W{serial:07d}")


@pytest.mark.asyncio
async def test_real_single_batch_counts_and_duplicate_receipt_replay(tmp_path):
    db, clock, economy, weekly = await setup(tmp_path)
    try:
        chef = _identity("chef", "寿司主厨")
        single = await cook(db, clock, economy, chef, 10)
        assert single.foods[0].template_id == SUSHI
        assert await weekly.process_receipt(single.receipt)
        assert not await weekly.process_receipt(single.receipt)
        clock.value += timedelta(minutes=1)
        batch = await cook(db, clock, economy, chef, 20, count=3)
        assert batch.food_count == 3
        assert await weekly.process_receipt(batch.receipt)
        assert not await weekly.process_receipt(batch.receipt)
        page = await weekly.leaderboard(chef)
        assert (page.season_number, page.cooking_metric, page.player_score_text) == (2, True, "4 份")
        assert page.entries[0].catch_count == 2  # two receipts, four portions
        await weekly.initialize()
        rows = await db.fetch_all("SELECT * FROM weekly_competition_entries")
        assert len(rows) == 2
        assert sum(len(json.loads(row["source_snapshot_json"])["matched_food_ids"]) for row in rows) == 4
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sold_eaten_and_transferred_foods_count_original_chef_on_backfill(tmp_path):
    db, clock, economy, weekly = await setup(tmp_path)
    try:
        chef, recipient = _identity("chef", "厨师"), _identity("recipient", "收赠者")
        result = await cook(db, clock, economy, chef, 30, count=3)
        await FrameworkService(db).touch_identity(recipient)
        async with db.transaction() as session:
            for food, state in zip(result.foods, ("sold", "consumed", "active"), strict=True):
                await session.execute(
                    "UPDATE food_instances SET state=?,owner_player_id=? WHERE food_instance_id=?",
                    (state, recipient.player_id, food.food_instance_id),
                )
        await weekly.initialize()
        page = await weekly.leaderboard(recipient)
        assert page.player_rank is None
        assert [(row.player_id, row.score) for row in page.entries] == [(chef.player_id, 3)]
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    ("admin", "gift", "trade", "coupon", "domain", "lottery", "other-food", "no-source", "foreign-scope", "no-output"),
)
async def test_other_acquisition_paths_are_not_cooking_points(tmp_path, invalid):
    db, clock, economy, weekly = await setup(tmp_path)
    try:
        chef = _identity("chef", "主厨")
        result = await cook(db, clock, economy, chef, 40)
        await FrameworkService(db).touch_identity(replace(chef, scope=ScopeKey("qq", "100")))
        food_id = result.foods[0].food_instance_id
        async with db.transaction() as session:
            if invalid in {"admin", "gift", "trade", "coupon", "domain", "lottery"}:
                await session.execute(
                    "UPDATE command_receipts SET command_name=? WHERE receipt_id=?",
                    (f"pig-catcher.{invalid}", result.receipt.receipt_id),
                )
            elif invalid == "other-food":
                await session.execute(
                    "UPDATE food_instances SET template_id='food-5-common' WHERE food_instance_id=?", (food_id,)
                )
            elif invalid == "no-source":
                await session.execute(
                    "UPDATE food_instances SET source_pig_instance_id=NULL WHERE food_instance_id=?", (food_id,)
                )
            elif invalid == "foreign-scope":
                await session.execute(
                    "UPDATE command_receipts SET scope_id='qq:100' WHERE receipt_id=?", (result.receipt.receipt_id,)
                )
            else:
                await session.execute(
                    "UPDATE command_receipts SET result_json='{}' WHERE receipt_id=?", (result.receipt.receipt_id,)
                )
        await weekly.initialize()
        assert (await weekly.leaderboard(chef)).total_count == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_duplicate_output_ids_count_once_and_exclude_non_sushi(tmp_path):
    db, clock, economy, weekly = await setup(tmp_path)
    try:
        chef = _identity("chef", "主厨")
        result = await cook(db, clock, economy, chef, 50, count=3)
        async with db.transaction() as session:
            await session.execute(
                "UPDATE food_instances SET template_id='food-5-common' WHERE food_instance_id=?",
                (result.foods[-1].food_instance_id,),
            )
            payload = json.loads(result.receipt.result_json)
            payload["food_instance_ids"].append(result.foods[0].food_instance_id)
            await session.execute(
                "UPDATE command_receipts SET result_json=? WHERE receipt_id=?",
                (json.dumps(payload), result.receipt.receipt_id),
            )
        await weekly.initialize()
        assert (await weekly.leaderboard(chef)).player_score_text == "2 份"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_boundary_scheduling_and_only_event_period_counts(tmp_path):
    db, clock, economy, weekly = await setup(tmp_path)
    try:
        chef = _identity("chef", "主厨")
        clock.value = START - timedelta(seconds=1)
        await cook(db, clock, economy, chef, 60)
        await weekly.initialize()
        assert (await weekly.leaderboard(chef)).season_number == 1
        row = await db.fetch_one("SELECT status FROM weekly_competitions WHERE season_number=2")
        assert row["status"] == "scheduled"
        clock.value = START
        result = await cook(db, clock, economy, chef, 61)
        assert await weekly.process_receipt(result.receipt)
        assert (await weekly.leaderboard(chef)).player_score_text == "1 份"
        clock.value = START + timedelta(days=7)
        result = await cook(db, clock, economy, chef, 62)
        assert not await weekly.process_receipt(result.receipt)
        page = await weekly.leaderboard(chef)
        assert page.status == "settled" and page.player_score_text == "1 份"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_tie_uses_earliest_final_score_not_biggest_batch_and_four_scopes(tmp_path):
    db, clock, economy, weekly = await setup(tmp_path)
    try:
        first = _identity("first", "先达成")
        second = _identity("second", "后达成")
        await cook(db, clock, economy, first, 70)
        clock.value += timedelta(minutes=1)
        await cook(db, clock, economy, first, 71)
        clock.value += timedelta(minutes=1)
        await cook(db, clock, economy, second, 72, count=2)
        for index, scope in enumerate(
            (
                ScopeKey("qq", "1092931381"),
                ScopeKey("qq", "237716658"),
                ScopeKey("qq-official", "5E5854406D0297D6FEAE696A13E3A339"),
                ScopeKey("qq-official", "9EA2810F378FBD7DC3219C56CEAB3520"),
            )
        ):
            identity = replace(first, scope=scope)
            await cook(db, clock, economy, identity, 80 + index)
            page = await weekly.leaderboard(identity)
            assert page.total_count == 1 and page.player_score_text == "1 份"
        page = await weekly.leaderboard(first)
        assert [row.player_id for row in page.entries] == [first.player_id, second.player_id]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_top_ten_settlement_grants_new_art_and_equips_without_double_rewards(tmp_path):
    db, clock, economy, weekly = await setup(tmp_path)
    try:
        players = [_identity(f"player-{index}", f"主厨{index}") for index in range(11)]
        for index, identity in enumerate(players):
            await cook(db, clock, economy, identity, 100 + index)
            clock.value += timedelta(seconds=1)
        await weekly.initialize()
        clock.value = START + timedelta(days=7)
        await weekly.initialize()
        await weekly.initialize()
        awards = await db.fetch_all("SELECT * FROM weekly_competition_awards")
        assert len(awards) == 10
        definition = WEEKLY_COMPETITIONS_BY_SEASON[2]
        for index, identity in enumerate(players):
            rewards = await db.fetch_all(
                "SELECT reward_id,quantity FROM achievement_reward_inventory WHERE player_id=?", (identity.player_id,)
            )
            actual = {row["reward_id"]: row["quantity"] for row in rewards}
            if index == 10:
                assert not actual
                continue
            expected = {
                item.reward_id: item.quantity
                for item in definition.rewards_for_rank(index + 1)
                if item.reward_type != "coin"
            }
            assert actual == expected
            assert actual[WEEKLY_SUSHI_MEDAL_ID] == 1
        assert len(await weekly.equip_competition_cosmetics(players[0], definition.name)) == 3
        badges = AchievementBadgeService(AchievementService(db, clock=clock), labels=WEEKLY_REWARD_NAMES)
        chosen = await badges.execute(replace(players[0], message_id="equip-medal"), "1 寿司拼盘大王·匠心寿司徽章")
        assert "匠心寿司徽章" in chosen.view.banner
        assert await weekly.equip_competition_cosmetics(players[-1], definition.name) is None
        assert (
            len(await db.fetch_all("SELECT 1 FROM currency_ledger WHERE reason_code='weekly-competition-reward'")) == 10
        )
    finally:
        await db.close()
