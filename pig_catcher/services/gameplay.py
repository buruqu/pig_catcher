"""Third-round catching, collection, records, and item application services."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..config.model import CatchingSection, RankingSection
from ..domain.economy import level_cooking_higher_rarity_multiplier
from ..domain.enums import Rarity, RecordType, StatureProfile
from ..domain.errors import (
    AmbiguousPigSelectorError,
    CatchCooldownError,
    DailyCatchLimitError,
    DomainValidationError,
    ItemInventoryError,
    NoDrawableTemplateError,
    PigNotFoundError,
    ReceiptConflictError,
)
from ..domain.food_effects import active_effect_from_row, apply_catch_effects
from ..domain.gameplay import (
    CATCH_COIN_REWARDS,
    CATCH_EXPERIENCE_REWARDS,
    PIG_RARITY_NAMES,
    ItemDefinition,
    LevelProgress,
    generate_pig_attributes,
    item_by_id,
    item_by_name,
    level_progress,
    size_label,
    weight_label,
)
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, RandomSource, SystemClock, SystemRandomSource
from ..domain.quota import catch_quota_window
from ..domain.rules import (
    LEVEL_CATCH_BONUS_CAP_LEVEL,
    catch_weights,
    choose_rarity,
    normalize_weights,
)
from ..domain.selectors import new_short_code, parse_asset_selector
from ..domain.social import describe_body_scale
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    AssetRepository,
    EconomyRepository,
    FrameworkRepository,
    GameplayRepository,
    ReceiptRepository,
    SocialRepository,
)
from ..version import RULESET_VERSION
from .assets import CollectionProgress
from .command_state import (
    iso_timestamp,
    receipt_payload,
    valid_page_count,
    validate_existing_receipt,
)
from .receipts import request_fingerprint

_CATCH_COMMAND = "pig-catcher.catch"
_ARM_ITEM_COMMAND = "pig-catcher.arm-item"
_CANCEL_ITEM_COMMAND = "pig-catcher.cancel-item"
_SHORT_CODE_PATTERN = re.compile(r"^[A-F0-9]{8}$")
_FAT_LABELS = {
    "lean": "偏瘦",
    "balanced": "均衡",
    "fatty": "偏肥",
}


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
    effect_summaries: tuple[str, ...] = ()
    global_size_record: bool = False
    global_weight_record: bool = False
    giant_sighting: bool = False


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


@dataclass(frozen=True, slots=True)
class ItemActionResult:
    """Committed item equip or cancellation result."""

    receipt: CommandReceipt
    receipt_created: bool
    operation: str
    item: ItemDefinition
    quantity: int


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
    stature_profile = str(
        row.get("stature_profile") or StatureProfile.STANDARD.value
    )
    body = describe_body_scale(
        stature_profile=stature_profile,
        size_value=float(row["size_value"]),
        size_percentile=float(row["size_percentile"]),
        weight_value=float(row["weight_value"]),
        weight_percentile=float(row["weight_percentile"]),
        giant_size_threshold_cm=giant_size_threshold_cm,
        giant_weight_threshold_kg=giant_weight_threshold_kg,
    )
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
        image_relpath=str(row.get("image_relpath") or ""),
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
    new_text = "NEW｜首次收入图鉴\n" if result.catalog_new else ""
    body_text = (
        f"体格：{result.pig.body_label}｜{result.pig.body_description}\n"
        if result.pig.body_label
        else ""
    )
    effect_text = (
        f"\n美食加成：{'；'.join(result.effect_summaries)}"
        if result.effect_summaries
        else ""
    )
    return (
        "【抓猪成功】\n"
        f"{result.pig.owner_display_name} 抓到了 {result.pig.stars} {result.pig.display_name}\n"
        f"{new_text}"
        f"编号：{result.pig.selector}\n"
        f"品质：{result.pig.rarity_name}\n"
        f"体型：{result.pig.size_value:.1f} cm\n"
        f"重量：{result.pig.weight_value:.2f} kg\n"
        f"体态：{result.pig.fat_label}\n"
        f"{body_text}"
        f"官方价值：{result.pig.official_value} 猪币\n"
        f"奖励：+{result.coin_reward} 猪币 / +{result.experience_reward} 经验\n"
        f"等级：Lv.{progress.level} · {progress.title}；"
        f"{result.total_experience}/{progress.next_threshold} EXP\n"
        f"当前余额：{result.coin_balance} 猪币\n"
        f"本时段抓猪：{result.daily_count}/{result.daily_limit}\n"
        f"本次道具：{item_text}\n"
        f"群纪录：{record_text}{effect_text}"
    )


def format_profile_summary(profile: PlayerProfile) -> str:
    """Return a complete text fallback for the player profile."""

    next_text = (
        "已达当前最高等级"
        if profile.level.next_threshold is None
        else f"距下一等级 {profile.level.next_threshold - profile.total_experience} 经验"
    )
    armed = profile.armed_item.display_name if profile.armed_item is not None else "无"
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
        f"本时段抓猪：{profile.daily_count}/{profile.daily_limit}\n"
        f"抓猪冷却：{profile.cooldown_remaining_seconds} 秒\n"
        f"已装备抓猪道具：{armed}\n"
        "已装备做菜道具："
        f"{profile.armed_cooking_item.display_name if profile.armed_cooking_item else '无'}"
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
        f"体格：{pig.body_label}（巨物分 {pig.giant_score:.1f}）\n"
        f"体格评价：{pig.body_description}\n"
        if pig.body_label
        else ""
    )
    return (
        "【猪猪详情】\n"
        f"{pig.stars} {pig.display_name}（{pig.rarity_name}）\n"
        f"编号：{pig.selector}\n"
        f"体型：{pig.size_value:.1f} cm（{size_label(pig.size_percentile)}）\n"
        f"重量：{pig.weight_value:.2f} kg（{weight_label(pig.weight_percentile)}）\n"
        f"体态：{pig.fat_label}\n"
        f"{body}"
        f"官方价值：{pig.official_value} 猪币\n"
        f"群纪录：{'、'.join(records) if records else '无'}\n"
        f"获得时间：{pig.acquired_at}\n"
        f"描述：{pig.description}"
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
            f"{pig.stars} {pig.selector}｜{pig.size_value:.1f}cm｜"
            f"{pig.weight_value:.2f}kg｜{pig.official_value}猪币"
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
            f"｜最佳 {entry.best_size or 0:.1f}cm / {entry.best_weight or 0:.2f}kg{animation}"
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
            f"{entry.record_label} {entry.record_value:.2f}{entry.unit}｜"
            f"{entry.holder_display_name}"
        )
    if result.global_entries:
        lines.append("—— 全群绝对纪录 ——")
        for entry in result.global_entries:
            lines.append(
                f"{entry.record_label}最高｜{'★' * entry.rarity} "
                f"{entry.display_name}#{entry.short_code}｜"
                f"{entry.record_value:.2f}{entry.unit}｜"
                f"{entry.holder_display_name}"
            )
    if result.giant_sightings:
        lines.append("—— 最近巨物目击 ——")
        for sighting in result.giant_sightings:
            lines.append(
                f"{'★' * sighting.rarity} "
                f"{sighting.display_name}#{sighting.short_code}｜"
                f"{sighting.size_value:.1f}cm / {sighting.weight_value:.2f}kg｜"
                f"{sighting.giant_score:.1f}分｜{sighting.holder_display_name}"
            )
    return "\n".join(lines)


def format_item_action_summary(result: ItemActionResult) -> str:
    """Return a text fallback for item equip or cancellation."""

    if result.operation == "armed":
        return (
            "【道具已装备】\n"
            f"{result.item.display_name} 已用于下一次"
            f"{'抓猪' if result.item.action_type == 'catching' else '做菜'}成功结算。\n"
            f"当前库存：{result.quantity}\n"
            f"效果：{result.item.effect_summary}"
        )
    return (
        "【道具已取消】\n"
        f"已取消装备 {result.item.display_name}，道具未被消耗。\n"
        f"当前库存：{result.quantity}"
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
        random_source: RandomSource | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        short_code_factory: Callable[[], str] | None = None,
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
        self.random_source = random_source or SystemRandomSource()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.short_code_factory = short_code_factory or new_short_code

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
            daily_count, last_acquired_at = await self.repository.catch_usage(
                session,
                player_id=identity.player_id,
                window_start=window_start,
                window_end=window_end,
            )
            extra_granted, extra_consumed = (
                await self.economy_repository.extra_catch_grants(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            )
            daily_limit = self.catching.daily_limit + extra_granted
            if daily_count >= daily_limit:
                raise DailyCatchLimitError(
                    f"本时段已经抓了 {daily_count}/{daily_limit} 次，"
                    f"下次刷新：北京时间 {quota_window.next_refresh_label}。"
                )
            using_extra_catch = daily_count >= self.catching.daily_limit
            if using_extra_catch and extra_consumed >= extra_granted:
                raise DailyCatchLimitError(
                    f"本时段已经抓了 {daily_count}/{daily_limit} 次，"
                    "额外抓猪机会已用完。"
                )
            remaining = _cooldown_remaining(
                now=now_datetime,
                last_acquired_at=last_acquired_at,
                cooldown_seconds=self.catching.cooldown_seconds,
            )
            if remaining:
                raise CatchCooldownError(remaining)

            templates = await self.repository.list_drawable_pig_templates(
                session,
                scope_id=identity.scope.value,
            )
            if not templates:
                raise NoDrawableTemplateError("当前群没有可用猪猪素材，请联系管理员导入并启用素材。")
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
            armed_item, armed_quantity = self._armed_item(armed_row, "catching")
            weights = self._available_weights(
                buckets=buckets,
                feed_level=feed_level,
                player_level=probability_level,
                lucky_whistle=(
                    armed_item is not None and armed_item.item_id == "lucky-whistle"
                ),
            )
            active_effects = tuple(
                active_effect_from_row(row)
                for row in await self.economy_repository.list_active_food_effects(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            )
            effect_application = apply_catch_effects(weights, active_effects)
            weights = effect_application.weights
            rarity_roll = self.random_source.random()
            rarity = choose_rarity(weights, rarity_roll)
            candidates = buckets[rarity]
            template_roll = self.random_source.random()
            template = candidates[min(int(template_roll * len(candidates)), len(candidates) - 1)]
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
            pig_instance_id = self._new_identifier()
            short_code = await self._new_unique_short_code(session)
            random_snapshot = {
                "ruleset_version": RULESET_VERSION,
                "base_weights": list(self.catching.weights()),
                "normalized_weights": [round(value, 8) for value in weights],
                "feed_level": feed_level,
                "player_level": probability_level,
                "item_id": armed_item.item_id if armed_item is not None else "",
                "rarity_roll": rarity_roll,
                "template_roll": template_roll,
                "attribute_rolls": list(attribute_rolls),
                "food_effect_entry_ids": list(
                    effect_application.consumed_entry_ids
                ),
                "food_effect_summaries": list(effect_application.summaries),
                "stature_bias": effect_application.stature_bias,
                "extra_catch_granted": extra_granted,
                "extra_catch_used": using_extra_catch,
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
                    "random_snapshot_json": self.repository.random_snapshot_json(
                        random_snapshot
                    ),
                    "acquired_at": now,
                    "updated_at": now,
                },
            )
            coin_reward = CATCH_COIN_REWARDS[rarity]
            experience_reward = CATCH_EXPERIENCE_REWARDS[rarity]
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
                stature_profile=str(
                    template.get("stature_profile")
                    or StatureProfile.STANDARD.value
                ),
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
                    raise ItemInventoryError(
                        f"已装备的“{armed_item.display_name}”库存不足，本次抓猪未结算。"
                    )
            if effect_application.consumed_entry_ids:
                await self.economy_repository.consume_food_effects(
                    session,
                    player_id=identity.player_id,
                    effect_entry_ids=effect_application.consumed_entry_ids,
                    now=now,
                )
            if using_extra_catch:
                await self.economy_repository.consume_extra_catch(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            pig_row = await self.repository.get_pig_by_instance_id(
                session,
                pig_instance_id=pig_instance_id,
            )
            if pig_row is None:
                raise RuntimeError("抓猪实例提交前无法读取。")
            pig = self._pig_view(pig_row)
            payload: dict[str, Any] = {
                "daily_count": daily_count + 1,
                "daily_limit": daily_limit,
                "quota_window": quota_window.label,
                "next_quota_refresh": quota_window.next_refresh_label,
                "coin_reward": coin_reward,
                "experience_reward": experience_reward,
                "coin_balance": coin_balance,
                "total_experience": total_experience,
                "catalog_new": catalog_new,
                "size_record": size_record,
                "weight_record": weight_record,
                "global_size_record": global_size_record,
                "global_weight_record": global_weight_record,
                "giant_sighting": giant_sighting,
                "feed_level": feed_level,
                "item_id": armed_item.item_id if armed_item is not None else "",
                "item_name": armed_item.display_name if armed_item is not None else "",
                "weights": [round(value, 8) for value in weights],
                "effect_summaries": list(effect_application.summaries),
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
                daily_count=daily_count + 1,
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
                effect_summaries=effect_application.summaries,
                global_size_record=global_size_record,
                global_weight_record=global_weight_record,
                giant_sighting=giant_sighting,
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
            )
            return CatchResult(
                pig=pig,
                receipt=reservation.receipt,
                receipt_created=reservation.created,
                daily_count=daily_count + 1,
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
                effect_summaries=effect_application.summaries,
                global_size_record=global_size_record,
                global_weight_record=global_weight_record,
                giant_sighting=giant_sighting,
            )

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
            daily_count, last_acquired_at = await self.repository.catch_usage(
                session,
                player_id=identity.player_id,
                window_start=window_start,
                window_end=window_end,
            )
            extra_granted, _ = await self.economy_repository.extra_catch_grants(
                session,
                player_id=identity.player_id,
                now=now,
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
            food_collected, food_total = (
                await self.economy_repository.visible_food_catalog_counts(
                    session,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                )
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
            daily_limit=self.catching.daily_limit + extra_granted,
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
                f"{showcase_row.get('pig_display_name')}#"
                f"{showcase_row.get('pig_short_code')}"
                if showcase_row.get("pig_display_name")
                and showcase_row.get("pig_short_code")
                else ""
            ),
            showcase_food=(
                f"{showcase_row.get('food_display_name')}#"
                f"{showcase_row.get('food_short_code')}"
                if showcase_row.get("food_display_name")
                and showcase_row.get("food_short_code")
                else ""
            ),
            level_catch_base_high_percent=sum(base_weights[3:]),
            level_catch_adjusted_high_percent=sum(level_weights[3:]),
            level_cooking_bonus_percent=(
                level_cooking_higher_rarity_multiplier(progress.level) - 1.0
            )
            * 100.0,
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
            candidates = "、".join(
                f"{row['display_name_snapshot']}#{row['short_code']}" for row in rows[:8]
            )
            raise AmbiguousPigSelectorError(
                f"“{selector.name}”有多只，请带短编号重试：{candidates}"
            )
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
                )
                for row in sighting_rows
            ),
        )

    async def arm_item(self, identity: CommandIdentity, item_name: str) -> ItemActionResult:
        """Equip an owned item without consuming it until a successful action."""

        item = item_by_name(item_name)
        request_payload = {"item_id": item.item_id}
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
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            quantity = await self.repository.item_quantity(
                session,
                player_id=identity.player_id,
                item_id=item.item_id,
            )
            if quantity <= 0:
                raise ItemInventoryError(f"你的背包中没有“{item.display_name}”。")
            await self.repository.arm_item(
                session,
                player_id=identity.player_id,
                action_type=item.action_type,
                item_id=item.item_id,
                now=now,
            )
            payload = {
                "operation": "armed",
                "item_id": item.item_id,
                "quantity": quantity,
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
                quantity=quantity,
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
                quantity=quantity,
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
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            item_id = await self.repository.cancel_armed_item(
                session,
                player_id=identity.player_id,
                action_type=action_type,
            )
            if item_id is None:
                label = "抓猪" if action_type == "catching" else "做菜"
                raise ItemInventoryError(f"当前没有为“{label}”装备道具。")
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
            effect_summaries=tuple(
                str(value)
                for value in payload.get("effect_summaries", [])
                if str(value).strip()
            ),
            global_size_record=bool(payload.get("global_size_record") or False),
            global_weight_record=bool(payload.get("global_weight_record") or False),
            giant_sighting=bool(payload.get("giant_sighting") or False),
        )

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

    def _available_weights(
        self,
        *,
        buckets: Mapping[Rarity, Sequence[Mapping[str, object]]],
        feed_level: int,
        player_level: int,
        lucky_whistle: bool,
    ) -> tuple[float, ...]:
        weights = catch_weights(
            self.catching.weights(),
            feed_level=feed_level,
            player_level=player_level,
            lucky_whistle=lucky_whistle,
            six_star_available=bool(buckets[Rarity.SIX]),
        )
        available = tuple(
            weight if buckets[rarity] else 0.0
            for rarity, weight in zip(Rarity, weights, strict=True)
        )
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
        quantity = int(row["quantity"] or 0)
        if quantity <= 0:
            raise ItemInventoryError(
                f"已装备的“{item.display_name}”库存不足，请先取消道具。"
            )
        return item, quantity

    def _new_identifier(self) -> str:
        candidate = str(self.id_factory() or "").strip()
        if not candidate or len(candidate) > 128:
            raise RuntimeError("实例 ID 生成器返回了无效值。")
        return candidate

    async def _new_unique_short_code(self, session: DatabaseSession) -> str:
        for _ in range(32):
            candidate = str(self.short_code_factory() or "").strip().upper()
            if not _SHORT_CODE_PATTERN.fullmatch(candidate):
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
