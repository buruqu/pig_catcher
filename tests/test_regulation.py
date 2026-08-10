"""Automatic gift/trade regulation graph and transactional escalation tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from pig_catcher.config.model import RankingSection, RegulationSection, TradingSection
from pig_catcher.domain.enums import AssetKind, TradeStatus
from pig_catcher.domain.regulation import TransferSignal, analyze_transfer_graph
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

    clock.value += timedelta(hours=24, minutes=2)
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
