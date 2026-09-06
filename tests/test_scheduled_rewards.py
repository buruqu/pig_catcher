"""Birthday reward cutoff, authorization, transactional grants and stable memorial labels."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.domain.models import ScopeKey
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.services.command_state import iso_timestamp
from pig_catcher.services.gameplay import pig_view_from_row
from pig_catcher.services.scheduled_rewards import BIRTHDAY_AT, ScheduledRewardService

from .test_admin_commands import _identity
from .test_gameplay import MutableClock, _database_with_catalog, _food_entry, _pig_entry


@pytest.fixture
async def world(tmp_path):
    db = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("birthday-pig", rarity=5, display_name="撅撅猪"),
            _food_entry(
                "birthday-food", rarity=5, group_id=None, effect_id="", effect_params={}, display_name="撅撅猪派"
            ),
        ],
    )
    clock = MutableClock(BIRTHDAY_AT - timedelta(hours=1))
    service = ScheduledRewardService(db, clock=clock)
    scopes = ("qq:10001", "qq:10002", "qq-official:official1", "qq-official:official2")
    async with db.transaction() as session:
        for scope in scopes:
            for user in ("one", "two"):
                identity = replace(_identity(user_id=user, display_name="群友"), scope=ScopeKey.parse(scope))
                await FrameworkRepository().touch_identity(session, identity=identity, now=iso_timestamp(clock.value))
    try:
        yield db, service, clock, list(scopes)
    finally:
        await db.close()


async def test_birthday_no_early_grants_cutoff_once_all_worlds(world):
    db, service, clock, scopes = world
    with pytest.raises(DomainValidationError):
        await service.schedule_birthday(scopes, numbering_confirmed=False)
    assert (await service.schedule_birthday(scopes, numbering_confirmed=True))["created"]
    assert not (await service.schedule_birthday(scopes, numbering_confirmed=True))["created"]
    assert await service.process_due() == 0
    assert (await db.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == 0
    clock.value = BIRTHDAY_AT + timedelta(hours=2)
    async with db.transaction() as session:
        late = _identity(user_id="late", display_name="晚到群友")
        await FrameworkRepository().touch_identity(session, identity=late, now=iso_timestamp(clock.value))
    assert sum(await asyncio.gather(service.process_due(), service.process_due())) == 8
    assert await service.process_due() == 0
    assert (await db.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == 8 * 9600
    assert (await db.fetch_one("SELECT coin_balance FROM players WHERE platform_user_id='late'"))[0] == 0
    assert (await db.fetch_one("SELECT COUNT(*) FROM pig_instances WHERE commemorative_code='20260906'"))[0] == 8
    assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 8
    assert (await db.fetch_one("SELECT SUM(quantity) FROM achievement_reward_inventory"))[0] == 16
    assert (
        await db.fetch_one(
            "SELECT COUNT(DISTINCT short_code) FROM (SELECT short_code FROM pig_instances "
            "UNION ALL SELECT short_code FROM food_instances)"
        )
    )[0] == 16
    assert (await db.fetch_one("SELECT SUM(total_catches+total_cooks) FROM player_statistics"))[0] == 0
    assert len(await service.pending_notices()) == 4
    for _, result in await service.pending_notices():
        assert "2 人" in result.view.text() and "20260906" in result.view.text()
    row = await db.fetch_one("SELECT * FROM pig_instances LIMIT 1")
    assert "生日纪念 20260906" in pig_view_from_row(dict(row)).display_tags
    assert not await db.fetch_all("PRAGMA foreign_key_check")
    assert (await db.fetch_one("PRAGMA integrity_check"))[0] == "ok"


async def test_campaign_payload_conflicts_and_missing_template_fail_closed(world):
    db, service, clock, scopes = world
    with pytest.raises(DomainValidationError):
        await service.schedule_birthday(["qq:unknown"], numbering_confirmed=True)
    await service.schedule_birthday(scopes, numbering_confirmed=True)
    with pytest.raises(DomainValidationError):
        await service.schedule_birthday(scopes[:1], numbering_confirmed=True)
    async with db.transaction() as session:
        await session.execute("UPDATE food_templates SET enabled=0")
    clock.value = BIRTHDAY_AT
    with pytest.raises(DomainValidationError):
        await service.process_due()
    assert (await db.fetch_one("SELECT COUNT(*) FROM scheduled_reward_grants"))[0] == 0


async def test_batch_failure_rolls_back_one_group_and_retry(world, monkeypatch):
    db, service, clock, scopes = world
    await service.schedule_birthday(scopes, numbering_confirmed=True)
    clock.value = BIRTHDAY_AT
    real = service.admin._insert_granted_asset
    calls = 0

    async def fail_fourth(*args, **kwargs):
        nonlocal calls
        calls += 1
        value = await real(*args, **kwargs)
        if calls == 4:
            raise RuntimeError("second player's food insert failed")
        return value

    monkeypatch.setattr(service.admin, "_insert_granted_asset", fail_fourth)
    with pytest.raises(RuntimeError):
        await service.process_due()
    # The failed group rolled back; the other three groups completed independently.
    assert (await db.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == 6 * 9600
    assert (await db.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 6
    assert (await db.fetch_one("SELECT COUNT(*) FROM reward_coupon_grants"))[0] == 12
    monkeypatch.setattr(service.admin, "_insert_granted_asset", real)
    assert await service.process_due() == 2
    await db.close()
    await db.open()
    assert await ScheduledRewardService(db, clock=clock).process_due() == 0
    results = await db.fetch_all("SELECT result_json FROM scheduled_reward_grants")
    assert all(json.loads(row[0])["coins"] == 9600 for row in results)


async def test_native_runner_sends_each_group_once_and_stops(world):
    import logging

    from pig_catcher.services.receipts import ReceiptService
    from pig_catcher.services.red_packets import RedPacketService
    from pig_catcher.services.social_rewards_runner import SocialRewardsRunner

    db, service, clock, scopes = world
    await service.schedule_birthday(scopes, numbering_confirmed=True)
    clock.value = BIRTHDAY_AT
    sent = []
    receipts = ReceiptService(db, clock=clock)

    async def deliver(stream, result):
        if await receipts.claim_send(result.receipt.receipt_id):
            sent.append((stream, result.view.title))
            await receipts.mark_sent(result.receipt.receipt_id)

    runner = SocialRewardsRunner(
        RedPacketService(db, clock=clock),
        service,
        logger=logging.getLogger("offline-social-rewards"),
        deliver=deliver,
        interval=0.01,
    )
    await runner.tick()
    await runner.tick()
    runner.start()
    await asyncio.sleep(0.03)
    await runner.stop()
    assert runner.task is None
    assert len(sent) == 4
    assert not await service.pending_notices()


async def test_empty_scope_has_no_duplicate_notice(world):
    db, service, clock, scopes = world
    async with db.transaction() as session:
        await FrameworkRepository().ensure_scope(
            session,
            scope=ScopeKey("qq", "empty"),
            group_name="空数字入口",
            stream_id="empty-stream",
            now=iso_timestamp(clock.now()),
        )
    await service.schedule_birthday([*scopes, "qq:empty"], numbering_confirmed=True)
    clock.value = BIRTHDAY_AT
    assert await service.process_due() == 8
    assert len(await service.pending_notices()) == 4
    assert (await db.fetch_one("SELECT state FROM scheduled_reward_campaigns"))[0] == "complete"


async def test_stamped_database_missing_campaign_table_is_rejected(world):
    import sqlite3

    from pig_catcher.domain.errors import MigrationError
    from pig_catcher.infrastructure.database import PigCatcherDatabase

    db, service, clock, scopes = world
    await db.close()
    with sqlite3.connect(db.path) as conn:
        conn.execute("DROP TABLE scheduled_reward_grants")
    with pytest.raises(MigrationError, match="scheduled_reward_grants"):
        await PigCatcherDatabase(db.path).open()
