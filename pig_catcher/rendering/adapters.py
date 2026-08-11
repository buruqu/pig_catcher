"""Map application DTOs to path-safe rendering view models."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from ..domain.economy import (
    adjusted_cooking_weights,
    cookware_higher_rarity_multiplier,
)
from ..domain.errors import RenderError
from ..domain.food_effects import effect_summary
from ..domain.gameplay import level_progress, size_label, weight_label
from ..domain.rules import catch_weights, cooking_weights
from ..domain.social import TRADE_STATUS_LABELS
from ..services.economy import (
    BatchCookingResult,
    BatchSaleResult,
    CookingResult,
    EatResult,
    FoodCatalogPage,
    FoodInventoryPage,
    FoodView,
    LedgerPage,
    PurchaseResult,
    SaleResult,
    StorePage,
)
from ..services.gameplay import (
    CatalogPage,
    CatchResult,
    InventoryPage,
    ItemActionResult,
    PigView,
    PlayerProfile,
    RecordsPage,
)
from ..services.social import (
    GiftResult,
    RankingEntry,
    RankingPage,
    ShowcaseAsset,
    ShowcaseResult,
    TradeActionResult,
    TradePage,
)
from .models import (
    BatchCookingItemViewModel,
    BatchCookingViewModel,
    CatalogItemViewModel,
    CatalogViewModel,
    CollectionProgressViewModel,
    EconomyReceiptRowViewModel,
    EconomyReceiptViewModel,
    FoodCardViewModel,
    FoodCatalogItemViewModel,
    FoodCatalogViewModel,
    FoodInventoryItemViewModel,
    FoodInventoryViewModel,
    GiantSightingViewModel,
    InventoryItemViewModel,
    InventoryViewModel,
    ItemReceiptViewModel,
    LedgerEntryViewModel,
    LedgerViewModel,
    PigCardViewModel,
    ProfileViewModel,
    RankingItemViewModel,
    RankingViewModel,
    RecordItemViewModel,
    RecordsViewModel,
    StoreConsumableProbabilityRowViewModel,
    StoreProbabilityRowViewModel,
    StoreProductViewModel,
    StoreViewModel,
    TradeListItemViewModel,
    TradeListViewModel,
)

_BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


def _display_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:19].replace("T", " ")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M")


def _collection_view(collection: object) -> CollectionProgressViewModel:
    return CollectionProgressViewModel(
        collection_name=str(collection.collection_name),
        collaboration_name=str(collection.collaboration_name),
        collected_count=int(collection.collected_count),
        available_count=int(collection.available_count),
        total_count=int(collection.total_count),
    )


def _probability_line(weights: Sequence[float]) -> str:
    """Format final rarity weights as a compact probability line."""

    def formatted(value: float) -> str:
        rounded_tenth = round(float(value), 1)
        if abs(float(value) - rounded_tenth) <= 0.0005:
            return f"{value:.1f}"
        return f"{value:.3f}"

    return " ".join(
        f"{index + 1}★{formatted(value)}%"
        for index, value in enumerate(weights)
        if value > 0
    )


def _probability_sources(
    *,
    player_level: int | None,
    feed_level: int | None,
    cookware_level: int | None,
    item_name: str,
    effect_count: int,
    exclusive_effect_active: bool = False,
) -> str:
    """Summarize every factor that shaped the final probability."""

    if exclusive_effect_active:
        return "六星菜独占规则（等级、升级、道具与其他菜品均未参与）"
    parts: list[str] = []
    if player_level:
        parts.append(f"等级 Lv.{player_level}")
    if feed_level is not None:
        parts.append(f"饲料 Lv.{feed_level}")
    if cookware_level is not None:
        parts.append(f"厨具 Lv.{cookware_level}")
    if item_name:
        parts.append(f"道具·{item_name}")
    if effect_count:
        parts.append(f"美食加成 ×{effect_count}")
    return "、".join(parts) if parts else "无额外加成"


def pig_card_view(
    pig: PigView,
    *,
    mode_label: str,
    catch: CatchResult | None = None,
) -> PigCardViewModel:
    """Build one catch or detail card view."""

    progress = level_progress(catch.total_experience) if catch is not None else None
    return PigCardViewModel(
        mode_label=mode_label,
        display_name=pig.display_name,
        owner_display_name=pig.owner_display_name,
        rarity=pig.rarity,
        rarity_name=pig.rarity_name,
        short_code=pig.short_code,
        description=pig.description,
        size_value=pig.size_value,
        size_percentile=pig.size_percentile,
        weight_value=pig.weight_value,
        weight_percentile=pig.weight_percentile,
        fat_ratio=pig.fat_ratio,
        fat_label=pig.fat_label,
        official_value=pig.official_value,
        acquired_at=_display_time(pig.acquired_at),
        image_fit=pig.image_fit,
        media_visible=pig.media_visible,
        is_animated=pig.is_animated,
        media_format=pig.media_format,
        collection_name=pig.collection_name,
        character_name=pig.character_name,
        coin_reward=catch.coin_reward if catch is not None else None,
        experience_reward=catch.experience_reward if catch is not None else None,
        coin_balance=catch.coin_balance if catch is not None else None,
        total_experience=catch.total_experience if catch is not None else None,
        player_level=progress.level if progress is not None else None,
        level_title=progress.title if progress is not None else "",
        next_level_experience=(progress.next_threshold if progress is not None else None),
        level_progress_percent=(progress.progress_percent if progress is not None else 0.0),
        daily_count=catch.daily_count if catch is not None else None,
        daily_limit=catch.daily_limit if catch is not None else None,
        quota_exempt_catch=catch.quota_exempt_catch if catch is not None else False,
        item_name=catch.item_name if catch is not None else "",
        catalog_new=catch.catalog_new if catch is not None else False,
        size_record=catch.size_record if catch is not None else pig.is_size_record,
        weight_record=catch.weight_record if catch is not None else pig.is_weight_record,
        body_label=pig.body_label,
        body_description=pig.body_description,
        giant_score=pig.giant_score,
        global_size_record=(catch.global_size_record if catch is not None else pig.is_global_size_record),
        global_weight_record=(catch.global_weight_record if catch is not None else pig.is_global_weight_record),
        giant_sighting=(catch.giant_sighting if catch is not None else pig.is_giant_sighting),
        size_label=size_label(pig.size_percentile),
        weight_label=weight_label(pig.weight_percentile),
        effect_summaries=(catch.effect_summaries if catch is not None else ()),
        excluded_summaries=(catch.excluded_summaries if catch is not None else ()),
        tutorial_text=("输入 /切换 猪保千 可在猪猪立绘与表情包之间切换" if pig.alternate_image_relpath else ""),
        probability_line=(_probability_line(catch.weights) if catch is not None else ""),
        probability_sources=(
            _probability_sources(
                player_level=progress.level if progress is not None else None,
                feed_level=catch.feed_level,
                cookware_level=None,
                item_name=catch.item_name,
                effect_count=len(catch.effect_summaries),
                exclusive_effect_active=catch.exclusive_effect_active,
            )
            if catch is not None
            else ""
        ),
    )


def profile_view(profile: PlayerProfile) -> ProfileViewModel:
    """Build a player profile rendering view."""

    return ProfileViewModel(
        display_name=profile.display_name,
        level=profile.level.level,
        title=profile.level.title,
        total_experience=profile.total_experience,
        next_threshold=profile.level.next_threshold,
        progress_percent=profile.level.progress_percent,
        coin_balance=profile.coin_balance,
        total_catches=profile.total_catches,
        active_pigs=profile.active_pigs,
        catalog_count=profile.catalog_count,
        visible_catalog_total=profile.visible_catalog_total,
        held_records=profile.held_records,
        daily_count=profile.daily_count,
        daily_limit=profile.daily_limit,
        cooldown_remaining_seconds=profile.cooldown_remaining_seconds,
        feed_level=profile.feed_level,
        armed_item_name=(profile.armed_item.display_name if profile.armed_item is not None else ""),
        armed_item_quantity=profile.armed_item_quantity,
        cookware_level=profile.cookware_level,
        total_cooks=profile.total_cooks,
        active_foods=profile.active_foods,
        food_catalog_count=profile.food_catalog_count,
        visible_food_catalog_total=profile.visible_food_catalog_total,
        armed_cooking_item_name=(
            profile.armed_cooking_item.display_name if profile.armed_cooking_item is not None else ""
        ),
        armed_cooking_item_quantity=profile.armed_cooking_item_quantity,
        collections=tuple(_collection_view(item) for item in profile.collections),
        showcase_pig=profile.showcase_pig,
        showcase_food=profile.showcase_food,
        level_catch_base_high_percent=profile.level_catch_base_high_percent,
        level_catch_adjusted_high_percent=(profile.level_catch_adjusted_high_percent),
        level_cooking_bonus_percent=profile.level_cooking_bonus_percent,
        level_bonus_cap_level=profile.level_bonus_cap_level,
    )


def inventory_view(page: InventoryPage) -> InventoryViewModel:
    """Build an inventory page rendering view."""

    return InventoryViewModel(
        display_name=page.display_name,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        rarity=page.rarity,
        sort=page.sort,
        items=tuple(
            InventoryItemViewModel(
                key=pig.pig_instance_id,
                display_name=pig.display_name,
                short_code=pig.short_code,
                rarity=pig.rarity,
                size_value=pig.size_value,
                weight_value=pig.weight_value,
                fat_label=pig.fat_label,
                official_value=pig.official_value,
                media_visible=pig.media_visible,
                is_animated=pig.is_animated,
                image_fit=pig.image_fit,
                body_label=pig.body_label,
            )
            for pig in page.pigs
        ),
    )


def catalog_view(page: CatalogPage) -> CatalogViewModel:
    """Build a privacy-aware complete catalog rendering view."""

    return CatalogViewModel(
        display_name=page.display_name,
        total_count=page.total_count,
        rarity=page.rarity,
        undiscovered_only=page.undiscovered_only,
        collected_count=page.collected_count,
        visible_catalog_total=page.visible_catalog_total,
        items=tuple(
            CatalogItemViewModel(
                key=entry.template_id,
                display_name=entry.display_name,
                rarity=entry.rarity,
                discovered=entry.discovered,
                acquired_count=entry.acquired_count,
                best_size=entry.best_size,
                best_weight=entry.best_weight,
                collection_name=entry.collection_name,
                character_name=entry.character_name,
                media_visible=entry.discovered,
                is_animated=entry.is_animated,
                image_fit=entry.image_fit,
            )
            for entry in page.entries
        ),
        collections=tuple(_collection_view(item) for item in page.collections),
    )


def records_view(page: RecordsPage) -> RecordsViewModel:
    """Build a current-group records rendering view."""

    return RecordsViewModel(
        group_name=page.group_name,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        items=tuple(
            RecordItemViewModel(
                record_label=entry.record_label,
                record_value=entry.record_value,
                unit=entry.unit,
                display_name=entry.display_name,
                rarity=entry.rarity,
                short_code=entry.short_code,
                holder_display_name=entry.holder_display_name,
                achieved_at=_display_time(entry.achieved_at),
            )
            for entry in page.entries
        ),
        global_items=tuple(
            RecordItemViewModel(
                record_label=entry.record_label,
                record_value=entry.record_value,
                unit=entry.unit,
                display_name=entry.display_name,
                rarity=entry.rarity,
                short_code=entry.short_code,
                holder_display_name=entry.holder_display_name,
                achieved_at=_display_time(entry.achieved_at),
            )
            for entry in page.global_entries
        ),
        giant_sightings=tuple(
            GiantSightingViewModel(
                display_name=entry.display_name,
                rarity=entry.rarity,
                short_code=entry.short_code,
                holder_display_name=entry.holder_display_name,
                size_value=entry.size_value,
                weight_value=entry.weight_value,
                giant_score=entry.giant_score,
                qualification_label=(
                    "双项巨物"
                    if entry.size_qualified and entry.weight_qualified
                    else "体型巨物"
                    if entry.size_qualified
                    else "重量巨物"
                ),
                achieved_at=_display_time(entry.achieved_at),
            )
            for entry in page.giant_sightings
        ),
    )


def item_receipt_view(result: ItemActionResult) -> ItemReceiptViewModel:
    """Build an item equip/cancellation receipt rendering view."""

    return ItemReceiptViewModel(
        operation=result.operation,
        item_name=result.item.display_name,
        action_label="抓猪" if result.item.action_type == "catching" else "做菜",
        quantity=result.quantity,
        effect_summary=result.item.effect_summary,
    )


def food_card_view(
    food: FoodView,
    *,
    mode_label: str,
    cooking: CookingResult | None = None,
) -> FoodCardViewModel:
    """Build one food detail or cooking-result card."""

    bonus_selector = ""
    if cooking is not None and cooking.bonus_serving and len(cooking.foods) > 1:
        bonus_selector = cooking.foods[1].selector
    progress = level_progress(cooking.total_experience) if cooking is not None else None
    return FoodCardViewModel(
        mode_label=mode_label,
        display_name=food.display_name,
        owner_display_name=food.owner_display_name,
        rarity=food.rarity,
        rarity_name=food.rarity_name,
        short_code=food.short_code,
        description=food.description,
        portion_weight=food.portion_weight,
        fat_label=food.fat_label,
        official_value=food.official_value,
        acquired_at=_display_time(food.acquired_at),
        source_selector=food.source_selector,
        effect_summary=effect_summary(food.effect_id, food.effect_params),
        image_fit=food.image_fit,
        media_visible=food.media_visible,
        is_animated=food.is_animated,
        media_format=food.media_format,
        coin_reward=cooking.coin_reward if cooking is not None else None,
        experience_reward=(cooking.experience_reward if cooking is not None else None),
        coin_balance=cooking.coin_balance if cooking is not None else None,
        total_experience=(cooking.total_experience if cooking is not None else None),
        player_level=progress.level if progress is not None else None,
        level_title=progress.title if progress is not None else "",
        next_level_experience=(progress.next_threshold if progress is not None else None),
        level_progress_percent=(progress.progress_percent if progress is not None else 0.0),
        cookware_level=(cooking.cookware_level if cooking is not None else None),
        item_name=cooking.item_name if cooking is not None else "",
        catalog_new_count=(cooking.catalog_new_count if cooking is not None else 0),
        bonus_selector=bonus_selector,
        probability_summary=(cooking.probability_summary if cooking is not None else ""),
        effect_summaries=(cooking.effect_summaries if cooking is not None else ()),
        excluded_summaries=(cooking.excluded_summaries if cooking is not None else ()),
        probability_line=(_probability_line(cooking.weights) if cooking is not None else ""),
        probability_sources=(
            _probability_sources(
                player_level=progress.level if progress is not None else None,
                feed_level=None,
                cookware_level=(cooking.cookware_level if cooking is not None else None),
                item_name=cooking.item_name if cooking is not None else "",
                effect_count=(len(cooking.effect_summaries) if cooking is not None else 0),
                exclusive_effect_active=(cooking.exclusive_effect_active if cooking is not None else False),
            )
            if cooking is not None
            else ""
        ),
    )


def food_inventory_view(page: FoodInventoryPage) -> FoodInventoryViewModel:
    """Build one food inventory rendering view."""

    return FoodInventoryViewModel(
        display_name=page.display_name,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        rarity=page.rarity,
        sort=page.sort,
        items=tuple(
            FoodInventoryItemViewModel(
                key=food.food_instance_id,
                display_name=food.display_name,
                short_code=food.short_code,
                rarity=food.rarity,
                portion_weight=food.portion_weight,
                fat_label=food.fat_label,
                official_value=food.official_value,
                media_visible=food.media_visible,
                is_animated=food.is_animated,
                image_fit=food.image_fit,
            )
            for food in page.foods
        ),
    )


def food_catalog_view(page: FoodCatalogPage) -> FoodCatalogViewModel:
    """Build one privacy-aware complete food catalog rendering view."""

    return FoodCatalogViewModel(
        display_name=page.display_name,
        total_count=page.total_count,
        rarity=page.rarity,
        undiscovered_only=page.undiscovered_only,
        collected_count=page.collected_count,
        visible_catalog_total=page.visible_catalog_total,
        items=tuple(
            FoodCatalogItemViewModel(
                key=entry.template_id,
                display_name=entry.display_name,
                rarity=entry.rarity,
                discovered=entry.discovered,
                acquired_count=entry.acquired_count,
                best_portion_weight=entry.best_portion_weight,
                media_visible=entry.discovered,
                is_animated=entry.is_animated,
                image_fit=entry.image_fit,
                effect_summary=(
                    effect_summary(entry.effect_id, entry.effect_params) if entry.discovered and entry.effect_id else ""
                ),
            )
            for entry in page.entries
        ),
    )


def store_view(page: StorePage) -> StoreViewModel:
    """Build one store rendering view."""

    feed_probability_rows = tuple(
        StoreProbabilityRowViewModel(
            level=level,
            value=f"{high_probability:.2f}%",
            delta=" · ".join(
                f"{rarity}★{weights[rarity - 1]:.2f}"
                for rarity in range(4, 7)
            ),
            current=level == page.feed_level,
        )
        for level in range(6)
        for weights in (catch_weights(page.catch_base_weights, feed_level=level),)
        for high_probability in (sum(weights[3:]),)
    )
    cookware_probability_rows = tuple(
        StoreProbabilityRowViewModel(
            level=level,
            value=(f"+{(cookware_higher_rarity_multiplier(level) - 1.0) * 100.0:.0f}%"),
            delta="相对权重",
            current=level == page.cookware_level,
        )
        for level in range(6)
    )
    lucky_before = catch_weights(page.catch_base_weights)
    lucky_after = catch_weights(page.catch_base_weights, lucky_whistle=True)
    lucky_whistle_rows = tuple(
        StoreConsumableProbabilityRowViewModel(
            label=f"{rarity} 星",
            before=f"{before:.2f}%",
            after=f"{after:.2f}%",
        )
        for rarity, before, after in zip(
            range(1, 7),
            lucky_before,
            lucky_after,
            strict=True,
        )
    )
    super_lucky_after = catch_weights(
        page.catch_base_weights,
        item_id="super-lucky-whistle",
    )
    super_lucky_whistle_rows = tuple(
        StoreConsumableProbabilityRowViewModel(
            label=f"{rarity} 星",
            before=f"{before:.2f}%",
            after=f"{after:.2f}%",
        )
        for rarity, before, after in zip(
            range(1, 7),
            lucky_before,
            super_lucky_after,
            strict=True,
        )
    )
    star_radar_after = catch_weights(
        page.catch_base_weights,
        item_id="star-pig-radar",
    )
    star_pig_radar_rows = tuple(
        StoreConsumableProbabilityRowViewModel(
            label=f"{rarity} 星",
            before=f"{before:.2f}%",
            after=f"{after:.2f}%",
        )
        for rarity, before, after in zip(
            range(1, 7),
            lucky_before,
            star_radar_after,
            strict=True,
        )
    )
    chef_spice_rows = tuple(
        StoreConsumableProbabilityRowViewModel(
            label=f"{rarity} 星猪",
            before=_probability_distribution(cooking_weights(rarity)),
            after=_probability_distribution(
                adjusted_cooking_weights(
                    rarity,
                    size_percentile=0.0,
                    weight_percentile=0.0,
                    cookware_level=0,
                    player_level=1,
                    chef_spice=True,
                )
            ),
        )
        for rarity in range(1, 6)
    )
    super_chef_spice_rows = (
        StoreConsumableProbabilityRowViewModel(
            label="6 星猪",
            before=_probability_distribution(cooking_weights(6)),
            after=_probability_distribution(
                adjusted_cooking_weights(
                    6,
                    size_percentile=0.0,
                    weight_percentile=0.0,
                    cookware_level=0,
                    player_level=1,
                    chef_spice=False,
                    item_id="super-chef-spice",
                )
            ),
        ),
    )
    return StoreViewModel(
        display_name=page.display_name,
        coin_balance=page.coin_balance,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        category=page.category,
        feed_level=page.feed_level,
        cookware_level=page.cookware_level,
        feed_probability_rows=feed_probability_rows,
        cookware_probability_rows=cookware_probability_rows,
        lucky_whistle_rows=lucky_whistle_rows,
        super_lucky_whistle_rows=super_lucky_whistle_rows,
        star_pig_radar_rows=star_pig_radar_rows,
        chef_spice_rows=chef_spice_rows,
        super_chef_spice_rows=super_chef_spice_rows,
        products=tuple(
            StoreProductViewModel(
                display_name=product.display_name,
                category=product.category,
                unit_price=product.unit_price,
                effect_summary=product.effect_summary,
                current_level=product.current_level,
                target_level=product.target_level,
                command=(
                    f"/升级 {'猪饲料' if product.product_id == 'upgrade-feed' else '厨具'}"
                    if product.product_type == "upgrade"
                    else f"/购买 {product.display_name}"
                ),
            )
            for product in page.products
        ),
    )


def _probability_distribution(weights: tuple[float, ...]) -> str:
    """Compactly format only reachable rarity outcomes."""

    return " · ".join(f"{rarity}★ {value:.0f}%" for rarity, value in enumerate(weights, start=1) if value > 0)


def purchase_receipt_view(result: PurchaseResult) -> EconomyReceiptViewModel:
    """Build a purchase receipt."""

    acquired = (
        f"当前等级 Lv.{result.upgrade_level}"
        if result.product_type == "upgrade"
        else f"当前库存 {result.inventory_quantity}"
    )
    return EconomyReceiptViewModel(
        eyebrow="猪猪商城 · 原子扣款与发货",
        title="购买成功",
        badge_label="剩余猪币",
        badge_value=str(result.balance_after),
        summary=f"{result.display_name} ×{result.quantity}",
        rows=(
            EconomyReceiptRowViewModel("单价", f"{result.unit_price} 猪币"),
            EconomyReceiptRowViewModel("本次支付", f"{result.total_price} 猪币"),
            EconomyReceiptRowViewModel("获得", acquired),
        ),
        note="同一消息重复投递不会再次扣款或发货。",
    )


def batch_sale_receipt_view(result: BatchSaleResult) -> EconomyReceiptViewModel:
    """Build one low-rarity batch-sale receipt."""

    kind = "猪猪" if result.asset_kind == "pig" else "美食"
    scope = f"{result.rarity} 星{kind}" if result.rarity is not None else f"1 至 {result.max_rarity} 星{kind}"
    return EconomyReceiptViewModel(
        eyebrow="官方回收 · 原子批量结算",
        title="批量售卖成功",
        badge_label="当前余额",
        badge_value=str(result.balance_after),
        summary=f"{scope} ×{result.asset_count}",
        rows=(
            EconomyReceiptRowViewModel("售出数量", f"{result.asset_count} 件"),
            EconomyReceiptRowViewModel("本次收入", f"{result.total_value} 猪币"),
            EconomyReceiptRowViewModel("处理范围", scope),
        ),
        note="联动猪与交易锁定资产未被处理；历史图鉴不会减少。",
    )


def batch_cook_view(result: BatchCookingResult) -> BatchCookingViewModel:
    """Build one grid of batch-cooked foods ordered by rarity descending."""

    items = tuple(
        BatchCookingItemViewModel(
            key=food.food_instance_id,
            display_name=food.display_name,
            short_code=food.short_code,
            rarity=food.rarity,
            portion_weight=food.portion_weight,
            fat_label=food.fat_label,
            official_value=food.official_value,
            media_visible=True,
            is_animated=food.is_animated,
            image_fit=food.image_fit,
        )
        for food in sorted(result.foods, key=lambda item: (-item.rarity, item.acquired_at))
    )
    return BatchCookingViewModel(
        display_name=result.source_pigs[0].owner_display_name if result.source_pigs else "",
        pig_count=result.pig_count,
        food_count=result.food_count,
        coin_reward=result.coin_reward,
        experience_reward=result.experience_reward,
        catalog_new_count=result.catalog_new_count,
        rarity=result.rarity,
        items=items,
    )


def eat_receipt_view(result: EatResult) -> EconomyReceiptViewModel:
    """Build a food-consumption receipt."""

    experience = result.base_experience + result.effect.experience_bonus
    rows = [
        EconomyReceiptRowViewModel("品鉴经验", f"+{experience}"),
        EconomyReceiptRowViewModel("累计经验", str(result.total_experience)),
    ]
    if result.effect.coin_bonus:
        rows.append(EconomyReceiptRowViewModel("额外猪币", f"+{result.effect.coin_bonus}"))
    return EconomyReceiptViewModel(
        eyebrow="美食品鉴 · 成功后消耗一份",
        title="开饭啦",
        badge_label="当前猪币",
        badge_value=str(result.coin_balance),
        summary=f"{result.food.stars} {result.food.selector}",
        rows=tuple(rows),
        note=result.effect.summary,
    )


def sale_receipt_view(result: SaleResult) -> EconomyReceiptViewModel:
    """Build an official-sale receipt."""

    kind = "猪猪" if result.asset_kind == "pig" else "美食"
    return EconomyReceiptViewModel(
        eyebrow=f"官方售卖 · {kind}已离开背包",
        title="售卖成功",
        badge_label="当前猪币",
        badge_value=str(result.balance_after),
        summary=f"{'★' * result.rarity} {result.selector}",
        rows=(
            EconomyReceiptRowViewModel("官方价值", f"{result.official_value} 猪币"),
            EconomyReceiptRowViewModel("到账", f"+{result.official_value} 猪币"),
            EconomyReceiptRowViewModel("图鉴", "已解锁记录保留"),
        ),
        note="售卖不可撤销；同一消息重复投递不会重复到账。",
    )


def ledger_view(page: LedgerPage) -> LedgerViewModel:
    """Build one reconciled ledger rendering view."""

    return LedgerViewModel(
        display_name=page.display_name,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        coin_balance=page.coin_balance,
        ledger_total=page.ledger_total,
        items=tuple(
            LedgerEntryViewModel(
                amount_text=f"{'+' if entry.amount > 0 else ''}{entry.amount}",
                positive=entry.amount > 0,
                balance_after=entry.balance_after,
                reason_text=entry.reason_text,
                created_at=_display_time(entry.created_at),
            )
            for entry in page.entries
        ),
    )


def gift_receipt_view(result: GiftResult) -> EconomyReceiptViewModel:
    """Build a rendered immediate-gift receipt."""

    return EconomyReceiptViewModel(
        eyebrow="群内赠送 · 原子转移",
        title="赠送完成",
        badge_label=result.asset.kind_label,
        badge_value="★" * result.asset.rarity,
        summary=(f"{result.sender_display_name} 将 {result.asset.selector} 赠送给 {result.recipient_display_name}"),
        rows=(
            EconomyReceiptRowViewModel("接收方", result.recipient_display_name),
            EconomyReceiptRowViewModel("资产属性", result.asset.detail_text),
            EconomyReceiptRowViewModel("官方价值", f"{result.asset.official_value} 猪币"),
        ),
        note="赠送不产生猪币或经验，资产已在当前群内完成转移。",
    )


def trade_receipt_view(result: TradeActionResult) -> EconomyReceiptViewModel:
    """Build a rendered offer or bilateral-confirmation receipt."""

    title = {
        "created": "交易报价已创建",
        "accepted": "双方交易已完成",
        "rejected": "交易已拒绝",
        "cancelled": "交易已取消",
    }.get(result.operation, f"交易{result.trade.status_label}")
    rows = [
        EconomyReceiptRowViewModel("交易号", result.trade.trade_id),
        EconomyReceiptRowViewModel(
            "交易双方",
            f"{result.trade.sender_display_name} → {result.trade.recipient_display_name}",
        ),
        EconomyReceiptRowViewModel("交易价格", f"{result.trade.price} 猪币"),
        EconomyReceiptRowViewModel("有效期至", _display_time(result.trade.expires_at)),
    ]
    if result.buyer_balance is not None and result.seller_balance is not None:
        rows.append(
            EconomyReceiptRowViewModel(
                "成交余额",
                f"买方 {result.buyer_balance} / 卖方 {result.seller_balance}",
            )
        )
    return EconomyReceiptViewModel(
        eyebrow="双方确认交易 · 当前群",
        title=title,
        badge_label=result.trade.status_label,
        badge_value=result.trade.trade_id,
        summary=f"{'★' * result.trade.asset.rarity} {result.trade.asset.selector}",
        rows=tuple(rows),
        note=(
            "接收方使用 /接受交易 交易号 完成付款；未完成报价会在五分钟后自动解锁。"
            if result.operation == "created"
            else "交易状态已原子写入，重复命令不会再次转移资产或猪币。"
        ),
    )


def showcase_receipt_view(result: ShowcaseResult) -> EconomyReceiptViewModel:
    """Build a rendered showcase-slot update receipt."""

    kind_label = "猪猪" if result.asset_kind.value == "pig" else "美食"
    summary = (
        f"已取消{kind_label}展示位"
        if result.cleared
        else f"已展示 {result.asset.selector if result.asset is not None else ''}"
    )
    return EconomyReceiptViewModel(
        eyebrow="个人展示位 · 当前群",
        title="展示位已更新",
        badge_label=kind_label,
        badge_value="已取消" if result.cleared else "已设置",
        summary=summary,
        rows=(
            EconomyReceiptRowViewModel(
                "展示资产",
                result.asset.selector if result.asset is not None else "无",
            ),
            EconomyReceiptRowViewModel(
                "展示品质",
                "★" * result.asset.rarity if result.asset is not None else "无",
            ),
        ),
        note="排行榜会优先展示该资产；取消后自动回退到当前持有的高价值资产。",
    )


def trade_list_view(page: TradePage) -> TradeListViewModel:
    """Build a current-player bilateral trade list."""

    return TradeListViewModel(
        display_name=page.display_name,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        status_label=(TRADE_STATUS_LABELS[page.status] if page.status is not None else "全部"),
        items=tuple(
            TradeListItemViewModel(
                trade_id=entry.trade_id,
                status_label=entry.status_label,
                asset_name=entry.asset.display_name,
                asset_code=entry.asset.short_code,
                rarity=entry.asset.rarity,
                price=entry.price,
                sender_name=entry.sender_display_name,
                recipient_name=entry.recipient_display_name,
                expires_at=_display_time(entry.expires_at),
            )
            for entry in page.entries
        ),
    )


def _ranking_showcase(
    entry: RankingEntry,
    ranking_type: str,
) -> ShowcaseAsset | None:
    if ranking_type == "美食":
        return entry.showcase_food or entry.showcase_pig
    if ranking_type == "巨物":
        return entry.giant_pig or entry.showcase_pig
    return entry.showcase_pig or entry.showcase_food


def ranking_view(page: RankingPage) -> RankingViewModel:
    """Build an original, compact white-and-pink leaderboard."""

    items: list[RankingItemViewModel] = []
    for entry in page.entries:
        showcase = _ranking_showcase(entry, page.ranking_type)
        items.append(
            RankingItemViewModel(
                key=entry.player_id,
                rank=entry.rank,
                display_name=entry.display_name,
                metric_text=entry.metric_text,
                pig_progress=f"{entry.pig_catalog_count}/{entry.pig_catalog_total}",
                food_progress=f"{entry.food_catalog_count}/{entry.food_catalog_total}",
                asset_count=entry.active_pigs + entry.active_foods,
                coin_balance=entry.coin_balance,
                showcase_name=showcase.display_name if showcase is not None else "",
                showcase_detail=showcase.detail_text if showcase is not None else "",
                showcase_rarity=showcase.rarity if showcase is not None else 0,
                showcase_kind=(
                    "猪猪"
                    if showcase is not None and showcase.asset_kind.value == "pig"
                    else "美食"
                    if showcase is not None
                    else ""
                ),
                media_visible=bool(showcase and showcase.media_visible),
                is_animated=bool(showcase and showcase.is_animated),
                image_fit=showcase.image_fit if showcase is not None else "contain",
            )
        )
    return RankingViewModel(
        group_name=page.group_name,
        ranking_type=page.ranking_type,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        items=tuple(items),
    )


def media_path(data_dir: Path, relative_path: str) -> Path:
    """Resolve a persisted media path without allowing data-dir escape."""

    root = Path(data_dir).resolve()
    normalized = Path(str(relative_path or ""))
    candidate = (root / normalized).resolve()
    if not relative_path or normalized.is_absolute() or candidate == root or not candidate.is_relative_to(root):
        raise RenderError("素材路径不在插件数据目录内。")
    return candidate


def pig_media_path(data_dir: Path, pig: PigView) -> Path | None:
    """Resolve one visible pig's source media."""

    if not pig.media_visible:
        return None
    return media_path(data_dir, pig.image_relpath)


def food_media_path(data_dir: Path, food: FoodView) -> Path | None:
    """Resolve one visible food's source media."""

    if not food.media_visible:
        return None
    return media_path(data_dir, food.image_relpath)


def inventory_media_paths(data_dir: Path, page: InventoryPage) -> dict[str, Path]:
    """Resolve visible inventory media; GIFs are previewed by the renderer."""

    return {pig.pig_instance_id: media_path(data_dir, pig.image_relpath) for pig in page.pigs if pig.media_visible}


def catalog_media_paths(data_dir: Path, page: CatalogPage) -> dict[str, Path]:
    """Resolve discovered catalog media, including animated sources."""

    return {entry.template_id: media_path(data_dir, entry.image_relpath) for entry in page.entries if entry.discovered}


def food_inventory_media_paths(
    data_dir: Path,
    page: FoodInventoryPage,
) -> dict[str, Path]:
    """Resolve visible food inventory media, including animated sources."""

    return {
        food.food_instance_id: media_path(data_dir, food.image_relpath) for food in page.foods if food.media_visible
    }


def food_catalog_media_paths(
    data_dir: Path,
    page: FoodCatalogPage,
) -> dict[str, Path]:
    """Resolve discovered food catalog media, including animated sources."""

    return {entry.template_id: media_path(data_dir, entry.image_relpath) for entry in page.entries if entry.discovered}


def ranking_media_paths(
    data_dir: Path,
    page: RankingPage,
) -> dict[str, Path]:
    """Resolve leaderboard media; GIFs use a deterministic preview frame."""

    result: dict[str, Path] = {}
    for entry in page.entries:
        showcase = _ranking_showcase(entry, page.ranking_type)
        if showcase is None or not showcase.media_visible:
            continue
        result[entry.player_id] = media_path(data_dir, showcase.image_relpath)
    return result
