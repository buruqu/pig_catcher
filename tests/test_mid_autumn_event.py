"""中秋开场福利和第三期吃月饼冲榜的隔离数据库验收。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.errors import DailyCatchLimitError
from pig_catcher.domain.mid_autumn import (
    JADE_RABBIT_PIG_ID,
    LANTERN_PIG_ID,
    MID_AUTUMN_START,
    OSMANTHUS_MOONCAKE_ID,
    OSMANTHUS_PIG_ID,
    RED_BEAN_MOONCAKE_ID,
    SNOW_SKIN_MOONCAKE_ID,
)
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.infrastructure.repositories.receipts import ReceiptRepository
from pig_catcher.services.achievements import AchievementService
from pig_catcher.services.command_state import iso_timestamp
from pig_catcher.services.gameplay import GameplayService
from pig_catcher.services.scheduled_rewards import MID_AUTUMN_ID, ScheduledRewardService
from pig_catcher.services.weekly_competitions import WeeklyCompetitionService

from .test_admin_commands import _identity
from .test_gameplay import MutableClock, SequenceRandom, _catch_rolls, _database_with_catalog, _food_entry, _pig_entry


@pytest.mark.asyncio
async def test_mid_autumn_gift_is_cutoff_scoped_atomic_and_once(tmp_path):
    pigs = (
        (LANTERN_PIG_ID, 5, "灯笼照月猪"),
        (OSMANTHUS_PIG_ID, 5, "桂花猪"),
        (JADE_RABBIT_PIG_ID, 5, "玉兔猪"),
    )
    foods = (
        (RED_BEAN_MOONCAKE_ID, 5, "豆沙猪月饼"),
        (OSMANTHUS_MOONCAKE_ID, 5, "桂花猪月饼"),
        (SNOW_SKIN_MOONCAKE_ID, 5, "冰皮猪月饼"),
    )
    db = await _database_with_catalog(
        tmp_path,
        [*(_pig_entry(key, rarity=rarity, display_name=name) for key, rarity, name in pigs),
         *(_food_entry(key, rarity=rarity, display_name=name, group_id=None, effect_id="", effect_params={})
           for key, rarity, name in foods)],
    )
    clock = MutableClock(MID_AUTUMN_START - timedelta(hours=1))
    gifts = ScheduledRewardService(db, clock=clock)
    alice = _identity(user_id="alice", display_name="阿月")
    other = _identity(user_id="other", display_name="另一群玩家", group_id="10002")
    async with db.transaction() as session:
        for identity in (alice, other):
            await FrameworkRepository().touch_identity(session, identity=identity, now=iso_timestamp(clock.value))
    try:
        assert await gifts.ensure_mid_autumn_scheduled()
        assert not (await gifts.schedule_mid_autumn([alice.scope.value, other.scope.value]))["created"]
        assert await gifts.process_due() == 0
        clock.value = MID_AUTUMN_START + timedelta(minutes=1)
        late = _identity(user_id="late", display_name="迟到玩家")
        async with db.transaction() as session:
            await FrameworkRepository().touch_identity(session, identity=late, now=iso_timestamp(clock.value))
        assert await gifts.process_due() == 2
        assert await gifts.process_due() == 0
        grant_count = await db.fetch_one(
            "SELECT COUNT(*) FROM scheduled_reward_grants WHERE campaign_id=?", (MID_AUTUMN_ID,)
        )
        assert grant_count[0] == 2
        assert (await db.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 6
        assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 6
        alice_balance = await db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (alice.player_id,))
        assert alice_balance[0] == 92_500
        assert (await db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (late.player_id,)))[0] == 0
        codes = await db.fetch_one(
            "SELECT COUNT(DISTINCT short_code) FROM ("
            "SELECT short_code FROM pig_instances UNION ALL SELECT short_code FROM food_instances)"
        )
        assert codes[0] == 12
        assert not await db.fetch_all("PRAGMA foreign_key_check")

        # 只有活动内实际吃下的月饼回执计分；领取礼包本身不计。
        ranking = WeeklyCompetitionService(db, clock=clock)
        await ranking.initialize()
        assert (await ranking.leaderboard(alice)).total_count == 0
        owned_foods = await db.fetch_all(
            "SELECT food_instance_id FROM food_instances WHERE owner_player_id=? ORDER BY template_id",
            (alice.player_id,),
        )
        async with db.transaction() as session:
            for index, food in enumerate(owned_foods, 1):
                occurred = iso_timestamp(MID_AUTUMN_START + timedelta(minutes=index))
                await session.execute(
                    "INSERT INTO command_receipts(receipt_id,idempotency_key,scope_id,player_id,command_name,"
                    "request_fingerprint,result_type,result_object_id,result_json,text_summary,catch_quota_cost,"
                    "send_status,created_at,updated_at) VALUES(?,?,?,?,?,'midautumn-test','food-consumed',?,"
                    "'{}','吃月饼',0,'sent',?,?)",
                    (f"moon-eat-{index}", f"moon-eat-key-{index}", alice.scope.value, alice.player_id,
                     "pig-catcher.eat", food["food_instance_id"], occurred, occurred),
                )
        page = await ranking.leaderboard(alice)
        assert page.season_number == 3 and page.eating_metric
        assert page.entries[0].score == 3
        await ranking.initialize()
        assert (await ranking.leaderboard(alice)).entries[0].score == 3
        achievements = AchievementService(db, clock=clock)
        await achievements.initialize()
        async with db.transaction(immediate=False) as session:
            receipts = [
                await ReceiptRepository().get_by_key(session, f"moon-eat-key-{index}")
                for index in range(1, 4)
            ]
        unlocked = []
        for receipt in receipts:
            unlocked.extend(await achievements.process_receipt(receipt))
        assert any(item.achievement_id == "midautumn-2026-three-mooncakes" for item in unlocked)
        stamp = await db.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory WHERE player_id=? AND reward_id=?",
            (alice.player_id, "weekly-003-mooncake-stamp"),
        )
        assert stamp[0] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_mid_autumn_base_catch_quota_is_ten_then_returns_to_normal(tmp_path):
    db = await _database_with_catalog(tmp_path, [_pig_entry("ordinary-pig", rarity=1)])
    clock = MutableClock(MID_AUTUMN_START + timedelta(hours=1))
    gameplay = GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(*(roll for _ in range(10) for roll in _catch_rolls())),
    )
    try:
        identity = _identity(user_id="quota-player", display_name="额度玩家")
        assert (await gameplay.profile(identity)).daily_limit == 10
        for index in range(10):
            result = await gameplay.catch(
                _identity(user_id="quota-player", display_name="额度玩家", message_id=f"moon-quota-{index}")
            )
            assert result.daily_limit == 10
        with pytest.raises(DailyCatchLimitError):
            await gameplay.catch(_identity(user_id="quota-player", display_name="额度玩家", message_id="moon-quota-11"))
        clock.value = MID_AUTUMN_START + timedelta(days=6, hours=1)
        assert (await gameplay.profile(identity)).daily_limit == 5
    finally:
        await db.close()


@pytest.mark.parametrize(
    ("festival_id", "selection_roll"),
    [(LANTERN_PIG_ID, 0.1), (OSMANTHUS_PIG_ID, 0.5), (JADE_RABBIT_PIG_ID, 0.9)],
)
@pytest.mark.asyncio
async def test_festival_catch_up_selects_one_five_star_pig_and_stops_after_event(
    tmp_path, festival_id, selection_roll
):
    db = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("ordinary-5", rarity=5),
            _pig_entry(LANTERN_PIG_ID, rarity=5),
            _pig_entry(OSMANTHUS_PIG_ID, rarity=5),
            _pig_entry(JADE_RABBIT_PIG_ID, rarity=5),
        ],
    )
    weights = {f"rarity_{index}_weight": 100.0 if index == 5 else 0.0 for index in range(1, 7)}
    settings = CatchingSection(cooldown_seconds=0, **weights)
    event_clock = MutableClock(MID_AUTUMN_START + timedelta(hours=1))
    # 五星结果先投 50% UP，再等概率选三只五星中秋猪。
    base_rolls = _catch_rolls()
    event_rolls = (base_rolls[0], base_rolls[1], 0.49, selection_roll, *base_rolls[2:])
    gameplay = GameplayService(db, settings, clock=event_clock, random_source=SequenceRandom(*event_rolls))
    try:
        winner = await gameplay.catch(_identity(user_id="up-player", display_name="UP 玩家", message_id="up-event"))
        assert winner.pig.template_id == festival_id
        assert winner.pig.rarity == 5
        assert winner.daily_limit == 10
        after_clock = MutableClock(MID_AUTUMN_START + timedelta(days=6, hours=1))
        after = GameplayService(db, settings, clock=after_clock, random_source=SequenceRandom(*_catch_rolls()))
        ordinary = await after.catch(_identity(user_id="up-player", display_name="UP 玩家", message_id="up-after"))
        assert ordinary.pig.template_id == "ordinary-5"
        assert ordinary.daily_limit == 5
    finally:
        await db.close()
