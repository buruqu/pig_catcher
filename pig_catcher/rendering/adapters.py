"""Map application DTOs to path-safe rendering view models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from ..domain.errors import RenderError
from ..services.economy import (
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
from .models import (
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
    InventoryItemViewModel,
    InventoryViewModel,
    ItemReceiptViewModel,
    LedgerEntryViewModel,
    LedgerViewModel,
    PigCardViewModel,
    ProfileViewModel,
    RecordItemViewModel,
    RecordsViewModel,
    StoreProductViewModel,
    StoreViewModel,
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


def pig_card_view(
    pig: PigView,
    *,
    mode_label: str,
    catch: CatchResult | None = None,
) -> PigCardViewModel:
    """Build one catch or detail card view."""

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
        daily_count=catch.daily_count if catch is not None else None,
        daily_limit=catch.daily_limit if catch is not None else None,
        item_name=catch.item_name if catch is not None else "",
        catalog_new=catch.catalog_new if catch is not None else False,
        size_record=catch.size_record if catch is not None else pig.is_size_record,
        weight_record=catch.weight_record if catch is not None else pig.is_weight_record,
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
        armed_item_name=(
            profile.armed_item.display_name if profile.armed_item is not None else ""
        ),
        armed_item_quantity=profile.armed_item_quantity,
        cookware_level=profile.cookware_level,
        total_cooks=profile.total_cooks,
        active_foods=profile.active_foods,
        food_catalog_count=profile.food_catalog_count,
        visible_food_catalog_total=profile.visible_food_catalog_total,
        armed_cooking_item_name=(
            profile.armed_cooking_item.display_name
            if profile.armed_cooking_item is not None
            else ""
        ),
        armed_cooking_item_quantity=profile.armed_cooking_item_quantity,
        collections=tuple(_collection_view(item) for item in profile.collections),
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
            )
            for pig in page.pigs
        ),
    )


def catalog_view(page: CatalogPage) -> CatalogViewModel:
    """Build a privacy-aware catalog page rendering view."""

    return CatalogViewModel(
        display_name=page.display_name,
        page=page.page,
        page_count=page.page_count,
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
        effect_summary=food.effect_id or "暂无额外效果",
        image_fit=food.image_fit,
        media_visible=food.media_visible,
        is_animated=food.is_animated,
        media_format=food.media_format,
        coin_reward=cooking.coin_reward if cooking is not None else None,
        experience_reward=(
            cooking.experience_reward if cooking is not None else None
        ),
        coin_balance=cooking.coin_balance if cooking is not None else None,
        total_experience=(
            cooking.total_experience if cooking is not None else None
        ),
        cookware_level=(
            cooking.cookware_level if cooking is not None else None
        ),
        item_name=cooking.item_name if cooking is not None else "",
        catalog_new_count=(
            cooking.catalog_new_count if cooking is not None else 0
        ),
        bonus_selector=bonus_selector,
        probability_summary=(
            cooking.probability_summary if cooking is not None else ""
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
    """Build one privacy-aware food catalog rendering view."""

    return FoodCatalogViewModel(
        display_name=page.display_name,
        page=page.page,
        page_count=page.page_count,
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
            )
            for entry in page.entries
        ),
    )


def store_view(page: StorePage) -> StoreViewModel:
    """Build one store rendering view."""

    return StoreViewModel(
        display_name=page.display_name,
        coin_balance=page.coin_balance,
        page=page.page,
        page_count=page.page_count,
        total_count=page.total_count,
        category=page.category,
        feed_level=page.feed_level,
        cookware_level=page.cookware_level,
        products=tuple(
            StoreProductViewModel(
                display_name=product.display_name,
                category=product.category,
                unit_price=product.unit_price,
                effect_summary=product.effect_summary,
                current_level=product.current_level,
                target_level=product.target_level,
            )
            for product in page.products
        ),
    )


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


def eat_receipt_view(result: EatResult) -> EconomyReceiptViewModel:
    """Build a food-consumption receipt."""

    experience = result.base_experience + result.effect.experience_bonus
    rows = [
        EconomyReceiptRowViewModel("品鉴经验", f"+{experience}"),
        EconomyReceiptRowViewModel("累计经验", str(result.total_experience)),
    ]
    if result.effect.coin_bonus:
        rows.append(
            EconomyReceiptRowViewModel("额外猪币", f"+{result.effect.coin_bonus}")
        )
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


def media_path(data_dir: Path, relative_path: str) -> Path:
    """Resolve a persisted media path without allowing data-dir escape."""

    root = Path(data_dir).resolve()
    normalized = Path(str(relative_path or ""))
    candidate = (root / normalized).resolve()
    if (
        not relative_path
        or normalized.is_absolute()
        or candidate == root
        or not candidate.is_relative_to(root)
    ):
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
    """Resolve only static visible inventory media."""

    return {
        pig.pig_instance_id: media_path(data_dir, pig.image_relpath)
        for pig in page.pigs
        if pig.media_visible and not pig.is_animated
    }


def catalog_media_paths(data_dir: Path, page: CatalogPage) -> dict[str, Path]:
    """Resolve only discovered static catalog media."""

    return {
        entry.template_id: media_path(data_dir, entry.image_relpath)
        for entry in page.entries
        if entry.discovered and not entry.is_animated
    }


def food_inventory_media_paths(
    data_dir: Path,
    page: FoodInventoryPage,
) -> dict[str, Path]:
    """Resolve only static visible food inventory media."""

    return {
        food.food_instance_id: media_path(data_dir, food.image_relpath)
        for food in page.foods
        if food.media_visible and not food.is_animated
    }


def food_catalog_media_paths(
    data_dir: Path,
    page: FoodCatalogPage,
) -> dict[str, Path]:
    """Resolve only discovered static food catalog media."""

    return {
        entry.template_id: media_path(data_dir, entry.image_relpath)
        for entry in page.entries
        if entry.discovered and not entry.is_animated
    }
