"""Catch-quota window reset safety and group-isolation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.services import CatchQuotaResetService, GameplayService

from .test_gameplay import (
    MutableClock,
    SequenceRandom,
    _catch_rolls,
    _database_with_catalog,
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
