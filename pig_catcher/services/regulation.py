"""赠送与成交交易共用的自动监管状态机。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..config.access import normalized_id_set
from ..config.model import RegulationSection
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, SystemClock
from ..domain.regulation import TransferSignal, analyze_transfer_graph
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    FrameworkRepository,
    ReceiptRepository,
    RegulationRepository,
    SocialRepository,
)
from ..version import RULESET_VERSION
from .command_state import (
    iso_timestamp,
    receipt_payload,
    validate_existing_receipt,
)
from .receipts import request_fingerprint

_STATUS_RANK = {
    "watching": 0,
    "supervised": 1,
    "social-restricted": 2,
    "plugin-restricted": 3,
    "closed": 4,
    "dismissed": 4,
}
_REGULATION_RELEASE_COMMAND = "pig-catcher.admin-regulation-release"


def _parse_timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


@dataclass(frozen=True, slots=True)
class RegulationOutcome:
    """一次事务内监管决定；公开文本绝不包含内部评分或阈值。"""

    case_id: str
    blocked: bool
    stage: str
    notice_ids: tuple[str, ...] = ()
    hold_expires_at: str = ""
    public_message: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "blocked": self.blocked,
            "stage": self.stage,
            "notice_ids": list(self.notice_ids),
            "hold_expires_at": self.hold_expires_at,
            "public_message": self.public_message,
        }

    @classmethod
    def from_payload(cls, value: object) -> RegulationOutcome | None:
        if not isinstance(value, Mapping):
            return None
        return cls(
            case_id=str(value.get("case_id") or ""),
            blocked=bool(value.get("blocked")),
            stage=str(value.get("stage") or ""),
            notice_ids=tuple(
                str(item)
                for item in value.get("notice_ids", ())
                if str(item).strip()
            ),
            hold_expires_at=str(value.get("hold_expires_at") or ""),
            public_message=str(value.get("public_message") or ""),
        )


@dataclass(frozen=True, slots=True)
class RegulationNotice:
    notice_id: str
    case_id: str
    player_id: str
    display_name: str
    stage: str
    message_text: str


@dataclass(frozen=True, slots=True)
class RegulationHold:
    case_id: str
    player_id: str
    hold_type: str
    expires_at: str

    @property
    def public_message(self) -> str:
        operation = "抓猪插件" if self.hold_type == "plugin" else "赠送与交易功能"
        return (
            f"该账号的{operation}目前处于临时限制状态，预计于 "
            f"{self.expires_at} 自动恢复。如需复核，请联系插件管理员。"
        )


@dataclass(frozen=True, slots=True)
class RegulationCaseSummary:
    case_id: str
    status: str
    updated_at: str
    member_count: int
    active_hold_count: int


@dataclass(frozen=True, slots=True)
class RegulationCaseDetail:
    summary: RegulationCaseSummary
    members: tuple[dict[str, object], ...]
    holds: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class RegulationReleaseResult:
    receipt: CommandReceipt
    receipt_created: bool
    case_id: str
    released_hold_count: int
    already_closed: bool


class RegulationService:
    """生成可审计案件，并在同一 SQLite 事务中决定放行或阻断。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        config: RegulationSection,
        *,
        admin_user_ids: Iterable[str] = (),
        repository: RegulationRepository | None = None,
        social_repository: SocialRepository | None = None,
        receipt_repository: ReceiptRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.config = config
        self.repository = repository or RegulationRepository()
        self.social_repository = social_repository or SocialRepository()
        self.receipt_repository = receipt_repository or ReceiptRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.clock = clock or SystemClock()
        self.admin_user_ids = normalized_id_set(admin_user_ids)
        self.enabled_scope_ids = frozenset(config.enabled_scope_ids)

    def scope_enabled(self, scope_id: str) -> bool:
        return self.config.mode != "关闭" and scope_id in self.enabled_scope_ids

    def _is_protected_player(self, row: Mapping[str, object]) -> bool:
        user_id = str(row.get("platform_user_id") or "")
        scope_id = str(row.get("scope_id") or "")
        platform = scope_id.split(":", 1)[0].lower() if ":" in scope_id else ""
        return user_id in self.admin_user_ids or (
            bool(platform) and f"{platform}:{user_id}" in self.admin_user_ids
        )

    @staticmethod
    def _asset_key(row: Mapping[str, object]) -> str:
        instance_id = str(row["asset_instance_id"])
        if str(row["asset_kind"]) == "pig":
            return f"pig-lineage:{instance_id}"
        source_pig_id = str(row.get("source_pig_instance_id") or "")
        return (
            f"pig-lineage:{source_pig_id}"
            if source_pig_id
            else f"food:{instance_id}"
        )

    @classmethod
    def _signals_from_rows(
        cls,
        rows: Sequence[Mapping[str, object]],
    ) -> list[TransferSignal]:
        signals: list[TransferSignal] = []
        for row in rows:
            event_id = str(row["transfer_event_id"])
            transfer_type = str(row["transfer_type"])
            official_value = int(row.get("official_value") or 0)
            price_value = row.get("price")
            price = int(price_value) if price_value is not None else None
            created_at = _parse_timestamp(row["created_at"])
            signals.append(
                TransferSignal(
                    event_id=event_id,
                    from_player_id=str(row["from_player_id"]),
                    to_player_id=str(row["to_player_id"]),
                    asset_key=cls._asset_key(row),
                    channel="asset",
                    transfer_type=transfer_type,
                    created_at=created_at,
                    rarity=int(row.get("rarity") or 0),
                    official_value=official_value,
                    price=price,
                )
            )
            if (
                transfer_type == "trade"
                and price is not None
                and official_value > 0
                and price / official_value > 1.30
            ):
                signals.append(
                    TransferSignal(
                        event_id=f"{event_id}:coin",
                        from_player_id=str(row["to_player_id"]),
                        to_player_id=str(row["from_player_id"]),
                        asset_key=f"coin:{row.get('trade_id') or event_id}",
                        channel="coin",
                        transfer_type="trade",
                        created_at=created_at,
                        rarity=0,
                        official_value=official_value,
                        price=price,
                    )
                )
        return signals

    async def current_hold(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        player_ids: Sequence[str],
        hold_types: Sequence[str],
        now: str,
    ) -> RegulationHold | None:
        if not self.scope_enabled(scope_id):
            return None
        await self.repository.expire_holds(session, now=now)
        rows = await self.repository.active_holds(
            session,
            player_ids=player_ids,
            hold_types=hold_types,
            now=now,
        )
        rows = [row for row in rows if str(row["scope_id"]) == scope_id]
        if not rows:
            return None
        row = rows[0]
        return RegulationHold(
            case_id=str(row["case_id"]),
            player_id=str(row["player_id"]),
            hold_type=str(row["hold_type"]),
            expires_at=str(row["expires_at"]),
        )

    async def active_plugin_hold(
        self,
        identity: CommandIdentity,
    ) -> RegulationHold | None:
        if not self.scope_enabled(identity.scope.value):
            return None
        if identity.user_id in self.admin_user_ids or (
            f"{identity.scope.platform}:{identity.user_id}" in self.admin_user_ids
        ):
            return None
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            return await self.current_hold(
                session,
                scope_id=identity.scope.value,
                player_ids=(identity.player_id,),
                hold_types=("plugin",),
                now=now,
            )

    async def evaluate_transfer(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        source_operation_key: str,
        transfer_event_id: str,
        from_player_id: str,
        to_player_id: str,
        asset_kind: str,
        asset_instance_id: str,
        rarity: int,
        official_value: int,
        transfer_type: str,
        price: int | None,
        active_player_ids: Sequence[str],
        now: str,
    ) -> RegulationOutcome | None:
        if not self.scope_enabled(scope_id):
            return None
        current_hold = await self.current_hold(
            session,
            scope_id=scope_id,
            player_ids=active_player_ids,
            hold_types=("social", "plugin"),
            now=now,
        )
        if current_hold is not None:
            return RegulationOutcome(
                case_id=current_hold.case_id,
                blocked=True,
                stage=f"active-{current_hold.hold_type}-restriction",
                hold_expires_at=current_hold.expires_at,
                public_message=current_hold.public_message,
            )

        now_datetime = _parse_timestamp(now)
        since = iso_timestamp(now_datetime - timedelta(days=self.config.lookback_days))
        rows = await self.repository.transfer_rows(session, scope_id=scope_id, since=since)
        signals = self._signals_from_rows(rows)
        asset_key = await self.repository.asset_lineage(
            session,
            asset_kind=asset_kind,
            asset_instance_id=asset_instance_id,
        )
        proposed_asset = TransferSignal(
            event_id=transfer_event_id,
            from_player_id=from_player_id,
            to_player_id=to_player_id,
            asset_key=asset_key,
            channel="asset",
            transfer_type=transfer_type,
            created_at=now_datetime,
            rarity=rarity,
            official_value=official_value,
            price=price,
        )
        signals.append(proposed_asset)
        anchor_ids = {transfer_event_id}
        if (
            transfer_type == "trade"
            and price is not None
            and official_value > 0
            and price / official_value > 1.30
        ):
            coin_event_id = f"{transfer_event_id}:coin"
            signals.append(
                TransferSignal(
                    event_id=coin_event_id,
                    from_player_id=to_player_id,
                    to_player_id=from_player_id,
                    asset_key=f"coin:{source_operation_key}",
                    channel="coin",
                    transfer_type="trade",
                    created_at=now_datetime,
                    rarity=0,
                    official_value=official_value,
                    price=price,
                )
            )
            anchor_ids.add(coin_event_id)
        analysis = analyze_transfer_graph(
            tuple(signals),
            anchor_event_ids=frozenset(anchor_ids),
        )
        if analysis is None or analysis.score < self.config.warning_score:
            return None

        targets = analysis.target_player_ids
        signature = hashlib.sha256("\x1f".join(targets).encode("utf-8")).hexdigest()[:24]
        target_json = json.dumps(targets, ensure_ascii=False)
        evidence = {
            "targets": targets,
            "upstream": analysis.upstream_player_ids,
            "sources": analysis.source_player_ids,
            "relays": analysis.relay_player_ids,
            "path_event_ids": analysis.path_event_ids,
            "unique_asset_count": analysis.unique_asset_count,
            "concentration_percent": analysis.concentration_percent,
            "active_day_count": analysis.active_day_count,
            "max_path_depth": analysis.max_path_depth,
            "high_rarity_asset_count": analysis.high_rarity_asset_count,
            "six_star_asset_count": analysis.six_star_asset_count,
            "price_anomaly_level": analysis.price_anomaly_level,
            "source_operation_key": source_operation_key,
            "transfer_type": transfer_type,
            "evaluated_at": now,
        }
        evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        related_since = iso_timestamp(now_datetime - timedelta(days=30))
        incident = await self.repository.latest_related_case(
            session,
            scope_id=scope_id,
            target_player_ids=targets,
            target_signature=signature,
            since=related_since,
        )
        if incident is None:
            case_id = uuid4().hex
            case_status = "watching"
            await self.repository.insert_case(
                session,
                case_id=case_id,
                scope_id=scope_id,
                target_signature=signature,
                target_player_ids_json=target_json,
                score=analysis.score,
                ruleset_version=RULESET_VERSION,
                evidence_json=evidence_json,
                now=now,
            )
        else:
            case_id = str(incident["case_id"])
            case_status = str(incident["status"])

        active_ids = set(_unique(active_player_ids))
        source_ids = set(analysis.source_player_ids)
        relay_ids = set(analysis.relay_player_ids)
        target_ids = set(targets)
        all_members = set(analysis.upstream_player_ids) | target_ids | active_ids
        for player_id in sorted(all_members):
            if transfer_type == "trade" and player_id in active_ids:
                role = "active-trader"
            elif player_id in relay_ids:
                role = "relay"
            elif player_id in source_ids:
                role = "source"
            else:
                role = "target"
            await self.repository.upsert_member(
                session,
                case_id=case_id,
                player_id=player_id,
                role=role,
                active_participant=player_id in active_ids,
                now=now,
            )

        player_rows = await self.repository.player_rows(
            session,
            player_ids=tuple(sorted(all_members)),
        )
        row_by_player = {str(row["player_id"]): row for row in player_rows}
        protected_ids = {
            player_id
            for player_id, row in row_by_player.items()
            if self._is_protected_player(row)
        }
        warning_player_ids = set(analysis.upstream_player_ids)
        if transfer_type == "trade":
            warning_player_ids.update(active_ids)

        notice_ids: list[str] = []
        current_unwarned = False
        for player_id in sorted(warning_player_ids):
            member = await self.repository.member(
                session,
                case_id=case_id,
                player_id=player_id,
            )
            if member is None or member.get("warning_served_at"):
                continue
            row = row_by_player.get(player_id, {})
            display_name = str(row.get("display_name") or "相关玩家")
            notice_id = await self.repository.insert_notice(
                session,
                notice_id=uuid4().hex,
                case_id=case_id,
                player_id=player_id,
                stage="warning",
                incident_number=0,
                message_text=(
                    f"【行为提醒】{display_name}，请注意近期在抓猪插件中的资产流转行为。"
                    "请停止可能造成异常集中流转的操作；若类似行为持续，相关功能可能被暂时限制。"
                ),
                source_operation_key=source_operation_key,
                now=now,
            )
            notice_ids.append(notice_id)
            if player_id in active_ids and player_id not in protected_ids:
                current_unwarned = True

        await self.repository.update_case(
            session,
            case_id=case_id,
            target_signature=signature,
            target_player_ids_json=target_json,
            status=case_status,
            score=max(analysis.score, int(incident["score"]) if incident else 0),
            evidence_json=evidence_json,
            now=now,
        )
        await self.repository.insert_event(
            session,
            event_id=uuid4().hex,
            case_id=case_id,
            scope_id=scope_id,
            player_id=from_player_id,
            event_type="threshold-observed",
            score=analysis.score,
            payload_json=evidence_json,
            now=now,
        )

        if self.config.mode == "仅提醒" or current_unwarned:
            return RegulationOutcome(
                case_id=case_id,
                blocked=False,
                stage="warning",
                notice_ids=tuple(dict.fromkeys(notice_ids)),
            )

        actionable_ids = tuple(
            player_id
            for player_id in _unique(active_player_ids)
            if player_id not in protected_ids
        )
        if not actionable_ids:
            return RegulationOutcome(
                case_id=case_id,
                blocked=False,
                stage="protected",
                notice_ids=tuple(dict.fromkeys(notice_ids)),
            )
        members = [
            await self.repository.member(session, case_id=case_id, player_id=player_id)
            for player_id in actionable_ids
        ]
        if any(member is None or not member.get("warning_served_at") for member in members):
            return RegulationOutcome(
                case_id=case_id,
                blocked=False,
                stage="warning",
                notice_ids=tuple(dict.fromkeys(notice_ids)),
            )

        cooldown = timedelta(minutes=self.config.notice_cooldown_minutes)
        incident_counts: dict[str, int] = {}
        for player_id, member in zip(actionable_ids, members, strict=True):
            if member is None:
                continue
            last_incident = member.get("last_incident_at")
            if (
                last_incident
                and now_datetime - _parse_timestamp(last_incident) < cooldown
            ):
                incident_counts[player_id] = int(member["incident_count"])
                continue
            incident_counts[player_id] = await self.repository.increment_incident(
                session,
                case_id=case_id,
                player_id=player_id,
                now=now,
            )

        stage_by_player: dict[str, str] = {}
        for player_id, count in incident_counts.items():
            if count >= 3:
                stage_by_player[player_id] = "plugin-restriction"
            elif count >= 2:
                stage_by_player[player_id] = "social-restriction"
            else:
                stage_by_player[player_id] = "supervision"
        stage = max(
            stage_by_player.values(),
            key=lambda value: {
                "supervision": 1,
                "social-restriction": 2,
                "plugin-restriction": 3,
            }[value],
        )
        case_status = {
            "supervision": "supervised",
            "social-restriction": "social-restricted",
            "plugin-restriction": "plugin-restricted",
        }[stage]
        if incident is not None and (
            _STATUS_RANK.get(str(incident["status"]), 0) > _STATUS_RANK[case_status]
        ):
            case_status = str(incident["status"])

        hold_expires_by_player: dict[str, str] = {}
        hold_type_by_player: dict[str, str] = {}
        held_player_ids: list[str] = []
        for player_id, player_stage in stage_by_player.items():
            player_incident = incident_counts[player_id]
            if player_stage == "supervision":
                continue
            hold_type = "plugin" if player_stage == "plugin-restriction" else "social"
            if hold_type == "social":
                duration = timedelta(hours=self.config.social_hold_hours)
                sequence = max(1, player_incident - 1)
            else:
                history_90 = await self.repository.plugin_hold_history(
                    session,
                    player_id=player_id,
                    since=iso_timestamp(now_datetime - timedelta(days=90)),
                )
                history_30 = [
                    item
                    for item in history_90
                    if _parse_timestamp(item["starts_at"])
                    >= now_datetime - timedelta(days=30)
                ]
                if len(history_90) >= 2:
                    duration = timedelta(days=self.config.severe_repeat_ban_days)
                elif history_30:
                    duration = timedelta(days=self.config.repeat_ban_days)
                else:
                    duration = timedelta(hours=self.config.plugin_hold_hours)
                sequence = len(history_90) + 1
            expires = iso_timestamp(now_datetime + duration)
            hold_expires_by_player[player_id] = expires
            hold_type_by_player[player_id] = hold_type
            held_player_ids.append(player_id)
            await self.repository.insert_hold(
                session,
                hold_id=uuid4().hex,
                case_id=case_id,
                player_id=player_id,
                hold_type=hold_type,
                sequence_number=sequence,
                starts_at=now,
                expires_at=expires,
                reason="自动监管临时限制；永久封禁必须人工复核",
                now=now,
            )
        hold_expires_at = max(hold_expires_by_player.values(), default="")
        if held_player_ids:
            await self.social_repository.cancel_pending_offers_for_players(
                session,
                scope_id=scope_id,
                player_ids=held_player_ids,
                now=now,
            )

        stage_message = {
            "supervision": (
                "本次操作未执行。系统已对相关账号启动行为监管，请停止可能造成异常集中流转的操作。"
            ),
            "social-restriction": (
                f"本次操作未执行，相关账号的赠送与交易功能已被临时限制至 {hold_expires_at}。"
                "如需复核，请联系插件管理员。"
            ),
            "plugin-restriction": (
                f"本次操作未执行，相关账号的抓猪插件访问已被临时限制至 {hold_expires_at}。"
                "如需复核，请联系插件管理员。"
            ),
        }[stage]
        for player_id in actionable_ids:
            row = row_by_player.get(player_id, {})
            display_name = str(row.get("display_name") or "相关玩家")
            player_stage = stage_by_player[player_id]
            player_expiry = hold_expires_by_player.get(player_id, "")
            player_message = {
                "supervision": (
                    "本次操作未执行。系统已启动行为监管，请停止可能造成异常集中流转的操作。"
                ),
                "social-restriction": (
                    f"本次操作未执行，赠送与交易功能已被临时限制至 {player_expiry}。"
                    "如需复核，请联系插件管理员。"
                ),
                "plugin-restriction": (
                    f"本次操作未执行，抓猪插件访问已被临时限制至 {player_expiry}。"
                    "如需复核，请联系插件管理员。"
                ),
            }[player_stage]
            notice_id = await self.repository.insert_notice(
                session,
                notice_id=uuid4().hex,
                case_id=case_id,
                player_id=player_id,
                stage=player_stage,
                incident_number=incident_counts[player_id],
                message_text=f"【行为监管】{display_name}：{player_message}",
                source_operation_key=source_operation_key,
                now=now,
            )
            notice_ids.append(notice_id)

        await self.repository.update_case(
            session,
            case_id=case_id,
            target_signature=signature,
            target_player_ids_json=target_json,
            status=case_status,
            score=max(analysis.score, int(incident["score"]) if incident else 0),
            evidence_json=evidence_json,
            now=now,
        )
        await self.repository.insert_event(
            session,
            event_id=uuid4().hex,
            case_id=case_id,
            scope_id=scope_id,
            player_id=from_player_id,
            event_type=stage,
            score=analysis.score,
            payload_json=json.dumps(
                {
                    "source_operation_key": source_operation_key,
                    "active_player_ids": actionable_ids,
                    "incident_counts": incident_counts,
                    "hold_types": hold_type_by_player,
                    "hold_expires_at": hold_expires_by_player,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            now=now,
        )
        return RegulationOutcome(
            case_id=case_id,
            blocked=True,
            stage=stage,
            notice_ids=tuple(dict.fromkeys(notice_ids)),
            hold_expires_at=hold_expires_at,
            public_message=stage_message,
        )

    async def claim_notice(self, notice_id: str) -> RegulationNotice | None:
        now_datetime = self.clock.now()
        now = iso_timestamp(now_datetime)
        stale_before = iso_timestamp(
            now_datetime - timedelta(minutes=self.config.notice_cooldown_minutes)
        )
        async with self.database.transaction() as session:
            await self.repository.requeue_stale_claimed_notice(
                session,
                notice_id=notice_id,
                stale_before=stale_before,
                now=now,
            )
            claimed = await self.repository.claim_notice(
                session,
                notice_id=notice_id,
                now=now,
            )
            if not claimed:
                return None
            row = await self.repository.notice(session, notice_id=notice_id)
            if row is None:
                return None
            return RegulationNotice(
                notice_id=notice_id,
                case_id=str(row["case_id"]),
                player_id=str(row["player_id"]),
                display_name=str(row["display_name"]),
                stage=str(row["stage"]),
                message_text=str(row["message_text"]),
            )

    async def mark_notice_sent(self, notice_id: str) -> bool:
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            row = await self.repository.mark_notice_sent(
                session,
                notice_id=notice_id,
                now=now,
            )
            if row is None:
                return False
            if str(row["stage"]) == "warning":
                await self.repository.mark_warning_served(
                    session,
                    case_id=str(row["case_id"]),
                    player_id=str(row["player_id"]),
                    now=now,
                )
            return True

    async def mark_notice_failed(self, notice_id: str, error_text: str) -> bool:
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            return await self.repository.mark_notice_failed(
                session,
                notice_id=notice_id,
                error_text=error_text,
                now=now,
            )

    async def list_cases(
        self,
        *,
        scope_id: str,
        limit: int = 10,
    ) -> tuple[RegulationCaseSummary, ...]:
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.repository.expire_holds(session, now=now)
            rows = await self.repository.list_cases(
                session,
                scope_id=scope_id,
                limit=max(1, min(limit, 30)),
            )
        return tuple(self._summary(row) for row in rows)

    async def case_detail(
        self,
        *,
        scope_id: str,
        case_id_prefix: str,
    ) -> RegulationCaseDetail:
        async with self.database.transaction(immediate=False) as session:
            rows = await self.repository.case_by_id_prefix(
                session,
                scope_id=scope_id,
                case_id_prefix=case_id_prefix,
            )
            if not rows:
                raise ValueError("当前群找不到该监管案件号。")
            if len(rows) > 1:
                raise ValueError("案件号前缀不唯一，请输入更多位。")
            row = rows[0]
            members = await self.repository.members(session, case_id=str(row["case_id"]))
            holds = await self.repository.holds_for_case(session, case_id=str(row["case_id"]))
        return RegulationCaseDetail(
            summary=self._summary(row),
            members=tuple(members),
            holds=tuple(holds),
        )

    async def release_case(
        self,
        *,
        identity: CommandIdentity,
        case_id_prefix: str,
        reason: str,
    ) -> RegulationReleaseResult:
        now = iso_timestamp(self.clock.now())
        request_payload = {
            "command_version": 1,
            "case_id_prefix": case_id_prefix.strip(),
            "reason": str(reason or "管理员人工复核解除")[:300],
        }
        idempotency_key = MessageKeyFactory.build(
            identity,
            _REGULATION_RELEASE_COMMAND,
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
                    command_name=_REGULATION_RELEASE_COMMAND,
                    request_payload=request_payload,
                )
                payload = receipt_payload(existing)
                return RegulationReleaseResult(
                    receipt=existing,
                    receipt_created=False,
                    case_id=str(payload["case_id"]),
                    released_hold_count=int(payload["released_hold_count"]),
                    already_closed=bool(payload["already_closed"]),
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            rows = await self.repository.case_by_id_prefix(
                session,
                scope_id=identity.scope.value,
                case_id_prefix=case_id_prefix,
            )
            if not rows:
                raise ValueError("当前群找不到该监管案件号。")
            if len(rows) > 1:
                raise ValueError("案件号前缀不唯一，请输入更多位。")
            row = rows[0]
            case_id = str(row["case_id"])
            released = await self.repository.release_case_holds(
                session,
                case_id=case_id,
                now=now,
            )
            changed = await self.repository.close_case(
                session,
                case_id=case_id,
                status="dismissed",
                now=now,
            )
            if changed:
                audit_payload = json.dumps(
                    {
                        "actor_user_id": identity.user_id,
                        "reason": str(reason or "管理员人工复核解除")[:300],
                        "released_hold_count": released,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                await self.repository.insert_event(
                    session,
                    event_id=uuid4().hex,
                    case_id=case_id,
                    scope_id=identity.scope.value,
                    player_id=None,
                    event_type="admin-release",
                    score=int(row["score"]),
                    payload_json=audit_payload,
                    now=now,
                )
                await self.repository.insert_admin_audit(
                    session,
                    audit_event_id=uuid4().hex,
                    scope_id=identity.scope.value,
                    actor_user_id=identity.user_id,
                    case_id=case_id,
                    detail_json=audit_payload,
                    now=now,
                )
            result_payload = {
                "case_id": case_id,
                "released_hold_count": released,
                "already_closed": not changed,
            }
            if changed:
                text_summary = (
                    f"【猪管·监管解除完成】\n案件号：{case_id[:12]}\n"
                    f"已解除临时限制：{released} 项\n"
                    f"复核原因：{request_payload['reason'][:120]}"
                )
            else:
                text_summary = (
                    f"【猪管·监管解除】案件 {case_id[:12]} 已经关闭，无需重复操作。"
                )
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=_REGULATION_RELEASE_COMMAND,
                request_fingerprint=request_fingerprint(request_payload),
                result_type="regulation-case-released",
                result_object_id=case_id,
                result_json=json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
                text_summary=text_summary,
                now=now,
            )
            return RegulationReleaseResult(
                receipt=reservation.receipt,
                receipt_created=reservation.created,
                case_id=case_id,
                released_hold_count=released,
                already_closed=not changed,
            )

    @staticmethod
    def _summary(row: Mapping[str, Any]) -> RegulationCaseSummary:
        return RegulationCaseSummary(
            case_id=str(row["case_id"]),
            status=str(row["status"]),
            updated_at=str(row["updated_at"]),
            member_count=int(row.get("member_count") or 0),
            active_hold_count=int(row.get("active_hold_count") or 0),
        )


__all__ = [
    "RegulationCaseDetail",
    "RegulationCaseSummary",
    "RegulationHold",
    "RegulationNotice",
    "RegulationOutcome",
    "RegulationReleaseResult",
    "RegulationService",
]
