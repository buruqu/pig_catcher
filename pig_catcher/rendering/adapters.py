"""Map application DTOs to path-safe rendering view models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from ..domain.errors import RenderError
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
    InventoryItemViewModel,
    InventoryViewModel,
    ItemReceiptViewModel,
    PigCardViewModel,
    ProfileViewModel,
    RecordItemViewModel,
    RecordsViewModel,
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
