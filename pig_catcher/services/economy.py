"""Fourth-round cooking, food collection, store, sale, and ledger services."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
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
    upgrade_type_by_name,
)
from ..domain.enums import AssetKind, Rarity, ReceiptSendStatus, UpgradeType
from ..domain.errors import (
    AmbiguousFoodSelectorError,
    AmbiguousPigSelectorError,
    AssetStateConflictError,
    CookingTemplateError,
    FoodEffectError,
    FoodNotFoundError,
    InsufficientBalanceError,
    ItemInventoryError,
    LedgerReconciliationError,
    PigNotFoundError,
    ReceiptConflictError,
    StoreProductError,
    UpgradeLimitError,
)
from ..domain.food_effects import (
    EXTRA_CATCHES,
    active_effect_from_row,
    apply_cooking_effects,
    effect_summary,
    resolve_food_effect,
)
from ..domain.gameplay import ItemDefinition, item_by_id, level_progress
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, RandomSource, SystemClock, SystemRandomSource
from ..domain.rules import (
    BASE_CATCH_WEIGHTS,
    catch_weights,
    choose_rarity,
    normalize_weights,
)
from ..domain.selectors import new_short_code, parse_asset_selector
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories import (
    EconomyRepository,
    FrameworkRepository,
    GameplayRepository,
    ReceiptRepository,
    SocialRepository,
)
from ..version import RULESET_VERSION
from .command_state import (
    iso_timestamp,
    receipt_payload,
    valid_page_count,
    validate_existing_receipt,
)
from .gameplay import PigView, pig_view_from_row
from .receipts import request_fingerprint

_COOK_COMMAND = "pig-catcher.cook"
_EAT_COMMAND = "pig-catcher.eat"
_PURCHASE_COMMAND = "pig-catcher.purchase"
_UPGRADE_COMMAND = "pig-catcher.upgrade"
_SELL_PIG_COMMAND = "pig-catcher.sell-pig"
_SELL_FOOD_COMMAND = "pig-catcher.sell-food"
_BATCH_SELL_PIG_COMMAND = "pig-catcher.batch-sell-pig"
_BATCH_SELL_FOOD_COMMAND = "pig-catcher.batch-sell-food"
_SHORT_CODE_PATTERN = re.compile(r"^[A-F0-9]{8}$")
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
    effect_summaries: tuple[str, ...] = ()

    @property
    def probability_summary(self) -> str:
        """Format the persisted final rarity weights for user-facing audit."""

        return " · ".join(
            f"{rarity}★ {weight:.1f}%"
            for rarity, weight in enumerate(self.weights, start=1)
            if weight > 0
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
        best_portion_weight=(
            float(row["best_portion_weight"])
            if row["best_portion_weight"] is not None
            else None
        ),
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
    bonus = (
        f"\n大份餐盒加餐：{result.foods[1].selector}"
        if result.bonus_serving and len(result.foods) > 1
        else ""
    )
    item = result.item_name or "无"
    effect_text = (
        f"\n美食加成：{'；'.join(result.effect_summaries)}"
        if result.effect_summaries
        else ""
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
        f"最终品质概率：{result.probability_summary}{effect_text}"
    )


def format_food_detail_summary(food: FoodView) -> str:
    """Return a complete text fallback for one food."""

    effect = effect_summary(food.effect_id, food.effect_params)
    return (
        "【美食详情】\n"
        f"{food.stars} {food.display_name}（{food.rarity_name}）\n"
        f"编号：{food.selector}\n"
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
            f"{food.stars} {food.selector}｜{food.portion_weight:.2f}kg｜"
            f"{food.fat_label}｜{food.official_value}猪币"
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
    coin = (
        f"；额外 +{result.effect.coin_bonus} 猪币"
        if result.effect.coin_bonus
        else ""
    )
    return (
        "【美食品鉴】\n"
        f"已吃掉 {result.food.stars} {result.food.selector}\n"
        f"获得经验：+{experience}{coin}\n"
        f"当前累计经验：{result.total_experience}；猪币：{result.coin_balance}\n"
        f"效果：{result.effect.summary}"
    )


def format_store_summary(result: StorePage) -> str:
    """Return a complete store fallback."""

    feed_probabilities = tuple(
        sum(
            catch_weights(
                result.catch_base_weights,
                feed_level=level,
            )[3:]
        )
        for level in range(6)
    )
    cookware_bonuses = tuple(
        (cookware_higher_rarity_multiplier(level) - 1.0) * 100.0
        for level in range(6)
    )
    lines = [
        "【猪猪商城】",
        f"玩家：{result.display_name}；余额：{result.coin_balance} 猪币",
        f"分类：{result.category}；单页展示全部 {result.total_count} 项",
        f"猪饲料 Lv.{result.feed_level}；厨具 Lv.{result.cookware_level}",
        "猪饲料 Lv.0-5 的 4-6 星合计概率："
        + " / ".join(f"{value:.2f}%" for value in feed_probabilities),
        "厨具 Lv.0-5 的高档菜相对权重增幅："
        + " / ".join(f"+{value:.0f}%" for value in cookware_bonuses),
        "六星猪做菜不受厨具影响，保持 90% 五星 / 10% 六星。",
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
        lines.append(
            f"{product.display_name}｜{price}｜{product.effect_summary}｜{command}"
        )
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
    return (
        "【批量售卖成功】\n"
        f"范围：1 至 {result.max_rarity} 星{kind}\n"
        f"售出：{result.asset_count} 件\n"
        f"收入：{result.total_value} 猪币\n"
        f"当前余额：{result.balance_after}\n"
        "交易锁定的资产未被处理，已解锁图鉴记录不会减少。"
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
        lines.append(
            f"{sign}{entry.amount}｜余额 {entry.balance_after}｜"
            f"{entry.reason_text}｜{entry.created_at}"
        )
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
        random_source: RandomSource | None = None,
        clock: Clock | None = None,
        id_factory: Callable[[], str] | None = None,
        short_code_factory: Callable[[], str] | None = None,
        effect_handlers: Mapping[str, FoodEffectHandler] | None = None,
        catch_base_weights: Sequence[float] | None = None,
    ) -> None:
        self.database = database
        self.cooking = cooking
        self.economy = economy
        self.repository = repository or EconomyRepository()
        self.gameplay_repository = gameplay_repository or GameplayRepository()
        self.framework_repository = framework_repository or FrameworkRepository()
        self.receipt_repository = receipt_repository or ReceiptRepository()
        self.social_repository = social_repository or SocialRepository()
        self.random_source = random_source or SystemRandomSource()
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.short_code_factory = short_code_factory or new_short_code
        self.effect_handlers = dict(effect_handlers or {})
        self.catch_base_weights = normalize_weights(
            BASE_CATCH_WEIGHTS
            if catch_base_weights is None
            else catch_base_weights
        )

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
            source = await self._resolve_pig_for_action(
                session,
                identity,
                normalized_selector,
            )
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
            armed_row = await self.gameplay_repository.get_armed_item(
                session,
                player_id=identity.player_id,
                action_type="cooking",
            )
            armed_item, _ = self._armed_item(armed_row)
            weights = adjusted_cooking_weights(
                source.rarity,
                size_percentile=source.size_percentile,
                weight_percentile=source.weight_percentile,
                cookware_level=cookware_level,
                player_level=probability_level,
                chef_spice=(
                    armed_item is not None and armed_item.item_id == "chef-spice"
                ),
            )
            active_effects = tuple(
                active_effect_from_row(row)
                for row in await self.repository.list_active_food_effects(
                    session,
                    player_id=identity.player_id,
                    now=now,
                )
            )
            effect_application = apply_cooking_effects(
                weights,
                active_effects,
                source_rarity=source.rarity,
            )
            weights = effect_application.weights
            rarity_roll = self.random_source.random()
            output_rarity = choose_rarity(weights, rarity_roll)
            templates = await self.repository.list_drawable_food_templates(
                session,
                scope_id=identity.scope.value,
                rarity=int(output_rarity),
            )
            if not templates:
                raise CookingTemplateError(
                    f"当前群没有可用的 {int(output_rarity)} 星美食模板，原料猪未消耗。"
                )
            desired_affinity = source.fat_category
            if source.rarity == 6 and int(output_rarity) == 6:
                paired_template_id = source.paired_food_template_id
                candidates = [
                    candidate
                    for candidate in templates
                    if str(candidate["template_id"]) == paired_template_id
                ]
                if not paired_template_id or not candidates:
                    raise CookingTemplateError(
                        "这只六星猪没有当前群可用的对应定制六星菜，原料猪未消耗。"
                    )
            else:
                paired_template_id = ""
                if armed_item is not None and armed_item.item_id == "precision-knife":
                    desired_affinity = "lean"
                elif armed_item is not None and armed_item.item_id == "slow-cook-seasoning":
                    desired_affinity = "fatty"
                candidates = self._affinity_candidates(templates, desired_affinity)
            template_roll = self.random_source.random()
            template = candidates[
                min(int(template_roll * len(candidates)), len(candidates) - 1)
            ]
            portion_roll = self.random_source.random()
            main_attributes = generate_food_attributes(
                rarity=output_rarity,
                template_id=str(template["template_id"]),
                source_weight=source.weight_value,
                source_weight_percentile=source.weight_percentile,
                portion_roll=portion_roll,
            )
            bonus_roll: float | None = None
            bonus_portion_roll: float | None = None
            bonus_attributes: FoodAttributes | None = None
            bonus_serving = False
            if (
                armed_item is not None
                and armed_item.item_id == "large-lunch-box"
                and source.rarity <= 5
                and int(output_rarity) <= 5
            ):
                bonus_roll = self.random_source.random()
                bonus_serving = bonus_roll < 0.25
                if bonus_serving:
                    bonus_portion_roll = self.random_source.random()
                    bonus_attributes = generate_food_attributes(
                        rarity=output_rarity,
                        template_id=str(template["template_id"]),
                        source_weight=source.weight_value,
                        source_weight_percentile=source.weight_percentile,
                        portion_roll=bonus_portion_roll,
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
            consumed = await self.repository.consume_pig_for_cooking(
                session,
                pig_instance_id=source.pig_instance_id,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                now=now,
            )
            if not consumed:
                raise AssetStateConflictError("原料猪已不在有效背包中，本次做菜未结算。")
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
                "item_id": armed_item.item_id if armed_item is not None else "",
                "rarity_roll": rarity_roll,
                "template_roll": template_roll,
                "desired_affinity": desired_affinity,
                "paired_food_template_id": paired_template_id,
                "bonus_roll": bonus_roll,
                "bonus_serving": bonus_serving,
                "food_effect_entry_ids": list(
                    effect_application.consumed_entry_ids
                ),
                "food_effect_summaries": list(effect_application.summaries),
            }
            catalog_new_count = 0
            for index, (food_id, short_code, attributes) in enumerate(
                zip(food_ids, short_codes, food_specs, strict=True)
            ):
                random_snapshot = {
                    **snapshot_base,
                    "serving_index": index,
                    "portion_roll": (
                        portion_roll
                        if index == 0
                        else bonus_portion_roll
                    ),
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
                        "effect_params_json": str(
                            template.get("effect_params_json") or "{}"
                        ),
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
            await self.social_repository.increment_statistic(
                session,
                player_id=identity.player_id,
                field="total_cooks",
                now=now,
            )
            if armed_item is not None:
                item_consumed = await self.gameplay_repository.consume_armed_item(
                    session,
                    player_id=identity.player_id,
                    action_type="cooking",
                    item_id=armed_item.item_id,
                    now=now,
                )
                if not item_consumed:
                    raise ItemInventoryError(
                        f"已装备的“{armed_item.display_name}”库存不足，本次做菜未结算。"
                    )
            if effect_application.consumed_entry_ids:
                await self.repository.consume_food_effects(
                    session,
                    player_id=identity.player_id,
                    effect_entry_ids=effect_application.consumed_entry_ids,
                    now=now,
                )
            foods = tuple(
                [
                    await self._food_by_id(session, food_id)
                    for food_id in food_ids
                ]
            )
            payload = {
                "source_pig_instance_id": source.pig_instance_id,
                "food_instance_ids": food_ids,
                "coin_reward": coin_reward,
                "experience_reward": experience_reward,
                "coin_balance": coin_balance,
                "total_experience": total_experience,
                "catalog_new_count": catalog_new_count,
                "cookware_level": cookware_level,
                "item_id": armed_item.item_id if armed_item is not None else "",
                "item_name": armed_item.display_name if armed_item is not None else "",
                "weights": [round(value, 8) for value in weights],
                "bonus_serving": bonus_serving,
                "effect_summaries": list(effect_application.summaries),
            }
            provisional = CookingResult(
                source_pig=source,
                foods=foods,
                receipt=self._provisional_receipt(
                    idempotency_key=idempotency_key,
                    identity=identity,
                    command_name=_COOK_COMMAND,
                    request_payload=request_payload,
                    result_type="cooking",
                    result_object_id=food_ids[0],
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                coin_reward=coin_reward,
                experience_reward=experience_reward,
                coin_balance=coin_balance,
                total_experience=total_experience,
                catalog_new_count=catalog_new_count,
                cookware_level=cookware_level,
                item_id=armed_item.item_id if armed_item is not None else "",
                item_name=armed_item.display_name if armed_item is not None else "",
                weights=weights,
                bonus_serving=bonus_serving,
                effect_summaries=effect_application.summaries,
            )
            reservation = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=_COOK_COMMAND,
                request_payload=request_payload,
                result_type="cooking",
                result_object_id=food_ids[0],
                result_payload=payload,
                text_summary=format_cooking_summary(provisional),
                now=now,
            )
            return CookingResult(
                source_pig=source,
                foods=foods,
                receipt=reservation,
                receipt_created=True,
                coin_reward=coin_reward,
                experience_reward=experience_reward,
                coin_balance=coin_balance,
                total_experience=total_experience,
                catalog_new_count=catalog_new_count,
                cookware_level=cookware_level,
                item_id=armed_item.item_id if armed_item is not None else "",
                item_name=armed_item.display_name if armed_item is not None else "",
                weights=weights,
                bonus_serving=bonus_serving,
                effect_summaries=effect_application.summaries,
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
            effect_expires_at = effect.expires_at
            if effect.queued_effect_id:
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
            base_experience = EAT_EXPERIENCE_REWARDS[Rarity(food.rarity)]
            total_experience = await self.repository.add_experience(
                session,
                player_id=identity.player_id,
                experience=base_experience + effect.experience_bonus,
                now=now,
            )
            if effect.coin_bonus:
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
            payload = {
                "food_instance_id": food.food_instance_id,
                "base_experience": base_experience,
                "effect_summary": effect.summary,
                "effect_experience_bonus": effect.experience_bonus,
                "effect_coin_bonus": effect.coin_bonus,
                "effect_entry_id": effect_entry_id,
                "queued_effect_id": effect.queued_effect_id,
                "queued_effect_params": dict(effect.queued_effect_params),
                "effect_granted_uses": effect.granted_uses,
                "effect_expires_at": effect_expires_at,
                "total_experience": total_experience,
                "coin_balance": coin_balance,
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
                text_summary=format_eat_summary(provisional),
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
            )

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
        filtered = tuple(
            product
            for product in products
            if resolved is None or product.category == resolved
        )
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
            raise StoreProductError(
                "永久升级请使用 /升级 猪饲料 或 /升级 厨具。"
            )
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
            raise StoreProductError(
                f"购买数量必须位于 1 至 {self.economy.max_purchase_quantity}。"
            )
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
                    raise UpgradeLimitError(
                        f"{'猪饲料' if upgrade_type is UpgradeType.FEED else '厨具'}已达到 Lv.5。"
                    )
                prices = (
                    self.economy.feed_upgrade_prices
                    if upgrade_type is UpgradeType.FEED
                    else self.economy.cookware_upgrade_prices
                )
                display_name = (
                    "猪饲料升级"
                    if upgrade_type is UpgradeType.FEED
                    else "厨具升级"
                )
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
                raise InsufficientBalanceError(
                    f"购买需要 {total_price} 猪币，当前余额不足。"
                )
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
    ) -> BatchSaleResult:
        """Sell every unlocked low-rarity pig or food in one transaction."""

        if asset_kind not in {"pig", "food"}:
            raise StoreProductError("批量售卖类型只能是“猪猪”或“美食”。")
        if not 1 <= int(max_rarity) <= 3:
            raise StoreProductError("批量售卖只允许处理 1 至 3 星资产。")
        await self._expire_stale_offers()
        command_name = (
            _BATCH_SELL_PIG_COMMAND
            if asset_kind == "pig"
            else _BATCH_SELL_FOOD_COMMAND
        )
        request_payload = {
            "command_version": 1,
            "asset_kind": asset_kind,
            "max_rarity": int(max_rarity),
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
            count, total_value = await self.repository.batch_sell_low_rarity(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                asset_kind=asset_kind,
                max_rarity=int(max_rarity),
                now=now,
            )
            if count == 0:
                noun = "猪猪" if asset_kind == "pig" else "美食"
                error = (
                    f"背包中没有可批量售卖的 1 至 {max_rarity} 星{noun}；"
                    "交易锁定资产不会被处理。"
                )
                if asset_kind == "pig":
                    raise PigNotFoundError(error)
                raise FoodNotFoundError(error)
            balance_after = await self.repository.apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=total_value,
                reason_code=f"batch-sell-{asset_kind}",
                reason_text=f"批量售卖低星{'猪猪' if asset_kind == 'pig' else '美食'}",
                source_object_type=asset_kind,
                source_object_id=f"rarity-1-{max_rarity}",
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
                    result_object_id=f"{asset_kind}:rarity-1-{max_rarity}",
                    result_payload=payload,
                    now=now,
                ),
                receipt_created=True,
                asset_kind=asset_kind,
                asset_count=count,
                max_rarity=int(max_rarity),
                total_value=total_value,
                balance_after=balance_after,
            )
            receipt = await self._reserve(
                session,
                identity=identity,
                idempotency_key=idempotency_key,
                command_name=command_name,
                request_payload=request_payload,
                result_type="batch-sale",
                result_object_id=f"{asset_kind}:rarity-1-{max_rarity}",
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
                    f"猪币对账异常：余额 {balance}，流水合计 {ledger_total}。"
                    "请停止交易并联系插件管理员。"
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
                asset_kind=(
                    AssetKind.PIG
                    if asset_kind == AssetKind.PIG.value
                    else AssetKind.FOOD
                ),
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
            candidates = "、".join(
                f"{row['display_name_snapshot']}#{row['short_code']}" for row in rows[:8]
            )
            raise AmbiguousPigSelectorError(
                f"“{selector.name}”有多只，请带短编号重试：{candidates}"
            )
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
            candidates = "、".join(
                f"{row['display_name_snapshot']}#{row['short_code']}" for row in rows[:8]
            )
            raise AmbiguousFoodSelectorError(
                f"“{selector.name}”有多份，请带短编号重试：{candidates}"
            )
        return food_view_from_row(rows[0])

    async def _resolve_pig_for_action(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        selector_text: str,
    ) -> PigView:
        if selector_text:
            return await self._resolve_pig(session, identity, selector_text)
        pig_instance_id = await self.repository.cheapest_active_asset_id(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            asset_kind="pig",
            max_rarity=3,
        )
        if pig_instance_id is None:
            raise PigNotFoundError(
                "背包中没有可自动处理的 1 至 3 星猪猪；4 星以上请填写名称#短编号。"
            )
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
            return await self._resolve_food(session, identity, selector_text)
        food_instance_id = await self.repository.cheapest_active_asset_id(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            asset_kind="food",
            max_rarity=3,
        )
        if food_instance_id is None:
            raise FoodNotFoundError(
                "背包中没有可自动处理的 1 至 3 星美食；4 星以上请填写名称#短编号。"
            )
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
            return FoodEffectOutcome(
                summary=grant.summary,
                queued_effect_id=grant.effect_id,
                queued_effect_params=grant.params,
                granted_uses=grant.granted_uses,
            )
        handler = self.effect_handlers.get(effect_id)
        if handler is None:
            raise FoodEffectError(
                f"美食效果“{effect_id}”尚未注册，当前不会消耗这份美食。"
            )
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
        foods = tuple(
            [
                await self._food_by_id(session, str(food_id))
                for food_id in raw_food_ids
            ]
        )
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
            effect_summaries=tuple(
                str(value)
                for value in payload.get("effect_summaries", [])
                if str(value).strip()
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
            if not _SHORT_CODE_PATTERN.fullmatch(candidate):
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
