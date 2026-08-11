"""可审计、按群精确执行的抓猪额度重置服务。"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ..domain.errors import DomainValidationError, ReceiptConflictError
from ..domain.food_effects import (
    GROUP_WINDOW_HIGH_STAR_BOOST,
    QUOTA_RESET_CHANCE,
    resolve_food_effect,
)
from ..domain.models import CommandIdentity, CommandReceipt, ScopeKey
from ..domain.ports import Clock, MessageKeyFactory, SystemClock
from ..domain.quota import CatchQuotaWindow, catch_quota_window
from ..infrastructure.database import PigCatcherDatabase
from ..infrastructure.repositories import (
    EconomyRepository,
    FrameworkRepository,
    QuotaRepository,
    ReceiptRepository,
)
from ..version import RULESET_VERSION
from .command_state import (
    iso_timestamp,
    receipt_payload,
    validate_existing_receipt,
)
from .receipts import request_fingerprint

_RESET_COMMAND = "pig-catcher.reset-quota"
_RESET_REQUEST = {"command_version": 1}
_CHANCE_COMMAND = "pig-catcher.reset-quota-chance"
_CHANCE_REQUEST = {"command_version": 1}


@dataclass(frozen=True, slots=True)
class CatchQuotaResetResult:
    """一次指定群当前窗口重置的审计结果。"""

    audit_event_id: str
    scope_id: str
    window: CatchQuotaWindow
    cleared_catches: int
    affected_players: int
    backup_path: Path
    created_at: str
    receipt: CommandReceipt | None = None
    receipt_created: bool = True
    group_rewarded_players: int = 0
    group_coin_reward: int = 0
    group_dedicated_catches: int = 0
    group_effect_expires_at: str = ""
    hidden_boost_chance_percent: float = 0.0
    hidden_five_star_multiplier: float = 1.0
    hidden_six_star_multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class QuotaWindowBoostResult:
    """一次群级窗口提额（含额度重置）的审计结果。"""

    scope_ids: tuple[str, ...]
    window: CatchQuotaWindow
    window_start: str
    window_end: str
    limit_value: int
    backup_path: Path
    cleared_catches: int
    affected_players: int
    created_at: str
    audit_event_ids: tuple[str, ...]


class CatchQuotaResetService:
    """通过审计时间戳归零有效计数，不删除任何历史回执。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        refresh_hours: list[int] | tuple[int, ...],
        timezone_name: str,
        window_limit: int,
        repository: QuotaRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        receipt_repository: ReceiptRepository | None = None,
        economy_repository: EconomyRepository | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.refresh_hours = tuple(refresh_hours)
        self.timezone_name = timezone_name
        self.window_limit = int(window_limit)
        self.repository = repository or QuotaRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.receipt_repository = receipt_repository or ReceiptRepository()
        self.economy_repository = economy_repository or EconomyRepository()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)

    async def backup_and_reset_from_command(
        self,
        *,
        data_dir: Path,
        identity: CommandIdentity,
        source: str = "group-command",
    ) -> CatchQuotaResetResult:
        """幂等地备份并重置命令所在群的当前额度窗口。"""

        idempotency_key = MessageKeyFactory.build(identity, _RESET_COMMAND)
        existing = await self._command_receipt(idempotency_key)
        if existing is not None:
            validate_existing_receipt(
                existing,
                identity=identity,
                command_name=_RESET_COMMAND,
                request_payload=_RESET_REQUEST,
            )
            return self._result_from_receipt(existing, receipt_created=False)

        if not await self._scope_exists(identity.scope.value):
            raise DomainValidationError(
                f"数据库中不存在群范围 {identity.scope.value}，拒绝重置。"
            )
        now = self.clock.now()
        backup_path = self._backup_path(
            data_dir=data_dir,
            platform=identity.scope.platform,
            scope_id=identity.scope.value,
            now=now,
        )
        await self.database.backup_to(backup_path)
        return await self._reset_command_transaction(
            identity=identity,
            source=source,
            backup_path=backup_path,
            now=now,
            idempotency_key=idempotency_key,
        )

    async def reset_from_quota_chance(
        self,
        *,
        data_dir: Path,
        identity: CommandIdentity,
    ) -> CatchQuotaResetResult:
        """玩家消耗一次六星菜“重置额度机会”重置本群当前额度窗口。

        与管理员 /重置 走相同的备份、审计与幂等回执流程，并在同一事务内
        扣减玩家持有的 quota-reset 效果一次，避免重复使用。
        """

        idempotency_key = MessageKeyFactory.build(identity, _CHANCE_COMMAND)
        existing = await self._command_receipt(idempotency_key)
        if existing is not None:
            validate_existing_receipt(
                existing,
                identity=identity,
                command_name=_CHANCE_COMMAND,
                request_payload=_CHANCE_REQUEST,
            )
            return self._result_from_receipt(existing, receipt_created=False)

        if not await self._scope_exists(identity.scope.value):
            raise DomainValidationError(
                f"数据库中不存在群范围 {identity.scope.value}，拒绝重置。"
            )
        now = self.clock.now()
        backup_path = self._backup_path(
            data_dir=data_dir,
            platform=identity.scope.platform,
            scope_id=identity.scope.value,
            now=now,
        )
        await self.database.backup_to(backup_path)
        return await self._chance_reset_transaction(
            identity=identity,
            backup_path=backup_path,
            now=now,
            idempotency_key=idempotency_key,
        )

    async def backup_and_reset_current_window(
        self,
        *,
        data_dir: Path,
        group_id: str,
        platform: str = "qq",
        actor_user_id: str,
        source: str,
    ) -> CatchQuotaResetResult:
        """先在线备份，再精确重置一个群的当前额度窗口。"""

        scope_id = ScopeKey(platform=platform, group_id=group_id).value
        if not await self._scope_exists(scope_id):
            raise DomainValidationError(f"数据库中不存在群范围 {scope_id}，拒绝重置。")
        now = self.clock.now()
        backup_path = self._backup_path(
            data_dir=data_dir,
            platform=platform,
            scope_id=scope_id,
            now=now,
        )
        await self.database.backup_to(backup_path)
        return await self.reset_current_window(
            group_id=group_id,
            platform=platform,
            actor_user_id=actor_user_id,
            source=source,
            backup_path=backup_path,
            now=now,
        )

    async def apply_window_boost(
        self,
        *,
        data_dir: Path,
        scope_ids: Sequence[str],
        limit_value: int,
        created_by: str = "local-operator",
        reason: str = "",
        source: str = "manual",
        window_start: str | None = None,
    ) -> QuotaWindowBoostResult:
        """为一个或多个群作用域的当前额度窗口提升额度并重置计数。

        提额记录以 (scope_id, window_start) 为主键：窗口切换后自动失效，
        无需定时器即可在下一个刷新时段恢复每时段基础额度；窗口内生效期间
        抓猪额度按 limit_value 计算，并暂时忽略玩家违规限制（违规者在提额
        窗口内同样可抓满提升额度）。
        """

        normalized = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in scope_ids
                if str(value).strip()
            )
        )
        if not normalized:
            raise DomainValidationError("至少需要指定一个群作用域。")
        if not 1 <= int(limit_value) <= 1000:
            raise DomainValidationError("提额度数必须在 1 至 1000 之间。")
        now = self.clock.now()
        window = catch_quota_window(
            now,
            refresh_hours=self.refresh_hours,
            timezone_name=self.timezone_name,
        )
        requested_start = str(window_start or "").strip()
        target_start = (
            iso_timestamp(window.start)
            if not requested_start
            else requested_start
        )
        target_end = iso_timestamp(window.end)
        now_text = iso_timestamp(now)
        backup_path = self._backup_path(
            data_dir=data_dir,
            platform="multi",
            scope_id="-".join(normalized),
            now=now,
        )
        await self.database.backup_to(backup_path)
        audit_event_ids: list[str] = []
        total_cleared = 0
        total_players = 0
        async with self.database.transaction() as session:
            for scope_id in normalized:
                if not await self.repository.scope_exists(
                    session,
                    scope_id=scope_id,
                ):
                    raise DomainValidationError(
                        f"数据库中不存在群范围 {scope_id}，拒绝提额。"
                    )
                effective_start = await self.repository.effective_window_start(
                    session,
                    scope_id=scope_id,
                    window_start=target_start,
                    window_end=target_end,
                )
                cleared, affected = await self.repository.usage_since(
                    session,
                    scope_id=scope_id,
                    effective_start=effective_start,
                    window_end=target_end,
                )
                total_cleared += cleared
                total_players += affected
                await self.repository.upsert_window_boost(
                    session,
                    scope_id=scope_id,
                    window_start=target_start,
                    limit_value=int(limit_value),
                    created_by=created_by,
                    reason=reason,
                    now=now_text,
                )
                audit_event_id = self.id_factory()
                audit_event_ids.append(audit_event_id)
                detail = {
                    "source": str(source or "").strip() or "manual",
                    "ruleset_version": RULESET_VERSION,
                    "window_start": target_start,
                    "window_end": target_end,
                    "limit_value": int(limit_value),
                    "created_by": str(created_by or "").strip(),
                    "reason": str(reason or "").strip(),
                    "cleared_catches": cleared,
                    "affected_players": affected,
                    "backup_path": str(Path(backup_path).resolve()),
                }
                await self.repository.insert_boost_event(
                    session,
                    audit_event_id=audit_event_id,
                    scope_id=scope_id,
                    actor_user_id=(
                        str(created_by or "").strip() or "local-operator"
                    ),
                    object_id=target_start,
                    detail_json=json.dumps(
                        detail,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now=now_text,
                )
        return QuotaWindowBoostResult(
            scope_ids=normalized,
            window=window,
            window_start=target_start,
            window_end=target_end,
            limit_value=int(limit_value),
            backup_path=Path(backup_path).resolve(),
            cleared_catches=total_cleared,
            affected_players=total_players,
            created_at=now_text,
            audit_event_ids=tuple(audit_event_ids),
        )

    async def reset_current_window(
        self,
        *,
        group_id: str,
        platform: str = "qq",
        actor_user_id: str,
        source: str,
        backup_path: Path,
        now: datetime | None = None,
    ) -> CatchQuotaResetResult:
        """写入群级窗口重置事件并返回重置前的有效用量。"""

        scope_id = ScopeKey(platform=platform, group_id=group_id).value
        now_datetime = now or self.clock.now()
        window = catch_quota_window(
            now_datetime,
            refresh_hours=self.refresh_hours,
            timezone_name=self.timezone_name,
        )
        now_text = iso_timestamp(now_datetime)
        window_start = iso_timestamp(window.start)
        window_end = iso_timestamp(window.end)
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            if not await self.repository.scope_exists(session, scope_id=scope_id):
                raise DomainValidationError(f"数据库中不存在群范围 {scope_id}，拒绝重置。")
            effective_start = await self.repository.effective_window_start(
                session,
                scope_id=scope_id,
                window_start=window_start,
                window_end=window_end,
            )
            cleared_catches, affected_players = await self.repository.usage_since(
                session,
                scope_id=scope_id,
                effective_start=effective_start,
                window_end=window_end,
            )
            detail = {
                "source": str(source or "").strip() or "manual",
                "ruleset_version": RULESET_VERSION,
                "window_start": window_start,
                "window_end": window_end,
                "previous_effective_start": effective_start,
                "window_limit": self.window_limit,
                "refresh_hours": list(self.refresh_hours),
                "cleared_catches": cleared_catches,
                "affected_players": affected_players,
                "cooldown_cleared": True,
                "backup_path": str(Path(backup_path).resolve()),
            }
            await self.repository.insert_reset_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=scope_id,
                actor_user_id=str(actor_user_id or "").strip() or "local-operator",
                object_id=window_start,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=now_text,
            )
        return CatchQuotaResetResult(
            audit_event_id=audit_event_id,
            scope_id=scope_id,
            window=window,
            cleared_catches=cleared_catches,
            affected_players=affected_players,
            backup_path=Path(backup_path).resolve(),
            created_at=now_text,
        )

    async def _reset_command_transaction(
        self,
        *,
        identity: CommandIdentity,
        source: str,
        backup_path: Path,
        now: datetime,
        idempotency_key: str,
    ) -> CatchQuotaResetResult:
        scope_id = identity.scope.value
        window = catch_quota_window(
            now,
            refresh_hours=self.refresh_hours,
            timezone_name=self.timezone_name,
        )
        now_text = iso_timestamp(now)
        window_start = iso_timestamp(window.start)
        window_end = iso_timestamp(window.end)
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_RESET_COMMAND,
                    request_payload=_RESET_REQUEST,
                )
                return self._result_from_receipt(existing, receipt_created=False)
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now_text,
            )
            effective_start = await self.repository.effective_window_start(
                session,
                scope_id=scope_id,
                window_start=window_start,
                window_end=window_end,
            )
            cleared_catches, affected_players = await self.repository.usage_since(
                session,
                scope_id=scope_id,
                effective_start=effective_start,
                window_end=window_end,
            )
            detail = self._reset_detail(
                source=source,
                window_start=window_start,
                window_end=window_end,
                previous_effective_start=effective_start,
                cleared_catches=cleared_catches,
                affected_players=affected_players,
                backup_path=backup_path,
            )
            result_payload = {
                "audit_event_id": audit_event_id,
                "scope_id": scope_id,
                "window_start": window_start,
                "window_end": window_end,
                "window_label": window.label,
                "next_refresh_label": window.next_refresh_label,
                "cleared_catches": cleared_catches,
                "affected_players": affected_players,
                "backup_path": str(backup_path),
                "created_at": now_text,
            }
            summary = self._command_summary(
                identity=identity,
                window=window,
                cleared_catches=cleared_catches,
                affected_players=affected_players,
            )
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=scope_id,
                player_id=identity.player_id,
                command_name=_RESET_COMMAND,
                request_fingerprint=request_fingerprint(_RESET_REQUEST),
                result_type="catch-quota-reset",
                result_object_id=audit_event_id,
                result_json=json.dumps(
                    result_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=summary,
                now=now_text,
            )
            if not reservation.created:
                validate_existing_receipt(
                    reservation.receipt,
                    identity=identity,
                    command_name=_RESET_COMMAND,
                    request_payload=_RESET_REQUEST,
                )
                return self._result_from_receipt(
                    reservation.receipt,
                    receipt_created=False,
                )
            await self.repository.insert_reset_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=scope_id,
                actor_user_id=identity.user_id,
                object_id=window_start,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=now_text,
            )
        return CatchQuotaResetResult(
            audit_event_id=audit_event_id,
            scope_id=scope_id,
            window=window,
            cleared_catches=cleared_catches,
            affected_players=affected_players,
            backup_path=backup_path,
            created_at=now_text,
            receipt=reservation.receipt,
            receipt_created=True,
        )

    async def _chance_reset_transaction(
        self,
        *,
        identity: CommandIdentity,
        backup_path: Path,
        now: datetime,
        idempotency_key: str,
    ) -> CatchQuotaResetResult:
        """在单事务内完成重置并消耗玩家的一次重置机会效果。"""

        scope_id = identity.scope.value
        window = catch_quota_window(
            now,
            refresh_hours=self.refresh_hours,
            timezone_name=self.timezone_name,
        )
        now_text = iso_timestamp(now)
        window_start = iso_timestamp(window.start)
        window_end = iso_timestamp(window.end)
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_CHANCE_COMMAND,
                    request_payload=_CHANCE_REQUEST,
                )
                return self._result_from_receipt(existing, receipt_created=False)
            effect_row = await session.fetch_one(
                """
                SELECT effect_entry_id, source_food_instance_id, params_json
                FROM player_food_effects
                WHERE player_id = ?
                  AND effect_id = ?
                  AND consumed_uses < granted_uses
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at, effect_entry_id
                LIMIT 1
                """,
                (identity.player_id, QUOTA_RESET_CHANCE, now_text),
            )
            if effect_row is None:
                raise DomainValidationError(
                    "你没有可用的重置额度机会；食用六星菜“糖醋排骨”可获得一次，"
                    "发送 /重置额度 即可使用。"
                )
            effect_grant = resolve_food_effect(
                QUOTA_RESET_CHANCE,
                json.loads(str(effect_row["params_json"] or "{}")),
            )
            group_dedicated_catches = int(
                effect_grant.params.get("group_dedicated_catches") or 0
            )
            group_coin_reward = int(effect_grant.params.get("group_coin") or 0)
            five_star_multiplier = float(
                effect_grant.params.get("five_star_multiplier") or 1.0
            )
            six_star_multiplier = float(
                effect_grant.params.get("six_star_multiplier") or 1.0
            )
            hidden_boost_chance_percent = float(
                effect_grant.params.get("hidden_boost_chance_percent") or 0.0
            )
            hidden_five_star_multiplier = float(
                effect_grant.params.get("hidden_five_star_multiplier") or 1.0
            )
            hidden_six_star_multiplier = float(
                effect_grant.params.get("hidden_six_star_multiplier") or 1.0
            )
            enhanced_group_reset = bool(
                group_dedicated_catches
                or group_coin_reward
                or five_star_multiplier > 1.0
                or six_star_multiplier > 1.0
                or hidden_boost_chance_percent > 0.0
            )
            group_effect_entry_id = self.id_factory() if enhanced_group_reset else ""
            group_effect_expires_at = (
                iso_timestamp(window.start + timedelta(days=1))
                if enhanced_group_reset
                else ""
            )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now_text,
            )
            effective_start = await self.repository.effective_window_start(
                session,
                scope_id=scope_id,
                window_start=window_start,
                window_end=window_end,
            )
            cleared_catches, affected_players = await self.repository.usage_since(
                session,
                scope_id=scope_id,
                effective_start=effective_start,
                window_end=window_end,
            )
            detail = self._reset_detail(
                source="quota-chance",
                window_start=window_start,
                window_end=window_end,
                previous_effective_start=effective_start,
                cleared_catches=cleared_catches,
                affected_players=affected_players,
                backup_path=backup_path,
            )
            scoped_players = (
                await self.economy_repository.players_in_scope(
                    session,
                    scope_id=scope_id,
                )
                if enhanced_group_reset
                else []
            )
            group_rewarded_players = (
                len(scoped_players) if group_coin_reward > 0 else 0
            )
            detail.update(
                {
                    "group_effect_entry_id": group_effect_entry_id,
                    "group_effect_expires_at": group_effect_expires_at,
                    "group_rewarded_players": group_rewarded_players,
                    "group_coin_reward": group_coin_reward,
                    "group_dedicated_catches": group_dedicated_catches,
                    "five_star_multiplier": five_star_multiplier,
                    "six_star_multiplier": six_star_multiplier,
                    "hidden_boost_chance_percent": hidden_boost_chance_percent,
                    "hidden_five_star_multiplier": hidden_five_star_multiplier,
                    "hidden_six_star_multiplier": hidden_six_star_multiplier,
                }
            )
            result_payload = {
                "audit_event_id": audit_event_id,
                "scope_id": scope_id,
                "window_start": window_start,
                "window_end": window_end,
                "window_label": window.label,
                "next_refresh_label": window.next_refresh_label,
                "cleared_catches": cleared_catches,
                "affected_players": affected_players,
                "backup_path": str(backup_path),
                "created_at": now_text,
                "group_rewarded_players": group_rewarded_players,
                "group_coin_reward": group_coin_reward,
                "group_dedicated_catches": group_dedicated_catches,
                "group_effect_expires_at": group_effect_expires_at,
                "hidden_boost_chance_percent": hidden_boost_chance_percent,
                "hidden_five_star_multiplier": hidden_five_star_multiplier,
                "hidden_six_star_multiplier": hidden_six_star_multiplier,
            }
            summary = self._command_summary(
                identity=identity,
                window=window,
                cleared_catches=cleared_catches,
                affected_players=affected_players,
                group_rewarded_players=group_rewarded_players,
                group_coin_reward=group_coin_reward,
                group_dedicated_catches=group_dedicated_catches,
                five_star_multiplier=five_star_multiplier,
                six_star_multiplier=six_star_multiplier,
                group_effect_expires_at=group_effect_expires_at,
                hidden_boost_chance_percent=hidden_boost_chance_percent,
                hidden_five_star_multiplier=hidden_five_star_multiplier,
                hidden_six_star_multiplier=hidden_six_star_multiplier,
            )
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=scope_id,
                player_id=identity.player_id,
                command_name=_CHANCE_COMMAND,
                request_fingerprint=request_fingerprint(_CHANCE_REQUEST),
                result_type="catch-quota-reset",
                result_object_id=audit_event_id,
                result_json=json.dumps(
                    result_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=summary,
                now=now_text,
            )
            if not reservation.created:
                validate_existing_receipt(
                    reservation.receipt,
                    identity=identity,
                    command_name=_CHANCE_COMMAND,
                    request_payload=_CHANCE_REQUEST,
                )
                return self._result_from_receipt(
                    reservation.receipt,
                    receipt_created=False,
                )
            await self.repository.insert_reset_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=scope_id,
                actor_user_id=identity.user_id,
                object_id=window_start,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=now_text,
            )
            if enhanced_group_reset:
                group_params = {
                    "coin_per_player": group_coin_reward,
                    "dedicated_catches": group_dedicated_catches,
                    "five_star_multiplier": five_star_multiplier,
                    "six_star_multiplier": six_star_multiplier,
                    "source_label": "糖醋排骨",
                }
                if hidden_boost_chance_percent > 0.0:
                    group_params.update(
                        {
                            "hidden_boost_chance_percent": (
                                hidden_boost_chance_percent
                            ),
                            "hidden_five_star_multiplier": (
                                hidden_five_star_multiplier
                            ),
                            "hidden_six_star_multiplier": (
                                hidden_six_star_multiplier
                            ),
                        }
                    )
                await self.economy_repository.insert_group_food_effect(
                    session,
                    group_effect_entry_id=group_effect_entry_id,
                    scope_id=scope_id,
                    source_player_id=identity.player_id,
                    source_food_instance_id=str(
                        effect_row["source_food_instance_id"]
                    ),
                    effect_id=GROUP_WINDOW_HIGH_STAR_BOOST,
                    params_json=json.dumps(
                        group_params,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    granted_uses_per_player=group_dedicated_catches,
                    starts_at=now_text,
                    expires_at=group_effect_expires_at,
                    now=now_text,
                )
                for index, player in enumerate(scoped_players):
                    if group_coin_reward <= 0:
                        break
                    balance = await self.economy_repository.apply_currency_change(
                        session,
                        player_id=str(player["player_id"]),
                        scope_id=scope_id,
                        amount=group_coin_reward,
                        reason_code="quota-reset-group-food-effect",
                        reason_text="糖醋排骨重置额度全群奖励",
                        source_object_type="quota-reset",
                        source_object_id=audit_event_id,
                        ledger_entry_id=self.id_factory(),
                        idempotency_key=f"{idempotency_key}:group-coin:{index}",
                        now=now_text,
                    )
                    if balance is None:
                        raise RuntimeError("糖醋排骨全群猪币奖励写入失败。")
            cursor = await session.execute(
                """
                UPDATE player_food_effects
                SET consumed_uses = consumed_uses + 1,
                    updated_at = ?
                WHERE effect_entry_id = ?
                  AND player_id = ?
                  AND consumed_uses < granted_uses
                """,
                (now_text, str(effect_row["effect_entry_id"]), identity.player_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("重置额度机会状态已变化，本次操作未结算。")
        return CatchQuotaResetResult(
            audit_event_id=audit_event_id,
            scope_id=scope_id,
            window=window,
            cleared_catches=cleared_catches,
            affected_players=affected_players,
            backup_path=backup_path,
            created_at=now_text,
            receipt=reservation.receipt,
            receipt_created=True,
            group_rewarded_players=group_rewarded_players,
            group_coin_reward=group_coin_reward,
            group_dedicated_catches=group_dedicated_catches,
            group_effect_expires_at=group_effect_expires_at,
            hidden_boost_chance_percent=hidden_boost_chance_percent,
            hidden_five_star_multiplier=hidden_five_star_multiplier,
            hidden_six_star_multiplier=hidden_six_star_multiplier,
        )

    def _reset_detail(
        self,
        *,
        source: str,
        window_start: str,
        window_end: str,
        previous_effective_start: str,
        cleared_catches: int,
        affected_players: int,
        backup_path: Path,
    ) -> dict[str, object]:
        return {
            "source": str(source or "").strip() or "manual",
            "ruleset_version": RULESET_VERSION,
            "window_start": window_start,
            "window_end": window_end,
            "previous_effective_start": previous_effective_start,
            "window_limit": self.window_limit,
            "refresh_hours": list(self.refresh_hours),
            "cleared_catches": cleared_catches,
            "affected_players": affected_players,
            "cooldown_cleared": True,
            "backup_path": str(Path(backup_path).resolve()),
        }

    def _command_summary(
        self,
        *,
        identity: CommandIdentity,
        window: CatchQuotaWindow,
        cleared_catches: int,
        affected_players: int,
        group_rewarded_players: int = 0,
        group_coin_reward: int = 0,
        group_dedicated_catches: int = 0,
        five_star_multiplier: float = 1.0,
        six_star_multiplier: float = 1.0,
        group_effect_expires_at: str = "",
        hidden_boost_chance_percent: float = 0.0,
        hidden_five_star_multiplier: float = 1.0,
        hidden_six_star_multiplier: float = 1.0,
    ) -> str:
        group_label = identity.group_name or "当前群"
        group_bonus = ""
        if group_effect_expires_at:
            hidden_bonus = ""
            if hidden_boost_chance_percent > 0.0:
                hidden_bonus = (
                    f"每次专属抓猪有 {hidden_boost_chance_percent:g}% 概率爆发为 "
                    f"×{hidden_five_star_multiplier:g}/×{hidden_six_star_multiplier:g}；"
                )
            group_bonus = (
                f"\n糖醋排骨全群强化：{group_rewarded_players} 名已登记玩家各 +"
                f"{group_coin_reward} 猪币、各获 {group_dedicated_catches} 次专属抓猪额度；"
                f"5 星/6 星相对权重 ×{five_star_multiplier:g}/×{six_star_multiplier:g}；"
                f"{hidden_bonus}有效至 {group_effect_expires_at}。"
            )
        return (
            "【抓猪次数已重置】\n"
            f"群：{group_label}\n"
            f"时段：{window.label}\n"
            f"已归零：{cleared_catches} 次，涉及 {affected_players} 名玩家\n"
            f"基础额度：{self.window_limit} 次/人\n"
            f"历史抓取、资产和累计统计均已保留。{group_bonus}"
        )

    @staticmethod
    def _backup_path(
        *,
        data_dir: Path,
        platform: str,
        scope_id: str,
        now: datetime,
    ) -> Path:
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        scope_fingerprint = sha256(scope_id.encode("utf-8")).hexdigest()[:12]
        return (
            Path(data_dir).resolve()
            / "backups"
            / f"pig_catcher-pre-quota-reset-{platform}-{scope_fingerprint}-{timestamp}.sqlite3"
        ).resolve()

    async def _command_receipt(self, idempotency_key: str) -> CommandReceipt | None:
        async with self.database.transaction(immediate=False) as session:
            return await self.receipt_repository.get_by_key(session, idempotency_key)

    @staticmethod
    def _result_from_receipt(
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> CatchQuotaResetResult:
        payload = receipt_payload(receipt)
        try:
            window = CatchQuotaWindow(
                start=datetime.fromisoformat(
                    str(payload["window_start"]).replace("Z", "+00:00")
                ),
                end=datetime.fromisoformat(
                    str(payload["window_end"]).replace("Z", "+00:00")
                ),
                label=str(payload["window_label"]),
                next_refresh_label=str(payload["next_refresh_label"]),
            )
            return CatchQuotaResetResult(
                audit_event_id=str(payload["audit_event_id"]),
                scope_id=str(payload["scope_id"]),
                window=window,
                cleared_catches=int(payload["cleared_catches"]),
                affected_players=int(payload["affected_players"]),
                backup_path=Path(str(payload["backup_path"])).resolve(),
                created_at=str(payload["created_at"]),
                receipt=receipt,
                receipt_created=receipt_created,
                group_rewarded_players=int(
                    payload.get("group_rewarded_players") or 0
                ),
                group_coin_reward=int(payload.get("group_coin_reward") or 0),
                group_dedicated_catches=int(
                    payload.get("group_dedicated_catches") or 0
                ),
                group_effect_expires_at=str(
                    payload.get("group_effect_expires_at") or ""
                ),
                hidden_boost_chance_percent=float(
                    payload.get("hidden_boost_chance_percent") or 0.0
                ),
                hidden_five_star_multiplier=float(
                    payload.get("hidden_five_star_multiplier") or 1.0
                ),
                hidden_six_star_multiplier=float(
                    payload.get("hidden_six_star_multiplier") or 1.0
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReceiptConflictError("额度重置回执中的业务结果无法解析。") from exc

    async def _scope_exists(self, scope_id: str) -> bool:
        async with self.database.transaction(immediate=False) as session:
            return await self.repository.scope_exists(session, scope_id=scope_id)
