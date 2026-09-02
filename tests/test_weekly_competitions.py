"""First PiG Dream! weekly competition season and settlement tests."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.weekly_competitions import (
    WEEKLY_COMPETITION_DEFINITIONS,
    WEEKLY_SPRINT_BADGE_IDS,
    WEEKLY_SPRINT_FRAME_ID,
    WEEKLY_SPRINT_TITLE_ID,
    WeeklyAggregation,
)
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.services import FrameworkService, WeeklyCompetitionService, beijing_week_window

from .helpers import build_message, create_plugin, create_test_plugin
from .test_plugin import _command_kwargs, _install_test_pig


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _identity(
    user_id: str,
    display_name: str,
    *,
    group_id: str = "weekly-group",
    message_id: str = "weekly-message",
) -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", group_id),
        stream_id=f"stream-{group_id}",
        user_id=user_id,
        display_name=display_name,
        message_id=message_id,
        group_name=f"周榜群-{group_id}",
    )


async def _seed_catch(
    database: PigCatcherDatabase,
    identity: CommandIdentity,
    *,
    serial: int,
    value: int,
    occurred_at: str,
    state: str = "active",
) -> None:
    await FrameworkService(database).touch_identity(identity)
    catalog_hash = "a" * 64
    pig_id = f"weekly-pig-{serial}"
    receipt_id = f"weekly-receipt-{serial}"
    short_code = f"W{serial:07d}"
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT OR IGNORE INTO asset_manifest_imports(
                catalog_hash, catalog_id, manifest_version, source_label,
                storage_relpath, entry_count, status, created_at
            ) VALUES (?, 'weekly-tests', 4, 'pytest', 'assets/test', 1, 'active', ?)
            """,
            (catalog_hash, occurred_at),
        )
        await session.execute(
            """
            INSERT OR IGNORE INTO pig_templates(
                template_id, catalog_hash, template_version, display_name,
                rarity, scope_type, description, image_relpath, image_sha256,
                image_fit, length_min, length_max, weight_min, weight_max,
                fat_profile, recipe_tags_json, source_label, license,
                consent_status, enabled, created_at, updated_at
            ) VALUES (
                'weekly-test-pig', ?, 1, '周榜测试猪', 4, 'common', 'pytest',
                'assets/test/pig.png', ?, 'contain', 30, 80, 20, 180,
                'balanced', '[]', 'pytest', 'test-only', 'not-required', 1, ?, ?
            )
            """,
            (catalog_hash, "b" * 64, occurred_at, occurred_at),
        )
        await session.execute(
            """
            INSERT INTO pig_instances(
                pig_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                size_value, size_percentile, weight_value, weight_percentile,
                fat_ratio, official_value, ruleset_version, random_snapshot_json,
                state, acquired_at, disposed_at, updated_at
            ) VALUES (?, ?, ?, ?, 'weekly-test-pig', 1, 4, '周榜测试猪',
                      60, 0.5, 100, 0.5, 40, ?, 31, '{}', ?, ?, ?, ?)
            """,
            (
                pig_id,
                short_code,
                identity.scope.value,
                identity.player_id,
                value,
                state,
                occurred_at,
                occurred_at if state != "active" else None,
                occurred_at,
            ),
        )
        await session.execute(
            """
            INSERT INTO command_receipts(
                receipt_id, idempotency_key, scope_id, player_id, command_name,
                request_fingerprint, result_type, result_object_id, result_json,
                text_summary, catch_quota_cost, send_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pig-catcher.catch', ?, 'pig', ?, '{}',
                      '抓猪成功', 1, 'sent', ?, ?)
            """,
            (
                receipt_id,
                f"weekly-key-{serial}",
                identity.scope.value,
                identity.player_id,
                f"fingerprint-{serial}",
                pig_id,
                occurred_at,
                occurred_at,
            ),
        )


def test_first_weekly_definition_is_data_driven_and_complete() -> None:
    assert len(WEEKLY_COMPETITION_DEFINITIONS) == 1
    definition = WEEKLY_COMPETITION_DEFINITIONS[0]
    assert definition.season_number == 1
    assert definition.name == "抓猪冲刺！！！"
    assert definition.aggregation is WeeklyAggregation.SUM
    assert definition.source_field == "official_value"
    assert definition.source_command_names == ("pig-catcher.catch",)
    assert tuple(rank for tier in definition.reward_tiers for rank in tier.ranks) == tuple(range(1, 11))
    assert definition.rewards_for_rank(11) == ()


def test_beijing_week_window_uses_monday_boundary() -> None:
    start, end = beijing_week_window(datetime(2026, 8, 26, 4, 30, tzinfo=UTC))
    assert start == datetime(2026, 8, 23, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 30, 16, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_backfill_ranking_scope_isolation_ties_and_disposed_assets(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "weekly.sqlite3")
    await database.open()
    clock = MutableClock(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    alice = _identity("alice", "爱丽丝")
    bob = _identity("bob", "波布")
    carol = _identity("carol", "卡萝")
    other_scope = _identity("alice", "另一个爱丽丝", group_id="other-group")
    await _seed_catch(database, alice, serial=1, value=60, occurred_at="2026-09-01T01:00:00.000Z")
    await _seed_catch(
        database,
        alice,
        serial=2,
        value=40,
        occurred_at="2026-09-01T02:00:00.000Z",
        state="sold",
    )
    await _seed_catch(database, bob, serial=3, value=100, occurred_at="2026-09-01T03:00:00.000Z")
    await _seed_catch(database, carol, serial=4, value=100, occurred_at="2026-09-01T04:00:00.000Z")
    await _seed_catch(database, other_scope, serial=5, value=999, occurred_at="2026-09-01T05:00:00.000Z")

    service = WeeklyCompetitionService(database, clock=clock)
    await service.initialize()
    page = await service.leaderboard(alice)
    assert [(item.display_name, item.score, item.catch_count) for item in page.entries] == [
        ("波布", 100.0, 1),
        ("卡萝", 100.0, 1),
        ("爱丽丝", 100.0, 2),
    ]
    assert page.player_rank == 3
    assert page.total_count == 3
    other = await service.leaderboard(other_scope)
    assert other.total_count == 1 and other.entries[0].score == 999

    # Reprocessing and full backfill are both idempotent.
    await service.initialize()
    count = await database.fetch_one("SELECT COUNT(*) AS count FROM weekly_competition_entries")
    assert count is not None and int(count["count"]) == 5
    await database.close()


@pytest.mark.asyncio
async def test_settlement_rewards_top_ten_once_and_event_cosmetics_can_be_equipped(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "settlement.sqlite3")
    await database.open()
    clock = MutableClock(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    players = [_identity(f"player-{rank}", f"玩家{rank}") for rank in range(1, 12)]
    for rank, identity in enumerate(players, start=1):
        await _seed_catch(
            database,
            identity,
            serial=100 + rank,
            value=1200 - rank * 10,
            occurred_at=f"2026-09-01T{rank:02d}:00:00.000Z",
        )
    service = WeeklyCompetitionService(database, clock=clock)
    await service.initialize()

    clock.value = datetime(2026, 9, 7, 16, 1, tzinfo=UTC)
    settled = await service.leaderboard(players[0])
    assert settled.status == "settled"
    awards = await database.fetch_all(
        "SELECT final_rank, player_id FROM weekly_competition_awards ORDER BY final_rank"
    )
    assert len(awards) == 10
    assert [int(row["final_rank"]) for row in awards] == list(range(1, 11))
    assert all(str(row["player_id"]) != players[-1].player_id for row in awards)

    winner = players[0]
    winner_row = await database.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (winner.player_id,))
    assert winner_row is not None and int(winner_row["coin_balance"]) == 10_000
    rewards = await database.fetch_all(
        """
        SELECT reward_type, reward_id, quantity
        FROM achievement_reward_inventory
        WHERE player_id=?
        ORDER BY reward_type, reward_id
        """,
        (winner.player_id,),
    )
    reward_map = {(str(row["reward_type"]), str(row["reward_id"])): int(row["quantity"]) for row in rewards}
    assert reward_map[("title", WEEKLY_SPRINT_TITLE_ID)] == 1
    assert reward_map[("frame", WEEKLY_SPRINT_FRAME_ID)] == 1
    assert reward_map[("badge", WEEKLY_SPRINT_BADGE_IDS[1])] == 1
    assert reward_map[("ticket", "achievement-catch")] == 5
    assert reward_map[("ticket", "achievement-firework")] == 2

    await service.initialize()
    ledger_count = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM currency_ledger WHERE reason_code='weekly-competition-reward'"
    )
    assert ledger_count is not None and int(ledger_count["count"]) == 10

    award = await service.claim_pending_award(player_id=winner.player_id)
    assert award is not None and award.final_rank == 1
    assert await service.claim_pending_award(player_id=winner.player_id) is None
    clock.value += timedelta(minutes=11)
    recovered_award = await service.claim_pending_award(player_id=winner.player_id)
    assert recovered_award is not None and recovered_award.award_id == award.award_id
    assert await service.mark_award_notification(recovered_award.award_id, sent=False, error="临时发送失败")
    retried_award = await service.claim_pending_award(player_id=winner.player_id)
    assert retried_award is not None and retried_award.award_id == award.award_id
    assert await service.mark_award_notification(retried_award.award_id, sent=True)
    assert await service.claim_pending_award(player_id=winner.player_id) is None

    equipped = await service.equip_competition_cosmetics(winner, "抓猪冲刺！！！")
    assert equipped is not None and "抓猪冲刺者" in equipped[0]
    profile = await database.fetch_one(
        """
        SELECT equipped_title_id, equipped_frame_id, showcase_achievement_id
        FROM achievement_profiles WHERE player_id=?
        """,
        (winner.player_id,),
    )
    assert profile is not None
    assert profile["equipped_title_id"] == WEEKLY_SPRINT_TITLE_ID
    assert profile["equipped_frame_id"] == WEEKLY_SPRINT_FRAME_ID
    assert profile["showcase_achievement_id"] == WEEKLY_SPRINT_BADGE_IDS[1]
    await database.close()


@pytest.mark.asyncio
async def test_plugin_scores_catch_and_renders_weekly_command_alias(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={
            "features": {"weekly_competitions_enabled": True},
            "catching": {"cooldown_seconds": 0},
        },
    )
    await _install_test_pig(plugin, tmp_path)
    launch_clock = MutableClock(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    plugin._weekly_competition_service.clock = launch_clock
    plugin._gameplay_service.clock = launch_clock
    message = build_message(message_id="weekly-plugin-catch")
    caught = await plugin.handle_catch(stream_id="stream-10001", **_command_kwargs(message))
    assert caught[0] is True
    entry = await plugin.database.fetch_one("SELECT metric_value FROM weekly_competition_entries")
    assert entry is not None and float(entry["metric_value"]) > 0

    query_message = build_message(message_id="weekly-plugin-query")
    shown = await plugin.handle_weekly_competition(
        stream_id="stream-10001",
        matched_groups={"arguments": None},
        raw_message="/zzx",
        message=query_message,
    )
    assert shown[0] is True
    html = context.render.calls[-1][0]
    assert "抓猪冲刺！！！" in html
    assert "本周抓猪累计官方价值" in html
    assert "测试成员" in html
    await plugin.on_unload()


def test_weekly_command_pattern_accepts_page_and_rejects_extra_text() -> None:
    components = {item["name"]: item for item in create_plugin().get_components()}
    pattern = components["pig_catcher_weekly_competition"]["metadata"]["command_pattern"]
    assert re.search(pattern, "/抓猪线")
    assert re.search(pattern, "/抓猪线 2")
    assert re.search(pattern, "/zzx 3")
    assert re.search(pattern, "/抓猪线 本周") is None
