"""Automatic gift/trade regulation graph and transactional escalation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from pig_catcher.config.model import RankingSection, RegulationSection, TradingSection
from pig_catcher.domain.enums import AssetKind, TradeStatus
from pig_catcher.domain.regulation import (
    TransferSignal,
    analyze_transfer_graph,
    classify_account_activity,
    relax_established_mutual_gifts,
)
from pig_catcher.infrastructure.repositories import FrameworkRepository
from pig_catcher.services import RegulationService, SocialService
from pig_catcher.services.command_state import iso_timestamp
from tests.test_social import (
    MutableClock,
    _catch_many,
    _database_with_social_catalog,
    _grant_coins,
    _identity,
)


def _signal(
    event_id: str,
    sender: str,
    recipient: str,
    *,
    asset_key: str | None = None,
    channel: str = "asset",
    transfer_type: str = "gift",
    price: int | None = None,
    official_value: int = 100,
) -> TransferSignal:
    return TransferSignal(
        event_id=event_id,
        from_player_id=sender,
        to_player_id=recipient,
        asset_key=asset_key or f"unit:{event_id}",
        channel=channel,
        transfer_type=transfer_type,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        rarity=2,
        official_value=official_value,
        price=price,
    )


def test_graph_exempts_one_feeder_but_detects_funnels_and_chains() -> None:
    assert analyze_transfer_graph(
        (_signal("one", "small", "main"),),
        anchor_event_ids=frozenset({"one"}),
    ) is None

    funnel = analyze_transfer_graph(
        (
            _signal("a1", "small-a", "main"),
            _signal("b1", "small-b", "main"),
            _signal("a2", "small-a", "main"),
            _signal("b2", "small-b", "main"),
        ),
        anchor_event_ids=frozenset({"b2"}),
    )
    assert funnel is not None
    assert funnel.upstream_player_ids == ("small-a", "small-b")
    assert funnel.target_player_ids == ("main",)

    chain = analyze_transfer_graph(
        (
            _signal("c1", "small-a", "relay", asset_key="pig:a1"),
            _signal("c2", "small-b", "relay", asset_key="pig:b1"),
            _signal("c3", "small-a", "relay", asset_key="pig:a2"),
            _signal("c4", "small-b", "relay", asset_key="pig:b2"),
            _signal("c5", "relay", "main", asset_key="pig:a1"),
            _signal("c6", "relay", "main", asset_key="pig:b1"),
            _signal("c7", "relay", "main", asset_key="pig:a2"),
            _signal("c8", "relay", "main", asset_key="pig:b2"),
        ),
        anchor_event_ids=frozenset({"c8"}),
    )
    assert chain is not None
    assert chain.source_player_ids == ("small-a", "small-b")
    assert chain.relay_player_ids == ("relay",)
    assert chain.max_path_depth == 2


def test_account_activity_classification_requires_complete_evidence() -> None:
    common = {
        "established_min_messages": 30,
        "established_min_active_days": 7,
        "likely_alt_max_messages": 5,
        "likely_alt_max_active_days": 2,
        "likely_alt_max_plugin_age_days": 7,
        "likely_alt_max_game_actions": 10,
    }
    established = classify_account_activity(
        player_id="main",
        chat_history_available=True,
        chat_message_count=30,
        chat_active_day_count=7,
        plugin_age_days=1,
        game_action_count=0,
        **common,
    )
    likely_alt = classify_account_activity(
        player_id="alt",
        chat_history_available=True,
        chat_message_count=5,
        chat_active_day_count=2,
        plugin_age_days=7,
        game_action_count=10,
        **common,
    )
    unavailable = classify_account_activity(
        player_id="unknown",
        chat_history_available=False,
        chat_message_count=0,
        chat_active_day_count=0,
        plugin_age_days=0,
        game_action_count=0,
        **common,
    )
    assert established.tier == "established"
    assert likely_alt.tier == "likely-alt"
    assert unavailable.tier == "unknown"


def test_established_reciprocal_gifts_are_filtered_but_one_way_gifts_remain() -> None:
    signals = (
        _signal("a-to-b", "a", "b"),
        _signal("b-to-a", "b", "a"),
        _signal("c-to-b", "c", "b"),
    )
    remaining, relaxed_ids = relax_established_mutual_gifts(
        signals,
        established_player_ids=frozenset({"a", "b", "c"}),
    )
    assert relaxed_ids == ("a-to-b", "b-to-a")
    assert tuple(signal.event_id for signal in remaining) == ("c-to-b",)


def test_two_one_shot_sources_require_likely_alt_evidence() -> None:
    signals = (
        _signal("a-to-main", "small-a", "main"),
        _signal("b-to-main", "small-b", "main"),
    )
    assert analyze_transfer_graph(
        signals,
        anchor_event_ids=frozenset({"b-to-main"}),
    ) is None
    strict = analyze_transfer_graph(
        signals,
        anchor_event_ids=frozenset({"b-to-main"}),
        strict_player_ids=frozenset({"small-a", "small-b"}),
    )
    assert strict is not None
    assert strict.strict_source_count == 2


def test_graph_does_not_join_unrelated_assets_through_same_relay() -> None:
    signals = (
        _signal("in-a1", "small-a", "relay", asset_key="pig:a1"),
        _signal("in-a2", "small-a", "relay", asset_key="pig:a2"),
        _signal("in-b1", "small-b", "relay", asset_key="pig:b1"),
        _signal("in-b2", "small-b", "relay", asset_key="pig:b2"),
        _signal("out-1", "relay", "main", asset_key="pig:other-1"),
        _signal("out-2", "relay", "main", asset_key="pig:other-2"),
        _signal("out-3", "relay", "main", asset_key="pig:other-3"),
        _signal("out-4", "relay", "main", asset_key="pig:other-4"),
    )
    assert analyze_transfer_graph(
        signals,
        anchor_event_ids=frozenset({"out-4"}),
    ) is None


def test_graph_detects_two_sinks_only_when_they_share_sources() -> None:
    split_funnel = (
        _signal("s-a1", "small-a", "main-1"),
        _signal("s-a2", "small-a", "main-2"),
        _signal("s-b1", "small-b", "main-1"),
        _signal("s-b2", "small-b", "main-2"),
    )
    analysis = analyze_transfer_graph(
        split_funnel,
        anchor_event_ids=frozenset({"s-b2"}),
    )
    assert analysis is not None
    assert analysis.target_player_ids == ("main-1", "main-2")
    assert analysis.source_player_ids == ("small-a", "small-b")

    unrelated_sinks = (
        _signal("u-a1", "small-a", "main-1"),
        _signal("u-a2", "small-a", "main-1"),
        _signal("u-b1", "small-b", "main-2"),
        _signal("u-b2", "small-b", "main-2"),
    )
    assert analyze_transfer_graph(
        unrelated_sinks,
        anchor_event_ids=frozenset({"u-b2"}),
    ) is None


def test_graph_detects_overpriced_trade_coin_funnel() -> None:
    signals = (
        _signal(
            "trade-a:coin",
            "buyer-a",
            "seller",
            channel="coin",
            transfer_type="trade",
            price=400,
        ),
        _signal(
            "trade-b:coin",
            "buyer-b",
            "seller",
            channel="coin",
            transfer_type="trade",
            price=400,
        ),
        _signal(
            "trade-c:coin",
            "buyer-c",
            "seller",
            channel="coin",
            transfer_type="trade",
            price=400,
        ),
    )
    analysis = analyze_transfer_graph(
        signals,
        anchor_event_ids=frozenset({"trade-c:coin"}),
    )
    assert analysis is not None
    assert analysis.target_player_ids == ("seller",)
    assert analysis.price_anomaly_level == "severe"


def test_graph_does_not_punish_two_normal_price_trades_alone() -> None:
    signals = (
        _signal(
            "fair-a",
            "seller-a",
            "buyer",
            transfer_type="trade",
            price=100,
        ),
        _signal(
            "fair-b",
            "seller-b",
            "buyer",
            transfer_type="trade",
            price=100,
        ),
    )
    assert analyze_transfer_graph(
        signals,
        anchor_event_ids=frozenset({"fair-b"}),
    ) is None


async def _seed_two_source_assets(tmp_path: Path) -> tuple[object, MutableClock, list[object]]:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    assets = await _catch_many(database, clock, count=5, user_id="source-a")
    extra_instance_id = "regulation-extra-pig"
    extra_short_code = "REGX0001"
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO pig_instances(
                pig_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                size_value, size_percentile, weight_value, weight_percentile,
                fat_ratio, official_value, ruleset_version, random_snapshot_json,
                state, locked_trade_id, acquired_at, disposed_at, updated_at,
                display_variant
            )
            SELECT ?, ?, scope_id, owner_player_id,
                   template_id, template_version, rarity, display_name_snapshot,
                   size_value, size_percentile, weight_value, weight_percentile,
                   fat_ratio, official_value, ruleset_version, random_snapshot_json,
                   'active', NULL, acquired_at, NULL, updated_at, display_variant
            FROM pig_instances
            WHERE pig_instance_id = ?
            """,
            (extra_instance_id, extra_short_code, assets[2].pig.pig_instance_id),
        )
    assets.append(
        SimpleNamespace(
            pig=replace(
                assets[2].pig,
                pig_instance_id=extra_instance_id,
                short_code=extra_short_code,
            )
        )
    )
    source_b = _identity(user_id="source-b", message_id="seed-source-b")
    async with database.transaction() as session:
        await FrameworkRepository().touch_identity(
            session,
            identity=source_b,
            now=iso_timestamp(clock.now()),
        )
        for item in assets[4:]:
            await session.execute(
                """
                UPDATE pig_instances
                SET owner_player_id = ?, updated_at = ?
                WHERE pig_instance_id = ?
                """,
                (source_b.player_id, iso_timestamp(clock.now()), item.pig.pig_instance_id),
            )
    return database, clock, assets


async def _mark_all_notices_sent(
    regulation: RegulationService,
    notice_ids: tuple[str, ...],
) -> None:
    for notice_id in notice_ids:
        notice = await regulation.claim_notice(notice_id)
        assert notice is not None
        assert "风险分" not in notice.message_text
        assert "阈值" not in notice.message_text
        assert await regulation.mark_notice_sent(notice_id) is True


def _active_chat_provider(clock: MutableClock, *user_ids: str):
    messages: list[dict[str, object]] = []
    for day_offset in range(7):
        for user_id in user_ids:
            for message_index in range(5):
                timestamp = clock.now() - timedelta(
                    days=day_offset,
                    minutes=message_index + 1,
                )
                messages.append(
                    {
                        "message_id": f"{user_id}-{day_offset}-{message_index}",
                        "timestamp": str(timestamp.timestamp()),
                        "platform": "qq",
                        "message_info": {
                            "user_info": {"user_id": user_id},
                            "group_info": {"group_id": "100"},
                        },
                        "is_command": False,
                        "is_notify": False,
                    }
                )

    async def provider(
        chat_id: str,
        start_time: float,
        end_time: float,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        assert chat_id == "stream-100"
        assert start_time < end_time
        return tuple(messages[-limit:])

    return provider


@pytest.mark.asyncio
async def test_established_players_reciprocal_gifts_do_not_open_case(
    tmp_path: Path,
) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)
    regulation = RegulationService(
        database,
        RegulationSection(enabled_scope_ids=["qq:100"]),
        clock=clock,
        chat_message_provider=_active_chat_provider(
            clock,
            "source-a",
            "source-b",
            "main",
        ),
    )
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
    )
    operations = (
        ("source-a", "main", assets[0].pig.selector, "mutual-1"),
        ("main", "source-a", assets[0].pig.selector, "mutual-2"),
        ("source-b", "main", assets[4].pig.selector, "mutual-3"),
        ("main", "source-b", assets[4].pig.selector, "mutual-4"),
        ("source-a", "main", assets[0].pig.selector, "mutual-5"),
        ("source-b", "main", assets[4].pig.selector, "mutual-6"),
    )
    for sender, recipient, selector, message_id in operations:
        result = await social.gift(
            _identity(user_id=sender, message_id=message_id),
            _identity(user_id=recipient, message_id=f"target-{message_id}"),
            asset_kind=AssetKind.PIG,
            selector_text=selector,
        )
        assert result.regulation is None
    count = await database.fetch_one("SELECT COUNT(*) AS count FROM anti_abuse_cases")
    assert count is not None and int(count["count"]) == 0
    await database.close()


@pytest.mark.asyncio
async def test_two_low_activity_new_sources_are_checked_strictly(
    tmp_path: Path,
) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)

    async def empty_provider(
        chat_id: str,
        start_time: float,
        end_time: float,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        return ()

    regulation = RegulationService(
        database,
        RegulationSection(enabled_scope_ids=["qq:100"]),
        clock=clock,
        chat_message_provider=empty_provider,
    )
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
    )
    first = await social.gift(
        _identity(user_id="source-a", message_id="strict-a"),
        _identity(user_id="main", message_id="strict-target-a"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[0].pig.selector,
    )
    assert first.regulation is None
    second = await social.gift(
        _identity(user_id="source-b", message_id="strict-b"),
        _identity(user_id="main", message_id="strict-target-b"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[4].pig.selector,
    )
    assert second.regulation is not None
    assert second.regulation.stage == "warning"
    case = await database.fetch_one(
        "SELECT evidence_json FROM anti_abuse_cases WHERE case_id = ?",
        (second.regulation.case_id,),
    )
    assert case is not None
    assert '"strict_source_count": 2' in str(case["evidence_json"])
    await database.close()


@pytest.mark.asyncio
async def test_gift_regulation_warns_then_blocks_and_escalates_idempotently(
    tmp_path: Path,
) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)
    regulation = RegulationService(
        database,
        RegulationSection(
            enabled_scope_ids=["qq:100"],
            notice_cooldown_minutes=1,
            social_hold_hours=1,
        ),
        clock=clock,
    )
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
    )
    def target(message_id: str):
        return _identity(user_id="main", message_id=message_id)

    first = await social.gift(
        _identity(user_id="source-a", message_id="gift-a-1"),
        target("target-1"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[0].pig.selector,
    )
    assert first.regulation is None

    second = await social.gift(
        _identity(user_id="source-b", message_id="gift-b-1"),
        target("target-2"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[4].pig.selector,
    )
    assert second.regulation is None
    third = await social.gift(
        _identity(user_id="source-a", message_id="gift-a-2"),
        target("target-3"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[1].pig.selector,
    )
    assert third.regulation is None
    warned = await social.gift(
        _identity(user_id="source-b", message_id="gift-b-2"),
        target("target-4"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[5].pig.selector,
    )
    assert warned.regulation is not None
    assert warned.regulation.blocked is False
    assert warned.regulation.stage == "warning"
    assert len(warned.regulation.notice_ids) == 2
    await _mark_all_notices_sent(regulation, warned.regulation.notice_ids)

    clock.value += timedelta(minutes=2)
    supervised = await social.gift(
        _identity(user_id="source-a", message_id="gift-a-3"),
        target("target-5"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[2].pig.selector,
    )
    assert supervised.regulation is not None
    assert supervised.regulation.blocked is True
    assert supervised.regulation.stage == "supervision"
    owner = await database.fetch_one(
        "SELECT owner_player_id, state FROM pig_instances WHERE pig_instance_id = ?",
        (assets[2].pig.pig_instance_id,),
    )
    assert owner is not None
    assert tuple(owner) == (
        _identity(user_id="source-a", message_id="owner").player_id,
        "active",
    )

    duplicate = await social.gift(
        _identity(user_id="source-a", message_id="gift-a-3"),
        target("target-duplicate"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[2].pig.selector,
    )
    assert duplicate.receipt_created is False
    member = await database.fetch_one(
        """
        SELECT incident_count FROM anti_abuse_case_members
        WHERE case_id = ? AND player_id = ?
        """,
        (supervised.regulation.case_id, _identity(user_id="source-a", message_id="x").player_id),
    )
    assert member is not None and int(member["incident_count"]) == 1

    clock.value += timedelta(minutes=2)
    restricted = await social.gift(
        _identity(user_id="source-a", message_id="gift-a-4"),
        target("target-6"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[2].pig.selector,
    )
    assert restricted.regulation is not None
    assert restricted.regulation.stage == "social-restriction"
    holds = await database.fetch_all(
        "SELECT hold_type, status FROM anti_abuse_holds WHERE case_id = ?",
        (restricted.regulation.case_id,),
    )
    assert [tuple(row) for row in holds] == [("social", "active")]

    clock.value += timedelta(hours=1, minutes=2)
    plugin_restricted = await social.gift(
        _identity(user_id="source-a", message_id="gift-a-5"),
        target("target-7"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[2].pig.selector,
    )
    assert plugin_restricted.regulation is not None
    assert plugin_restricted.regulation.stage == "plugin-restriction"
    active_hold = await regulation.active_plugin_hold(
        _identity(user_id="source-a", message_id="plugin-check")
    )
    assert active_hold is not None and active_hold.hold_type == "plugin"
    assert await regulation.active_plugin_hold(
        _identity(user_id="main", message_id="passive-target-check")
    ) is None

    released = await regulation.release_case(
        identity=_identity(user_id="admin", message_id="release-1"),
        case_id_prefix=plugin_restricted.regulation.case_id[:8],
        reason="测试人工复核",
    )
    assert released.already_closed is False
    assert released.released_hold_count == 1
    released_duplicate = await regulation.release_case(
        identity=_identity(user_id="admin", message_id="release-1"),
        case_id_prefix=plugin_restricted.regulation.case_id[:8],
        reason="测试人工复核",
    )
    assert released_duplicate.receipt_created is False
    audit_count = await database.fetch_one(
        """
        SELECT COUNT(*) AS count FROM audit_events
        WHERE action = 'automatic-regulation-released'
        """
    )
    assert audit_count is not None and int(audit_count["count"]) == 1
    assert await regulation.active_plugin_hold(
        _identity(user_id="source-a", message_id="plugin-check-2")
    ) is None
    await database.close()


@pytest.mark.asyncio
async def test_accepted_trade_is_evaluated_before_money_or_asset_moves(
    tmp_path: Path,
) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)
    target = _identity(user_id="main", message_id="target-seed")
    await _grant_coins(database, target, 1000)
    regulation = RegulationService(
        database,
        RegulationSection(
            enabled_scope_ids=["qq:100"],
            notice_cooldown_minutes=1,
        ),
        clock=clock,
    )
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
        trade_id_factory=iter(("AAAABBBB", "CCCCDDDD")).__next__,
    )

    await social.gift(
        _identity(user_id="source-a", message_id="seed-gift-a"),
        _identity(user_id="main", message_id="seed-target-a"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[0].pig.selector,
    )
    second_seed = await social.gift(
        _identity(user_id="source-b", message_id="seed-gift-b"),
        _identity(user_id="main", message_id="seed-target-b"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[4].pig.selector,
    )
    assert second_seed.regulation is None
    third_seed = await social.gift(
        _identity(user_id="source-a", message_id="seed-gift-a-2"),
        _identity(user_id="main", message_id="seed-target-a-2"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[1].pig.selector,
    )
    assert third_seed.regulation is None
    warning = await social.gift(
        _identity(user_id="source-b", message_id="seed-gift-b-2"),
        _identity(user_id="main", message_id="seed-target-b-2"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[5].pig.selector,
    )
    assert warning.regulation is not None
    await _mark_all_notices_sent(regulation, warning.regulation.notice_ids)

    first_offer = await social.create_trade(
        _identity(user_id="source-a", message_id="offer-1"),
        _identity(user_id="main", message_id="offer-target-1"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[2].pig.selector,
        price=50,
    )
    first_accept = await social.accept_trade(
        _identity(user_id="main", message_id="accept-1"),
        first_offer.trade.trade_id,
    )
    assert first_accept.trade.status is TradeStatus.ACCEPTED
    assert first_accept.regulation is not None
    assert first_accept.regulation.blocked is False
    await _mark_all_notices_sent(regulation, first_accept.regulation.notice_ids)

    clock.value += timedelta(minutes=2)
    second_offer = await social.create_trade(
        _identity(user_id="source-a", message_id="offer-2"),
        _identity(user_id="main", message_id="offer-target-2"),
        asset_kind=AssetKind.PIG,
        selector_text=assets[3].pig.selector,
        price=50,
    )
    blocked = await social.accept_trade(
        _identity(user_id="main", message_id="accept-2"),
        second_offer.trade.trade_id,
    )
    assert blocked.regulation is not None and blocked.regulation.blocked is True
    assert blocked.trade.status is TradeStatus.CANCELLED
    owner = await database.fetch_one(
        "SELECT owner_player_id, state, locked_trade_id FROM pig_instances WHERE pig_instance_id = ?",
        (assets[3].pig.pig_instance_id,),
    )
    assert owner is not None
    assert tuple(owner) == (
        _identity(user_id="source-a", message_id="owner").player_id,
        "active",
        None,
    )
    ledger = await database.fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM currency_ledger
        WHERE source_object_type = 'trade' AND source_object_id = ?
        """,
        (second_offer.trade.trade_id,),
    )
    assert ledger is not None and int(ledger["count"]) == 0
    await database.close()


@pytest.mark.asyncio
async def test_unlisted_scope_never_creates_regulation_state(tmp_path: Path) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)
    regulation = RegulationService(database, RegulationSection(), clock=clock)
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
    )
    for user_id, asset in (("source-a", assets[0]), ("source-b", assets[4])):
        result = await social.gift(
            _identity(user_id=user_id, message_id=f"disabled-{user_id}"),
            _identity(user_id="main", message_id=f"target-{user_id}"),
            asset_kind=AssetKind.PIG,
            selector_text=asset.pig.selector,
        )
        assert result.regulation is None
    count = await database.fetch_one("SELECT COUNT(*) AS count FROM anti_abuse_cases")
    assert count is not None and int(count["count"]) == 0
    await database.close()


@pytest.mark.asyncio
async def test_admin_transfers_are_fully_excluded_from_current_and_historical_graphs(
    tmp_path: Path,
) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)
    regulation = RegulationService(
        database,
        RegulationSection(enabled_scope_ids=["qq:100"]),
        admin_user_ids=["qq:source-a"],
        clock=clock,
    )
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
    )
    operations = (
        ("source-a", assets[0].pig.selector, "admin-a-1"),
        ("source-b", assets[4].pig.selector, "normal-b-1"),
        ("source-a", assets[1].pig.selector, "admin-a-2"),
        ("source-b", assets[5].pig.selector, "normal-b-2"),
    )
    for sender, selector, message_id in operations:
        result = await social.gift(
            _identity(user_id=sender, message_id=message_id),
            _identity(user_id="main", message_id=f"target-{message_id}"),
            asset_kind=AssetKind.PIG,
            selector_text=selector,
        )
        assert result.regulation is None
    case_count = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM anti_abuse_cases"
    )
    event_count = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM anti_abuse_events"
    )
    assert case_count is not None and int(case_count["count"]) == 0
    assert event_count is not None and int(event_count["count"]) == 0
    await database.close()


@pytest.mark.asyncio
async def test_cases_auto_dismiss_after_24_hours_and_disappear_from_admin_views(
    tmp_path: Path,
) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)
    regulation = RegulationService(
        database,
        RegulationSection(enabled_scope_ids=["qq:100"]),
        clock=clock,
    )
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
    )
    warning = None
    for sender, asset_index, suffix in (
        ("source-a", 0, "a1"),
        ("source-b", 4, "b1"),
        ("source-a", 1, "a2"),
        ("source-b", 5, "b2"),
    ):
        warning = await social.gift(
            _identity(user_id=sender, message_id=f"expiry-{suffix}"),
            _identity(user_id="main", message_id=f"expiry-target-{suffix}"),
            asset_kind=AssetKind.PIG,
            selector_text=assets[asset_index].pig.selector,
        )
    assert warning is not None and warning.regulation is not None
    case_id = warning.regulation.case_id
    assert len(await regulation.list_cases(scope_id="qq:100")) == 1

    clock.value += timedelta(hours=24, seconds=1)
    assert await regulation.list_cases(scope_id="qq:100") == ()
    row = await database.fetch_one(
        "SELECT status, score FROM anti_abuse_cases WHERE case_id = ?",
        (case_id,),
    )
    assert row is not None and tuple(row) == ("dismissed", 0)
    expiry_event = await database.fetch_one(
        """
        SELECT score FROM anti_abuse_events
        WHERE case_id = ? AND event_type = 'automatic-expiry'
        """,
        (case_id,),
    )
    assert expiry_event is not None and int(expiry_event["score"]) == 0
    with pytest.raises(ValueError, match="找不到"):
        await regulation.case_detail(
            scope_id="qq:100",
            case_id_prefix=case_id[:8],
        )
    await database.close()


@pytest.mark.asyncio
async def test_global_regulation_reset_backs_up_and_clears_current_state(
    tmp_path: Path,
) -> None:
    database, clock, assets = await _seed_two_source_assets(tmp_path)
    regulation = RegulationService(
        database,
        RegulationSection(enabled_scope_ids=["qq:100"]),
        clock=clock,
    )
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        regulation_service=regulation,
        clock=clock,
    )
    warning = None
    for sender, asset_index, suffix in (
        ("source-a", 0, "a1"),
        ("source-b", 4, "b1"),
        ("source-a", 1, "a2"),
        ("source-b", 5, "b2"),
    ):
        warning = await social.gift(
            _identity(user_id=sender, message_id=f"reset-{suffix}"),
            _identity(user_id="main", message_id=f"reset-target-{suffix}"),
            asset_kind=AssetKind.PIG,
            selector_text=assets[asset_index].pig.selector,
        )
    assert warning is not None and warning.regulation is not None
    case_id = warning.regulation.case_id
    sent_notice_id, pending_notice_id = warning.regulation.notice_ids
    assert await regulation.claim_notice(sent_notice_id) is not None
    assert await regulation.mark_notice_sent(sent_notice_id) is True

    now = iso_timestamp(clock.now())
    source_player_id = _identity(user_id="source-a", message_id="member").player_id
    async with database.transaction() as session:
        await session.execute(
            """
            UPDATE anti_abuse_case_members
            SET incident_count = 4, last_incident_at = ?, warning_served_at = ?
            WHERE case_id = ?
            """,
            (now, now, case_id),
        )
        await session.execute(
            """
            INSERT INTO anti_abuse_holds(
                hold_id, case_id, player_id, hold_type, sequence_number,
                status, starts_at, expires_at, reason,
                created_at, updated_at, released_at
            )
            VALUES ('reset-hold', ?, ?, 'social', 99, 'active', ?, ?,
                    'test', ?, ?, NULL)
            """,
            (
                case_id,
                source_player_id,
                now,
                iso_timestamp(clock.now() + timedelta(hours=12)),
                now,
                now,
            ),
        )

    result = await regulation.backup_and_reset_all_state(
        data_dir=tmp_path / "data",
        actor_user_id="test-operator",
        reason="专项测试清零",
        source="pytest",
    )
    assert result.backup_path.is_file()
    assert result.case_count == 1
    assert result.previously_active_case_count == 1
    assert result.reset_member_count >= 1
    assert result.invalidated_notice_count == 1
    assert result.released_hold_count == 1
    assert len(result.reset_event_ids) == 1
    assert len(result.audit_event_ids) == 1

    case = await database.fetch_one(
        "SELECT status, score FROM anti_abuse_cases WHERE case_id = ?",
        (case_id,),
    )
    member = await database.fetch_one(
        """
        SELECT SUM(incident_count) AS incidents,
               SUM(CASE WHEN warning_served_at IS NOT NULL THEN 1 ELSE 0 END) AS warned
        FROM anti_abuse_case_members WHERE case_id = ?
        """,
        (case_id,),
    )
    notices = await database.fetch_all(
        "SELECT notice_id, status, error_text FROM anti_abuse_notices ORDER BY notice_id"
    )
    hold = await database.fetch_one(
        "SELECT status FROM anti_abuse_holds WHERE hold_id = 'reset-hold'"
    )
    assert case is not None and tuple(case) == ("dismissed", 0)
    assert member is not None and int(member["incidents"] or 0) == 0
    assert member is not None and int(member["warned"] or 0) == 0
    notice_by_id = {str(row["notice_id"]): row for row in notices}
    assert str(notice_by_id[sent_notice_id]["status"]) == "sent"
    assert str(notice_by_id[pending_notice_id]["status"]) == "failed"
    assert "全局监管重置" in str(notice_by_id[pending_notice_id]["error_text"])
    assert hold is not None and str(hold["status"]) == "released"
    assert await regulation.claim_notice(pending_notice_id) is None
    assert await regulation.list_cases(scope_id="qq:100") == ()
    await database.close()


@pytest.mark.asyncio
async def test_scope_regulation_reset_does_not_touch_another_group(
    tmp_path: Path,
) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    now = iso_timestamp(clock.now())
    expires_at = iso_timestamp(clock.now() + timedelta(hours=12))
    target = _identity(user_id="target", message_id="scope-reset-target")
    other = _identity(
        user_id="other",
        message_id="scope-reset-other",
        group_id="200",
    )
    async with database.transaction() as session:
        framework = FrameworkRepository()
        await framework.touch_identity(session, identity=target, now=now)
        await framework.touch_identity(session, identity=other, now=now)
        for identity, case_id, score in (
            (target, "target-case", 88),
            (other, "other-case", 91),
        ):
            await session.execute(
                """
                INSERT INTO anti_abuse_cases(
                    case_id, scope_id, target_signature, target_player_ids_json,
                    status, score, ruleset_version, evidence_json,
                    created_at, updated_at, last_evidence_at, resolved_at
                )
                VALUES (?, ?, ?, ?, 'supervised', ?, 1, '{}', ?, ?, ?, NULL)
                """,
                (
                    case_id,
                    identity.scope.value,
                    identity.player_id,
                    f'["{identity.player_id}"]',
                    score,
                    now,
                    now,
                    now,
                ),
            )
            await session.execute(
                """
                INSERT INTO anti_abuse_case_members(
                    case_id, player_id, role, active_participant,
                    warning_served_at, incident_count, last_incident_at,
                    created_at, updated_at
                )
                VALUES (?, ?, 'target', 1, ?, 3, ?, ?, ?)
                """,
                (case_id, identity.player_id, now, now, now, now),
            )
            await session.execute(
                """
                INSERT INTO anti_abuse_notices(
                    notice_id, case_id, player_id, stage, incident_number,
                    message_text, status, source_operation_key, error_text,
                    created_at, updated_at, sent_at
                )
                VALUES (?, ?, ?, 'supervision', 1, 'test', 'pending', '', '',
                        ?, ?, NULL)
                """,
                (f"{case_id}-notice", case_id, identity.player_id, now, now),
            )
            await session.execute(
                """
                INSERT INTO anti_abuse_holds(
                    hold_id, case_id, player_id, hold_type, sequence_number,
                    status, starts_at, expires_at, reason,
                    created_at, updated_at, released_at
                )
                VALUES (?, ?, ?, 'social', 1, 'active', ?, ?, 'test',
                        ?, ?, NULL)
                """,
                (
                    f"{case_id}-hold",
                    case_id,
                    identity.player_id,
                    now,
                    expires_at,
                    now,
                    now,
                ),
            )

    regulation = RegulationService(database, RegulationSection(), clock=clock)
    result = await regulation.backup_and_reset_scope_state(
        data_dir=tmp_path / "data",
        scope_id=target.scope.value,
        actor_user_id="test-operator",
        reason="只撤销目标群",
        source="pytest",
    )

    assert result.backup_path.is_file()
    assert result.case_count == 1
    assert result.previously_active_case_count == 1
    assert result.reset_member_count == 1
    assert result.invalidated_notice_count == 1
    assert result.released_hold_count == 1

    target_case = await database.fetch_one(
        "SELECT status, score FROM anti_abuse_cases WHERE case_id = 'target-case'"
    )
    other_case = await database.fetch_one(
        "SELECT status, score FROM anti_abuse_cases WHERE case_id = 'other-case'"
    )
    target_member = await database.fetch_one(
        """
        SELECT warning_served_at, incident_count, last_incident_at
        FROM anti_abuse_case_members WHERE case_id = 'target-case'
        """
    )
    other_member = await database.fetch_one(
        """
        SELECT warning_served_at, incident_count, last_incident_at
        FROM anti_abuse_case_members WHERE case_id = 'other-case'
        """
    )
    notices = await database.fetch_all(
        "SELECT case_id, status, error_text FROM anti_abuse_notices ORDER BY case_id"
    )
    holds = await database.fetch_all(
        "SELECT case_id, status FROM anti_abuse_holds ORDER BY case_id"
    )
    events = await database.fetch_all(
        "SELECT scope_id, event_type FROM anti_abuse_events ORDER BY event_id"
    )
    audits = await database.fetch_all(
        "SELECT scope_id, action FROM audit_events ORDER BY created_at"
    )

    assert target_case is not None and tuple(target_case) == ("dismissed", 0)
    assert other_case is not None and tuple(other_case) == ("supervised", 91)
    assert target_member is not None and tuple(target_member) == (None, 0, None)
    assert other_member is not None and tuple(other_member) == (now, 3, now)
    assert [tuple(row) for row in notices] == [
        ("other-case", "pending", ""),
        ("target-case", "failed", "指定群监管重置：案件已撤销，不再投递。"),
    ]
    assert [tuple(row) for row in holds] == [
        ("other-case", "active"),
        ("target-case", "released"),
    ]
    assert [tuple(row) for row in events] == [
        (target.scope.value, "scope-reset")
    ]
    assert [tuple(row) for row in audits] == [
        (target.scope.value, "automatic-regulation-scope-reset")
    ]
    assert await regulation.list_cases(scope_id=target.scope.value) == ()
    assert len(await regulation.list_cases(scope_id=other.scope.value)) == 1
    await database.close()
