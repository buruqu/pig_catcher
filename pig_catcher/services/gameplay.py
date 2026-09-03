"""Third-round catching, collection, records, and item application services."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..config.model import CatchingSection, LaunchCampaignSection, RankingSection
from ..domain.display import display_tags_from_json, format_length, format_measurement, format_weight
from ..domain.economy import (
    generate_food_attributes,
    level_cooking_higher_rarity_multiplier,
)
from ..domain.enums import AssetKind, Rarity, RecordType, StatureProfile
from ..domain.errors import (
    AmbiguousPigSelectorError,
    CatchCooldownError,
    DailyCatchLimitError,
    DomainValidationError,
    ItemInventoryError,
    NoDrawableTemplateError,
    PigNotFoundError,
    ReceiptConflictError,
    TechniqueError,
)
from ..domain.food_effects import (
    CATCH_DUPLICATION_CHANCE,
    CATCH_EFFECT_IDS,
    QUOTA_EXEMPT_CATCH_EFFECTS,
    active_effect_from_row,
    active_group_effect_from_row,
    active_quota_effect_bonuses,
    add_six_star_probability_points,
    apply_catch_effects,
    apply_group_catch_effects,
    apply_group_hidden_boost,
    apply_six_star_progress,
    group_hidden_boost_chance,
    has_compatible_exclusive_catch_effect,
    has_compatible_exclusive_group_catch_effect,
    resolve_food_effect,
)
from ..domain.gameplay import (
    CATCH_COIN_REWARDS,
    CATCH_EXPERIENCE_REWARDS,
    COIN_BOUNTY_REWARD_MULTIPLIER,
    PIG_RARITY_NAMES,
    ItemDefinition,
    LevelProgress,
    PigAttributes,
    generate_pig_attributes,
    item_by_id,
    item_by_name,
    level_progress,
    size_label,
    veteran_benefits,
    weight_label,
)
from ..domain.launch_campaign import (
    apply_first_day_high_star_weights,
    first_day_active,
)
from ..domain.launch_campaign import (
    effective_window_limit as campaign_window_limit,
)
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, RandomSource, SystemClock, SystemRandomSource
from ..domain.quota import catch_quota_window, stack_catch_quota_layers
from ..domain.rules import (
    LEVEL_CATCH_BONUS_CAP_LEVEL,
    catch_weights,
    choose_rarity,
    normalize_weights,
)
from ..domain.selectors import parse_asset_selector
from ..domain.short_codes import is_valid_short_code, new_short_code
from ..domain.social import describe_body_scale
from ..domain.special_content import (
    GOJO_BLUE_FOOD_TEMPLATE_ID,
    GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS,
    GOJO_PIG_TEMPLATE_ID,
    GOJO_RED_FOOD_TEMPLATE_ID,
    KFC_FOOD_TEMPLATE_ID,
    KFC_PIG_TEMPLATE_ID,
    SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS,
    SUKUNA_FOOD_TEMPLATE_ID,
    SUKUNA_PIG_TEMPLATE_ID,
    TECHNIQUE_DISPLAY_NAMES,
    TECHNIQUE_DOMAIN_GOJO_BYPASS,
    TECHNIQUE_HOLLOW_PURPLE,
    TECHNIQUE_LAPSE_BLUE,
    TECHNIQUE_MALEVOLENT_KITCHEN,
    TECHNIQUE_REVERSAL_RED,
    domain_cooking_weights,
    is_crazy_thursday,
)
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    AchievementRepository,
    AssetRepository,
    EconomyRepository,
    FrameworkRepository,
    GameplayRepository,
    QuotaRepository,
    ReceiptRepository,
    RestrictionRepository,
    SocialRepository,
    TechniqueRepository,
)
from ..infrastructure.repositories.restrictions import CATCH_WINDOW_LIMIT
from ..version import RULESET_VERSION
from .assets import CollectionProgress
from .command_state import (
    iso_timestamp,
    receipt_payload,
    valid_page_count,
    validate_existing_receipt,
)
from .receipts import request_fingerprint
from .veteran_rewards import settle_veteran_rewards

_CATCH_COMMAND = "pig-catcher.catch"
# 达妮娅泡泡云冻每层抓猪六星概率加成（百分点），与正式目录效果参数一致。
_DANIYA_CATCH_BONUS_PER_STACK = 0.2
_ARM_ITEM_COMMAND = "pig-catcher.arm-item"
_CANCEL_ITEM_COMMAND = "pig-catcher.cancel-item"
_FAT_LABELS = {
    "lean": "偏瘦",
    "balanced": "均衡",
    "fatty": "偏肥",
}
_CATCH_PROBABILITY_ITEM_IDS = frozenset({"lucky-whistle", "super-lucky-whistle", "star-pig-radar"})


@dataclass(frozen=True, slots=True)
class PigView:
    """A stable, path-free view of one owned pig instance."""

    pig_instance_id: str
    short_code: str
    scope_id: str
    owner_player_id: str
    owner_display_name: str
    template_id: str
    template_version: int
    rarity: int
    display_name: str
    description: str
    size_value: float
    size_percentile: float
    weight_value: float
    weight_percentile: float
    fat_ratio: float
    official_value: int
    acquired_at: str
    image_relpath: str
    image_fit: str
    media_format: str
    is_animated: bool
    frame_count: int
    media_visible: bool
    collection_name: str
    collection_total: int
    character_name: str
    is_size_record: bool
    is_weight_record: bool
    stature_profile: str = StatureProfile.STANDARD.value
    is_global_size_record: bool = False
    is_global_weight_record: bool = False
    is_giant_sighting: bool = False
    body_label: str = ""
    body_description: str = ""
    giant_score: float = 0.0
    paired_food_template_id: str = ""
    display_variant: str = "pig"
    alternate_image_relpath: str = ""
    is_favorite: bool = False
    activity_label: str = ""
    display_tags: tuple[str, ...] = ()

    @property
    def stars(self) -> str:
        return "★" * self.rarity

    @property
    def rarity_name(self) -> str:
        return PIG_RARITY_NAMES[Rarity(self.rarity)]

    @property
    def selector(self) -> str:
        return f"{self.display_name}#{self.short_code}"

    @property
    def fat_category(self) -> str:
        if self.fat_ratio <= 35:
            return "lean"
        if self.fat_ratio <= 64:
            return "balanced"
        return "fatty"

    @property
    def fat_label(self) -> str:
        return _FAT_LABELS[self.fat_category]


@dataclass(frozen=True, slots=True)
class CatchResult:
    """Committed catch result and its one-time delivery receipt."""

    pig: PigView
    receipt: CommandReceipt
    receipt_created: bool
    daily_count: int
    daily_limit: int
    coin_reward: int
    experience_reward: int
    coin_balance: int
    total_experience: int
    catalog_new: bool
    size_record: bool
    weight_record: bool
    feed_level: int
    item_id: str
    item_name: str
    weights: tuple[float, ...]
    item_remaining_uses: int = 0
    effect_summaries: tuple[str, ...] = ()
    excluded_summaries: tuple[str, ...] = ()
    exclusive_effect_active: bool = False
    quota_exempt_catch: bool = False
    global_size_record: bool = False
    global_weight_record: bool = False
    giant_sighting: bool = False
    technique_resolution: TechniqueCatchResolution | None = None
    veteran_coin_reward: int = 0
    veteran_reward_levels: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class TechniqueFoodView:
    """One food serving created by a group technique."""

    food_instance_id: str
    short_code: str
    owner_player_id: str
    owner_display_name: str
    rarity: int
    display_name: str
    image_relpath: str
    image_fit: str
    media_format: str
    is_animated: bool
    media_visible: bool

    @property
    def selector(self) -> str:
        return f"{self.display_name}#{self.short_code}"


@dataclass(frozen=True, slots=True)
class TechniqueCatchResolution:
    """Structured public settlement for a catch intercepted by a technique."""

    technique_id: str
    technique_name: str
    source_player_id: str
    source_display_name: str
    target_player_id: str
    target_display_name: str
    remaining_uses: int
    summary: str
    generated_foods: tuple[TechniqueFoodView, ...] = ()


@dataclass(frozen=True, slots=True)
class PlayerProfile:
    """Current player state used by the profile card."""

    display_name: str
    coin_balance: int
    total_experience: int
    level: LevelProgress
    total_catches: int
    active_pigs: int
    catalog_count: int
    visible_catalog_total: int
    held_records: int
    daily_count: int
    daily_limit: int
    cooldown_remaining_seconds: int
    feed_level: int
    armed_item: ItemDefinition | None
    armed_item_quantity: int
    cookware_level: int
    total_cooks: int
    active_foods: int
    food_catalog_count: int
    visible_food_catalog_total: int
    armed_cooking_item: ItemDefinition | None
    armed_cooking_item_quantity: int
    collections: tuple[CollectionProgress, ...]
    showcase_pig: str = ""
    showcase_food: str = ""
    level_catch_base_high_percent: float = 0.0
    level_catch_adjusted_high_percent: float = 0.0
    level_cooking_bonus_percent: float = 0.0
    level_bonus_cap_level: int = LEVEL_CATCH_BONUS_CAP_LEVEL
    veteran_tier: int = 0
    veteran_catch_coin_bonus: int = 0
    veteran_cook_coin_bonus: int = 0
    veteran_experience_bonus_percent: int = 0
    veteran_milestone_coin_reward: int = 0
    veteran_cumulative_coin_reward: int = 0
    veteran_claimed_tier: int = 0
    veteran_next_tier_level: int | None = 21
    veteran_next_tier_coin_reward: int | None = 1_000


@dataclass(frozen=True, slots=True)
class InventoryPage:
    """A filtered page of active pig instances."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    page_size: int
    rarity: int | None
    sort: str
    pigs: tuple[PigView, ...]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One visible catalog slot; undiscovered entries remain masked by the view."""

    template_id: str
    display_name: str
    rarity: int
    description: str
    image_relpath: str
    image_fit: str
    media_format: str
    is_animated: bool
    frame_count: int
    collection_id: str
    collection_name: str
    collection_slot: int | None
    collection_total: int
    character_name: str
    discovered: bool
    acquired_count: int
    best_size: float | None
    best_weight: float | None
    first_acquired_at: str
    last_acquired_at: str
    display_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """A complete privacy-aware pig catalog."""

    display_name: str
    total_count: int
    rarity: int | None
    undiscovered_only: bool
    collected_count: int
    visible_catalog_total: int
    entries: tuple[CatalogEntry, ...]
    collections: tuple[CollectionProgress, ...]


@dataclass(frozen=True, slots=True)
class RecordEntry:
    """One group record for size or weight."""

    record_type: str
    record_value: float
    achieved_at: str
    display_name: str
    rarity: int
    short_code: str
    holder_display_name: str
    player_id: str = ""

    @property
    def record_label(self) -> str:
        return "体型" if self.record_type == RecordType.SIZE.value else "重量"

    @property
    def unit(self) -> str:
        return "cm" if self.record_type == RecordType.SIZE.value else "kg"


@dataclass(frozen=True, slots=True)
class RecordsPage:
    """Current-group pig records."""

    group_name: str
    page: int
    page_count: int
    total_count: int
    page_size: int
    entries: tuple[RecordEntry, ...]
    global_entries: tuple[RecordEntry, ...] = ()
    giant_sightings: tuple[GiantSightingEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class GiantSightingEntry:
    """One immutable current-group absolute-threshold sighting."""

    display_name: str
    rarity: int
    short_code: str
    holder_display_name: str
    size_value: float
    weight_value: float
    giant_score: float
    size_qualified: bool
    weight_qualified: bool
    achieved_at: str
    player_id: str = ""


@dataclass(frozen=True, slots=True)
class DailyGiantEntry:
    """One player's best pig for one of today's two giant rankings."""

    rank: int
    player_id: str
    holder_display_name: str
    pig_instance_id: str
    display_name: str
    rarity: int
    short_code: str
    size_value: float
    weight_value: float
    acquired_at: str
    image_relpath: str
    image_fit: str
    media_visible: bool
    is_animated: bool


@dataclass(frozen=True, slots=True)
class DailyGiants:
    """Today's current-group best-size and best-weight leaderboards."""

    group_name: str
    date_label: str
    participant_count: int
    catch_count: int
    size_entries: tuple[DailyGiantEntry, ...]
    weight_entries: tuple[DailyGiantEntry, ...]


@dataclass(frozen=True, slots=True)
class ItemActionResult:
    """Committed item equip or cancellation result."""

    receipt: CommandReceipt
    receipt_created: bool
    operation: str
    item: ItemDefinition
    quantity: int
    armed_uses: int = 0


@dataclass(frozen=True, slots=True)
class TechniqueActivationResult:
    """One idempotent group-technique activation or immediate Hollow Purple."""

    receipt: CommandReceipt
    receipt_created: bool
    technique_id: str
    summary: str
    total_uses: int = 0
    remaining_permits: int = 0
    purple_unlocked: int = 0
    granted_pigs: tuple[PigView, ...] = ()


def _safe_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptConflictError("数据库中的抓猪时间格式无效。") from exc
    return _safe_datetime(parsed)


def _cooldown_remaining(
    *,
    now: datetime,
    last_acquired_at: str | None,
    cooldown_seconds: int,
) -> int:
    if cooldown_seconds <= 0 or not last_acquired_at:
        return 0
    elapsed = (_safe_datetime(now) - _parse_timestamp(last_acquired_at)).total_seconds()
    return max(0, math.ceil(cooldown_seconds - elapsed))


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def pig_view_from_row(
    row: Mapping[str, object],
    *,
    giant_size_threshold_cm: float = 120.0,
    giant_weight_threshold_kg: float = 350.0,
) -> PigView:
    stature_profile = str(row.get("stature_profile") or StatureProfile.STANDARD.value)
    body = describe_body_scale(
        stature_profile=stature_profile,
        size_value=float(row["size_value"]),
        size_percentile=float(row["size_percentile"]),
        weight_value=float(row["weight_value"]),
        weight_percentile=float(row["weight_percentile"]),
        giant_size_threshold_cm=giant_size_threshold_cm,
        giant_weight_threshold_kg=giant_weight_threshold_kg,
    )
    display_variant = str(row.get("display_variant") or "pig")
    image_relpath = str(row.get("image_relpath") or "")
    alternate_image_relpath = str(row.get("alternate_image_relpath") or "")
    if display_variant == "sticker" and alternate_image_relpath:
        image_relpath = alternate_image_relpath
    return PigView(
        pig_instance_id=str(row["pig_instance_id"]),
        short_code=str(row["short_code"]),
        scope_id=str(row["scope_id"]),
        owner_player_id=str(row["owner_player_id"]),
        owner_display_name=str(row.get("owner_display_name") or ""),
        template_id=str(row["template_id"]),
        template_version=int(row["template_version"]),
        rarity=int(row["rarity"]),
        display_name=str(row["display_name_snapshot"]),
        description=str(row.get("description") or ""),
        size_value=float(row["size_value"]),
        size_percentile=float(row["size_percentile"]),
        weight_value=float(row["weight_value"]),
        weight_percentile=float(row["weight_percentile"]),
        fat_ratio=float(row["fat_ratio"]),
        official_value=int(row["official_value"]),
        acquired_at=str(row["acquired_at"]),
        image_relpath=image_relpath,
        image_fit=str(row.get("image_fit") or "contain"),
        media_format=str(row.get("media_format") or "PNG"),
        is_animated=bool(row.get("is_animated") or False),
        frame_count=int(row.get("frame_count") or 1),
        media_visible=bool(row.get("media_visible", True)),
        collection_name=str(row.get("collection_name") or ""),
        collection_total=int(row.get("collection_total") or 0),
        character_name=str(row.get("character_name") or ""),
        stature_profile=stature_profile,
        is_size_record=bool(row.get("is_size_record") or False),
        is_weight_record=bool(row.get("is_weight_record") or False),
        is_global_size_record=bool(row.get("is_global_size_record") or False),
        is_global_weight_record=bool(row.get("is_global_weight_record") or False),
        is_giant_sighting=bool(row.get("is_giant_sighting") or False),
        body_label=body.label,
        body_description=body.description,
        giant_score=body.giant_score,
        paired_food_template_id=str(row.get("paired_food_template_id") or ""),
        display_variant=display_variant,
        alternate_image_relpath=alternate_image_relpath,
        display_tags=(
            display_tags_from_json(row.get("display_tags_json")) if bool(row.get("media_visible", True)) else ()
        ),
        is_favorite=bool(row.get("is_favorite") or False),
        activity_label={"dispatch": "派遣中", "tour": "巡演中", "battle": "对战中"}.get(
            str(row.get("busy_purpose") or ""),
            " / ".join(
                label
                for label, active in (
                    ("乐队保护", row.get("tour_protected")),
                    ("战斗保护", row.get("battle_protected")),
                )
                if active
            ),
        ),
    )


def _catalog_from_row(row: Mapping[str, object]) -> CatalogEntry:
    discovered = bool(row["discovered"])
    return CatalogEntry(
        template_id=str(row["template_id"]),
        display_name=str(row["display_name"]),
        rarity=int(row["rarity"]),
        description=str(row["description"]),
        image_relpath=str(row["image_relpath"]),
        image_fit=str(row["image_fit"]),
        media_format=str(row["media_format"]),
        is_animated=bool(row["is_animated"]),
        frame_count=int(row["frame_count"]),
        collection_id=str(row["collection_id"] or ""),
        collection_name=str(row["collection_name"] or ""),
        collection_slot=int(row["collection_slot"]) if row["collection_slot"] is not None else None,
        collection_total=int(row["collection_total"] or 0),
        character_name=str(row["character_name"] or ""),
        discovered=discovered,
        acquired_count=int(row["acquired_count"] or 0),
        best_size=_optional_float(row["best_size"]),
        best_weight=_optional_float(row["best_weight"]),
        first_acquired_at=str(row["first_acquired_at"] or ""),
        last_acquired_at=str(row["last_acquired_at"] or ""),
        display_tags=(display_tags_from_json(row.get("display_tags_json")) if discovered else ()),
    )


def _collection_from_row(row: Mapping[str, object]) -> CollectionProgress:
    return CollectionProgress(
        collection_id=str(row["collection_id"]),
        collection_name=str(row["collection_name"]),
        collaboration_name=str(row["collaboration_name"]),
        collected_count=int(row["collected_count"]),
        available_count=int(row["available_count"]),
        total_count=int(row["collection_total"]),
    )


def _format_collection_rows(rows: Sequence[Mapping[str, object]]) -> tuple[CollectionProgress, ...]:
    return tuple(_collection_from_row(row) for row in rows)


def _format_probability(value: float) -> str:
    """Keep small six-star multipliers visible without making normal cards noisy."""

    rounded_tenth = round(float(value), 1)
    if math.isclose(float(value), rounded_tenth, abs_tol=0.0005):
        return f"{value:.1f}"
    return f"{value:.3f}"


def format_catch_summary(result: CatchResult) -> str:
    """Return a complete path-free text fallback for a catch."""

    progress = level_progress(result.total_experience)
    records: list[str] = []
    if result.size_record:
        records.append("体型新纪录")
    if result.weight_record:
        records.append("重量新纪录")
    if result.global_size_record:
        records.append("全群体型最高")
    if result.global_weight_record:
        records.append("全群重量最高")
    if result.giant_sighting:
        records.append("巨物目击已留档")
    record_text = "、".join(records) if records else "未刷新群纪录"
    item_text = result.item_name or "无"
    if result.item_name:
        item_text += f"（连续使用队列剩余 {result.item_remaining_uses} 次）"
    new_text = "NEW｜首次收入图鉴\n" if result.catalog_new else ""
    body_text = f"体格：{result.pig.body_label}｜{result.pig.body_description}\n" if result.pig.body_label else ""
    tags_text = f"标签：{' · '.join(result.pig.display_tags)}\n" if result.pig.display_tags else ""
    effect_text = f"\n美食加成：{'；'.join(result.effect_summaries)}" if result.effect_summaries else ""
    excluded_text = f"\n互斥未叠加：{'；'.join(result.excluded_summaries)}" if result.excluded_summaries else ""
    quota_text = "\n专属次数：本次未消耗正常抓猪额度" if result.quota_exempt_catch else ""
    veteran_text = (
        "\n资深里程碑："
        + "、".join(f"Lv.{level}" for level in result.veteran_reward_levels)
        + f" 一次性发放 +{result.veteran_coin_reward:,} 猪币"
        if result.veteran_coin_reward
        else ""
    )
    probability_line = " ".join(
        f"{index + 1}★{_format_probability(value)}%" for index, value in enumerate(result.weights) if value > 0
    )
    probability_source_parts = [
        f"等级 Lv.{progress.level}",
        f"饲料 Lv.{result.feed_level}",
    ]
    if result.item_name:
        probability_source_parts.append(f"道具·{result.item_name}")
    if result.effect_summaries:
        probability_source_parts.append(f"美食加成 ×{len(result.effect_summaries)}")
    probability_sources = (
        "六星菜独占规则（等级、升级、道具与其他菜品均未参与）"
        if result.exclusive_effect_active
        else "、".join(probability_source_parts)
    )
    return (
        "【抓猪成功】\n"
        f"{result.pig.owner_display_name} 抓到了 {result.pig.stars} {result.pig.display_name}\n"
        f"{new_text}"
        f"编号：{result.pig.selector}\n"
        f"品质：{result.pig.rarity_name}\n"
        f"体型：{format_length(result.pig.size_value)}\n"
        f"重量：{format_weight(result.pig.weight_value)}\n"
        f"体态：{result.pig.fat_label}\n"
        f"{body_text}"
        f"{tags_text}"
        f"官方价值：{result.pig.official_value} 猪币\n"
        f"奖励：+{result.coin_reward} 猪币 / +{result.experience_reward} 经验\n"
        f"等级：Lv.{progress.level} · {progress.title}；"
        f"{result.total_experience}/{progress.next_threshold} EXP\n"
        f"当前余额：{result.coin_balance} 猪币\n"
        f"本时段抓猪：{result.daily_count}/{result.daily_limit}\n"
        f"本次道具：{item_text}\n"
        f"本次最终概率：{probability_line}\n"
        f"概率来源：{probability_sources}\n"
        f"群纪录：{record_text}{effect_text}{excluded_text}{quota_text}{veteran_text}"
    )


def format_profile_summary(profile: PlayerProfile) -> str:
    """Return a complete text fallback for the player profile."""

    next_text = (
        "已达当前最高等级"
        if profile.level.next_threshold is None
        else f"距下一等级 {profile.level.next_threshold - profile.total_experience} 经验"
    )
    armed = (
        f"{profile.armed_item.display_name}（剩余 {profile.armed_item_quantity} 次）"
        if profile.armed_item is not None
        else "无"
    )
    cooking_armed = (
        f"{profile.armed_cooking_item.display_name}（剩余 {profile.armed_cooking_item_quantity} 次）"
        if profile.armed_cooking_item is not None
        else "无"
    )
    veteran_next = (
        "已达最高档"
        if profile.veteran_next_tier_level is None
        else (f"下一档 Lv.{profile.veteran_next_tier_level} 奖励 {profile.veteran_next_tier_coin_reward:,} 猪币")
    )
    veteran_claim = (
        "全部已领取" if profile.veteran_claimed_tier >= profile.veteran_tier else "有已达成奖励待在下次获得经验时补领"
    )
    return (
        "【抓猪档案】\n"
        f"玩家：{profile.display_name}\n"
        f"称号：Lv.{profile.level.level} {profile.level.title}（{profile.total_experience} 经验，{next_text}）\n"
        f"猪币：{profile.coin_balance}\n"
        f"累计抓取：{profile.total_catches}；当前持有：{profile.active_pigs}\n"
        f"猪猪图鉴：{profile.catalog_count}/{profile.visible_catalog_total}\n"
        f"累计做菜：{profile.total_cooks}；当前美食：{profile.active_foods}\n"
        f"美食图鉴：{profile.food_catalog_count}/{profile.visible_food_catalog_total}\n"
        f"猪猪展示：{profile.showcase_pig or '未设置'}\n"
        f"美食展示：{profile.showcase_food or '未设置'}\n"
        f"当前持有群纪录：{profile.held_records}\n"
        f"猪饲料：Lv.{profile.feed_level}；厨具：Lv.{profile.cookware_level}\n"
        "等级概率加成：抓猪 4-6 星 "
        f"{profile.level_catch_base_high_percent:.2f}% → "
        f"{profile.level_catch_adjusted_high_percent:.2f}%；"
        f"普通做菜高档权重 +{profile.level_cooking_bonus_percent:.2f}%"
        f"（Lv.{profile.level_bonus_cap_level} 封顶）\n"
        f"资深里程碑：{profile.veteran_tier}/5 档；本档一次性奖励 "
        f"{profile.veteran_milestone_coin_reward:,} 猪币，累计可得 "
        f"{profile.veteran_cumulative_coin_reward:,} 猪币；{veteran_claim}（{veteran_next}）\n"
        f"本时段抓猪：{profile.daily_count}/{profile.daily_limit}\n"
        f"抓猪冷却：{profile.cooldown_remaining_seconds} 秒\n"
        f"已装备抓猪道具：{armed}\n"
        f"已装备做菜道具：{cooking_armed}"
    )


def format_pig_detail_summary(pig: PigView) -> str:
    """Return a complete text fallback for an owned pig."""

    records: list[str] = []
    if pig.is_size_record:
        records.append("本群体型纪录")
    if pig.is_weight_record:
        records.append("本群重量纪录")
    if pig.is_global_size_record:
        records.append("全群体型最高")
    if pig.is_global_weight_record:
        records.append("全群重量最高")
    if pig.is_giant_sighting:
        records.append("巨物目击留档")
    body = (
        f"体格：{pig.body_label}（巨物分 {pig.giant_score:.1f}）\n体格评价：{pig.body_description}\n"
        if pig.body_label
        else ""
    )
    collection = (
        f"联动：{pig.collection_name}{' · ' + pig.character_name if pig.character_name else ''}\n"
        if pig.collection_name
        else "联动：非联动猪\n"
    )
    return (
        "【猪猪详情】\n"
        f"{pig.stars} {pig.display_name}（{pig.rarity_name}）\n"
        f"编号：{pig.selector}{'（已收藏保护）' if pig.is_favorite else ''}\n"
        f"状态：{pig.activity_label or '空闲'}\n"
        f"{collection}"
        f"体型：{format_length(pig.size_value, include_base=True)}（{size_label(pig.size_percentile)}）\n"
        f"重量：{format_weight(pig.weight_value, include_base=True)}（{weight_label(pig.weight_percentile)}）\n"
        f"体态：{pig.fat_label}\n"
        f"{body}"
        f"官方价值：{pig.official_value} 猪币\n"
        f"群纪录：{'、'.join(records) if records else '无'}\n"
        f"获得时间：{pig.acquired_at}\n"
        f"描述：{pig.description}" + (f"\n标签：{' · '.join(pig.display_tags)}" if pig.display_tags else "")
    )


def format_inventory_summary(result: InventoryPage) -> str:
    """Return a concise text fallback for an inventory page."""

    lines = [
        "【猪猪背包】",
        f"玩家：{result.display_name}",
        f"第 {result.page}/{result.page_count} 页；共 {result.total_count} 只；排序：{result.sort}",
    ]
    if result.rarity is not None:
        lines.append(f"品质筛选：{result.rarity} 星")
    if not result.pigs:
        lines.append("当前没有符合条件的猪猪。")
    for pig in result.pigs:
        lines.append(
            f"{pig.stars} {pig.selector}｜{format_length(pig.size_value)}｜"
            f"{format_weight(pig.weight_value)}｜{pig.official_value}猪币"
            + (f"｜{' · '.join(pig.display_tags[:2])}" if pig.display_tags else "")
        )
    return "\n".join(lines)


def format_catalog_summary(result: CatalogPage) -> str:
    """Return a privacy-preserving text fallback for a complete catalog."""

    lines = [
        "【猪猪图鉴】",
        f"玩家：{result.display_name}",
        (
            f"按品质完整排列；本筛选 {result.total_count} 项；"
            f"总进度 {result.collected_count}/{result.visible_catalog_total}"
        ),
    ]
    if not result.entries:
        lines.append("当前没有符合条件的图鉴条目。")
    current_rarity: int | None = None
    for entry in result.entries:
        if entry.rarity != current_rarity:
            current_rarity = entry.rarity
            lines.append(f"【{current_rarity} 星品质】")
        if not entry.discovered:
            lines.append(f"{'★' * entry.rarity} ???｜尚未发现")
            continue
        animation = "｜动态" if entry.is_animated else ""
        lines.append(
            f"{'★' * entry.rarity} {entry.display_name}｜已抓 {entry.acquired_count} 次"
            f"｜最佳 {format_length(entry.best_size or 0)} / {format_weight(entry.best_weight or 0)}{animation}"
            + (f"｜{' · '.join(entry.display_tags[:2])}" if entry.display_tags else "")
        )
    return "\n".join(lines)


def format_records_summary(result: RecordsPage) -> str:
    """Return a complete text fallback for current-group records."""

    lines = [
        "【猪猪纪录】",
        f"群：{result.group_name or '当前群'}",
        f"第 {result.page}/{result.page_count} 页；共 {result.total_count} 项",
    ]
    if not result.entries:
        lines.append("本群还没有猪猪纪录。")
    for entry in result.entries:
        lines.append(
            f"{'★' * entry.rarity} {entry.display_name}#{entry.short_code}｜"
            f"{entry.record_label} {format_measurement(entry.record_value, entry.unit)}｜"
            f"{entry.holder_display_name}"
        )
    if result.global_entries:
        lines.append("—— 全群绝对纪录 ——")
        for entry in result.global_entries:
            lines.append(
                f"{entry.record_label}最高｜{'★' * entry.rarity} "
                f"{entry.display_name}#{entry.short_code}｜"
                f"{format_measurement(entry.record_value, entry.unit)}｜"
                f"{entry.holder_display_name}"
            )
    if result.giant_sightings:
        lines.append("—— 最近巨物目击 ——")
        for sighting in result.giant_sightings:
            lines.append(
                f"{'★' * sighting.rarity} "
                f"{sighting.display_name}#{sighting.short_code}｜"
                f"{format_length(sighting.size_value)} / {format_weight(sighting.weight_value)}｜"
                f"{sighting.giant_score:.1f}分｜{sighting.holder_display_name}"
            )
    return "\n".join(lines)


def format_daily_giants_summary(result: DailyGiants) -> str:
    """Return a readable fallback for today's two current-group rankings."""

    lines = [
        f"【今日巨物 · {result.date_label}】",
        f"群：{result.group_name or '当前群'}",
        f"今日抓猪：{result.catch_count} 只；参榜：{result.participant_count} 人",
    ]
    if not result.size_entries:
        lines.append("今天还没有抓到猪猪。")
        return "\n".join(lines)
    lines.append("—— 最大体型榜 ——")
    for entry in result.size_entries:
        lines.append(
            f"{entry.rank}. {entry.holder_display_name}｜{'★' * entry.rarity} "
            f"{entry.display_name}#{entry.short_code}｜{format_length(entry.size_value)}｜"
            f"{format_weight(entry.weight_value)}"
        )
    lines.append("—— 最重体重榜 ——")
    for entry in result.weight_entries:
        lines.append(
            f"{entry.rank}. {entry.holder_display_name}｜{'★' * entry.rarity} "
            f"{entry.display_name}#{entry.short_code}｜{format_weight(entry.weight_value)}｜"
            f"{format_length(entry.size_value)}"
        )
    return "\n".join(lines)


def format_item_action_summary(result: ItemActionResult) -> str:
    """Return a text fallback for item equip or cancellation."""

    if result.operation == "armed":
        return (
            "【道具已装备】\n"
            f"{result.item.display_name} 已安排连续 {result.armed_uses} 次兼容的"
            f"{'抓猪' if result.item.action_type == 'catching' else '做菜'}成功结算。\n"
            f"当前库存：{result.quantity}\n"
            f"效果：{result.item.effect_summary}"
        )
    return (
        f"【道具已取消】\n已取消 {result.item.display_name} 的连续使用队列"
        f"（原剩余 {result.armed_uses} 次），道具未被消耗。\n当前库存：{result.quantity}"
    )


class GameplayService:
    """Own all third-round state transitions and group-scoped reads."""

    def __init__(
        self,
        database: PigCatcherDatabase,
        catching: CatchingSection,
        *,
        ranking: RankingSection | None = None,
        repository: GameplayRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        receipt_repository: ReceiptRepository | None = None,
        asset_repository: AssetRepository | None = None,
        economy_repository: EconomyRepository | None = None,
        social_repository: SocialRepository | None = None,
        restriction_repository: RestrictionRepository | None = None,
        quota_repository: QuotaRepository | None = None,
        technique_repository: TechniqueRepository | None = None,
        achievement_repository: AchievementRepository | None = None,
        random_source: RandomSource | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        short_code_factory: Callable[[], str] | None = None,
        launch_campaign: LaunchCampaignSection | None = None,
    ) -> None:
        self.database = database
        self.catching = catching
        self.ranking = ranking or RankingSection()
        self.repository = repository or GameplayRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.receipt_repository = receipt_repository or ReceiptRepository()
        self.asset_repository = asset_repository or AssetRepository()
        self.economy_repository = economy_repository or EconomyRepository()
        self.social_repository = social_repository or SocialRepository()
        self.restriction_repository = restriction_repository or RestrictionRepository()
        self.quota_repository = quota_repository or QuotaRepository()
        self.technique_repository = technique_repository or TechniqueRepository()
        self.achievement_repository = achievement_repository or AchievementRepository()
        self.random_source = random_source or SystemRandomSource()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.short_code_factory = short_code_factory or new_short_code
        self.launch_campaign = launch_campaign or LaunchCampaignSection()

    async def catch(self, identity: CommandIdentity) -> CatchResult:
        """Commit exactly one catch for one source message."""

        request_payload = {"command_version": 1}
        idempotency_key = MessageKeyFactory.build(identity, _CATCH_COMMAND)
        async with self.database.transaction() as session:
            now_datetime = _safe_datetime(self.clock.now())
            now = iso_timestamp(now_datetime)
            quota_window = catch_quota_window(
                now_datetime,
                refresh_hours=self.catching.quota_refresh_hours,
                timezone_name=self.catching.daily_reset_timezone,
            )
            window_start = iso_timestamp(quota_window.start)
            window_end = iso_timestamp(quota_window.end)
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_CATCH_COMMAND,
                    request_payload=request_payload,
                )
                return await self._catch_from_receipt(session, existing, receipt_created=False)

            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            daily_count, total_catch_count, last_acquired_at = await self.repository.catch_usage(
                session,
                player_id=identity.player_id,
                window_start=window_start,
                window_end=window_end,
            )
            active_effects = tuple(
                active_effect_from_row(row)
                for row in await self.economy_repository.list_active_food_effects(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            )
            active_group_effects = tuple(
                active_group_effect_from_row(row)
                for row in await self.economy_repository.list_active_group_food_effects(
                    session,
                    scope_id=identity.scope.value,
                    player_id=identity.player_id,
                    now=now,
                )
            )
            window_transfer = await self.economy_repository.active_catch_window_transfer(
                session,
                player_id=identity.player_id,
                now=now,
            )
            transfer_blocked = bool(
                window_transfer is not None
                and str(window_transfer["blocked_window_start"]) <= now < str(window_transfer["blocked_window_end"])
            )
            transfer_target_active = bool(
                window_transfer is not None
                and str(window_transfer["target_window_start"]) <= now < str(window_transfer["target_window_end"])
            )
            if transfer_blocked:
                raise DailyCatchLimitError(
                    "月栖萤光卷正在封存本时段抓猪额度；本时段不能抓猪，"
                    f"全部额度将在 {str(window_transfer['target_window_start'])} 开始的下个时段返还。"
                )
            current_window_bonus, today_window_bonus = active_quota_effect_bonuses(active_effects)
            extra_granted, extra_consumed = await self.economy_repository.extra_catch_grants(
                session,
                player_id=identity.player_id,
                now=now,
            )
            permanent_bonus, weekly_bonus = await self.economy_repository.catch_quota_bonuses(
                session,
                player_id=identity.player_id,
                now=now,
            )
            catch_restriction = await self.restriction_repository.active_restriction(
                session,
                player_id=identity.player_id,
                restriction_type=CATCH_WINDOW_LIMIT,
                now=now,
            )
            achievement_catch_tickets = await self.achievement_repository.active_ticket_ids(
                session,
                player_id=identity.player_id,
                action_type="catching",
            )
            achievement_visual_tickets = await self.achievement_repository.active_ticket_ids(
                session,
                player_id=identity.player_id,
                action_type="visual",
            )
            window_boost = await self.quota_repository.active_window_boost(
                session,
                scope_id=identity.scope.value,
                window_start=window_start,
            )
            if window_boost is not None:
                # 提额窗口：本时段额度按提升值计算，且无视玩家违规限制
                quota_layers = stack_catch_quota_layers(
                    configured_base=int(window_boost["limit_value"]),
                    extra_granted=extra_granted,
                    extra_consumed=extra_consumed,
                )
                catch_restriction = None
            else:
                configured_base_limit = campaign_window_limit(
                    self.launch_campaign,
                    now_datetime,
                    normal_limit=self.catching.daily_limit,
                )
                quota_layers = stack_catch_quota_layers(
                    configured_base=configured_base_limit,
                    permanent_bonus=permanent_bonus,
                    weekly_bonus=weekly_bonus,
                    current_window_bonus=current_window_bonus,
                    today_window_bonus=today_window_bonus,
                    extra_granted=extra_granted,
                    extra_consumed=extra_consumed,
                )
            base_window_limit = quota_layers.base_window_limit
            normal_daily_limit = quota_layers.effective_limit(used_count=daily_count)
            if transfer_target_active:
                transferred_uses = int(window_transfer["transferred_uses"])
                base_window_limit += transferred_uses
                normal_daily_limit += transferred_uses
            daily_limit = self._restricted_daily_limit(
                normal_limit=normal_daily_limit,
                restriction=catch_restriction,
            )
            templates = await self.repository.list_drawable_pig_templates(
                session,
                scope_id=identity.scope.value,
            )
            if not is_crazy_thursday(
                now_datetime,
                timezone_name=self.catching.daily_reset_timezone,
            ):
                templates = [template for template in templates if str(template["template_id"]) != KFC_PIG_TEMPLATE_ID]
            if not templates:
                raise NoDrawableTemplateError("当前群没有可用猪猪素材，请联系管理员导入并启用素材。")
            active_group_technique = await self.technique_repository.active_group_effect(
                session,
                scope_id=identity.scope.value,
            )
            deferred_duplication_effects = (
                tuple(effect for effect in active_effects if effect.effect_id == CATCH_DUPLICATION_CHANCE)
                if active_group_technique is not None
                else ()
            )
            applicable_active_effects = (
                tuple(effect for effect in active_effects if effect.effect_id != CATCH_DUPLICATION_CHANCE)
                if deferred_duplication_effects
                else active_effects
            )
            buckets = self._template_buckets(templates)
            feed_level = await self.repository.get_feed_level(
                session,
                player_id=identity.player_id,
            )
            probability_experience = await self.repository.get_player_experience(
                session,
                player_id=identity.player_id,
            )
            probability_level = level_progress(probability_experience).level
            armed_row = await self.repository.get_armed_item(
                session,
                player_id=identity.player_id,
                action_type="catching",
            )
            armed_item, armed_uses = self._armed_item(armed_row, "catching")
            equipped_item = armed_item
            group_exclusive_effect_active = (
                False
                if transfer_target_active
                else has_compatible_exclusive_group_catch_effect(active_group_effects)
            )
            personal_exclusive_effect_active = (
                not transfer_target_active
                and not group_exclusive_effect_active
                and has_compatible_exclusive_catch_effect(
                    applicable_active_effects,
                    six_star_available=bool(buckets[Rarity.SIX]),
                )
            )
            # 六星菜独占效果：回到未受等级、饲料、道具和普通菜影响的基础层。
            exclusive_effect_active = (
                transfer_target_active
                or group_exclusive_effect_active
                or personal_exclusive_effect_active
            )
            deferred_achievement_tickets = bool(
                exclusive_effect_active and (achievement_catch_tickets or achievement_visual_tickets)
            )
            if exclusive_effect_active:
                armed_item = None
                achievement_catch_tickets = frozenset()
                achievement_visual_tickets = frozenset()
            weights = self._available_weights(
                buckets=buckets,
                feed_level=0 if exclusive_effect_active else feed_level,
                player_level=1 if exclusive_effect_active else probability_level,
                item_id=armed_item.item_id if armed_item is not None else "",
            )
            if transfer_target_active:
                effect_application = apply_catch_effects(weights, ())
                group_effect_application = apply_group_catch_effects(weights, ())
                weights = normalize_weights(json.loads(str(window_transfer["fixed_weights_json"])))
                effect_summaries = (
                    "月栖萤光卷平移时段：本时段额外返还 "
                    f"{int(window_transfer['transferred_uses'])} 次额度，品质固定为4星42% / 5星40% / 6星18%。",
                )
                excluded_summaries = tuple(
                    resolve_food_effect(effect.effect_id, effect.params).summary
                    + "（本次由月栖萤光卷固定分布接管，保留且未消耗）"
                    for effect in active_effects
                    if effect.effect_id in CATCH_EFFECT_IDS
                )
                if active_group_effects:
                    excluded_summaries += ("当前全群六星菜概率效果在平移时段保留且未消耗。",)
                if equipped_item is not None:
                    excluded_summaries += (
                        f"已装备的“{equipped_item.display_name}”在平移时段保留且未消耗。",
                    )
            elif group_exclusive_effect_active:
                effect_application = apply_catch_effects(weights, ())
                group_effect_application = apply_group_catch_effects(
                    weights,
                    active_group_effects,
                )
                weights = group_effect_application.weights
                effect_summaries = group_effect_application.summaries
                excluded_summaries = group_effect_application.skipped_summaries
                excluded_summaries += tuple(
                    resolve_food_effect(effect.effect_id, effect.params).summary
                    + "（本次由全群六星菜独占，未生效且未消耗）"
                    for effect in active_effects
                    if effect.effect_id in CATCH_EFFECT_IDS
                )
                if equipped_item is not None:
                    excluded_summaries += (
                        f"已装备的“{equipped_item.display_name}”受全群六星菜独占规则影响，本次未生效且未消耗。",
                    )
            else:
                effect_application = apply_catch_effects(
                    weights,
                    applicable_active_effects,
                    random_value=self.random_source.random,
                    shuffle_base_weights=self.catching.weights(),
                )
                weights = effect_application.weights
                if effect_application.shuffle_permutation:
                    # 纯概率换位，不叠加任何成长；无六星授权时才转入五星。
                    if not buckets[Rarity.SIX]:
                        available_weights = list(weights)
                        available_weights[4] += available_weights[5]
                        available_weights[5] = 0.0
                        weights = tuple(available_weights)
                    weights = normalize_weights(
                        tuple(
                            weight if buckets[rarity] else 0.0 for rarity, weight in zip(Rarity, weights, strict=True)
                        )
                    )
                effect_summaries = effect_application.summaries
                excluded_summaries = effect_application.skipped_summaries
                if personal_exclusive_effect_active:
                    group_effect_application = apply_group_catch_effects(weights, ())
                    if active_group_effects:
                        excluded_summaries += ("当前全群六星菜加成本次由个人六星菜独占规则接管，未参与结算。",)
                elif effect_application.collaboration_only:
                    group_effect_application = apply_group_catch_effects(weights, ())
                    if active_group_effects:
                        excluded_summaries += ("当前全群六星菜概率加成不改变联动猪固定品质分布，本次未参与结算。",)
                else:
                    group_effect_application = apply_group_catch_effects(
                        weights,
                        active_group_effects,
                    )
                    weights = group_effect_application.weights
                    effect_summaries += group_effect_application.summaries
                    excluded_summaries += group_effect_application.skipped_summaries
            if deferred_achievement_tickets:
                excluded_summaries += ("已装备的临时成就券本次受六星独占规则影响，保留且不消耗。",)
            if deferred_duplication_effects and not exclusive_effect_active:
                excluded_summaries += tuple(
                    resolve_food_effect(effect.effect_id, effect.params).summary
                    + "（当前由群体术式接管猪猪归属，复制效果保留未消耗）"
                    for effect in deferred_duplication_effects
                )
            window_resonance = await self.economy_repository.active_window_resonance(
                session,
                player_id=identity.player_id,
                now=now,
            )
            candidate_buckets = buckets
            if effect_application.collaboration_only:
                collaboration_templates = [
                    template for template in templates if str(template.get("collection_id") or "").strip()
                ]
                candidate_buckets = self._template_buckets(collaboration_templates)
                available = tuple(
                    weight if candidate_buckets[rarity] else 0.0 for rarity, weight in zip(Rarity, weights, strict=True)
                )
                if not any(available):
                    raise NoDrawableTemplateError("当前群没有可用联动猪，效果已保留且本次抓猪未结算。")
                weights = normalize_weights(available)
                if armed_item is not None and armed_item.item_id in _CATCH_PROBABILITY_ITEM_IDS:
                    excluded_summaries += (
                        f"已装备的“{armed_item.display_name}”不改变联动猪固定品质分布，本次保留未消耗。",
                    )
                    armed_item = None
            consumed_effect_ids = {effect.effect_entry_id: effect.effect_id for effect in active_effects}
            quota_exempt_catch = any(
                consumed_effect_ids.get(entry_id) in QUOTA_EXEMPT_CATCH_EFFECTS
                for entry_id in effect_application.consumed_entry_ids
            )
            quota_exempt_catch = bool(
                quota_exempt_catch
                or group_effect_application.dedicated_entry_id
                or group_effect_application.quota_exempt
            )
            achievement_catch_ticket_used = "achievement-catch" in achievement_catch_tickets and not quota_exempt_catch
            if achievement_catch_ticket_used:
                quota_exempt_catch = True
                effect_summaries += ("成就抓猪券：本次为专属普通抓猪，不消耗正常时段额度。",)
            if catch_restriction is not None and total_catch_count >= daily_limit:
                expiry = str(catch_restriction.get("expires_at") or "")
                raise DailyCatchLimitError(
                    "账号处于违规处理期，"
                    f"本时段额度限制为 {daily_limit} 次；限制截止："
                    f"{self._restriction_expiry_label(expiry)}。"
                )
            if not quota_exempt_catch and daily_count >= daily_limit:
                raise DailyCatchLimitError(
                    f"本时段已经抓了 {daily_count}/{daily_limit} 次，"
                    f"下次刷新：北京时间 {quota_window.next_refresh_label}。"
                )
            using_extra_catch = (
                not quota_exempt_catch and catch_restriction is None and daily_count >= base_window_limit
            )
            if using_extra_catch and extra_consumed >= extra_granted:
                raise DailyCatchLimitError(f"本时段已经抓了 {daily_count}/{daily_limit} 次，额外抓猪机会已用完。")
            if using_extra_catch:
                effect_summaries += (
                    f"额外抓猪次数池：本次结算后剩余 {max(0, extra_granted - extra_consumed - 1)}/{extra_granted} 次。",
                )
            remaining = _cooldown_remaining(
                now=now_datetime,
                last_acquired_at=last_acquired_at,
                cooldown_seconds=self.catching.cooldown_seconds,
            )
            if remaining:
                raise CatchCooldownError(remaining)
            settled_daily_count = daily_count + (0 if quota_exempt_catch else 1)
            hidden_boost_chance = group_hidden_boost_chance(
                group_effect_application,
                active_group_effects,
            )
            if hidden_boost_chance > 0.0:
                previous_group_summaries = group_effect_application.summaries
                group_effect_application = apply_group_hidden_boost(
                    group_effect_application,
                    active_group_effects,
                    roll=self.random_source.random(),
                )
                weights = group_effect_application.weights
                if group_effect_application.hidden_boost_triggered:
                    prefix_length = len(effect_summaries) - len(previous_group_summaries)
                    effect_summaries = effect_summaries[:prefix_length] + group_effect_application.summaries
            six_star_progress_stacks = await self.economy_repository.six_star_progress_stacks(
                session,
                player_id=identity.player_id,
            )
            if six_star_progress_stacks and not exclusive_effect_active:
                progressed_weights = apply_six_star_progress(
                    weights,
                    stacks=six_star_progress_stacks,
                    bonus_per_stack=_DANIYA_CATCH_BONUS_PER_STACK,
                    action="catch",
                )
                if progressed_weights != normalize_weights(weights):
                    weights = progressed_weights
                    effect_summaries += (
                        f"达妮娅泡泡云冻永久加成：6 星概率 "
                        f"+{_DANIYA_CATCH_BONUS_PER_STACK * six_star_progress_stacks:g} "
                        f"个百分点（{six_star_progress_stacks} 层）。",
                    )
            elif six_star_progress_stacks:
                excluded_summaries += ("达妮娅泡泡云冻永久概率加成本次受六星菜独占规则影响，未参与结算。",)
            if window_resonance is not None and not exclusive_effect_active:
                resonance_bonus = 3.07 + int(window_resonance["catch_bonus_basis_points"]) / 100.0
                weights = add_six_star_probability_points(
                    weights,
                    bonus_points=resonance_bonus,
                    action="catch",
                )
                effect_summaries += (
                    f"粉蓝四叶草共鸣：本次6星概率额外+{resonance_bonus:g}个百分点"
                    f"（基础3.07，做菜累计{int(window_resonance['catch_bonus_basis_points']) / 100:g}）。",
                )
            elif window_resonance is not None:
                excluded_summaries += ("粉蓝四叶草共鸣本次受六星独占固定分布影响，累计状态保留。",)
            campaign_probability_active = bool(
                first_day_active(self.launch_campaign, now_datetime)
                and not exclusive_effect_active
                and not effect_application.collaboration_only
                and not effect_application.shuffle_permutation
            )
            if campaign_probability_active:
                weights = apply_first_day_high_star_weights(weights, self.launch_campaign, now_datetime)
                effect_summaries += (
                    f"2.0 开服首日：4/5/6 星权重 ×{self.launch_campaign.first_day_high_star_multiplier:g}。",
                )
            elif first_day_active(self.launch_campaign, now_datetime):
                excluded_summaries += (
                    "2.0 开服首日高星加成本次遇到六星独占、联动固定分布或概率换位规则，未参与结算。",
                )
            rarity_roll = self.random_source.random()
            rarity = choose_rarity(weights, rarity_roll)
            candidates = candidate_buckets[rarity]
            catalog_guide_used = False
            template_roll = self.random_source.random()
            template = self._select_template(
                candidates,
                template_roll,
                giant_template_multiplier=(
                    effect_application.giant_template_multiplier if rarity is Rarity.FIVE else 1.0
                ),
            )
            if (
                "catalog-guide" in achievement_catch_tickets
                and rarity is not Rarity.SIX
                and not effect_application.collaboration_only
                and str(template.get("template_id") or "") != KFC_PIG_TEMPLATE_ID
                and str(template.get("scope_type") or "common") == "common"
            ):
                eligible_candidates = [
                    row
                    for row in candidates
                    if str(row.get("template_id") or "") != KFC_PIG_TEMPLATE_ID
                    and str(row.get("scope_type") or "common") == "common"
                ]
                unseen = await self.achievement_repository.unseen_pig_template_ids(
                    session,
                    player_id=identity.player_id,
                    template_ids=tuple(str(row["template_id"]) for row in eligible_candidates),
                )
                guided = [row for row in eligible_candidates if str(row["template_id"]) in unseen]
                if guided:
                    template = self._select_template(
                        guided,
                        template_roll,
                        giant_template_multiplier=(
                            effect_application.giant_template_multiplier if rarity is Rarity.FIVE else 1.0
                        ),
                    )
                    catalog_guide_used = True
                    effect_summaries += ("图鉴引路券：在已确定品质内优先选择尚未发现的公共猪猪。",)
            attribute_rolls = tuple(self.random_source.random() for _ in range(5))
            attributes = generate_pig_attributes(
                rarity=rarity,
                length_min=float(template["length_min"]),
                length_max=float(template["length_max"]),
                weight_min=float(template["weight_min"]),
                weight_max=float(template["weight_max"]),
                fat_profile=str(template["fat_profile"]),
                random_values=attribute_rolls,
                item_id=armed_item.item_id if armed_item is not None else "",
                stature_bias=effect_application.stature_bias,
            )
            rescale_ticket_id = next(
                (
                    ticket_id
                    for ticket_id in ("giant-rescale", "mini-rescale")
                    if ticket_id in achievement_catch_tickets
                ),
                "",
            )
            rescale_rolls: tuple[float, ...] = ()
            if rescale_ticket_id:
                rescale_rolls = tuple(self.random_source.random() for _ in range(5))
                alternative = generate_pig_attributes(
                    rarity=rarity,
                    length_min=float(template["length_min"]),
                    length_max=float(template["length_max"]),
                    weight_min=float(template["weight_min"]),
                    weight_max=float(template["weight_max"]),
                    fat_profile=str(template["fat_profile"]),
                    random_values=rescale_rolls,
                    item_id=armed_item.item_id if armed_item is not None else "",
                    stature_bias=effect_application.stature_bias,
                )
                first_score = attributes.size_percentile + attributes.weight_percentile
                second_score = alternative.size_percentile + alternative.weight_percentile
                keep_alternative = (
                    second_score > first_score if rescale_ticket_id == "giant-rescale" else second_score < first_score
                )
                if keep_alternative:
                    attributes = alternative
                effect_summaries += (
                    (
                        "巨物复秤券：生成两套体型体重并保留巨物评分更高的一套。"
                        if rescale_ticket_id == "giant-rescale"
                        else "迷你复秤券：生成两套体型体重并保留更迷你的一套。"
                    ),
                )
            pig_instance_id = self._new_identifier()
            short_code = await self._new_unique_short_code(session)
            duplication_roll: float | None = None
            duplication_triggered = False
            duplicated_pig_instance_id = ""
            duplicated_short_code = ""
            if effect_application.duplicate_chance_percent > 0.0:
                duplication_roll = self.random_source.random()
                duplication_triggered = duplication_roll < (effect_application.duplicate_chance_percent / 100.0)
                if duplication_triggered:
                    duplicated_pig_instance_id = self._new_identifier()
                    duplicated_short_code = await self._new_unique_short_code(
                        session,
                        reserved=(short_code,),
                    )
                    effect_summaries += (
                        "美食复制成功：额外获得一只完全相同的 "
                        f"{template['display_name']}#{duplicated_short_code}；"
                        "复制品不重复发放抓猪奖励。",
                    )
                else:
                    effect_summaries += ("美食加成本次未触发复制。",)
            auto_gift_target_player_id = ""
            resonance_reward_foods: tuple[str, ...] = ()
            if window_resonance is not None:
                cook_bonus_after = await self.economy_repository.add_window_resonance_cook_bonus(
                    session,
                    player_id=identity.player_id,
                    basis_points=int(rarity) * 100,
                    now=now,
                )
                effect_summaries += (
                    f"粉蓝四叶草共鸣：本次{int(rarity)}星猪令六星做菜累计加成增至"
                    f"+{cook_bonus_after / 100:g}个百分点。",
                )
                if rarity is Rarity.SIX:
                    await self.economy_repository.reset_window_resonance_bonus(
                        session,
                        player_id=identity.player_id,
                        column="catch_bonus_basis_points",
                        now=now,
                    )
            random_snapshot = {
                "ruleset_version": RULESET_VERSION,
                "base_weights": list(self.catching.weights()),
                "normalized_weights": [round(value, 8) for value in weights],
                "shuffle_permutation": list(effect_application.shuffle_permutation),
                "shuffle_rolls": list(effect_application.shuffle_rolls),
                "feed_level": feed_level,
                "player_level": probability_level,
                "item_id": armed_item.item_id if armed_item is not None else "",
                "rarity_roll": rarity_roll,
                "template_roll": template_roll,
                "attribute_rolls": list(attribute_rolls),
                "achievement_rescale_rolls": list(rescale_rolls),
                "achievement_ticket_ids": sorted(achievement_catch_tickets),
                "food_effect_entry_ids": list(effect_application.consumed_entry_ids),
                "food_effect_summaries": list(effect_summaries),
                "group_food_effect_entry_ids": list(group_effect_application.consumed_entry_ids),
                "group_dedicated_effect_entry_id": (group_effect_application.dedicated_entry_id),
                "group_hidden_boost_roll": group_effect_application.hidden_boost_roll,
                "group_hidden_boost_triggered": (group_effect_application.hidden_boost_triggered),
                "group_effect_source_user_id": (group_effect_application.source_user_id),
                "group_effect_source_display_name": (group_effect_application.source_display_name),
                "auto_gift_target_player_id": auto_gift_target_player_id,
                "stature_bias": effect_application.stature_bias,
                "collaboration_only": effect_application.collaboration_only,
                "giant_template_multiplier": effect_application.giant_template_multiplier,
                "extra_catch_granted": extra_granted,
                "extra_catch_used": using_extra_catch,
                "permanent_window_bonus": permanent_bonus,
                "weekly_window_bonus": weekly_bonus,
                "current_window_bonus": current_window_bonus,
                "today_window_bonus": today_window_bonus,
                "exclusive_effect_active": exclusive_effect_active,
                "quota_exempt_catch": quota_exempt_catch,
                "catch_window_limit_restriction_id": (
                    str(catch_restriction["restriction_id"]) if catch_restriction is not None else ""
                ),
                "catch_window_limit_restriction_expires_at": (
                    str(catch_restriction.get("expires_at") or "") if catch_restriction is not None else ""
                ),
                "quota_window_boost_limit": (int(window_boost["limit_value"]) if window_boost is not None else 0),
                "launch_campaign_id": self.launch_campaign.campaign_id if self.launch_campaign.enabled else "",
                "launch_first_day_active": first_day_active(self.launch_campaign, now_datetime),
                "launch_high_star_multiplier_applied": campaign_probability_active,
                "group_technique_id": (
                    str(active_group_technique["technique_id"]) if active_group_technique is not None else ""
                ),
                "group_technique_source_player_id": (
                    str(active_group_technique["source_player_id"]) if active_group_technique is not None else ""
                ),
                "duplication_roll": duplication_roll,
                "duplication_triggered": duplication_triggered,
                "duplicated_pig_instance_id": duplicated_pig_instance_id,
                "duplicated_short_code": duplicated_short_code,
            }
            await self.repository.insert_pig_instance(
                session,
                values={
                    "pig_instance_id": pig_instance_id,
                    "short_code": short_code,
                    "scope_id": identity.scope.value,
                    "owner_player_id": identity.player_id,
                    "template_id": str(template["template_id"]),
                    "template_version": int(template["template_version"]),
                    "rarity": int(rarity),
                    "display_name_snapshot": str(template["display_name"]),
                    "size_value": attributes.size_value,
                    "size_percentile": attributes.size_percentile,
                    "weight_value": attributes.weight_value,
                    "weight_percentile": attributes.weight_percentile,
                    "fat_ratio": attributes.fat_ratio,
                    "official_value": attributes.official_value,
                    "ruleset_version": RULESET_VERSION,
                    "random_snapshot_json": self.repository.random_snapshot_json(random_snapshot),
                    "acquired_at": now,
                    "updated_at": now,
                },
            )
            if window_resonance is not None and rarity is Rarity.SIX:
                resonance_reward_foods = await self._grant_random_three_star_foods(
                    session,
                    identity=identity,
                    source_pig_instance_id=pig_instance_id,
                    source_weight=attributes.weight_value,
                    source_weight_percentile=attributes.weight_percentile,
                    source_fat_category=attributes.fat_category,
                    count=7,
                    now=now,
                    source_key=idempotency_key,
                )
                effect_summaries += (
                    "粉蓝四叶草共鸣命中六星：六星猪概率累计已清零，并获得7道随机三星菜："
                    + "、".join(resonance_reward_foods)
                    + "。",
                )
                random_snapshot["food_effect_summaries"] = list(effect_summaries)
                random_snapshot["window_resonance_reward_foods"] = list(resonance_reward_foods)
                await session.execute(
                    "UPDATE pig_instances SET random_snapshot_json=?, updated_at=? WHERE pig_instance_id=?",
                    (self.repository.random_snapshot_json(random_snapshot), now, pig_instance_id),
                )
            if duplication_triggered:
                duplicate_snapshot = {
                    **random_snapshot,
                    "source": CATCH_DUPLICATION_CHANCE,
                    "duplicated_from_pig_instance_id": pig_instance_id,
                    "duplicate_reward_granted": False,
                }
                await self.repository.insert_pig_instance(
                    session,
                    values={
                        "pig_instance_id": duplicated_pig_instance_id,
                        "short_code": duplicated_short_code,
                        "scope_id": identity.scope.value,
                        "owner_player_id": identity.player_id,
                        "template_id": str(template["template_id"]),
                        "template_version": int(template["template_version"]),
                        "rarity": int(rarity),
                        "display_name_snapshot": str(template["display_name"]),
                        "size_value": attributes.size_value,
                        "size_percentile": attributes.size_percentile,
                        "weight_value": attributes.weight_value,
                        "weight_percentile": attributes.weight_percentile,
                        "fat_ratio": attributes.fat_ratio,
                        "official_value": attributes.official_value,
                        "ruleset_version": RULESET_VERSION,
                        "random_snapshot_json": (self.repository.random_snapshot_json(duplicate_snapshot)),
                        "acquired_at": now,
                        "updated_at": now,
                    },
                )
            technique_resolution: TechniqueCatchResolution | None = None
            if active_group_technique is not None:
                technique_resolution = await self._apply_group_technique_to_catch(
                    session,
                    identity=identity,
                    active_effect=active_group_technique,
                    pig_instance_id=pig_instance_id,
                    short_code=short_code,
                    template=template,
                    rarity=rarity,
                    attributes=attributes,
                    now=now,
                )
                random_snapshot["group_technique_remaining_uses"] = technique_resolution.remaining_uses
                random_snapshot["food_effect_summaries"] = list((*effect_summaries, technique_resolution.summary))
                await session.execute(
                    """
                    UPDATE pig_instances
                    SET random_snapshot_json = ?, updated_at = ?
                    WHERE pig_instance_id = ?
                    """,
                    (
                        self.repository.random_snapshot_json(random_snapshot),
                        now,
                        pig_instance_id,
                    ),
                )
                effect_summaries += (technique_resolution.summary,)
            auto_gift_target_player_id = ""
            if active_group_technique is None and group_effect_application.source_user_id:
                auto_gift_chance = 0.0
                auto_gift_rarities: tuple[int, ...] = (6,)
                auto_gift_source_label = "阿萨姆红茶奶雾锅"
                for group_effect in active_group_effects:
                    if (
                        group_effect.group_effect_entry_id in group_effect_application.consumed_entry_ids
                        or group_effect.group_effect_entry_id == group_effect_application.dedicated_entry_id
                    ):
                        auto_gift_chance = float(group_effect.params.get("auto_gift_chance_percent") or 0.0)
                        if group_effect.params.get("auto_gift_rarities"):
                            auto_gift_rarities = tuple(
                                int(value) for value in group_effect.params["auto_gift_rarities"]
                            )
                        auto_gift_source_label = str(group_effect.params.get("source_label") or auto_gift_source_label)
                        break
                if (
                    int(rarity) in auto_gift_rarities
                    and auto_gift_chance > 0.0
                    and self.random_source.random() < auto_gift_chance / 100.0
                ):
                    activator_player_id = f"{identity.scope.value}:{group_effect_application.source_user_id}"
                    if activator_player_id != identity.player_id:
                        transferred = await self.repository.transfer_pig_owner(
                            session,
                            pig_instance_id=pig_instance_id,
                            owner_player_id=activator_player_id,
                            now=now,
                        )
                        if transferred:
                            await self.social_repository.insert_transfer_event(
                                session,
                                transfer_event_id=self._new_identifier(),
                                scope_id=identity.scope.value,
                                asset_kind=AssetKind.PIG,
                                asset_instance_id=pig_instance_id,
                                from_player_id=identity.player_id,
                                to_player_id=activator_player_id,
                                transfer_type="system-group-effect",
                                trade_id=None,
                                now=now,
                            )
                            await self.repository.upsert_pig_catalog(
                                session,
                                player_id=activator_player_id,
                                template_id=str(template["template_id"]),
                                size_value=attributes.size_value,
                                weight_value=attributes.weight_value,
                                now=now,
                            )
                            auto_gift_target_player_id = activator_player_id
                            random_snapshot["auto_gift_target_player_id"] = activator_player_id
                            await session.execute(
                                """
                                UPDATE pig_instances
                                SET random_snapshot_json = ?, updated_at = ?
                                WHERE pig_instance_id = ?
                                """,
                                (
                                    self.repository.random_snapshot_json(random_snapshot),
                                    now,
                                    pig_instance_id,
                                ),
                            )
                            effect_summaries += (
                                f"{auto_gift_source_label}：{identity.display_name} 抓到了 "
                                f"{int(rarity)} 星 {template['display_name']}（{short_code}），"
                                "被效果自动赠送给发动群友 "
                                f"{group_effect_application.source_display_name}！",
                            )
            coin_reward = CATCH_COIN_REWARDS[rarity]
            experience_reward = CATCH_EXPERIENCE_REWARDS[rarity]
            if armed_item is not None and armed_item.item_id == "coin-bounty-tag":
                coin_reward *= COIN_BOUNTY_REWARD_MULTIPLIER
                experience_reward = (experience_reward * 3 + 1) // 2
            coin_reward += effect_application.coin_bonus
            experience_reward = math.ceil(experience_reward * effect_application.experience_multiplier)
            coin_balance, total_experience = await self.repository.apply_catch_rewards(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                coin_reward=coin_reward,
                experience_reward=experience_reward,
                ledger_entry_id=self._new_identifier(),
                pig_instance_id=pig_instance_id,
                idempotency_key=idempotency_key,
                now=now,
            )
            veteran_reward = await settle_veteran_rewards(
                self.economy_repository,
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                player_level=level_progress(total_experience).level,
                current_balance=coin_balance,
                id_factory=self._new_identifier,
                now=now,
            )
            coin_balance = veteran_reward.balance_after
            catalog_new = await self.repository.upsert_pig_catalog(
                session,
                player_id=identity.player_id,
                template_id=str(template["template_id"]),
                size_value=attributes.size_value,
                weight_value=attributes.weight_value,
                now=now,
            )
            size_record = await self.repository.update_group_record(
                session,
                scope_id=identity.scope.value,
                template_id=str(template["template_id"]),
                record_type=RecordType.SIZE,
                pig_instance_id=pig_instance_id,
                record_value=attributes.size_value,
                player_id=identity.player_id,
                now=now,
            )
            weight_record = await self.repository.update_group_record(
                session,
                scope_id=identity.scope.value,
                template_id=str(template["template_id"]),
                record_type=RecordType.WEIGHT,
                pig_instance_id=pig_instance_id,
                record_value=attributes.weight_value,
                player_id=identity.player_id,
                now=now,
            )
            body = describe_body_scale(
                stature_profile=str(template.get("stature_profile") or StatureProfile.STANDARD.value),
                size_value=attributes.size_value,
                size_percentile=attributes.size_percentile,
                weight_value=attributes.weight_value,
                weight_percentile=attributes.weight_percentile,
                giant_size_threshold_cm=self.ranking.giant_size_threshold_cm,
                giant_weight_threshold_kg=self.ranking.giant_weight_threshold_kg,
            )
            global_size_record = await self.social_repository.update_global_record(
                session,
                scope_id=identity.scope.value,
                template_id=str(template["template_id"]),
                record_type=RecordType.SIZE,
                pig_instance_id=pig_instance_id,
                record_value=attributes.size_value,
                player_id=identity.player_id,
                now=now,
            )
            global_weight_record = await self.social_repository.update_global_record(
                session,
                scope_id=identity.scope.value,
                template_id=str(template["template_id"]),
                record_type=RecordType.WEIGHT,
                pig_instance_id=pig_instance_id,
                record_value=attributes.weight_value,
                player_id=identity.player_id,
                now=now,
            )
            giant_sighting = await self.social_repository.insert_giant_sighting(
                session,
                pig_instance_id=pig_instance_id,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                template_id=str(template["template_id"]),
                size_value=attributes.size_value,
                weight_value=attributes.weight_value,
                giant_score=body.giant_score,
                size_qualified=body.size_qualified,
                weight_qualified=body.weight_qualified,
                now=now,
            )
            if armed_item is not None:
                consumed = await self.repository.consume_armed_item(
                    session,
                    player_id=identity.player_id,
                    action_type="catching",
                    item_id=armed_item.item_id,
                    now=now,
                )
                if not consumed:
                    raise ItemInventoryError(f"已装备的“{armed_item.display_name}”库存不足，本次抓猪未结算。")
            if effect_application.consumed_entry_ids:
                await self.economy_repository.consume_food_effects(
                    session,
                    player_id=identity.player_id,
                    effect_entry_ids=effect_application.consumed_entry_ids,
                    now=now,
                )
            group_effect_uses = {
                *group_effect_application.consumed_entry_ids,
            }
            if group_effect_application.dedicated_entry_id:
                group_effect_uses.add(group_effect_application.dedicated_entry_id)
            for group_effect_entry_id in sorted(group_effect_uses):
                await self.economy_repository.consume_group_food_effect_use(
                    session,
                    group_effect_entry_id=group_effect_entry_id,
                    player_id=identity.player_id,
                    now=now,
                )
            if using_extra_catch:
                await self.economy_repository.consume_extra_catch(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            for ticket_id, used in (
                ("achievement-catch", achievement_catch_ticket_used),
                ("catalog-guide", catalog_guide_used),
                (rescale_ticket_id, bool(rescale_ticket_id)),
            ):
                if not ticket_id or not used:
                    continue
                if not await self.achievement_repository.consume_active_ticket(
                    session,
                    player_id=identity.player_id,
                    ticket_id=ticket_id,
                    now=now,
                ):
                    raise RuntimeError("成就券状态已变化，本次抓猪未结算。")
            if "achievement-firework" in achievement_visual_tickets:
                if not await self.achievement_repository.consume_active_ticket(
                    session,
                    player_id=identity.player_id,
                    ticket_id="achievement-firework",
                    now=now,
                ):
                    raise RuntimeError("成就礼花券状态已变化，本次抓猪未结算。")
                effect_summaries += ("成就礼花券：本次成功卡启用 PiG Dream! 庆祝礼花。",)
            pig_row = await self.repository.get_pig_by_instance_id(
                session,
                pig_instance_id=pig_instance_id,
            )
            if pig_row is None:
                raise RuntimeError("抓猪实例提交前无法读取。")
            pig = self._pig_view(pig_row)
            payload: dict[str, Any] = {
                "daily_count": settled_daily_count,
                "daily_limit": daily_limit,
                "quota_window": quota_window.label,
                "next_quota_refresh": quota_window.next_refresh_label,
                "coin_reward": coin_reward,
                "experience_reward": experience_reward,
                "coin_balance": coin_balance,
                "total_experience": total_experience,
                "veteran_coin_reward": veteran_reward.coin_reward,
                "veteran_reward_levels": list(veteran_reward.rewarded_levels),
                "catalog_new": catalog_new,
                "size_record": size_record,
                "weight_record": weight_record,
                "global_size_record": global_size_record,
                "global_weight_record": global_weight_record,
                "giant_sighting": giant_sighting,
                "feed_level": feed_level,
                "item_id": armed_item.item_id if armed_item is not None else "",
                "item_name": armed_item.display_name if armed_item is not None else "",
                "item_remaining_uses": (max(0, armed_uses - 1) if armed_item is not None else 0),
                "weights": [round(value, 8) for value in weights],
                "display_tags": list(pig.display_tags),
                "effect_summaries": list(effect_summaries),
                "excluded_summaries": list(excluded_summaries),
                "exclusive_effect_active": exclusive_effect_active,
                "quota_exempt_catch": quota_exempt_catch,
                "group_hidden_boost_triggered": (group_effect_application.hidden_boost_triggered),
                "group_effect_source_user_id": (group_effect_application.source_user_id),
                "group_effect_source_display_name": (group_effect_application.source_display_name),
                "duplication_triggered": duplication_triggered,
                "duplicated_pig_instance_id": duplicated_pig_instance_id,
                "duplicated_short_code": duplicated_short_code,
                "technique_resolution": (
                    self._technique_resolution_payload(technique_resolution)
                    if technique_resolution is not None
                    else None
                ),
                "window_resonance_reward_foods": list(resonance_reward_foods),
            }
            provisional_receipt = CommandReceipt(
                receipt_id="",
                idempotency_key=idempotency_key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=_CATCH_COMMAND,
                request_fingerprint=request_fingerprint(request_payload),
                result_type="pig",
                result_object_id=pig_instance_id,
                result_json=json.dumps(payload, ensure_ascii=False),
                text_summary="",
                send_status=self._pending_status(),
                created_at=now,
                updated_at=now,
            )
            provisional = CatchResult(
                pig=pig,
                receipt=provisional_receipt,
                receipt_created=True,
                daily_count=settled_daily_count,
                daily_limit=daily_limit,
                coin_reward=coin_reward,
                experience_reward=experience_reward,
                coin_balance=coin_balance,
                total_experience=total_experience,
                catalog_new=catalog_new,
                size_record=size_record,
                weight_record=weight_record,
                feed_level=feed_level,
                item_id=armed_item.item_id if armed_item is not None else "",
                item_name=armed_item.display_name if armed_item is not None else "",
                weights=weights,
                item_remaining_uses=(max(0, armed_uses - 1) if armed_item is not None else 0),
                effect_summaries=effect_summaries,
                excluded_summaries=excluded_summaries,
                exclusive_effect_active=exclusive_effect_active,
                quota_exempt_catch=quota_exempt_catch,
                global_size_record=global_size_record,
                global_weight_record=global_weight_record,
                giant_sighting=giant_sighting,
                technique_resolution=technique_resolution,
                veteran_coin_reward=veteran_reward.coin_reward,
                veteran_reward_levels=veteran_reward.rewarded_levels,
            )
            summary = format_catch_summary(provisional)
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=_CATCH_COMMAND,
                request_fingerprint=request_fingerprint(request_payload),
                result_type="pig",
                result_object_id=pig_instance_id,
                result_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=summary,
                now=now,
                catch_quota_cost=0 if quota_exempt_catch else 1,
            )
            return CatchResult(
                pig=pig,
                receipt=reservation.receipt,
                receipt_created=reservation.created,
                daily_count=settled_daily_count,
                daily_limit=daily_limit,
                coin_reward=coin_reward,
                experience_reward=experience_reward,
                coin_balance=coin_balance,
                total_experience=total_experience,
                catalog_new=catalog_new,
                size_record=size_record,
                weight_record=weight_record,
                feed_level=feed_level,
                item_id=armed_item.item_id if armed_item is not None else "",
                item_name=armed_item.display_name if armed_item is not None else "",
                weights=weights,
                item_remaining_uses=(max(0, armed_uses - 1) if armed_item is not None else 0),
                effect_summaries=effect_summaries,
                excluded_summaries=excluded_summaries,
                exclusive_effect_active=exclusive_effect_active,
                quota_exempt_catch=quota_exempt_catch,
                global_size_record=global_size_record,
                global_weight_record=global_weight_record,
                giant_sighting=giant_sighting,
                technique_resolution=technique_resolution,
                veteran_coin_reward=veteran_reward.coin_reward,
                veteran_reward_levels=veteran_reward.rewarded_levels,
            )

    async def activate_group_technique(
        self,
        identity: CommandIdentity,
        *,
        technique_id: str,
    ) -> TechniqueActivationResult:
        """Consume one food-granted permit and start an exact-scope group effect."""

        uses_by_technique = {
            TECHNIQUE_MALEVOLENT_KITCHEN: 10,
            TECHNIQUE_LAPSE_BLUE: 5,
            TECHNIQUE_REVERSAL_RED: 5,
        }
        if technique_id not in uses_by_technique:
            raise TechniqueError("未知的群体术式。")
        command_name = f"pig-catcher.technique.{technique_id}"
        request_payload = {"command_version": 1, "technique_id": technique_id}
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
                payload = receipt_payload(existing)
                return TechniqueActivationResult(
                    receipt=existing,
                    receipt_created=False,
                    technique_id=technique_id,
                    summary=existing.text_summary,
                    total_uses=int(payload.get("total_uses") or 0),
                    remaining_permits=int(payload.get("remaining_permits") or 0),
                    purple_unlocked=int(payload.get("purple_unlocked") or 0),
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            active = await self.technique_repository.active_group_effect(
                session,
                scope_id=identity.scope.value,
            )
            if active is not None:
                active_name = TECHNIQUE_DISPLAY_NAMES.get(
                    str(active["technique_id"]),
                    "未知术式",
                )
                raise TechniqueError(
                    f"本群的{active_name}仍剩 {int(active['remaining_uses'])} 次结算；结束前不能发动另一种群体术式。"
                )
            consumed = await self.technique_repository.consume_permit(
                session,
                player_id=identity.player_id,
                technique_id=technique_id,
                now=now,
            )
            if not consumed:
                raise TechniqueError(
                    f"你没有可用的{TECHNIQUE_DISPLAY_NAMES[technique_id]}发动资格；请先食用对应专属菜。"
                )
            total_uses = uses_by_technique[technique_id]
            effect_entry_id = self._new_identifier()
            await self.technique_repository.insert_group_effect(
                session,
                effect_entry_id=effect_entry_id,
                scope_id=identity.scope.value,
                technique_id=technique_id,
                source_player_id=identity.player_id,
                uses=total_uses,
                now=now,
            )
            if technique_id == TECHNIQUE_MALEVOLENT_KITCHEN:
                await self.technique_repository.grant_permit(
                    session,
                    player_id=identity.player_id,
                    technique_id=TECHNIQUE_DOMAIN_GOJO_BYPASS,
                    uses=1,
                    now=now,
                )
            purple_unlocked = await self.technique_repository.record_color_activation(
                session,
                player_id=identity.player_id,
                technique_id=technique_id,
                now=now,
            )
            remaining_permits = await self.technique_repository.available_permits(
                session,
                player_id=identity.player_id,
                technique_id=technique_id,
            )
            actor = str(identity.display_name or "").strip() or "未命名群友"
            if technique_id == TECHNIQUE_MALEVOLENT_KITCHEN:
                detail = (
                    "接下来本群 10 次抓猪会把成品猪立即做成高品质菜；"
                    "若抓到六星猪，六星菜概率固定为 25%。每次出餐各复制一份给发动者与抓猪者。"
                )
            elif technique_id == TECHNIQUE_LAPSE_BLUE:
                detail = "接下来本群 5 次抓到的猪都会被苍吸引给发动者。"
            else:
                detail = "接下来本群 5 次抓到的猪都会由赫随机分配给一名已登记群友。"
            purple_text = " 苍与赫已经完成一组组合，额外解锁 1 次 /虚式 茈。" if purple_unlocked else ""
            summary = (
                f"【{TECHNIQUE_DISPLAY_NAMES[technique_id]}发动】\n"
                f"发动者：{actor}\n{detail}\n"
                f"同类资格剩余：{remaining_permits} 次。{purple_text}"
            )
            payload = {
                "technique_id": technique_id,
                "effect_entry_id": effect_entry_id,
                "total_uses": total_uses,
                "remaining_permits": remaining_permits,
                "purple_unlocked": purple_unlocked,
            }
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command_name,
                request_fingerprint=request_fingerprint(request_payload),
                result_type="technique-activation",
                result_object_id=effect_entry_id,
                result_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=summary,
                now=now,
            )
            return TechniqueActivationResult(
                receipt=reservation.receipt,
                receipt_created=reservation.created,
                technique_id=technique_id,
                summary=summary,
                total_uses=total_uses,
                remaining_permits=remaining_permits,
                purple_unlocked=purple_unlocked,
            )

    async def activate_hollow_purple(
        self,
        identity: CommandIdentity,
    ) -> TechniqueActivationResult:
        """Consume one paired Blue/Red unlock and grant five random six-star pigs."""

        command_name = "pig-catcher.technique.hollow-purple"
        request_payload = {"command_version": 1}
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
                payload = receipt_payload(existing)
                raw_ids = payload.get("pig_instance_ids")
                pig_ids = raw_ids if isinstance(raw_ids, list) else []
                pig_views: list[PigView] = []
                for pig_id in pig_ids:
                    row = await self.repository.get_pig_by_instance_id(
                        session,
                        pig_instance_id=str(pig_id),
                    )
                    if row is not None:
                        pig_views.append(self._pig_view(row))
                pigs = tuple(pig_views)
                return TechniqueActivationResult(
                    receipt=existing,
                    receipt_created=False,
                    technique_id=TECHNIQUE_HOLLOW_PURPLE,
                    summary=existing.text_summary,
                    remaining_permits=int(payload.get("remaining_permits") or 0),
                    granted_pigs=pigs,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            templates = [
                template
                for template in await self.repository.list_drawable_pig_templates(
                    session,
                    scope_id=identity.scope.value,
                )
                if int(template["rarity"]) == 6
            ]
            if not templates:
                raise TechniqueError("当前群没有已授权的六星猪模板，虚式资格未消耗。")
            consumed = await self.technique_repository.consume_permit(
                session,
                player_id=identity.player_id,
                technique_id=TECHNIQUE_HOLLOW_PURPLE,
                now=now,
            )
            if not consumed:
                raise TechniqueError("你还没有完成一组苍与赫，暂时不能使用 /虚式 茈。")
            pig_ids: list[str] = []
            for index in range(5):
                template_roll = self.random_source.random()
                template = templates[min(int(template_roll * len(templates)), len(templates) - 1)]
                attribute_rolls = tuple(self.random_source.random() for _ in range(5))
                attributes = generate_pig_attributes(
                    rarity=Rarity.SIX,
                    length_min=float(template["length_min"]),
                    length_max=float(template["length_max"]),
                    weight_min=float(template["weight_min"]),
                    weight_max=float(template["weight_max"]),
                    fat_profile=str(template["fat_profile"]),
                    random_values=attribute_rolls,
                )
                pig_instance_id = self._new_identifier()
                short_code = await self._new_unique_short_code(session)
                await self.repository.insert_pig_instance(
                    session,
                    values={
                        "pig_instance_id": pig_instance_id,
                        "short_code": short_code,
                        "scope_id": identity.scope.value,
                        "owner_player_id": identity.player_id,
                        "template_id": str(template["template_id"]),
                        "template_version": int(template["template_version"]),
                        "rarity": 6,
                        "display_name_snapshot": str(template["display_name"]),
                        "size_value": attributes.size_value,
                        "size_percentile": attributes.size_percentile,
                        "weight_value": attributes.weight_value,
                        "weight_percentile": attributes.weight_percentile,
                        "fat_ratio": attributes.fat_ratio,
                        "official_value": attributes.official_value,
                        "ruleset_version": RULESET_VERSION,
                        "random_snapshot_json": self.repository.random_snapshot_json(
                            {
                                "ruleset_version": RULESET_VERSION,
                                "source": TECHNIQUE_HOLLOW_PURPLE,
                                "grant_index": index,
                                "template_roll": template_roll,
                                "attribute_rolls": list(attribute_rolls),
                            }
                        ),
                        "acquired_at": now,
                        "updated_at": now,
                    },
                )
                await self.repository.upsert_pig_catalog(
                    session,
                    player_id=identity.player_id,
                    template_id=str(template["template_id"]),
                    size_value=attributes.size_value,
                    weight_value=attributes.weight_value,
                    now=now,
                )
                pig_ids.append(pig_instance_id)
            remaining_permits = await self.technique_repository.available_permits(
                session,
                player_id=identity.player_id,
                technique_id=TECHNIQUE_HOLLOW_PURPLE,
            )
            pig_views = []
            for pig_id in pig_ids:
                row = await self.repository.get_pig_by_instance_id(
                    session,
                    pig_instance_id=pig_id,
                )
                if row is not None:
                    pig_views.append(self._pig_view(row))
            pigs = tuple(pig_views)
            selectors = "、".join(pig.selector for pig in pigs)
            summary = (
                "【虚式·茈发动】\n"
                f"{identity.display_name or '未命名群友'} 随机获得 5 只六星猪：\n"
                f"{selectors}\n剩余虚式资格：{remaining_permits} 次。"
            )
            payload = {
                "pig_instance_ids": pig_ids,
                "remaining_permits": remaining_permits,
            }
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command_name,
                request_fingerprint=request_fingerprint(request_payload),
                result_type="hollow-purple",
                result_object_id=pig_ids[0],
                result_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=summary,
                now=now,
            )
            return TechniqueActivationResult(
                receipt=reservation.receipt,
                receipt_created=reservation.created,
                technique_id=TECHNIQUE_HOLLOW_PURPLE,
                summary=summary,
                remaining_permits=remaining_permits,
                granted_pigs=pigs,
            )

    async def _apply_group_technique_to_catch(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        active_effect: Mapping[str, object],
        pig_instance_id: str,
        short_code: str,
        template: Mapping[str, object],
        rarity: Rarity,
        attributes: PigAttributes,
        now: str,
    ) -> TechniqueCatchResolution:
        technique_id = str(active_effect["technique_id"])
        source_player_id = str(active_effect["source_player_id"])
        source_name = str(active_effect.get("source_display_name") or "").strip()
        source_name = source_name or "未命名群友"
        if technique_id == TECHNIQUE_MALEVOLENT_KITCHEN:
            generated_foods = await self._domain_auto_cook(
                session,
                identity=identity,
                source_player_id=source_player_id,
                pig_instance_id=pig_instance_id,
                template=template,
                rarity=rarity,
                attributes=attributes,
                now=now,
            )
            food_summary = "、".join(food.selector for food in generated_foods)
            if str(template["template_id"]) == GOJO_PIG_TEMPLATE_ID:
                self_caught_in_own_domain = identity.player_id == source_player_id
                action = (
                    f"{TECHNIQUE_DISPLAY_NAMES[technique_id]}由 {source_name} 接管："
                    f"{template['display_name']}#{short_code} 已立即做成两道专属菜："
                    f"{food_summary}，"
                    + ("由发动者本人全部获得" if self_caught_in_own_domain else "随机分给两名不同群友，一人一道")
                )
            else:
                action = (
                    f"{TECHNIQUE_DISPLAY_NAMES[technique_id]}由 {source_name} 接管："
                    f"{template['display_name']}#{short_code} 已立即做成 {food_summary}，"
                    "发动者与抓猪者各获得一份"
                )
            target_player_id = ""
            target_name = ""
        else:
            generated_foods = ()
            players = sorted(
                await self.economy_repository.players_in_scope(
                    session,
                    scope_id=identity.scope.value,
                ),
                key=lambda row: str(row["player_id"]),
            )
            if technique_id == TECHNIQUE_LAPSE_BLUE:
                target_player_id = source_player_id
                target_name = source_name
            elif technique_id == TECHNIQUE_REVERSAL_RED:
                target_roll = self.random_source.random()
                target = players[min(int(target_roll * len(players)), len(players) - 1)]
                target_player_id = str(target["player_id"])
                target_name = str(target.get("display_name") or "").strip()
                target_name = target_name or "未命名群友"
            else:
                raise RuntimeError("持久化了未知群体术式。")
            if target_player_id != identity.player_id:
                transferred = await self.repository.transfer_pig_owner(
                    session,
                    pig_instance_id=pig_instance_id,
                    owner_player_id=target_player_id,
                    now=now,
                )
                if not transferred:
                    raise RuntimeError("术式转移猪猪归属失败。")
                await self.social_repository.insert_transfer_event(
                    session,
                    transfer_event_id=self._new_identifier(),
                    scope_id=identity.scope.value,
                    asset_kind=AssetKind.PIG,
                    asset_instance_id=pig_instance_id,
                    from_player_id=identity.player_id,
                    to_player_id=target_player_id,
                    transfer_type="system-group-effect",
                    trade_id=None,
                    now=now,
                )
                await self.repository.upsert_pig_catalog(
                    session,
                    player_id=target_player_id,
                    template_id=str(template["template_id"]),
                    size_value=attributes.size_value,
                    weight_value=attributes.weight_value,
                    now=now,
                )
            action = (
                f"{TECHNIQUE_DISPLAY_NAMES[technique_id]}由 {source_name} 接管："
                f"{template['display_name']}#{short_code} 归属 {target_name}"
            )
        remaining = await self.technique_repository.consume_group_effect_use(
            session,
            effect_entry_id=str(active_effect["effect_entry_id"]),
            now=now,
        )
        summary = f"{action}（本群术式剩余 {remaining} 次）"
        return TechniqueCatchResolution(
            technique_id=technique_id,
            technique_name=TECHNIQUE_DISPLAY_NAMES[technique_id],
            source_player_id=source_player_id,
            source_display_name=source_name,
            target_player_id=target_player_id,
            target_display_name=target_name,
            remaining_uses=remaining,
            summary=summary,
            generated_foods=generated_foods,
        )

    async def _domain_auto_cook(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        source_player_id: str,
        pig_instance_id: str,
        template: Mapping[str, object],
        rarity: Rarity,
        attributes: PigAttributes,
        now: str,
    ) -> tuple[TechniqueFoodView, ...]:
        """Consume a just-caught pig and create one serving for each beneficiary."""

        source_template_id = str(template["template_id"])
        is_gojo_pig = source_template_id == GOJO_PIG_TEMPLATE_ID
        if is_gojo_pig:
            weights = normalize_weights((0, 0, 0, 0, 100, 0))
        else:
            weights = domain_cooking_weights(int(rarity))
        rarity_roll = self.random_source.random()
        output_rarity = choose_rarity(weights, rarity_roll)
        food_templates = await self.economy_repository.list_drawable_food_templates(
            session,
            scope_id=identity.scope.value,
            rarity=int(output_rarity),
        )
        if not food_templates:
            raise TechniqueError(f"领域缺少 {int(output_rarity)} 星美食模板，本次抓猪未结算。")
        special_roll: float | None = None
        special_template_id = ""
        template_roll: float | None = None
        recipient_rolls: tuple[float, ...] = ()
        gojo_self_caught_in_own_domain = False
        if is_gojo_pig:
            templates_by_id = {str(candidate["template_id"]): candidate for candidate in food_templates}
            missing_template_ids = [
                template_id for template_id in GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS if template_id not in templates_by_id
            ]
            if missing_template_ids:
                raise TechniqueError("领域命中五条猪，但两道专属菜尚未完整启用。")
            serving_templates = (
                templates_by_id[GOJO_BLUE_FOOD_TEMPLATE_ID],
                templates_by_id[GOJO_RED_FOOD_TEMPLATE_ID],
            )
            gojo_self_caught_in_own_domain = identity.player_id == source_player_id
            if gojo_self_caught_in_own_domain:
                owners = (source_player_id, source_player_id)
            else:
                players = sorted(
                    await self.economy_repository.players_in_scope(
                        session,
                        scope_id=identity.scope.value,
                    ),
                    key=lambda row: str(row["player_id"]),
                )
                if len(players) < 2:
                    raise TechniqueError("领域命中五条猪，但当前群已登记玩家不足两人，无法一人分配一道专属菜。")
                first_roll = self.random_source.random()
                first_index = min(int(first_roll * len(players)), len(players) - 1)
                first_owner = players.pop(first_index)
                second_roll = self.random_source.random()
                second_index = min(int(second_roll * len(players)), len(players) - 1)
                second_owner = players[second_index]
                owners = (
                    str(first_owner["player_id"]),
                    str(second_owner["player_id"]),
                )
                recipient_rolls = (first_roll, second_roll)
        elif int(output_rarity) == 6:
            special_template_id = str(template.get("paired_food_template_id") or "")
            if not special_template_id:
                raise TechniqueError("领域抽到六星菜，但原料猪没有对应定制菜。")
        elif int(output_rarity) == 5:
            if source_template_id == SUKUNA_PIG_TEMPLATE_ID:
                special_roll = self.random_source.random()
                if special_roll < 0.20:
                    special_template_id = SUKUNA_FOOD_TEMPLATE_ID
            elif source_template_id == KFC_PIG_TEMPLATE_ID:
                special_roll = self.random_source.random()
                if special_roll < 0.50:
                    special_template_id = KFC_FOOD_TEMPLATE_ID
        if not is_gojo_pig:
            if special_template_id:
                candidates = [
                    candidate for candidate in food_templates if str(candidate["template_id"]) == special_template_id
                ]
                if not candidates:
                    raise TechniqueError("领域命中了专属菜，但该模板尚未启用。")
            else:
                candidates = [
                    candidate
                    for candidate in food_templates
                    if str(candidate["template_id"]) not in SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS
                ]
                if not candidates:
                    raise TechniqueError("领域缺少可用的普通五星菜模板。")
            template_roll = self.random_source.random()
            food_template = candidates[min(int(template_roll * len(candidates)), len(candidates) - 1)]
            serving_templates = (food_template, food_template)
            owners = (source_player_id, identity.player_id)
        food_ids = [self._new_identifier(), self._new_identifier()]
        first_short_code = await self._new_unique_short_code(session)
        short_codes = [
            first_short_code,
            await self._new_unique_short_code(
                session,
                reserved=(first_short_code,),
            ),
        ]
        portion_rolls = [self.random_source.random(), self.random_source.random()]
        food_attributes = [
            generate_food_attributes(
                rarity=output_rarity,
                template_id=str(serving_template["template_id"]),
                source_weight=attributes.weight_value,
                source_weight_percentile=attributes.weight_percentile,
                portion_roll=portion_roll,
            )
            for serving_template, portion_roll in zip(
                serving_templates,
                portion_rolls,
                strict=True,
            )
        ]
        consumed = await self.economy_repository.consume_pig_for_cooking(
            session,
            pig_instance_id=pig_instance_id,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            now=now,
        )
        if not consumed:
            raise RuntimeError("领域自动做菜时原料猪状态发生变化。")
        for index, (
            owner,
            food_template,
            food_id,
            short_code,
            food_attribute,
        ) in enumerate(
            zip(
                owners,
                serving_templates,
                food_ids,
                short_codes,
                food_attributes,
                strict=True,
            )
        ):
            await self.economy_repository.insert_food_instance(
                session,
                values={
                    "food_instance_id": food_id,
                    "short_code": short_code,
                    "scope_id": identity.scope.value,
                    "owner_player_id": owner,
                    "template_id": str(food_template["template_id"]),
                    "template_version": int(food_template["template_version"]),
                    "source_pig_instance_id": pig_instance_id,
                    "rarity": int(output_rarity),
                    "display_name_snapshot": str(food_template["display_name"]),
                    "portion_weight": food_attribute.portion_weight,
                    "fat_category": attributes.fat_category,
                    "official_value": food_attribute.official_value,
                    "effect_id": str(food_template.get("effect_id") or ""),
                    "effect_params_json": str(food_template.get("effect_params_json") or "{}"),
                    "ruleset_version": RULESET_VERSION,
                    "random_snapshot_json": self.repository.random_snapshot_json(
                        {
                            "ruleset_version": RULESET_VERSION,
                            "source": TECHNIQUE_MALEVOLENT_KITCHEN,
                            "source_pig_instance_id": pig_instance_id,
                            "weights": list(weights),
                            "rarity_roll": rarity_roll,
                            "template_roll": template_roll,
                            "special_roll": special_roll,
                            "special_template_id": (
                                str(food_template["template_id"]) if is_gojo_pig else special_template_id
                            ),
                            "domain_gojo_dual_recipe": is_gojo_pig,
                            "domain_gojo_self_caught": (gojo_self_caught_in_own_domain),
                            "recipient_rolls": list(recipient_rolls),
                            "recipient_player_ids": list(owners),
                            "serving_index": index,
                            "portion_roll": portion_rolls[index],
                            "recipe_factor": food_attribute.recipe_factor,
                        }
                    ),
                    "acquired_at": now,
                    "updated_at": now,
                },
            )
            await self.economy_repository.upsert_food_catalog(
                session,
                player_id=owner,
                template_id=str(food_template["template_id"]),
                portion_weight=food_attribute.portion_weight,
                now=now,
            )
        await self.social_repository.increment_statistic(
            session,
            player_id=source_player_id,
            field="total_cooks",
            now=now,
        )
        foods: list[TechniqueFoodView] = []
        for food_id in food_ids:
            row = await self.economy_repository.get_food_by_instance_id(
                session,
                food_instance_id=food_id,
            )
            if row is None:
                raise RuntimeError("领域出餐后无法读取美食实例。")
            foods.append(self._technique_food_view(row))
        return tuple(foods)

    async def toggle_baogian(
        self,
        identity: CommandIdentity,
        *,
        short_code: str | None = None,
    ) -> tuple[int, str, str]:
        """切换玩家持有的保千猪立绘与表情包。

        覆盖四个群（NapCat 双群 + QQ 官方双群）的保千猪实例；背包里有多只
        保千猪时必须通过 ``short_code`` 指定目标实例，否则返回编号提示。
        返回 (切换数量, 新变体, 提示文案)。
        """

        return await self.toggle_pig_art(
            identity,
            display_name="保千猪",
            alternate_label="表情包",
            short_code=short_code,
        )

    async def toggle_pig_art(
        self,
        identity: CommandIdentity,
        *,
        display_name: str,
        alternate_label: str,
        short_code: str | None = None,
    ) -> tuple[int, str, str]:
        """Switch one owned alternate-art pig by name and optional short code."""

        now_datetime = _safe_datetime(self.clock.now())
        now = iso_timestamp(now_datetime)
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            instances = await self.repository.list_switchable_pig_instances(
                session,
                player_id=identity.player_id,
                display_name=display_name,
            )
        if not instances:
            return 0, "", f"你还没有{display_name}，无法切换立绘。"
        target: dict[str, object] | None = None
        if short_code:
            normalized = str(short_code).strip().upper()
            matches = [instance for instance in instances if str(instance["short_code"]).upper() == normalized]
            if not matches:
                codes = "、".join(str(instance["short_code"]) for instance in instances)
                return 0, "", (f"背包中没有编号 {short_code} 的{display_name}；你当前持有的{display_name}编号：{codes}")
            target = matches[0]
        elif len(instances) > 1:
            codes = "、".join(str(instance["short_code"]) for instance in instances)
            command_name = "猪保千" if display_name == "保千猪" else display_name
            return 0, "", (f"你有 {len(instances)} 只{display_name}，请指定编号切换：/切换 {command_name} {codes}")
        else:
            target = instances[0]
        async with self.database.transaction() as session:
            count, new_variant = await self.repository.toggle_pig_instances(
                session,
                player_id=identity.player_id,
                instance_ids=[str(target["pig_instance_id"])],
                now=now,
            )
        label = alternate_label if new_variant == "sticker" else "默认立绘"
        return count, new_variant, (f"已将{display_name} {target['short_code']} 切换为{label}。")

    async def profile(self, identity: CommandIdentity) -> PlayerProfile:
        """Read the current-group player profile."""

        now_datetime = _safe_datetime(self.clock.now())
        now = iso_timestamp(now_datetime)
        quota_window = catch_quota_window(
            now_datetime,
            refresh_hours=self.catching.quota_refresh_hours,
            timezone_name=self.catching.daily_reset_timezone,
        )
        window_start = iso_timestamp(quota_window.start)
        window_end = iso_timestamp(quota_window.end)
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            row = await self.repository.profile_row(
                session,
                player_id=identity.player_id,
            )
            if row is None:
                raise RuntimeError("玩家档案初始化后无法读取。")
            daily_count, _, last_acquired_at = await self.repository.catch_usage(
                session,
                player_id=identity.player_id,
                window_start=window_start,
                window_end=window_end,
            )
            active_effects = tuple(
                active_effect_from_row(row)
                for row in await self.economy_repository.list_active_food_effects(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            )
            current_window_bonus, today_window_bonus = active_quota_effect_bonuses(active_effects)
            extra_granted, extra_consumed = await self.economy_repository.extra_catch_grants(
                session,
                player_id=identity.player_id,
                now=now,
            )
            permanent_bonus, weekly_bonus = await self.economy_repository.catch_quota_bonuses(
                session,
                player_id=identity.player_id,
                now=now,
            )
            catch_restriction = await self.restriction_repository.active_restriction(
                session,
                player_id=identity.player_id,
                restriction_type=CATCH_WINDOW_LIMIT,
                now=now,
            )
            window_boost = await self.quota_repository.active_window_boost(
                session,
                scope_id=identity.scope.value,
                window_start=window_start,
            )
            if window_boost is not None:
                quota_layers = stack_catch_quota_layers(
                    configured_base=int(window_boost["limit_value"]),
                    extra_granted=extra_granted,
                    extra_consumed=extra_consumed,
                )
                catch_restriction = None
            else:
                configured_base_limit = campaign_window_limit(
                    self.launch_campaign,
                    now_datetime,
                    normal_limit=self.catching.daily_limit,
                )
                quota_layers = stack_catch_quota_layers(
                    configured_base=configured_base_limit,
                    permanent_bonus=permanent_bonus,
                    weekly_bonus=weekly_bonus,
                    current_window_bonus=current_window_bonus,
                    today_window_bonus=today_window_bonus,
                    extra_granted=extra_granted,
                    extra_consumed=extra_consumed,
                )
            feed_level = await self.repository.get_feed_level(
                session,
                player_id=identity.player_id,
            )
            armed_row = await self.repository.get_armed_item(
                session,
                player_id=identity.player_id,
                action_type="catching",
            )
            armed_item, armed_quantity = self._armed_item(armed_row, "catching")
            cooking_armed_row = await self.repository.get_armed_item(
                session,
                player_id=identity.player_id,
                action_type="cooking",
            )
            cooking_item, cooking_item_quantity = self._armed_item(
                cooking_armed_row,
                "cooking",
            )
            upgrades = await self.economy_repository.get_upgrade_levels(
                session,
                player_id=identity.player_id,
            )
            economy_row = await self.economy_repository.economy_profile_row(
                session,
                player_id=identity.player_id,
            )
            if economy_row is None:
                raise RuntimeError("玩家经济档案初始化后无法读取。")
            claimed_veteran_tiers = await self.economy_repository.claimed_veteran_reward_tiers(
                session,
                player_id=identity.player_id,
            )
            food_collected, food_total = await self.economy_repository.visible_food_catalog_counts(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
            )
            visible_collected, visible_total = await self.repository.visible_catalog_counts(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
            )
            collection_rows = await self.asset_repository.list_collection_progress_rows(
                session,
                player_id=identity.player_id,
            )
            showcase_row = await self.social_repository.showcase_row(
                session,
                player_id=identity.player_id,
            )
        experience = int(row["experience"])
        progress = level_progress(experience)
        benefits = veteran_benefits(progress.level)
        base_weights = normalize_weights(self.catching.weights())
        level_weights = catch_weights(
            base_weights,
            player_level=progress.level,
        )
        return PlayerProfile(
            display_name=str(row["display_name"]),
            coin_balance=int(row["coin_balance"]),
            total_experience=experience,
            level=progress,
            total_catches=int(row["total_catches"]),
            active_pigs=int(row["active_pigs"]),
            catalog_count=visible_collected,
            visible_catalog_total=visible_total,
            held_records=int(row["held_records"]),
            daily_count=daily_count,
            daily_limit=self._restricted_daily_limit(
                normal_limit=quota_layers.effective_limit(used_count=daily_count),
                restriction=catch_restriction,
            ),
            cooldown_remaining_seconds=_cooldown_remaining(
                now=now_datetime,
                last_acquired_at=last_acquired_at,
                cooldown_seconds=self.catching.cooldown_seconds,
            ),
            feed_level=feed_level,
            armed_item=armed_item,
            armed_item_quantity=armed_quantity,
            cookware_level=upgrades["cookware"],
            total_cooks=int(economy_row["total_cooks"]),
            active_foods=int(economy_row["active_foods"]),
            food_catalog_count=food_collected,
            visible_food_catalog_total=food_total,
            armed_cooking_item=cooking_item,
            armed_cooking_item_quantity=cooking_item_quantity,
            collections=_format_collection_rows(collection_rows),
            showcase_pig=(
                f"{showcase_row.get('pig_display_name')}#{showcase_row.get('pig_short_code')}"
                if showcase_row.get("pig_display_name") and showcase_row.get("pig_short_code")
                else ""
            ),
            showcase_food=(
                f"{showcase_row.get('food_display_name')}#{showcase_row.get('food_short_code')}"
                if showcase_row.get("food_display_name") and showcase_row.get("food_short_code")
                else ""
            ),
            level_catch_base_high_percent=sum(base_weights[3:]),
            level_catch_adjusted_high_percent=sum(level_weights[3:]),
            level_cooking_bonus_percent=(level_cooking_higher_rarity_multiplier(progress.level) - 1.0) * 100.0,
            veteran_tier=benefits.tier,
            veteran_catch_coin_bonus=benefits.catch_coin_bonus,
            veteran_cook_coin_bonus=benefits.cook_coin_bonus,
            veteran_experience_bonus_percent=benefits.experience_bonus_percent,
            veteran_milestone_coin_reward=benefits.milestone_coin_reward,
            veteran_cumulative_coin_reward=benefits.cumulative_coin_reward,
            veteran_claimed_tier=max(claimed_veteran_tiers, default=0),
            veteran_next_tier_level=benefits.next_tier_level,
            veteran_next_tier_coin_reward=benefits.next_tier_coin_reward,
        )

    async def pig_detail(self, identity: CommandIdentity, selector_text: str) -> PigView:
        """Resolve exactly one active pig owned by the current-group player."""

        selector = parse_asset_selector(selector_text)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            rows = await self.repository.find_active_pigs(
                session,
                player_id=identity.player_id,
                selector=selector,
            )
        if not rows:
            raise PigNotFoundError(f"你的猪猪背包中找不到“{selector_text.strip()}”。")
        if len(rows) > 1:
            candidates = "、".join(f"{row['display_name_snapshot']}#{row['short_code']}" for row in rows[:8])
            raise AmbiguousPigSelectorError(f"“{selector.name}”有多只，请带短编号重试：{candidates}")
        return self._pig_view(rows[0])

    async def inventory(
        self,
        identity: CommandIdentity,
        *,
        page: int,
        rarity: int | None,
        sort: str,
    ) -> InventoryPage:
        """Read one filtered page of active pigs."""

        page_size = self.catching.inventory_page_size
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            total, rows = await self.repository.inventory_page(
                session,
                player_id=identity.player_id,
                rarity=rarity,
                sort=sort,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
        pages = valid_page_count(page, total, page_size)
        return InventoryPage(
            display_name=identity.display_name,
            page=page,
            page_count=pages,
            total_count=total,
            page_size=page_size,
            rarity=rarity,
            sort=sort,
            pigs=tuple(self._pig_view(row) for row in rows),
        )

    async def catalog(
        self,
        identity: CommandIdentity,
        *,
        rarity: int | None,
        undiscovered_only: bool,
    ) -> CatalogPage:
        """Read every visible catalog slot without leaking private asset details."""

        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            total, rows = await self.repository.catalog_entries(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                rarity=rarity,
                undiscovered_only=undiscovered_only,
            )
            collected, visible_total = await self.repository.visible_catalog_counts(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
            )
            collection_rows = await self.asset_repository.list_collection_progress_rows(
                session,
                player_id=identity.player_id,
            )
        return CatalogPage(
            display_name=identity.display_name,
            total_count=total,
            rarity=rarity,
            undiscovered_only=undiscovered_only,
            collected_count=collected,
            visible_catalog_total=visible_total,
            entries=tuple(_catalog_from_row(row) for row in rows),
            collections=_format_collection_rows(collection_rows),
        )

    async def records(self, identity: CommandIdentity, *, page: int) -> RecordsPage:
        """Read current-group size and weight records."""

        page_size = self.catching.records_page_size
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            total, rows = await self.repository.records_page(
                session,
                scope_id=identity.scope.value,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            global_rows = (
                await self.social_repository.global_records(
                    session,
                    scope_id=identity.scope.value,
                )
                if page == 1
                else []
            )
            sighting_rows = (
                await self.social_repository.giant_sightings(
                    session,
                    scope_id=identity.scope.value,
                    limit=self.ranking.giant_sightings_limit,
                )
                if page == 1
                else []
            )
        pages = valid_page_count(page, total, page_size)
        return RecordsPage(
            group_name=identity.group_name,
            page=page,
            page_count=pages,
            total_count=total,
            page_size=page_size,
            entries=tuple(
                RecordEntry(
                    record_type=str(row["record_type"]),
                    record_value=float(row["record_value"]),
                    achieved_at=str(row["achieved_at"]),
                    display_name=str(row["display_name"]),
                    rarity=int(row["rarity"]),
                    short_code=str(row["short_code"]),
                    holder_display_name=str(row["holder_display_name"]),
                    player_id=str(row["player_id"]),
                )
                for row in rows
            ),
            global_entries=tuple(
                RecordEntry(
                    record_type=str(row["record_type"]),
                    record_value=float(row["record_value"]),
                    achieved_at=str(row["achieved_at"]),
                    display_name=str(row["display_name"]),
                    rarity=int(row["rarity"]),
                    short_code=str(row["short_code"]),
                    holder_display_name=str(row["holder_display_name"]),
                    player_id=str(row["player_id"]),
                )
                for row in global_rows
            ),
            giant_sightings=tuple(
                GiantSightingEntry(
                    display_name=str(row["display_name"]),
                    rarity=int(row["rarity"]),
                    short_code=str(row["short_code"]),
                    holder_display_name=str(row["holder_display_name"]),
                    size_value=float(row["size_value"]),
                    weight_value=float(row["weight_value"]),
                    giant_score=float(row["giant_score"]),
                    size_qualified=bool(row["size_qualified"]),
                    weight_qualified=bool(row["weight_qualified"]),
                    achieved_at=str(row["achieved_at"]),
                    player_id=str(row["player_id"]),
                )
                for row in sighting_rows
            ),
        )

    async def daily_giants(self, identity: CommandIdentity) -> DailyGiants:
        """Read today's per-player best size and weight in the current scope."""

        now_datetime = _safe_datetime(self.clock.now())
        beijing_timezone = timezone(timedelta(hours=8), "Asia/Shanghai")
        local_now = now_datetime.astimezone(beijing_timezone)
        local_start = datetime.combine(
            local_now.date(),
            time.min,
            tzinfo=beijing_timezone,
        )
        start_at = iso_timestamp(local_start.astimezone(UTC))
        end_at = iso_timestamp(now_datetime)
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=end_at,
            )
            participant_count, catch_count, size_rows, weight_rows = await self.repository.daily_giants(
                session,
                scope_id=identity.scope.value,
                start_at=start_at,
                end_at=end_at,
                limit=self.ranking.ranking_page_size,
            )

        def entries(rows: Sequence[Mapping[str, object]]) -> tuple[DailyGiantEntry, ...]:
            return tuple(
                DailyGiantEntry(
                    rank=index,
                    player_id=str(row["player_id"]),
                    holder_display_name=str(row["holder_display_name"]),
                    pig_instance_id=str(row["pig_instance_id"]),
                    display_name=str(row["display_name"]),
                    rarity=int(row["rarity"]),
                    short_code=str(row["short_code"]),
                    size_value=float(row["size_value"]),
                    weight_value=float(row["weight_value"]),
                    acquired_at=str(row["acquired_at"]),
                    image_relpath=str(row["image_relpath"]),
                    image_fit=str(row["image_fit"]),
                    media_visible=bool(row["media_visible"]),
                    is_animated=bool(row["is_animated"]),
                )
                for index, row in enumerate(rows, start=1)
            )

        return DailyGiants(
            group_name=identity.group_name,
            date_label=f"北京时间 {local_now:%Y-%m-%d} {local_now:%H:%M} 截止",
            participant_count=participant_count,
            catch_count=catch_count,
            size_entries=entries(size_rows),
            weight_entries=entries(weight_rows),
        )

    async def arm_item(
        self,
        identity: CommandIdentity,
        item_name: str,
        *,
        quantity: int = 1,
    ) -> ItemActionResult:
        """Queue an owned item for consecutive compatible successful actions."""

        item = item_by_name(item_name)
        if quantity <= 0:
            raise ItemInventoryError("道具连续使用次数必须是正整数。")
        request_payload = {"command_version": 2, "item_id": item.item_id, "quantity": quantity}
        idempotency_key = MessageKeyFactory.build(identity, _ARM_ITEM_COMMAND)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_ARM_ITEM_COMMAND,
                    request_payload=request_payload,
                )
                payload = receipt_payload(existing)
                stored_item = item_by_id(str(payload["item_id"]))
                return ItemActionResult(
                    receipt=existing,
                    receipt_created=False,
                    operation="armed",
                    item=stored_item,
                    quantity=int(payload["quantity"]),
                    armed_uses=int(payload.get("armed_uses") or 1),
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            inventory_quantity = await self.repository.item_quantity(
                session,
                player_id=identity.player_id,
                item_id=item.item_id,
            )
            if inventory_quantity <= 0:
                raise ItemInventoryError(f"你的背包中没有“{item.display_name}”。")
            if quantity > inventory_quantity:
                raise ItemInventoryError(
                    f"“{item.display_name}”库存只有 {inventory_quantity} 个，无法安排连续 {quantity} 次。"
                )
            await self.repository.arm_item(
                session,
                player_id=identity.player_id,
                action_type=item.action_type,
                item_id=item.item_id,
                remaining_uses=quantity,
                now=now,
            )
            payload = {
                "operation": "armed",
                "item_id": item.item_id,
                "quantity": inventory_quantity,
                "armed_uses": quantity,
            }
            provisional = ItemActionResult(
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=_ARM_ITEM_COMMAND,
                    request_payload=request_payload,
                    result_type="item-armed",
                    result_object_id=item.item_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                operation="armed",
                item=item,
                quantity=inventory_quantity,
                armed_uses=quantity,
            )
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=_ARM_ITEM_COMMAND,
                request_fingerprint=request_fingerprint(request_payload),
                result_type="item-armed",
                result_object_id=item.item_id,
                result_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=format_item_action_summary(provisional),
                now=now,
            )
            return ItemActionResult(
                receipt=reservation.receipt,
                receipt_created=reservation.created,
                operation="armed",
                item=item,
                quantity=inventory_quantity,
                armed_uses=quantity,
            )

    async def cancel_item(
        self,
        identity: CommandIdentity,
        action_type: str,
    ) -> ItemActionResult:
        """Cancel one equipped action item without changing inventory quantity."""

        if action_type not in {"catching", "cooking"}:
            raise DomainValidationError("动作只能是 catching 或 cooking。")
        request_payload = {"action_type": action_type}
        idempotency_key = MessageKeyFactory.build(identity, _CANCEL_ITEM_COMMAND)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_CANCEL_ITEM_COMMAND,
                    request_payload=request_payload,
                )
                payload = receipt_payload(existing)
                stored_item = item_by_id(str(payload["item_id"]))
                return ItemActionResult(
                    receipt=existing,
                    receipt_created=False,
                    operation="cancelled",
                    item=stored_item,
                    quantity=int(payload["quantity"]),
                    armed_uses=int(payload.get("armed_uses") or 1),
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            cancelled = await self.repository.cancel_armed_item(
                session,
                player_id=identity.player_id,
                action_type=action_type,
            )
            if cancelled is None:
                label = "抓猪" if action_type == "catching" else "做菜"
                raise ItemInventoryError(f"当前没有为“{label}”装备道具。")
            item_id, armed_uses = cancelled
            item = item_by_id(item_id)
            quantity = await self.repository.item_quantity(
                session,
                player_id=identity.player_id,
                item_id=item.item_id,
            )
            payload = {
                "operation": "cancelled",
                "item_id": item.item_id,
                "quantity": quantity,
                "armed_uses": armed_uses,
            }
            provisional = ItemActionResult(
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=_CANCEL_ITEM_COMMAND,
                    request_payload=request_payload,
                    result_type="item-cancelled",
                    result_object_id=item.item_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                operation="cancelled",
                item=item,
                quantity=quantity,
                armed_uses=armed_uses,
            )
            reservation = await self.receipt_repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=_CANCEL_ITEM_COMMAND,
                request_fingerprint=request_fingerprint(request_payload),
                result_type="item-cancelled",
                result_object_id=item.item_id,
                result_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=format_item_action_summary(provisional),
                now=now,
            )
            return ItemActionResult(
                receipt=reservation.receipt,
                receipt_created=reservation.created,
                operation="cancelled",
                item=item,
                quantity=quantity,
                armed_uses=armed_uses,
            )

    async def _catch_from_receipt(
        self,
        session: DatabaseSession,
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> CatchResult:
        if receipt.result_type != "pig" or not receipt.result_object_id:
            raise ReceiptConflictError("抓猪回执没有关联猪实例。")
        row = await self.repository.get_pig_by_instance_id(
            session,
            pig_instance_id=receipt.result_object_id,
        )
        if row is None:
            raise ReceiptConflictError("抓猪回执关联的猪实例不存在。")
        payload = receipt_payload(receipt)
        weights_raw = payload.get("weights", ())
        if not isinstance(weights_raw, list) or len(weights_raw) != 6:
            raise ReceiptConflictError("抓猪回执中的概率快照无效。")
        if "display_tags" in payload:
            # 新回执重放保持当时标签；旧回执没有此字段时使用兼容的当前模板。
            row["display_tags_json"] = json.dumps(payload["display_tags"], ensure_ascii=False)
        return CatchResult(
            pig=self._pig_view(row),
            receipt=receipt,
            receipt_created=receipt_created,
            daily_count=int(payload["daily_count"]),
            daily_limit=int(payload["daily_limit"]),
            coin_reward=int(payload["coin_reward"]),
            experience_reward=int(payload["experience_reward"]),
            coin_balance=int(payload["coin_balance"]),
            total_experience=int(payload["total_experience"]),
            catalog_new=bool(payload["catalog_new"]),
            size_record=bool(payload["size_record"]),
            weight_record=bool(payload["weight_record"]),
            feed_level=int(payload["feed_level"]),
            item_id=str(payload.get("item_id") or ""),
            item_name=str(payload.get("item_name") or ""),
            weights=tuple(float(value) for value in weights_raw),
            item_remaining_uses=int(payload.get("item_remaining_uses") or 0),
            effect_summaries=tuple(str(value) for value in payload.get("effect_summaries", []) if str(value).strip()),
            excluded_summaries=tuple(
                str(value) for value in payload.get("excluded_summaries", []) if str(value).strip()
            ),
            exclusive_effect_active=bool(payload.get("exclusive_effect_active") or False),
            quota_exempt_catch=bool(payload.get("quota_exempt_catch") or False),
            global_size_record=bool(payload.get("global_size_record") or False),
            global_weight_record=bool(payload.get("global_weight_record") or False),
            giant_sighting=bool(payload.get("giant_sighting") or False),
            technique_resolution=self._technique_resolution_from_payload(payload.get("technique_resolution")),
            veteran_coin_reward=int(payload.get("veteran_coin_reward") or 0),
            veteran_reward_levels=tuple(int(value) for value in payload.get("veteran_reward_levels", [])),
        )

    @staticmethod
    def _technique_food_view(row: Mapping[str, object]) -> TechniqueFoodView:
        return TechniqueFoodView(
            food_instance_id=str(row["food_instance_id"]),
            short_code=str(row["short_code"]),
            owner_player_id=str(row["owner_player_id"]),
            owner_display_name=(str(row.get("owner_display_name") or "").strip() or "未命名群友"),
            rarity=int(row["rarity"]),
            display_name=str(row["display_name_snapshot"]),
            image_relpath=str(row.get("image_relpath") or ""),
            image_fit=str(row.get("image_fit") or "contain"),
            media_format=str(row.get("media_format") or "PNG"),
            is_animated=bool(row.get("is_animated") or False),
            media_visible=bool(row.get("media_visible") or False),
        )

    @classmethod
    def _technique_resolution_from_payload(
        cls,
        raw: object,
    ) -> TechniqueCatchResolution | None:
        if not isinstance(raw, Mapping):
            return None
        raw_foods = raw.get("generated_foods")
        foods: list[TechniqueFoodView] = []
        if isinstance(raw_foods, list):
            for item in raw_foods:
                if isinstance(item, Mapping):
                    foods.append(cls._technique_food_view(item))
        return TechniqueCatchResolution(
            technique_id=str(raw.get("technique_id") or ""),
            technique_name=str(raw.get("technique_name") or ""),
            source_player_id=str(raw.get("source_player_id") or ""),
            source_display_name=str(raw.get("source_display_name") or "未命名群友"),
            target_player_id=str(raw.get("target_player_id") or ""),
            target_display_name=str(raw.get("target_display_name") or ""),
            remaining_uses=int(raw.get("remaining_uses") or 0),
            summary=str(raw.get("summary") or ""),
            generated_foods=tuple(foods),
        )

    @staticmethod
    def _technique_resolution_payload(
        resolution: TechniqueCatchResolution,
    ) -> dict[str, object]:
        return {
            "technique_id": resolution.technique_id,
            "technique_name": resolution.technique_name,
            "source_player_id": resolution.source_player_id,
            "source_display_name": resolution.source_display_name,
            "target_player_id": resolution.target_player_id,
            "target_display_name": resolution.target_display_name,
            "remaining_uses": resolution.remaining_uses,
            "summary": resolution.summary,
            "generated_foods": [
                {
                    "food_instance_id": food.food_instance_id,
                    "short_code": food.short_code,
                    "owner_player_id": food.owner_player_id,
                    "owner_display_name": food.owner_display_name,
                    "rarity": food.rarity,
                    "display_name_snapshot": food.display_name,
                    "image_relpath": food.image_relpath,
                    "image_fit": food.image_fit,
                    "media_format": food.media_format,
                    "is_animated": food.is_animated,
                    "media_visible": food.media_visible,
                }
                for food in resolution.generated_foods
            ],
        }

    def _pig_view(self, row: Mapping[str, object]) -> PigView:
        return pig_view_from_row(
            row,
            giant_size_threshold_cm=self.ranking.giant_size_threshold_cm,
            giant_weight_threshold_kg=self.ranking.giant_weight_threshold_kg,
        )

    @staticmethod
    def _template_buckets(
        templates: Sequence[Mapping[str, object]],
    ) -> dict[Rarity, list[Mapping[str, object]]]:
        buckets = {rarity: [] for rarity in Rarity}
        for template in templates:
            try:
                rarity = Rarity(int(template["rarity"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise DomainValidationError("素材库中存在无效猪猪品质。") from exc
            buckets[rarity].append(template)
        return buckets

    @staticmethod
    def _select_template(
        candidates: Sequence[Mapping[str, object]],
        roll: float,
        *,
        giant_template_multiplier: float = 1.0,
    ) -> Mapping[str, object]:
        """Select one template, optionally weighting giant-profile candidates."""

        if not candidates:
            raise NoDrawableTemplateError("所选品质没有可用猪猪素材。")
        normalized_roll = float(roll)
        if not 0.0 <= normalized_roll < 1.0:
            raise DomainValidationError("模板随机落点必须位于 [0, 1)。")
        multiplier = max(1.0, float(giant_template_multiplier))
        if multiplier == 1.0:
            return candidates[min(int(normalized_roll * len(candidates)), len(candidates) - 1)]
        candidate_weights = [
            multiplier if str(candidate.get("stature_profile") or "") == StatureProfile.GIANT.value else 1.0
            for candidate in candidates
        ]
        target = normalized_roll * sum(candidate_weights)
        cumulative = 0.0
        for candidate, weight in zip(candidates, candidate_weights, strict=True):
            cumulative += weight
            if target < cumulative:
                return candidate
        return candidates[-1]

    def _available_weights(
        self,
        *,
        buckets: Mapping[Rarity, Sequence[Mapping[str, object]]],
        feed_level: int,
        player_level: int,
        item_id: str,
    ) -> tuple[float, ...]:
        weights = catch_weights(
            self.catching.weights(),
            feed_level=feed_level,
            player_level=player_level,
            item_id=item_id,
            six_star_available=bool(buckets[Rarity.SIX]),
        )
        available = tuple(weight if buckets[rarity] else 0.0 for rarity, weight in zip(Rarity, weights, strict=True))
        if not any(available):
            raise NoDrawableTemplateError("当前群没有可用猪猪素材。")
        return normalize_weights(available)

    @staticmethod
    def _armed_item(
        row: Mapping[str, object] | None,
        action_type: str,
    ) -> tuple[ItemDefinition | None, int]:
        if row is None:
            return None, 0
        item = item_by_id(str(row["item_id"]))
        if item.action_type != action_type:
            raise ItemInventoryError("已装备道具与当前动作不兼容，请先取消道具。")
        inventory_quantity = int(row["quantity"] or 0)
        remaining_uses = int(row.get("remaining_uses") or 0)
        if inventory_quantity <= 0 or remaining_uses <= 0:
            raise ItemInventoryError(f"已装备的“{item.display_name}”库存不足，请先取消道具。")
        return item, remaining_uses

    @staticmethod
    def _restricted_daily_limit(
        *,
        normal_limit: int,
        restriction: Mapping[str, object] | None,
    ) -> int:
        if restriction is None:
            return normal_limit
        return min(normal_limit, int(restriction["limit_value"]))

    @staticmethod
    def _restriction_expiry_label(value: str) -> str:
        if not value:
            return "长期"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("北京时间 %Y-%m-%d %H:%M:%S")

    async def _grant_random_three_star_foods(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        source_pig_instance_id: str,
        source_weight: float,
        source_weight_percentile: float,
        source_fat_category: str,
        count: int,
        now: str,
        source_key: str,
    ) -> tuple[str, ...]:
        """Grant auditable random 3-star dishes without replaying cooking rewards."""

        templates = await self.economy_repository.list_drawable_food_templates(
            session,
            scope_id=identity.scope.value,
            rarity=3,
        )
        if not templates:
            raise NoDrawableTemplateError("粉蓝四叶草奖励找不到当前群可用的三星菜模板。")
        labels: list[str] = []
        reserved_codes: list[str] = []
        for index in range(max(0, int(count))):
            template_roll = self.random_source.random()
            template = templates[min(int(template_roll * len(templates)), len(templates) - 1)]
            portion_roll = self.random_source.random()
            attributes = generate_food_attributes(
                rarity=Rarity.THREE,
                template_id=str(template["template_id"]),
                source_weight=source_weight,
                source_weight_percentile=source_weight_percentile,
                portion_roll=portion_roll,
            )
            food_instance_id = self._new_identifier()
            short_code = await self._new_unique_short_code(session, reserved=reserved_codes)
            reserved_codes.append(short_code)
            await self.economy_repository.insert_food_instance(
                session,
                values={
                    "food_instance_id": food_instance_id,
                    "short_code": short_code,
                    "scope_id": identity.scope.value,
                    "owner_player_id": identity.player_id,
                    "template_id": str(template["template_id"]),
                    "template_version": int(template["template_version"]),
                    "source_pig_instance_id": source_pig_instance_id,
                    "rarity": 3,
                    "display_name_snapshot": str(template["display_name"]),
                    "portion_weight": attributes.portion_weight,
                    "fat_category": source_fat_category,
                    "official_value": attributes.official_value,
                    "effect_id": str(template.get("effect_id") or ""),
                    "effect_params_json": str(template.get("effect_params_json") or "{}"),
                    "ruleset_version": RULESET_VERSION,
                    "random_snapshot_json": self.repository.random_snapshot_json(
                        {
                            "ruleset_version": RULESET_VERSION,
                            "source": "window-six-star-resonance",
                            "source_key": source_key,
                            "reward_index": index + 1,
                            "template_roll": template_roll,
                            "portion_roll": portion_roll,
                            "source_pig_instance_id": source_pig_instance_id,
                        }
                    ),
                    "acquired_at": now,
                    "updated_at": now,
                },
            )
            await self.economy_repository.upsert_food_catalog(
                session,
                player_id=identity.player_id,
                template_id=str(template["template_id"]),
                portion_weight=attributes.portion_weight,
                now=now,
            )
            labels.append(f"{template['display_name']}#{short_code}")
        return tuple(labels)

    def _new_identifier(self) -> str:
        candidate = str(self.id_factory() or "").strip()
        if not candidate or len(candidate) > 128:
            raise RuntimeError("实例 ID 生成器返回了无效值。")
        return candidate

    async def _new_unique_short_code(
        self,
        session: DatabaseSession,
        *,
        reserved: Sequence[str] = (),
    ) -> str:
        reserved_codes = {str(value).strip().upper() for value in reserved}
        for _ in range(32):
            candidate = str(self.short_code_factory() or "").strip().upper()
            if not is_valid_short_code(candidate):
                continue
            if candidate in reserved_codes:
                continue
            if not await self.repository.short_code_exists(session, candidate):
                return candidate
        raise RuntimeError("连续生成 32 次仍无法得到唯一猪猪短编号。")

    @staticmethod
    def _pending_status():
        from ..domain.enums import ReceiptSendStatus

        return ReceiptSendStatus.PENDING

    @staticmethod
    def _provisional_receipt(
        *,
        idempotency_key: str,
        identity: CommandIdentity,
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
            result_json=json.dumps(dict(result_payload), ensure_ascii=False),
            text_summary="",
            send_status=GameplayService._pending_status(),
            created_at=now,
            updated_at=now,
        )
