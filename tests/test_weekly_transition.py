"""定时交接：原子发奖、双群公告、实际送达后120秒及断点恢复。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.services.receipts import ReceiptService
from pig_catcher.services.weekly_competitions import WeeklyCompetitionService
from pig_catcher.services.weekly_transition import WeeklyTransitionService

from .test_weekly_competitions import _identity, _seed_catch
from .test_weekly_sushi import cook, setup

END = datetime(2026, 9, 7, 16, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def world(tmp_path):
    db, clock, economy, weekly = await setup(tmp_path)
    clock.value = END - timedelta(minutes=1)
    identities = [_identity(f"winner-{i}", f"猪友{i}", group_id=f"group-{i}") for i in range(2)]
    for i, identity in enumerate(identities):
        await _seed_catch(db, identity, serial=i, value=100 + i, occurred_at="2026-09-07T15:58:00.000Z")
    await weekly.initialize()
    transition = WeeklyTransitionService(weekly)
    await transition.schedule(previous_season=1, next_season=2, scope_ids=[i.scope.value for i in identities])
    try:
        yield db, clock, economy, weekly, transition, identities
    finally:
        await db.close()


async def mark(db, clock, result, *, failed=False):
    receipts = ReceiptService(db, clock=clock)
    assert await receipts.claim_send(result.receipt.receipt_id)
    if failed:
        await receipts.mark_failed(result.receipt.receipt_id, "离线注入：QQ拒绝发送")
    else:
        await receipts.mark_sent(result.receipt.receipt_id)


async def test_midnight_settles_without_player_commands_then_two_minutes_after_last_send(world):
    db, clock, _, weekly, transition, identities = world
    await transition.process_due()
    assert not await transition.pending_notices()
    assert not await db.fetch_all("SELECT * FROM weekly_competition_awards")
    clock.value = END
    await transition.process_due()
    notices = await transition.pending_notices()
    assert len(notices) == 2
    assert len(await db.fetch_all("SELECT * FROM weekly_competition_awards")) == 2
    for index, (_, result) in enumerate(notices):
        assert result.view.presentation == "weekly-event"
        assert "10,000" not in result.view.text() or "奖励" in result.view.text()
        assert "猪币 ×10000" in result.view.text()
        assert identities[index].user_id not in result.view.text()
        clock.value = END + timedelta(seconds=10 * (index + 1))
        await mark(db, clock, result)
    # 重启后也从实际最后一次送达计时，不是固定00:02提前开。
    transition = WeeklyTransitionService(WeeklyCompetitionService(db, clock=clock))
    clock.value = END + timedelta(seconds=139)
    await weekly.initialize()
    await transition.process_due()
    assert (await db.fetch_one("SELECT status FROM weekly_competitions WHERE season_number=2"))["status"] == "scheduled"
    assert not await transition.pending_notices()
    clock.value += timedelta(seconds=1)
    await transition.process_due()
    current = await db.fetch_one("SELECT starts_at,status FROM weekly_competitions WHERE season_number=2")
    assert dict(current) == {"starts_at": "2026-09-07T16:02:20.000Z", "status": "active"}
    opens = await transition.pending_notices()
    assert len(opens) == 2
    for _, result in opens:
        assert json.loads(result.receipt.result_json)["stage"] == "open"
        assert "寿司拼盘大王" in result.view.text()
        await mark(db, clock, result)
    await transition.process_due()
    await weekly.initialize()
    assert not await transition.pending_notices()
    assert len(await db.fetch_all("SELECT * FROM weekly_competition_awards")) == 2
    assert len(await db.fetch_all("SELECT 1 FROM currency_ledger WHERE reason_code='weekly-competition-reward'")) == 2


@pytest.mark.parametrize("failure", ["failed", "claimed", "pending"])
async def test_failed_or_ambiguous_close_blocks_early_start_without_duplicate_reward(world, failure):
    db, clock, _, weekly, transition, _ = world
    clock.value = END
    await transition.process_due()
    notices = await transition.pending_notices()
    await mark(db, clock, notices[0][1])
    receipt = notices[1][1].receipt
    service = ReceiptService(db, clock=clock)
    if failure != "pending":
        await service.claim_send(receipt.receipt_id)
    if failure == "failed":
        await service.mark_failed(receipt.receipt_id, "测试拒绝")
    clock.value += timedelta(hours=2)
    await weekly.initialize()
    await transition.process_due()
    assert (await db.fetch_one("SELECT status FROM weekly_competitions WHERE season_number=2"))["status"] == "scheduled"
    pending = await transition.pending_notices()
    assert len(pending) == (1 if failure == "pending" else 0)
    assert len(await db.fetch_all("SELECT * FROM weekly_competition_awards")) == 2


async def test_actual_opening_boundary_excludes_two_minute_gap_and_backfill(world):
    db, clock, economy, weekly, transition, identities = world
    chef = identities[0]
    clock.value = END
    await transition.process_due()
    for _, notice in await transition.pending_notices():
        await mark(db, clock, notice)
    clock.value = END + timedelta(seconds=119)
    early = await cook(db, clock, economy, chef, 200)
    assert not await weekly.process_receipt(early.receipt)
    clock.value += timedelta(seconds=1)
    await transition.process_due()
    good = await cook(db, clock, economy, chef, 201)
    assert await weekly.process_receipt(good.receipt)
    await weekly.initialize()
    assert (await weekly.leaderboard(chef)).player_score_text == "1 份"


async def test_schedule_replay_drift_and_bad_scope_rollback(world):
    db, _, _, _, transition, identities = world
    assert not (
        await transition.schedule(previous_season=1, next_season=2, scope_ids=[i.scope.value for i in identities])
    )["created"]
    with pytest.raises(DomainValidationError, match="不同参数"):
        await transition.schedule(previous_season=1, next_season=2, scope_ids=[identities[0].scope.value])
    assert len(await db.fetch_all("SELECT * FROM weekly_transitions")) == 1


async def test_settlement_rolls_back_without_creating_notice(world, monkeypatch):
    db, clock, _, weekly, transition, _ = world
    original = weekly._grant_rewards

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("注入发奖失败")

    monkeypatch.setattr(weekly, "_grant_rewards", fail)
    clock.value = END
    with pytest.raises(RuntimeError, match="注入"):
        await transition.process_due()
    assert not await db.fetch_all("SELECT * FROM weekly_competition_awards")
    assert not await transition.pending_notices()
    monkeypatch.setattr(weekly, "_grant_rewards", original)
    await transition.process_due()
    assert len(await transition.pending_notices()) == 2


async def test_no_plan_never_broadcasts_and_normal_weekly_tick_still_settles(tmp_path):
    db, clock, _, weekly = await setup(tmp_path)
    try:
        clock.value = END - timedelta(seconds=1)
        await weekly.initialize()
        transition = WeeklyTransitionService(weekly)
        clock.value = END
        await transition.process_due()
        assert not await transition.pending_notices()
        assert (await db.fetch_one("SELECT status FROM weekly_competitions WHERE season_number=1"))[
            "status"
        ] == "settled"
    finally:
        await db.close()
