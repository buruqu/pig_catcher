"""Fifth-round gifts, two-party trades, showcases, and group rankings."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from ..config.model import RankingSection, TradingSection
from ..domain.display import format_length, format_weight
from ..domain.enums import AssetKind, ReceiptSendStatus, TradeStatus
from ..domain.errors import (
    AssetStateConflictError,
    FoodNotFoundError,
    InsufficientBalanceError,
    PigNotFoundError,
    ReceiptConflictError,
    SelfTransferError,
    SocialTransferRestrictedError,
    TradeExpiredError,
    TradeNotFoundError,
    TradePermissionError,
    TradePriceError,
    TradeStateError,
)
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, SystemClock
from ..domain.selectors import parse_asset_selector
from ..domain.social import (
    TRADE_STATUS_LABELS,
    giant_score,
    normalize_ranking_type,
    normalize_trade_id,
)
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    EconomyRepository,
    FrameworkRepository,
    GameplayRepository,
    ReceiptRepository,
    RestrictionRepository,
    SocialRepository,
)
from ..infrastructure.repositories.activity_locks import require_unoccupied
from ..infrastructure.repositories.restrictions import (
    GIFT_TRANSFER_BAN,
    PLUGIN_ACCESS_BAN,
    TRADE_BAN,
)
from .command_state import (
    iso_timestamp,
    receipt_payload,
    valid_page_count,
    validate_existing_receipt,
)
from .economy import FoodView, food_view_from_row
from .gameplay import PigView, pig_view_from_row
from .receipts import request_fingerprint
from .regulation import RegulationOutcome, RegulationService

_GIFT_COMMAND = "pig-catcher.gift"
_TRADE_OFFER_COMMAND = "pig-catcher.trade-offer"
_TRADE_ACCEPT_COMMAND = "pig-catcher.trade-accept"
_TRADE_REJECT_COMMAND = "pig-catcher.trade-reject"
_TRADE_CANCEL_COMMAND = "pig-catcher.trade-cancel"
_SHOWCASE_COMMAND = "pig-catcher.showcase"


@dataclass(frozen=True, slots=True)
class SocialAsset:
    """Path-safe immutable asset summary used by social receipts."""

    asset_kind: AssetKind
    instance_id: str
    display_name: str
    short_code: str
    rarity: int
    official_value: int
    detail_text: str

    @property
    def selector(self) -> str:
        return f"{self.display_name}#{self.short_code}"

    @property
    def kind_label(self) -> str:
        return "猪猪" if self.asset_kind is AssetKind.PIG else "美食"


@dataclass(frozen=True, slots=True)
class GiftResult:
    """Committed immediate gift."""

    receipt: CommandReceipt
    receipt_created: bool
    sender_display_name: str
    recipient_display_name: str
    asset: SocialAsset
    regulation: RegulationOutcome | None = None


@dataclass(frozen=True, slots=True)
class TradeView:
    """One persisted trade offer and its current state."""

    trade_id: str
    sender_player_id: str
    sender_display_name: str
    recipient_player_id: str
    recipient_display_name: str
    asset: SocialAsset
    price: int
    status: TradeStatus
    created_at: str
    expires_at: str
    resolved_at: str

    @property
    def status_label(self) -> str:
        return TRADE_STATUS_LABELS[self.status]


@dataclass(frozen=True, slots=True)
class TradeActionResult:
    """Committed trade creation or resolution."""

    receipt: CommandReceipt
    receipt_created: bool
    operation: str
    trade: TradeView
    buyer_balance: int | None = None
    seller_balance: int | None = None
    regulation: RegulationOutcome | None = None


@dataclass(frozen=True, slots=True)
class TradePage:
    """One player's current-group incoming and outgoing offers."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    status: TradeStatus | None
    entries: tuple[TradeView, ...]


@dataclass(frozen=True, slots=True)
class ShowcaseResult:
    """Committed display-slot update."""

    receipt: CommandReceipt
    receipt_created: bool
    asset_kind: AssetKind
    asset: SocialAsset | None
    cleared: bool


@dataclass(frozen=True, slots=True)
class ShowcaseAsset:
    """One leaderboard display asset with optional local media metadata."""

    asset_kind: AssetKind
    instance_id: str
    display_name: str
    short_code: str
    rarity: int
    official_value: int
    detail_text: str
    image_relpath: str
    image_fit: str
    media_visible: bool
    is_animated: bool


@dataclass(frozen=True, slots=True)
class RankingEntry:
    """One stable group leaderboard row."""

    rank: int
    player_id: str
    display_name: str
    metric_value: float
    metric_text: str
    pig_catalog_count: int
    pig_catalog_total: int
    food_catalog_count: int
    food_catalog_total: int
    active_pigs: int
    active_foods: int
    coin_balance: int
    showcase_pig: ShowcaseAsset | None
    showcase_food: ShowcaseAsset | None
    giant_pig: ShowcaseAsset | None
    giant_score: float


@dataclass(frozen=True, slots=True)
class RankingPage:
    """One current-group leaderboard page."""

    group_name: str
    ranking_type: str
    page: int
    page_count: int
    total_count: int
    entries: tuple[RankingEntry, ...]


def _asset_payload(asset: SocialAsset) -> dict[str, object]:
    return {
        "asset_kind": asset.asset_kind.value,
        "instance_id": asset.instance_id,
        "display_name": asset.display_name,
        "short_code": asset.short_code,
        "rarity": asset.rarity,
        "official_value": asset.official_value,
        "detail_text": asset.detail_text,
    }


def _asset_from_payload(payload: Mapping[str, object]) -> SocialAsset:
    return SocialAsset(
        asset_kind=AssetKind(str(payload["asset_kind"])),
        instance_id=str(payload["instance_id"]),
        display_name=str(payload["display_name"]),
        short_code=str(payload["short_code"]),
        rarity=int(payload["rarity"]),
        official_value=int(payload["official_value"]),
        detail_text=str(payload.get("detail_text") or ""),
    )


def _social_asset_from_pig(pig: PigView) -> SocialAsset:
    return SocialAsset(
        asset_kind=AssetKind.PIG,
        instance_id=pig.pig_instance_id,
        display_name=pig.display_name,
        short_code=pig.short_code,
        rarity=pig.rarity,
        official_value=pig.official_value,
        detail_text=f"{format_length(pig.size_value)} · {format_weight(pig.weight_value)}",
    )


def _social_asset_from_food(food: FoodView) -> SocialAsset:
    return SocialAsset(
        asset_kind=AssetKind.FOOD,
        instance_id=food.food_instance_id,
        display_name=food.display_name,
        short_code=food.short_code,
        rarity=food.rarity,
        official_value=food.official_value,
        detail_text=f"{format_weight(food.portion_weight)} · {food.fat_label}",
    )


def _showcase_pig(pig: PigView) -> ShowcaseAsset:
    return ShowcaseAsset(
        asset_kind=AssetKind.PIG,
        instance_id=pig.pig_instance_id,
        display_name=pig.display_name,
        short_code=pig.short_code,
        rarity=pig.rarity,
        official_value=pig.official_value,
        detail_text=f"{format_length(pig.size_value)} · {format_weight(pig.weight_value)}",
        image_relpath=pig.image_relpath,
        image_fit=pig.image_fit,
        media_visible=pig.media_visible,
        is_animated=pig.is_animated,
    )


def _showcase_food(food: FoodView) -> ShowcaseAsset:
    return ShowcaseAsset(
        asset_kind=AssetKind.FOOD,
        instance_id=food.food_instance_id,
        display_name=food.display_name,
        short_code=food.short_code,
        rarity=food.rarity,
        official_value=food.official_value,
        detail_text=f"{format_weight(food.portion_weight)} · {food.fat_label}",
        image_relpath=food.image_relpath,
        image_fit=food.image_fit,
        media_visible=food.media_visible,
        is_animated=food.is_animated,
    )


def format_gift_summary(result: GiftResult) -> str:
    if result.regulation is not None and result.regulation.blocked:
        return f"【赠送未执行】\n{result.regulation.public_message}"
    return (
        "【赠送成功】\n"
        f"{result.sender_display_name} 将 {result.asset.kind_label}"
        f" {result.asset.selector} 赠送给 {result.recipient_display_name}\n"
        f"品质：{'★' * result.asset.rarity}\n"
        f"属性：{result.asset.detail_text}\n"
        "本次不产生猪币或经验。"
    )


def format_trade_summary(result: TradeActionResult) -> str:
    trade = result.trade
    if result.regulation is not None and result.regulation.blocked:
        return (
            "【交易未执行】\n"
            f"交易号：{trade.trade_id}\n"
            f"物品：{'★' * trade.asset.rarity} {trade.asset.selector}\n"
            f"{result.regulation.public_message}"
        )
    balance = ""
    if result.buyer_balance is not None and result.seller_balance is not None:
        balance = (
            f"\n成交后余额：买方 {result.buyer_balance} / "
            f"卖方 {result.seller_balance} 猪币"
        )
    return (
        f"【交易{trade.status_label}】\n"
        f"交易号：{trade.trade_id}\n"
        f"卖方：{trade.sender_display_name}\n"
        f"买方：{trade.recipient_display_name}\n"
        f"物品：{'★' * trade.asset.rarity} {trade.asset.selector}\n"
        f"价格：{trade.price} 猪币\n"
        f"状态：{trade.status_label}\n"
        f"有效期至：{trade.expires_at}{balance}"
    )


def format_trade_page_summary(page: TradePage) -> str:
    status = TRADE_STATUS_LABELS[page.status] if page.status is not None else "全部"
    lines = [
        "【我的交易】",
        f"玩家：{page.display_name}",
        f"筛选：{status}；第 {page.page}/{page.page_count} 页；共 {page.total_count} 笔",
    ]
    for trade in page.entries:
        lines.append(
            f"{trade.trade_id}｜{trade.status_label}｜{trade.asset.selector}｜"
            f"{trade.price} 币｜{trade.sender_display_name} → "
            f"{trade.recipient_display_name}"
        )
    if not page.entries:
        lines.append("当前没有符合条件的交易。")
    return "\n".join(lines)


def format_showcase_summary(result: ShowcaseResult) -> str:
    kind_label = "猪猪" if result.asset_kind is AssetKind.PIG else "美食"
    if result.cleared:
        return f"【展示设置】已取消{kind_label}展示位。"
    if result.asset is None:
        raise RuntimeError("展示设置结果缺少资产。")
    return (
        f"【展示设置】已将{kind_label}展示位设为 "
        f"{'★' * result.asset.rarity} {result.asset.selector}。"
    )


def format_ranking_summary(page: RankingPage) -> str:
    lines = [
        f"【猪猪排行 · {page.ranking_type}】",
        f"群：{page.group_name or '当前群'}",
        f"第 {page.page}/{page.page_count} 页；共 {page.total_count} 人",
    ]
    for entry in page.entries:
        lines.append(
            f"{entry.rank}. {entry.display_name}｜{entry.metric_text}｜"
            f"猪 {entry.pig_catalog_count}/{entry.pig_catalog_total}｜"
            f"菜 {entry.food_catalog_count}/{entry.food_catalog_total}"
        )
    if not page.entries:
        lines.append("本群还没有排行数据。")
    return "\n".join(lines)


class SocialService:
    """Own fifth-round state transitions and current-group ranking reads."""

    def __init__(
        self,
        database: PigCatcherDatabase,
        trading: TradingSection,
        ranking: RankingSection,
        *,
        repository: SocialRepository | None = None,
        gameplay_repository: GameplayRepository | None = None,
        economy_repository: EconomyRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        receipt_repository: ReceiptRepository | None = None,
        restriction_repository: RestrictionRepository | None = None,
        regulation_service: RegulationService | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        trade_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.trading = trading
        self.ranking_config = ranking
        self.repository = repository or SocialRepository()
        self.gameplay_repository = gameplay_repository or GameplayRepository()
        self.economy_repository = economy_repository or EconomyRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.receipt_repository = receipt_repository or ReceiptRepository()
        self.restriction_repository = restriction_repository or RestrictionRepository()
        self.regulation_service = regulation_service
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.trade_id_factory = trade_id_factory or (lambda: uuid4().hex[:8].upper())

    async def expire_stale_offers(self) -> int:
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            return await self.repository.expire_stale_offers(session, now=now)

    async def gift(
        self,
        identity: CommandIdentity,
        recipient: CommandIdentity,
        *,
        asset_kind: AssetKind,
        selector_text: str,
    ) -> GiftResult:
        self._validate_parties(identity, recipient)
        await self.expire_stale_offers()
        selector = parse_asset_selector(selector_text)
        request_payload = {
            "command_version": 1,
            "asset_kind": asset_kind.value,
            "name": selector.name,
            "short_code": selector.short_code or "",
            "recipient_user_id": recipient.user_id,
        }
        idempotency_key = MessageKeyFactory.build(identity, _GIFT_COMMAND)
        now = iso_timestamp(self.clock.now())
        activity_snapshot = (
            await self.regulation_service.chat_activity_snapshot(
                stream_id=identity.stream_id,
                scope_id=identity.scope.value,
                now=now,
            )
            if self.regulation_service is not None
            else None
        )
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_GIFT_COMMAND,
                    request_payload=request_payload,
                )
                return self._gift_from_receipt(existing, receipt_created=False)
            await self._ensure_social_transfer_allowed(
                session,
                scope_id=identity.scope.value,
                player_ids=(identity.player_id, recipient.player_id),
                restriction_type=GIFT_TRANSFER_BAN,
                now=now,
            )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            await self.framework_repository.touch_identity(
                session,
                identity=recipient,
                now=now,
            )
            asset, pig, food = await self._resolve_asset(
                session,
                identity,
                asset_kind=asset_kind,
                selector_text=selector_text,
            )
            transfer_event_id = self.id_factory()
            regulation = None
            if self.regulation_service is not None:
                regulation = await self.regulation_service.evaluate_transfer(
                    session,
                    scope_id=identity.scope.value,
                    source_operation_key=idempotency_key,
                    transfer_event_id=transfer_event_id,
                    from_player_id=identity.player_id,
                    to_player_id=recipient.player_id,
                    asset_kind=asset_kind.value,
                    asset_instance_id=asset.instance_id,
                    rarity=asset.rarity,
                    official_value=asset.official_value,
                    transfer_type="gift",
                    price=None,
                    active_player_ids=(identity.player_id,),
                    now=now,
                    activity_snapshot=activity_snapshot,
                )
            if regulation is not None and regulation.blocked:
                payload = {
                    "sender_display_name": identity.display_name,
                    "recipient_display_name": recipient.display_name,
                    "asset": _asset_payload(asset),
                    "regulation": regulation.to_payload(),
                }
                provisional = GiftResult(
                    receipt=self._provisional_receipt(
                        identity=identity,
                        idempotency_key=idempotency_key,
                        command_name=_GIFT_COMMAND,
                        request_payload=request_payload,
                        result_type="gift-regulation-blocked",
                        result_object_id=asset.instance_id,
                        result_payload=payload,
                        now=now,
                    ),
                    receipt_created=True,
                    sender_display_name=identity.display_name,
                    recipient_display_name=recipient.display_name,
                    asset=asset,
                    regulation=regulation,
                )
                receipt = await self._reserve_receipt(
                    session,
                    provisional.receipt,
                    text_summary=format_gift_summary(provisional),
                )
                return GiftResult(
                    receipt=receipt,
                    receipt_created=True,
                    sender_display_name=identity.display_name,
                    recipient_display_name=recipient.display_name,
                    asset=asset,
                    regulation=regulation,
                )
            transferred = await self.repository.transfer_active_asset(
                session,
                asset_kind=asset_kind,
                asset_instance_id=asset.instance_id,
                scope_id=identity.scope.value,
                from_player_id=identity.player_id,
                to_player_id=recipient.player_id,
                now=now,
            )
            if not transferred:
                raise AssetStateConflictError("资产已被处置或锁定，本次赠送未发生。")
            await self._record_recipient_catalog(
                session,
                recipient_player_id=recipient.player_id,
                pig=pig,
                food=food,
                now=now,
            )
            await self.repository.clear_showcase_asset(
                session,
                player_id=identity.player_id,
                asset_kind=asset_kind,
                asset_instance_id=asset.instance_id,
                now=now,
            )
            await self.repository.insert_transfer_event(
                session,
                transfer_event_id=transfer_event_id,
                scope_id=identity.scope.value,
                asset_kind=asset_kind,
                asset_instance_id=asset.instance_id,
                from_player_id=identity.player_id,
                to_player_id=recipient.player_id,
                transfer_type="gift",
                trade_id=None,
                now=now,
            )
            await self.repository.increment_statistic(
                session,
                player_id=identity.player_id,
                field="gifts_sent",
                now=now,
            )
            await self.repository.increment_statistic(
                session,
                player_id=recipient.player_id,
                field="gifts_received",
                now=now,
            )
            payload = {
                "sender_display_name": identity.display_name,
                "recipient_display_name": recipient.display_name,
                "asset": _asset_payload(asset),
                "regulation": regulation.to_payload() if regulation is not None else None,
            }
            provisional = GiftResult(
                receipt=self._provisional_receipt(
                    identity=identity,
                    idempotency_key=idempotency_key,
                    command_name=_GIFT_COMMAND,
                    request_payload=request_payload,
                    result_type="gift",
                    result_object_id=asset.instance_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                sender_display_name=identity.display_name,
                recipient_display_name=recipient.display_name,
                asset=asset,
                regulation=regulation,
            )
            receipt = await self._reserve_receipt(
                session,
                provisional.receipt,
                text_summary=format_gift_summary(provisional),
            )
            return GiftResult(
                receipt=receipt,
                receipt_created=True,
                sender_display_name=identity.display_name,
                recipient_display_name=recipient.display_name,
                asset=asset,
                regulation=regulation,
            )

    async def create_trade(
        self,
        identity: CommandIdentity,
        recipient: CommandIdentity,
        *,
        asset_kind: AssetKind,
        selector_text: str,
        price: int,
    ) -> TradeActionResult:
        self._validate_parties(identity, recipient)
        if price <= 0 or price > self.trading.max_trade_price:
            raise TradePriceError(
                f"交易价格必须位于 1 至 {self.trading.max_trade_price} 猪币。"
            )
        await self.expire_stale_offers()
        selector = parse_asset_selector(selector_text)
        request_payload = {
            "command_version": 1,
            "asset_kind": asset_kind.value,
            "name": selector.name,
            "short_code": selector.short_code or "",
            "recipient_user_id": recipient.user_id,
            "price": price,
        }
        idempotency_key = MessageKeyFactory.build(identity, _TRADE_OFFER_COMMAND)
        now_datetime = self.clock.now()
        now = iso_timestamp(now_datetime)
        expires_at = iso_timestamp(
            now_datetime + timedelta(minutes=self.trading.offer_expiry_minutes)
        )
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_TRADE_OFFER_COMMAND,
                    request_payload=request_payload,
                )
                return self._trade_action_from_receipt(
                    existing,
                    receipt_created=False,
                )
            await self._ensure_social_transfer_allowed(
                session,
                scope_id=identity.scope.value,
                player_ids=(identity.player_id, recipient.player_id),
                restriction_type=TRADE_BAN,
                now=now,
            )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            await self.framework_repository.touch_identity(
                session,
                identity=recipient,
                now=now,
            )
            asset, _, _ = await self._resolve_asset(
                session,
                identity,
                asset_kind=asset_kind,
                selector_text=selector_text,
            )
            trade_id = await self._new_trade_id(session)
            await self.repository.insert_trade_offer(
                session,
                trade_id=trade_id,
                scope_id=identity.scope.value,
                sender_player_id=identity.player_id,
                recipient_player_id=recipient.player_id,
                asset_kind=asset_kind,
                asset_instance_id=asset.instance_id,
                price=price,
                created_at=now,
                expires_at=expires_at,
            )
            locked = await self.repository.lock_asset_for_trade(
                session,
                asset_kind=asset_kind,
                asset_instance_id=asset.instance_id,
                scope_id=identity.scope.value,
                owner_player_id=identity.player_id,
                trade_id=trade_id,
                now=now,
            )
            if not locked:
                raise AssetStateConflictError("资产已被处置或锁定，无法创建报价。")
            trade = TradeView(
                trade_id=trade_id,
                sender_player_id=identity.player_id,
                sender_display_name=identity.display_name,
                recipient_player_id=recipient.player_id,
                recipient_display_name=recipient.display_name,
                asset=asset,
                price=price,
                status=TradeStatus.PENDING,
                created_at=now,
                expires_at=expires_at,
                resolved_at="",
            )
            payload = self._trade_payload(trade, operation="created")
            provisional = TradeActionResult(
                receipt=self._provisional_receipt(
                    identity=identity,
                    idempotency_key=idempotency_key,
                    command_name=_TRADE_OFFER_COMMAND,
                    request_payload=request_payload,
                    result_type="trade-created",
                    result_object_id=trade_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                operation="created",
                trade=trade,
            )
            receipt = await self._reserve_receipt(
                session,
                provisional.receipt,
                text_summary=format_trade_summary(provisional),
            )
            return TradeActionResult(
                receipt=receipt,
                receipt_created=True,
                operation="created",
                trade=trade,
            )

    async def accept_trade(
        self,
        identity: CommandIdentity,
        trade_id: str,
    ) -> TradeActionResult:
        await self.expire_stale_offers()
        request_payload = {"command_version": 1, "trade_id": trade_id}
        idempotency_key = MessageKeyFactory.build(identity, _TRADE_ACCEPT_COMMAND)
        now = iso_timestamp(self.clock.now())
        activity_snapshot = (
            await self.regulation_service.chat_activity_snapshot(
                stream_id=identity.stream_id,
                scope_id=identity.scope.value,
                now=now,
            )
            if self.regulation_service is not None
            else None
        )
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_TRADE_ACCEPT_COMMAND,
                    request_payload=request_payload,
                )
                return self._trade_action_from_receipt(
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            row = await self._pending_trade_for_actor(
                session,
                identity=identity,
                trade_id=trade_id,
                role="recipient",
            )
            trade = self._trade_from_row(row)
            await self._ensure_social_transfer_allowed(
                session,
                scope_id=identity.scope.value,
                player_ids=(
                    trade.sender_player_id,
                    trade.recipient_player_id,
                ),
                restriction_type=TRADE_BAN,
                now=now,
            )
            transfer_event_id = self.id_factory()
            regulation = None
            if self.regulation_service is not None:
                regulation = await self.regulation_service.evaluate_transfer(
                    session,
                    scope_id=identity.scope.value,
                    source_operation_key=idempotency_key,
                    transfer_event_id=transfer_event_id,
                    from_player_id=trade.sender_player_id,
                    to_player_id=trade.recipient_player_id,
                    asset_kind=trade.asset.asset_kind.value,
                    asset_instance_id=trade.asset.instance_id,
                    rarity=trade.asset.rarity,
                    official_value=trade.asset.official_value,
                    transfer_type="trade",
                    price=trade.price,
                    active_player_ids=(
                        trade.sender_player_id,
                        trade.recipient_player_id,
                    ),
                    now=now,
                    activity_snapshot=activity_snapshot,
                )
            if regulation is not None and regulation.blocked:
                await self.repository.resolve_trade(
                    session,
                    trade_id=trade.trade_id,
                    expected_status=TradeStatus.PENDING,
                    new_status=TradeStatus.CANCELLED,
                    now=now,
                )
                await self.repository.unlock_trade_asset(
                    session,
                    asset_kind=trade.asset.asset_kind,
                    asset_instance_id=trade.asset.instance_id,
                    trade_id=trade.trade_id,
                    now=now,
                )
                cancelled = TradeView(
                    trade_id=trade.trade_id,
                    sender_player_id=trade.sender_player_id,
                    sender_display_name=trade.sender_display_name,
                    recipient_player_id=trade.recipient_player_id,
                    recipient_display_name=trade.recipient_display_name,
                    asset=trade.asset,
                    price=trade.price,
                    status=TradeStatus.CANCELLED,
                    created_at=trade.created_at,
                    expires_at=trade.expires_at,
                    resolved_at=now,
                )
                payload = self._trade_payload(
                    cancelled,
                    operation="regulation-blocked",
                    regulation=regulation,
                )
                provisional = TradeActionResult(
                    receipt=self._provisional_receipt(
                        identity=identity,
                        idempotency_key=idempotency_key,
                        command_name=_TRADE_ACCEPT_COMMAND,
                        request_payload=request_payload,
                        result_type="trade-regulation-blocked",
                        result_object_id=trade.trade_id,
                        result_payload=payload,
                        now=now,
                    ),
                    receipt_created=True,
                    operation="regulation-blocked",
                    trade=cancelled,
                    regulation=regulation,
                )
                receipt = await self._reserve_receipt(
                    session,
                    provisional.receipt,
                    text_summary=format_trade_summary(provisional),
                )
                return TradeActionResult(
                    receipt=receipt,
                    receipt_created=True,
                    operation="regulation-blocked",
                    trade=cancelled,
                    regulation=regulation,
                )
            buyer_balance = await self.economy_repository.apply_currency_change(
                session,
                player_id=trade.recipient_player_id,
                scope_id=identity.scope.value,
                amount=-trade.price,
                reason_code="player-trade-purchase",
                reason_text=f"购买 {trade.asset.selector}",
                source_object_type="trade",
                source_object_id=trade.trade_id,
                ledger_entry_id=self.id_factory(),
                idempotency_key=f"trade:{trade.trade_id}:buyer",
                now=now,
            )
            if buyer_balance is None:
                raise InsufficientBalanceError(
                    f"猪币不足，需要 {trade.price}；报价保持待处理。"
                )
            seller_balance = await self.economy_repository.apply_currency_change(
                session,
                player_id=trade.sender_player_id,
                scope_id=identity.scope.value,
                amount=trade.price,
                reason_code="player-trade-sale",
                reason_text=f"出售 {trade.asset.selector}",
                source_object_type="trade",
                source_object_id=trade.trade_id,
                ledger_entry_id=self.id_factory(),
                idempotency_key=f"trade:{trade.trade_id}:seller",
                now=now,
            )
            if seller_balance is None:
                raise RuntimeError("交易卖方入账失败。")
            pig, food = await self._asset_views_by_id(
                session,
                asset_kind=trade.asset.asset_kind,
                instance_id=trade.asset.instance_id,
            )
            transferred = await self.repository.accept_trade_asset(
                session,
                asset_kind=trade.asset.asset_kind,
                asset_instance_id=trade.asset.instance_id,
                trade_id=trade.trade_id,
                scope_id=identity.scope.value,
                sender_player_id=trade.sender_player_id,
                recipient_player_id=trade.recipient_player_id,
                now=now,
            )
            if not transferred:
                raise AssetStateConflictError("交易物品锁已失效，本次未扣款。")
            resolved = await self.repository.resolve_trade(
                session,
                trade_id=trade.trade_id,
                expected_status=TradeStatus.PENDING,
                new_status=TradeStatus.ACCEPTED,
                now=now,
            )
            if not resolved:
                raise TradeStateError("交易状态已变化，本次未成交。")
            await self._record_recipient_catalog(
                session,
                recipient_player_id=trade.recipient_player_id,
                pig=pig,
                food=food,
                now=now,
            )
            await self.repository.clear_showcase_asset(
                session,
                player_id=trade.sender_player_id,
                asset_kind=trade.asset.asset_kind,
                asset_instance_id=trade.asset.instance_id,
                now=now,
            )
            await self.repository.insert_transfer_event(
                session,
                transfer_event_id=transfer_event_id,
                scope_id=identity.scope.value,
                asset_kind=trade.asset.asset_kind,
                asset_instance_id=trade.asset.instance_id,
                from_player_id=trade.sender_player_id,
                to_player_id=trade.recipient_player_id,
                transfer_type="trade",
                trade_id=trade.trade_id,
                now=now,
            )
            for player_id in (
                trade.sender_player_id,
                trade.recipient_player_id,
            ):
                await self.repository.increment_statistic(
                    session,
                    player_id=player_id,
                    field="trades_completed",
                    now=now,
                )
            accepted = TradeView(
                trade_id=trade.trade_id,
                sender_player_id=trade.sender_player_id,
                sender_display_name=trade.sender_display_name,
                recipient_player_id=trade.recipient_player_id,
                recipient_display_name=trade.recipient_display_name,
                asset=trade.asset,
                price=trade.price,
                status=TradeStatus.ACCEPTED,
                created_at=trade.created_at,
                expires_at=trade.expires_at,
                resolved_at=now,
            )
            payload = self._trade_payload(
                accepted,
                operation="accepted",
                buyer_balance=buyer_balance,
                seller_balance=seller_balance,
                regulation=regulation,
            )
            provisional = TradeActionResult(
                receipt=self._provisional_receipt(
                    identity=identity,
                    idempotency_key=idempotency_key,
                    command_name=_TRADE_ACCEPT_COMMAND,
                    request_payload=request_payload,
                    result_type="trade-accepted",
                    result_object_id=trade.trade_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                operation="accepted",
                trade=accepted,
                buyer_balance=buyer_balance,
                seller_balance=seller_balance,
                regulation=regulation,
            )
            receipt = await self._reserve_receipt(
                session,
                provisional.receipt,
                text_summary=format_trade_summary(provisional),
            )
            return TradeActionResult(
                receipt=receipt,
                receipt_created=True,
                operation="accepted",
                trade=accepted,
                buyer_balance=buyer_balance,
                seller_balance=seller_balance,
                regulation=regulation,
            )

    async def reject_trade(
        self,
        identity: CommandIdentity,
        trade_id: str,
    ) -> TradeActionResult:
        return await self._close_trade(
            identity,
            trade_id,
            operation="rejected",
            command_name=_TRADE_REJECT_COMMAND,
            required_role="recipient",
            new_status=TradeStatus.REJECTED,
        )

    async def cancel_trade(
        self,
        identity: CommandIdentity,
        trade_id: str,
    ) -> TradeActionResult:
        return await self._close_trade(
            identity,
            trade_id,
            operation="cancelled",
            command_name=_TRADE_CANCEL_COMMAND,
            required_role="sender",
            new_status=TradeStatus.CANCELLED,
        )

    async def trade_page(
        self,
        identity: CommandIdentity,
        *,
        page: int,
        status: TradeStatus | None,
    ) -> TradePage:
        await self.expire_stale_offers()
        now = iso_timestamp(self.clock.now())
        page_size = self.trading.trade_page_size
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            total, rows = await self.repository.trade_page(
                session,
                player_id=identity.player_id,
                status=status,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
        pages = valid_page_count(page, total, page_size)
        return TradePage(
            display_name=identity.display_name,
            page=page,
            page_count=pages,
            total_count=total,
            status=status,
            entries=tuple(self._trade_from_row(row) for row in rows),
        )

    async def set_showcase(
        self,
        identity: CommandIdentity,
        *,
        asset_kind: AssetKind,
        selector_text: str,
        clear: bool,
    ) -> ShowcaseResult:
        await self.expire_stale_offers()
        request_payload = {
            "command_version": 1,
            "asset_kind": asset_kind.value,
            "selector": selector_text,
            "clear": clear,
        }
        idempotency_key = MessageKeyFactory.build(identity, _SHOWCASE_COMMAND)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_SHOWCASE_COMMAND,
                    request_payload=request_payload,
                )
                return self._showcase_from_receipt(
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            asset: SocialAsset | None = None
            if not clear:
                asset, _, _ = await self._resolve_asset(
                    session,
                    identity,
                    asset_kind=asset_kind,
                    selector_text=selector_text,
                    allow_favorite=True,
                )
            await self.repository.set_showcase(
                session,
                player_id=identity.player_id,
                asset_kind=asset_kind,
                asset_instance_id=asset.instance_id if asset is not None else None,
                now=now,
            )
            payload = {
                "asset_kind": asset_kind.value,
                "asset": _asset_payload(asset) if asset is not None else None,
                "cleared": clear,
            }
            provisional = ShowcaseResult(
                receipt=self._provisional_receipt(
                    identity=identity,
                    idempotency_key=idempotency_key,
                    command_name=_SHOWCASE_COMMAND,
                    request_payload=request_payload,
                    result_type="showcase",
                    result_object_id=asset.instance_id if asset is not None else "",
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                asset_kind=asset_kind,
                asset=asset,
                cleared=clear,
            )
            receipt = await self._reserve_receipt(
                session,
                provisional.receipt,
                text_summary=format_showcase_summary(provisional),
            )
            return ShowcaseResult(
                receipt=receipt,
                receipt_created=True,
                asset_kind=asset_kind,
                asset=asset,
                cleared=clear,
            )

    async def ranking(
        self,
        identity: CommandIdentity,
        *,
        ranking_type: str,
        page: int,
    ) -> RankingPage:
        await self.expire_stale_offers()
        resolved_type = normalize_ranking_type(ranking_type)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            pig_total, food_total, rows = await self.repository.ranking_base_rows(
                session,
                scope_id=identity.scope.value,
                giant_size_threshold_cm=self.ranking_config.giant_size_threshold_cm,
                giant_weight_threshold_kg=self.ranking_config.giant_weight_threshold_kg,
            )
            ranked = self._rank_rows(
                rows,
                ranking_type=resolved_type,
                pig_total=pig_total,
                food_total=food_total,
            )
            page_size = self.ranking_config.ranking_page_size
            pages = valid_page_count(page, len(ranked), page_size)
            selected = ranked[(page - 1) * page_size : page * page_size]
            entries: list[RankingEntry] = []
            for rank, row, metric, metric_text, giant_metric in selected:
                showcase_pig = await self._showcase_pig_by_id(
                    session,
                    row.get("showcase_pig_id"),
                )
                showcase_food = await self._showcase_food_by_id(
                    session,
                    row.get("showcase_food_id"),
                )
                giant_pig = await self._showcase_pig_by_id(
                    session,
                    row.get("giant_pig_id"),
                )
                entries.append(
                    RankingEntry(
                        rank=rank,
                        player_id=str(row["player_id"]),
                        display_name=str(row["display_name"]),
                        metric_value=metric,
                        metric_text=metric_text,
                        pig_catalog_count=int(row["pig_catalog_count"]),
                        pig_catalog_total=pig_total,
                        food_catalog_count=int(row["food_catalog_count"]),
                        food_catalog_total=food_total,
                        active_pigs=int(row["active_pigs"]),
                        active_foods=int(row["active_foods"]),
                        coin_balance=int(row["coin_balance"]),
                        showcase_pig=showcase_pig,
                        showcase_food=showcase_food,
                        giant_pig=giant_pig,
                        giant_score=giant_metric,
                    )
                )
        return RankingPage(
            group_name=identity.group_name,
            ranking_type=resolved_type,
            page=page,
            page_count=pages,
            total_count=len(ranked),
            entries=tuple(entries),
        )

    async def showcase_names(
        self,
        identity: CommandIdentity,
    ) -> tuple[str, str]:
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            row = await self.repository.showcase_row(
                session,
                player_id=identity.player_id,
            )
        pig = str(row.get("pig_display_name") or "")
        pig_code = str(row.get("pig_short_code") or "")
        food = str(row.get("food_display_name") or "")
        food_code = str(row.get("food_short_code") or "")
        return (
            f"{pig}#{pig_code}" if pig and pig_code else "",
            f"{food}#{food_code}" if food and food_code else "",
        )

    async def _close_trade(
        self,
        identity: CommandIdentity,
        trade_id: str,
        *,
        operation: str,
        command_name: str,
        required_role: str,
        new_status: TradeStatus,
    ) -> TradeActionResult:
        await self.expire_stale_offers()
        request_payload = {"command_version": 1, "trade_id": trade_id}
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
                return self._trade_action_from_receipt(
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            row = await self._pending_trade_for_actor(
                session,
                identity=identity,
                trade_id=trade_id,
                role=required_role,
            )
            trade = self._trade_from_row(row)
            resolved = await self.repository.resolve_trade(
                session,
                trade_id=trade.trade_id,
                expected_status=TradeStatus.PENDING,
                new_status=new_status,
                now=now,
            )
            if not resolved:
                raise TradeStateError("交易状态已变化，无法重复处理。")
            unlocked = await self.repository.unlock_trade_asset(
                session,
                asset_kind=trade.asset.asset_kind,
                asset_instance_id=trade.asset.instance_id,
                trade_id=trade.trade_id,
                now=now,
            )
            if not unlocked:
                raise AssetStateConflictError("交易物品锁异常，本次状态未改变。")
            closed = TradeView(
                trade_id=trade.trade_id,
                sender_player_id=trade.sender_player_id,
                sender_display_name=trade.sender_display_name,
                recipient_player_id=trade.recipient_player_id,
                recipient_display_name=trade.recipient_display_name,
                asset=trade.asset,
                price=trade.price,
                status=new_status,
                created_at=trade.created_at,
                expires_at=trade.expires_at,
                resolved_at=now,
            )
            payload = self._trade_payload(closed, operation=operation)
            provisional = TradeActionResult(
                receipt=self._provisional_receipt(
                    identity=identity,
                    idempotency_key=idempotency_key,
                    command_name=command_name,
                    request_payload=request_payload,
                    result_type=f"trade-{operation}",
                    result_object_id=trade.trade_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                operation=operation,
                trade=closed,
            )
            receipt = await self._reserve_receipt(
                session,
                provisional.receipt,
                text_summary=format_trade_summary(provisional),
            )
            return TradeActionResult(
                receipt=receipt,
                receipt_created=True,
                operation=operation,
                trade=closed,
            )

    async def _pending_trade_for_actor(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        trade_id: str,
        role: str,
    ) -> dict[str, object]:
        row = await self.repository.trade_row(
            session,
            scope_id=identity.scope.value,
            trade_id=trade_id,
        )
        if row is None:
            raise TradeNotFoundError("当前群找不到该交易号。")
        status = TradeStatus(str(row["status"]))
        if status is TradeStatus.EXPIRED:
            raise TradeExpiredError("交易已过期，物品已经解锁。")
        if status is not TradeStatus.PENDING:
            raise TradeStateError(
                f"交易当前状态为“{TRADE_STATUS_LABELS[status]}”，不能重复处理。"
            )
        expected_player = (
            str(row["recipient_player_id"])
            if role == "recipient"
            else str(row["sender_player_id"])
        )
        if identity.player_id != expected_player:
            role_label = "接收方" if role == "recipient" else "发起方"
            raise TradePermissionError(f"只有交易{role_label}可以执行此操作。")
        return row

    async def _resolve_asset(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        *,
        asset_kind: AssetKind,
        selector_text: str,
        allow_favorite: bool = False,
    ) -> tuple[SocialAsset, PigView | None, FoodView | None]:
        selector = parse_asset_selector(selector_text)
        if asset_kind is AssetKind.PIG:
            rows = await self.gameplay_repository.find_active_pigs(
                session,
                player_id=identity.player_id,
                selector=selector,
                prefer_highest=allow_favorite,
                available_only=not allow_favorite and selector.short_code is None,
            )
            if not rows:
                raise PigNotFoundError(
                    f"你的猪猪背包中找不到“{selector_text.strip()}”。"
                )
            if selector.short_code is None:
                eligible = (
                    rows
                    if allow_favorite
                    else [row for row in rows if not bool(row.get("is_favorite") or False)]
                )
                if not eligible:
                    raise AssetStateConflictError(
                        f"“{selector.name}”的全部实例都已收藏保护，请先取消收藏。"
                    )
                row = (
                    max(
                        eligible,
                        key=lambda item: (
                            int(item["official_value"]),
                            str(item["acquired_at"]),
                            str(item["pig_instance_id"]),
                        ),
                    )
                    if allow_favorite
                    else eligible[0]
                )
            else:
                row = rows[0]
            pig = self._pig_view(row)
            if not allow_favorite:
                await require_unoccupied(session, pig.pig_instance_id)
            if pig.is_favorite and not allow_favorite:
                raise AssetStateConflictError(
                    f"“{pig.selector}”已收藏保护，请先使用 /取消收藏 猪猪 {pig.selector}。"
                )
            return _social_asset_from_pig(pig), pig, None
        rows = await self.economy_repository.find_active_foods(
            session,
            player_id=identity.player_id,
            selector=selector,
            prefer_highest=allow_favorite,
        )
        if not rows:
            raise FoodNotFoundError(
                f"你的美食背包中找不到“{selector_text.strip()}”。"
            )
        if selector.short_code is None:
            eligible = (
                rows
                if allow_favorite
                else [row for row in rows if not bool(row.get("is_favorite") or False)]
            )
            if not eligible:
                raise AssetStateConflictError(
                    f"“{selector.name}”的全部实例都已收藏保护，请先取消收藏。"
                )
            row = (
                max(
                    eligible,
                    key=lambda item: (
                        int(item["official_value"]),
                        str(item["acquired_at"]),
                        str(item["food_instance_id"]),
                    ),
                )
                if allow_favorite
                else eligible[0]
            )
        else:
            row = rows[0]
        food = food_view_from_row(row)
        if food.is_favorite and not allow_favorite:
            raise AssetStateConflictError(
                f"“{food.selector}”已收藏保护，请先使用 /取消收藏 美食 {food.selector}。"
            )
        return _social_asset_from_food(food), None, food

    async def _asset_views_by_id(
        self,
        session: DatabaseSession,
        *,
        asset_kind: AssetKind,
        instance_id: str,
    ) -> tuple[PigView | None, FoodView | None]:
        if asset_kind is AssetKind.PIG:
            row = await self.gameplay_repository.get_pig_by_instance_id(
                session,
                pig_instance_id=instance_id,
            )
            if row is None:
                raise AssetStateConflictError("交易猪猪实例不存在。")
            return self._pig_view(row), None
        row = await self.economy_repository.get_food_by_instance_id(
            session,
            food_instance_id=instance_id,
        )
        if row is None:
            raise AssetStateConflictError("交易美食实例不存在。")
        return None, food_view_from_row(row)

    async def _record_recipient_catalog(
        self,
        session: DatabaseSession,
        *,
        recipient_player_id: str,
        pig: PigView | None,
        food: FoodView | None,
        now: str,
    ) -> None:
        if pig is not None:
            await self.gameplay_repository.upsert_pig_catalog(
                session,
                player_id=recipient_player_id,
                template_id=pig.template_id,
                size_value=pig.size_value,
                weight_value=pig.weight_value,
                now=now,
            )
            return
        if food is None:
            raise RuntimeError("转移资产缺少猪猪或美食视图。")
        await self.economy_repository.upsert_food_catalog(
            session,
            player_id=recipient_player_id,
            template_id=food.template_id,
            portion_weight=food.portion_weight,
            now=now,
        )

    async def _showcase_pig_by_id(
        self,
        session: DatabaseSession,
        value: object,
    ) -> ShowcaseAsset | None:
        instance_id = str(value or "")
        if not instance_id:
            return None
        row = await self.gameplay_repository.get_pig_by_instance_id(
            session,
            pig_instance_id=instance_id,
        )
        return _showcase_pig(self._pig_view(row)) if row is not None else None

    async def _showcase_food_by_id(
        self,
        session: DatabaseSession,
        value: object,
    ) -> ShowcaseAsset | None:
        instance_id = str(value or "")
        if not instance_id:
            return None
        row = await self.economy_repository.get_food_by_instance_id(
            session,
            food_instance_id=instance_id,
        )
        return _showcase_food(food_view_from_row(row)) if row is not None else None

    def _rank_rows(
        self,
        rows: list[dict[str, object]],
        *,
        ranking_type: str,
        pig_total: int,
        food_total: int,
    ) -> list[tuple[int, dict[str, object], float, str, float]]:
        scored: list[tuple[dict[str, object], float, str, float, str]] = []
        for row in rows:
            pig_ratio = (
                int(row["pig_catalog_count"]) / pig_total if pig_total else 0.0
            )
            food_ratio = (
                int(row["food_catalog_count"]) / food_total if food_total else 0.0
            )
            giant_metric = 0.0
            if (
                row.get("giant_size_value") is not None
                and row.get("giant_weight_value") is not None
            ):
                giant_metric = giant_score(
                    size_value=float(row["giant_size_value"]),
                    weight_value=float(row["giant_weight_value"]),
                    giant_size_threshold_cm=self.ranking_config.giant_size_threshold_cm,
                    giant_weight_threshold_kg=self.ranking_config.giant_weight_threshold_kg,
                )
            if ranking_type == "综合":
                metric = 100.0 * (
                    pig_ratio * self.ranking_config.pig_catalog_weight_percent / 100.0
                    + food_ratio
                    * self.ranking_config.food_catalog_weight_percent
                    / 100.0
                )
                metric_text = f"综合 {metric:.1f}%"
                reached = max(
                    str(row.get("pig_catalog_reached_at") or ""),
                    str(row.get("food_catalog_reached_at") or ""),
                )
            elif ranking_type == "抓猪":
                metric = float(row["total_catches"])
                metric_text = f"抓猪 {int(metric)} 次"
                reached = str(row.get("last_catch_at") or "")
            elif ranking_type == "美食":
                metric = float(row["total_cooks"])
                metric_text = f"做菜 {int(metric)} 次"
                reached = str(row.get("last_cook_at") or "")
            elif ranking_type == "价值":
                metric = float(row["asset_value"])
                metric_text = f"总价值 {int(metric)}"
                reached = str(row.get("created_at") or "")
            elif ranking_type == "数量":
                metric = float(int(row["active_pigs"]) + int(row["active_foods"]))
                metric_text = f"持有 {int(metric)} 件"
                reached = str(row.get("created_at") or "")
            elif ranking_type == "猪币":
                metric = float(row["coin_balance"])
                metric_text = f"猪币 {int(metric)}"
                reached = str(row.get("coin_reached_at") or row.get("created_at") or "")
            else:
                metric = giant_metric
                metric_text = (
                    f"巨物 {metric:.1f} 分"
                    if row.get("giant_pig_id")
                    else "暂无巨物"
                )
                reached = str(
                    row.get("giant_reached_at")
                    or row.get("created_at")
                    or ""
                )
            scored.append((row, metric, metric_text, giant_metric, reached))

        scored.sort(
            key=lambda item: (
                -item[1],
                item[4] or str(item[0].get("created_at") or ""),
                str(item[0]["player_id"]),
            )
        )
        return [
            (index, row, metric, metric_text, giant_metric)
            for index, (row, metric, metric_text, giant_metric, _) in enumerate(
                scored,
                start=1,
            )
        ]

    def _pig_view(self, row: Mapping[str, object]) -> PigView:
        return pig_view_from_row(
            row,
            giant_size_threshold_cm=self.ranking_config.giant_size_threshold_cm,
            giant_weight_threshold_kg=self.ranking_config.giant_weight_threshold_kg,
        )

    def _validate_parties(
        self,
        identity: CommandIdentity,
        recipient: CommandIdentity,
    ) -> None:
        if identity.scope != recipient.scope:
            raise SelfTransferError("不支持跨群赠送或交易。")
        if identity.user_id == recipient.user_id:
            raise SelfTransferError("不能把资产赠送或交易给自己。")

    async def _ensure_social_transfer_allowed(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        player_ids: tuple[str, ...],
        restriction_type: str,
        now: str,
    ) -> None:
        plugin_restrictions = await self.restriction_repository.active_restrictions_for_players(
            session,
            player_ids=player_ids,
            restriction_type=PLUGIN_ACCESS_BAN,
            now=now,
        )
        if plugin_restrictions:
            raise SocialTransferRestrictedError(
                "参与方账号已被列入插件黑名单，禁止参与赠送、收赠和交易；"
                "解除需由管理员复核。"
            )
        restrictions = await self.restriction_repository.active_restrictions_for_players(
            session,
            player_ids=player_ids,
            restriction_type=restriction_type,
            now=now,
        )
        if not restrictions:
            if self.regulation_service is None:
                return
            hold = await self.regulation_service.current_hold(
                session,
                scope_id=scope_id,
                player_ids=player_ids,
                hold_types=("social", "plugin"),
                now=now,
            )
            if hold is None:
                return
            raise SocialTransferRestrictedError(hold.public_message)
        if restriction_type == GIFT_TRANSFER_BAN:
            operation = "赠送或收赠任何猪猪和美食"
        else:
            operation = "创建或接受任何猪猪和美食交易"
        raise SocialTransferRestrictedError(
            f"参与方账号已被永久列入黑名单，禁止{operation}；"
            "解除需由管理员复核。"
        )

    async def _new_trade_id(self, session: DatabaseSession) -> str:
        for _ in range(64):
            candidate = str(self.trade_id_factory()).strip().upper()
            try:
                candidate = normalize_trade_id(candidate)
            except Exception:
                continue
            if not await self.repository.trade_id_exists(
                session,
                trade_id=candidate,
            ):
                return candidate
        raise RuntimeError("无法生成唯一交易号。")

    @staticmethod
    def _trade_from_row(row: Mapping[str, object]) -> TradeView:
        asset = SocialAsset(
            asset_kind=AssetKind(str(row["asset_kind"])),
            instance_id=str(row["asset_instance_id"]),
            display_name=str(row["asset_display_name"]),
            short_code=str(row["asset_short_code"]),
            rarity=int(row["asset_rarity"]),
            official_value=int(row.get("asset_official_value") or 0),
            detail_text="",
        )
        return TradeView(
            trade_id=str(row["trade_id"]),
            sender_player_id=str(row["sender_player_id"]),
            sender_display_name=str(row["sender_display_name"]),
            recipient_player_id=str(row["recipient_player_id"]),
            recipient_display_name=str(row["recipient_display_name"]),
            asset=asset,
            price=int(row["price"]),
            status=TradeStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            resolved_at=str(row.get("resolved_at") or ""),
        )

    @staticmethod
    def _trade_payload(
        trade: TradeView,
        *,
        operation: str,
        buyer_balance: int | None = None,
        seller_balance: int | None = None,
        regulation: RegulationOutcome | None = None,
    ) -> dict[str, object]:
        return {
            "operation": operation,
            "trade": {
                "trade_id": trade.trade_id,
                "sender_player_id": trade.sender_player_id,
                "sender_display_name": trade.sender_display_name,
                "recipient_player_id": trade.recipient_player_id,
                "recipient_display_name": trade.recipient_display_name,
                "asset": _asset_payload(trade.asset),
                "price": trade.price,
                "status": trade.status.value,
                "created_at": trade.created_at,
                "expires_at": trade.expires_at,
                "resolved_at": trade.resolved_at,
            },
            "buyer_balance": buyer_balance,
            "seller_balance": seller_balance,
            "regulation": regulation.to_payload() if regulation is not None else None,
        }

    @staticmethod
    def _trade_action_from_receipt(
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> TradeActionResult:
        payload = receipt_payload(receipt)
        trade_payload = payload.get("trade")
        if not isinstance(trade_payload, Mapping):
            raise ReceiptConflictError("交易回执缺少交易快照。")
        asset_payload = trade_payload.get("asset")
        if not isinstance(asset_payload, Mapping):
            raise ReceiptConflictError("交易回执缺少资产快照。")
        trade = TradeView(
            trade_id=str(trade_payload["trade_id"]),
            sender_player_id=str(trade_payload["sender_player_id"]),
            sender_display_name=str(trade_payload["sender_display_name"]),
            recipient_player_id=str(trade_payload["recipient_player_id"]),
            recipient_display_name=str(trade_payload["recipient_display_name"]),
            asset=_asset_from_payload(asset_payload),
            price=int(trade_payload["price"]),
            status=TradeStatus(str(trade_payload["status"])),
            created_at=str(trade_payload["created_at"]),
            expires_at=str(trade_payload["expires_at"]),
            resolved_at=str(trade_payload.get("resolved_at") or ""),
        )
        buyer = payload.get("buyer_balance")
        seller = payload.get("seller_balance")
        return TradeActionResult(
            receipt=receipt,
            receipt_created=receipt_created,
            operation=str(payload["operation"]),
            trade=trade,
            buyer_balance=int(buyer) if buyer is not None else None,
            seller_balance=int(seller) if seller is not None else None,
            regulation=RegulationOutcome.from_payload(payload.get("regulation")),
        )

    @staticmethod
    def _gift_from_receipt(
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> GiftResult:
        payload = receipt_payload(receipt)
        asset_payload = payload.get("asset")
        if not isinstance(asset_payload, Mapping):
            raise ReceiptConflictError("赠送回执缺少资产快照。")
        return GiftResult(
            receipt=receipt,
            receipt_created=receipt_created,
            sender_display_name=str(payload["sender_display_name"]),
            recipient_display_name=str(payload["recipient_display_name"]),
            asset=_asset_from_payload(asset_payload),
            regulation=RegulationOutcome.from_payload(payload.get("regulation")),
        )

    @staticmethod
    def _showcase_from_receipt(
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> ShowcaseResult:
        payload = receipt_payload(receipt)
        asset_payload = payload.get("asset")
        asset = (
            _asset_from_payload(asset_payload)
            if isinstance(asset_payload, Mapping)
            else None
        )
        return ShowcaseResult(
            receipt=receipt,
            receipt_created=receipt_created,
            asset_kind=AssetKind(str(payload["asset_kind"])),
            asset=asset,
            cleared=bool(payload["cleared"]),
        )

    @staticmethod
    def _provisional_receipt(
        *,
        identity: CommandIdentity,
        idempotency_key: str,
        command_name: str,
        request_payload: Mapping[str, Any],
        result_type: str,
        result_object_id: str,
        result_payload: Mapping[str, Any],
        now: str,
    ) -> CommandReceipt:
        return CommandReceipt(
            receipt_id="",
            idempotency_key=idempotency_key,
            scope_id=identity.scope.value,
            player_id=identity.player_id,
            command_name=command_name,
            request_fingerprint=request_fingerprint(request_payload),
            result_type=result_type,
            result_object_id=result_object_id,
            result_json=json.dumps(result_payload, ensure_ascii=False),
            text_summary="",
            send_status=ReceiptSendStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

    async def _reserve_receipt(
        self,
        session: DatabaseSession,
        receipt: CommandReceipt,
        *,
        text_summary: str,
    ) -> CommandReceipt:
        reservation = await self.receipt_repository.reserve(
            session,
            idempotency_key=receipt.idempotency_key,
            scope_id=receipt.scope_id,
            player_id=receipt.player_id,
            command_name=receipt.command_name,
            request_fingerprint=receipt.request_fingerprint,
            result_type=receipt.result_type,
            result_object_id=receipt.result_object_id,
            result_json=receipt.result_json,
            text_summary=text_summary,
            now=receipt.created_at,
        )
        return reservation.receipt
