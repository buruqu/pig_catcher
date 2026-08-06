"""玩家处罚限制的批量应用服务。"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ..domain.errors import DomainValidationError
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import RestrictionRepository, SocialRepository
from ..infrastructure.repositories.restrictions import (
    CATCH_WINDOW_LIMIT,
    GIFT_TRANSFER_BAN,
    TRADE_BAN,
)
from .command_state import iso_timestamp


@dataclass(frozen=True, slots=True)
class RestrictionBatchResult:
    """一次已提交的玩家处罚批次。"""

    batch_id: str
    scope_id: str
    player_ids: tuple[str, ...]
    display_names: tuple[str, ...]
    starts_at: str
    catch_limit_expires_at: str
    catch_window_limit: int
    cancelled_pending_trades: int
    backup_path: Path


@dataclass(frozen=True, slots=True)
class SocialBlacklistUpdateResult:
    """One audited add/remove operation for the two social blacklists."""

    operation_id: str
    audit_event_id: str
    scope_id: str
    platform: str
    group_name: str
    player_ids: tuple[str, ...]
    platform_user_ids: tuple[str, ...]
    display_names: tuple[str, ...]
    gift_action: str
    trade_action: str
    gift_rows_changed: int
    trade_rows_changed: int
    cancelled_pending_trades: int
    backup_path: Path


@dataclass(frozen=True, slots=True)
class AnnouncementClaim:
    """A durable claim created before one panel-controlled send attempt."""

    announcement_id: str
    audit_event_id: str
    scope_id: str
    platform: str
    group_name: str
    stream_id: str
    content: str
    content_sha256: str
    created_at: str


class RestrictionAdminService:
    """原子写入可到期处罚并记录审计事件。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        restriction_repository: RestrictionRepository | None = None,
        social_repository: SocialRepository | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.restriction_repository = restriction_repository or RestrictionRepository()
        self.social_repository = social_repository or SocialRepository()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)

    async def apply_batch(
        self,
        *,
        scope_id: str,
        player_ids: Sequence[str],
        duration: timedelta,
        catch_window_limit: int,
        reason: str,
        source: str,
        created_by: str,
        backup_path: Path,
    ) -> RestrictionBatchResult:
        normalized_scope = str(scope_id or "").strip()
        normalized_players = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in player_ids
                if str(value).strip()
            )
        )
        if not normalized_scope or not normalized_players:
            raise DomainValidationError("处罚必须指定精确群范围和至少一名玩家。")
        if duration <= timedelta(0):
            raise DomainValidationError("处罚时长必须大于零。")
        if catch_window_limit < 0:
            raise DomainValidationError("抓猪时段额度不能小于零。")

        now_datetime = self.clock.now()
        starts_at = iso_timestamp(now_datetime)
        catch_limit_expires_at = iso_timestamp(now_datetime + duration)
        batch_id = self.id_factory()
        async with self.database.transaction() as session:
            players = await self.restriction_repository.players_in_scope(
                session,
                scope_id=normalized_scope,
                player_ids=normalized_players,
            )
            found = {str(player["player_id"]) for player in players}
            missing = sorted(set(normalized_players) - found)
            if missing:
                raise DomainValidationError(
                    "以下玩家不属于指定群范围，处罚未写入：" + "、".join(missing)
                )

            for player_id in normalized_players:
                await self.restriction_repository.upsert_restriction(
                    session,
                    restriction_id=self.id_factory(),
                    player_id=player_id,
                    restriction_type=GIFT_TRANSFER_BAN,
                    limit_value=None,
                    starts_at=starts_at,
                    expires_at=None,
                    reason=reason,
                    source=source,
                    created_by=created_by,
                    now=starts_at,
                )
                await self.restriction_repository.upsert_restriction(
                    session,
                    restriction_id=self.id_factory(),
                    player_id=player_id,
                    restriction_type=TRADE_BAN,
                    limit_value=None,
                    starts_at=starts_at,
                    expires_at=None,
                    reason=reason,
                    source=source,
                    created_by=created_by,
                    now=starts_at,
                )
                await self.restriction_repository.upsert_restriction(
                    session,
                    restriction_id=self.id_factory(),
                    player_id=player_id,
                    restriction_type=CATCH_WINDOW_LIMIT,
                    limit_value=catch_window_limit,
                    starts_at=starts_at,
                    expires_at=catch_limit_expires_at,
                    reason=reason,
                    source=source,
                    created_by=created_by,
                    now=starts_at,
                )

            cancelled = await self.social_repository.cancel_pending_offers_for_players(
                session,
                scope_id=normalized_scope,
                player_ids=normalized_players,
                now=starts_at,
            )
            detail = {
                "batch_id": batch_id,
                "players": [
                    {
                        "player_id": str(player["player_id"]),
                        "platform_user_id": str(player["platform_user_id"]),
                        "display_name": str(player["display_name"]),
                    }
                    for player in players
                ],
                "restriction_types": [
                    GIFT_TRANSFER_BAN,
                    TRADE_BAN,
                    CATCH_WINDOW_LIMIT,
                ],
                "catch_window_limit": catch_window_limit,
                "starts_at": starts_at,
                "gift_transfer_ban_expires_at": None,
                "trade_ban_expires_at": None,
                "catch_limit_expires_at": catch_limit_expires_at,
                "reason": reason,
                "source": source,
                "created_by": created_by,
                "cancelled_pending_trades": cancelled,
                "backup_path": str(Path(backup_path).resolve()),
            }
            await self.restriction_repository.insert_audit_event(
                session,
                audit_event_id=self.id_factory(),
                scope_id=normalized_scope,
                actor_user_id=created_by,
                object_id=batch_id,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=starts_at,
            )

        by_id = {str(player["player_id"]): player for player in players}
        return RestrictionBatchResult(
            batch_id=batch_id,
            scope_id=normalized_scope,
            player_ids=normalized_players,
            display_names=tuple(
                str(by_id[player_id]["display_name"])
                for player_id in normalized_players
            ),
            starts_at=starts_at,
            catch_limit_expires_at=catch_limit_expires_at,
            catch_window_limit=catch_window_limit,
            cancelled_pending_trades=cancelled,
            backup_path=Path(backup_path),
        )

    async def backup_and_update_social_blacklists(
        self,
        *,
        data_dir: Path,
        group_id: str,
        platform: str,
        user_ids: Sequence[str],
        gift_action: str,
        trade_action: str,
        reason: str,
        source: str,
        created_by: str,
    ) -> SocialBlacklistUpdateResult:
        """Validate targets, back up online, then atomically update both lists."""

        normalized_group_id = str(group_id or "").strip()
        normalized_platform = str(platform or "").strip().lower()
        normalized_gift_action = self._normalize_blacklist_action(gift_action)
        normalized_trade_action = self._normalize_blacklist_action(trade_action)
        if not normalized_group_id:
            raise DomainValidationError("黑名单管理必须指定精确群号。")
        if normalized_gift_action == "none" and normalized_trade_action == "none":
            raise DomainValidationError("黑名单管理至少需要一种加入或解除动作。")

        scope, players, platform_user_ids = await self._resolve_targets(
            group_id=normalized_group_id,
            platform=normalized_platform,
            user_ids=user_ids,
        )
        now_datetime = self.clock.now()
        backup_path = self._blacklist_backup_path(
            data_dir=Path(data_dir),
            scope_id=str(scope["scope_id"]),
            now=now_datetime,
        )
        await self.database.backup_to(backup_path)

        now = iso_timestamp(now_datetime)
        operation_id = self.id_factory()
        audit_event_id = self.id_factory()
        scope_id = str(scope["scope_id"])
        async with self.database.transaction() as session:
            current_players = await self.restriction_repository.players_by_platform_user_ids(
                session,
                scope_id=scope_id,
                platform_user_ids=platform_user_ids,
            )
            found = {str(player["platform_user_id"]) for player in current_players}
            missing = [user_id for user_id in platform_user_ids if user_id not in found]
            if missing:
                raise DomainValidationError(
                    "以下成员不属于指定群范围，黑名单未修改：" + "、".join(missing)
                )
            current_by_user = {
                str(player["platform_user_id"]): player for player in current_players
            }
            ordered_players = tuple(current_by_user[user_id] for user_id in platform_user_ids)
            ordered_player_ids = tuple(str(player["player_id"]) for player in ordered_players)

            gift_rows_changed = await self._apply_blacklist_action(
                session,
                player_ids=ordered_player_ids,
                restriction_type=GIFT_TRANSFER_BAN,
                action=normalized_gift_action,
                now=now,
                reason=reason,
                source=source,
                created_by=created_by,
            )
            trade_rows_changed = await self._apply_blacklist_action(
                session,
                player_ids=ordered_player_ids,
                restriction_type=TRADE_BAN,
                action=normalized_trade_action,
                now=now,
                reason=reason,
                source=source,
                created_by=created_by,
            )
            cancelled = 0
            if normalized_trade_action == "add":
                cancelled = await self.social_repository.cancel_pending_offers_for_players(
                    session,
                    scope_id=scope_id,
                    player_ids=ordered_player_ids,
                    now=now,
                )
            detail = {
                "operation_id": operation_id,
                "players": [
                    {
                        "player_id": str(player["player_id"]),
                        "platform_user_id": str(player["platform_user_id"]),
                        "display_name": str(player["display_name"]),
                    }
                    for player in ordered_players
                ],
                "gift_action": normalized_gift_action,
                "trade_action": normalized_trade_action,
                "gift_rows_changed": gift_rows_changed,
                "trade_rows_changed": trade_rows_changed,
                "cancelled_pending_trades": cancelled,
                "reason": str(reason or "").strip(),
                "source": str(source or "").strip(),
                "backup_path": str(backup_path),
            }
            await self.restriction_repository.insert_operation_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=scope_id,
                actor_user_id=str(created_by or "").strip() or "maibot-admin-panel",
                action="social-blacklists-updated",
                object_type="player-blacklist-batch",
                object_id=operation_id,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=now,
            )

        ordered = {str(player["platform_user_id"]): player for player in players}
        return SocialBlacklistUpdateResult(
            operation_id=operation_id,
            audit_event_id=audit_event_id,
            scope_id=scope_id,
            platform=str(scope["platform"]),
            group_name=str(scope["group_name"]),
            player_ids=tuple(str(ordered[user_id]["player_id"]) for user_id in platform_user_ids),
            platform_user_ids=platform_user_ids,
            display_names=tuple(str(ordered[user_id]["display_name"]) for user_id in platform_user_ids),
            gift_action=normalized_gift_action,
            trade_action=normalized_trade_action,
            gift_rows_changed=gift_rows_changed,
            trade_rows_changed=trade_rows_changed,
            cancelled_pending_trades=cancelled,
            backup_path=backup_path,
        )

    async def _resolve_targets(
        self,
        *,
        group_id: str,
        platform: str,
        user_ids: Sequence[str],
    ) -> tuple[dict[str, object], tuple[dict[str, object], ...], tuple[str, ...]]:
        async with self.database.transaction(immediate=False) as session:
            scopes = await self.restriction_repository.scopes_for_group(
                session,
                group_id=group_id,
                platform=platform,
            )
            if len(scopes) != 1:
                raise DomainValidationError(
                    f"群号必须精确匹配一个现有范围，实际匹配 {len(scopes)} 个。"
                )
            scope = scopes[0]
            platform_user_ids = self._normalize_platform_user_ids(
                user_ids,
                scope_id=str(scope["scope_id"]),
                platform=str(scope["platform"]),
            )
            players = await self.restriction_repository.players_by_platform_user_ids(
                session,
                scope_id=str(scope["scope_id"]),
                platform_user_ids=platform_user_ids,
            )
        found = {str(player["platform_user_id"]): player for player in players}
        missing = [user_id for user_id in platform_user_ids if user_id not in found]
        if missing:
            raise DomainValidationError(
                "以下成员尚未在指定群留下插件身份，黑名单未修改：" + "、".join(missing)
            )
        return scope, tuple(found[user_id] for user_id in platform_user_ids), platform_user_ids

    async def _apply_blacklist_action(
        self,
        session: DatabaseSession,
        *,
        player_ids: Sequence[str],
        restriction_type: str,
        action: str,
        now: str,
        reason: str,
        source: str,
        created_by: str,
    ) -> int:
        if action == "none":
            return 0
        if action == "remove":
            return await self.restriction_repository.delete_restrictions(
                session,
                player_ids=player_ids,
                restriction_type=restriction_type,
            )
        for player_id in player_ids:
            await self.restriction_repository.upsert_restriction(
                session,
                restriction_id=self.id_factory(),
                player_id=player_id,
                restriction_type=restriction_type,
                limit_value=None,
                starts_at=now,
                expires_at=None,
                reason=str(reason or "").strip(),
                source=str(source or "").strip(),
                created_by=str(created_by or "").strip(),
                now=now,
            )
        return len(player_ids)

    @staticmethod
    def _normalize_blacklist_action(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"none", "add", "remove"}:
            raise DomainValidationError(f"不支持的黑名单动作：{value}")
        return normalized

    @staticmethod
    def _normalize_platform_user_ids(
        values: Sequence[str],
        *,
        scope_id: str,
        platform: str,
    ) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw_value in values:
            value = str(raw_value or "").strip()
            if value.startswith(f"{scope_id}:"):
                value = value[len(scope_id) + 1 :]
            elif value.startswith(f"{platform}:"):
                value = value[len(platform) + 1 :]
            elif ":" in value:
                raise DomainValidationError(f"成员 ID 平台或群范围不匹配：{value}")
            if not value or len(value) > 320 or any(ord(character) < 32 for character in value):
                raise DomainValidationError("成员 ID/OpenID 不合法。")
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            raise DomainValidationError("黑名单管理至少需要一名成员。")
        return tuple(normalized)

    @staticmethod
    def _blacklist_backup_path(
        *,
        data_dir: Path,
        scope_id: str,
        now: datetime,
    ) -> Path:
        timestamp = now.strftime("%Y%m%d-%H%M%S-%f")
        scope_fingerprint = sha256(scope_id.encode("utf-8")).hexdigest()[:12]
        return (
            Path(data_dir).resolve()
            / "backups"
            / f"pig_catcher-pre-blacklist-update-{scope_fingerprint}-{timestamp}.sqlite3"
        ).resolve()


class AnnouncementAdminService:
    """Claim, route, and audit a single panel-controlled announcement send."""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        restriction_repository: RestrictionRepository | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.repository = restriction_repository or RestrictionRepository()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)

    async def claim(
        self,
        *,
        group_id: str,
        platform: str,
        content: str,
        source: str,
        created_by: str,
    ) -> AnnouncementClaim:
        normalized_content = str(content or "").strip()
        if not normalized_content:
            raise DomainValidationError("公告正文不能为空。")
        if len(normalized_content) > 4000:
            raise DomainValidationError("公告正文不能超过 4000 个字符。")
        normalized_group_id = str(group_id or "").strip()
        normalized_platform = str(platform or "").strip().lower()
        now = iso_timestamp(self.clock.now())
        announcement_id = self.id_factory()
        audit_event_id = self.id_factory()
        async with self.database.transaction() as session:
            scopes = await self.repository.scopes_for_group(
                session,
                group_id=normalized_group_id,
                platform=normalized_platform,
            )
            if len(scopes) != 1:
                raise DomainValidationError(
                    f"群号必须精确匹配一个现有范围，实际匹配 {len(scopes)} 个。"
                )
            scope = scopes[0]
            stream_id = str(scope["stream_id"] or "").strip()
            if not stream_id:
                raise DomainValidationError("目标群还没有可用的 MaiBot 聊天流，无法发送公告。")
            content_hash = sha256(normalized_content.encode("utf-8")).hexdigest()
            detail = {
                "announcement_id": announcement_id,
                "content": normalized_content,
                "content_sha256": content_hash,
                "content_length": len(normalized_content),
                "stream_id": stream_id,
                "source": str(source or "").strip(),
                "status": "claimed",
            }
            await self.repository.insert_operation_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=str(scope["scope_id"]),
                actor_user_id=str(created_by or "").strip() or "maibot-admin-panel",
                action="announcement-send-claimed",
                object_type="group-announcement",
                object_id=announcement_id,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=now,
            )
        return AnnouncementClaim(
            announcement_id=announcement_id,
            audit_event_id=audit_event_id,
            scope_id=str(scope["scope_id"]),
            platform=str(scope["platform"]),
            group_name=str(scope["group_name"]),
            stream_id=stream_id,
            content=normalized_content,
            content_sha256=content_hash,
            created_at=now,
        )

    async def record_result(
        self,
        claim: AnnouncementClaim,
        *,
        success: bool,
        error: str = "",
    ) -> str:
        audit_event_id = self.id_factory()
        now = iso_timestamp(self.clock.now())
        detail = {
            "announcement_id": claim.announcement_id,
            "claimed_audit_event_id": claim.audit_event_id,
            "content_sha256": claim.content_sha256,
            "stream_id": claim.stream_id,
            "status": "succeeded" if success else "failed",
            "error": str(error or "").strip()[:500],
        }
        async with self.database.transaction() as session:
            await self.repository.insert_operation_audit_event(
                session,
                audit_event_id=audit_event_id,
                scope_id=claim.scope_id,
                actor_user_id="maibot-admin-panel",
                action=(
                    "announcement-send-succeeded"
                    if success
                    else "announcement-send-failed"
                ),
                object_type="group-announcement",
                object_id=claim.announcement_id,
                detail_json=json.dumps(
                    detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now=now,
            )
        return audit_event_id
