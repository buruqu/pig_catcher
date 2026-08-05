"""玩家处罚限制的批量应用服务。"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from ..domain.errors import DomainValidationError
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import PigCatcherDatabase
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
