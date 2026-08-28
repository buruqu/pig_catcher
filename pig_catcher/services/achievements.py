"""Application service for data-driven v2 achievements.

Committed command receipts are the durable event source.  Processing a receipt
is idempotent and grants the unlock, points, coins and non-tradable rewards in a
single transaction.  This keeps the gameplay services independent from the
achievement catalogue and makes recovery after a crash deterministic.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..domain.achievements import (
    ACHIEVEMENT_DEFINITIONS,
    LEGACY_ACHIEVEMENT_DEFINITIONS,
    TIER_LABELS,
    UNLOCK_SUMMARY_LIMIT,
    AchievementConditionKind,
    AchievementDefinition,
    AchievementReward,
    AchievementTier,
    AchievementUnlock,
)
from ..domain.activity_achievements import ACTIVITY_REWARDS, LEGACY_REGULAR_IDS
from ..domain.dispatch import MATERIAL_SCALE, safe_display_name
from ..domain.errors import DomainValidationError
from ..domain.gameplay import generate_pig_attributes, level_progress
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, SystemClock
from ..domain.short_codes import new_short_code, normalize_short_code
from ..domain.weekly_competitions import WEEKLY_REWARD_NAMES
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    AchievementRepository,
    EconomyRepository,
    FrameworkRepository,
    GameplayRepository,
)
from ..version import RULESET_VERSION

_COLLECTION_ALIASES = {
    "poppin-party": "bandori-poppin-party",
    "afterglow": "bandori-afterglow",
    "mygo": "bandori-mygo",
    "morfonica": "bandori-morfonica",
    "mugendai": "bandori-yumemita-viola",
    "pastel-palettes": "bandori-pastel-palettes",
    "hello-happy-world": "bandori-hello-happy-world",
    "roselia": "bandori-roselia",
    "raise-a-suilen": "bandori-raise-a-suilen",
    "ave-mujica": "bandori-ave-mujica",
    "jujutsu-kaisen": "jujutsu-kaisen",
}

_MILESTONE_REWARDS: Mapping[int, tuple[AchievementReward, ...]] = {
    50: (AchievementReward("frame", "achievement-pale-pink"),),
    150: (AchievementReward("ticket", "achievement-catch", 3),),
    300: (
        AchievementReward("ticket", "catalog-guide", 2),
        AchievementReward("ticket", "food-inspiration"),
    ),
    500: (
        AchievementReward("cosmetic", "badge-showcase-3"),
        AchievementReward("ticket", "identifier-reforge"),
    ),
    750: (
        AchievementReward("frame", "achievement-gold"),
        AchievementReward("chest", "achievement-choice"),
    ),
    1000: (
        AchievementReward("title", "achievement-legend"),
        AchievementReward("ticket", "achievement-firework"),
        AchievementReward("coin", "pig-coin", 10000),
    ),
}

_TICKET_NAMES: Mapping[str, tuple[str, str]] = {
    "成就抓猪券": ("achievement-catch", "catching"),
    "图鉴引路券": ("catalog-guide", "catching"),
    "美食灵感券": ("food-inspiration", "cooking"),
    "巨物复秤券": ("giant-rescale", "catching"),
    "迷你复秤券": ("mini-rescale", "catching"),
    "回锅重做券": ("recook", "cooking"),
    "成就礼花券": ("achievement-firework", "visual"),
}

_REWARD_NAMES: Mapping[str, str] = {
    "pig-coin": "猪币",
    "achievement-catch": "成就抓猪券",
    "catalog-guide": "图鉴引路券",
    "food-inspiration": "美食灵感券",
    "giant-rescale": "巨物复秤券",
    "mini-rescale": "迷你复秤券",
    "recook": "回锅重做券",
    "identifier-reforge": "编号重铸券",
    "asset-code-change": "编号修改券",
    "pig-choice": "猪猪自选券",
    "achievement-firework": "成就礼花券",
    "achievement-choice": "成就自选宝箱",
    "regular-five-star-memorial": "常规成就毕业纪念猪礼盒",
    "achievement-pale-pink": "淡粉成就边框",
    "achievement-gold": "金色成就边框",
    "achievement-legend": "传说成就称号",
    "roulette-omniscient": "轮盘全知者",
    "technique-observer": "术式观测者",
    "rain-love": "雨爱",
    "sushi-commander": "寿司总大将",
    "pure-chef": "纯粹主厨",
    "947947": "947947",
    "all-giants": "万猪之巅",
    "all-minis": "掌上万猪",
    "badge-showcase-3": "三格徽章展示架",
    **WEEKLY_REWARD_NAMES,
    **{key: value["name"] for key, value in ACTIVITY_REWARDS.items()},
}


def _now_text(clock: Clock) -> str:
    return clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_mapping(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class AchievementEntry:
    achievement_id: str
    name: str
    category: str
    tier: AchievementTier
    tier_label: str
    hidden: bool
    unlocked: bool
    description: str
    hint: str
    progress: int
    target: int
    points: int
    rewards: tuple[AchievementReward, ...]
    unlocked_at: str = ""


@dataclass(frozen=True, slots=True)
class AchievementOverview:
    display_name: str
    points: int
    unlocked_count: int
    total_count: int
    equipped_title_id: str
    equipped_frame_id: str
    showcase_achievement_name: str
    next_milestone: int | None
    rewards: tuple[tuple[str, str, int], ...]
    recent: tuple[AchievementEntry, ...]


@dataclass(frozen=True, slots=True)
class AchievementPage:
    display_name: str
    category: str
    page: int
    page_count: int
    total_count: int
    entries: tuple[AchievementEntry, ...]


@dataclass(frozen=True, slots=True)
class AchievementRankingEntry:
    rank: int
    display_name: str
    points: int
    unlocked_count: int


@dataclass(frozen=True, slots=True)
class AchievementRanking:
    group_name: str
    page: int
    page_count: int
    total_count: int
    entries: tuple[AchievementRankingEntry, ...]


@dataclass(frozen=True, slots=True)
class AchievementBackfillSummary:
    display_name: str
    unlocked_count: int
    total_points: int
    rewards: tuple[AchievementReward, ...]
    highlights: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AchievementMemorialPig:
    display_name: str
    short_code: str


@dataclass(frozen=True, slots=True)
class AchievementCosmetics:
    title_id: str
    frame_id: str
    showcase_achievement_id: str
    badge_name: str = ""


class AchievementService:
    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        repository: AchievementRepository | None = None,
        economy_repository: EconomyRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        gameplay_repository: GameplayRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or AchievementRepository()
        self.economy_repository = economy_repository or EconomyRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.gameplay_repository = gameplay_repository or GameplayRepository()
        self.clock = clock or SystemClock()
        self._definitions_ready = False

    async def initialize(self) -> None:
        now = _now_text(self.clock)
        async with self.database.transaction() as session:
            await self._sync_definitions(session, now=now)
        self._definitions_ready = True

    async def _sync_definitions(self, session: DatabaseSession, *, now: str) -> None:
        for definition in ACHIEVEMENT_DEFINITIONS:
            condition = {
                "kind": definition.condition.kind.value,
                "metric": definition.condition.metric,
                "target": definition.condition.target,
                "parameters": dict(definition.condition.parameters),
            }
            rewards = [
                {"type": reward.reward_type, "id": reward.reward_id, "quantity": reward.quantity}
                for reward in definition.rewards
            ]
            await self.repository.sync_definition(
                session,
                achievement_id=definition.achievement_id,
                definition_version=definition.definition_version,
                name=definition.name,
                category=definition.category,
                tier=definition.tier.value,
                hidden=definition.hidden,
                points=definition.points,
                description=definition.description,
                hint=definition.hint,
                condition_json=_json(condition),
                rewards_json=_json(rewards),
                now=now,
            )

    async def process_receipt(self, receipt: CommandReceipt) -> tuple[AchievementUnlock, ...]:
        business = await self._process_business_receipt(receipt)
        activities = await self.process_activity_facts(scope_id=receipt.scope_id, receipt_id=receipt.receipt_id)
        return business + activities

    async def process_activity_facts(self, *, scope_id: str, receipt_id: str) -> tuple[AchievementUnlock, ...]:
        from .activity_achievements import ActivityAchievements

        return await ActivityAchievements(self).process_scope(scope_id, receipt_id, _now_text(self.clock))

    async def notification_players(self, *, scope_id: str, receipt_id: str) -> tuple[str, ...]:
        async with self.database.transaction(immediate=False) as session:
            rows = await session.fetch_all(
                "SELECT DISTINCT player_id FROM achievement_unlocks "
                "WHERE scope_id=? AND source_receipt_id=? AND notification_status='pending'",
                (scope_id, receipt_id),
            )
        return tuple(str(row[0]) for row in rows)

    async def _process_business_receipt(self, receipt: CommandReceipt) -> tuple[AchievementUnlock, ...]:
        if not receipt.player_id:
            return ()
        now = _now_text(self.clock)
        payload = _safe_mapping(receipt.result_json)
        async with self.database.transaction() as session:
            if not self._definitions_ready:
                await self._sync_definitions(session, now=now)
            await self.repository.ensure_profile(session, player_id=receipt.player_id, now=now)
            await self._capture_scope_targets(session, scope_id=receipt.scope_id, now=now)
            backfilled_now = await self._maybe_backfill(
                session,
                player_id=receipt.player_id,
                scope_id=receipt.scope_id,
                now=now,
            )
            event_id = str(uuid4())
            created = await self.repository.insert_event(
                session,
                event_id=event_id,
                receipt_id=receipt.receipt_id,
                player_id=receipt.player_id,
                scope_id=receipt.scope_id,
                event_type=receipt.result_type,
                payload_json=receipt.result_json,
                now=now,
            )
            if not created:
                return ()
            progress_rows = await self.repository.progress_rows(session, player_id=receipt.player_id)
            metrics = await self.repository.metric_snapshot(session, player_id=receipt.player_id)
            metrics["player_level"] = level_progress(metrics.get("experience", 0)).level
            gift_partners = (
                await self.repository.transfer_partner_ids(
                    session,
                    player_id=receipt.player_id,
                    transfer_type="gift",
                )
                if receipt.result_type == "gift"
                else set()
            )
            trade_partners = (
                await self.repository.transfer_partner_ids(
                    session,
                    player_id=receipt.player_id,
                    transfer_type="trade",
                )
                if receipt.result_type == "trade-accepted"
                else set()
            )
            roulette_outcomes: set[str] = set()
            sushi_instances: set[str] = set()
            color_counts = await self.repository.technique_color_counts(session, player_id=receipt.player_id)
            context = await self._event_context(session, receipt=receipt, payload=payload)
            flags, additions, counter_deltas = self._event_signals(receipt, payload, context)
            if backfilled_now:
                # The one-time historical snapshot already includes this committed
                # business receipt. Event flags still run, but numeric counters must
                # not count the same action twice.
                counter_deltas.clear()
            unlocks: list[AchievementUnlock] = []
            for definition in LEGACY_ACHIEVEMENT_DEFINITIONS:
                previous = progress_rows.get(definition.achievement_id, {})
                if previous.get("unlocked_at"):
                    continue
                previous_value = int(previous.get("progress_value") or 0)
                state = _safe_mapping(str(previous.get("state_json") or "{}"))
                value, target, state = await self._evaluate(
                    session,
                    definition=definition,
                    player_id=receipt.player_id,
                    scope_id=receipt.scope_id,
                    metrics=metrics,
                    previous_value=previous_value,
                    state=state,
                    flags=flags,
                    additions=additions,
                    counter_deltas=counter_deltas,
                    gift_partners=gift_partners,
                    trade_partners=trade_partners,
                    roulette_outcomes=roulette_outcomes,
                    sushi_instances=sushi_instances,
                    color_counts=color_counts,
                )
                state["target"] = target
                unlocked_at = now if target > 0 and value >= target else None
                await self.repository.upsert_progress(
                    session,
                    player_id=receipt.player_id,
                    achievement_id=definition.achievement_id,
                    definition_version=definition.definition_version,
                    progress_value=min(value, target) if target else value,
                    state_json=_json(state),
                    unlocked_at=unlocked_at,
                    now=now,
                )
                if unlocked_at is None:
                    continue
                unlock_id = str(uuid4())
                rewards_json = _json(
                    [
                        {"type": reward.reward_type, "id": reward.reward_id, "quantity": reward.quantity}
                        for reward in definition.rewards
                    ]
                )
                inserted = await self.repository.insert_unlock(
                    session,
                    unlock_id=unlock_id,
                    player_id=receipt.player_id,
                    scope_id=receipt.scope_id,
                    achievement_id=definition.achievement_id,
                    definition_version=definition.definition_version,
                    source_event_id=event_id,
                    source_receipt_id=receipt.receipt_id,
                    points_awarded=definition.points,
                    rewards_json=rewards_json,
                    notification_status="pending",
                    now=now,
                )
                if not inserted:
                    continue
                await self._grant_rewards(
                    session,
                    player_id=receipt.player_id,
                    scope_id=receipt.scope_id,
                    source_key=f"achievement:{definition.achievement_id}",
                    rewards=definition.rewards,
                    now=now,
                )
                unlocks.append(
                    AchievementUnlock(
                        definition.achievement_id,
                        definition.name,
                        definition.tier,
                        definition.points,
                        definition.rewards,
                        now,
                    )
                )
            await self._settle_milestones(session, player_id=receipt.player_id, scope_id=receipt.scope_id, now=now)
            await self._settle_regular_completion(
                session,
                player_id=receipt.player_id,
                now=now,
            )
            return tuple(unlocks)

    async def _capture_scope_targets(self, session: DatabaseSession, *, scope_id: str, now: str) -> None:
        captured = await self.repository.captured_scope_achievement_ids(session, scope_id=scope_id)
        snapshot_ids = {"ultimate-all-giants", "ultimate-all-minis"}
        if snapshot_ids <= captured and all(
            definition.achievement_id in captured
            for definition in ACHIEVEMENT_DEFINITIONS
            if definition.condition.metric.startswith("collection:")
        ):
            return
        all_templates: list[dict[str, object]] = []
        all_targets: list[tuple[str, str]] = []
        if not snapshot_ids <= captured:
            all_templates = await self.repository.visible_template_rows(session, scope_id=scope_id)
            all_targets = [(str(row["template_id"]), str(row["display_name"])) for row in all_templates]
        for achievement_id in ("ultimate-all-giants", "ultimate-all-minis"):
            if achievement_id in captured:
                continue
            await self.repository.replace_scope_targets(
                session, scope_id=scope_id, achievement_id=achievement_id, targets=all_targets, now=now
            )
        for definition in ACHIEVEMENT_DEFINITIONS:
            metric = definition.condition.metric
            if not metric.startswith("collection:") or definition.achievement_id in captured:
                continue
            alias = metric.split(":", 1)[1]
            collection_id = _COLLECTION_ALIASES.get(alias, alias)
            rows = await self.repository.visible_template_rows(session, scope_id=scope_id, collection_id=collection_id)
            targets = [(str(row["template_id"]), str(row["display_name"])) for row in rows]
            await self.repository.replace_scope_targets(
                session, scope_id=scope_id, achievement_id=definition.achievement_id, targets=targets, now=now
            )

    async def _maybe_backfill(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        now: str,
    ) -> bool:
        """Backfill only players that already existed when Schema 35 was installed.

        The migration seeds those players as pending. Players created afterwards
        have no state row and therefore receive normal real-time unlock cards.
        Event-only hidden achievements that cannot be proven from durable facts
        intentionally start accumulating after activation.
        """

        status = await self.repository.backfill_status(session, player_id=player_id)
        if status not in {"pending", "failed"}:
            return False
        if not await self.repository.claim_backfill(session, player_id=player_id, now=now):
            return False
        receipt_id = f"achievement-backfill-v1:{player_id}"
        event_id = str(uuid4())
        created = await self.repository.insert_event(
            session,
            event_id=event_id,
            receipt_id=receipt_id,
            player_id=player_id,
            scope_id=scope_id,
            event_type="historical-backfill",
            payload_json="{}",
            now=now,
        )
        if not created:
            await self.repository.complete_backfill(session, player_id=player_id, now=now)
            return True

        progress_rows = await self.repository.progress_rows(session, player_id=player_id)
        metrics = await self.repository.metric_snapshot(session, player_id=player_id, historical=True)
        metrics["player_level"] = level_progress(metrics.get("experience", 0)).level
        gift_partners = await self.repository.transfer_partner_ids(session, player_id=player_id, transfer_type="gift")
        trade_partners = await self.repository.transfer_partner_ids(session, player_id=player_id, transfer_type="trade")
        roulette_outcomes = await self.repository.roulette_outcomes(session, player_id=player_id)
        sushi_instances = await self.repository.sushi_instance_ids(session, player_id=player_id)
        color_counts = await self.repository.technique_color_counts(session, player_id=player_id)
        reliable_event_metrics = {"blue_red_pair", "millionaire_947947"}
        for definition in LEGACY_ACHIEVEMENT_DEFINITIONS:
            if (
                definition.condition.kind is AchievementConditionKind.EVENT
                and definition.condition.metric not in reliable_event_metrics
            ):
                continue
            previous = progress_rows.get(definition.achievement_id, {})
            if previous.get("unlocked_at"):
                continue
            state = _safe_mapping(str(previous.get("state_json") or "{}"))
            value, target, state = await self._evaluate(
                session,
                definition=definition,
                player_id=player_id,
                scope_id=scope_id,
                metrics=metrics,
                previous_value=int(previous.get("progress_value") or 0),
                state=state,
                flags={},
                additions={},
                counter_deltas={},
                gift_partners=gift_partners,
                trade_partners=trade_partners,
                roulette_outcomes=roulette_outcomes,
                sushi_instances=sushi_instances,
                color_counts=color_counts,
            )
            state["target"] = target
            unlocked_at = now if target > 0 and value >= target else None
            await self.repository.upsert_progress(
                session,
                player_id=player_id,
                achievement_id=definition.achievement_id,
                definition_version=definition.definition_version,
                progress_value=min(value, target) if target else value,
                state_json=_json(state),
                unlocked_at=unlocked_at,
                now=now,
            )
            if unlocked_at is None:
                continue
            rewards_json = _json(
                [
                    {
                        "type": reward.reward_type,
                        "id": reward.reward_id,
                        "quantity": reward.quantity,
                    }
                    for reward in definition.rewards
                ]
            )
            inserted = await self.repository.insert_unlock(
                session,
                unlock_id=str(uuid4()),
                player_id=player_id,
                scope_id=scope_id,
                achievement_id=definition.achievement_id,
                definition_version=definition.definition_version,
                source_event_id=event_id,
                source_receipt_id=receipt_id,
                points_awarded=definition.points,
                rewards_json=rewards_json,
                notification_status="summary",
                now=now,
            )
            if inserted:
                await self._grant_rewards(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    source_key=f"achievement:{definition.achievement_id}",
                    rewards=definition.rewards,
                    now=now,
                )
        await self._settle_milestones(session, player_id=player_id, scope_id=scope_id, now=now)
        await self._settle_regular_completion(
            session,
            player_id=player_id,
            now=now,
        )
        await self.repository.complete_backfill(session, player_id=player_id, now=now)
        return True

    async def _event_context(
        self,
        session: DatabaseSession,
        *,
        receipt: CommandReceipt,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {}
        pig_ids: list[str] = []
        if receipt.result_type == "pig" and receipt.result_object_id:
            pig_ids.append(receipt.result_object_id)
        raw_pig_ids = payload.get("pig_instance_ids")
        if isinstance(raw_pig_ids, list):
            pig_ids.extend(str(value) for value in raw_pig_ids if value)
        raw_source_ids = payload.get("source_pig_instance_ids")
        if isinstance(raw_source_ids, list):
            pig_ids.extend(str(value) for value in raw_source_ids if value)
        source_id = str(payload.get("source_pig_instance_id") or "")
        if source_id:
            pig_ids.append(source_id)
        pigs = []
        for pig_id in dict.fromkeys(pig_ids):
            row = await self.repository.pig_row(session, pig_instance_id=pig_id)
            if row is not None:
                pigs.append(row)
        context["pigs"] = pigs
        food_ids: list[str] = []
        raw_food_ids = payload.get("food_instance_ids")
        if isinstance(raw_food_ids, list):
            food_ids.extend(str(value) for value in raw_food_ids if value)
        if receipt.result_type == "food-consumed" and receipt.result_object_id:
            food_ids.append(receipt.result_object_id)
        technique = payload.get("technique_resolution")
        if isinstance(technique, Mapping):
            generated = technique.get("generated_foods")
            if isinstance(generated, list):
                food_ids.extend(str(row.get("food_instance_id") or "") for row in generated if isinstance(row, Mapping))
        context["foods"] = await self.repository.food_rows(
            session, food_instance_ids=tuple(dict.fromkeys(value for value in food_ids if value))
        )
        effect_entry_ids: list[str] = []
        for pig in pigs:
            snapshot = _safe_mapping(str(pig.get("random_snapshot_json") or "{}"))
            raw_entries = snapshot.get("food_effect_entry_ids")
            if isinstance(raw_entries, list):
                effect_entry_ids.extend(str(value) for value in raw_entries if value)
        context["effect_source_names"] = await self.repository.food_effect_source_names(
            session,
            effect_entry_ids=tuple(dict.fromkeys(effect_entry_ids)),
        )
        return context

    def _event_signals(
        self,
        receipt: CommandReceipt,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> tuple[dict[str, bool], dict[str, set[str]], dict[str, int]]:
        flags: dict[str, bool] = {}
        additions: dict[str, set[str]] = {}
        deltas: dict[str, int] = {}
        pigs = [row for row in context.get("pigs", []) if isinstance(row, Mapping)]
        foods = [row for row in context.get("foods", []) if isinstance(row, Mapping)]
        names = {str(row.get("display_name_snapshot") or "") for row in (*pigs, *foods)}
        source_names = {str(row.get("display_name_snapshot") or "") for row in pigs}
        effect_source_names = set(str(value) for value in context.get("effect_source_names", set()))

        if receipt.result_type == "pig":
            pig = pigs[0] if pigs else {}
            rarity = int(pig.get("rarity") or 0)
            if rarity == 5:
                deltas["five_star_pigs"] = 1
            elif rarity == 6:
                deltas["six_star_pigs"] = 1
            flags["kfc_thursday_catch"] = "KFC猪" in names and self._is_beijing_thursday(receipt.created_at)
            flags["sugar_1004_burst"] = bool(payload.get("group_hidden_boost_triggered"))
            random_snapshot = _safe_mapping(str(pig.get("random_snapshot_json") or "{}"))
            flags["assam_auto_gift"] = bool(random_snapshot.get("auto_gift_target_player_id"))
            if payload.get("giant_sighting"):
                deltas["giant_sightings"] = 1
                deltas["size_board_entries"] = 1
                deltas["weight_board_entries"] = 1
                deltas["dual_board_entries"] = 1
            if payload.get("global_size_record"):
                deltas["size_record_breaks"] = 1
            if payload.get("global_weight_record"):
                deltas["weight_record_breaks"] = 1
            streak_six = rarity == 6
            flags["catch_is_six"] = streak_six
            summaries = " ".join(str(value) for value in payload.get("effect_summaries", []) if value)
            if "珍猪奶茶" in effect_source_names or "珍猪奶茶" in summaries:
                flags["pearl_effect_used"] = True
                additions["pearl_copy_results"] = {"1" if payload.get("duplication_triggered") else "0"}
            if "小马猪蒙布朗" in effect_source_names or "小马猪蒙布朗" in summaries:
                flags["xiaoma_effect_used"] = True
            technique = payload.get("technique_resolution")
            if isinstance(technique, Mapping) and str(technique.get("technique_id")) == "malevolent-kitchen":
                generated = technique.get("generated_foods")
                generated_rows = (
                    [row for row in generated if isinstance(row, Mapping)] if isinstance(generated, list) else []
                )
                flags["domain_six_star_cook"] = any(int(row.get("rarity") or 0) == 6 for row in generated_rows)
                generated_names = {str(row.get("display_name_snapshot") or "") for row in generated_rows}
                flags["domain_gojo_cook"] = (
                    "五条猪" in names and len(generated_names & {"五条猪无量苍蓝雪山", "五条猪无量赫焰雪山"}) == 2
                )
        if receipt.result_type in {"cooking", "batch-cooking"}:
            deltas["five_star_foods"] = sum(int(row.get("rarity") or 0) == 5 for row in foods)
            deltas["six_star_foods"] = sum(int(row.get("rarity") or 0) == 6 for row in foods)
            flags["kfc_bucket_cook"] = "KFC猪" in source_names and "炸猪全家桶" in names
            effect_text = " ".join(str(value) for value in payload.get("effect_summaries", []) if value)
            flags["aya_repair_return"] = "彩彩修车猪慕斯" in effect_text and "返还原料猪成功" in effect_text
            food_snapshots = [_safe_mapping(str(row.get("random_snapshot_json") or "{}")) for row in foods]
            temporary_probability_effect = any(
                snapshot.get("item_id")
                or snapshot.get("food_effect_entry_ids")
                or snapshot.get("exclusive_effect_active")
                or snapshot.get("domain_gojo_bypass")
                or snapshot.get("achievement_recook_roll") is not None
                for snapshot in food_snapshots
            )
            flags["natural_six_star_cook"] = (
                receipt.result_type == "cooking"
                and any(int(row.get("rarity") or 0) == 6 for row in foods)
                and not temporary_probability_effect
            )
            sushi_ids = {
                str(row.get("food_instance_id") or "")
                for row in foods
                if str(row.get("display_name_snapshot") or "") == "猪寿司拼盘"
            }
            if sushi_ids:
                additions["sushi_platter_instances"] = sushi_ids
        if receipt.result_type == "food-consumed":
            deltas["foods_eaten"] = 1
            flags["kfc_group_settlement"] = (
                "炸猪全家桶" in names and int(payload.get("group_rewarded_players") or 0) >= 1
            )
            flags["dragon_group_settlement"] = (
                "神龙化猪七星云海锅" in names and int(payload.get("group_rewarded_players") or 0) >= 1
            )
            if "猪寿司拼盘" in names:
                additions["sushi_platter_instances"] = {receipt.result_object_id}
        if receipt.result_type == "roulette-spin":
            outcome = int(payload.get("outcome") or 0)
            if 1 <= outcome <= 6:
                additions["roulette_faces"] = {str(outcome)}
        if receipt.result_type == "technique-activation":
            technique_id = str(payload.get("technique_id") or "")
            flags["domain_activated"] = technique_id == "malevolent-kitchen"
            additions["color_techniques"] = {technique_id} if technique_id in {"lapse-blue", "reversal-red"} else set()
        if receipt.result_type == "hollow-purple":
            flags["hollow_purple"] = True
        if receipt.result_type == "showcase":
            flags["showcase_set"] = bool(payload.get("asset")) or bool(receipt.result_object_id)
        asset = payload.get("asset")
        if (
            isinstance(asset, Mapping)
            and str(asset.get("display_name") or asset.get("display_name_snapshot") or "") == "猪寿司拼盘"
        ):
            additions["sushi_platter_instances"] = {str(asset.get("instance_id") or receipt.result_object_id)}
        self._apply_declarative_signals(
            receipt=receipt,
            payload=payload,
            flags=flags,
            additions=additions,
        )
        return flags, additions, deltas

    @staticmethod
    def _apply_declarative_signals(
        *,
        receipt: CommandReceipt,
        payload: Mapping[str, Any],
        flags: dict[str, bool],
        additions: dict[str, set[str]],
    ) -> None:
        """Evaluate future simple receipt achievements without service edits.

        An event definition may declare ``event_type`` plus optional
        ``payload_key``/``equals`` parameters. A set definition can additionally
        declare ``set_payload_key``. Complex multi-step mechanics still get a
        named reducer above, while ordinary new receipt achievements remain
        definition-only additions.
        """

        def payload_value(path: str) -> Any:
            current: Any = payload
            for part in path.split("."):
                if not isinstance(current, Mapping) or part not in current:
                    return None
                current = current[part]
            return current

        for definition in ACHIEVEMENT_DEFINITIONS:
            parameters = definition.condition.parameters
            expected_type = parameters.get("event_type")
            if expected_type is None:
                continue
            expected_types = (
                {str(value) for value in expected_type}
                if isinstance(expected_type, (list, tuple, set))
                else {str(expected_type)}
            )
            if receipt.result_type not in expected_types:
                continue
            payload_key = str(parameters.get("payload_key") or "")
            value = payload_value(payload_key) if payload_key else True
            if "equals" in parameters and value != parameters["equals"]:
                continue
            if definition.condition.kind is AchievementConditionKind.EVENT:
                flags[definition.condition.metric] = bool(value)
            set_key = str(parameters.get("set_payload_key") or "")
            if definition.condition.kind is AchievementConditionKind.SET and set_key:
                raw = payload_value(set_key)
                values = raw if isinstance(raw, list) else [raw]
                additions.setdefault(definition.condition.metric, set()).update(
                    str(item) for item in values if item is not None and str(item) != ""
                )

    async def _evaluate(
        self,
        session: DatabaseSession,
        *,
        definition: AchievementDefinition,
        player_id: str,
        scope_id: str,
        metrics: Mapping[str, int],
        previous_value: int,
        state: dict[str, Any],
        flags: Mapping[str, bool],
        additions: Mapping[str, set[str]],
        counter_deltas: Mapping[str, int],
        gift_partners: set[str],
        trade_partners: set[str],
        roulette_outcomes: set[str],
        sushi_instances: set[str],
        color_counts: tuple[int, int],
    ) -> tuple[int, int, dict[str, Any]]:
        condition = definition.condition
        metric = condition.metric
        target = condition.target
        if condition.kind is AchievementConditionKind.THRESHOLD:
            if metric == "fifty_no_six":
                value = 0 if flags.get("catch_is_six") else previous_value + (1 if "catch_is_six" in flags else 0)
                return value, 50, state
            return max(previous_value, int(metrics.get(metric, 0))) + int(counter_deltas.get(metric, 0)), target, state
        if condition.kind is AchievementConditionKind.EVENT:
            if metric == "fifty_no_six":
                streak = int(state.get("streak", previous_value))
                if "catch_is_six" in flags:
                    streak = 0 if flags["catch_is_six"] else streak + 1
                state["streak"] = streak
                return streak, 50, state
            if metric == "blue_red_pair":
                value = int(color_counts[0] > 0) + int(color_counts[1] > 0)
                return value, 2, state
            if metric == "pearl_tea_double_copy":
                used = int(state.get("used", 0))
                copied = int(state.get("copied", 0))
                if flags.get("pearl_effect_used"):
                    used += 1
                    copied += int("1" in additions.get("pearl_copy_results", set()))
                completed = used >= 2 and copied >= 2
                if used >= 2 and not completed:
                    used = copied = 0
                state.update({"used": used, "copied": copied})
                return int(completed), 1, state
            if metric in {"xiaoma_zero_six", "xiaoma_five_six"}:
                used = int(state.get("used", 0))
                sixes = int(state.get("sixes", 0))
                if flags.get("xiaoma_effect_used"):
                    used += 1
                    sixes += int(flags.get("catch_is_six", False))
                completed = used >= 5 and (
                    (metric == "xiaoma_zero_six" and sixes == 0) or (metric == "xiaoma_five_six" and sixes == 5)
                )
                if used >= 5 and not completed:
                    used = sixes = 0
                state.update({"used": used, "sixes": sixes})
                return int(completed), 1, state
            if metric == "millionaire_947947":
                active = metrics.get("coin_balance", 0) >= 947947 and metrics.get("ordinary_balance", 0) >= 947947
                return int(active), 1, state
            return int(bool(flags.get(metric))), 1, state
        if condition.kind is AchievementConditionKind.SET:
            items = set(str(value) for value in state.get("items", [])) | additions.get(metric, set())
            if metric == "gift_partners":
                items |= gift_partners
            elif metric == "trade_partners":
                items |= trade_partners
            elif metric == "roulette_faces":
                items |= roulette_outcomes
            elif metric == "sushi_platter_instances":
                items |= sushi_instances
            state["items"] = sorted(items)
            return len(items), target, state
        if metric.startswith("collection:"):
            value, captured_target = await self.repository.scope_target_counts(
                session,
                scope_id=scope_id,
                player_id=player_id,
                achievement_id=definition.achievement_id,
                mode="catalog",
            )
            return value, captured_target or 1, state
        mode = "giant" if metric == "all_template_giants" else "mini"
        value, captured_target = await self.repository.scope_target_counts(
            session, scope_id=scope_id, player_id=player_id, achievement_id=definition.achievement_id, mode=mode
        )
        return value, captured_target or 1, state

    async def _grant_rewards(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        source_key: str,
        rewards: tuple[AchievementReward, ...],
        now: str,
    ) -> None:
        for reward in rewards:
            if reward.reward_type == "coin":
                balance = await self.economy_repository.apply_currency_change(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    amount=reward.quantity,
                    reason_code="achievement-reward",
                    reason_text="成就奖励",
                    source_object_type="achievement",
                    source_object_id=source_key,
                    ledger_entry_id=str(uuid4()),
                    # The ledger key is globally unique, while each player in
                    # each scope is entitled to the same achievement reward.
                    # Old unlock rows still suppress re-grants after upgrading.
                    idempotency_key=f"{source_key}:{player_id}:coin",
                    now=now,
                )
                if balance is None:
                    raise RuntimeError("成就猪币奖励无法写入玩家余额。")
            elif reward.reward_type == "material":
                from ..infrastructure.repositories.materials import MaterialRepository

                await MaterialRepository().change(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    material_id=ACTIVITY_REWARDS[reward.reward_id]["material_id"],
                    delta_units=reward.quantity * MATERIAL_SCALE,
                    source_kind="achievement-reward",
                    source_id=source_key,
                    entry_key=f"{source_key}:{player_id}:{reward.reward_id}",
                    now=now,
                )
            else:
                await self.repository.grant_reward(
                    session,
                    player_id=player_id,
                    reward_type=reward.reward_type,
                    reward_id=reward.reward_id,
                    quantity=reward.quantity,
                    now=now,
                )

    async def _settle_milestones(self, session: DatabaseSession, *, player_id: str, scope_id: str, now: str) -> None:
        profile = await self.repository.profile_row(session, player_id=player_id)
        points = int(profile["achievement_points"] if profile else 0)
        for milestone, rewards in _MILESTONE_REWARDS.items():
            if points < milestone:
                continue
            rewards_json = _json(
                [{"type": item.reward_type, "id": item.reward_id, "quantity": item.quantity} for item in rewards]
            )
            if await self.repository.claim_milestone(
                session, player_id=player_id, milestone_points=milestone, rewards_json=rewards_json, now=now
            ):
                await self._grant_rewards(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    source_key=f"achievement-milestone:{milestone}",
                    rewards=rewards,
                    now=now,
                )

    async def _settle_regular_completion(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        now: str,
    ) -> None:
        regular_ids = LEGACY_REGULAR_IDS
        if await self.repository.regular_unlock_count(
            session,
            player_id=player_id,
            achievement_ids=regular_ids,
        ) != len(regular_ids):
            return
        operation_key = f"achievement-regular-completion:{player_id}"
        if await self.repository.operation_result(session, operation_key=operation_key):
            return
        await self.repository.grant_reward(
            session,
            player_id=player_id,
            reward_type="chest",
            reward_id="regular-five-star-memorial",
            quantity=1,
            now=now,
        )
        await self.repository.insert_operation(
            session,
            operation_key=operation_key,
            player_id=player_id,
            operation_type="regular-achievement-completion",
            result_json=_json({"reward_id": "regular-five-star-memorial"}),
            now=now,
        )

    @staticmethod
    def _is_beijing_thursday(value: str) -> bool:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.astimezone(timezone(timedelta(hours=8))).weekday() == 3

    async def pending_unlocks(self, *, player_id: str, receipt_id: str) -> tuple[AchievementUnlock, ...]:
        async with self.database.transaction(immediate=False) as session:
            rows = await self.repository.pending_unlock_rows(session, player_id=player_id, receipt_id=receipt_id)
        return tuple(
            AchievementUnlock(
                achievement_id=str(row["achievement_id"]),
                name=str(row["name"]),
                tier=AchievementTier(str(row["tier"])),
                points=int(row["points"]),
                rewards=tuple(
                    AchievementReward(str(item["type"]), str(item["id"]), int(item["quantity"]))
                    for item in json.loads(str(row["rewards_json"]))
                ),
                unlocked_at=str(row["unlocked_at"]),
            )
            for row in rows
        )

    async def claim_notifications(self, *, player_id: str, receipt_id: str) -> tuple[str, ...]:
        now = _now_text(self.clock)
        async with self.database.transaction() as session:
            rows = await self.repository.pending_unlock_rows(session, player_id=player_id, receipt_id=receipt_id)
            ids = tuple(str(row["unlock_id"]) for row in rows)
            if ids and await self.repository.update_notification_status(
                session, unlock_ids=ids, from_status="pending", to_status="claimed", error="", now=now
            ):
                return ids
            return ()

    async def mark_notifications(self, unlock_ids: tuple[str, ...], *, sent: bool, error: str = "") -> None:
        if not unlock_ids:
            return
        async with self.database.transaction() as session:
            await self.repository.update_notification_status(
                session,
                unlock_ids=unlock_ids,
                from_status="claimed",
                to_status="sent" if sent else "failed",
                error=error,
                now=_now_text(self.clock),
            )

    async def claim_backfill_summary(
        self, *, player_id: str
    ) -> tuple[tuple[str, ...], AchievementBackfillSummary] | None:
        now = _now_text(self.clock)
        async with self.database.transaction() as session:
            rows = await self.repository.backfill_summary_rows(session, player_id=player_id)
            if not rows:
                return None
            unlock_ids = tuple(str(row["unlock_id"]) for row in rows)
            if not await self.repository.update_notification_status(
                session,
                unlock_ids=unlock_ids,
                from_status="summary",
                to_status="claimed",
                error="",
                now=now,
            ):
                return None
            profile = await self.repository.profile_row(session, player_id=player_id)
        reward_totals: dict[tuple[str, str], int] = {}
        for row in rows:
            for item in json.loads(str(row["rewards_json"])):
                key = (str(item["type"]), str(item["id"]))
                reward_totals[key] = reward_totals.get(key, 0) + int(item["quantity"])
        rewards = tuple(
            AchievementReward(reward_type, reward_id, quantity)
            for (reward_type, reward_id), quantity in sorted(reward_totals.items())
        )
        return unlock_ids, AchievementBackfillSummary(
            display_name=str(profile["display_name"] if profile else "本群玩家"),
            unlocked_count=len(rows),
            total_points=sum(int(row["points"]) for row in rows),
            rewards=rewards,
            highlights=tuple(str(row["name"]) for row in rows[:8]),
        )

    async def overview(self, identity: CommandIdentity) -> AchievementOverview:
        await self._ensure_identity_profile(identity)
        async with self.database.transaction(immediate=False) as session:
            profile = await self.repository.profile_row(session, player_id=identity.player_id)
            rows = await self.repository.list_achievement_rows(session, player_id=identity.player_id, category=None)
            rewards = await self.repository.reward_rows(session, player_id=identity.player_id)
        if profile is None:
            raise RuntimeError("无法读取成就档案。")
        entries = tuple(self._entry_from_row(row) for row in rows)
        recent = tuple(
            sorted((entry for entry in entries if entry.unlocked), key=lambda item: item.unlocked_at, reverse=True)[:5]
        )
        points = int(profile["achievement_points"])
        next_milestone = next((value for value in _MILESTONE_REWARDS if value > points), None)
        return AchievementOverview(
            str(profile["display_name"]),
            points,
            int(profile["unlocked_count"]),
            len(ACHIEVEMENT_DEFINITIONS),
            _REWARD_NAMES.get(
                str(profile["equipped_title_id"]),
                str(profile["equipped_title_id"]),
            ),
            str(profile["equipped_frame_id"]),
            next(
                (
                    definition.name
                    for definition in ACHIEVEMENT_DEFINITIONS
                    if definition.achievement_id == str(profile["showcase_achievement_id"])
                ),
                _REWARD_NAMES.get(
                    str(profile["showcase_achievement_id"]),
                    str(profile["showcase_achievement_id"]),
                ),
            ),
            next_milestone,
            tuple((str(row["reward_type"]), str(row["reward_id"]), int(row["quantity"])) for row in rewards),
            recent,
        )

    async def page(self, identity: CommandIdentity, *, category: str | None, page: int) -> AchievementPage:
        await self._ensure_identity_profile(identity)
        normalized = str(category or "").strip() or None
        valid_categories = {item.category for item in ACHIEVEMENT_DEFINITIONS}
        if normalized is not None and normalized not in valid_categories:
            raise DomainValidationError("成就分类不存在，可用分类：" + "、".join(sorted(valid_categories)))
        async with self.database.transaction(immediate=False) as session:
            profile = await self.repository.profile_row(session, player_id=identity.player_id)
            rows = await self.repository.list_achievement_rows(
                session, player_id=identity.player_id, category=normalized
            )
        total = len(rows)
        page_count = max(1, math.ceil(total / 10))
        selected_page = max(1, min(int(page), page_count))
        sliced = rows[(selected_page - 1) * 10 : selected_page * 10]
        return AchievementPage(
            str(profile["display_name"] if profile else identity.display_name),
            normalized or "全部",
            selected_page,
            page_count,
            total,
            tuple(self._entry_from_row(row) for row in sliced),
        )

    async def detail(self, identity: CommandIdentity, name: str) -> AchievementEntry:
        await self._ensure_identity_profile(identity)
        async with self.database.transaction(immediate=False) as session:
            rows = await self.repository.list_achievement_rows(session, player_id=identity.player_id, category=None)
        matches = [
            self._entry_from_row(row) for row in rows if str(row["name"]).casefold() == str(name).strip().casefold()
        ]
        if not matches:
            raise DomainValidationError("找不到这个成就名称。")
        return matches[0]

    async def detail_page(self, identity: CommandIdentity, name: str) -> AchievementPage:
        entry = await self.detail(identity, name)
        return AchievementPage(
            display_name=identity.display_name,
            category="成就详情",
            page=1,
            page_count=1,
            total_count=1,
            entries=(entry,),
        )

    async def equip_title(self, identity: CommandIdentity, title_id: str) -> bool:
        await self._ensure_identity_profile(identity)
        async with self.database.transaction() as session:
            return await self.repository.update_equipped_title(
                session, player_id=identity.player_id, title_id=title_id, now=_now_text(self.clock)
            )

    async def equip_title_by_achievement(self, identity: CommandIdentity, name: str) -> str:
        entry = await self.detail(identity, name)
        if not entry.unlocked:
            raise DomainValidationError("这个成就尚未解锁，不能佩戴其称号。")
        title = next((reward.reward_id for reward in entry.rewards if reward.reward_type == "title"), "")
        if not title:
            raise DomainValidationError("这个成就没有可佩戴称号奖励。")
        if not await self.equip_title(identity, title):
            raise DomainValidationError("称号奖励尚未进入库存。")
        return title

    async def equip_cosmetics_by_achievement(self, identity: CommandIdentity, name: str) -> tuple[str, ...]:
        entry = await self.detail(identity, name)
        if not entry.unlocked:
            raise DomainValidationError("这个成就尚未解锁，不能佩戴其外观。")
        title = next(
            (reward.reward_id for reward in entry.rewards if reward.reward_type == "title"),
            None,
        )
        frame = next(
            (reward.reward_id for reward in entry.rewards if reward.reward_type == "frame"),
            None,
        )
        has_badge = any(reward.reward_type == "badge" for reward in entry.rewards)
        if title is None and frame is None and not has_badge:
            raise DomainValidationError("这个成就没有可佩戴的称号、边框或徽章。")
        now = _now_text(self.clock)
        async with self.database.transaction() as session:
            updated = await self.repository.update_equipped_cosmetics(
                session,
                player_id=identity.player_id,
                title_id=title,
                frame_id=frame,
                showcase_achievement_id=(entry.achievement_id if has_badge else None),
                now=now,
            )
        if not updated:
            raise DomainValidationError("成就外观奖励尚未进入库存。")
        return tuple(
            value
            for value in (
                f"称号·{title}" if title else "",
                f"边框·{frame}" if frame else "",
                f"徽章·{entry.name}" if has_badge else "",
            )
            if value
        )

    async def clear_equipped_cosmetics(self, identity: CommandIdentity) -> None:
        await self._ensure_identity_profile(identity)
        async with self.database.transaction() as session:
            await self.repository.clear_equipped_cosmetics(
                session,
                player_id=identity.player_id,
                now=_now_text(self.clock),
            )

    async def open_choice_chest(self, identity: CommandIdentity, choice: str) -> tuple[AchievementReward, ...]:
        choices: Mapping[str, tuple[AchievementReward, ...]] = {
            "抓猪": (AchievementReward("ticket", "achievement-catch", 3), AchievementReward("ticket", "giant-rescale")),
            "做菜": (AchievementReward("ticket", "recook", 2), AchievementReward("ticket", "food-inspiration")),
            "图鉴": (AchievementReward("ticket", "catalog-guide", 2), AchievementReward("ticket", "food-inspiration")),
            "外观": (
                AchievementReward("frame", "achievement-choice-pink"),
                AchievementReward("badge", "achievement-choice"),
            ),
        }
        rewards = choices.get(str(choice or "").strip())
        if rewards is None:
            raise DomainValidationError("宝箱类型只能填写：抓猪、做菜、图鉴、外观。")
        await self._ensure_identity_profile(identity)
        now = _now_text(self.clock)
        operation_key = f"achievement-chest:{identity.player_id}:{identity.message_id or 'no-message'}"
        async with self.database.transaction() as session:
            existing = await self.repository.operation_result(session, operation_key=operation_key)
            if existing is not None:
                raw = json.loads(existing)
                return tuple(
                    AchievementReward(str(item["type"]), str(item["id"]), int(item["quantity"])) for item in raw
                )
            consumed = await self.repository.consume_reward(
                session,
                player_id=identity.player_id,
                reward_type="chest",
                reward_id="achievement-choice",
                quantity=1,
                now=now,
            )
            if not consumed:
                raise DomainValidationError("你没有可用的成就自选宝箱。")
            await self._grant_rewards(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                source_key=operation_key,
                rewards=rewards,
                now=now,
            )
            await self.repository.insert_operation(
                session,
                operation_key=operation_key,
                player_id=identity.player_id,
                operation_type="open-choice-chest",
                result_json=_json(
                    [{"type": item.reward_type, "id": item.reward_id, "quantity": item.quantity} for item in rewards]
                ),
                now=now,
            )
        return rewards

    async def activate_ticket(
        self, identity: CommandIdentity, ticket_name: str, *, track_achievements: bool = True
    ) -> str:
        normalized = str(ticket_name or "").strip()
        ticket = _TICKET_NAMES.get(normalized)
        if ticket is None:
            raise DomainValidationError("可使用的成就券：" + "、".join(_TICKET_NAMES))
        ticket_id, action_type = ticket
        if track_achievements:
            await self._ensure_identity_profile(identity)
        now = _now_text(self.clock)
        operation_key = f"achievement-ticket:{identity.player_id}:{identity.message_id or 'no-message'}"
        async with self.database.transaction() as session:
            if not track_achievements:
                await self.framework_repository.touch_identity(session, identity=identity, now=now)
            existing = await self.repository.operation_result(session, operation_key=operation_key)
            if existing is not None:
                return str(json.loads(existing).get("ticket_id") or ticket_id)
            if ticket_id in {"giant-rescale", "mini-rescale"}:
                active = await self.repository.active_ticket_ids(
                    session,
                    player_id=identity.player_id,
                    action_type="catching",
                )
                opposite = "mini-rescale" if ticket_id == "giant-rescale" else "giant-rescale"
                if opposite in active:
                    raise DomainValidationError("巨物复秤券与迷你复秤券不能同时待命，请先完成当前抓猪。")
            consumed = await self.repository.consume_reward(
                session,
                player_id=identity.player_id,
                reward_type="ticket",
                reward_id=ticket_id,
                quantity=1,
                now=now,
            )
            if not consumed:
                raise DomainValidationError(f"你没有可用的{normalized}。")
            await self.repository.activate_ticket(
                session,
                effect_entry_id=str(uuid4()),
                player_id=identity.player_id,
                ticket_id=ticket_id,
                action_type=action_type,
                uses=1,
                now=now,
            )
            await self.repository.insert_operation(
                session,
                operation_key=operation_key,
                player_id=identity.player_id,
                operation_type="activate-achievement-ticket",
                result_json=_json({"ticket_id": ticket_id}),
                now=now,
            )
        return ticket_id

    async def reforge_identifier(
        self,
        identity: CommandIdentity,
        *,
        asset_kind: str,
        old_code: str,
        new_code: str,
    ) -> str:
        kind = {"猪猪": "pig", "猪": "pig", "美食": "food", "菜": "food"}.get(str(asset_kind or "").strip())
        if kind is None:
            raise DomainValidationError("资产类型只能填写猪猪或美食。")
        normalized_old = normalize_short_code(old_code)
        normalized_new = normalize_short_code(new_code)
        if normalized_old == normalized_new:
            raise DomainValidationError("新旧编号不能相同。")
        await self._ensure_identity_profile(identity)
        now = _now_text(self.clock)
        operation_key = f"achievement-reforge:{identity.player_id}:{identity.message_id or 'no-message'}"
        async with self.database.transaction() as session:
            existing = await self.repository.operation_result(session, operation_key=operation_key)
            if existing is not None:
                return str(json.loads(existing).get("new_code") or normalized_new)
            consumed = await self.repository.consume_reward(
                session,
                player_id=identity.player_id,
                reward_type="ticket",
                reward_id="identifier-reforge",
                quantity=1,
                now=now,
            )
            if not consumed:
                raise DomainValidationError("你没有可用的编号重铸券。")
            instance_id = await self.repository.reforge_short_code(
                session,
                player_id=identity.player_id,
                asset_kind=kind,
                old_code=normalized_old,
                new_code=normalized_new,
                now=now,
            )
            if instance_id is None:
                raise DomainValidationError("找不到这件可重铸资产，或新编号已被猪猪/美食占用。")
            await self.repository.insert_operation(
                session,
                operation_key=operation_key,
                player_id=identity.player_id,
                operation_type="reforge-asset-identifier",
                result_json=_json(
                    {
                        "asset_kind": kind,
                        "instance_id": instance_id,
                        "old_code": normalized_old,
                        "new_code": normalized_new,
                    }
                ),
                now=now,
            )
        return normalized_new

    async def claim_memorial_pig(self, identity: CommandIdentity, template_name: str) -> AchievementMemorialPig:
        normalized = str(template_name or "").strip()
        if not normalized:
            raise DomainValidationError("请填写尚未收集的公共五星猪名称。")
        await self._ensure_identity_profile(identity)
        now = _now_text(self.clock)
        operation_key = f"achievement-memorial-pig:{identity.player_id}:{identity.message_id or 'no-message'}"
        async with self.database.transaction() as session:
            existing = await self.repository.operation_result(session, operation_key=operation_key)
            if existing is not None:
                payload = _safe_mapping(existing)
                return AchievementMemorialPig(str(payload["display_name"]), str(payload["short_code"]))
            template = await self.repository.memorial_pig_template(
                session,
                player_id=identity.player_id,
                template_name=normalized,
            )
            if template is None:
                raise DomainValidationError("找不到尚未收集的同名公共五星猪；六星、KFC猪和群私有模板不可选择。")
            consumed = await self.repository.consume_reward(
                session,
                player_id=identity.player_id,
                reward_type="chest",
                reward_id="regular-five-star-memorial",
                quantity=1,
                now=now,
            )
            if not consumed:
                raise DomainValidationError("你还没有完成全部常规成就，或纪念赠礼已经领取。")
            rng = random.SystemRandom()
            rolls = tuple(rng.random() for _ in range(5))
            attributes = generate_pig_attributes(
                rarity=5,
                length_min=float(template["length_min"]),
                length_max=float(template["length_max"]),
                weight_min=float(template["weight_min"]),
                weight_max=float(template["weight_max"]),
                fat_profile=str(template["fat_profile"]),
                random_values=rolls,
            )
            short_code = ""
            for _ in range(128):
                candidate = new_short_code()
                if not await self.gameplay_repository.short_code_exists(session, candidate):
                    short_code = candidate
                    break
            if not short_code:
                raise RuntimeError("无法生成唯一的成就纪念猪编号。")
            pig_instance_id = str(uuid4())
            await self.gameplay_repository.insert_pig_instance(
                session,
                values={
                    "pig_instance_id": pig_instance_id,
                    "short_code": short_code,
                    "scope_id": identity.scope.value,
                    "owner_player_id": identity.player_id,
                    "template_id": str(template["template_id"]),
                    "template_version": int(template["template_version"]),
                    "rarity": 5,
                    "display_name_snapshot": str(template["display_name"]),
                    "size_value": attributes.size_value,
                    "size_percentile": attributes.size_percentile,
                    "weight_value": attributes.weight_value,
                    "weight_percentile": attributes.weight_percentile,
                    "fat_ratio": attributes.fat_ratio,
                    "official_value": attributes.official_value,
                    "ruleset_version": RULESET_VERSION,
                    "random_snapshot_json": _json(
                        {
                            "source": "achievement-commemorative",
                            "achievement_rolls": rolls,
                            "statistics_granted": False,
                            "catch_reward_granted": False,
                        }
                    ),
                    "acquired_at": now,
                    "updated_at": now,
                },
            )
            await self.gameplay_repository.upsert_pig_catalog(
                session,
                player_id=identity.player_id,
                template_id=str(template["template_id"]),
                size_value=attributes.size_value,
                weight_value=attributes.weight_value,
                now=now,
            )
            result = AchievementMemorialPig(str(template["display_name"]), short_code)
            await self.repository.insert_operation(
                session,
                operation_key=operation_key,
                player_id=identity.player_id,
                operation_type="claim-achievement-memorial-pig",
                result_json=_json(
                    {
                        "pig_instance_id": pig_instance_id,
                        "display_name": result.display_name,
                        "short_code": result.short_code,
                    }
                ),
                now=now,
            )
        return result

    async def player_display_name(self, player_id: str) -> str:
        async with self.database.transaction(immediate=False) as session:
            profile = await self.repository.profile_row(session, player_id=player_id)
        return safe_display_name(str(profile["display_name"] if profile else "本群玩家"), player_id.rsplit(":", 1)[-1])

    async def cosmetics_for_player(self, player_id: str) -> AchievementCosmetics:
        async with self.database.transaction(immediate=False) as session:
            profile = await self.repository.profile_row(session, player_id=player_id)
        return AchievementCosmetics(
            _REWARD_NAMES.get(
                str(profile["equipped_title_id"] if profile else ""),
                str(profile["equipped_title_id"] if profile else ""),
            ),
            str(profile["equipped_frame_id"] if profile else ""),
            str(profile["showcase_achievement_id"] if profile else ""),
            next(
                (
                    next(
                        (_REWARD_NAMES.get(r.reward_id, d.name) for r in d.rewards if r.reward_type == "badge"), d.name
                    )
                    for d in ACHIEVEMENT_DEFINITIONS
                    if profile and d.achievement_id == profile["showcase_achievement_id"]
                ),
                "",
            ),
        )

    async def ranking(self, identity: CommandIdentity, *, page: int) -> AchievementRanking:
        await self._ensure_identity_profile(identity)
        async with self.database.transaction(immediate=False) as session:
            total = await self.repository.ranking_count(session, scope_id=identity.scope.value)
            page_count = max(1, math.ceil(total / 10))
            selected_page = max(1, min(int(page), page_count))
            rows = await self.repository.ranking_rows(
                session, scope_id=identity.scope.value, limit=10, offset=(selected_page - 1) * 10
            )
        return AchievementRanking(
            identity.group_name or identity.scope.group_id,
            selected_page,
            page_count,
            total,
            tuple(
                AchievementRankingEntry(
                    int(row["rank"]),
                    str(row["display_name"]),
                    int(row["achievement_points"]),
                    int(row["unlocked_count"]),
                )
                for row in rows
            ),
        )

    async def _ensure_identity_profile(self, identity: CommandIdentity) -> None:
        now = _now_text(self.clock)
        async with self.database.transaction() as session:
            if not self._definitions_ready:
                await self._sync_definitions(session, now=now)
            await self.framework_repository.touch_identity(session, identity=identity, now=now)
            await self.repository.ensure_profile(session, player_id=identity.player_id, now=now)
            await self._capture_scope_targets(session, scope_id=identity.scope.value, now=now)
            await self._maybe_backfill(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                now=now,
            )
        await self.process_activity_facts(
            scope_id=identity.scope.value, receipt_id=f"activity-query:{identity.message_id}"
        )

    @staticmethod
    def _entry_from_row(row: Mapping[str, object]) -> AchievementEntry:
        unlocked = bool(row.get("unlocked_at"))
        hidden = bool(row.get("hidden"))
        condition = _safe_mapping(str(row.get("condition_json") or "{}"))
        state = _safe_mapping(str(row.get("state_json") or "{}"))
        target = max(1, int(state.get("target") or condition.get("target") or 1))
        rewards_raw = json.loads(str(row.get("rewards_json") or "[]"))
        rewards = tuple(
            AchievementReward(str(item["type"]), str(item["id"]), int(item["quantity"])) for item in rewards_raw
        )
        name = str(row["name"]) if unlocked or not hidden else "？？？"
        description = str(row["description"]) if unlocked or not hidden else str(row["hint"])
        return AchievementEntry(
            str(row["achievement_id"]),
            name,
            str(row["category"]),
            AchievementTier(str(row["tier"])),
            TIER_LABELS[AchievementTier(str(row["tier"]))],
            hidden,
            unlocked,
            description,
            str(row["hint"]),
            int(row.get("progress_value") or 0) if unlocked or not hidden else 0,
            target if unlocked or not hidden else 1,
            int(row["points"]),
            rewards if unlocked or not hidden else (),
            str(row.get("unlocked_at") or ""),
        )


def reward_label(reward: AchievementReward) -> str:
    labels = {
        "coin": "猪币",
        "ticket": "玩法券",
        "title": "称号",
        "frame": "边框",
        "badge": "徽章",
        "chest": "自选宝箱",
        "cosmetic": "展示外观",
        "material": "材料",
    }
    display_name = _REWARD_NAMES.get(reward.reward_id, reward.reward_id)
    if reward.reward_type == "coin":
        return f"{display_name} ×{reward.quantity}"
    return f"{labels.get(reward.reward_type, reward.reward_type)}·{display_name} ×{reward.quantity}"


def format_achievement_unlocks(unlocks: tuple[AchievementUnlock, ...]) -> str:
    lines = ["【PiG Dream! 成就已解锁】"]
    for unlock in unlocks[:UNLOCK_SUMMARY_LIMIT]:
        rewards = "、".join(reward_label(item) for item in unlock.rewards) or "成就点"
        lines.append(f"{TIER_LABELS[unlock.tier]} · {unlock.name}（+{unlock.points} 点）\n奖励：{rewards}")
    if len(unlocks) > UNLOCK_SUMMARY_LIMIT:
        lines.append(f"另有{len(unlocks) - UNLOCK_SUMMARY_LIMIT}项已达成，全部奖励已到账；/猪猪成就 分页查看。")
    return "\n".join(lines)


__all__ = [
    "AchievementBackfillSummary",
    "AchievementCosmetics",
    "AchievementMemorialPig",
    "AchievementEntry",
    "AchievementOverview",
    "AchievementPage",
    "AchievementRanking",
    "AchievementRankingEntry",
    "AchievementService",
    "format_achievement_unlocks",
    "reward_label",
]
