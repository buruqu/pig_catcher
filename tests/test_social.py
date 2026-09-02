"""Fifth-round social, body-scale, global-record, and ranking tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.config.model import CatchingSection, RankingSection, TradingSection
from pig_catcher.domain.enums import AssetKind, StatureProfile, TradeStatus
from pig_catcher.domain.errors import (
    AssetStateConflictError,
    DailyCatchLimitError,
    DomainValidationError,
    InsufficientBalanceError,
    SelfTransferError,
    SocialTransferRestrictedError,
    TradePermissionError,
    TradeStateError,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.social import RANKING_TYPES, describe_body_scale
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.repositories import (
    EconomyRepository,
    FrameworkRepository,
    RestrictionRepository,
)
from pig_catcher.infrastructure.repositories.restrictions import (
    CATCH_WINDOW_LIMIT,
    GIFT_TRANSFER_BAN,
    TRADE_BAN,
)
from pig_catcher.services import (
    AssetCatalogService,
    GameplayService,
    RestrictionAdminService,
    SocialService,
)
from pig_catcher.services.command_state import iso_timestamp


class SequenceRandom:
    """Deterministic random source."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def random(self) -> float:
        if not self.values:
            raise AssertionError("deterministic random source exhausted")
        return self.values.pop(0)


class MutableClock:
    """UTC clock that tests can advance."""

    def __init__(self) -> None:
        self.value = datetime(2026, 7, 28, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def _identity(
    *,
    user_id: str,
    message_id: str,
    group_id: str = "100",
) -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", group_id),
        stream_id=f"stream-{group_id}",
        user_id=user_id,
        display_name=f"成员{user_id}",
        message_id=message_id,
        group_name=f"测试群{group_id}",
    )


def _pig_entry(
    template_id: str,
    *,
    display_name: str,
    length_min: float,
    length_max: float,
    weight_min: float,
    weight_max: float,
    stature_profile: str,
) -> dict[str, object]:
    return {
        "template_id": template_id,
        "kind": "pig",
        "display_name": display_name,
        "rarity": 2,
        "scope": "common",
        "group_scope_id": None,
        "description": f"{display_name}的第五轮测试描述。",
        "image": f"{template_id}.png",
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "not-required",
        "length_min_cm": length_min,
        "length_max_cm": length_max,
        "weight_min_kg": weight_min,
        "weight_max_kg": weight_max,
        "fat_profile": "balanced",
        "stature_profile": stature_profile,
        "recipe_tags": ["测试"],
    }


async def _database_with_social_catalog(tmp_path: Path) -> PigCatcherDatabase:
    source = tmp_path / "source"
    source.mkdir()
    entries = [
        _pig_entry(
            "giant-pig",
            display_name="巨型测试猪",
            length_min=120,
            length_max=260,
            weight_min=350,
            weight_max=1800,
            stature_profile="giant",
        ),
        _pig_entry(
            "mini-pig",
            display_name="迷你测试猪",
            length_min=4,
            length_max=16,
            weight_min=0.35,
            weight_max=6,
            stature_profile="mini",
        ),
    ]
    for index, entry in enumerate(entries):
        Image.new(
            "RGBA",
            (64, 64),
            (255, 160 + index * 20, 200, 255),
        ).save(source / str(entry["image"]), format="PNG")
    (source / "assets.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "catalog_id": "fifth-round-tests",
                "source_label": "pytest fifth-round catalog",
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    database = PigCatcherDatabase(data_dir / "pig.sqlite3")
    await database.open()
    await AssetCatalogService(
        database,
        AssetCatalogStorage(data_dir),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    ).import_manifest(source / "assets.json")
    return database


def _catching() -> CatchingSection:
    return CatchingSection(
        daily_limit=10,
        cooldown_seconds=0,
        rarity_1_weight=0,
        rarity_2_weight=1,
        rarity_3_weight=0,
        rarity_4_weight=0,
        rarity_5_weight=0,
        rarity_6_weight=0,
    )


async def _catch_many(
    database: PigCatcherDatabase,
    clock: MutableClock,
    *,
    count: int,
    user_id: str = "seller",
) -> list[object]:
    rolls: list[float] = []
    for _ in range(count):
        rolls.extend((0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5))
    prefix = chr(ord("A") + sum(user_id.encode("utf-8")) % 26)
    codes = iter(f"{prefix}00000{index:02X}" for index in range(1, count + 1))
    service = GameplayService(
        database,
        _catching(),
        ranking=RankingSection(),
        random_source=SequenceRandom(*rolls),
        clock=clock,
        short_code_factory=codes.__next__,
    )
    results = []
    for index in range(count):
        results.append(
            await service.catch(
                _identity(
                    user_id=user_id,
                    message_id=f"catch-{user_id}-{index}",
                )
            )
        )
    return results


async def _grant_coins(
    database: PigCatcherDatabase,
    identity: CommandIdentity,
    amount: int,
) -> None:
    now = iso_timestamp(datetime(2026, 7, 28, 3, 0, tzinfo=UTC))
    async with database.transaction() as session:
        await FrameworkRepository().touch_identity(
            session,
            identity=identity,
            now=now,
        )
        balance = await EconomyRepository().apply_currency_change(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            amount=amount,
            reason_code="test-grant",
            reason_text="测试入账",
            source_object_type="test",
            source_object_id=identity.user_id,
            ledger_entry_id=f"seed-{identity.user_id}",
            idempotency_key=f"seed-{identity.player_id}",
            now=now,
        )
        assert balance == amount


def test_body_scale_profiles_are_materially_different() -> None:
    mini = describe_body_scale(
        stature_profile=StatureProfile.MINI,
        size_value=10,
        size_percentile=0.5,
        weight_value=3,
        weight_percentile=0.5,
        giant_size_threshold_cm=120,
        giant_weight_threshold_kg=350,
    )
    giant = describe_body_scale(
        stature_profile=StatureProfile.GIANT,
        size_value=150,
        size_percentile=0.2,
        weight_value=500,
        weight_percentile=0.2,
        giant_size_threshold_cm=120,
        giant_weight_threshold_kg=350,
    )
    assert mini.label == "袖珍品种"
    assert mini.is_giant_sighting is False
    assert giant.label == "双项巨物"
    assert giant.is_giant_sighting is True
    assert giant.giant_score > mini.giant_score * 20


@pytest.mark.asyncio
async def test_catch_records_new_body_and_global_giant_once(tmp_path: Path) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    service = GameplayService(
        database,
        _catching(),
        ranking=RankingSection(),
        random_source=SequenceRandom(
            0.5,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ),
        clock=clock,
        short_code_factory=lambda: "A19F2C3D",
    )
    identity = _identity(user_id="seller", message_id="giant-catch")
    result = await service.catch(identity)
    assert result.catalog_new is True
    assert result.pig.body_label == "双项巨物"
    assert result.global_size_record is True
    assert result.global_weight_record is True
    assert result.giant_sighting is True
    assert "NEW" in result.receipt.text_summary
    assert "巨物目击已留档" in result.receipt.text_summary

    duplicate = await GameplayService(
        database,
        _catching(),
        ranking=RankingSection(),
        random_source=SequenceRandom(),
        clock=clock,
    ).catch(identity)
    assert duplicate.receipt_created is False
    for table, expected in {
        "pig_instances": 1,
        "group_global_records": 2,
        "giant_sightings": 1,
        "command_receipts": 1,
    }.items():
        row = await database.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
        assert row is not None and int(row["count"]) == expected
    await database.close()


@pytest.mark.asyncio
async def test_gift_and_trade_are_atomic_idempotent_and_history_stable(
    tmp_path: Path,
) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    caught = await _catch_many(database, clock, count=3)
    seller = _identity(user_id="seller", message_id="gift-1")
    buyer = _identity(user_id="buyer", message_id="buyer-seed")
    await _grant_coins(database, buyer, 500)
    service = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
        trade_id_factory=iter(("AAAABBBB", "CCCCDDDD")).__next__,
    )

    gifted = await service.gift(
        seller,
        _identity(user_id="buyer", message_id="target"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[0].pig.selector,
    )
    assert gifted.receipt_created is True
    duplicate = await service.gift(
        seller,
        _identity(user_id="buyer", message_id="target"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[0].pig.selector,
    )
    assert duplicate.receipt_created is False

    offer = await service.create_trade(
        _identity(user_id="seller", message_id="trade-create"),
        _identity(user_id="buyer", message_id="target-2"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[1].pig.selector,
        price=120,
    )
    assert offer.trade.status is TradeStatus.PENDING
    locked = await database.fetch_one(
        "SELECT state, locked_trade_id FROM pig_instances WHERE pig_instance_id = ?",
        (caught[1].pig.pig_instance_id,),
    )
    assert locked is not None and tuple(locked) == ("locked-for-trade", "AAAABBBB")

    accepted_identity = _identity(user_id="buyer", message_id="trade-accept")
    accepted = await service.accept_trade(accepted_identity, "AAAABBBB")
    assert accepted.trade.status is TradeStatus.ACCEPTED
    assert (accepted.tax_amount, accepted.seller_net) == (6, 114)
    accepted_duplicate = await SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
    ).accept_trade(accepted_identity, "AAAABBBB")
    assert accepted_duplicate.receipt_created is False

    owners = await database.fetch_all(
        """
        SELECT pig_instance_id, owner_player_id, state
        FROM pig_instances
        ORDER BY pig_instance_id
        """
    )
    transferred = {
        str(row["pig_instance_id"]): (
            str(row["owner_player_id"]),
            str(row["state"]),
        )
        for row in owners
    }
    assert transferred[caught[0].pig.pig_instance_id] == (
        _identity(user_id="buyer", message_id="x").player_id,
        "active",
    )
    assert transferred[caught[1].pig.pig_instance_id] == (
        _identity(user_id="buyer", message_id="x").player_id,
        "active",
    )
    stats = await database.fetch_all(
        """
        SELECT player_id, total_catches, gifts_sent, gifts_received, trades_completed
        FROM player_statistics
        ORDER BY player_id
        """
    )
    stat_map = {str(row["player_id"]): tuple(row)[1:] for row in stats}
    assert stat_map[_identity(user_id="seller", message_id="x").player_id] == (
        3,
        1,
        0,
        1,
    )
    assert stat_map[_identity(user_id="buyer", message_id="x").player_id] == (
        0,
        0,
        1,
        1,
    )
    trade_ledger = await database.fetch_one(
        """
        SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total
        FROM currency_ledger
        WHERE source_object_type = 'trade' AND source_object_id = 'AAAABBBB'
        """
    )
    assert trade_ledger is not None
    assert tuple(trade_ledger) == (2, -6)

    insufficient_offer = await service.create_trade(
        _identity(user_id="seller", message_id="trade-expensive"),
        _identity(user_id="buyer", message_id="target-3"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[2].pig.selector,
        price=1000,
    )
    with pytest.raises(InsufficientBalanceError):
        await service.accept_trade(
            _identity(user_id="buyer", message_id="trade-poor"),
            insufficient_offer.trade.trade_id,
        )
    pending = await database.fetch_one(
        "SELECT status FROM trade_offers WHERE trade_id = ?",
        (insufficient_offer.trade.trade_id,),
    )
    assert pending is not None and pending["status"] == "pending"

    with pytest.raises(SelfTransferError):
        await service.gift(
            _identity(user_id="seller", message_id="self"),
            _identity(user_id="seller", message_id="self-target"),
            asset_kind=AssetKind.PIG,
            selector_text=caught[2].pig.selector,
        )
    with pytest.raises(SelfTransferError):
        await service.gift(
            _identity(user_id="seller", message_id="cross"),
            _identity(user_id="buyer", message_id="cross-target", group_id="999"),
            asset_kind=AssetKind.PIG,
            selector_text=caught[2].pig.selector,
        )
    await database.close()


@pytest.mark.asyncio
async def test_manual_gifts_have_separate_beijing_daily_send_and_receive_limits(tmp_path: Path) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    caught = await _catch_many(database, clock, count=6)
    other_catch = (await _catch_many(database, clock, count=1, user_id="other-sender"))[0]
    service = SocialService(database, TradingSection(), RankingSection(), clock=clock)
    recipient = _identity(user_id="recipient", message_id="target")
    for index, catch in enumerate(caught[:5]):
        result = await service.gift(
            _identity(user_id="seller", message_id=f"gift-limit-{index}"),
            recipient,
            asset_kind=AssetKind.PIG,
            selector_text=catch.pig.selector,
        )
        assert result.sender_remaining == result.recipient_remaining == 4 - index
    with pytest.raises(DomainValidationError, match="主动赠送额度"):
        await service.gift(
            _identity(user_id="seller", message_id="gift-limit-sixth"),
            recipient,
            asset_kind=AssetKind.PIG,
            selector_text=caught[5].pig.selector,
        )
    with pytest.raises(DomainValidationError, match="收赠"):
        await service.gift(
            _identity(user_id="other-sender", message_id="gift-receive-sixth"),
            recipient,
            asset_kind=AssetKind.PIG,
            selector_text=other_catch.pig.selector,
        )
    # Exactly at Beijing midnight both counters reset; the same recipient can receive again.
    clock.value = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    reset = await service.gift(
        _identity(user_id="other-sender", message_id="gift-after-midnight"),
        recipient,
        asset_kind=AssetKind.PIG,
        selector_text=other_catch.pig.selector,
    )
    assert (reset.sender_remaining, reset.recipient_remaining) == (4, 4)
    await database.close()


@pytest.mark.asyncio
async def test_name_only_social_selection_skips_favorites(tmp_path: Path) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    caught = await _catch_many(database, clock, count=3)
    favorite = caught[2].pig
    async with database.transaction() as session:
        await session.execute(
            "UPDATE pig_instances SET is_favorite = 1 WHERE pig_instance_id = ?",
            (favorite.pig_instance_id,),
        )
    service = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
        trade_id_factory=lambda: "FACEBEEF",
    )

    with pytest.raises(AssetStateConflictError, match="收藏保护"):
        await service.gift(
            _identity(user_id="seller", message_id="favorite-exact-gift"),
            _identity(user_id="buyer", message_id="favorite-target"),
            asset_kind=AssetKind.PIG,
            selector_text=favorite.selector,
        )

    candidates = sorted(
        (entry.pig.official_value, entry.pig.pig_instance_id)
        for entry in caught[:2]
    )
    gifted = await service.gift(
        _identity(user_id="seller", message_id="favorite-name-gift"),
        _identity(user_id="buyer", message_id="favorite-name-target"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[0].pig.display_name,
    )
    assert gifted.asset.instance_id == candidates[0][1]

    offer = await service.create_trade(
        _identity(user_id="seller", message_id="favorite-name-trade"),
        _identity(user_id="third", message_id="favorite-trade-target"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[0].pig.display_name,
        price=1,
    )
    assert offer.trade.asset.instance_id == candidates[1][1]
    protected = await database.fetch_one(
        """
        SELECT owner_player_id, state, is_favorite
        FROM pig_instances
        WHERE pig_instance_id = ?
        """,
        (favorite.pig_instance_id,),
    )
    assert protected is not None
    assert tuple(protected) == (
        _identity(user_id="seller", message_id="x").player_id,
        "active",
        1,
    )
    await database.close()


@pytest.mark.asyncio
async def test_trade_roles_expiry_showcase_and_all_rankings(tmp_path: Path) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    caught = await _catch_many(database, clock, count=2)
    seller = _identity(user_id="seller", message_id="offer")
    buyer = _identity(user_id="buyer", message_id="seed")
    stranger = _identity(user_id="stranger", message_id="stranger")
    await _grant_coins(database, buyer, 300)
    service = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
        trade_id_factory=iter(("1234ABCD", "5678ABCD")).__next__,
    )
    offer = await service.create_trade(
        seller,
        _identity(user_id="buyer", message_id="target"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[0].pig.selector,
        price=50,
    )
    with pytest.raises(TradePermissionError):
        await service.reject_trade(stranger, offer.trade.trade_id)
    rejected = await service.reject_trade(
        _identity(user_id="buyer", message_id="reject"),
        offer.trade.trade_id,
    )
    assert rejected.trade.status is TradeStatus.REJECTED

    expiring = await service.create_trade(
        _identity(user_id="seller", message_id="expiring"),
        _identity(user_id="buyer", message_id="target-2"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[1].pig.selector,
        price=60,
    )
    clock.value += timedelta(minutes=6)
    assert await service.expire_stale_offers() == 1
    expired = await database.fetch_one(
        "SELECT status FROM trade_offers WHERE trade_id = ?",
        (expiring.trade.trade_id,),
    )
    unlocked = await database.fetch_one(
        "SELECT state, locked_trade_id FROM pig_instances WHERE pig_instance_id = ?",
        (caught[1].pig.pig_instance_id,),
    )
    assert expired is not None and expired["status"] == "expired"
    assert unlocked is not None and tuple(unlocked) == ("active", None)

    showcase = await service.set_showcase(
        _identity(user_id="seller", message_id="showcase"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[1].pig.selector,
        clear=False,
    )
    assert showcase.asset is not None
    pig_name, food_name = await service.showcase_names(
        _identity(user_id="seller", message_id="profile")
    )
    assert pig_name == caught[1].pig.selector
    assert food_name == ""

    for ranking_type in RANKING_TYPES:
        page = await service.ranking(
            _identity(user_id="seller", message_id=f"rank-{ranking_type}"),
            ranking_type=ranking_type,
            page=1,
        )
        assert page.ranking_type == ranking_type
        assert page.total_count == 2
        assert [entry.rank for entry in page.entries] == [1, 2]
        assert all(entry.pig_catalog_total == 2 for entry in page.entries)

    cleared = await service.set_showcase(
        _identity(user_id="seller", message_id="showcase-clear"),
        asset_kind=AssetKind.PIG,
        selector_text="",
        clear=True,
    )
    assert cleared.cleared is True
    await database.close()


@pytest.mark.asyncio
async def test_value_and_quantity_ties_do_not_move_when_rankings_are_viewed(
    tmp_path: Path,
) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    service = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
    )
    buyer = _identity(user_id="buyer", message_id="rank-buyer")
    seller = _identity(user_id="seller", message_id="rank-seller")
    await service.ranking(buyer, ranking_type="价值", page=1)
    clock.value += timedelta(seconds=1)
    initial = await service.ranking(seller, ranking_type="价值", page=1)
    clock.value += timedelta(seconds=1)
    revisited = await service.ranking(buyer, ranking_type="价值", page=1)
    quantity = await service.ranking(buyer, ranking_type="数量", page=1)

    expected = [buyer.player_id, seller.player_id]
    assert [entry.player_id for entry in initial.entries] == expected
    assert [entry.player_id for entry in revisited.entries] == expected
    assert [entry.player_id for entry in quantity.entries] == expected
    await database.close()


@pytest.mark.asyncio
async def test_concurrent_acceptance_has_exactly_one_winner(tmp_path: Path) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    caught = await _catch_many(database, clock, count=1)
    await _grant_coins(
        database,
        _identity(user_id="buyer", message_id="seed"),
        500,
    )
    service = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
        trade_id_factory=lambda: "ABCDEF12",
    )
    offer = await service.create_trade(
        _identity(user_id="seller", message_id="offer"),
        _identity(user_id="buyer", message_id="target"),
        asset_kind=AssetKind.PIG,
        selector_text=caught[0].pig.selector,
        price=100,
    )
    outcomes = await asyncio.gather(
        service.accept_trade(
            _identity(user_id="buyer", message_id="accept-a"),
            offer.trade.trade_id,
        ),
        service.accept_trade(
            _identity(user_id="buyer", message_id="accept-b"),
            offer.trade.trade_id,
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in outcomes) == 1
    assert sum(isinstance(result, TradeStateError) for result in outcomes) == 1
    row = await database.fetch_one(
        """
        SELECT status FROM trade_offers WHERE trade_id = 'ABCDEF12'
        """
    )
    transfer = await database.fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM asset_transfer_events
        WHERE trade_id = 'ABCDEF12'
        """
    )
    ledger = await database.fetch_one(
        """
        SELECT COUNT(*) AS count, SUM(amount) AS total
        FROM currency_ledger
        WHERE source_object_type = 'trade' AND source_object_id = 'ABCDEF12'
        """
    )
    assert row is not None and row["status"] == "accepted"
    assert transfer is not None and transfer["count"] == 1
    assert ledger is not None and tuple(ledger) == (2, -5)
    await database.close()


@pytest.mark.asyncio
async def test_permanent_social_blacklists_and_expiring_catch_limit(
    tmp_path: Path,
) -> None:
    database = await _database_with_social_catalog(tmp_path)
    clock = MutableClock()
    caught = await _catch_many(database, clock, count=4)
    social = SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
        trade_id_factory=iter(("BAA00001", "BAA00002")).__next__,
    )
    seller = _identity(user_id="seller", message_id="gift-before-ban")
    buyer = _identity(user_id="buyer", message_id="buyer-before-ban")
    third = _identity(user_id="third", message_id="third-before-ban")

    await social.gift(
        seller,
        buyer,
        asset_kind=AssetKind.PIG,
        selector_text=caught[0].pig.selector,
    )
    pending_for_buyer = await social.create_trade(
        _identity(user_id="seller", message_id="offer-before-ban-1"),
        buyer,
        asset_kind=AssetKind.PIG,
        selector_text=caught[1].pig.selector,
        price=1,
    )
    pending_for_third = await social.create_trade(
        _identity(user_id="seller", message_id="offer-before-ban-2"),
        third,
        asset_kind=AssetKind.PIG,
        selector_text=caught[2].pig.selector,
        price=1,
    )

    result = await RestrictionAdminService(database, clock=clock).apply_batch(
        scope_id=buyer.scope.value,
        player_ids=(buyer.player_id,),
        duration=timedelta(days=7),
        catch_window_limit=1,
        reason="pytest 违规处理",
        source="pytest",
        created_by="pytest-admin",
        backup_path=tmp_path / "pre-restrictions.sqlite3",
    )
    assert result.cancelled_pending_trades == 1
    assert result.catch_limit_expires_at == iso_timestamp(clock.value + timedelta(days=7))

    rows = await database.fetch_all(
        """
        SELECT restriction_type, limit_value, expires_at
        FROM player_restrictions
        WHERE player_id = ?
        ORDER BY restriction_type
        """,
        (buyer.player_id,),
    )
    restrictions = {str(row["restriction_type"]): dict(row) for row in rows}
    assert set(restrictions) == {
        CATCH_WINDOW_LIMIT,
        GIFT_TRANSFER_BAN,
        TRADE_BAN,
    }
    assert restrictions[GIFT_TRANSFER_BAN]["expires_at"] is None
    assert restrictions[TRADE_BAN]["expires_at"] is None
    assert restrictions[CATCH_WINDOW_LIMIT]["limit_value"] == 1
    assert restrictions[CATCH_WINDOW_LIMIT]["expires_at"] is not None

    cancelled = await database.fetch_one(
        "SELECT status FROM trade_offers WHERE trade_id = ?",
        (pending_for_buyer.trade.trade_id,),
    )
    assert cancelled is not None and cancelled["status"] == TradeStatus.CANCELLED.value

    with pytest.raises(SocialTransferRestrictedError, match="赠送或收赠"):
        await social.gift(
            _identity(user_id="buyer", message_id="gift-banned-sender"),
            seller,
            asset_kind=AssetKind.PIG,
            selector_text=caught[0].pig.selector,
        )
    with pytest.raises(SocialTransferRestrictedError, match="赠送或收赠"):
        await social.gift(
            _identity(user_id="seller", message_id="gift-banned-recipient"),
            buyer,
            asset_kind=AssetKind.PIG,
            selector_text=caught[3].pig.selector,
        )
    with pytest.raises(SocialTransferRestrictedError, match="创建或接受"):
        await social.create_trade(
            _identity(user_id="seller", message_id="trade-banned-recipient"),
            buyer,
            asset_kind=AssetKind.PIG,
            selector_text=caught[3].pig.selector,
            price=1,
        )

    now = iso_timestamp(clock.value)
    async with database.transaction() as session:
        await RestrictionRepository().upsert_restriction(
            session,
            restriction_id="third-trade-ban",
            player_id=third.player_id,
            restriction_type=TRADE_BAN,
            limit_value=None,
            starts_at=now,
            expires_at=None,
            reason="pytest",
            source="pytest",
            created_by="pytest-admin",
            now=now,
        )
    with pytest.raises(SocialTransferRestrictedError, match="创建或接受"):
        await social.accept_trade(
            _identity(user_id="third", message_id="accept-while-banned"),
            pending_for_third.trade.trade_id,
        )

    catching = GameplayService(
        database,
        _catching(),
        ranking=RankingSection(),
        random_source=SequenceRandom(
            *(0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5) * 2
        ),
        clock=clock,
        short_code_factory=iter(("B1000001", "B1000002")).__next__,
    )
    first = await catching.catch(
        _identity(user_id="buyer", message_id="restricted-catch-1")
    )
    assert first.daily_limit == 1
    with pytest.raises(DailyCatchLimitError, match="本时段额度限制为 1 次"):
        await catching.catch(
            _identity(user_id="buyer", message_id="restricted-catch-2")
        )

    clock.value += timedelta(days=8)
    resumed = await catching.catch(
        _identity(user_id="buyer", message_id="catch-after-expiry")
    )
    assert resumed.daily_limit == _catching().daily_limit
    with pytest.raises(SocialTransferRestrictedError, match="永久列入黑名单"):
        await social.gift(
            _identity(user_id="buyer", message_id="gift-after-catch-expiry"),
            seller,
            asset_kind=AssetKind.PIG,
            selector_text=caught[0].pig.selector,
        )
    await database.close()
