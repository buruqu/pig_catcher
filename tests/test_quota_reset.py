"""Catch-quota window reset safety and group-isolation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.infrastructure.repositories import FrameworkRepository
from pig_catcher.services import CatchQuotaResetService, GameplayService

from .test_gameplay import (
    MutableClock,
    SequenceRandom,
    _catch_rolls,
    _database_with_catalog,
    _food_entry,
    _identity,
    _pig_entry,
)


@pytest.mark.asyncio
async def test_manual_reset_is_precise_audited_and_clears_window_cooldown(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    gameplay = GameplayService(
        database,
        CatchingSection(cooldown_seconds=60, daily_limit=2),
        random_source=SequenceRandom(
            *(
                roll
                for _ in range(5)
                for roll in _catch_rolls()
            )
        ),
        clock=clock,
    )
    await gameplay.catch(_identity(group_id="100", message_id="g100-1"))
    clock.value += timedelta(seconds=61)
    await gameplay.catch(_identity(group_id="100", message_id="g100-2"))
    clock.value += timedelta(seconds=61)
    await gameplay.catch(_identity(group_id="200", message_id="g200-1"))
    clock.value += timedelta(seconds=61)
    await gameplay.catch(_identity(group_id="200", message_id="g200-2"))

    reset = CatchQuotaResetService(
        database,
        refresh_hours=[0, 9, 12, 19],
        timezone_name="Asia/Shanghai",
        window_limit=2,
        clock=clock,
        id_factory=lambda: "reset-audit-1",
    )
    result = await reset.backup_and_reset_current_window(
        data_dir=database.path.parent,
        group_id="100",
        actor_user_id="test-admin",
        source="pytest",
    )
    assert result.scope_id == "qq:100"
    assert result.cleared_catches == 2
    assert result.affected_players == 1
    assert result.backup_path.is_file()
    assert result.backup_path.parent == (database.path.parent / "backups").resolve()

    after_reset = await gameplay.catch(
        _identity(group_id="100", message_id="g100-after-reset")
    )
    assert after_reset.daily_count == 1
    other_group = await gameplay.profile(
        _identity(group_id="200", message_id="g200-profile")
    )
    assert other_group.daily_count == 2
    assert other_group.cooldown_remaining_seconds == 60

    row = await database.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM command_receipts
             WHERE command_name = 'pig-catcher.catch') AS receipts,
            action,
            scope_id,
            actor_user_id
        FROM audit_events
        WHERE audit_event_id = 'reset-audit-1'
        """
    )
    assert row is not None
    assert tuple(row) == (
        5,
        "catch-quota-window-reset",
        "qq:100",
        "test-admin",
    )
    assert await database.integrity_check() == ("ok",)
    await database.close()


@pytest.mark.asyncio
async def test_manual_reset_rejects_unknown_group_before_backup(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    service = CatchQuotaResetService(
        database,
        refresh_hours=[0, 9, 12, 19],
        timezone_name="Asia/Shanghai",
        window_limit=5,
    )
    with pytest.raises(DomainValidationError, match="不存在群范围"):
        await service.backup_and_reset_current_window(
            data_dir=database.path.parent,
            group_id="not-created",
            actor_user_id="test-admin",
            source="pytest",
        )
    assert not (database.path.parent / "backups").exists()
    await database.close()


@pytest.mark.asyncio
async def test_sugar_ribs_reset_grants_atomic_group_rewards_and_dedicated_catches(
    tmp_path: Path,
) -> None:
    enhanced_params = {
        "count": 1,
        "five_star_multiplier": 1.007,
        "group_coin": 1007,
        "group_dedicated_catches": 10,
        "hidden_boost_chance_percent": 10,
        "hidden_five_star_multiplier": 10.04,
        "hidden_six_star_multiplier": 10.04,
        "six_star_multiplier": 1.007,
    }
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("one-pig", rarity=1),
            _pig_entry("five-pig", rarity=5),
            _pig_entry("six-pig", rarity=6, group_id="100"),
            _food_entry(
                "sugar-ribs-food",
                effect_id="quota-reset",
                effect_params=enhanced_params,
                display_name="糖醋排骨",
            ),
        ],
    )
    clock = MutableClock(datetime(2026, 8, 11, 4, 0, tzinfo=UTC))
    eater = _identity(user_id="1455722694", message_id="sugar-reset")
    other = _identity(user_id="OFFICIAL_OPEN_ID", message_id="other-profile")
    now = "2026-08-11T04:00:00.000Z"
    async with database.transaction() as session:
        framework = FrameworkRepository()
        await framework.touch_identity(session, identity=eater, now=now)
        await framework.touch_identity(session, identity=other, now=now)
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                portion_weight, fat_category, official_value, effect_id,
                effect_params_json, ruleset_version, random_snapshot_json,
                state, acquired_at, disposed_at, updated_at
            ) VALUES (
                'sugar-ribs-source', 'SUGARRIB', ?, ?, 'sugar-ribs-food', 1,
                6, '糖醋排骨', 1.0, 'balanced', 25000, 'quota-reset',
                '{"count":1,"five_star_multiplier":1.007,"group_coin":1007,"group_dedicated_catches":10,"hidden_boost_chance_percent":10,"hidden_five_star_multiplier":10.04,"hidden_six_star_multiplier":10.04,"six_star_multiplier":1.007}',
                18, '{}', 'consumed', ?, ?, ?
            )
            """,
            (eater.scope.value, eater.player_id, now, now, now),
        )
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                created_at, updated_at
            ) VALUES (
                'sugar-reset-chance', ?, 'sugar-ribs-source', 'quota-reset',
                '{"count":1,"five_star_multiplier":1.007,"group_coin":1007,"group_dedicated_catches":10,"hidden_boost_chance_percent":10,"hidden_five_star_multiplier":10.04,"hidden_six_star_multiplier":10.04,"six_star_multiplier":1.007}',
                1, 0, ?, ?
            )
            """,
            (eater.player_id, now, now),
        )

    reset = CatchQuotaResetService(
        database,
        refresh_hours=[0, 9, 12, 19],
        timezone_name="Asia/Shanghai",
        window_limit=1,
        clock=clock,
        id_factory=iter(
            (
                "sugar-reset-audit",
                "sugar-group-effect",
                "sugar-coin-eater",
                "sugar-coin-other",
            )
        ).__next__,
    )
    result = await reset.reset_from_quota_chance(
        data_dir=tmp_path,
        identity=eater,
    )
    duplicate = await reset.reset_from_quota_chance(
        data_dir=tmp_path,
        identity=eater,
    )
    assert result.group_rewarded_players == 2
    assert result.group_coin_reward == 1007
    assert result.group_dedicated_catches == 10
    assert result.hidden_boost_chance_percent == pytest.approx(10)
    assert result.hidden_five_star_multiplier == pytest.approx(10.04)
    assert result.hidden_six_star_multiplier == pytest.approx(10.04)
    assert result.group_effect_expires_at == "2026-08-12T04:00:00.000Z"
    assert duplicate.receipt_created is False
    balances = await database.fetch_all(
        "SELECT platform_user_id, coin_balance FROM players ORDER BY platform_user_id"
    )
    assert {row["platform_user_id"]: row["coin_balance"] for row in balances} == {
        "1455722694": 1007,
        "OFFICIAL_OPEN_ID": 1007,
    }
    group_effect = await database.fetch_one(
        """
        SELECT effect.effect_id, effect.granted_uses_per_player,
               effect.expires_at, source.platform_user_id AS source_user_id
        FROM group_food_effects AS effect
        JOIN players AS source ON source.player_id = effect.source_player_id
        WHERE effect.group_effect_entry_id = 'sugar-group-effect'
        """
    )
    assert group_effect is not None
    assert tuple(group_effect) == (
        "group-window-high-star-boost",
        10,
        "2026-08-12T04:00:00.000Z",
        "1455722694",
    )

    gameplay = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0, daily_limit=1),
        random_source=SequenceRandom(0.05, *_catch_rolls(), *_catch_rolls()),
        clock=clock,
        id_factory=iter(
            (
                "sugar-dedicated-pig",
                "sugar-dedicated-ledger",
                "sugar-expired-pig",
                "sugar-expired-ledger",
            )
        ).__next__,
        short_code_factory=iter(("SUGAR001", "SUGAR002")).__next__,
    )
    caught = await gameplay.catch(
        _identity(user_id="OFFICIAL_OPEN_ID", message_id="sugar-dedicated-catch")
    )
    assert caught.quota_exempt_catch is True
    assert caught.daily_count == 0
    assert any(
        "发动群友 ID：1455722694" in summary
        for summary in caught.effect_summaries
    )
    assert any("隐藏效果爆发" in summary for summary in caught.effect_summaries)
    assert caught.weights[4] > 4.0
    assert caught.weights[5] > 1.0
    usage = await database.fetch_one(
        "SELECT consumed_uses FROM group_food_effect_usage "
        "WHERE group_effect_entry_id = 'sugar-group-effect' AND player_id = ?",
        (other.player_id,),
    )
    assert usage is not None and usage["consumed_uses"] == 1
    clock.value = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
    expired = await gameplay.catch(
        _identity(user_id="OFFICIAL_OPEN_ID", message_id="sugar-expired-catch")
    )
    assert expired.quota_exempt_catch is False
    assert all("发动群友 ID" not in summary for summary in expired.effect_summaries)
    await database.close()
