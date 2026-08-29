"""Audited, idempotent administrator commands for one exact group scope."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..domain.economy import generate_food_attributes, recipe_affinity
from ..domain.enums import AssetKind
from ..domain.errors import DomainValidationError
from ..domain.gameplay import generate_pig_attributes
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import (
    Clock,
    MessageKeyFactory,
    RandomSource,
    SystemClock,
    SystemRandomSource,
)
from ..domain.quota import catch_quota_window
from ..domain.selectors import parse_asset_selector
from ..domain.short_codes import new_short_code, normalize_short_code
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    AdministrationRepository,
    EconomyRepository,
    FrameworkRepository,
    GameplayRepository,
    ReceiptRepository,
    RestrictionRepository,
    SocialRepository,
)
from ..infrastructure.repositories.activity_locks import require_unoccupied
from ..infrastructure.repositories.battle import BattleRepository, beijing_day
from ..infrastructure.repositories.dispatch import DispatchRepository
from ..infrastructure.repositories.restrictions import (
    GIFT_TRANSFER_BAN,
    PLUGIN_ACCESS_BAN,
    TRADE_BAN,
)
from ..version import RULESET_VERSION
from .command_state import iso_timestamp, validate_existing_receipt
from .receipts import request_fingerprint


@dataclass(frozen=True, slots=True)
class AdminCommandResult:
    """One committed administrator mutation and its single-send receipt."""

    receipt: CommandReceipt
    receipt_created: bool
    action: str
    affected_players: int = 0


@dataclass(frozen=True, slots=True)
class AdminBlacklistSnapshot:
    """All active current-group operational blacklist entries."""

    scope_id: str
    rows: tuple[Mapping[str, object], ...]


class AdministrationService:
    """Own privileged coin, asset, blacklist, and personal-quota transactions."""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        refresh_hours: Sequence[int],
        timezone_name: str,
        repository: AdministrationRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        gameplay_repository: GameplayRepository | None = None,
        economy_repository: EconomyRepository | None = None,
        receipt_repository: ReceiptRepository | None = None,
        restriction_repository: RestrictionRepository | None = None,
        social_repository: SocialRepository | None = None,
        battle_repository: BattleRepository | None = None,
        clock: Clock | None = None,
        random_source: RandomSource | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.refresh_hours = tuple(int(value) for value in refresh_hours)
        self.timezone_name = str(timezone_name)
        self.repository = repository or AdministrationRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.gameplay_repository = gameplay_repository or GameplayRepository()
        self.economy_repository = economy_repository or EconomyRepository()
        self.receipt_repository = receipt_repository or ReceiptRepository()
        self.restriction_repository = restriction_repository or RestrictionRepository()
        self.social_repository = social_repository or SocialRepository()
        self.battle_repository = battle_repository or BattleRepository()
        self.clock = clock or SystemClock()
        self.random_source = random_source or SystemRandomSource()
        self.id_factory = id_factory or (lambda: uuid4().hex)

    async def is_plugin_access_banned(
        self,
        *,
        scope_id: str,
        platform_user_id: str,
    ) -> bool:
        """Return whether an existing player is on the operational plugin blacklist."""

        now = iso_timestamp(self.clock.now())
        async with self.database.transaction(immediate=False) as session:
            row = await self.restriction_repository.active_plugin_access_ban(
                session,
                scope_id=scope_id,
                platform_user_id=platform_user_id,
                now=now,
            )
        return row is not None

    async def adjust_coins(
        self,
        identity: CommandIdentity,
        *,
        command_name: str,
        amount: int,
        target_user_id: str = "",
        all_players: bool = False,
    ) -> AdminCommandResult:
        """Apply one signed coin adjustment; admin deductions may cross zero."""

        normalized_amount = int(amount)
        if normalized_amount == 0:
            raise DomainValidationError("管理员猪币调整不能为零。")
        if abs(normalized_amount) > 9_000_000_000_000_000:
            raise DomainValidationError("单次猪币调整超出安全整数范围。")
        normalized_target = (
            "" if all_players else self._normalize_target_user_id(identity, target_user_id)
        )
        request_payload = {
            "command_version": 1,
            "amount": normalized_amount,
            "all_players": bool(all_players),
            "target_user_id": normalized_target,
        }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now = iso_timestamp(self.clock.now())
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                return self._existing_result(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            if all_players:
                players = await self.repository.players_in_scope(
                    session,
                    scope_id=identity.scope.value,
                )
            else:
                players = [
                    await self._require_target_player(
                        session,
                        identity=identity,
                        platform_user_id=normalized_target,
                    )
                ]
            if not players:
                raise DomainValidationError("当前群没有已登记玩家，未执行猪币调整。")

            balances: list[dict[str, object]] = []
            for player in players:
                player_id = str(player["player_id"])
                balance = await self.economy_repository.apply_currency_change(
                    session,
                    player_id=player_id,
                    scope_id=identity.scope.value,
                    amount=normalized_amount,
                    reason_code="admin-coin-adjustment",
                    reason_text=(
                        f"插件管理员{'发放' if normalized_amount > 0 else '扣除'}猪币"
                    ),
                    source_object_type="admin-operation",
                    source_object_id=audit_event_id,
                    ledger_entry_id=self.id_factory(),
                    idempotency_key=f"{idempotency_key}:coin:{player_id}",
                    now=now,
                    allow_negative=True,
                )
                if balance is None:
                    raise RuntimeError("管理员猪币调整未能写入目标玩家。")
                balances.append(
                    {
                        "player_id": player_id,
                        "platform_user_id": str(player["platform_user_id"]),
                        "display_name": str(player["display_name"]),
                        "balance_after": balance,
                    }
                )

            verb = "发放" if normalized_amount > 0 else "扣除"
            absolute_amount = abs(normalized_amount)
            if all_players:
                summary = (
                    f"【猪管·全员{verb}完成】\n"
                    f"当前群已登记玩家：{len(players)} 人\n"
                    f"每人{verb}：{absolute_amount} 猪币\n"
                    f"总变动：{normalized_amount * len(players):+d} 猪币\n"
                    f"审计号：{audit_event_id}"
                )
            else:
                target = balances[0]
                summary = (
                    f"【猪管·{verb}完成】\n"
                    f"玩家：{target['display_name']}（{target['platform_user_id']}）\n"
                    f"本次{verb}：{absolute_amount} 猪币\n"
                    f"调整后余额：{target['balance_after']} 猪币\n"
                    f"审计号：{audit_event_id}"
                )
            detail = {
                "amount": normalized_amount,
                "all_players": bool(all_players),
                "affected_players": len(players),
                "players": balances,
            }
            await self.repository.insert_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=identity.scope.value,
                actor_user_id=identity.user_id,
                action="admin-coins-adjusted",
                object_type="player-batch" if all_players else "player",
                object_id=(identity.scope.value if all_players else str(players[0]["player_id"])),
                detail_json=self._json(detail),
                now=now,
            )
            receipt = await self._reserve_receipt(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="admin-coin-adjustment",
                result_object_id=audit_event_id,
                result_payload=detail,
                text_summary=summary,
                now=now,
            )
        return AdminCommandResult(
            receipt=receipt,
            receipt_created=True,
            action="coin-adjustment",
            affected_players=len(players),
        )

    async def grant_asset(
        self,
        identity: CommandIdentity,
        *,
        command_name: str,
        target_user_id: str,
        asset_kind: AssetKind,
        template_selector: str,
        requested_short_code: str | None = None,
    ) -> AdminCommandResult:
        """Generate one current-scope asset without catch/cook rewards or statistics."""

        normalized_target = self._normalize_target_user_id(identity, target_user_id)
        normalized_selector = str(template_selector or "").strip()
        if not normalized_selector:
            raise DomainValidationError("要发放的素材名称或模板 ID 不能为空。")
        normalized_code = self._normalize_optional_short_code(requested_short_code)
        request_payload = {
            "command_version": 1,
            "target_user_id": normalized_target,
            "asset_kind": asset_kind.value,
            "template_selector": normalized_selector,
            "short_code": normalized_code or "",
        }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now = iso_timestamp(self.clock.now())
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                return self._existing_result(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            target = await self._require_target_player(
                session,
                identity=identity,
                platform_user_id=normalized_target,
            )
            templates = await self.repository.eligible_templates(
                session,
                scope_id=identity.scope.value,
                asset_kind=asset_kind,
                selector=normalized_selector,
            )
            if not templates:
                raise DomainValidationError(
                    "当前群找不到已启用且已授权的同名素材或模板 ID。"
                )
            if len(templates) != 1:
                choices = "、".join(str(row["template_id"]) for row in templates)
                raise DomainValidationError(f"素材名称不唯一，请改用模板 ID：{choices}")
            template = templates[0]
            short_code = await self._unique_short_code(session, requested=normalized_code)
            instance_id = self.id_factory()
            if asset_kind is AssetKind.PIG:
                attributes, snapshot = self._generate_admin_pig(template, identity)
                await self.gameplay_repository.insert_pig_instance(
                    session,
                    values={
                        "pig_instance_id": instance_id,
                        "short_code": short_code,
                        "scope_id": identity.scope.value,
                        "owner_player_id": str(target["player_id"]),
                        "template_id": str(template["template_id"]),
                        "template_version": int(template["template_version"]),
                        "rarity": int(template["rarity"]),
                        "display_name_snapshot": str(template["display_name"]),
                        "size_value": attributes.size_value,
                        "size_percentile": attributes.size_percentile,
                        "weight_value": attributes.weight_value,
                        "weight_percentile": attributes.weight_percentile,
                        "fat_ratio": attributes.fat_ratio,
                        "official_value": attributes.official_value,
                        "ruleset_version": RULESET_VERSION,
                        "random_snapshot_json": self._json(snapshot),
                        "acquired_at": now,
                        "updated_at": now,
                    },
                )
                await self.gameplay_repository.upsert_pig_catalog(
                    session,
                    player_id=str(target["player_id"]),
                    template_id=str(template["template_id"]),
                    size_value=attributes.size_value,
                    weight_value=attributes.weight_value,
                    now=now,
                )
                attribute_detail = {
                    "size_value": attributes.size_value,
                    "weight_value": attributes.weight_value,
                    "fat_ratio": attributes.fat_ratio,
                    "official_value": attributes.official_value,
                }
            else:
                attributes, fat_category, snapshot = self._generate_admin_food(template, identity)
                await self.economy_repository.insert_food_instance(
                    session,
                    values={
                        "food_instance_id": instance_id,
                        "short_code": short_code,
                        "scope_id": identity.scope.value,
                        "owner_player_id": str(target["player_id"]),
                        "template_id": str(template["template_id"]),
                        "template_version": int(template["template_version"]),
                        "source_pig_instance_id": None,
                        "rarity": int(template["rarity"]),
                        "display_name_snapshot": str(template["display_name"]),
                        "portion_weight": attributes.portion_weight,
                        "fat_category": fat_category,
                        "official_value": attributes.official_value,
                        "effect_id": str(template.get("effect_id") or ""),
                        "effect_params_json": str(template.get("effect_params_json") or "{}"),
                        "ruleset_version": RULESET_VERSION,
                        "random_snapshot_json": self._json(snapshot),
                        "acquired_at": now,
                        "updated_at": now,
                    },
                )
                await self.economy_repository.upsert_food_catalog(
                    session,
                    player_id=str(target["player_id"]),
                    template_id=str(template["template_id"]),
                    portion_weight=attributes.portion_weight,
                    now=now,
                )
                attribute_detail = {
                    "portion_weight": attributes.portion_weight,
                    "fat_category": fat_category,
                    "official_value": attributes.official_value,
                }
            detail = {
                "asset_kind": asset_kind.value,
                "instance_id": instance_id,
                "short_code": short_code,
                "template_id": str(template["template_id"]),
                "display_name": str(template["display_name"]),
                "rarity": int(template["rarity"]),
                "target_player_id": str(target["player_id"]),
                "target_platform_user_id": str(target["platform_user_id"]),
                "manual_short_code": bool(normalized_code),
                **attribute_detail,
            }
            kind_label = "猪猪" if asset_kind is AssetKind.PIG else "美食"
            summary = (
                f"【猪管·发放{kind_label}完成】\n"
                f"玩家：{target['display_name']}（{target['platform_user_id']}）\n"
                f"资产：{'★' * int(template['rarity'])} "
                f"{template['display_name']}#{short_code}\n"
                f"编号来源：{'管理员指定' if normalized_code else '系统自动生成'}\n"
                f"审计号：{audit_event_id}"
            )
            await self.repository.insert_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=identity.scope.value,
                actor_user_id=identity.user_id,
                action="admin-asset-granted",
                object_type=asset_kind.value,
                object_id=instance_id,
                detail_json=self._json(detail),
                now=now,
            )
            receipt = await self._reserve_receipt(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="admin-asset-grant",
                result_object_id=instance_id,
                result_payload=detail,
                text_summary=summary,
                now=now,
            )
        return AdminCommandResult(
            receipt=receipt,
            receipt_created=True,
            action="asset-grant",
            affected_players=1,
        )

    async def remove_asset(
        self,
        identity: CommandIdentity,
        *,
        command_name: str,
        target_user_id: str,
        asset_kind: AssetKind,
        selector_text: str,
    ) -> AdminCommandResult:
        """Preserve history while removing one exact active/locked asset from play."""

        normalized_target = self._normalize_target_user_id(identity, target_user_id)
        selector = parse_asset_selector(selector_text)
        if selector.short_code is None:
            raise DomainValidationError("管理员删除资产必须提供精确的资产短编号。")
        request_payload = {
            "command_version": 1,
            "target_user_id": normalized_target,
            "asset_kind": asset_kind.value,
            "selector": f"{selector.name}#{selector.short_code}",
        }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now = iso_timestamp(self.clock.now())
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                return self._existing_result(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            target = await self._require_target_player(
                session,
                identity=identity,
                platform_user_id=normalized_target,
            )
            await DispatchRepository().settle_elapsed(session, str(target["player_id"]), now)
            asset = await self.repository.active_asset_by_selector(
                session,
                scope_id=identity.scope.value,
                owner_player_id=str(target["player_id"]),
                asset_kind=asset_kind,
                display_name=selector.name,
                short_code=selector.short_code,
            )
            if asset is None:
                raise DomainValidationError("该玩家当前没有这件有效资产，或名称与编号不匹配。")
            instance_id = str(asset["asset_instance_id"])
            if asset_kind is AssetKind.PIG:
                await require_unoccupied(session, instance_id)
            cancelled_trades = await self.repository.cancel_pending_trade_for_asset(
                session,
                scope_id=identity.scope.value,
                asset_kind=asset_kind,
                asset_instance_id=instance_id,
                now=now,
            )
            removed = await self.repository.mark_asset_admin_removed(
                session,
                scope_id=identity.scope.value,
                owner_player_id=str(target["player_id"]),
                asset_kind=asset_kind,
                asset_instance_id=instance_id,
                now=now,
            )
            if not removed:
                raise RuntimeError("资产状态在管理员删除时发生变化，本次未提交。")
            await self.repository.clear_showcase_asset(
                session,
                owner_player_id=str(target["player_id"]),
                asset_kind=asset_kind,
                asset_instance_id=instance_id,
                now=now,
            )
            if asset_kind is AssetKind.PIG:
                await self.repository.repair_records_after_pig_removal(
                    session,
                    scope_id=identity.scope.value,
                    template_id=str(asset["template_id"]),
                    pig_instance_id=instance_id,
                )
            detail = {
                "asset_kind": asset_kind.value,
                "instance_id": instance_id,
                "selector": request_payload["selector"],
                "template_id": str(asset["template_id"]),
                "target_player_id": str(target["player_id"]),
                "target_platform_user_id": str(target["platform_user_id"]),
                "previous_state": str(asset["state"]),
                "cancelled_pending_trades": cancelled_trades,
                "history_preserved": True,
            }
            kind_label = "猪猪" if asset_kind is AssetKind.PIG else "美食"
            summary = (
                f"【猪管·删除{kind_label}完成】\n"
                f"玩家：{target['display_name']}（{target['platform_user_id']}）\n"
                f"资产：{request_payload['selector']}\n"
                "处理：已移出背包；历史实例与图鉴记录保留\n"
                f"取消关联待处理交易：{cancelled_trades} 笔\n"
                f"审计号：{audit_event_id}"
            )
            await self.repository.insert_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=identity.scope.value,
                actor_user_id=identity.user_id,
                action="admin-asset-removed",
                object_type=asset_kind.value,
                object_id=instance_id,
                detail_json=self._json(detail),
                now=now,
            )
            receipt = await self._reserve_receipt(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="admin-asset-removal",
                result_object_id=instance_id,
                result_payload=detail,
                text_summary=summary,
                now=now,
            )
        return AdminCommandResult(
            receipt=receipt,
            receipt_created=True,
            action="asset-removal",
            affected_players=1,
        )

    async def update_blacklist(
        self,
        identity: CommandIdentity,
        *,
        command_name: str,
        target_user_id: str,
        category: str,
        action: str,
        reason: str = "",
    ) -> AdminCommandResult:
        """Add/remove one exact current-group player from one operational list."""

        normalized_target = self._normalize_target_user_id(identity, target_user_id)
        restriction_type = self._restriction_type(category)
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"add", "remove"}:
            raise DomainValidationError("黑名单动作只能是加入或移除。")
        normalized_reason = str(reason or "").strip()[:500]
        request_payload = {
            "command_version": 1,
            "target_user_id": normalized_target,
            "category": category,
            "action": normalized_action,
            "reason": normalized_reason,
        }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now = iso_timestamp(self.clock.now())
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                return self._existing_result(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            target = await self._require_target_player(
                session,
                identity=identity,
                platform_user_id=normalized_target,
            )
            changed = False
            cancelled_trades = 0
            if normalized_action == "add":
                await self.restriction_repository.upsert_restriction(
                    session,
                    restriction_id=self.id_factory(),
                    player_id=str(target["player_id"]),
                    restriction_type=restriction_type,
                    limit_value=None,
                    starts_at=now,
                    expires_at=None,
                    reason=normalized_reason or "群内管理员命令加入",
                    source="group-admin-command",
                    created_by=identity.user_id,
                    now=now,
                )
                changed = True
                if restriction_type in {PLUGIN_ACCESS_BAN, TRADE_BAN}:
                    cancelled_trades = await self.social_repository.cancel_pending_offers_for_players(
                        session,
                        scope_id=identity.scope.value,
                        player_ids=(str(target["player_id"]),),
                        now=now,
                    )
            else:
                changed = await self.repository.delete_restriction(
                    session,
                    player_id=str(target["player_id"]),
                    restriction_type=restriction_type,
                )
            labels = {"plugin": "插件", "gift": "赠送/收赠", "trade": "交易"}
            verb = "加入" if normalized_action == "add" else "移除"
            result_label = "已更新" if changed else "原本不在名单中"
            detail = {
                "category": category,
                "restriction_type": restriction_type,
                "action": normalized_action,
                "changed": changed,
                "target_player_id": str(target["player_id"]),
                "target_platform_user_id": str(target["platform_user_id"]),
                "reason": normalized_reason,
                "cancelled_pending_trades": cancelled_trades,
            }
            summary = (
                f"【猪管·黑名单{verb}】\n"
                f"类别：{labels[category]}黑名单\n"
                f"玩家：{target['display_name']}（{target['platform_user_id']}）\n"
                f"结果：{result_label}\n"
                f"取消关联待处理交易：{cancelled_trades} 笔\n"
                f"审计号：{audit_event_id}"
            )
            await self.repository.insert_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=identity.scope.value,
                actor_user_id=identity.user_id,
                action="admin-blacklist-updated",
                object_type="player-restriction",
                object_id=str(target["player_id"]),
                detail_json=self._json(detail),
                now=now,
            )
            receipt = await self._reserve_receipt(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="admin-blacklist-update",
                result_object_id=audit_event_id,
                result_payload=detail,
                text_summary=summary,
                now=now,
            )
        return AdminCommandResult(
            receipt=receipt,
            receipt_created=True,
            action="blacklist-update",
            affected_players=1,
        )

    async def blacklist_snapshot(self, identity: CommandIdentity) -> AdminBlacklistSnapshot:
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction(immediate=False) as session:
            rows = await self.restriction_repository.list_active_blacklists(
                session,
                scope_id=identity.scope.value,
                now=now,
            )
        return AdminBlacklistSnapshot(
            scope_id=identity.scope.value,
            rows=tuple(rows),
        )

    async def reset_player_quota(
        self,
        identity: CommandIdentity,
        *,
        command_name: str,
        target_user_id: str,
    ) -> AdminCommandResult:
        """Reset only one player's current-window catch count and cooldown."""

        normalized_target = self._normalize_target_user_id(identity, target_user_id)
        request_payload = {
            "command_version": 1,
            "target_user_id": normalized_target,
        }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now_datetime = self.clock.now()
        now = iso_timestamp(now_datetime)
        window = catch_quota_window(
            now_datetime,
            refresh_hours=self.refresh_hours,
            timezone_name=self.timezone_name,
        )
        window_start = iso_timestamp(window.start)
        window_end = iso_timestamp(window.end)
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                return self._existing_result(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            target = await self._require_target_player(
                session,
                identity=identity,
                platform_user_id=normalized_target,
            )
            cleared_catches, _, _ = await self.gameplay_repository.catch_usage(
                session,
                player_id=str(target["player_id"]),
                window_start=window_start,
                window_end=window_end,
            )
            detail = {
                "target_player_id": str(target["player_id"]),
                "target_platform_user_id": str(target["platform_user_id"]),
                "window_start": window_start,
                "window_end": window_end,
                "cleared_catches": cleared_catches,
                "cooldown_cleared": True,
            }
            await self.repository.insert_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=identity.scope.value,
                actor_user_id=identity.user_id,
                action="player-catch-quota-window-reset",
                object_type="player",
                object_id=str(target["player_id"]),
                detail_json=self._json(detail),
                now=now,
            )
            summary = (
                "【猪管·玩家额度重置完成】\n"
                f"玩家：{target['display_name']}（{target['platform_user_id']}）\n"
                f"窗口：{window.label}\n"
                f"已归零：{cleared_catches} 次；冷却已清除\n"
                f"下次自然刷新：北京时间 {window.next_refresh_label}\n"
                f"审计号：{audit_event_id}"
            )
            receipt = await self._reserve_receipt(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="admin-player-quota-reset",
                result_object_id=audit_event_id,
                result_payload=detail,
                text_summary=summary,
                now=now,
            )
        return AdminCommandResult(
            receipt=receipt,
            receipt_created=True,
            action="player-quota-reset",
            affected_players=1,
        )

    async def reset_battle_quota(
        self,
        identity: CommandIdentity,
        *,
        command_name: str,
        target_user_id: str = "",
        all_players: bool = False,
    ) -> AdminCommandResult:
        """Reset both current-day battle roles for one player or this scope."""

        normalized_target = "" if all_players else self._normalize_target_user_id(identity, target_user_id)
        request_payload = {
            "command_version": 1,
            "all_players": bool(all_players),
            "target_user_id": normalized_target,
        }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now_datetime = self.clock.now()
        now = iso_timestamp(now_datetime)
        now_ms = int(now_datetime.timestamp() * 1000) // 1000 * 1000
        day = beijing_day(now_ms)
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                return self._existing_result(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            if all_players:
                players = await self.repository.players_in_scope(session, scope_id=identity.scope.value)
            else:
                players = [
                    await self._require_target_player(
                        session,
                        identity=identity,
                        platform_user_id=normalized_target,
                    )
                ]
            if not players:
                raise DomainValidationError("当前群没有已登记玩家，未执行比划机会重置。")

            plans: list[dict[str, object]] = []
            for player in players:
                player_id = str(player["player_id"])
                before_roles = await self.battle_repository.used_roles(session, player_id, day)
                generation = await self.battle_repository.quota_generation(session, player_id, day)
                plans.append(
                    {
                        "player_id": player_id,
                        "platform_user_id": str(player["platform_user_id"]),
                        "display_name": str(player["display_name"]),
                        "used_roles_before": sorted(before_roles),
                        "generation_before": generation,
                        "generation_after": generation + 1,
                    }
                )
            detail = {
                "day": day,
                "all_players": bool(all_players),
                "affected_players": len(plans),
                "players": plans,
                "roles_reset": ["initiator", "opponent"],
            }
            await self.repository.insert_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=identity.scope.value,
                actor_user_id=identity.user_id,
                action="admin-battle-quota-reset",
                object_type="player-batch" if all_players else "player",
                object_id=identity.scope.value if all_players else str(players[0]["player_id"]),
                detail_json=self._json(detail),
                now=now,
            )
            for plan in plans:
                player_id = str(plan["player_id"])
                generation, before_roles = await self.battle_repository.reset_quota_generation(
                    session,
                    player_id=player_id,
                    scope_id=identity.scope.value,
                    day=day,
                    reset_audit_id=audit_event_id,
                    now_ms=now_ms,
                )
                if generation != int(plan["generation_after"]) or sorted(before_roles) != plan["used_roles_before"]:
                    raise RuntimeError("比划机会重置计划在事务内发生变化，本次未提交。")
                await self.battle_repository.fact(
                    session,
                    player_id,
                    identity.scope.value,
                    audit_event_id,
                    f"quota-reset:{day}",
                    now_ms,
                    {
                        "actor_user_id": identity.user_id,
                        "day": day,
                        "used_roles_before": sorted(before_roles),
                        "generation": generation,
                        "initiator_remaining": 1,
                        "opponent_remaining": 1,
                    },
                )

            active_before = sum(bool(plan["used_roles_before"]) for plan in plans)
            if all_players:
                summary = (
                    "【猪管·全员比划机会重置完成】\n"
                    f"日期：{day}（北京时间）\n"
                    f"当前群已登记玩家：{len(plans)} 人\n"
                    f"其中重置前已使用过机会：{active_before} 人\n"
                    "现均可主动比划 1 次、被比划 1 次\n"
                    f"审计号：{audit_event_id}"
                )
            else:
                target = plans[0]
                role_labels = {"initiator": "主动比划", "opponent": "被比划"}
                used_text = "、".join(role_labels[role] for role in target["used_roles_before"]) or "无"
                summary = (
                    "【猪管·玩家比划机会重置完成】\n"
                    f"玩家：{target['display_name']}（{target['platform_user_id']}）\n"
                    f"重置前今日已用：{used_text}\n"
                    "现可主动比划 1 次、被比划 1 次\n"
                    f"审计号：{audit_event_id}"
                )
            receipt = await self._reserve_receipt(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="admin-battle-quota-reset",
                result_object_id=audit_event_id,
                result_payload=detail,
                text_summary=summary,
                now=now,
            )
        return AdminCommandResult(
            receipt=receipt,
            receipt_created=True,
            action="battle-quota-reset",
            affected_players=len(plans),
        )

    async def _require_target_player(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        platform_user_id: str,
    ) -> dict[str, object]:
        row = await self.repository.player_by_platform_user_id(
            session,
            scope_id=identity.scope.value,
            platform_user_id=platform_user_id,
        )
        if row is None:
            raise DomainValidationError(
                "目标用户尚未在当前群留下抓猪插件身份；为避免跨群或猜测身份，本次未执行。"
            )
        return row

    async def _reserve_receipt(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        idempotency_key: str,
        command_name: str,
        request_payload: Mapping[str, Any],
        result_type: str,
        result_object_id: str,
        result_payload: Mapping[str, Any],
        text_summary: str,
        now: str,
    ) -> CommandReceipt:
        reservation = await self.receipt_repository.reserve(
            session,
            idempotency_key=idempotency_key,
            scope_id=identity.scope.value,
            player_id=identity.player_id,
            command_name=command_name,
            request_fingerprint=request_fingerprint(request_payload),
            result_type=result_type,
            result_object_id=result_object_id,
            result_json=self._json(result_payload),
            text_summary=text_summary,
            now=now,
        )
        if not reservation.created:
            validate_existing_receipt(
                reservation.receipt,
                identity=identity,
                command_name=command_name,
                request_payload=request_payload,
            )
        return reservation.receipt

    @staticmethod
    def _existing_result(
        receipt: CommandReceipt,
        *,
        identity: CommandIdentity,
        command_name: str,
        request_payload: Mapping[str, Any],
    ) -> AdminCommandResult:
        validate_existing_receipt(
            receipt,
            identity=identity,
            command_name=command_name,
            request_payload=request_payload,
        )
        return AdminCommandResult(
            receipt=receipt,
            receipt_created=False,
            action="duplicate",
        )

    def _generate_admin_pig(
        self,
        template: Mapping[str, object],
        identity: CommandIdentity,
    ) -> tuple[Any, dict[str, object]]:
        rolls = tuple(self.random_source.random() for _ in range(5))
        attributes = generate_pig_attributes(
            rarity=int(template["rarity"]),
            length_min=float(template["length_min"]),
            length_max=float(template["length_max"]),
            weight_min=float(template["weight_min"]),
            weight_max=float(template["weight_max"]),
            fat_profile=str(template["fat_profile"]),
            random_values=rolls,
        )
        return attributes, {
            "ruleset_version": RULESET_VERSION,
            "source": "admin-grant",
            "actor_user_id": identity.user_id,
            "attribute_rolls": list(rolls),
            "gameplay_rewards_applied": False,
            "statistics_incremented": False,
        }

    def _generate_admin_food(
        self,
        template: Mapping[str, object],
        identity: CommandIdentity,
    ) -> tuple[Any, str, dict[str, object]]:
        portion_roll = self.random_source.random()
        synthetic_source_weight = 60.0
        synthetic_source_percentile = 0.5
        attributes = generate_food_attributes(
            rarity=int(template["rarity"]),
            template_id=str(template["template_id"]),
            source_weight=synthetic_source_weight,
            source_weight_percentile=synthetic_source_percentile,
            portion_roll=portion_roll,
        )
        try:
            tags_payload = json.loads(str(template.get("recipe_tags_json") or "[]"))
        except json.JSONDecodeError:
            tags_payload = []
        tags = tuple(str(value) for value in tags_payload) if isinstance(tags_payload, list) else ()
        fat_category = recipe_affinity(tags)
        return attributes, fat_category, {
            "ruleset_version": RULESET_VERSION,
            "source": "admin-grant",
            "actor_user_id": identity.user_id,
            "portion_roll": portion_roll,
            "synthetic_source_weight": synthetic_source_weight,
            "synthetic_source_weight_percentile": synthetic_source_percentile,
            "gameplay_rewards_applied": False,
            "statistics_incremented": False,
        }

    async def _unique_short_code(
        self,
        session: DatabaseSession,
        *,
        requested: str | None,
    ) -> str:
        if requested:
            if await self.gameplay_repository.short_code_exists(session, requested):
                raise DomainValidationError(f"短编号 {requested} 已被其他资产占用。")
            return requested
        for _ in range(64):
            candidate = new_short_code()
            if not await self.gameplay_repository.short_code_exists(session, candidate):
                return candidate
        raise RuntimeError("无法生成全库唯一的 8 位字母数字资产编号。")

    @staticmethod
    def _normalize_optional_short_code(value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_short_code(value)

    @staticmethod
    def _normalize_target_user_id(identity: CommandIdentity, value: str) -> str:
        normalized = str(value or "").strip()
        scope_prefix = f"{identity.scope.value}:"
        platform_prefix = f"{identity.scope.platform}:"
        if normalized.startswith(scope_prefix):
            normalized = normalized[len(scope_prefix) :]
        elif normalized.startswith(platform_prefix):
            normalized = normalized[len(platform_prefix) :]
        elif ":" in normalized:
            raise DomainValidationError("目标用户 ID 的平台或群范围与当前命令不一致。")
        if (
            not normalized
            or len(normalized) > 256
            or any(ord(character) < 32 for character in normalized)
        ):
            raise DomainValidationError("目标用户 ID/OpenID 不合法。")
        return normalized

    @staticmethod
    def _restriction_type(category: str) -> str:
        mapping = {
            "plugin": PLUGIN_ACCESS_BAN,
            "gift": GIFT_TRANSFER_BAN,
            "trade": TRADE_BAN,
        }
        try:
            return mapping[str(category or "").strip().lower()]
        except KeyError as exc:
            raise DomainValidationError("黑名单类别只能是：插件、赠送、交易。") from exc

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
