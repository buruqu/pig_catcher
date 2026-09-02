"""Application service for data-driven weekly competitions."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, SystemClock
from ..domain.weekly_competitions import (
    WEEKLY_COMPETITION_DEFINITIONS,
    WEEKLY_COMPETITIONS_BY_KEY,
    WEEKLY_REWARD_NAMES,
    WeeklyAggregation,
    WeeklyCompetitionDefinition,
    WeeklyReward,
    WeeklySortDirection,
)
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    AchievementRepository,
    EconomyRepository,
    FrameworkRepository,
    WeeklyCompetitionRepository,
)
from ..version import RULESET_VERSION

_PAGE_SIZE = 10
_NOTIFICATION_CLAIM_TTL = timedelta(minutes=10)
_DEFAULT_TIMEZONE = "Asia/Shanghai"
_BEIJING_TIMEZONE = timezone(timedelta(hours=8), _DEFAULT_TIMEZONE)
_COMMON_REWARD_NAMES = {
    "pig-coin": "猪币",
    "achievement-catch": "成就抓猪券",
    "achievement-firework": "成就礼花券",
}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Weekly competition clock must return an aware datetime")
    return value.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return _aware_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _aware_utc(parsed)


def beijing_week_window(value: datetime) -> tuple[datetime, datetime]:
    """Return Monday 00:00 through next Monday 00:00 in Beijing time."""

    local = _aware_utc(value).astimezone(_BEIJING_TIMEZONE)
    start_local = (local - timedelta(days=local.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return start_local.astimezone(UTC), (start_local + timedelta(days=7)).astimezone(UTC)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _score_text(value: float, unit: str) -> str:
    rounded = round(float(value), 4)
    number = f"{int(rounded):,}" if rounded.is_integer() else f"{rounded:,.2f}".rstrip("0").rstrip(".")
    return f"{number} {unit}".strip()


def weekly_reward_label(reward: WeeklyReward) -> str:
    name = WEEKLY_REWARD_NAMES.get(reward.reward_id, _COMMON_REWARD_NAMES.get(reward.reward_id, reward.reward_id))
    prefix = {
        "coin": "",
        "ticket": "道具·",
        "title": "称号·",
        "frame": "边框·",
        "badge": "牌子·",
    }.get(reward.reward_type, "")
    return f"{prefix}{name} ×{reward.quantity}"


@dataclass(frozen=True, slots=True)
class WeeklyCompetitionRankingEntry:
    rank: int
    player_id: str
    display_name: str
    score: float
    score_text: str
    catch_count: int
    highest_single_value: float
    highest_single_text: str
    last_update_at: str


@dataclass(frozen=True, slots=True)
class WeeklyCompetitionPage:
    competition_id: str
    season_number: int
    name: str
    status: str
    status_label: str
    group_name: str
    metric_label: str
    metric_unit: str
    period_text: str
    countdown_text: str
    page: int
    page_count: int
    total_count: int
    player_rank: int | None
    player_score_text: str
    entries: tuple[WeeklyCompetitionRankingEntry, ...]


@dataclass(frozen=True, slots=True)
class WeeklyCompetitionAward:
    award_id: str
    season_number: int
    competition_name: str
    display_name: str
    final_rank: int
    score: float
    score_text: str
    rewards: tuple[WeeklyReward, ...]


@dataclass(slots=True)
class _Standing:
    player_id: str
    display_name: str
    score: float
    catch_count: int
    highest_single_value: float
    last_update_at: str


def format_weekly_competition_summary(page: WeeklyCompetitionPage) -> str:
    lines = [
        f"【PiG Dream! 周冲榜 · 第 {page.season_number} 期】",
        page.name,
        f"群：{page.group_name}｜{page.status_label}｜{page.period_text}",
        f"指标：{page.metric_label}｜{page.countdown_text}",
        f"第 {page.page}/{page.page_count} 页｜参榜 {page.total_count} 人",
    ]
    for entry in page.entries:
        lines.append(
            f"{entry.rank}. {entry.display_name}｜{entry.score_text}｜"
            f"抓到 {entry.catch_count} 只｜单只最高 {entry.highest_single_text}"
        )
    if not page.entries:
        lines.append("本群本期还没有有效抓猪记录。")
    if page.player_rank is None:
        lines.append("我的名次：尚未上榜")
    else:
        lines.append(f"我的名次：第 {page.player_rank} 名｜{page.player_score_text}")
    return "\n".join(lines)


def format_weekly_award_summary(award: WeeklyCompetitionAward) -> str:
    return (
        f"【PiG Dream! 周冲榜结算】\n"
        f"{award.display_name} 在第 {award.season_number} 期“{award.competition_name}”获得第 {award.final_rank} 名。\n"
        f"最终成绩：{award.score_text}\n"
        f"奖励：{'、'.join(weekly_reward_label(item) for item in award.rewards)}\n"
        f"可使用 /佩戴成就 {award.competition_name} 佩戴本期称号、边框和牌子。"
    )


class WeeklyCompetitionService:
    """Track exact-scope weekly entries and settle top-ten rewards once."""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        repository: WeeklyCompetitionRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        achievement_repository: AchievementRepository | None = None,
        economy_repository: EconomyRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or WeeklyCompetitionRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.achievement_repository = achievement_repository or AchievementRepository()
        self.economy_repository = economy_repository or EconomyRepository()
        self.clock = clock or SystemClock()

    async def initialize(self) -> None:
        """Create the first season and recover every eligible committed catch."""

        now_value = _aware_utc(self.clock.now())
        now = _iso_utc(now_value)
        async with self.database.transaction() as session:
            await self._sync_definitions(session, now_value=now_value, now=now)
            await self._refresh(session, now_value=now_value, now=now, backfill_active=True)

    async def process_receipt(self, receipt: CommandReceipt) -> bool:
        """Consume one committed receipt without ever mutating the main result."""

        if not receipt.player_id:
            return False
        now_value = _aware_utc(self.clock.now())
        now = _iso_utc(now_value)
        inserted = False
        async with self.database.transaction() as session:
            await self._sync_definitions(session, now_value=now_value, now=now)
            await self._refresh(session, now_value=now_value, now=now, backfill_active=False)
            competitions = await self.repository.competitions_for_refresh(session)
            for competition in competitions:
                definition = self._definition_for_row(competition)
                if receipt.result_type != definition.source_result_type:
                    continue
                if receipt.command_name not in definition.source_command_names:
                    continue
                if not (
                    str(competition["starts_at"]) <= receipt.created_at < str(competition["ends_at"])
                ):
                    continue
                rows = await self.repository.source_receipt_rows(
                    session,
                    source_result_type=definition.source_result_type,
                    source_field=("" if definition.aggregation is WeeklyAggregation.COUNT else definition.source_field),
                    command_names=definition.source_command_names,
                    starts_at=str(competition["starts_at"]),
                    ends_at=str(competition["ends_at"]),
                    receipt_id=receipt.receipt_id,
                )
                for row in rows:
                    inserted = await self._insert_entry(
                        session,
                        competition=competition,
                        definition=definition,
                        row=row,
                        now=now,
                    ) or inserted
        return inserted

    async def leaderboard(self, identity: CommandIdentity, *, page: int = 1) -> WeeklyCompetitionPage:
        now_value = _aware_utc(self.clock.now())
        now = _iso_utc(now_value)
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            await self._sync_definitions(session, now_value=now_value, now=now)
            await self._refresh(session, now_value=now_value, now=now, backfill_active=True)
            competition = await self.repository.latest_competition(session)
            if competition is None:
                raise RuntimeError("本周冲榜活动尚未发布。")
            definition = self._definition_for_row(competition)
            rows = await self.repository.entry_rows(
                session,
                competition_id=str(competition["competition_id"]),
                scope_id=identity.scope.value,
            )
            standings = self._rank_rows(rows, definition)

        total = len(standings)
        page_count = max(1, math.ceil(total / _PAGE_SIZE))
        selected_page = max(1, min(int(page), page_count))
        selected = standings[(selected_page - 1) * _PAGE_SIZE : selected_page * _PAGE_SIZE]
        player_entry = next((item for item in standings if item.player_id == identity.player_id), None)
        start_local = _parse_utc(str(competition["starts_at"])).astimezone(_BEIJING_TIMEZONE)
        end_local = _parse_utc(str(competition["ends_at"])).astimezone(_BEIJING_TIMEZONE)
        status = str(competition["status"])
        return WeeklyCompetitionPage(
            competition_id=str(competition["competition_id"]),
            season_number=int(competition["season_number"]),
            name=str(competition["name"]),
            status=status,
            status_label={
                "scheduled": "尚未开始",
                "active": "冲刺进行中",
                "settled": "本期已结算",
                "cancelled": "本期已取消",
            }.get(status, status),
            group_name=identity.group_name or identity.scope.group_id,
            metric_label=str(competition["metric_label"]),
            metric_unit=str(competition["metric_unit"]),
            period_text=(
                f"{start_local:%m月%d日 %H:%M} — {end_local:%m月%d日 %H:%M}（北京时间）"
            ),
            countdown_text=self._countdown_text(now_value, end_local.astimezone(UTC), status),
            page=selected_page,
            page_count=page_count,
            total_count=total,
            player_rank=(standings.index(player_entry) + 1 if player_entry is not None else None),
            player_score_text=(
                _score_text(player_entry.score, definition.metric_unit) if player_entry is not None else "0"
            ),
            entries=tuple(
                WeeklyCompetitionRankingEntry(
                    rank=standings.index(item) + 1,
                    player_id=item.player_id,
                    display_name=item.display_name,
                    score=item.score,
                    score_text=_score_text(item.score, definition.metric_unit),
                    catch_count=item.catch_count,
                    highest_single_value=item.highest_single_value,
                    highest_single_text=_score_text(item.highest_single_value, definition.metric_unit),
                    last_update_at=_parse_utc(item.last_update_at)
                    .astimezone(_BEIJING_TIMEZONE)
                    .strftime("%m-%d %H:%M"),
                )
                for item in selected
            ),
        )

    async def claim_pending_award(self, *, player_id: str) -> WeeklyCompetitionAward | None:
        now_value = _aware_utc(self.clock.now())
        now = _iso_utc(now_value)
        async with self.database.transaction() as session:
            row = await self.repository.pending_award_row(
                session,
                player_id=player_id,
                stale_claimed_before=_iso_utc(now_value - _NOTIFICATION_CLAIM_TTL),
            )
            if row is None:
                return None
            award_id = str(row["award_id"])
            if not await self.repository.update_award_notification(
                session,
                award_id=award_id,
                from_status=str(row["notification_status"]),
                to_status="claimed",
                error="",
                now=now,
            ):
                return None
        rewards = self._decode_rewards(str(row["reward_snapshot_json"]))
        return WeeklyCompetitionAward(
            award_id=award_id,
            season_number=int(row["season_number"]),
            competition_name=str(row["name"]),
            display_name=str(row["display_name"]),
            final_rank=int(row["final_rank"]),
            score=float(row["score_value"]),
            score_text=_score_text(float(row["score_value"]), str(row["metric_unit"])),
            rewards=rewards,
        )

    async def mark_award_notification(
        self,
        award_id: str,
        *,
        sent: bool,
        error: str = "",
    ) -> bool:
        async with self.database.transaction() as session:
            return await self.repository.update_award_notification(
                session,
                award_id=award_id,
                from_status="claimed",
                to_status="sent" if sent else "failed",
                error=error,
                now=_iso_utc(self.clock.now()),
            )

    async def equip_competition_cosmetics(
        self,
        identity: CommandIdentity,
        competition_name: str,
    ) -> tuple[str, ...] | None:
        """Equip an owned season title, frame and rank plate by event name."""

        normalized = str(competition_name or "").strip()
        if not normalized:
            return None
        now = _iso_utc(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            await self.achievement_repository.ensure_profile(session, player_id=identity.player_id, now=now)
            row = await self.repository.player_award_for_competition(
                session,
                player_id=identity.player_id,
                competition_name=normalized,
            )
            if row is None:
                return None
            rewards = self._decode_rewards(str(row["reward_snapshot_json"]))
            title = next((item.reward_id for item in rewards if item.reward_type == "title"), "")
            frame = next((item.reward_id for item in rewards if item.reward_type == "frame"), "")
            badge = next((item.reward_id for item in rewards if item.reward_type == "badge"), "")
            for reward_type, reward_id in (("title", title), ("frame", frame), ("badge", badge)):
                if not reward_id or not await self.repository.owns_reward(
                    session,
                    player_id=identity.player_id,
                    reward_type=reward_type,
                    reward_id=reward_id,
                ):
                    raise RuntimeError("周冲榜外观奖励尚未进入库存。")
            updated = await self.achievement_repository.update_equipped_cosmetics(
                session,
                player_id=identity.player_id,
                title_id=title,
                frame_id=frame,
                showcase_achievement_id=badge,
                now=now,
            )
            if not updated:
                raise RuntimeError("周冲榜外观奖励无法佩戴。")
        return (
            f"称号·{WEEKLY_REWARD_NAMES.get(title, title)}",
            f"边框·{WEEKLY_REWARD_NAMES.get(frame, frame)}",
            f"牌子·{WEEKLY_REWARD_NAMES.get(badge, badge)}",
        )

    async def _sync_definitions(self, session: DatabaseSession, *, now_value: datetime, now: str) -> None:
        for definition in WEEKLY_COMPETITION_DEFINITIONS:
            if await self.repository.competition_by_definition(
                session,
                definition_key=definition.definition_key,
            ) is not None:
                continue
            if definition.fixed_starts_at and definition.fixed_ends_at:
                start = datetime.fromisoformat(definition.fixed_starts_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(definition.fixed_ends_at.replace("Z", "+00:00"))
            else:
                start, end = beijing_week_window(now_value)
            definition_snapshot = {
                "source_command_names": list(definition.source_command_names),
                "reward_tiers": [
                    {
                        "ranks": list(tier.ranks),
                        "rewards": [
                            {
                                "type": reward.reward_type,
                                "id": reward.reward_id,
                                "quantity": reward.quantity,
                            }
                            for reward in tier.rewards
                        ],
                    }
                    for tier in definition.reward_tiers
                ],
                "fixed_starts_at": definition.fixed_starts_at,
                "fixed_ends_at": definition.fixed_ends_at,
            }
            await self.repository.insert_competition(
                session,
                values={
                    "competition_id": str(uuid4()),
                    "season_number": definition.season_number,
                    "definition_key": definition.definition_key,
                    "name": definition.name,
                    "source_result_type": definition.source_result_type,
                    "source_field": definition.source_field,
                    "aggregation": definition.aggregation.value,
                    "sort_direction": definition.sort_direction.value,
                    "metric_label": definition.metric_label,
                    "metric_unit": definition.metric_unit,
                    "definition_json": _json(definition_snapshot),
                    "starts_at": _iso_utc(start),
                    "ends_at": _iso_utc(end),
                    "status": "active",
                    "ruleset_version": RULESET_VERSION,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    async def _refresh(
        self,
        session: DatabaseSession,
        *,
        now_value: datetime,
        now: str,
        backfill_active: bool,
    ) -> None:
        for competition in await self.repository.competitions_for_refresh(session):
            start = _parse_utc(str(competition["starts_at"]))
            end = _parse_utc(str(competition["ends_at"]))
            if now_value < start:
                continue
            if str(competition["status"]) == "scheduled" and now_value < end:
                await self.repository.set_competition_status(
                    session,
                    competition_id=str(competition["competition_id"]),
                    expected_statuses=("scheduled",),
                    status="active",
                    now=now,
                )
                competition["status"] = "active"
            definition = self._definition_for_row(competition)
            if now_value >= end:
                await self._backfill(session, competition=competition, definition=definition, now=now)
                await self._settle(session, competition=competition, definition=definition, now=now)
            elif backfill_active and str(competition["status"]) == "active":
                await self._backfill(session, competition=competition, definition=definition, now=now)

    async def _backfill(
        self,
        session: DatabaseSession,
        *,
        competition: dict[str, object],
        definition: WeeklyCompetitionDefinition,
        now: str,
    ) -> int:
        rows = await self.repository.source_receipt_rows(
            session,
            source_result_type=definition.source_result_type,
            source_field=("" if definition.aggregation is WeeklyAggregation.COUNT else definition.source_field),
            command_names=definition.source_command_names,
            starts_at=str(competition["starts_at"]),
            ends_at=str(competition["ends_at"]),
        )
        inserted = 0
        for row in rows:
            inserted += int(
                await self._insert_entry(
                    session,
                    competition=competition,
                    definition=definition,
                    row=row,
                    now=now,
                )
            )
        return inserted

    async def _insert_entry(
        self,
        session: DatabaseSession,
        *,
        competition: dict[str, object],
        definition: WeeklyCompetitionDefinition,
        row: dict[str, object],
        now: str,
    ) -> bool:
        snapshot = {
            "display_name": str(row.get("display_name_snapshot") or ""),
            "rarity": int(row.get("rarity") or 0),
            "official_value": int(row.get("official_value") or 0),
            "metric_field": definition.source_field,
            "metric_value": float(row.get("metric_value") or 0),
        }
        return await self.repository.insert_entry(
            session,
            values={
                "entry_id": str(uuid4()),
                "competition_id": str(competition["competition_id"]),
                "scope_id": str(row["scope_id"]),
                "player_id": str(row["player_id"]),
                "receipt_id": str(row["receipt_id"]),
                "source_object_type": definition.source_result_type,
                "source_object_id": str(row["source_object_id"]),
                "metric_value": float(row.get("metric_value") or 0),
                "rarity": int(row.get("rarity") or 0),
                "source_snapshot_json": _json(snapshot),
                "occurred_at": str(row["occurred_at"]),
                "created_at": now,
            },
        )

    async def _settle(
        self,
        session: DatabaseSession,
        *,
        competition: dict[str, object],
        definition: WeeklyCompetitionDefinition,
        now: str,
    ) -> None:
        competition_id = str(competition["competition_id"])
        for scope_id in await self.repository.entry_scope_ids(session, competition_id=competition_id):
            if await self.repository.settlement_row(
                session,
                competition_id=competition_id,
                scope_id=scope_id,
            ) is not None:
                continue
            standings = self._rank_rows(
                await self.repository.entry_rows(
                    session,
                    competition_id=competition_id,
                    scope_id=scope_id,
                ),
                definition,
            )
            winners = [
                (index, item)
                for index, item in enumerate(standings, start=1)
                if definition.rewards_for_rank(index)
            ]
            settlement_id = str(uuid4())
            created = await self.repository.insert_settlement(
                session,
                settlement_id=settlement_id,
                competition_id=competition_id,
                scope_id=scope_id,
                participant_count=len(standings),
                winner_count=min(10, len(winners)),
                now=now,
            )
            if not created:
                continue
            for rank, standing in winners[:10]:
                rewards = definition.rewards_for_rank(rank)
                award_id = str(uuid4())
                reward_json = _json(
                    [
                        {"type": reward.reward_type, "id": reward.reward_id, "quantity": reward.quantity}
                        for reward in rewards
                    ]
                )
                inserted = await self.repository.insert_award(
                    session,
                    values={
                        "award_id": award_id,
                        "settlement_id": settlement_id,
                        "competition_id": competition_id,
                        "scope_id": scope_id,
                        "player_id": standing.player_id,
                        "final_rank": rank,
                        "score_value": standing.score,
                        "reward_snapshot_json": reward_json,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                if inserted:
                    await self._grant_rewards(
                        session,
                        competition_id=competition_id,
                        scope_id=scope_id,
                        player_id=standing.player_id,
                        rank=rank,
                        award_id=award_id,
                        rewards=rewards,
                        now=now,
                    )
        await self.repository.set_competition_status(
            session,
            competition_id=competition_id,
            expected_statuses=("active", "scheduled"),
            status="settled",
            now=now,
        )

    async def _grant_rewards(
        self,
        session: DatabaseSession,
        *,
        competition_id: str,
        scope_id: str,
        player_id: str,
        rank: int,
        award_id: str,
        rewards: tuple[WeeklyReward, ...],
        now: str,
    ) -> None:
        await self.achievement_repository.ensure_profile(session, player_id=player_id, now=now)
        source_key = f"weekly:{competition_id}:{scope_id}:{player_id}:rank:{rank}"
        for reward in rewards:
            if reward.reward_type == "coin":
                balance = await self.economy_repository.apply_currency_change(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    amount=reward.quantity,
                    reason_code="weekly-competition-reward",
                    reason_text="周冲榜结算奖励",
                    source_object_type="weekly-competition-award",
                    source_object_id=award_id,
                    ledger_entry_id=str(uuid4()),
                    idempotency_key=f"{source_key}:coin",
                    now=now,
                )
                if balance is None:
                    raise RuntimeError("周冲榜猪币奖励无法写入玩家余额。")
            else:
                await self.achievement_repository.grant_reward(
                    session,
                    player_id=player_id,
                    reward_type=reward.reward_type,
                    reward_id=reward.reward_id,
                    quantity=reward.quantity,
                    now=now,
                )

    @staticmethod
    def _rank_rows(
        rows: list[dict[str, object]],
        definition: WeeklyCompetitionDefinition,
    ) -> list[_Standing]:
        grouped: dict[str, _Standing] = {}
        for row in rows:
            player_id = str(row["player_id"])
            value = float(row["metric_value"])
            standing = grouped.get(player_id)
            if standing is None:
                grouped[player_id] = _Standing(
                    player_id=player_id,
                    display_name=str(row["display_name"]),
                    score=(1.0 if definition.aggregation is WeeklyAggregation.COUNT else value),
                    catch_count=1,
                    highest_single_value=value,
                    last_update_at=str(row["occurred_at"]),
                )
                continue
            standing.display_name = str(row["display_name"])
            standing.catch_count += 1
            standing.highest_single_value = max(standing.highest_single_value, value)
            standing.last_update_at = max(standing.last_update_at, str(row["occurred_at"]))
            if definition.aggregation is WeeklyAggregation.SUM:
                standing.score += value
            elif definition.aggregation is WeeklyAggregation.COUNT:
                standing.score += 1
            elif definition.aggregation is WeeklyAggregation.MAX:
                standing.score = max(standing.score, value)
            elif definition.aggregation is WeeklyAggregation.MIN:
                standing.score = min(standing.score, value)
        direction = -1 if definition.sort_direction is WeeklySortDirection.DESCENDING else 1
        return sorted(
            grouped.values(),
            key=lambda item: (
                direction * item.score,
                -item.highest_single_value,
                -item.catch_count,
                item.last_update_at,
                item.player_id,
            ),
        )

    @staticmethod
    def _definition_for_row(row: dict[str, object]) -> WeeklyCompetitionDefinition:
        definition = WEEKLY_COMPETITIONS_BY_KEY.get(str(row["definition_key"]))
        if definition is None:
            raise RuntimeError(f"周冲榜定义已缺失：{row['definition_key']}")
        return definition

    @staticmethod
    def _decode_rewards(raw: str) -> tuple[WeeklyReward, ...]:
        value: Any = json.loads(raw or "[]")
        if not isinstance(value, list):
            raise RuntimeError("周冲榜奖励快照损坏。")
        return tuple(
            WeeklyReward(str(item["type"]), str(item["id"]), int(item["quantity"]))
            for item in value
            if isinstance(item, dict)
        )

    @staticmethod
    def _countdown_text(now: datetime, end: datetime, status: str) -> str:
        if status == "settled":
            return "本期成绩与奖励已冻结"
        if status == "cancelled":
            return "本期已取消"
        seconds = max(0, int((end - now).total_seconds()))
        days, remainder = divmod(seconds, 86_400)
        hours, remainder = divmod(remainder, 3_600)
        minutes = remainder // 60
        if days:
            return f"距结算 {days} 天 {hours} 小时"
        if hours:
            return f"距结算 {hours} 小时 {minutes} 分"
        return f"距结算 {minutes} 分"


__all__ = [
    "WeeklyCompetitionAward",
    "WeeklyCompetitionPage",
    "WeeklyCompetitionRankingEntry",
    "WeeklyCompetitionService",
    "beijing_week_window",
    "format_weekly_award_summary",
    "format_weekly_competition_summary",
    "weekly_reward_label",
]
