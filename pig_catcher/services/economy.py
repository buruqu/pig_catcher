"""Fourth-round cooking, food collection, store, sale, and ledger services."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..config.model import CookingSection, EconomySection
from ..domain.economy import (
    COOK_COIN_REWARDS,
    COOK_EXPERIENCE_REWARDS,
    EAT_EXPERIENCE_REWARDS,
    FOOD_RARITY_NAMES,
    FoodAttributes,
    StoreProduct,
    adjusted_cooking_weights,
    build_store_products,
    cookware_higher_rarity_multiplier,
    generate_food_attributes,
    item_product_by_name,
    recipe_affinity,
    scale_food_attributes,
    upgrade_type_by_name,
)
from ..domain.enums import AssetKind, Rarity, ReceiptSendStatus, UpgradeType
from ..domain.errors import (
    AmbiguousFoodSelectorError,
    AmbiguousPigSelectorError,
    AssetStateConflictError,
    BatchCookRestrictedError,
    CookCooldownError,
    CookingTemplateError,
    FoodEffectError,
    FoodNotFoundError,
    InsufficientBalanceError,
    ItemInventoryError,
    LedgerReconciliationError,
    NoCookablePigError,
    PigNotFoundError,
    ReceiptConflictError,
    StoreProductError,
    UpgradeLimitError,
)
from ..domain.food_effects import (
    COOK_EFFECT_IDS,
    CURRENT_WINDOW_CATCHES,
    EVEN_CATCH_DISTRIBUTION,
    EXTRA_CATCHES,
    GROUP_COIN_TRIBUTE,
    GROUP_EFFECT_IDS,
    GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH,
    GROUP_WINDOW_HIGH_STAR_BOOST,
    NEXT_GUARANTEED_SIX_STAR_CATCH,
    NEXT_HIGH_STAR_CATCH,
    NEXT_SIX_STAR_COOK,
    NEXT_SIX_STAR_COOK_BONUS,
    NEXT_STACKABLE_SIX_STAR_COOK_BONUS,
    PERMANENT_SIX_STAR_PROGRESS,
    PERMANENT_WINDOW_CATCH,
    QUOTA_RESET_CHANCE,
    ROLLING_DAY_WINDOW_CATCHES,
    ROULETTE_CHANCES,
    SIX_STAR_COOK_FAILURE_RETURN,
    TECHNIQUE_PERMIT,
    TODAY_WINDOW_CATCHES,
    WEEKLY_WINDOW_CATCHES,
    CookingEffectApplication,
    active_effect_from_row,
    apply_cooking_effects,
    apply_six_star_progress,
    effect_summary,
    has_compatible_exclusive_cook_effect,
    resolve_food_effect,
)
from ..domain.gameplay import (
    ItemDefinition,
    item_by_id,
    level_progress,
)
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, RandomSource, SystemClock, SystemRandomSource
from ..domain.quota import catch_quota_window
from ..domain.rules import (
    BASE_CATCH_WEIGHTS,
    catch_weights,
    choose_rarity,
    cooking_weights,
    normalize_weights,
)
from ..domain.selectors import parse_asset_selector
from ..domain.short_codes import is_valid_short_code, new_short_code
from ..domain.special_content import (
    GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS,
    GOJO_PIG_TEMPLATE_ID,
    INVERTED_SPEAR_ITEM_ID,
    KFC_FOOD_TEMPLATE_ID,
    KFC_PIG_TEMPLATE_ID,
    SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS,
    SUKUNA_FOOD_TEMPLATE_ID,
    SUKUNA_PIG_TEMPLATE_ID,
    TECHNIQUE_DOMAIN_GOJO_BYPASS,
)
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    EconomyRepository,
    FrameworkRepository,
    GameplayRepository,
    ReceiptRepository,
    SocialRepository,
    TechniqueRepository,
)
from ..version import RULESET_VERSION
from .command_state import (
    iso_timestamp,
    receipt_payload,
    valid_page_count,
    validate_existing_receipt,
)
from .gameplay import PigView, _cooldown_remaining, _safe_datetime, pig_view_from_row
from .receipts import request_fingerprint
from .veteran_rewards import settle_veteran_rewards

_COOK_COMMAND = "pig-catcher.cook"
_BATCH_COOK_COMMAND = "pig-catcher.batch-cook"
_ROULETTE_COMMAND = "pig-catcher.roulette"
# 达妮娅泡泡云冻每层六星猪做菜概率加成（百分点），与正式目录效果参数一致。
_DANIYA_COOK_BONUS_PER_STACK = 2.0
_EAT_COMMAND = "pig-catcher.eat"
_PURCHASE_COMMAND = "pig-catcher.purchase"
_UPGRADE_COMMAND = "pig-catcher.upgrade"
_SELL_PIG_COMMAND = "pig-catcher.sell-pig"
_SELL_FOOD_COMMAND = "pig-catcher.sell-food"
_BATCH_SELL_PIG_COMMAND = "pig-catcher.batch-sell-pig"
_BATCH_SELL_FOOD_COMMAND = "pig-catcher.batch-sell-food"
_FAVORITE_COMMAND = "pig-catcher.favorite"
_FAT_LABELS = {
    "lean": "偏瘦",
    "balanced": "均衡",
    "fatty": "偏肥",
}
_STORE_CATEGORIES = {
    "全部": None,
    "抓猪": "抓猪道具",
    "做菜": "做菜道具",
    "升级": "永久升级",
}


@dataclass(frozen=True, slots=True)
class FoodView:
    """A path-free view of one persisted food instance."""

    food_instance_id: str
    short_code: str
    scope_id: str
    owner_player_id: str
    owner_display_name: str
    template_id: str
    template_version: int
    rarity: int
    display_name: str
    description: str
    portion_weight: float
    fat_category: str
    official_value: int
    effect_id: str
    effect_params: Mapping[str, object]
    acquired_at: str
    image_relpath: str
    image_fit: str
    media_format: str
    is_animated: bool
    frame_count: int
    media_visible: bool
    source_pig_name: str
    source_pig_short_code: str
    recipe_tags: tuple[str, ...]
    is_favorite: bool = False

    @property
    def stars(self) -> str:
        return "★" * self.rarity

    @property
    def rarity_name(self) -> str:
        return FOOD_RARITY_NAMES[Rarity(self.rarity)]

    @property
    def selector(self) -> str:
        return f"{self.display_name}#{self.short_code}"

    @property
    def fat_label(self) -> str:
        return _FAT_LABELS.get(self.fat_category, "未知")

    @property
    def source_selector(self) -> str:
        if not self.source_pig_name:
            return "未知原料"
        if not self.source_pig_short_code:
            return self.source_pig_name
        return f"{self.source_pig_name}#{self.source_pig_short_code}"


@dataclass(frozen=True, slots=True)
class FoodEffectOutcome:
    """Validated optional effect applied when one food is eaten."""

    summary: str
    experience_bonus: int = 0
    coin_bonus: int = 0
    queued_effect_id: str = ""
    queued_effect_params: Mapping[str, object] = field(default_factory=dict)
    granted_uses: int = 0
    expires_at: str = ""


FoodEffectHandler = Callable[[FoodView, Mapping[str, object]], FoodEffectOutcome]


@dataclass(frozen=True, slots=True)
class CookingResult:
    """Committed cooking result and its delivery receipt."""

    source_pig: PigView
    foods: tuple[FoodView, ...]
    receipt: CommandReceipt
    receipt_created: bool
    coin_reward: int
    experience_reward: int
    coin_balance: int
    total_experience: int
    catalog_new_count: int
    cookware_level: int
    item_id: str
    item_name: str
    weights: tuple[float, ...]
    bonus_serving: bool
    effect_summaries: tuple[str, ...]
    item_remaining_uses: int = 0
    excluded_summaries: tuple[str, ...] = ()
    exclusive_effect_active: bool = False
    veteran_coin_reward: int = 0
    veteran_reward_levels: tuple[int, ...] = ()

    @property
    def probability_summary(self) -> str:
        """Format the persisted final rarity weights for user-facing audit."""

        return " · ".join(
            f"{rarity}★ {weight:.1f}%" for rarity, weight in enumerate(self.weights, start=1) if weight > 0
        )


@dataclass(frozen=True, slots=True)
class FoodInventoryPage:
    """A filtered page of active food instances."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    page_size: int
    rarity: int | None
    sort: str
    foods: tuple[FoodView, ...]


@dataclass(frozen=True, slots=True)
class FoodCatalogEntry:
    """One visible food catalog slot."""

    template_id: str
    display_name: str
    rarity: int
    description: str
    image_relpath: str
    image_fit: str
    media_format: str
    is_animated: bool
    frame_count: int
    discovered: bool
    acquired_count: int
    best_portion_weight: float | None
    first_acquired_at: str
    last_acquired_at: str
    recipe_tags: tuple[str, ...]
    effect_id: str
    effect_params: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FoodCatalogPage:
    """A complete privacy-aware food catalog."""

    display_name: str
    total_count: int
    rarity: int | None
    undiscovered_only: bool
    collected_count: int
    visible_catalog_total: int
    entries: tuple[FoodCatalogEntry, ...]


@dataclass(frozen=True, slots=True)
class EatResult:
    """Committed food consumption result."""

    food: FoodView
    receipt: CommandReceipt
    receipt_created: bool
    base_experience: int
    effect: FoodEffectOutcome
    total_experience: int
    coin_balance: int
    group_rewarded_players: int = 0
    group_coin_total: int = 0
    available_effect_uses: int = 0
    veteran_coin_reward: int = 0
    veteran_reward_levels: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RouletteResult:
    """One committed six-outcome pork-cutlet roulette spin."""

    receipt: CommandReceipt
    receipt_created: bool
    outcome: int
    outcome_summary: str
    remaining_spins: int
    coin_balance: int


@dataclass(frozen=True, slots=True)
class EatConfirmationRequest:
    """A 30-second confirmation before consuming the last copy of one dish."""

    food: FoodView
    expires_at: str


@dataclass(frozen=True, slots=True)
class StorePage:
    """One filtered store page for the current player."""

    display_name: str
    coin_balance: int
    page: int
    page_count: int
    total_count: int
    page_size: int
    category: str
    feed_level: int
    cookware_level: int
    products: tuple[StoreProduct, ...]
    catch_base_weights: tuple[float, ...] = BASE_CATCH_WEIGHTS


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    """Committed item or upgrade purchase."""

    receipt: CommandReceipt
    receipt_created: bool
    product_id: str
    display_name: str
    product_type: str
    quantity: int
    unit_price: int
    total_price: int
    balance_after: int
    inventory_quantity: int
    upgrade_type: str
    upgrade_level: int


@dataclass(frozen=True, slots=True)
class SaleResult:
    """Committed official sale of one pig or food."""

    receipt: CommandReceipt
    receipt_created: bool
    asset_kind: str
    display_name: str
    selector: str
    rarity: int
    official_value: int
    balance_after: int
    pig: PigView | None = None
    food: FoodView | None = None


@dataclass(frozen=True, slots=True)
class BatchSaleResult:
    """Committed sale of every unlocked one-to-three-star asset of one kind."""

    receipt: CommandReceipt
    receipt_created: bool
    asset_kind: str
    asset_count: int
    max_rarity: int
    total_value: int
    balance_after: int
    rarity: int | None = None
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class BatchCookingResult:
    """Committed batch cooking result (one pig per output, no per-item receipt)."""

    source_pigs: tuple[PigView, ...]
    foods: tuple[FoodView, ...]
    pig_count: int
    food_count: int
    coin_reward: int
    experience_reward: int
    coin_balance: int
    total_experience: int
    catalog_new_count: int
    rarity: int | None = None
    item_use_summaries: tuple[str, ...] = ()
    effect_use_summaries: tuple[str, ...] = ()
    veteran_coin_reward: int = 0
    veteran_reward_levels: tuple[int, ...] = ()
    receipt: CommandReceipt | None = None
    receipt_created: bool = True


@dataclass(frozen=True, slots=True)
class FavoriteResult:
    """Committed favorite-protection update for one or more same-name assets."""

    receipt: CommandReceipt
    receipt_created: bool
    asset_kind: str
    display_name: str
    target_count: int
    changed_count: int
    favorite: bool


@dataclass(frozen=True, slots=True)
class _CookOutcome:
    """One committed pig-to-food conversion shared by cook and batch_cook."""

    source: PigView
    foods: tuple[FoodView, ...]
    food_ids: tuple[str, ...]
    coin_reward: int
    experience_reward: int
    coin_balance: int
    total_experience: int
    catalog_new_count: int
    cookware_level: int
    item_id: str
    item_name: str
    weights: tuple[float, ...]
    bonus_serving: bool
    effect_summaries: tuple[str, ...]
    excluded_summaries: tuple[str, ...]
    exclusive_effect_active: bool
    item_remaining_uses: int = 0
    effect_entry_ids: tuple[str, ...] = ()
    veteran_coin_reward: int = 0
    veteran_reward_levels: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One immutable pig-coin ledger entry."""

    ledger_entry_id: str
    amount: int
    balance_after: int
    reason_code: str
    reason_text: str
    source_object_type: str
    source_object_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class LedgerPage:
    """One current-group personal ledger page with reconciliation."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    page_size: int
    coin_balance: int
    ledger_total: int
    entries: tuple[LedgerEntry, ...]


def _json_object(value: object, *, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise ReceiptConflictError(f"{label}不是有效 JSON。") from exc
    if not isinstance(parsed, dict):
        raise ReceiptConflictError(f"{label}必须是 JSON 对象。")
    return parsed


def _json_strings(value: object, *, label: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise ReceiptConflictError(f"{label}不是有效 JSON。") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ReceiptConflictError(f"{label}必须是字符串数组。")
    return tuple(item.strip() for item in parsed if item.strip())


def food_view_from_row(row: Mapping[str, object]) -> FoodView:
    """Convert a repository row into a stable food DTO."""

    return FoodView(
        food_instance_id=str(row["food_instance_id"]),
        short_code=str(row["short_code"]),
        scope_id=str(row["scope_id"]),
        owner_player_id=str(row["owner_player_id"]),
        owner_display_name=str(row.get("owner_display_name") or ""),
        template_id=str(row["template_id"]),
        template_version=int(row["template_version"]),
        rarity=int(row["rarity"]),
        display_name=str(row["display_name_snapshot"]),
        description=str(row.get("description") or ""),
        portion_weight=float(row["portion_weight"]),
        fat_category=str(row["fat_category"]),
        official_value=int(row["official_value"]),
        effect_id=str(row.get("effect_id") or ""),
        effect_params=_json_object(
            row.get("effect_params_json"),
            label="美食效果参数",
        ),
        acquired_at=str(row["acquired_at"]),
        image_relpath=str(row.get("image_relpath") or ""),
        image_fit=str(row.get("image_fit") or "contain"),
        media_format=str(row.get("media_format") or "PNG"),
        is_animated=bool(row.get("is_animated") or False),
        frame_count=int(row.get("frame_count") or 1),
        media_visible=bool(row.get("media_visible", True)),
        source_pig_name=str(row.get("source_pig_name") or ""),
        source_pig_short_code=str(row.get("source_pig_short_code") or ""),
        recipe_tags=_json_strings(
            row.get("recipe_tags_json"),
            label="美食食谱标签",
        ),
        is_favorite=bool(row.get("is_favorite") or False),
    )


def _catalog_entry_from_row(row: Mapping[str, object]) -> FoodCatalogEntry:
    discovered = bool(row["discovered"])
    return FoodCatalogEntry(
        template_id=str(row["template_id"]),
        display_name=str(row["display_name"]),
        rarity=int(row["rarity"]),
        description=str(row["description"]),
        image_relpath=str(row["image_relpath"]),
        image_fit=str(row["image_fit"]),
        media_format=str(row["media_format"]),
        is_animated=bool(row["is_animated"]),
        frame_count=int(row["frame_count"]),
        discovered=discovered,
        acquired_count=int(row["acquired_count"] or 0),
        best_portion_weight=(float(row["best_portion_weight"]) if row["best_portion_weight"] is not None else None),
        first_acquired_at=str(row["first_acquired_at"] or ""),
        last_acquired_at=str(row["last_acquired_at"] or ""),
        recipe_tags=_json_strings(
            row.get("recipe_tags_json"),
            label="美食图鉴食谱标签",
        ),
        effect_id=str(row.get("effect_id") or ""),
        effect_params=_json_object(
            row.get("effect_params_json"),
            label="美食图鉴效果参数",
        ),
    )


def format_cooking_summary(result: CookingResult) -> str:
    """Return a complete path-free text fallback for cooking."""

    progress = level_progress(result.total_experience)
    main = result.foods[0]
    bonus = f"\n大份餐盒加餐：{result.foods[1].selector}" if result.bonus_serving and len(result.foods) > 1 else ""
    item = result.item_name or "无"
    if result.item_name:
        item += f"（连续使用队列剩余 {result.item_remaining_uses} 次）"
    effect_text = f"\n美食加成：{'；'.join(result.effect_summaries)}" if result.effect_summaries else ""
    excluded_text = f"\n互斥未叠加：{'；'.join(result.excluded_summaries)}" if result.excluded_summaries else ""
    veteran_text = (
        "\n资深里程碑："
        + "、".join(f"Lv.{level}" for level in result.veteran_reward_levels)
        + f" 一次性发放 +{result.veteran_coin_reward:,} 猪币"
        if result.veteran_coin_reward
        else ""
    )
    probability_line = " ".join(f"{index + 1}★{value:.1f}%" for index, value in enumerate(result.weights) if value > 0)
    probability_source_parts = [
        f"等级 Lv.{progress.level}",
        f"厨具 Lv.{result.cookware_level}",
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
        "【做菜成功】\n"
        f"原料：{result.source_pig.selector}（{result.source_pig.stars}）\n"
        f"出餐：{main.stars} {main.selector}（{main.rarity_name}）\n"
        f"份量：{main.portion_weight:.2f} kg；肥瘦：{main.fat_label}\n"
        f"官方价值：{main.official_value} 猪币{bonus}\n"
        f"奖励：+{result.coin_reward} 猪币 / +{result.experience_reward} 经验\n"
        f"等级：Lv.{progress.level} · {progress.title}；"
        f"{result.total_experience}/{progress.next_threshold} EXP\n"
        f"当前余额：{result.coin_balance} 猪币\n"
        f"厨具：Lv.{result.cookware_level}；本次道具：{item}\n"
        f"本次最终概率：{probability_line}\n"
        f"概率来源：{probability_sources}{effect_text}{excluded_text}{veteran_text}"
    )


def format_batch_cooking_summary(result: BatchCookingResult) -> str:
    """Return a complete fallback for one idempotent batch-cooking receipt."""

    lines = [
        "【批量做菜成功】",
        f"消耗 {result.pig_count} 只原料猪，产出 {result.food_count} 份美食。",
        f"奖励：+{result.coin_reward} 猪币 / +{result.experience_reward} 经验。",
        f"当前余额：{result.coin_balance} 猪币；累计经验：{result.total_experience}。",
    ]
    if result.veteran_coin_reward:
        levels = "、".join(f"Lv.{level}" for level in result.veteran_reward_levels)
        lines.append(
            f"资深里程碑：{levels} 一次性发放 +{result.veteran_coin_reward:,} 猪币。"
        )
    if result.item_use_summaries:
        lines.append("道具结算：" + "；".join(result.item_use_summaries))
    if result.effect_use_summaries:
        lines.append("美食效果：" + "；".join(result.effect_use_summaries))
    return "\n".join(lines)


def format_food_detail_summary(food: FoodView) -> str:
    """Return a complete text fallback for one food."""

    effect = effect_summary(food.effect_id, food.effect_params)
    return (
        "【美食详情】\n"
        f"{food.stars} {food.display_name}（{food.rarity_name}）\n"
        f"编号：{food.selector}{'（已收藏保护）' if food.is_favorite else ''}\n"
        f"份量：{food.portion_weight:.2f} kg\n"
        f"肥瘦：{food.fat_label}\n"
        f"官方价值：{food.official_value} 猪币\n"
        f"原料：{food.source_selector}\n"
        f"效果：{effect}\n"
        f"获得时间：{food.acquired_at}\n"
        f"描述：{food.description}"
    )


def format_food_inventory_summary(result: FoodInventoryPage) -> str:
    """Return a concise food inventory fallback."""

    lines = [
        "【美食背包】",
        f"玩家：{result.display_name}",
        f"第 {result.page}/{result.page_count} 页；共 {result.total_count} 份；排序：{result.sort}",
    ]
    if result.rarity is not None:
        lines.append(f"品质筛选：{result.rarity} 星")
    if not result.foods:
        lines.append("当前没有符合条件的美食。")
    for food in result.foods:
        lines.append(
            f"{food.stars} {food.selector}｜{food.portion_weight:.2f}kg｜{food.fat_label}｜{food.official_value}猪币"
        )
    return "\n".join(lines)


def format_food_catalog_summary(result: FoodCatalogPage) -> str:
    """Return a privacy-preserving fallback for a complete food catalog."""

    lines = [
        "【美食图鉴】",
        f"玩家：{result.display_name}",
        (
            f"按品质完整排列；本筛选 {result.total_count} 项；"
            f"总进度 {result.collected_count}/{result.visible_catalog_total}"
        ),
    ]
    if not result.entries:
        lines.append("当前没有符合条件的美食图鉴条目。")
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
            f"{'★' * entry.rarity} {entry.display_name}｜已获得 {entry.acquired_count} 次"
            f"｜最大份量 {entry.best_portion_weight or 0:.2f}kg{animation}"
        )
    return "\n".join(lines)


def format_eat_summary(result: EatResult) -> str:
    """Return a complete food-consumption fallback."""

    experience = result.base_experience + result.effect.experience_bonus
    if result.effect.queued_effect_id == GROUP_COIN_TRIBUTE:
        coin = (
            f"；从 {result.group_rewarded_players} 名群友处共收到 "
            f"{result.group_coin_total} 猪币"
        )
    else:
        coin = f"；额外 +{result.effect.coin_bonus} 猪币" if result.effect.coin_bonus else ""
    uses = (
        f"\n效果可用次数：{result.effect.granted_uses} 次"
        if result.effect.granted_uses > 1
        else ""
    )
    veteran = (
        "\n资深里程碑："
        + "、".join(f"Lv.{level}" for level in result.veteran_reward_levels)
        + f" 一次性发放 +{result.veteran_coin_reward:,} 猪币"
        if result.veteran_coin_reward
        else ""
    )
    return (
        "【美食品鉴】\n"
        f"已吃掉 {result.food.stars} {result.food.selector}\n"
        f"获得经验：+{experience}{coin}\n"
        f"当前累计经验：{result.total_experience}；猪币：{result.coin_balance}\n"
        f"效果：{result.effect.summary}{uses}{veteran}"
    )


def format_roulette_summary(result: RouletteResult) -> str:
    """Return the complete text receipt for one roulette spin."""

    return (
        "【猪保千猪排轮盘】\n"
        f"转轮结果：{result.outcome}\n"
        f"奖励：{result.outcome_summary}\n"
        f"剩余转轮机会：{result.remaining_spins} 次\n"
        f"当前猪币：{result.coin_balance}"
    )


def is_group_event_food(result: EatResult) -> bool:
    """Return whether eating this food should replace the receipt with a group event."""

    return result.effect.queued_effect_id in {
        QUOTA_RESET_CHANCE,
        GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH,
    }


def _public_actor_name(*, display_name: str, player_id: str) -> str:
    """Prefer a QQ nickname and never promote a stable platform ID to display copy."""

    normalized = str(display_name or "").strip()
    platform_user_id = str(player_id or "").rsplit(":", 1)[-1]
    if not normalized or normalized == platform_user_id:
        return "未命名群友"
    return normalized


def _paired_multiplier_text(*, five_star: float, six_star: float) -> str:
    if five_star == six_star:
        return f"五星、六星相对权重 ×{five_star:g}"
    return (
        f"五星相对权重 ×{five_star:g}、"
        f"六星相对权重 ×{six_star:g}"
    )


def format_group_event_eat_summary(result: EatResult) -> str:
    """Return the public group-wide announcement fallback for a major dish."""

    veteran = (
        "\n资深里程碑："
        + "、".join(f"Lv.{level}" for level in result.veteran_reward_levels)
        + f" 一次性发放 +{result.veteran_coin_reward:,} 猪币。"
        if result.veteran_coin_reward
        else ""
    )
    actor = _public_actor_name(
        display_name=result.food.owner_display_name,
        player_id=result.food.owner_player_id,
    )
    if result.effect.queued_effect_id == QUOTA_RESET_CHANCE:
        params = result.effect.queued_effect_params
        reset_uses = int(result.effect.granted_uses)
        group_coin = int(params.get("group_coin") or 0)
        dedicated_catches = int(params.get("group_dedicated_catches") or 0)
        five_multiplier = float(params.get("five_star_multiplier") or 1.0)
        six_multiplier = float(params.get("six_star_multiplier") or 1.0)
        hidden_chance = float(
            params.get("hidden_boost_chance_percent") or 0.0
        )
        hidden_multiplier = float(
            params.get("hidden_five_star_multiplier") or 1.0
        )
        return (
            "【全群大事件 · 糖醋排骨登场】\n"
            f"{actor} 食用了六星菜“糖醋排骨”！\n"
            f"已获得 {reset_uses} 次全群 /重置额度 机会。\n"
            f"真正发动重置时，将为本群已登记玩家各发 {group_coin:,} 猪币、"
            f"各开启 {dedicated_catches} 次专属抓猪，并让"
            f"{_paired_multiplier_text(five_star=five_multiplier, six_star=six_multiplier)}；"
            f"每次专属抓猪还有 {hidden_chance:g}% 概率爆发为 ×{hidden_multiplier:g}。\n"
            "这次只是取得发动资格，尚未重置额度；请由食用者发送 /重置额度。"
            f"{veteran}"
        )
    if result.effect.queued_effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH:
        params = result.effect.queued_effect_params
        self_coin = int(params.get("self_coin") or result.effect.coin_bonus)
        other_coin = int(params.get("other_coin") or 0)
        uses_per_player = int(params.get("uses_per_player") or 0)
        five_multiplier = float(params.get("five_star_multiplier") or 1.0)
        six_multiplier = float(params.get("six_star_multiplier") or 1.0)
        return (
            "【全群大事件 · 神龙临世】\n"
            f"{actor} 食用了六星菜“神龙化猪七星云海锅”！\n"
            f"{actor} 获得 {self_coin:,} 猪币，其余本群已登记玩家各获得 "
            f"{other_coin:,} 猪币；"
            f"本次共惠及 {result.group_rewarded_players} 名玩家。\n"
            f"全群每名玩家接下来 {uses_per_player} 次抓猪的"
            f"{_paired_multiplier_text(five_star=five_multiplier, six_star=six_multiplier)}，"
            "按六星菜独占规则结算，"
            "不与其他道具或菜品叠加。"
            f"{veteran}"
        )
    return format_eat_summary(result)


def format_store_summary(result: StorePage) -> str:
    """Return a complete store fallback."""

    feed_distributions = tuple(
        catch_weights(
            result.catch_base_weights,
            feed_level=level,
        )
        for level in range(6)
    )
    feed_probabilities = tuple(sum(weights[3:]) for weights in feed_distributions)
    cookware_bonuses = tuple((cookware_higher_rarity_multiplier(level) - 1.0) * 100.0 for level in range(6))
    lucky_before = catch_weights(result.catch_base_weights)
    def catch_item_summary(item_id: str) -> str:
        after = catch_weights(result.catch_base_weights, item_id=item_id)
        return " / ".join(
            f"{rarity}★ {before:.2f}%→{value:.2f}%"
            for rarity, before, value in zip(
                range(1, 7),
                lucky_before,
                after,
                strict=True,
            )
        )

    def reachable_distribution(weights: tuple[float, ...]) -> str:
        return "、".join(f"{target}★ {value:.0f}%" for target, value in enumerate(weights, start=1) if value > 0)

    chef_summary = " / ".join(
        f"{rarity}★猪 {reachable_distribution(cooking_weights(rarity))}"
        "→"
        + reachable_distribution(
            adjusted_cooking_weights(
                rarity,
                size_percentile=0.0,
                weight_percentile=0.0,
                cookware_level=0,
                player_level=1,
                chef_spice=True,
            )
        )
        for rarity in range(1, 6)
    )
    lines = [
        "【猪猪商城】",
        f"玩家：{result.display_name}；余额：{result.coin_balance} 猪币",
        f"分类：{result.category}；单页展示全部 {result.total_count} 项",
        f"猪饲料 Lv.{result.feed_level}；厨具 Lv.{result.cookware_level}",
        "猪饲料 Lv.0-5 的 4-6 星合计概率：" + " / ".join(f"{value:.2f}%" for value in feed_probabilities),
        "猪饲料逐档 4★/5★/6★："
        + " / ".join(
            f"Lv.{level} {weights[3]:.2f}%/{weights[4]:.2f}%/{weights[5]:.2f}%"
            for level, weights in enumerate(feed_distributions)
        ),
        "厨具 Lv.0-5 的高档菜相对权重增幅：" + " / ".join(f"+{value:.0f}%" for value in cookware_bonuses),
        "单调增益规则：等级、饲料与概率道具组合后，4/5/6 星均不会低于组合前；"
        "定向菜品只从更低星级转移概率，不压低更高星级。",
        f"幸运猪哨（基础权重，使用前→使用后）：{catch_item_summary('lucky-whistle')}",
        f"超级幸运猪哨（基础权重，使用前→使用后）：{catch_item_summary('super-lucky-whistle')}",
        f"星辉探猪镜（基础权重，使用前→使用后）：{catch_item_summary('star-pig-radar')}",
        f"主厨香料（基础分布、Lv.0，使用前→使用后）：{chef_summary}",
        "超级主厨香料：六星猪做菜由 90% 五星 / 10% 六星调整为 80% / 20%。",
        "六星菜独占效果触发时，等级、升级、道具与其他菜品均不参与；道具和其他菜品保留不消耗。",
    ]
    if not result.products:
        lines.append("当前分类没有商品。")
    for product in result.products:
        price = "已满级" if product.unit_price <= 0 else f"{product.unit_price} 猪币"
        command = (
            f"/升级 {'猪饲料' if product.product_id == 'upgrade-feed' else '厨具'}"
            if product.product_type == "upgrade"
            else f"/购买 {product.display_name}"
        )
        lines.append(f"{product.display_name}｜{price}｜{product.effect_summary}｜{command}")
    return "\n".join(lines)


def format_purchase_summary(result: PurchaseResult) -> str:
    """Return an item or upgrade purchase fallback."""

    if result.product_type == "upgrade":
        acquired = f"当前等级：Lv.{result.upgrade_level}"
    else:
        acquired = f"当前库存：{result.inventory_quantity}"
    return (
        "【购买成功】\n"
        f"商品：{result.display_name} ×{result.quantity}\n"
        f"支付：{result.total_price} 猪币（单价 {result.unit_price}）\n"
        f"{acquired}\n"
        f"剩余猪币：{result.balance_after}"
    )


def format_sale_summary(result: SaleResult) -> str:
    """Return an official-sale fallback."""

    kind = "猪猪" if result.asset_kind == "pig" else "美食"
    return (
        "【官方售卖成功】\n"
        f"{kind}：{'★' * result.rarity} {result.selector}\n"
        f"售得：{result.official_value} 猪币\n"
        f"当前余额：{result.balance_after}\n"
        "该资产已离开背包，已解锁图鉴记录不会减少。"
    )


def format_batch_sale_summary(result: BatchSaleResult) -> str:
    """Return a complete batch-sale fallback."""

    kind = "猪猪" if result.asset_kind == "pig" else "美食"
    scope = (
        f"同名美食“{result.display_name}”"
        if result.display_name
        else (
            f"{result.rarity} 星{kind}"
            if result.rarity is not None
            else f"1 至 {result.max_rarity} 星{kind}"
        )
    )
    return (
        "【批量售卖成功】\n"
        f"范围：{scope}\n"
        f"售出：{result.asset_count} 件\n"
        f"收入：{result.total_value} 猪币\n"
        f"当前余额：{result.balance_after}\n"
        "收藏保护、联动保留与交易锁定的资产未被处理，已解锁图鉴记录不会减少。"
    )


def format_favorite_summary(result: FavoriteResult) -> str:
    """Return a text receipt for favorite protection changes."""

    kind = "猪猪" if result.asset_kind == "pig" else "美食"
    action = "加入收藏保护" if result.favorite else "取消收藏保护"
    unchanged = result.target_count - result.changed_count
    extra = f"；另有 {unchanged} 件状态未变化" if unchanged else ""
    protection = (
        "收藏资产不会被做菜、吃菜、售卖、赠送、交易或任何批量操作选中。"
        if result.favorite
        else "这些资产现在可以参与名称直选和批量操作。"
    )
    return (
        f"【{action}】\n"
        f"{kind}：{result.display_name}\n"
        f"匹配：{result.target_count} 件；变更：{result.changed_count} 件{extra}\n"
        f"{protection}"
    )


def format_ledger_summary(result: LedgerPage) -> str:
    """Return a reconciled ledger fallback."""

    lines = [
        "【猪币账本】",
        f"玩家：{result.display_name}",
        (
            f"余额：{result.coin_balance}；流水合计：{result.ledger_total}；"
            f"对账：{'一致' if result.coin_balance == result.ledger_total else '异常'}"
        ),
        f"第 {result.page}/{result.page_count} 页；共 {result.total_count} 条",
    ]
    if not result.entries:
        lines.append("当前还没有猪币流水。")
    for entry in result.entries:
        sign = "+" if entry.amount > 0 else ""
        lines.append(f"{sign}{entry.amount}｜余额 {entry.balance_after}｜{entry.reason_text}｜{entry.created_at}")
    return "\n".join(lines)


class EconomyService:
    """Own fourth-round state transitions and group-scoped economy reads."""

    def __init__(
        self,
        database: PigCatcherDatabase,
        cooking: CookingSection,
        economy: EconomySection,
        *,
        repository: EconomyRepository | None = None,
        gameplay_repository: GameplayRepository | None = None,
        framework_repository: FrameworkRepository | None = None,
        receipt_repository: ReceiptRepository | None = None,
        social_repository: SocialRepository | None = None,
        technique_repository: TechniqueRepository | None = None,
        random_source: RandomSource | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        short_code_factory: Callable[[], str] | None = None,
        effect_handlers: Mapping[str, FoodEffectHandler] | None = None,
        catch_base_weights: Sequence[float] | None = None,
        quota_refresh_hours: Sequence[int] = (0, 9, 12, 19),
        quota_timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.database = database
        self.cooking = cooking
        self.economy = economy
        self.repository = repository or EconomyRepository()
        self.gameplay_repository = gameplay_repository or GameplayRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.receipt_repository = receipt_repository or ReceiptRepository()
        self.social_repository = social_repository or SocialRepository()
        self.technique_repository = technique_repository or TechniqueRepository()
        self.random_source = random_source or SystemRandomSource()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.short_code_factory = short_code_factory or new_short_code
        self.effect_handlers = dict(effect_handlers or {})
        self.catch_base_weights = normalize_weights(
            BASE_CATCH_WEIGHTS if catch_base_weights is None else catch_base_weights
        )
        self.quota_refresh_hours = tuple(int(value) for value in quota_refresh_hours)
        self.quota_timezone_name = str(quota_timezone_name)

    async def cook(self, identity: CommandIdentity, selector_text: str) -> CookingResult:
        """Atomically consume one pig and produce one or two foods."""

        await self._expire_stale_offers()
        normalized_selector = str(selector_text or "").strip()
        if normalized_selector:
            selector = parse_asset_selector(normalized_selector)
            request_payload = {
                "command_version": 2,
                "selection": "exact",
                "name": selector.name,
                "short_code": selector.short_code or "",
            }
        else:
            request_payload = {
                "command_version": 2,
                "selection": "cheapest-low-rarity",
            }
        idempotency_key = MessageKeyFactory.build(identity, _COOK_COMMAND)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_COOK_COMMAND,
                    request_payload=request_payload,
                )
                return await self._cook_from_receipt(
                    session,
                    existing,
                    receipt_created=False,
                )

            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            await self._ensure_cook_cooldown(
                session,
                player_id=identity.player_id,
                now_datetime=self.clock.now(),
            )
            source = await self._resolve_pig_for_action(
                session,
                identity,
                normalized_selector,
            )
            outcome = await self._cook_one(
                session,
                identity=identity,
                source=source,
                now=now,
                idempotency_key=idempotency_key,
                apply_armed_item=True,
            )
            payload = {
                "source_pig_instance_id": outcome.source.pig_instance_id,
                "food_instance_ids": list(outcome.food_ids),
                "coin_reward": outcome.coin_reward,
                "experience_reward": outcome.experience_reward,
                "coin_balance": outcome.coin_balance,
                "total_experience": outcome.total_experience,
                "veteran_coin_reward": outcome.veteran_coin_reward,
                "veteran_reward_levels": list(outcome.veteran_reward_levels),
                "catalog_new_count": outcome.catalog_new_count,
                "cookware_level": outcome.cookware_level,
                "item_id": outcome.item_id,
                "item_name": outcome.item_name,
                "item_remaining_uses": outcome.item_remaining_uses,
                "weights": [round(value, 8) for value in outcome.weights],
                "bonus_serving": outcome.bonus_serving,
                "effect_summaries": list(outcome.effect_summaries),
                "excluded_summaries": list(outcome.excluded_summaries),
                "exclusive_effect_active": outcome.exclusive_effect_active,
            }
            provisional = CookingResult(
                source_pig=outcome.source,
                foods=outcome.foods,
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=_COOK_COMMAND,
                    request_payload=request_payload,
                    result_type="cooking",
                    result_object_id=outcome.food_ids[0],
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                coin_reward=outcome.coin_reward,
                experience_reward=outcome.experience_reward,
                coin_balance=outcome.coin_balance,
                total_experience=outcome.total_experience,
                catalog_new_count=outcome.catalog_new_count,
                cookware_level=outcome.cookware_level,
                item_id=outcome.item_id,
                item_name=outcome.item_name,
                weights=outcome.weights,
                bonus_serving=outcome.bonus_serving,
                effect_summaries=outcome.effect_summaries,
                item_remaining_uses=outcome.item_remaining_uses,
                excluded_summaries=outcome.excluded_summaries,
                exclusive_effect_active=outcome.exclusive_effect_active,
                veteran_coin_reward=outcome.veteran_coin_reward,
                veteran_reward_levels=outcome.veteran_reward_levels,
            )
            reservation = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=_COOK_COMMAND,
                request_payload=request_payload,
                result_type="cooking",
                result_object_id=outcome.food_ids[0],
                result_payload=payload,
                text_summary=format_cooking_summary(provisional),
                now=now,
            )
            return CookingResult(
                source_pig=outcome.source,
                foods=outcome.foods,
                receipt=reservation,
                receipt_created=True,
                coin_reward=outcome.coin_reward,
                experience_reward=outcome.experience_reward,
                coin_balance=outcome.coin_balance,
                total_experience=outcome.total_experience,
                catalog_new_count=outcome.catalog_new_count,
                cookware_level=outcome.cookware_level,
                item_id=outcome.item_id,
                item_name=outcome.item_name,
                weights=outcome.weights,
                bonus_serving=outcome.bonus_serving,
                effect_summaries=outcome.effect_summaries,
                item_remaining_uses=outcome.item_remaining_uses,
                excluded_summaries=outcome.excluded_summaries,
                exclusive_effect_active=outcome.exclusive_effect_active,
                veteran_coin_reward=outcome.veteran_coin_reward,
                veteran_reward_levels=outcome.veteran_reward_levels,
            )

    async def _ensure_cook_cooldown(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        now_datetime: datetime,
    ) -> None:
        """Raise CookCooldownError when the player cooked too recently."""

        last_cook_at = await self.social_repository.get_last_cook_at(
            session,
            player_id=player_id,
        )
        remaining = _cooldown_remaining(
            now=now_datetime,
            last_acquired_at=last_cook_at,
            cooldown_seconds=self.cooking.cook_cooldown_seconds,
        )
        if remaining:
            raise CookCooldownError(remaining)

    async def batch_cook(
        self,
        identity: CommandIdentity,
        rarity: int | None,
    ) -> BatchCookingResult:
        """Cook every eligible unlocked non-collaboration pig in one transaction.

        Batch cooking consumes ordinary queued items/effects in deterministic pig
        order and never consumes collaboration pigs. Six-star-origin cooking
        effects still require individual cooking so their presentation is preserved.
        """

        if rarity is not None and not 1 <= int(rarity) <= 5:
            raise NoCookablePigError("批量做菜只支持一星至五星原料猪。")
        await self._expire_stale_offers()
        now_datetime = _safe_datetime(self.clock.now())
        now = iso_timestamp(now_datetime)
        request_payload = {
            "command_version": 2,
            "rarity": int(rarity) if rarity is not None else None,
        }
        batch_key = MessageKeyFactory.build(identity, _BATCH_COOK_COMMAND)
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, batch_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_BATCH_COOK_COMMAND,
                    request_payload=request_payload,
                )
                return await self._batch_cook_from_receipt(
                    session,
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            await self._ensure_cook_cooldown(
                session,
                player_id=identity.player_id,
                now_datetime=now_datetime,
            )
            active_effects = tuple(
                active_effect_from_row(row)
                for row in await self.repository.list_active_food_effects(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            )
            # 只有来源确实为六星菜、且会影响做菜的效果禁止批量做菜。
            restricted_effects = [
                effect
                for effect in active_effects
                if effect.effect_id in COOK_EFFECT_IDS
                and effect.source_food_rarity == 6
                and (effect.granted_uses - effect.consumed_uses) >= 1
            ]
            if restricted_effects:
                summaries = "；".join(
                    (
                        effect.source_food_name
                        or resolve_food_effect(effect.effect_id, effect.params).summary
                    )
                    for effect in restricted_effects
                )
                raise BatchCookRestrictedError(
                    f"你持有六星菜做菜效果（{summaries}），"
                    "该效果只能逐个使用 /做菜，不能批量做菜。"
                )
            rows = await self.gameplay_repository.list_cookable_pigs(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                rarity=rarity,
                keep_highest=await self.gameplay_repository.batch_keep_highest(
                    session,
                    player_id=identity.player_id,
                ),
            )
            if not rows:
                raise NoCookablePigError("背包中没有可批量做菜的猪猪；联动猪和六星定制猪不会参与批量做菜。")
            source_pigs = tuple(pig_view_from_row(row) for row in rows)
            foods: list[FoodView] = []
            coin_reward = 0
            experience_reward = 0
            veteran_coin_reward = 0
            veteran_reward_levels: list[int] = []
            catalog_new_count = 0
            last_coin_balance = 0
            last_total_experience = 0
            item_use_counts: dict[str, int] = {}
            item_remaining: dict[str, int] = {}
            effect_use_counts: dict[str, int] = {}
            effect_last_summaries: dict[str, str] = {}
            for source in source_pigs:
                outcome = await self._cook_one(
                    session,
                    identity=identity,
                    source=source,
                    now=now,
                    idempotency_key=f"{batch_key}:{source.pig_instance_id}",
                    apply_armed_item=True,
                )
                foods.extend(outcome.foods)
                coin_reward += outcome.coin_reward
                experience_reward += outcome.experience_reward
                veteran_coin_reward += outcome.veteran_coin_reward
                veteran_reward_levels.extend(outcome.veteran_reward_levels)
                catalog_new_count += outcome.catalog_new_count
                last_coin_balance = outcome.coin_balance
                last_total_experience = outcome.total_experience
                if outcome.item_name:
                    item_use_counts[outcome.item_name] = (
                        item_use_counts.get(outcome.item_name, 0) + 1
                    )
                    item_remaining[outcome.item_name] = outcome.item_remaining_uses
                for entry_id, summary in zip(
                    outcome.effect_entry_ids,
                    outcome.effect_summaries,
                    strict=False,
                ):
                    effect_use_counts[entry_id] = (
                        effect_use_counts.get(entry_id, 0) + 1
                    )
                    effect_last_summaries[entry_id] = summary
            item_use_summaries = tuple(
                f"{name} ×{count}（队列剩余 {item_remaining[name]} 次）"
                for name, count in item_use_counts.items()
            )
            effect_use_summaries = tuple(
                summary
                + (
                    f"（本批共触发 {effect_use_counts[entry_id]} 次）"
                    if effect_use_counts[entry_id] > 1
                    else ""
                )
                for entry_id, summary in effect_last_summaries.items()
            )
            payload = {
                "source_pig_instance_ids": [
                    source.pig_instance_id for source in source_pigs
                ],
                "food_instance_ids": [food.food_instance_id for food in foods],
                "pig_count": len(source_pigs),
                "food_count": len(foods),
                "coin_reward": coin_reward,
                "experience_reward": experience_reward,
                "veteran_coin_reward": veteran_coin_reward,
                "veteran_reward_levels": veteran_reward_levels,
                "coin_balance": last_coin_balance,
                "total_experience": last_total_experience,
                "catalog_new_count": catalog_new_count,
                "rarity": int(rarity) if rarity is not None else None,
                "item_use_summaries": list(item_use_summaries),
                "effect_use_summaries": list(effect_use_summaries),
            }
            provisional = BatchCookingResult(
                source_pigs=source_pigs,
                foods=tuple(foods),
                pig_count=len(source_pigs),
                food_count=len(foods),
                coin_reward=coin_reward,
                experience_reward=experience_reward,
                coin_balance=last_coin_balance,
                total_experience=last_total_experience,
                catalog_new_count=catalog_new_count,
                rarity=rarity,
                item_use_summaries=item_use_summaries,
                effect_use_summaries=effect_use_summaries,
                veteran_coin_reward=veteran_coin_reward,
                veteran_reward_levels=tuple(veteran_reward_levels),
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=batch_key,
                command_name=_BATCH_COOK_COMMAND,
                request_payload=request_payload,
                result_type="batch-cooking",
                result_object_id=(foods[0].food_instance_id if foods else ""),
                result_payload=payload,
                text_summary=format_batch_cooking_summary(provisional),
                now=now,
            )
            return BatchCookingResult(
                source_pigs=source_pigs,
                foods=tuple(foods),
                pig_count=len(source_pigs),
                food_count=len(foods),
                coin_reward=coin_reward,
                experience_reward=experience_reward,
                coin_balance=last_coin_balance,
                total_experience=last_total_experience,
                catalog_new_count=catalog_new_count,
                rarity=rarity,
                item_use_summaries=item_use_summaries,
                effect_use_summaries=effect_use_summaries,
                veteran_coin_reward=veteran_coin_reward,
                veteran_reward_levels=tuple(veteran_reward_levels),
                receipt=receipt,
                receipt_created=True,
            )

    async def _cook_one(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        source: PigView,
        now: str,
        idempotency_key: str,
        apply_armed_item: bool,
    ) -> _CookOutcome:
        """Consume one pig and produce foods inside an open transaction.

        Shared by ``cook`` and ``batch_cook``; the caller owns receipts,
        idempotency handling, and player touch.
        """

        upgrades = await self.repository.get_upgrade_levels(
            session,
            player_id=identity.player_id,
        )
        cookware_level = upgrades["cookware"]
        probability_experience = (
            await self.gameplay_repository.get_player_experience(
                session,
                player_id=identity.player_id,
            )
        )
        probability_level = level_progress(probability_experience).level
        active_effects = tuple(
            active_effect_from_row(row)
            for row in await self.repository.list_active_food_effects(
                session,
                player_id=identity.player_id,
                now=now,
            )
        )
        is_gojo_pig = source.template_id == GOJO_PIG_TEMPLATE_ID
        # 六星菜独占效果：回到未受属性、等级、厨具、道具和普通菜影响的基础层。
        exclusive_effect_active = has_compatible_exclusive_cook_effect(
            active_effects,
            source_rarity=source.rarity,
        )
        if apply_armed_item and exclusive_effect_active and not is_gojo_pig:
            apply_armed_item = False
        armed_row = (
            await self.gameplay_repository.get_armed_item(
                session,
                player_id=identity.player_id,
                action_type="cooking",
            )
            if apply_armed_item
            else None
        )
        armed_item, armed_uses = self._armed_item(armed_row)
        domain_gojo_bypass = False
        if is_gojo_pig and (
            armed_item is None or armed_item.item_id != INVERTED_SPEAR_ITEM_ID
        ):
            domain_gojo_bypass = (
                await self.technique_repository.available_permits(
                    session,
                    player_id=identity.player_id,
                    technique_id=TECHNIQUE_DOMAIN_GOJO_BYPASS,
                )
                > 0
            )
            if not domain_gojo_bypass:
                raise CookingTemplateError(
                    "无下限术式挡住了厨具，五条猪毫发无伤；原料猪与已装备道具均未消耗。"
                )
        item_compatible = bool(
            armed_item is not None
            and (
                (
                    source.rarity <= 5
                    and armed_item.item_id
                    in {
                        "chef-spice",
                        "precision-knife",
                        "slow-cook-seasoning",
                        "large-lunch-box",
                        "no-downgrade-lid",
                        "harvest-apron",
                    }
                )
                or (
                    source.rarity <= 4
                    and armed_item.item_id == "ascension-stove-core"
                )
                or (
                    source.rarity == 6
                    and armed_item.item_id == "super-chef-spice"
                )
                or (
                    is_gojo_pig
                    and armed_item.item_id == INVERTED_SPEAR_ITEM_ID
                )
            )
        )
        applied_item = armed_item if item_compatible else None
        if domain_gojo_bypass:
            # 隐藏彩蛋：领域展开留下的一次许可直接突破无下限，并保证本次
            # 五星结果进入五条猪专属菜池；其他道具与菜品队列均保留。
            applied_item = None
            weights = normalize_weights((0, 0, 0, 0, 100, 0))
            effect_application = apply_cooking_effects(
                weights,
                (),
                source_rarity=source.rarity,
            )
        else:
            weights = (
                cooking_weights(source.rarity)
                if exclusive_effect_active
                else adjusted_cooking_weights(
                    source.rarity,
                    size_percentile=source.size_percentile,
                    weight_percentile=source.weight_percentile,
                    cookware_level=cookware_level,
                    player_level=probability_level,
                    chef_spice=False,
                    item_id=(
                        applied_item.item_id if applied_item is not None else ""
                    ),
                )
            )
            effect_application = apply_cooking_effects(
                weights,
                active_effects,
                source_rarity=source.rarity,
            )
        weights = effect_application.weights
        six_star_progress_stacks = (
            await self.repository.six_star_progress_stacks(
                session,
                player_id=identity.player_id,
            )
        )
        if six_star_progress_stacks and not (
            exclusive_effect_active or domain_gojo_bypass
        ):
            progressed_weights = apply_six_star_progress(
                weights,
                stacks=six_star_progress_stacks,
                bonus_per_stack=_DANIYA_COOK_BONUS_PER_STACK,
                action="cook",
            )
            if progressed_weights != weights:
                weights = progressed_weights
                effect_application = CookingEffectApplication(
                    weights=weights,
                    consumed_entry_ids=effect_application.consumed_entry_ids,
                    summaries=effect_application.summaries
                    + (
                        f"达妮娅泡泡云冻永久加成：6 星菜概率 "
                        f"+{_DANIYA_COOK_BONUS_PER_STACK * six_star_progress_stacks:g} "
                        f"个百分点（{six_star_progress_stacks} 层）。",
                    ),
                    skipped_summaries=effect_application.skipped_summaries,
                )
        elif six_star_progress_stacks:
            effect_application = replace(
                effect_application,
                skipped_summaries=effect_application.skipped_summaries
                + (
                    "达妮娅泡泡云冻永久概率加成本次受六星菜独占规则影响，未参与结算。",
                ),
            )
        rarity_roll = self.random_source.random()
        output_rarity = choose_rarity(weights, rarity_roll)
        failure_return_effect = None
        if (
            exclusive_effect_active
            and not effect_application.consumed_entry_ids
            and source.rarity == 6
        ):
            failure_return_effect = next(
                (
                    effect
                    for effect in sorted(
                        active_effects,
                        key=lambda candidate: (
                            candidate.created_at,
                            candidate.effect_entry_id,
                        ),
                    )
                    if effect.effect_id == SIX_STAR_COOK_FAILURE_RETURN
                ),
                None,
            )
        consumed_effect_entry_ids = list(
            effect_application.consumed_entry_ids
        )
        cook_effect_summaries = list(effect_application.summaries)
        failure_return_roll: float | None = None
        failure_return_triggered = False
        failure_return_remaining = 0
        if failure_return_effect is not None:
            remaining_before = (
                failure_return_effect.granted_uses
                - failure_return_effect.consumed_uses
            )
            if int(output_rarity) == 6:
                failure_return_remaining = remaining_before
                cook_effect_summaries.append(
                    "彩彩修车猪慕斯：本次成功做出六星菜，"
                    f"保护次数不消耗，仍剩 {remaining_before}/"
                    f"{failure_return_effect.granted_uses} 次。"
                )
            else:
                chance = float(
                    failure_return_effect.params["return_chance_percent"]
                )
                failure_return_roll = self.random_source.random()
                failure_return_triggered = (
                    failure_return_roll < chance / 100.0
                )
                failure_return_remaining = max(0, remaining_before - 1)
                consumed_effect_entry_ids.append(
                    failure_return_effect.effect_entry_id
                )
                result_text = (
                    "返还原料猪成功"
                    if failure_return_triggered
                    else "本次未触发返还"
                )
                cook_effect_summaries.append(
                    f"彩彩修车猪慕斯：六星做菜失败，{result_text}；"
                    f"保护次数剩余 {failure_return_remaining}/"
                    f"{failure_return_effect.granted_uses} 次。"
                )
        templates = await self.repository.list_drawable_food_templates(
            session,
            scope_id=identity.scope.value,
            rarity=int(output_rarity),
        )
        if not templates:
            raise CookingTemplateError(f"当前群没有可用的 {int(output_rarity)} 星美食模板，原料猪未消耗。")
        desired_affinity = source.fat_category
        special_food_roll: float | None = None
        special_template_id = ""
        if source.rarity == 6 and int(output_rarity) == 6:
            paired_template_id = source.paired_food_template_id
            candidates = [candidate for candidate in templates if str(candidate["template_id"]) == paired_template_id]
            if not paired_template_id or not candidates:
                raise CookingTemplateError("这只六星猪没有当前群可用的对应定制六星菜，原料猪未消耗。")
        else:
            paired_template_id = ""
            if int(output_rarity) == 5:
                if source.template_id == KFC_PIG_TEMPLATE_ID:
                    special_food_roll = self.random_source.random()
                    if special_food_roll < 0.50:
                        special_template_id = KFC_FOOD_TEMPLATE_ID
                elif source.template_id == SUKUNA_PIG_TEMPLATE_ID:
                    special_food_roll = self.random_source.random()
                    if special_food_roll < 0.20:
                        special_template_id = SUKUNA_FOOD_TEMPLATE_ID
                elif source.template_id == GOJO_PIG_TEMPLATE_ID:
                    special_food_roll = self.random_source.random()
                    if domain_gojo_bypass:
                        special_template_id = GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS[
                            min(
                                int(
                                    special_food_roll
                                    * len(GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS)
                                ),
                                len(GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS) - 1,
                            )
                        ]
                    elif special_food_roll < 0.20:
                        special_template_id = GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS[
                            0 if special_food_roll < 0.10 else 1
                        ]
            ordinary_templates = [
                candidate
                for candidate in templates
                if str(candidate["template_id"])
                not in SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS
            ]
            if applied_item is not None and applied_item.item_id == "precision-knife":
                desired_affinity = "lean"
            elif applied_item is not None and applied_item.item_id == "slow-cook-seasoning":
                desired_affinity = "fatty"
            if special_template_id:
                candidates = [
                    candidate
                    for candidate in templates
                    if str(candidate["template_id"]) == special_template_id
                ]
                if not candidates:
                    raise CookingTemplateError(
                        "专属菜模板尚未在当前素材目录启用，原料猪未消耗。"
                    )
            else:
                candidates = self._affinity_candidates(
                    ordinary_templates,
                    desired_affinity,
                )
                if not candidates:
                    raise CookingTemplateError(
                        "当前群缺少可用的普通五星菜模板，原料猪未消耗。"
                    )
        template_roll = self.random_source.random()
        template = candidates[min(int(template_roll * len(candidates)), len(candidates) - 1)]
        portion_roll = self.random_source.random()
        main_attributes = generate_food_attributes(
            rarity=output_rarity,
            template_id=str(template["template_id"]),
            source_weight=source.weight_value,
            source_weight_percentile=source.weight_percentile,
            portion_roll=portion_roll,
        )
        output_multiplier = {
            "precision-knife": 1.12,
            "slow-cook-seasoning": 1.18,
            "harvest-apron": 1.25,
        }.get(applied_item.item_id if applied_item is not None else "", 1.0)
        if output_multiplier > 1.0:
            main_attributes = scale_food_attributes(
                main_attributes,
                multiplier=output_multiplier,
            )
        bonus_roll: float | None = None
        bonus_portion_roll: float | None = None
        bonus_attributes: FoodAttributes | None = None
        bonus_serving = False
        if (
            applied_item is not None
            and applied_item.item_id == "large-lunch-box"
            and source.rarity <= 5
            and int(output_rarity) <= 5
        ):
            bonus_roll = self.random_source.random()
            bonus_serving = bonus_roll < 0.45
            if bonus_serving:
                bonus_portion_roll = self.random_source.random()
                bonus_attributes = generate_food_attributes(
                    rarity=output_rarity,
                    template_id=str(template["template_id"]),
                    source_weight=source.weight_value,
                    source_weight_percentile=source.weight_percentile,
                    portion_roll=bonus_portion_roll,
                )
                if output_multiplier > 1.0:
                    bonus_attributes = scale_food_attributes(
                        bonus_attributes,
                        multiplier=output_multiplier,
                    )

        food_specs = [main_attributes]
        if bonus_attributes is not None:
            food_specs.append(bonus_attributes)
        food_ids = [self._new_identifier() for _ in food_specs]
        short_codes: list[str] = []
        for _ in food_specs:
            short_code = await self._new_unique_short_code(
                session,
                reserved=short_codes,
            )
            short_codes.append(short_code)
        if not failure_return_triggered:
            consumed = await self.repository.consume_pig_for_cooking(
                session,
                pig_instance_id=source.pig_instance_id,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                now=now,
            )
            if not consumed:
                raise AssetStateConflictError(
                    "原料猪已不在有效背包中，本次做菜未结算。"
                )
            await self.social_repository.clear_showcase_asset(
                session,
                player_id=identity.player_id,
                asset_kind=AssetKind.PIG,
                asset_instance_id=source.pig_instance_id,
                now=now,
            )

        snapshot_base = {
            "ruleset_version": RULESET_VERSION,
            "source_pig_instance_id": source.pig_instance_id,
            "source_rarity": source.rarity,
            "weights": [round(value, 8) for value in weights],
            "cookware_level": cookware_level,
            "player_level": probability_level,
            "item_id": applied_item.item_id if applied_item is not None else "",
            "rarity_roll": rarity_roll,
            "template_roll": template_roll,
            "desired_affinity": desired_affinity,
            "paired_food_template_id": paired_template_id,
            "special_food_roll": special_food_roll,
            "special_food_template_id": special_template_id,
            "domain_gojo_bypass": domain_gojo_bypass,
            "bonus_roll": bonus_roll,
            "bonus_serving": bonus_serving,
            "food_effect_entry_ids": consumed_effect_entry_ids,
            "food_effect_summaries": cook_effect_summaries,
            "exclusive_effect_active": exclusive_effect_active,
            "failure_return_roll": failure_return_roll,
            "failure_return_triggered": failure_return_triggered,
            "failure_return_remaining": failure_return_remaining,
        }
        catalog_new_count = 0
        for index, (food_id, short_code, attributes) in enumerate(zip(food_ids, short_codes, food_specs, strict=True)):
            random_snapshot = {
                **snapshot_base,
                "serving_index": index,
                "portion_roll": (portion_roll if index == 0 else bonus_portion_roll),
                "recipe_factor": attributes.recipe_factor,
            }
            await self.repository.insert_food_instance(
                session,
                values={
                    "food_instance_id": food_id,
                    "short_code": short_code,
                    "scope_id": identity.scope.value,
                    "owner_player_id": identity.player_id,
                    "template_id": str(template["template_id"]),
                    "template_version": int(template["template_version"]),
                    "source_pig_instance_id": source.pig_instance_id,
                    "rarity": int(output_rarity),
                    "display_name_snapshot": str(template["display_name"]),
                    "portion_weight": attributes.portion_weight,
                    "fat_category": source.fat_category,
                    "official_value": attributes.official_value,
                    "effect_id": str(template.get("effect_id") or ""),
                    "effect_params_json": str(template.get("effect_params_json") or "{}"),
                    "ruleset_version": RULESET_VERSION,
                    "random_snapshot_json": self._snapshot_json(random_snapshot),
                    "acquired_at": now,
                    "updated_at": now,
                },
            )
            catalog_new_count += int(
                await self.repository.upsert_food_catalog(
                    session,
                    player_id=identity.player_id,
                    template_id=str(template["template_id"]),
                    portion_weight=attributes.portion_weight,
                    now=now,
                )
            )

        coin_reward = COOK_COIN_REWARDS[output_rarity]
        experience_reward = COOK_EXPERIENCE_REWARDS[output_rarity]
        coin_balance = await self.repository.apply_currency_change(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            amount=coin_reward,
            reason_code="cook-reward",
            reason_text="做菜奖励",
            source_object_type="food",
            source_object_id=food_ids[0],
            ledger_entry_id=self._new_identifier(),
            idempotency_key=f"{idempotency_key}:coin",
            now=now,
        )
        if coin_balance is None:
            raise RuntimeError("正数做菜奖励无法写入玩家余额。")
        total_experience = await self.repository.add_experience(
            session,
            player_id=identity.player_id,
            experience=experience_reward,
            now=now,
        )
        veteran_reward = await settle_veteran_rewards(
            self.repository,
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            player_level=level_progress(total_experience).level,
            current_balance=coin_balance,
            id_factory=self._new_identifier,
            now=now,
        )
        coin_balance = veteran_reward.balance_after
        await self.social_repository.increment_statistic(
            session,
            player_id=identity.player_id,
            field="total_cooks",
            now=now,
        )
        if applied_item is not None:
            item_consumed = await self.gameplay_repository.consume_armed_item(
                session,
                player_id=identity.player_id,
                action_type="cooking",
                item_id=applied_item.item_id,
                now=now,
            )
            if not item_consumed:
                raise ItemInventoryError(f"已装备的“{applied_item.display_name}”库存不足，本次做菜未结算。")
        if consumed_effect_entry_ids:
            await self.repository.consume_food_effects(
                session,
                player_id=identity.player_id,
                effect_entry_ids=tuple(consumed_effect_entry_ids),
                now=now,
            )
        if domain_gojo_bypass:
            consumed_bypass = await self.technique_repository.consume_permit(
                session,
                player_id=identity.player_id,
                technique_id=TECHNIQUE_DOMAIN_GOJO_BYPASS,
                now=now,
            )
            if not consumed_bypass:
                raise RuntimeError("领域内术式解除资格已被其他结算消耗。")
        foods = tuple([await self._food_by_id(session, food_id) for food_id in food_ids])
        return _CookOutcome(
            source=source,
            foods=foods,
            food_ids=tuple(food_ids),
            coin_reward=coin_reward,
            experience_reward=experience_reward,
            coin_balance=coin_balance,
            total_experience=total_experience,
            catalog_new_count=catalog_new_count,
            cookware_level=cookware_level,
            item_id=applied_item.item_id if applied_item is not None else "",
            item_name=applied_item.display_name if applied_item is not None else "",
            weights=weights,
            bonus_serving=bonus_serving,
            effect_summaries=tuple(cook_effect_summaries),
            excluded_summaries=effect_application.skipped_summaries,
            exclusive_effect_active=exclusive_effect_active,
            item_remaining_uses=(
                max(0, armed_uses - 1) if applied_item is not None else 0
            ),
            effect_entry_ids=tuple(consumed_effect_entry_ids),
            veteran_coin_reward=veteran_reward.coin_reward,
            veteran_reward_levels=veteran_reward.rewarded_levels,
        )

    async def _batch_cook_from_receipt(
        self,
        session: DatabaseSession,
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> BatchCookingResult:
        """Rehydrate one committed batch without touching pigs, items, or effects."""

        if receipt.result_type != "batch-cooking":
            raise ReceiptConflictError("批量做菜回执类型无效。")
        payload = receipt_payload(receipt)
        source_ids = payload.get("source_pig_instance_ids")
        food_ids = payload.get("food_instance_ids")
        if not isinstance(source_ids, list) or not source_ids:
            raise ReceiptConflictError("批量做菜回执缺少原料猪实例。")
        if not isinstance(food_ids, list) or not food_ids:
            raise ReceiptConflictError("批量做菜回执缺少美食实例。")
        source_pigs: list[PigView] = []
        for source_id in source_ids:
            row = await self.gameplay_repository.get_pig_by_instance_id(
                session,
                pig_instance_id=str(source_id),
            )
            if row is None:
                raise ReceiptConflictError("批量做菜回执关联的原料猪不存在。")
            source_pigs.append(pig_view_from_row(row))
        foods = tuple(
            [await self._food_by_id(session, str(food_id)) for food_id in food_ids]
        )
        item_summaries = payload.get("item_use_summaries", [])
        effect_summaries = payload.get("effect_use_summaries", [])
        if not isinstance(item_summaries, list) or not isinstance(
            effect_summaries,
            list,
        ):
            raise ReceiptConflictError("批量做菜回执中的效果摘要无效。")
        return BatchCookingResult(
            source_pigs=tuple(source_pigs),
            foods=foods,
            pig_count=int(payload["pig_count"]),
            food_count=int(payload["food_count"]),
            coin_reward=int(payload["coin_reward"]),
            experience_reward=int(payload["experience_reward"]),
            coin_balance=int(payload["coin_balance"]),
            total_experience=int(payload["total_experience"]),
            catalog_new_count=int(payload["catalog_new_count"]),
            rarity=(
                int(payload["rarity"])
                if payload.get("rarity") is not None
                else None
            ),
            item_use_summaries=tuple(str(value) for value in item_summaries),
            effect_use_summaries=tuple(str(value) for value in effect_summaries),
            veteran_coin_reward=int(payload.get("veteran_coin_reward") or 0),
            veteran_reward_levels=tuple(
                int(value)
                for value in payload.get("veteran_reward_levels", [])
            ),
            receipt=receipt,
            receipt_created=receipt_created,
        )

    async def food_detail(
        self,
        identity: CommandIdentity,
        selector_text: str,
    ) -> FoodView:
        """Resolve one active food owned by the current-group player."""

        parse_asset_selector(selector_text)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            return await self._resolve_food(session, identity, selector_text)

    async def food_inventory(
        self,
        identity: CommandIdentity,
        *,
        page: int,
        rarity: int | None,
        sort: str,
    ) -> FoodInventoryPage:
        """Read one active-food inventory page."""

        page_size = self.cooking.inventory_page_size
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            total, rows = await self.repository.food_inventory_page(
                session,
                player_id=identity.player_id,
                rarity=rarity,
                sort=sort,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
        pages = valid_page_count(page, total, page_size)
        return FoodInventoryPage(
            display_name=identity.display_name,
            page=page,
            page_count=pages,
            total_count=total,
            page_size=page_size,
            rarity=rarity,
            sort=sort,
            foods=tuple(food_view_from_row(row) for row in rows),
        )

    async def food_catalog(
        self,
        identity: CommandIdentity,
        *,
        rarity: int | None,
        undiscovered_only: bool,
    ) -> FoodCatalogPage:
        """Read every visible food catalog slot with privacy masking."""

        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            collected, visible_total = await self.repository.visible_food_catalog_counts(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
            )
            total, rows = await self.repository.food_catalog_entries(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                rarity=rarity,
                undiscovered_only=undiscovered_only,
            )
        return FoodCatalogPage(
            display_name=identity.display_name,
            total_count=total,
            rarity=rarity,
            undiscovered_only=undiscovered_only,
            collected_count=collected,
            visible_catalog_total=visible_total,
            entries=tuple(_catalog_entry_from_row(row) for row in rows),
        )

    async def eat(self, identity: CommandIdentity, selector_text: str) -> EatResult:
        """Consume one food after validating its registered effect."""

        await self._expire_stale_offers()
        normalized_selector = str(selector_text or "").strip()
        if normalized_selector:
            selector = parse_asset_selector(normalized_selector)
            request_payload = {
                "command_version": 2,
                "selection": "exact",
                "name": selector.name,
                "short_code": selector.short_code or "",
            }
        else:
            request_payload = {
                "command_version": 2,
                "selection": "cheapest-low-rarity",
            }
        idempotency_key = MessageKeyFactory.build(identity, _EAT_COMMAND)
        now_datetime = self.clock.now()
        now = iso_timestamp(now_datetime)
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_EAT_COMMAND,
                    request_payload=request_payload,
                )
                return await self._eat_from_receipt(
                    session,
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            food = await self._resolve_food_for_action(
                session,
                identity,
                normalized_selector,
            )
            effect = self._food_effect(food)
            effect_expires_at = effect.expires_at
            if effect.queued_effect_id in GROUP_EFFECT_IDS:
                effect_expires_at = self._next_same_window_effect_expiry(
                    now_datetime
                )
            elif effect.queued_effect_id in {
                CURRENT_WINDOW_CATCHES,
                TODAY_WINDOW_CATCHES,
            }:
                active_rows = await self.repository.list_active_food_effects(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
                if any(str(row.get("effect_id") or "") == effect.queued_effect_id for row in active_rows):
                    period = "本抓猪时段" if effect.queued_effect_id == CURRENT_WINDOW_CATCHES else "今天"
                    raise FoodEffectError(f"{period}的同类额度菜已经生效，不能重复叠加；美食未消耗。")
                if effect.queued_effect_id == CURRENT_WINDOW_CATCHES:
                    window = catch_quota_window(
                        now_datetime,
                        refresh_hours=self.quota_refresh_hours,
                        timezone_name=self.quota_timezone_name,
                    )
                    effect_expires_at = iso_timestamp(window.end)
                else:
                    effect_expires_at = self._daily_effect_expiry(now_datetime)
            elif (
                effect.queued_effect_id == NEXT_HIGH_STAR_CATCH
                and bool(effect.queued_effect_params.get("current_window_only"))
            ):
                window = catch_quota_window(
                    now_datetime,
                    refresh_hours=self.quota_refresh_hours,
                    timezone_name=self.quota_timezone_name,
                )
                effect_expires_at = iso_timestamp(window.end)
            elif effect.queued_effect_id == WEEKLY_WINDOW_CATCHES:
                effect_expires_at = self._rolling_seven_day_effect_expiry(now_datetime)
                granted = await self.repository.grant_weekly_catch_bonus(
                    session,
                    player_id=identity.player_id,
                    source_food_instance_id=food.food_instance_id,
                    count=int(effect.queued_effect_params["count"]),
                    expires_at=effect_expires_at,
                    now=now,
                )
                if not granted:
                    raise FoodEffectError("滚动 7 天全时段抓猪额度加成已经生效，不能重复叠加；美食未消耗。")
            elif effect.queued_effect_id == PERMANENT_WINDOW_CATCH:
                permanent_total = await self.repository.increment_permanent_catch_bonus(
                    session,
                    player_id=identity.player_id,
                    source_food_instance_id=food.food_instance_id,
                    count=int(effect.queued_effect_params["count"]),
                    max_bonus=int(effect.queued_effect_params["max_bonus"]),
                    now=now,
                )
                if permanent_total is None:
                    raise FoodEffectError("永久抓猪时段额度已经达到 +5 上限；美食未消耗。")
            elif effect.queued_effect_id == PERMANENT_SIX_STAR_PROGRESS:
                progress_total = await self.repository.increment_six_star_progress(
                    session,
                    player_id=identity.player_id,
                    source_food_instance_id=food.food_instance_id,
                    max_stacks=int(effect.queued_effect_params["max_stacks"]),
                    now=now,
                )
                if progress_total is None:
                    raise FoodEffectError(
                        "六星概率永久加成已经达到累计上限；美食未消耗。"
                    )
            elif effect.queued_effect_id == NEXT_STACKABLE_SIX_STAR_COOK_BONUS:
                active_rows = await self.repository.list_active_food_effects(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
                active_layers = sum(
                    int(row["granted_uses"]) - int(row["consumed_uses"])
                    for row in active_rows
                    if str(row.get("effect_id") or "") == NEXT_STACKABLE_SIX_STAR_COOK_BONUS
                )
                max_stacks = int(effect.queued_effect_params["max_stacks"])
                if active_layers >= max_stacks:
                    raise FoodEffectError(
                        f"猪饺的六星菜概率加成已经叠加 {max_stacks} 层；"
                        "请先用 6 星猪做菜后再食用，美食未消耗。"
                    )
            consumed = await self.repository.consume_food(
                session,
                food_instance_id=food.food_instance_id,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                now=now,
            )
            if not consumed:
                raise AssetStateConflictError("美食已不在有效背包中，本次品鉴未结算。")
            await self.social_repository.clear_showcase_asset(
                session,
                player_id=identity.player_id,
                asset_kind=AssetKind.FOOD,
                asset_instance_id=food.food_instance_id,
                now=now,
            )
            effect_entry_id = ""
            personal_effect_entry_id = ""
            roulette_available_spins = 0
            available_effect_uses = 0
            if effect.queued_effect_id and effect.queued_effect_id not in {
                WEEKLY_WINDOW_CATCHES,
                PERMANENT_WINDOW_CATCH,
                PERMANENT_SIX_STAR_PROGRESS,
                GROUP_COIN_TRIBUTE,
                ROULETTE_CHANCES,
                TECHNIQUE_PERMIT,
                *GROUP_EFFECT_IDS,
            }:
                effect_entry_id = self._new_identifier()
                if effect.queued_effect_id == EXTRA_CATCHES:
                    effect_expires_at = self._daily_effect_expiry(now_datetime)
                await self.repository.insert_food_effect(
                    session,
                    effect_entry_id=effect_entry_id,
                    player_id=identity.player_id,
                    source_food_instance_id=food.food_instance_id,
                    effect_id=effect.queued_effect_id,
                    params_json=json.dumps(
                        dict(effect.queued_effect_params),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    granted_uses=effect.granted_uses,
                    expires_at=effect_expires_at or None,
                    now=now,
                )
            elif effect.queued_effect_id in GROUP_EFFECT_IDS:
                effect_entry_id = self._new_identifier()
                granted_uses_per_player = (
                    int(effect.queued_effect_params["uses_per_player"])
                    if effect.queued_effect_id
                    == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH
                    else int(effect.queued_effect_params["dedicated_catches"])
                )
                await self.repository.insert_group_food_effect(
                    session,
                    group_effect_entry_id=effect_entry_id,
                    scope_id=identity.scope.value,
                    source_player_id=identity.player_id,
                    source_food_instance_id=food.food_instance_id,
                    effect_id=effect.queued_effect_id,
                    params_json=json.dumps(
                        dict(effect.queued_effect_params),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    granted_uses_per_player=granted_uses_per_player,
                    starts_at=now,
                    expires_at=effect_expires_at,
                    now=now,
                )
                personal_cook_uses = int(
                    effect.queued_effect_params.get("personal_six_star_cook_uses")
                    or 0
                )
                if personal_cook_uses:
                    personal_effect_entry_id = self._new_identifier()
                    personal_cook_params = {
                        "six_star_percent": float(
                            effect.queued_effect_params[
                                "personal_six_star_cook_percent"
                            ]
                        ),
                        "uses": personal_cook_uses,
                    }
                    personal_grant = resolve_food_effect(
                        NEXT_SIX_STAR_COOK,
                        personal_cook_params,
                    )
                    await self.repository.insert_food_effect(
                        session,
                        effect_entry_id=personal_effect_entry_id,
                        player_id=identity.player_id,
                        source_food_instance_id=food.food_instance_id,
                        effect_id=personal_grant.effect_id,
                        params_json=json.dumps(
                            personal_grant.params,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        granted_uses=personal_grant.granted_uses,
                        expires_at=None,
                        now=now,
                    )
            elif effect.queued_effect_id == ROULETTE_CHANCES:
                roulette_available_spins = await self.repository.grant_roulette_spins(
                    session,
                    player_id=identity.player_id,
                    source_food_instance_id=food.food_instance_id,
                    count=int(effect.queued_effect_params["count"]),
                    now=now,
                )
                available_effect_uses = roulette_available_spins
                effect = replace(
                    effect,
                    summary=(
                        f"{effect.summary} 当前未使用机会共 "
                        f"{roulette_available_spins} 次。"
                    ),
                )
            elif effect.queued_effect_id == TECHNIQUE_PERMIT:
                technique_id = str(
                    effect.queued_effect_params.get("technique_id") or ""
                )
                available = await self.technique_repository.grant_permit(
                    session,
                    player_id=identity.player_id,
                    technique_id=technique_id,
                    uses=1,
                    now=now,
                )
                available_effect_uses = available
                effect = replace(
                    effect,
                    summary=(
                        f"{effect.summary} 当前未发动资格共 {available} 次。"
                    ),
                )
            base_experience = EAT_EXPERIENCE_REWARDS[Rarity(food.rarity)]
            total_experience = await self.repository.add_experience(
                session,
                player_id=identity.player_id,
                experience=base_experience + effect.experience_bonus,
                now=now,
            )
            group_rewarded_players = 0
            group_coin_total = 0
            if effect.queued_effect_id in GROUP_EFFECT_IDS:
                coin_balance, group_rewarded_players = (
                    await self._apply_group_food_coin_rewards(
                        session,
                        identity=identity,
                        food=food,
                        effect=effect,
                        idempotency_key=idempotency_key,
                        now=now,
                    )
                )
            elif effect.queued_effect_id == GROUP_COIN_TRIBUTE:
                (
                    coin_balance,
                    group_rewarded_players,
                    group_coin_total,
                ) = await self._apply_group_coin_tribute(
                    session,
                    identity=identity,
                    food=food,
                    coin_per_player=int(
                        effect.queued_effect_params["coin_per_player"]
                    ),
                    idempotency_key=idempotency_key,
                    now=now,
                )
            elif effect.coin_bonus:
                coin_balance = await self.repository.apply_currency_change(
                    session,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                    amount=effect.coin_bonus,
                    reason_code="food-effect",
                    reason_text=f"食用{food.display_name}效果",
                    source_object_type="food",
                    source_object_id=food.food_instance_id,
                    ledger_entry_id=self._new_identifier(),
                    idempotency_key=f"{idempotency_key}:coin",
                    now=now,
                )
                if coin_balance is None:
                    raise RuntimeError("美食正数效果无法写入玩家余额。")
            else:
                row = await self.repository.economy_profile_row(
                    session,
                    player_id=identity.player_id,
                )
                if row is None:
                    raise RuntimeError("品鉴后无法读取玩家余额。")
                coin_balance = int(row["coin_balance"])
            veteran_reward = await settle_veteran_rewards(
                self.repository,
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                player_level=level_progress(total_experience).level,
                current_balance=coin_balance,
                id_factory=self._new_identifier,
                now=now,
            )
            coin_balance = veteran_reward.balance_after
            payload = {
                "food_instance_id": food.food_instance_id,
                "base_experience": base_experience,
                "effect_summary": effect.summary,
                "effect_experience_bonus": effect.experience_bonus,
                "effect_coin_bonus": effect.coin_bonus,
                "effect_entry_id": effect_entry_id,
                "personal_effect_entry_id": personal_effect_entry_id,
                "queued_effect_id": effect.queued_effect_id,
                "queued_effect_params": dict(effect.queued_effect_params),
                "effect_granted_uses": effect.granted_uses,
                "effect_expires_at": effect_expires_at,
                "roulette_available_spins": roulette_available_spins,
                "available_effect_uses": available_effect_uses,
                "group_rewarded_players": group_rewarded_players,
                "group_coin_total": group_coin_total,
                "total_experience": total_experience,
                "coin_balance": coin_balance,
                "veteran_coin_reward": veteran_reward.coin_reward,
                "veteran_reward_levels": list(veteran_reward.rewarded_levels),
            }
            provisional = EatResult(
                food=food,
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=_EAT_COMMAND,
                    request_payload=request_payload,
                    result_type="food-consumed",
                    result_object_id=food.food_instance_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                base_experience=base_experience,
                effect=effect,
                total_experience=total_experience,
                coin_balance=coin_balance,
                group_rewarded_players=group_rewarded_players,
                group_coin_total=group_coin_total,
                available_effect_uses=available_effect_uses,
                veteran_coin_reward=veteran_reward.coin_reward,
                veteran_reward_levels=veteran_reward.rewarded_levels,
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=_EAT_COMMAND,
                request_payload=request_payload,
                result_type="food-consumed",
                result_object_id=food.food_instance_id,
                result_payload=payload,
                text_summary=(
                    format_group_event_eat_summary(provisional)
                    if is_group_event_food(provisional)
                    else format_eat_summary(provisional)
                ),
                now=now,
            )
            return EatResult(
                food=food,
                receipt=receipt,
                receipt_created=True,
                base_experience=base_experience,
                effect=effect,
                total_experience=total_experience,
                coin_balance=coin_balance,
                group_rewarded_players=group_rewarded_players,
                group_coin_total=group_coin_total,
                available_effect_uses=available_effect_uses,
                veteran_coin_reward=veteran_reward.coin_reward,
                veteran_reward_levels=veteran_reward.rewarded_levels,
            )

    async def spin_roulette(self, identity: CommandIdentity) -> RouletteResult:
        """Consume one durable roulette chance and settle one uniform outcome."""

        request_payload = {"command_version": 1}
        idempotency_key = MessageKeyFactory.build(identity, _ROULETTE_COMMAND)
        now_datetime = _safe_datetime(self.clock.now())
        now = iso_timestamp(now_datetime)
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_ROULETTE_COMMAND,
                    request_payload=request_payload,
                )
                return self._roulette_from_receipt(
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            state = await self.repository.roulette_state(
                session,
                player_id=identity.player_id,
            )
            if state is None or int(state["available_spins"]) <= 0:
                raise FoodEffectError(
                    "没有可用的猪保千猪排轮盘机会；请先食用猪保千猪排轮盘。"
                )
            source_food_instance_id = str(state["source_food_instance_id"])
            remaining_spins = await self.repository.consume_roulette_spin(
                session,
                player_id=identity.player_id,
                now=now,
            )
            outcome_roll = self.random_source.random()
            outcome = min(int(outcome_roll * 6), 5) + 1
            outcome_summary = ""

            if outcome == 1:
                coin_balance = await self.repository.apply_currency_change(
                    session,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                    amount=10_000,
                    reason_code="roulette-reward",
                    reason_text="猪保千猪排轮盘奖励",
                    source_object_type="food",
                    source_object_id=source_food_instance_id,
                    ledger_entry_id=self._new_identifier(),
                    idempotency_key=f"{idempotency_key}:coin",
                    now=now,
                )
                if coin_balance is None:
                    raise RuntimeError("轮盘猪币奖励无法写入玩家余额。")
                outcome_summary = "获得 10000 猪币。"
            else:
                profile = await self.repository.economy_profile_row(
                    session,
                    player_id=identity.player_id,
                )
                if profile is None:
                    raise RuntimeError("轮盘结算后无法读取玩家余额。")
                coin_balance = int(profile["coin_balance"])

            if outcome == 2:
                remaining_spins = await self.repository.grant_roulette_spins(
                    session,
                    player_id=identity.player_id,
                    source_food_instance_id=source_food_instance_id,
                    count=2,
                    now=now,
                )
                outcome_summary = "额外获得 2 次转轮盘机会。"
            elif outcome in {3, 4, 5, 6}:
                if outcome == 3:
                    grant = resolve_food_effect(
                        NEXT_SIX_STAR_COOK_BONUS,
                        {"bonus_percent": 30},
                    )
                    expires_at = None
                elif outcome == 4:
                    grant = resolve_food_effect(
                        ROLLING_DAY_WINDOW_CATCHES,
                        {"count": 4},
                    )
                    expires_at = self._next_same_window_effect_expiry(
                        now_datetime
                    )
                elif outcome == 5:
                    grant = resolve_food_effect(
                        EVEN_CATCH_DISTRIBUTION,
                        {"uses": 5},
                    )
                    expires_at = None
                else:
                    grant = resolve_food_effect(
                        NEXT_GUARANTEED_SIX_STAR_CATCH,
                        {},
                    )
                    expires_at = None
                await self.repository.insert_food_effect(
                    session,
                    effect_entry_id=self._new_identifier(),
                    player_id=identity.player_id,
                    source_food_instance_id=source_food_instance_id,
                    effect_id=grant.effect_id,
                    params_json=json.dumps(
                        grant.params,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    granted_uses=grant.granted_uses,
                    expires_at=expires_at,
                    now=now,
                )
                outcome_summary = grant.summary

            payload = {
                "outcome_roll": outcome_roll,
                "outcome": outcome,
                "outcome_summary": outcome_summary,
                "remaining_spins": remaining_spins,
                "coin_balance": coin_balance,
            }
            provisional = RouletteResult(
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=_ROULETTE_COMMAND,
                    request_payload=request_payload,
                    result_type="roulette-spin",
                    result_object_id=source_food_instance_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                outcome=outcome,
                outcome_summary=outcome_summary,
                remaining_spins=remaining_spins,
                coin_balance=coin_balance,
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=_ROULETTE_COMMAND,
                request_payload=request_payload,
                result_type="roulette-spin",
                result_object_id=source_food_instance_id,
                result_payload=payload,
                text_summary=format_roulette_summary(provisional),
                now=now,
            )
            return replace(
                provisional,
                receipt=receipt,
            )

    async def eat_or_confirm(
        self,
        identity: CommandIdentity,
        selector_text: str,
    ) -> EatResult | EatConfirmationRequest:
        """Quick-eat the cheapest same-name copy, protecting the last copy."""

        normalized_selector = str(selector_text or "").strip()
        if not normalized_selector:
            return await self.eat(identity, normalized_selector)
        selector = parse_asset_selector(normalized_selector)
        if selector.short_code is not None:
            return await self.eat(identity, normalized_selector)

        idempotency_key = MessageKeyFactory.build(identity, _EAT_COMMAND)
        now_datetime = _safe_datetime(self.clock.now())
        now = iso_timestamp(now_datetime)
        selected: FoodView | None = None
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                return await self._eat_from_receipt(
                    session,
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            rows = await self.repository.find_active_foods(
                session,
                player_id=identity.player_id,
                selector=selector,
            )
            if not rows:
                raise FoodNotFoundError(
                    f"你的美食背包中找不到“{normalized_selector}”。"
                )
            eligible = [row for row in rows if not bool(row.get("is_favorite") or False)]
            if not eligible:
                raise AssetStateConflictError(
                    f"“{selector.name}”的全部实例都已收藏保护，请先取消收藏。"
                )
            selected = food_view_from_row(eligible[0])
            if len(eligible) == 1:
                expires_at = iso_timestamp(now_datetime + timedelta(seconds=30))
                await self.repository.upsert_pending_food_confirmation(
                    session,
                    player_id=identity.player_id,
                    food_instance_id=selected.food_instance_id,
                    requested_name=selector.name,
                    expires_at=expires_at,
                    now=now,
                )
                return EatConfirmationRequest(
                    food=selected,
                    expires_at=expires_at,
                )
        assert selected is not None
        return await self.eat(identity, selected.selector)

    @staticmethod
    def _roulette_from_receipt(
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> RouletteResult:
        payload = receipt_payload(receipt)
        return RouletteResult(
            receipt=receipt,
            receipt_created=receipt_created,
            outcome=int(payload["outcome"]),
            outcome_summary=str(payload["outcome_summary"]),
            remaining_spins=int(payload["remaining_spins"]),
            coin_balance=int(payload["coin_balance"]),
        )

    async def confirm_eat(
        self,
        identity: CommandIdentity,
        *,
        accepted: bool,
    ) -> EatResult | str:
        """Resolve the current player's pending last-copy food confirmation."""

        idempotency_key = MessageKeyFactory.build(identity, _EAT_COMMAND)
        now = iso_timestamp(self.clock.now())
        selected_selector = ""
        selected_instance_id = ""
        selected_name = ""
        async with self.database.transaction() as session:
            if accepted:
                existing = await self.receipt_repository.get_by_key(
                    session,
                    idempotency_key,
                )
                if existing is not None:
                    return await self._eat_from_receipt(
                        session,
                        existing,
                        receipt_created=False,
                    )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            pending = await self.repository.get_pending_food_confirmation(
                session,
                player_id=identity.player_id,
            )
            if pending is None:
                return "当前没有待确认的最后一道同名菜。"
            selected_instance_id = str(pending["food_instance_id"])
            selected_name = str(pending["display_name_snapshot"])
            if str(pending["expires_at"]) <= now:
                await self.repository.delete_pending_food_confirmation(
                    session,
                    player_id=identity.player_id,
                )
                return "吃菜确认已超过 30 秒并自动退出，请重新发送 /吃菜 菜名。"
            if not accepted:
                await self.repository.delete_pending_food_confirmation(
                    session,
                    player_id=identity.player_id,
                )
                return f"已取消食用最后一份“{selected_name}”。"
            if str(pending["state"]) != "active" or pending["locked_trade_id"] is not None:
                await self.repository.delete_pending_food_confirmation(
                    session,
                    player_id=identity.player_id,
                )
                return "待确认美食已不在可用背包中，本次确认已退出。"
            selected_selector = (
                f"{selected_name}#{str(pending['short_code'])}"
            )

        result = await self.eat(identity, selected_selector)
        async with self.database.transaction() as session:
            await self.repository.delete_pending_food_confirmation(
                session,
                player_id=identity.player_id,
                food_instance_id=selected_instance_id,
            )
        return result

    async def store(
        self,
        identity: CommandIdentity,
        *,
        page: int,
        category: str,
    ) -> StorePage:
        """Read all current store products on one page."""

        if category not in _STORE_CATEGORIES:
            raise StoreProductError("商城分类只能是：全部、抓猪、做菜、升级。")
        if page != 1:
            raise StoreProductError("猪猪商城已改为单页展示，不需要填写页码。")
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            upgrades = await self.repository.get_upgrade_levels(
                session,
                player_id=identity.player_id,
            )
            profile = await self.repository.economy_profile_row(
                session,
                player_id=identity.player_id,
            )
            if profile is None:
                raise RuntimeError("商城无法读取玩家余额。")
        products = build_store_products(
            feed_level=upgrades["feed"],
            cookware_level=upgrades["cookware"],
            feed_prices=self.economy.feed_upgrade_prices,
            cookware_prices=self.economy.cookware_upgrade_prices,
        )
        resolved = _STORE_CATEGORIES[category]
        filtered = tuple(product for product in products if resolved is None or product.category == resolved)
        page_size = max(1, len(filtered))
        return StorePage(
            display_name=identity.display_name,
            coin_balance=int(profile["coin_balance"]),
            page=page,
            page_count=1,
            total_count=len(filtered),
            page_size=page_size,
            category=category,
            feed_level=upgrades["feed"],
            cookware_level=upgrades["cookware"],
            products=filtered,
            catch_base_weights=self.catch_base_weights,
        )

    async def purchase(
        self,
        identity: CommandIdentity,
        product_name: str,
        *,
        quantity: int,
    ) -> PurchaseResult:
        """Buy consumable items; permanent upgrades use :meth:`upgrade`."""

        if upgrade_type_by_name(product_name) is not None:
            raise StoreProductError("永久升级请使用 /升级 猪饲料 或 /升级 厨具。")
        return await self._purchase_product(
            identity,
            product_name,
            quantity=quantity,
            command_name=_PURCHASE_COMMAND,
        )

    async def upgrade(
        self,
        identity: CommandIdentity,
        upgrade_name: str,
    ) -> PurchaseResult:
        """Buy exactly one permanent feed or cookware level."""

        if upgrade_type_by_name(upgrade_name) is None:
            raise StoreProductError("升级名称只能填写“猪饲料”或“厨具”。")
        return await self._purchase_product(
            identity,
            upgrade_name,
            quantity=1,
            command_name=_UPGRADE_COMMAND,
        )

    async def _purchase_product(
        self,
        identity: CommandIdentity,
        product_name: str,
        *,
        quantity: int,
        command_name: str,
    ) -> PurchaseResult:
        """Atomically debit coins and grant one validated store product."""

        normalized_name = str(product_name or "").strip()
        if not normalized_name:
            raise StoreProductError("请填写要购买的商品名称。")
        if quantity < 1 or quantity > self.economy.max_purchase_quantity:
            raise StoreProductError(f"购买数量必须位于 1 至 {self.economy.max_purchase_quantity}。")
        upgrade_type = upgrade_type_by_name(normalized_name)
        if upgrade_type is not None and quantity != 1:
            raise StoreProductError("永久升级每次只能购买一级，数量必须为 1。")
        if upgrade_type is None:
            item = item_product_by_name(normalized_name)
            product_id = item.item_id
        else:
            item = None
            product_id = f"upgrade-{upgrade_type.value}"
        request_payload = {
            "command_version": 1,
            "product_id": product_id,
            "quantity": quantity,
        }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
                return self._purchase_from_receipt(existing, receipt_created=False)
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            upgrades = await self.repository.get_upgrade_levels(
                session,
                player_id=identity.player_id,
            )
            if upgrade_type is None:
                assert item is not None
                display_name = item.display_name
                product_type = "item"
                unit_price = item.price
                upgrade_level = 0
                upgrade_type_value = ""
            else:
                current_level = upgrades[upgrade_type.value]
                if current_level >= 5:
                    raise UpgradeLimitError(f"{'猪饲料' if upgrade_type is UpgradeType.FEED else '厨具'}已达到 Lv.5。")
                prices = (
                    self.economy.feed_upgrade_prices
                    if upgrade_type is UpgradeType.FEED
                    else self.economy.cookware_upgrade_prices
                )
                display_name = "猪饲料升级" if upgrade_type is UpgradeType.FEED else "厨具升级"
                product_type = "upgrade"
                unit_price = prices[current_level]
                upgrade_level = current_level + 1
                upgrade_type_value = upgrade_type.value
            total_price = unit_price * quantity
            balance_after = await self.repository.apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=-total_price,
                reason_code="store-purchase",
                reason_text=f"购买{display_name}×{quantity}",
                source_object_type="store-product",
                source_object_id=product_id,
                ledger_entry_id=self._new_identifier(),
                idempotency_key=f"{idempotency_key}:coin",
                now=now,
            )
            if balance_after is None:
                raise InsufficientBalanceError(f"购买需要 {total_price} 猪币，当前余额不足。")
            inventory_quantity = 0
            if upgrade_type is None:
                assert item is not None
                inventory_quantity = await self.repository.add_item_inventory(
                    session,
                    player_id=identity.player_id,
                    item_id=item.item_id,
                    quantity=quantity,
                    now=now,
                )
            else:
                changed = await self.repository.set_upgrade_level(
                    session,
                    player_id=identity.player_id,
                    upgrade_type=upgrade_type.value,
                    expected_level=upgrade_level - 1,
                    target_level=upgrade_level,
                    now=now,
                )
                if not changed:
                    raise AssetStateConflictError("升级等级已发生变化，请重新查看商城。")
            payload = {
                "product_id": product_id,
                "display_name": display_name,
                "product_type": product_type,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_price": total_price,
                "balance_after": balance_after,
                "inventory_quantity": inventory_quantity,
                "upgrade_type": upgrade_type_value,
                "upgrade_level": upgrade_level,
            }
            provisional = self._purchase_result(
                self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                    result_type="purchase",
                    result_object_id=product_id,
                    result_payload=payload,
                    now=now,
                ),
                payload,
                receipt_created=True,
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="purchase",
                result_object_id=product_id,
                result_payload=payload,
                text_summary=format_purchase_summary(provisional),
                now=now,
            )
            return self._purchase_result(receipt, payload, receipt_created=True)

    async def sell_pig(
        self,
        identity: CommandIdentity,
        selector_text: str,
    ) -> SaleResult:
        """Sell one active pig to the official store."""

        return await self._sell(
            identity,
            selector_text,
            asset_kind="pig",
            command_name=_SELL_PIG_COMMAND,
        )

    async def sell_food(
        self,
        identity: CommandIdentity,
        selector_text: str,
    ) -> SaleResult:
        """Sell one active food to the official store."""

        return await self._sell(
            identity,
            selector_text,
            asset_kind="food",
            command_name=_SELL_FOOD_COMMAND,
        )

    async def batch_sell_low_rarity(
        self,
        identity: CommandIdentity,
        *,
        asset_kind: str,
        max_rarity: int = 3,
        rarity: int | None = None,
        display_name: str = "",
    ) -> BatchSaleResult:
        """Sell every unlocked low-rarity pig or food in one transaction.

        ``rarity`` 指定时只处理该品质；不指定时处理 ``1..max_rarity``。
        联动猪（有收藏图鉴条目）按模板保留价值最高的一只，其余重复实例可批量售卖。
        """

        if asset_kind not in {"pig", "food"}:
            raise StoreProductError("批量售卖类型只能是“猪猪”或“美食”。")
        if rarity is not None and not 1 <= int(rarity) <= 5:
            raise StoreProductError("批量售卖指定品质只支持一星至五星。")
        normalized_name = str(display_name or "").strip()
        if rarity is None and not 1 <= int(max_rarity) <= 3:
            if not (asset_kind == "food" and normalized_name and int(max_rarity) == 5):
                raise StoreProductError("批量售卖只允许处理 1 至 3 星资产。")
        if normalized_name and asset_kind != "food":
            raise StoreProductError("按名称批量售卖目前只支持美食。")
        await self._expire_stale_offers()
        command_name = _BATCH_SELL_PIG_COMMAND if asset_kind == "pig" else _BATCH_SELL_FOOD_COMMAND
        request_payload = {
            "command_version": 1,
            "asset_kind": asset_kind,
            "max_rarity": int(max_rarity),
            "rarity": rarity,
        }
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
                return self._batch_sale_from_receipt(
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            keep_highest = await self.gameplay_repository.batch_keep_highest(
                session,
                player_id=identity.player_id,
            )
            count, total_value = await self.repository.batch_sell_low_rarity(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                asset_kind=asset_kind,
                max_rarity=int(max_rarity),
                rarity=rarity,
                keep_highest=keep_highest,
                now=now,
                display_name=normalized_name,
            )
            if count == 0:
                noun = "猪猪" if asset_kind == "pig" else "美食"
                if rarity is not None:
                    error = f"背包中没有可批量售卖的 {rarity} 星{noun}；联动猪与交易锁定资产不会被处理。"
                elif normalized_name:
                    error = f"背包中没有可批量售卖的“{normalized_name}”；收藏保护与交易锁定资产不会被处理。"
                else:
                    error = f"背包中没有可批量售卖的 1 至 {max_rarity} 星{noun}；联动猪与交易锁定资产不会被处理。"
                if asset_kind == "pig":
                    raise PigNotFoundError(error)
                raise FoodNotFoundError(error)
            balance_after = await self.repository.apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=total_value,
                reason_code=f"batch-sell-{asset_kind}",
                reason_text=(
                    f"批量售卖同名美食{normalized_name}"
                    if normalized_name
                    else "批量售卖低星" + ("猪猪" if asset_kind == "pig" else "美食")
                ),
                source_object_type=asset_kind,
                source_object_id=(
                    f"name-{normalized_name}"
                    if normalized_name
                    else (
                        f"rarity-{rarity}"
                        if rarity is not None
                        else f"rarity-1-{max_rarity}"
                    )
                ),
                ledger_entry_id=self._new_identifier(),
                idempotency_key=f"{idempotency_key}:coin",
                now=now,
            )
            if balance_after is None:
                raise RuntimeError("批量售卖正数收益无法写入玩家余额。")
            payload = {
                "asset_kind": asset_kind,
                "asset_count": count,
                "max_rarity": int(max_rarity),
                "rarity": rarity,
                "display_name": normalized_name,
                "total_value": total_value,
                "balance_after": balance_after,
            }
            provisional = BatchSaleResult(
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                    result_type="batch-sale",
                    result_object_id=(
                        f"{asset_kind}:rarity-{rarity}" if rarity is not None else f"{asset_kind}:rarity-1-{max_rarity}"
                    ),
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                asset_kind=asset_kind,
                asset_count=count,
                max_rarity=int(max_rarity),
                total_value=total_value,
                balance_after=balance_after,
                rarity=rarity,
                display_name=normalized_name,
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="batch-sale",
                result_object_id=(
                    f"{asset_kind}:rarity-{rarity}" if rarity is not None else f"{asset_kind}:rarity-1-{max_rarity}"
                ),
                result_payload=payload,
                text_summary=format_batch_sale_summary(provisional),
                now=now,
            )
            return BatchSaleResult(
                receipt=receipt,
                receipt_created=True,
                asset_kind=asset_kind,
                asset_count=count,
                max_rarity=int(max_rarity),
                total_value=total_value,
                balance_after=balance_after,
                rarity=rarity,
                display_name=normalized_name,
            )

    async def ledger(self, identity: CommandIdentity, *, page: int) -> LedgerPage:
        """Read the immutable current-group ledger after reconciling it."""

        page_size = self.economy.ledger_page_size
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            balance, ledger_total = await self.repository.balance_reconciliation(
                session,
                player_id=identity.player_id,
            )
            if balance != ledger_total:
                raise LedgerReconciliationError(
                    f"猪币对账异常：余额 {balance}，流水合计 {ledger_total}。请停止交易并联系插件管理员。"
                )
            total, rows = await self.repository.ledger_page(
                session,
                player_id=identity.player_id,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
        pages = valid_page_count(page, total, page_size)
        return LedgerPage(
            display_name=identity.display_name,
            page=page,
            page_count=pages,
            total_count=total,
            page_size=page_size,
            coin_balance=balance,
            ledger_total=ledger_total,
            entries=tuple(
                LedgerEntry(
                    ledger_entry_id=str(row["ledger_entry_id"]),
                    amount=int(row["amount"]),
                    balance_after=int(row["balance_after"]),
                    reason_code=str(row["reason_code"]),
                    reason_text=str(row["reason_text"]),
                    source_object_type=str(row["source_object_type"]),
                    source_object_id=str(row["source_object_id"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            ),
        )

    async def set_favorite(
        self,
        identity: CommandIdentity,
        *,
        asset_kind: str,
        selector_text: str,
        favorite: bool,
    ) -> FavoriteResult:
        """Protect one exact asset or every active same-name copy atomically."""

        if asset_kind not in {"pig", "food"}:
            raise StoreProductError("收藏类型只能是“猪猪”或“美食”。")
        selector = parse_asset_selector(selector_text)
        request_payload = {
            "command_version": 2,
            "asset_kind": asset_kind,
            "name": selector.name,
            "short_code": selector.short_code or "",
            "favorite": bool(favorite),
        }
        idempotency_key = MessageKeyFactory.build(identity, _FAVORITE_COMMAND)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=_FAVORITE_COMMAND,
                    request_payload=request_payload,
                )
                return self._favorite_from_receipt(existing, receipt_created=False)
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            rows = await self.repository.favorite_asset_rows(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                asset_kind=asset_kind,
                selector=selector,
            )
            if not rows:
                noun = "猪猪" if asset_kind == "pig" else "美食"
                error = f"你的{noun}背包中找不到可收藏的“{selector_text.strip()}”。"
                if asset_kind == "pig":
                    raise PigNotFoundError(error)
                raise FoodNotFoundError(error)
            asset_ids = tuple(str(row["asset_id"]) for row in rows)
            changed_count = await self.repository.set_assets_favorite(
                session,
                asset_kind=asset_kind,
                asset_ids=asset_ids,
                favorite=favorite,
                now=now,
            )
            payload = {
                "asset_kind": asset_kind,
                "display_name": str(rows[0]["display_name_snapshot"]),
                "target_count": len(rows),
                "changed_count": changed_count,
                "favorite": bool(favorite),
            }
            provisional = FavoriteResult(
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=_FAVORITE_COMMAND,
                    request_payload=request_payload,
                    result_type="favorite-update",
                    result_object_id=asset_ids[0],
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                asset_kind=asset_kind,
                display_name=str(payload["display_name"]),
                target_count=len(rows),
                changed_count=changed_count,
                favorite=bool(favorite),
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=_FAVORITE_COMMAND,
                request_payload=request_payload,
                result_type="favorite-update",
                result_object_id=asset_ids[0],
                result_payload=payload,
                text_summary=format_favorite_summary(provisional),
                now=now,
            )
            return FavoriteResult(
                receipt=receipt,
                receipt_created=True,
                asset_kind=asset_kind,
                display_name=str(payload["display_name"]),
                target_count=len(rows),
                changed_count=changed_count,
                favorite=bool(favorite),
            )

    async def _sell(
        self,
        identity: CommandIdentity,
        selector_text: str,
        *,
        asset_kind: str,
        command_name: str,
    ) -> SaleResult:
        await self._expire_stale_offers()
        normalized_selector = str(selector_text or "").strip()
        if normalized_selector:
            selector = parse_asset_selector(normalized_selector)
            request_payload = {
                "command_version": 2,
                "selection": "exact",
                "name": selector.name,
                "short_code": selector.short_code or "",
            }
        else:
            request_payload = {
                "command_version": 2,
                "selection": "cheapest-low-rarity",
            }
        idempotency_key = MessageKeyFactory.build(identity, command_name)
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            existing = await self.receipt_repository.get_by_key(session, idempotency_key)
            if existing is not None:
                validate_existing_receipt(
                    existing,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                )
                return await self._sale_from_receipt(
                    session,
                    existing,
                    receipt_created=False,
                )
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            pig: PigView | None = None
            food: FoodView | None = None
            if asset_kind == "pig":
                pig = await self._resolve_pig_for_action(
                    session,
                    identity,
                    normalized_selector,
                )
                asset_id = pig.pig_instance_id
                display_name = pig.display_name
                selector_value = pig.selector
                rarity = pig.rarity
                official_value = pig.official_value
                changed = await self.repository.sell_pig(
                    session,
                    pig_instance_id=asset_id,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                    now=now,
                )
            else:
                food = await self._resolve_food_for_action(
                    session,
                    identity,
                    normalized_selector,
                )
                asset_id = food.food_instance_id
                display_name = food.display_name
                selector_value = food.selector
                rarity = food.rarity
                official_value = food.official_value
                changed = await self.repository.sell_food(
                    session,
                    food_instance_id=asset_id,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                    now=now,
                )
            if not changed:
                raise AssetStateConflictError("资产已不在有效背包中，本次售卖未结算。")
            await self.social_repository.clear_showcase_asset(
                session,
                player_id=identity.player_id,
                asset_kind=(AssetKind.PIG if asset_kind == AssetKind.PIG.value else AssetKind.FOOD),
                asset_instance_id=asset_id,
                now=now,
            )
            balance_after = await self.repository.apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=official_value,
                reason_code=f"sell-{asset_kind}",
                reason_text=f"官方售卖{display_name}",
                source_object_type=asset_kind,
                source_object_id=asset_id,
                ledger_entry_id=self._new_identifier(),
                idempotency_key=f"{idempotency_key}:coin",
                now=now,
            )
            if balance_after is None:
                raise RuntimeError("官方售卖正数收益无法写入玩家余额。")
            payload = {
                "asset_kind": asset_kind,
                "asset_id": asset_id,
                "display_name": display_name,
                "selector": selector_value,
                "rarity": rarity,
                "official_value": official_value,
                "balance_after": balance_after,
            }
            provisional = SaleResult(
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=command_name,
                    request_payload=request_payload,
                    result_type=f"{asset_kind}-sale",
                    result_object_id=asset_id,
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                asset_kind=asset_kind,
                display_name=display_name,
                selector=selector_value,
                rarity=rarity,
                official_value=official_value,
                balance_after=balance_after,
                pig=pig,
                food=food,
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type=f"{asset_kind}-sale",
                result_object_id=asset_id,
                result_payload=payload,
                text_summary=format_sale_summary(provisional),
                now=now,
            )
            return SaleResult(
                receipt=receipt,
                receipt_created=True,
                asset_kind=asset_kind,
                display_name=display_name,
                selector=selector_value,
                rarity=rarity,
                official_value=official_value,
                balance_after=balance_after,
                pig=pig,
                food=food,
            )

    async def _resolve_pig(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        selector_text: str,
    ) -> PigView:
        selector = parse_asset_selector(selector_text)
        rows = await self.gameplay_repository.find_active_pigs(
            session,
            player_id=identity.player_id,
            selector=selector,
        )
        if not rows:
            raise PigNotFoundError(f"你的猪猪背包中找不到“{selector_text.strip()}”。")
        if len(rows) > 1:
            candidates = "、".join(f"{row['display_name_snapshot']}#{row['short_code']}" for row in rows[:8])
            raise AmbiguousPigSelectorError(f"“{selector.name}”有多只，请带短编号重试：{candidates}")
        return pig_view_from_row(rows[0])

    async def _resolve_food(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        selector_text: str,
    ) -> FoodView:
        selector = parse_asset_selector(selector_text)
        rows = await self.repository.find_active_foods(
            session,
            player_id=identity.player_id,
            selector=selector,
        )
        if not rows:
            raise FoodNotFoundError(f"你的美食背包中找不到“{selector_text.strip()}”。")
        if len(rows) > 1:
            candidates = "、".join(f"{row['display_name_snapshot']}#{row['short_code']}" for row in rows[:8])
            raise AmbiguousFoodSelectorError(f"“{selector.name}”有多份，请带短编号重试：{candidates}")
        return food_view_from_row(rows[0])

    async def _resolve_pig_for_action(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        selector_text: str,
    ) -> PigView:
        if selector_text:
            selector = parse_asset_selector(selector_text)
            rows = await self.gameplay_repository.find_active_pigs(
                session,
                player_id=identity.player_id,
                selector=selector,
            )
            if not rows:
                raise PigNotFoundError(f"你的猪猪背包中找不到“{selector_text.strip()}”。")
            if selector.short_code is not None:
                pig = pig_view_from_row(rows[0])
                if pig.is_favorite:
                    raise AssetStateConflictError(
                        f"“{pig.selector}”已收藏保护，请先使用 /取消收藏 猪猪 {pig.selector}。"
                    )
                return pig
            eligible = [row for row in rows if not bool(row.get("is_favorite") or False)]
            if not eligible:
                raise AssetStateConflictError(
                    f"“{selector.name}”的全部实例都已收藏保护，请先取消收藏。"
                )
            selected = min(
                eligible,
                key=lambda row: (
                    int(row["official_value"]),
                    str(row["acquired_at"]),
                    str(row["pig_instance_id"]),
                ),
            )
            return pig_view_from_row(selected)
        pig_instance_id = await self.repository.cheapest_active_asset_id(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            asset_kind="pig",
            max_rarity=3,
        )
        if pig_instance_id is None:
            raise PigNotFoundError("背包中没有可自动处理的 1 至 3 星猪猪；4 星以上请填写名称#短编号。")
        row = await self.gameplay_repository.get_pig_by_instance_id(
            session,
            pig_instance_id=pig_instance_id,
        )
        if row is None:
            raise PigNotFoundError("自动选择的猪猪已不可用，请重试。")
        return pig_view_from_row(row)

    async def _resolve_food_for_action(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        selector_text: str,
    ) -> FoodView:
        if selector_text:
            selector = parse_asset_selector(selector_text)
            rows = await self.repository.find_active_foods(
                session,
                player_id=identity.player_id,
                selector=selector,
            )
            if not rows:
                raise FoodNotFoundError(f"你的美食背包中找不到“{selector_text.strip()}”。")
            if selector.short_code is not None:
                food = food_view_from_row(rows[0])
                if food.is_favorite:
                    raise AssetStateConflictError(
                        f"“{food.selector}”已收藏保护，请先使用 /取消收藏 美食 {food.selector}。"
                    )
                return food
            eligible = [row for row in rows if not bool(row.get("is_favorite") or False)]
            if not eligible:
                raise AssetStateConflictError(
                    f"“{selector.name}”的全部实例都已收藏保护，请先取消收藏。"
                )
            return food_view_from_row(eligible[0])
        food_instance_id = await self.repository.cheapest_active_asset_id(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            asset_kind="food",
            max_rarity=3,
        )
        if food_instance_id is None:
            raise FoodNotFoundError("背包中没有可自动处理的 1 至 3 星美食；4 星以上请填写名称#短编号。")
        return await self._food_by_id(session, food_instance_id)

    async def _food_by_id(
        self,
        session: DatabaseSession,
        food_instance_id: str,
    ) -> FoodView:
        row = await self.repository.get_food_by_instance_id(
            session,
            food_instance_id=food_instance_id,
        )
        if row is None:
            raise ReceiptConflictError("美食实例写入后无法读取。")
        return food_view_from_row(row)

    def _food_effect(self, food: FoodView) -> FoodEffectOutcome:
        effect_id = food.effect_id.strip()
        if not effect_id:
            return FoodEffectOutcome("基础效果：本次获得品鉴经验。")
        try:
            grant = resolve_food_effect(effect_id, food.effect_params)
        except FoodEffectError:
            grant = None
        if grant is not None:
            coin_bonus = 0
            if grant.effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH:
                coin_bonus = int(grant.params["self_coin"])
            elif grant.effect_id == GROUP_WINDOW_HIGH_STAR_BOOST:
                coin_bonus = int(grant.params["coin_per_player"])
            return FoodEffectOutcome(
                summary=grant.summary,
                coin_bonus=coin_bonus,
                queued_effect_id=grant.effect_id,
                queued_effect_params=grant.params,
                granted_uses=grant.granted_uses,
            )
        handler = self.effect_handlers.get(effect_id)
        if handler is None:
            raise FoodEffectError(f"美食效果“{effect_id}”尚未注册，当前不会消耗这份美食。")
        outcome = handler(food, food.effect_params)
        if (
            not isinstance(outcome, FoodEffectOutcome)
            or outcome.experience_bonus < 0
            or outcome.coin_bonus < 0
            or not outcome.summary.strip()
        ):
            raise FoodEffectError(f"美食效果“{effect_id}”返回了无效结果。")
        return outcome

    async def _cook_from_receipt(
        self,
        session: DatabaseSession,
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> CookingResult:
        payload = receipt_payload(receipt)
        source_id = str(payload.get("source_pig_instance_id") or "")
        raw_food_ids = payload.get("food_instance_ids")
        if not source_id or not isinstance(raw_food_ids, list) or not raw_food_ids:
            raise ReceiptConflictError("做菜回执没有完整的原料和出餐实例。")
        source_row = await self.gameplay_repository.get_pig_by_instance_id(
            session,
            pig_instance_id=source_id,
        )
        if source_row is None:
            raise ReceiptConflictError("做菜回执关联的原料猪不存在。")
        foods = tuple([await self._food_by_id(session, str(food_id)) for food_id in raw_food_ids])
        raw_weights = payload.get("weights")
        if not isinstance(raw_weights, list) or len(raw_weights) != 6:
            raise ReceiptConflictError("做菜回执中的概率快照无效。")
        return CookingResult(
            source_pig=pig_view_from_row(source_row),
            foods=foods,
            receipt=receipt,
            receipt_created=receipt_created,
            coin_reward=int(payload["coin_reward"]),
            experience_reward=int(payload["experience_reward"]),
            coin_balance=int(payload["coin_balance"]),
            total_experience=int(payload["total_experience"]),
            catalog_new_count=int(payload["catalog_new_count"]),
            cookware_level=int(payload["cookware_level"]),
            item_id=str(payload.get("item_id") or ""),
            item_name=str(payload.get("item_name") or ""),
            weights=tuple(float(value) for value in raw_weights),
            bonus_serving=bool(payload["bonus_serving"]),
            effect_summaries=tuple(str(value) for value in payload.get("effect_summaries", []) if str(value).strip()),
            item_remaining_uses=int(payload.get("item_remaining_uses") or 0),
            excluded_summaries=tuple(
                str(value) for value in payload.get("excluded_summaries", []) if str(value).strip()
            ),
            exclusive_effect_active=bool(payload.get("exclusive_effect_active") or False),
            veteran_coin_reward=int(payload.get("veteran_coin_reward") or 0),
            veteran_reward_levels=tuple(
                int(value)
                for value in payload.get("veteran_reward_levels", [])
            ),
        )

    async def _eat_from_receipt(
        self,
        session: DatabaseSession,
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> EatResult:
        payload = receipt_payload(receipt)
        food = await self._food_by_id(
            session,
            str(payload.get("food_instance_id") or ""),
        )
        return EatResult(
            food=food,
            receipt=receipt,
            receipt_created=receipt_created,
            base_experience=int(payload["base_experience"]),
            effect=FoodEffectOutcome(
                summary=str(payload["effect_summary"]),
                experience_bonus=int(payload["effect_experience_bonus"]),
                coin_bonus=int(payload["effect_coin_bonus"]),
                queued_effect_id=str(payload.get("queued_effect_id") or ""),
                queued_effect_params=(
                    dict(payload["queued_effect_params"])
                    if isinstance(payload.get("queued_effect_params"), dict)
                    else {}
                ),
                granted_uses=int(payload.get("effect_granted_uses") or 0),
                expires_at=str(payload.get("effect_expires_at") or ""),
            ),
            total_experience=int(payload["total_experience"]),
            coin_balance=int(payload["coin_balance"]),
            group_rewarded_players=int(payload.get("group_rewarded_players") or 0),
            group_coin_total=int(payload.get("group_coin_total") or 0),
            available_effect_uses=int(
                payload.get("available_effect_uses")
                or payload.get("roulette_available_spins")
                or 0
            ),
            veteran_coin_reward=int(payload.get("veteran_coin_reward") or 0),
            veteran_reward_levels=tuple(
                int(value)
                for value in payload.get("veteran_reward_levels", [])
            ),
        )

    def _purchase_from_receipt(
        self,
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> PurchaseResult:
        return self._purchase_result(
            receipt,
            receipt_payload(receipt),
            receipt_created=receipt_created,
        )

    @staticmethod
    def _purchase_result(
        receipt: CommandReceipt,
        payload: Mapping[str, object],
        *,
        receipt_created: bool,
    ) -> PurchaseResult:
        return PurchaseResult(
            receipt=receipt,
            receipt_created=receipt_created,
            product_id=str(payload["product_id"]),
            display_name=str(payload["display_name"]),
            product_type=str(payload["product_type"]),
            quantity=int(payload["quantity"]),
            unit_price=int(payload["unit_price"]),
            total_price=int(payload["total_price"]),
            balance_after=int(payload["balance_after"]),
            inventory_quantity=int(payload["inventory_quantity"]),
            upgrade_type=str(payload["upgrade_type"]),
            upgrade_level=int(payload["upgrade_level"]),
        )

    async def set_batch_keep_highest(
        self,
        identity: CommandIdentity,
        *,
        enabled: bool,
    ) -> tuple[bool, str]:
        """开启或关闭玩家的“批量保留”偏好。

        联动猪始终按模板保留一只价值最高的实例。开启后，批量售卖与批量做菜
        还会按模板各保留一只价值最高的普通猪猪或美食。
        """

        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            await self.framework_repository.touch_identity(
                session,
                identity=identity,
                now=now,
            )
            await self.gameplay_repository.set_batch_keep_highest(
                session,
                player_id=identity.player_id,
                enabled=enabled,
                now=now,
            )
        if enabled:
            return True, (
                "已开启批量保留：批量售卖与批量做菜时，每个普通猪猪品种和每道"
                "美食品种都会保留一只价值最高的实例；每种联动猪也会保留价值最高的一只。"
            )
        return True, (
            "已关闭批量保留：批量操作不再额外保留普通猪猪和美食；"
            "每种联动猪仍会保留价值最高的一只。"
        )

    @staticmethod
    def _batch_sale_from_receipt(
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> BatchSaleResult:
        payload = receipt_payload(receipt)
        return BatchSaleResult(
            receipt=receipt,
            receipt_created=receipt_created,
            asset_kind=str(payload["asset_kind"]),
            asset_count=int(payload["asset_count"]),
            max_rarity=int(payload["max_rarity"]),
            total_value=int(payload["total_value"]),
            balance_after=int(payload["balance_after"]),
            rarity=(int(payload["rarity"]) if payload.get("rarity") is not None else None),
            display_name=str(payload.get("display_name") or ""),
        )

    @staticmethod
    def _favorite_from_receipt(
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> FavoriteResult:
        payload = receipt_payload(receipt)
        return FavoriteResult(
            receipt=receipt,
            receipt_created=receipt_created,
            asset_kind=str(payload["asset_kind"]),
            display_name=str(payload["display_name"]),
            target_count=int(payload["target_count"]),
            changed_count=int(payload["changed_count"]),
            favorite=bool(payload["favorite"]),
        )

    async def _sale_from_receipt(
        self,
        session: DatabaseSession,
        receipt: CommandReceipt,
        *,
        receipt_created: bool,
    ) -> SaleResult:
        payload = receipt_payload(receipt)
        asset_kind = str(payload["asset_kind"])
        asset_id = str(payload["asset_id"])
        pig: PigView | None = None
        food: FoodView | None = None
        if asset_kind == "pig":
            row = await self.gameplay_repository.get_pig_by_instance_id(
                session,
                pig_instance_id=asset_id,
            )
            if row is None:
                raise ReceiptConflictError("售卖回执关联的猪猪不存在。")
            pig = pig_view_from_row(row)
        elif asset_kind == "food":
            food = await self._food_by_id(session, asset_id)
        else:
            raise ReceiptConflictError("售卖回执包含未知资产类型。")
        return SaleResult(
            receipt=receipt,
            receipt_created=receipt_created,
            asset_kind=asset_kind,
            display_name=str(payload["display_name"]),
            selector=str(payload["selector"]),
            rarity=int(payload["rarity"]),
            official_value=int(payload["official_value"]),
            balance_after=int(payload["balance_after"]),
            pig=pig,
            food=food,
        )

    async def _reserve(
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
            result_json=json.dumps(
                dict(result_payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            text_summary=text_summary,
            now=now,
        )
        return reservation.receipt

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
            send_status=ReceiptSendStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _affinity_candidates(
        templates: Sequence[Mapping[str, object]],
        desired_affinity: str,
    ) -> list[Mapping[str, object]]:
        matches = [
            template
            for template in templates
            if recipe_affinity(
                list(
                    _json_strings(
                        template.get("recipe_tags_json"),
                        label="美食模板食谱标签",
                    )
                )
            )
            == desired_affinity
        ]
        return matches or list(templates)

    @staticmethod
    def _armed_item(
        row: Mapping[str, object] | None,
    ) -> tuple[ItemDefinition | None, int]:
        if row is None:
            return None, 0
        item = item_by_id(str(row["item_id"]))
        if item.action_type != "cooking":
            raise ItemInventoryError("已装备道具与做菜动作不兼容，请先取消道具。")
        inventory_quantity = int(row["quantity"] or 0)
        remaining_uses = int(row.get("remaining_uses") or 0)
        if inventory_quantity <= 0 or remaining_uses <= 0:
            raise ItemInventoryError(f"已装备的“{item.display_name}”库存不足，请先取消道具。")
        return item, remaining_uses

    def _new_identifier(self) -> str:
        candidate = str(self.id_factory() or "").strip()
        if not candidate or len(candidate) > 128:
            raise RuntimeError("实例 ID 生成器返回了无效值。")
        return candidate

    async def _apply_group_food_coin_rewards(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        food: FoodView,
        effect: FoodEffectOutcome,
        idempotency_key: str,
        now: str,
    ) -> tuple[int, int]:
        """Reward every registered player in the exact scope, atomically."""

        players = await self.repository.players_in_scope(
            session,
            scope_id=identity.scope.value,
        )
        eater_balance: int | None = None
        rewarded = 0
        for index, player in enumerate(players):
            player_id = str(player["player_id"])
            if effect.queued_effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH:
                amount = int(
                    effect.queued_effect_params[
                        "self_coin" if player_id == identity.player_id else "other_coin"
                    ]
                )
            elif effect.queued_effect_id == GROUP_WINDOW_HIGH_STAR_BOOST:
                amount = int(effect.queued_effect_params["coin_per_player"])
            else:
                raise RuntimeError("群体猪币奖励关联了未知的六星菜效果。")
            if amount <= 0:
                if player_id == identity.player_id:
                    eater_balance = int(player["coin_balance"])
                continue
            balance = await self.repository.apply_currency_change(
                session,
                player_id=player_id,
                scope_id=identity.scope.value,
                amount=amount,
                reason_code="group-food-effect",
                reason_text=f"食用{food.display_name}的全群奖励",
                source_object_type="food",
                source_object_id=food.food_instance_id,
                ledger_entry_id=self._new_identifier(),
                idempotency_key=f"{idempotency_key}:group-coin:{index}",
                now=now,
            )
            if balance is None:
                raise RuntimeError("六星菜全群猪币奖励写入失败。")
            rewarded += 1
            if player_id == identity.player_id:
                eater_balance = balance
        if eater_balance is None:
            raise RuntimeError("六星菜食用者不在当前群玩家清单中。")
        return eater_balance, rewarded

    async def _apply_group_coin_tribute(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        food: FoodView,
        coin_per_player: int,
        idempotency_key: str,
        now: str,
    ) -> tuple[int, int, int]:
        """Transfer up to a fixed amount from every other registered player."""

        players = await self.repository.players_in_scope(
            session,
            scope_id=identity.scope.value,
        )
        transferred_total = 0
        payer_count = 0
        eater_balance: int | None = None
        for index, player in enumerate(players):
            player_id = str(player["player_id"])
            balance_before = int(player["coin_balance"])
            if player_id == identity.player_id:
                eater_balance = balance_before
                continue
            payment = min(max(0, int(coin_per_player)), balance_before)
            if payment <= 0:
                continue
            balance = await self.repository.apply_currency_change(
                session,
                player_id=player_id,
                scope_id=identity.scope.value,
                amount=-payment,
                reason_code="group-food-tribute",
                reason_text=f"向{food.display_name}食用者支付猪币",
                source_object_type="food",
                source_object_id=food.food_instance_id,
                ledger_entry_id=self._new_identifier(),
                idempotency_key=f"{idempotency_key}:tribute:{index}",
                now=now,
            )
            if balance is None:
                raise RuntimeError("群友猪币支付并发失败。")
            transferred_total += payment
            payer_count += 1
        if eater_balance is None:
            raise RuntimeError("炸猪全家桶食用者不在当前群玩家清单中。")
        if transferred_total:
            credited = await self.repository.apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=transferred_total,
                reason_code="group-food-tribute",
                reason_text=f"食用{food.display_name}收到群友猪币",
                source_object_type="food",
                source_object_id=food.food_instance_id,
                ledger_entry_id=self._new_identifier(),
                idempotency_key=f"{idempotency_key}:tribute:eater",
                now=now,
            )
            if credited is None:
                raise RuntimeError("炸猪全家桶猪币归集失败。")
            eater_balance = credited
        return eater_balance, payer_count, transferred_total

    @staticmethod
    def _daily_effect_expiry(now: datetime) -> str:
        beijing_timezone = timezone(timedelta(hours=8), "Asia/Shanghai")
        local = now.astimezone(beijing_timezone)
        next_day = (local + timedelta(days=1)).date()
        expiry = datetime.combine(
            next_day,
            datetime.min.time(),
            tzinfo=beijing_timezone,
        )
        return iso_timestamp(expiry)

    def _next_same_window_effect_expiry(self, now: datetime) -> str:
        window = catch_quota_window(
            now,
            refresh_hours=self.quota_refresh_hours,
            timezone_name=self.quota_timezone_name,
        )
        return iso_timestamp(window.start + timedelta(days=1))

    def _rolling_seven_day_effect_expiry(self, now: datetime) -> str:
        anniversary_window = catch_quota_window(
            now + timedelta(days=7),
            refresh_hours=self.quota_refresh_hours,
            timezone_name=self.quota_timezone_name,
        )
        return iso_timestamp(anniversary_window.end)

    async def _expire_stale_offers(self) -> int:
        now = iso_timestamp(self.clock.now())
        async with self.database.transaction() as session:
            return await self.social_repository.expire_stale_offers(
                session,
                now=now,
            )

    async def _new_unique_short_code(
        self,
        session: DatabaseSession,
        *,
        reserved: Collection[str] = (),
    ) -> str:
        for _ in range(32):
            candidate = str(self.short_code_factory() or "").strip().upper()
            if not is_valid_short_code(candidate):
                continue
            if candidate in reserved:
                continue
            if not await self.gameplay_repository.short_code_exists(session, candidate):
                return candidate
        raise RuntimeError("连续生成 32 次仍无法得到唯一美食短编号。")

    @staticmethod
    def _snapshot_json(snapshot: Mapping[str, object]) -> str:
        return json.dumps(
            dict(snapshot),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
