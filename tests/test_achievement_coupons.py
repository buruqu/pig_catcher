"""Typed coupon costs, preview binding, irreversible uses and no probability effects."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from pig_catcher.domain.activity_achievements import ACTIVITY_REWARDS
from pig_catcher.domain.dispatch import MATERIAL_SCALE
from pig_catcher.domain.errors import PigCatcherError, ReceiptConflictError
from pig_catcher.domain.tour import score_stage
from pig_catcher.domain.tour_catalog import default_plan
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.infrastructure.repositories.dispatch import iso_ms, timestamp_ms
from pig_catcher.services.achievement_rewards import AchievementRewardService
from pig_catcher.services.achievements import AchievementService

from .test_battle import world as _battle_fixture
from .test_dispatch import world as _dispatch_fixture
from .test_tour import pure_members  # noqa: F401
from .test_tour import world as _tour_fixture

battle_world = _battle_fixture
dispatch_world = _dispatch_fixture
tour_world = _tour_fixture


async def grant(w, ticket, quantity=2, identity=None):
    actor = identity or getattr(w, "identity", None) or w.a
    async with w.db.transaction() as session:
        await AchievementRepository().grant_reward(
            session,
            player_id=actor.player_id,
            reward_type=ACTIVITY_REWARDS[ticket]["kind"],
            reward_id=ticket,
            quantity=quantity,
            now=iso_ms(timestamp_ms(w.clock.now())),
        )


async def use(w, ticket, identity=None):
    actor = identity or getattr(w, "identity", None) or w.a
    service = AchievementRewardService(AchievementService(w.db, clock=w.clock))
    return await service.execute(replace(actor, message_id=uuid4().hex), "使用 " + ACTIVITY_REWARDS[ticket]["name"])


async def quantity(w, ticket, identity=None):
    actor = identity or getattr(w, "identity", None) or w.a
    row = await w.db.fetch_one(
        "SELECT quantity FROM achievement_reward_inventory WHERE player_id=? AND reward_id=?", (actor.player_id, ticket)
    )
    return row[0] if row else 0


async def test_choice_preview_exact_quantity_confirm_retry_and_expiry(tour_world):
    w = tour_world
    await grant(w, "materials-choice", 20)
    service = AchievementRewardService(AchievementService(w.db, clock=w.clock))
    actor = replace(w.identity, message_id="preview")
    preview = await service.execute(actor, "材料 基础材料自选份 训练矿石 10")
    assert "仅兑换训练矿石×10" in preview.view.text()
    assert await quantity(w, "materials-choice") == 20
    confirm = replace(actor, message_id="confirm")
    await service.execute(confirm, "确认")
    await service.execute(confirm, "确认")
    assert await quantity(w, "materials-choice") == 10
    assert (await w.db.fetch_one("SELECT quantity FROM material_balances WHERE material_id='training-ore'"))[0] == 10
    with pytest.raises(ReceiptConflictError):
        await service.execute(confirm, "材料 基础材料自选份 训练矿石 1")
    await service.execute(replace(actor, message_id="preview2"), "材料 基础材料自选份 舞台组件 3")
    w.clock.value += timedelta(seconds=30)
    with pytest.raises(PigCatcherError, match="有效"):
        await service.execute(replace(actor, message_id="expired"), "确认")
    assert await quantity(w, "materials-choice") == 10
    await grant(w, "training-choice")
    with pytest.raises(PigCatcherError, match="范围"):
        await service.execute(replace(actor, message_id="invalid"), "材料 训练材料自选份 舞台组件 1")


async def test_dispatch_luggage_is_flat_three_and_surprise_is_visual_only(dispatch_world):
    w = dispatch_world
    await w.team()
    await grant(w, "dispatch-luggage")
    await grant(w, "dispatch-story")
    await use(w, "dispatch-luggage")
    await use(w, "dispatch-story")
    await w.send("出发 1 青草近郊 4小时")
    assert await quantity(w, "dispatch-luggage") == 2
    departed = await w.send("确认")
    assert "剩余1张" in departed.view.text()
    assert await quantity(w, "dispatch-luggage") == 1
    await w.advance(4)
    rows = await w.db.fetch_all("SELECT * FROM material_ledger WHERE source_kind='achievement-coupon'")
    assert (
        len(rows) == 1 and rows[0]["material_id"] == "travel-supplies" and rows[0]["delta_units"] == 3 * MATERIAL_SCALE
    )
    trip = await w.db.fetch_one("SELECT * FROM dispatch_trips")
    detail = await w.send(trip["trip_id"], "journal")
    assert "口袋里的第六张明信片" in detail.view.text()
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_ticket_effects"))[0] == 0
    await w.send()
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_coupon_uses"))[0] == 2


async def test_dispatch_fare_free_route_preserves_and_recall_does_not_refund(dispatch_world):
    w = dispatch_world
    await w.team()
    await grant(w, "dispatch-bill")
    await use(w, "dispatch-bill")
    await w.start()
    assert await quantity(w, "dispatch-bill") == 2
    await w.advance(4)
    await w.start("回声矿洞", 4)
    assert await quantity(w, "dispatch-bill") == 1
    await w.advance(1)
    await w.send("召回 1")
    await w.send("确认")
    assert await quantity(w, "dispatch-bill") == 1
    row = await w.db.fetch_one("SELECT effect_json FROM achievement_coupon_uses")
    assert "coin_saving" in row[0]


async def test_dispatch_changed_selection_invalidates_confirmation(dispatch_world):
    w = dispatch_world
    await w.team()
    await grant(w, "dispatch-luggage")
    await grant(w, "dispatch-bill")
    await use(w, "dispatch-luggage")
    await w.send("出发 1 青草近郊 4小时")
    await use(w, "dispatch-bill")
    with pytest.raises(PigCatcherError, match="成就券"):
        await w.send("确认")
    assert await quantity(w, "dispatch-luggage") == await quantity(w, "dispatch-bill") == 2
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM dispatch_trips"))[0] == 0


async def test_tour_date_respects_seven_cap_and_idempotency(tour_world):
    w = tour_world
    await grant(w, "tour-date", 9)
    await use(w, "tour-date")
    assert (await w.db.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 3
    for _ in range(4):
        await use(w, "tour-date")
    with pytest.raises(PigCatcherError, match="7张"):
        await use(w, "tour-date")
    assert await quantity(w, "tour-date") == 4


async def test_tour_selected_coupon_one_stage_only_and_photo_after_completion(tour_world):
    w = tour_world
    for ticket in ("tour-steady-stage", "tour-encore-photo"):
        await grant(w, ticket)
        await use(w, ticket)
    await w.send("出发")
    assert await quantity(w, "tour-encore-photo") == 2
    await w.send("确认")
    assert await quantity(w, "tour-encore-photo") == 1
    first = await w.send("继续")
    assert "安可稳场券" in first.view.text() and "剩余1张" in first.view.text()
    result = await w.send("一键")
    assert "安可之后，留一盏灯" in result.view.text()
    assert await quantity(w, "tour-steady-stage") == 1  # no automatic consumption of spare coupon
    assert (
        await w.db.fetch_one("SELECT COUNT(*) FROM tour_collections WHERE collection_key LIKE 'achievement-photo:%'")
    )[0] == 1


@pytest.mark.parametrize("seed", [str(n) for n in range(50)])
def test_steady_preserves_roll_and_never_double_compensates_or_exceeds_three(seed):
    args = dict(equipment=5, stage_number=1, seed=seed)
    plan = default_plan()
    plan["tool"] = "cable"
    before = score_stage(pure_members(full=True), plan, **args)
    after = score_stage(pure_members(full=True), plan, steady_coupon=True, **args)
    assert before["variation_raw"] == after["variation_raw"]
    assert after["variation"] == max(0, before["variation"])
    assert 0 <= after["coupon_recovery"] <= 3
    assert after["score"] <= 100
    assert after["components"] == before["components"]


async def test_battle_coupon_waits_for_acceptance_and_keeps_initial_weights(battle_world):
    w = battle_world
    await grant(w, "battle-banner")
    await use(w, "battle-banner")
    await w.invite()
    assert await quantity(w, "battle-banner") == 2
    result = await w.send("接受", "challenge", actor=w.b)
    assert await quantity(w, "battle-banner") == 1
    assert "原创入场海报" in result.view.text()
    assert all(f.weight == "5" for f in result.view.fighters)


async def test_training_discount_materials_and_repeated_confirm(battle_world):
    w = battle_world
    await w.fund()
    await grant(w, "training-rebate")
    await use(w, "training-rebate")
    await w.send("强化")
    assert await quantity(w, "training-rebate") == 2
    coin_before = (await w.db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (w.a.player_id,)))[0]
    result = await w.send("确认", mid="warmup-confirm")
    await w.send("确认", mid="warmup-confirm")
    assert await quantity(w, "training-rebate") == 1
    assert "减免300猪币" in result.view.text()
    assert (await w.db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (w.a.player_id,)))[
        0
    ] == coin_before
    rows = await w.db.fetch_all("SELECT delta_units FROM material_ledger WHERE source_kind='battle-spend'")
    assert sorted(r[0] for r in rows) == [
        -60 * MATERIAL_SCALE,
        -20 * MATERIAL_SCALE,
        -20 * MATERIAL_SCALE,
        -20 * MATERIAL_SCALE,
    ]
