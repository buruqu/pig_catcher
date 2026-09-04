"""Schema 36 achievement registry, idempotency, rewards and rendering tests."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from pig_catcher.commands.context import extract_command_identity
from pig_catcher.domain.achievements import ACHIEVEMENT_DEFINITIONS
from pig_catcher.domain.models import CommandReceipt, ReceiptSendStatus
from pig_catcher.infrastructure.repositories import AchievementRepository
from pig_catcher.version import RULESET_VERSION, SCHEMA_VERSION

from .helpers import build_message, create_test_plugin
from .test_plugin import _command_kwargs, _install_test_pig


def test_v2_registry_contains_the_frozen_first_season_shape() -> None:
    ids = [item.achievement_id for item in ACHIEVEMENT_DEFINITIONS]
    assert len(ids) == len(set(ids)) == 130
    categories = Counter(item.category for item in ACHIEVEMENT_DEFINITIONS)
    assert (
        sum(
            categories[name]
            for name in (
                "捕猎历程",
                "高星猎手",
                "图鉴收藏",
                "料理品鉴",
                "巨物纪录",
                "成长经营",
                "社交展示",
            )
        )
        == 49
    )
    assert categories["联动印章"] == 11
    assert categories["隐藏彩蛋"] == 20
    assert categories["终极收藏"] == 2
    assert SCHEMA_VERSION == 62
    assert RULESET_VERSION == 60


@pytest.mark.asyncio
async def test_first_catch_unlock_is_atomic_rendered_and_idempotent(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={
            "features": {"achievements_enabled": True},
            "catching": {"cooldown_seconds": 0},
        },
    )
    await _install_test_pig(plugin, tmp_path)
    message = build_message(message_id="achievement-first-catch")
    first = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert first[0] is True
    assert len(context.send.images) == 2  # catch card + merged unlock card
    unlock = await plugin.database.fetch_one(
        """
        SELECT points_awarded, notification_status
        FROM achievement_unlocks
        WHERE player_id='qq:10001:20001' AND achievement_id='catch-total-1'
        """
    )
    assert unlock is not None
    assert int(unlock["points_awarded"]) == 5
    assert unlock["notification_status"] == "sent"
    reward = await plugin.database.fetch_one(
        """
        SELECT amount FROM currency_ledger
        WHERE idempotency_key='achievement:catch-total-1:qq:10001:20001:coin'
        """
    )
    assert reward is not None and int(reward["amount"]) == 200
    counter = await plugin.database.fetch_one(
        """
        SELECT metric_value FROM achievement_metric_counters
        WHERE player_id='qq:10001:20001' AND metric_key='ordinary_coins_earned'
        """
    )
    ledger_total = await plugin.database.fetch_one(
        """
        SELECT SUM(amount) AS total FROM currency_ledger
        WHERE player_id='qq:10001:20001' AND amount>0
          AND reason_code <> 'admin-coin-adjustment'
        """
    )
    assert counter is not None and ledger_total is not None
    assert int(counter["metric_value"]) == int(ledger_total["total"])

    duplicate = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    assert len(context.send.images) == 2
    count = await plugin.database.fetch_one(
        "SELECT COUNT(*) AS count FROM achievement_events WHERE player_id='qq:10001:20001'"
    )
    assert count is not None and int(count["count"]) == 1
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_achievement_pages_mask_hidden_entries_and_render(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"features": {"achievements_enabled": True}},
    )
    result = await plugin.handle_achievements(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="achievement-overview"), arguments=""),
    )
    assert result[0] is True
    assert len(context.send.images) == 1
    page = await plugin._achievement_service.page(
        __import__("pig_catcher.commands.context", fromlist=["extract_command_identity"]).extract_command_identity(
            "stream-10001", {"message": build_message(message_id="achievement-hidden")}
        ),
        category="隐藏彩蛋",
        page=1,
    )
    assert page.entries
    assert all(entry.name == "？？？" for entry in page.entries if not entry.unlocked)
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_schema35_has_all_critical_achievement_tables(tmp_path: Path) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    rows = await plugin.database.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'achievement_%'"
    )
    names = {str(row["name"]) for row in rows}
    assert {
        "achievement_definition_snapshots",
        "achievement_profiles",
        "achievement_progress",
        "achievement_events",
        "achievement_unlocks",
        "achievement_reward_inventory",
        "achievement_metric_counters",
        "achievement_scope_targets",
        "achievement_backfill_state",
        "achievement_milestone_claims",
        "achievement_operations",
        "achievement_ticket_effects",
    } <= names
    assert await plugin.database.fetch_one("PRAGMA quick_check") is not None
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_existing_player_backfill_is_atomic_summarized_and_idempotent(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"features": {"achievements_enabled": True}},
    )
    identity = extract_command_identity(
        "stream-10001",
        {"message": build_message(message_id="backfill-profile")},
    )
    await plugin.gameplay_service.profile(identity)
    async with plugin.database.transaction() as session:
        await session.execute(
            "UPDATE player_statistics SET total_catches=10 WHERE player_id=?",
            (identity.player_id,),
        )
        await session.execute(
            """
            INSERT INTO achievement_backfill_state(player_id, status, updated_at)
            VALUES (?, 'pending', '2026-08-26T00:00:00.000Z')
            """,
            (identity.player_id,),
        )

    overview = await plugin._achievement_service.overview(identity)
    assert overview.unlocked_count == 2
    assert overview.points == 10
    summary_claim = await plugin._achievement_service.claim_backfill_summary(player_id=identity.player_id)
    assert summary_claim is not None
    unlock_ids, summary = summary_claim
    assert len(unlock_ids) == summary.unlocked_count == 2
    assert summary.total_points == 10
    assert sum(reward.quantity for reward in summary.rewards if reward.reward_type == "coin") == 400
    await plugin._achievement_service.mark_notifications(unlock_ids, sent=True)

    second = await plugin._achievement_service.overview(identity)
    assert second.unlocked_count == 2
    assert await plugin._achievement_service.claim_backfill_summary(player_id=identity.player_id) is None
    event_count = await plugin.database.fetch_one(
        "SELECT COUNT(*) AS count FROM achievement_events WHERE event_type='historical-backfill'"
    )
    assert event_count is not None and int(event_count["count"]) == 1
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_achievement_catch_ticket_is_consumed_by_one_eligible_catch(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={
            "features": {"achievements_enabled": True},
            "catching": {"cooldown_seconds": 0},
        },
    )
    await _install_test_pig(plugin, tmp_path)
    identity = extract_command_identity(
        "stream-10001",
        {"message": build_message(message_id="activate-catch-ticket")},
    )
    await plugin.gameplay_service.profile(identity)
    repository = AchievementRepository()
    async with plugin.database.transaction() as session:
        await repository.ensure_profile(session, player_id=identity.player_id, now="2026-08-26T00:00:00.000Z")
        await repository.grant_reward(
            session,
            player_id=identity.player_id,
            reward_type="ticket",
            reward_id="achievement-catch",
            quantity=1,
            now="2026-08-26T00:00:00.000Z",
        )
    assert await plugin._achievement_service.activate_ticket(identity, "成就抓猪券") == "achievement-catch"

    caught = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="ticket-catch")),
    )
    assert caught[0] is True
    receipt = await plugin.database.fetch_one(
        """
        SELECT result_json FROM command_receipts
        WHERE player_id=? AND command_name='pig-catcher.catch'
        ORDER BY created_at DESC LIMIT 1
        """,
        (identity.player_id,),
    )
    assert receipt is not None
    assert json.loads(str(receipt["result_json"]))["quota_exempt_catch"] is True
    active = await plugin.database.fetch_one(
        """
        SELECT granted_uses, consumed_uses FROM achievement_ticket_effects
        WHERE player_id=? AND ticket_id='achievement-catch'
        """,
        (identity.player_id,),
    )
    assert active is not None and tuple(active) == (1, 1)
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_identifier_reforge_ticket_is_case_insensitive_and_idempotent(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path)
    identity = extract_command_identity(
        "stream-10001",
        {"message": build_message(message_id="reforge-source")},
    )
    await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="reforge-catch")),
    )
    pig = await plugin.database.fetch_one(
        "SELECT short_code FROM pig_instances WHERE owner_player_id=? AND state='active'",
        (identity.player_id,),
    )
    assert pig is not None
    repository = AchievementRepository()
    async with plugin.database.transaction() as session:
        await repository.ensure_profile(session, player_id=identity.player_id, now="2026-08-26T00:00:00.000Z")
        await repository.grant_reward(
            session,
            player_id=identity.player_id,
            reward_type="ticket",
            reward_id="identifier-reforge",
            quantity=1,
            now="2026-08-26T00:00:00.000Z",
        )
    old_code = str(pig["short_code"])
    changed = await plugin._achievement_service.reforge_identifier(
        identity,
        asset_kind="猪猪",
        old_code=old_code.lower(),
        new_code="PiGdream20",
    )
    assert changed == "PIGDREAM20"
    assert (
        await plugin._achievement_service.reforge_identifier(
            identity,
            asset_kind="猪猪",
            old_code=old_code,
            new_code="pigdream20",
        )
        == "PIGDREAM20"
    )
    stored = await plugin.database.fetch_one(
        "SELECT short_code FROM pig_instances WHERE owner_player_id=?",
        (identity.player_id,),
    )
    assert stored is not None and stored["short_code"] == "PIGDREAM20"
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_regular_completion_memorial_pig_is_a_functional_non_catch_reward(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    await _install_test_pig(plugin, tmp_path)
    identity = extract_command_identity(
        "stream-10001",
        {"message": build_message(message_id="memorial-claim")},
    )
    await plugin.gameplay_service.profile(identity)
    repository = AchievementRepository()
    async with plugin.database.transaction() as session:
        await session.execute("UPDATE pig_templates SET rarity=5 WHERE template_id='command-pig'")
        await repository.ensure_profile(session, player_id=identity.player_id, now="2026-08-26T00:00:00.000Z")
        await repository.grant_reward(
            session,
            player_id=identity.player_id,
            reward_type="chest",
            reward_id="regular-five-star-memorial",
            quantity=1,
            now="2026-08-26T00:00:00.000Z",
        )

    result = await plugin._achievement_service.claim_memorial_pig(identity, "命令测试猪")
    assert result.display_name == "命令测试猪"
    pig = await plugin.database.fetch_one(
        """
        SELECT rarity, random_snapshot_json FROM pig_instances
        WHERE owner_player_id=? AND short_code=?
        """,
        (identity.player_id, result.short_code),
    )
    assert pig is not None and int(pig["rarity"]) == 5
    assert json.loads(str(pig["random_snapshot_json"]))["source"] == "achievement-commemorative"
    stats = await plugin.database.fetch_one(
        "SELECT total_catches FROM player_statistics WHERE player_id=?",
        (identity.player_id,),
    )
    assert stats is not None and int(stats["total_catches"]) == 0
    duplicate = await plugin._achievement_service.claim_memorial_pig(identity, "命令测试猪")
    assert duplicate == result
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_runtime_star_counters_accumulate_without_historical_receipt_scans(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={
            "features": {"achievements_enabled": True},
            "catching": {"cooldown_seconds": 0},
        },
    )
    await _install_test_pig(plugin, tmp_path)
    async with plugin.database.transaction() as session:
        await session.execute("UPDATE pig_templates SET rarity=5 WHERE template_id='command-pig'")

    for index in range(2):
        caught = await plugin.handle_catch(
            stream_id="stream-10001",
            **_command_kwargs(build_message(message_id=f"five-star-counter-{index}")),
        )
        assert caught[0] is True

    progress = await plugin.database.fetch_one(
        """
        SELECT progress_value FROM achievement_progress
        WHERE player_id='qq:10001:20001' AND achievement_id='catch-five-star-20'
        """
    )
    assert progress is not None and int(progress["progress_value"]) == 2
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_set_progress_survives_unrelated_receipts_and_merges_new_values(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"features": {"achievements_enabled": True}},
    )
    identity = extract_command_identity(
        "stream-10001",
        {"message": build_message(message_id="roulette-profile")},
    )
    await plugin.gameplay_service.profile(identity)

    def receipt(receipt_id: str, result_type: str, payload: dict[str, object]) -> CommandReceipt:
        return CommandReceipt(
            receipt_id=receipt_id,
            idempotency_key=receipt_id,
            scope_id=identity.scope.value,
            player_id=identity.player_id,
            command_name=f"test.{result_type}",
            request_fingerprint=receipt_id,
            result_type=result_type,
            result_object_id="",
            result_json=json.dumps(payload),
            text_summary="",
            send_status=ReceiptSendStatus.PENDING,
            created_at="2026-08-26T00:00:00.000Z",
            updated_at="2026-08-26T00:00:00.000Z",
        )

    await plugin._achievement_service.process_receipt(receipt("roulette-one", "roulette-spin", {"outcome": 1}))
    await plugin._achievement_service.process_receipt(receipt("unrelated", "profile", {}))
    await plugin._achievement_service.process_receipt(receipt("roulette-two", "roulette-spin", {"outcome": 2}))

    progress = await plugin.database.fetch_one(
        """
        SELECT progress_value, state_json FROM achievement_progress
        WHERE player_id=? AND achievement_id='hidden-roulette-all-faces'
        """,
        (identity.player_id,),
    )
    assert progress is not None and int(progress["progress_value"]) == 2
    assert json.loads(str(progress["state_json"]))["items"] == ["1", "2"]
    await plugin.on_unload()
