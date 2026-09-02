"""Map application DTOs to path-safe rendering view models."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from ..domain.achievements import TIER_LABELS, AchievementReward
from ..domain.economy import (
    adjusted_cooking_weights,
    cookware_higher_rarity_multiplier,
)
from ..domain.errors import RenderError
from ..domain.food_effects import (
    GROUP_COIN_TRIBUTE,
    GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH,
    QUOTA_RESET_CHANCE,
    ROULETTE_CHANCES,
    TECHNIQUE_PERMIT,
    effect_summary,
)
from ..domain.gameplay import level_progress, size_label, weight_label
from ..domain.rules import catch_weights, cooking_weights
from ..domain.social import TRADE_STATUS_LABELS
from ..domain.special_content import (
    GOJO_PIG_TEMPLATE_ID,
    TECHNIQUE_DISPLAY_NAMES,
    TECHNIQUE_HOLLOW_PURPLE,
    TECHNIQUE_LAPSE_BLUE,
    TECHNIQUE_MALEVOLENT_KITCHEN,
    TECHNIQUE_REVERSAL_RED,
)
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
    RouletteResult,
    SaleResult,
    StorePage,
)
from ..services.gameplay import (
    CatalogPage,
    CatchResult,
    DailyGiantEntry,
    DailyGiants,
    InventoryPage,
    ItemActionResult,
    PigView,
    PlayerProfile,
    RecordsPage,
    TechniqueActivationResult,
    TechniqueFoodView,
)
from ..services.quota import CatchQuotaResetResult
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
    AchievementBackfillSummaryViewModel,
    AchievementOverviewViewModel,
    AchievementPageViewModel,
    AchievementRankingRowViewModel,
    AchievementRankingViewModel,
    AchievementRowViewModel,
    AchievementUnlockViewModel,
    BatchCookingItemViewModel,
    BatchCookingViewModel,
    CatalogItemViewModel,
    CatalogViewModel,
    CollectionProgressViewModel,
    DailyGiantItemViewModel,
    DailyGiantsViewModel,
    EconomyReceiptRowViewModel,
    EconomyReceiptViewModel,
    FoodCardViewModel,
    FoodCatalogItemViewModel,
    FoodCatalogViewModel,
    FoodInventoryItemViewModel,
    FoodInventoryViewModel,
    GiantSightingViewModel,
    GroupEventAssetViewModel,
    GroupEventRowViewModel,
    GroupEventViewModel,
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
    WeeklyCompetitionAwardViewModel,
    WeeklyCompetitionRowViewModel,
    WeeklyCompetitionViewModel,
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


def _public_actor_name(*, display_name: str, stable_id: str) -> str:
    """Use the QQ nickname in public cards; hide a raw ID fallback."""

    normalized = str(display_name or "").strip()
    platform_user_id = str(stable_id or "").rsplit(":", 1)[-1]
    if not normalized or normalized == platform_user_id:
        return "未命名群友"
    return normalized


def _paired_star_multiplier(*, five_star: float, six_star: float) -> str:
    if five_star == six_star:
        return f"5★ / 6★ ×{five_star:g}"
    return f"5★ ×{five_star:g} / 6★ ×{six_star:g}"


def _veteran_reward_note(result: CatchResult | EatResult) -> str:
    """Build one compact milestone note for special event cards."""

    if not result.veteran_coin_reward:
        return ""
    levels = "、".join(f"Lv.{level}" for level in result.veteran_reward_levels)
    return f"；资深里程碑 {levels} 已一次性发放 +{result.veteran_coin_reward:,} 猪币"


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

    return " ".join(f"{index + 1}★{formatted(value)}%" for index, value in enumerate(weights) if value > 0)


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
    tutorial_text = ""
    if pig.media_visible and pig.alternate_image_relpath:
        variants = {
            "保千猪": ("猪保千", "猪猪立绘与表情包"),
            "初华猪": ("初华猪", "普通版与戴帽子版"),
        }
        if pig.display_name in variants:
            command_name, variant_names = variants[pig.display_name]
            selector = f" {pig.short_code}" if pig.short_code else ""
            tutorial_text = f"输入 /切换 {command_name}{selector} 可在{variant_names}之间切换"
    return PigCardViewModel(
        mode_label=mode_label,
        display_name=pig.display_name,
        activity_label=pig.activity_label,
        display_tags=pig.display_tags if pig.media_visible else (),
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
        veteran_coin_reward=(catch.veteran_coin_reward if catch is not None else 0),
        veteran_reward_levels=(catch.veteran_reward_levels if catch is not None else ()),
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
        item_remaining_uses=(catch.item_remaining_uses if catch is not None else 0),
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
        tutorial_text=tutorial_text,
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
        achievement_firework=(catch is not None and any("成就礼花券" in summary for summary in catch.effect_summaries)),
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
        veteran_tier=profile.veteran_tier,
        veteran_catch_coin_bonus=profile.veteran_catch_coin_bonus,
        veteran_cook_coin_bonus=profile.veteran_cook_coin_bonus,
        veteran_experience_bonus_percent=profile.veteran_experience_bonus_percent,
        veteran_milestone_coin_reward=profile.veteran_milestone_coin_reward,
        veteran_cumulative_coin_reward=profile.veteran_cumulative_coin_reward,
        veteran_claimed_tier=profile.veteran_claimed_tier,
        veteran_next_tier_level=profile.veteran_next_tier_level,
        veteran_next_tier_coin_reward=profile.veteran_next_tier_coin_reward,
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
                is_favorite=pig.is_favorite,
                activity_label=pig.activity_label,
                display_tags=pig.display_tags if pig.media_visible else (),
                # 只标注已存百分位，沿用成就/体格的0.92/0.88与0.08/0.15口径。
                # 这是物理特征，不代表成就已达成；绝对双项巨物仍单独显示。
                extreme_label=(
                    "双顶壮硕"
                    if pig.media_visible and pig.size_percentile >= 0.92 and pig.weight_percentile >= 0.88
                    else "双顶迷你"
                    if pig.media_visible and pig.size_percentile <= 0.08 and pig.weight_percentile <= 0.15
                    else ""
                ),
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
                display_tags=entry.display_tags if entry.discovered else (),
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
                player_id=entry.player_id,
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
                player_id=entry.player_id,
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
                player_id=entry.player_id,
            )
            for entry in page.giant_sightings
        ),
    )


def daily_giants_view(result: DailyGiants) -> DailyGiantsViewModel:
    """Build the two-list view for today's size and weight leaders."""

    def item_view(entry: DailyGiantEntry, *, metric: str) -> DailyGiantItemViewModel:
        return DailyGiantItemViewModel(
            key=f"{metric}:{entry.pig_instance_id}",
            rank=entry.rank,
            holder_display_name=entry.holder_display_name,
            display_name=entry.display_name,
            rarity=entry.rarity,
            short_code=entry.short_code,
            size_value=entry.size_value,
            weight_value=entry.weight_value,
            acquired_at=_display_time(entry.acquired_at),
            media_visible=entry.media_visible,
            is_animated=entry.is_animated,
            image_fit=entry.image_fit,
            player_id=entry.player_id,
        )

    return DailyGiantsViewModel(
        group_name=result.group_name,
        date_label=result.date_label,
        participant_count=result.participant_count,
        catch_count=result.catch_count,
        size_items=tuple(item_view(entry, metric="size") for entry in result.size_entries),
        weight_items=tuple(item_view(entry, metric="weight") for entry in result.weight_entries),
    )


def item_receipt_view(result: ItemActionResult) -> ItemReceiptViewModel:
    """Build an item equip/cancellation receipt rendering view."""

    return ItemReceiptViewModel(
        operation=result.operation,
        item_name=result.item.display_name,
        action_label="抓猪" if result.item.action_type == "catching" else "做菜",
        quantity=result.quantity,
        armed_uses=result.armed_uses,
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
        veteran_coin_reward=(cooking.veteran_coin_reward if cooking is not None else 0),
        veteran_reward_levels=(cooking.veteran_reward_levels if cooking is not None else ()),
        coin_balance=cooking.coin_balance if cooking is not None else None,
        total_experience=(cooking.total_experience if cooking is not None else None),
        player_level=progress.level if progress is not None else None,
        level_title=progress.title if progress is not None else "",
        next_level_experience=(progress.next_threshold if progress is not None else None),
        level_progress_percent=(progress.progress_percent if progress is not None else 0.0),
        cookware_level=(cooking.cookware_level if cooking is not None else None),
        item_name=cooking.item_name if cooking is not None else "",
        item_remaining_uses=(cooking.item_remaining_uses if cooking is not None else 0),
        catalog_new_count=(cooking.catalog_new_count if cooking is not None else 0),
        bonus_selector=bonus_selector,
        probability_summary=(cooking.probability_summary if cooking is not None else ""),
        effect_summaries=(cooking.effect_summaries if cooking is not None else ()),
        achievement_firework=(
            cooking is not None and any("成就礼花券" in summary for summary in cooking.effect_summaries)
        ),
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
                is_favorite=food.is_favorite,
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

    show_main_probabilities = page.shop_section == "主商城"
    feed_probability_rows = tuple(
        StoreProbabilityRowViewModel(
            level=level,
            value=f"{high_probability:.2f}%",
            delta=" · ".join(f"{rarity}★{weights[rarity - 1]:.2f}" for rarity in range(4, 7)),
            current=level == page.feed_level,
        )
        for level in range(11)
        for weights in (catch_weights(page.catch_base_weights, feed_level=level),)
        for high_probability in (sum(weights[3:]),)
    ) if show_main_probabilities else ()
    cookware_probability_rows = tuple(
        StoreProbabilityRowViewModel(
            level=level,
            value=(f"+{(cookware_higher_rarity_multiplier(level) - 1.0) * 100.0:.0f}%"),
            delta="相对权重",
            current=level == page.cookware_level,
        )
        for level in range(11)
    ) if show_main_probabilities else ()
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
        shop_section=page.shop_section,
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
    scope = (
        f"同名美食“{result.display_name}”"
        if result.display_name
        else (f"{result.rarity} 星{kind}" if result.rarity is not None else f"1 至 {result.max_rarity} 星{kind}")
    )
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
        note="收藏保护、联动保留与交易锁定资产未被处理；历史图鉴不会减少。",
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
        veteran_coin_reward=result.veteran_coin_reward,
        veteran_reward_levels=result.veteran_reward_levels,
        catalog_new_count=result.catalog_new_count,
        rarity=result.rarity,
        items=items,
        item_use_summaries=result.item_use_summaries,
        effect_use_summaries=result.effect_use_summaries,
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
    if result.veteran_coin_reward:
        levels = "、".join(f"Lv.{level}" for level in result.veteran_reward_levels)
        rows.append(
            EconomyReceiptRowViewModel(
                "资深里程碑",
                f"{levels} · +{result.veteran_coin_reward:,} 猪币",
            )
        )
    if result.effect.granted_uses > 1:
        rows.append(
            EconomyReceiptRowViewModel(
                "效果可用次数",
                f"{result.effect.granted_uses} 次",
            )
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


def group_event_eat_view(
    result: EatResult,
    *,
    group_name: str,
) -> GroupEventViewModel:
    """Build a major-event announcement for sugar ribs or the cloud-sea pot."""

    actor = _public_actor_name(
        display_name=result.food.owner_display_name,
        stable_id=result.food.owner_player_id,
    )
    event_time = _display_time(result.receipt.created_at)
    if result.effect.queued_effect_id == QUOTA_RESET_CHANCE:
        params = result.effect.queued_effect_params
        reset_uses = int(result.effect.granted_uses)
        group_coin = int(params.get("group_coin") or 0)
        dedicated_catches = int(params.get("group_dedicated_catches") or 0)
        five_multiplier = float(params.get("five_star_multiplier") or 1.0)
        six_multiplier = float(params.get("six_star_multiplier") or 1.0)
        hidden_chance = float(params.get("hidden_boost_chance_percent") or 0.0)
        return GroupEventViewModel(
            tone="sugar",
            eyebrow="六星盛宴 · 全群事件资格已取得",
            title="糖醋排骨登场",
            subtitle="酸甜一响，全群强化蓄势待发",
            actor_name=actor,
            group_name=group_name or "当前群",
            event_time=event_time,
            hero_label="发动资格",
            hero_value=f"{reset_uses} 次 /重置额度",
            rows=(
                GroupEventRowViewModel(
                    "全群猪币",
                    f"每人 +{group_coin:,}",
                    "实际执行 /重置额度 后发放",
                ),
                GroupEventRowViewModel(
                    "专属抓猪",
                    f"每人 {dedicated_catches} 次",
                    "不占用正常抓猪额度",
                ),
                GroupEventRowViewModel(
                    "高星强化",
                    _paired_star_multiplier(
                        five_star=five_multiplier,
                        six_star=six_multiplier,
                    ),
                    f"每次专属抓猪另有 {hidden_chance:g}% 隐藏爆发",
                ),
            ),
            note=(
                "本次食用只取得发动资格，尚未重置任何额度。"
                "请由食用者在本群发送 /重置额度，届时将再次发布正式发动通告。" + _veteran_reward_note(result)
            ),
            footer="全群事件将在真正发动时原子结算",
            settlement_committed=False,
            media_visible=result.food.media_visible,
            is_animated=result.food.is_animated,
            image_fit=result.food.image_fit,
        )
    if result.effect.queued_effect_id == GROUP_NEXT_EXCLUSIVE_HIGH_STAR_CATCH:
        params = result.effect.queued_effect_params
        self_coin = int(params.get("self_coin") or result.effect.coin_bonus)
        other_coin = int(params.get("other_coin") or 0)
        uses_per_player = int(params.get("uses_per_player") or 0)
        five_multiplier = float(params.get("five_star_multiplier") or 1.0)
        six_multiplier = float(params.get("six_star_multiplier") or 1.0)
        return GroupEventViewModel(
            tone="cloud",
            eyebrow="六星盛宴 · 神龙临世",
            title="七星云海，福泽全群",
            subtitle="神龙化猪七星云海锅已经开席",
            actor_name=actor,
            group_name=group_name or "当前群",
            event_time=event_time,
            hero_label="全群高星权重",
            hero_value=_paired_star_multiplier(
                five_star=five_multiplier,
                six_star=six_multiplier,
            ),
            rows=(
                GroupEventRowViewModel(
                    "食用者奖励",
                    f"+{self_coin:,} 猪币",
                    actor,
                ),
                GroupEventRowViewModel(
                    "其余群友奖励",
                    f"每人 +{other_coin:,} 猪币",
                    f"本次共惠及 {result.group_rewarded_players} 名已登记玩家",
                ),
                GroupEventRowViewModel(
                    "下一次抓猪",
                    (
                        f"纯基础独占 ×{five_multiplier:g}"
                        if five_multiplier == six_multiplier
                        else _paired_star_multiplier(
                            five_star=five_multiplier,
                            six_star=six_multiplier,
                        )
                    ),
                    f"每名玩家各生效 {uses_per_player} 次，不与其他道具或菜品叠加",
                ),
            ),
            note=(
                "全群效果从当前抓猪时段开始，到次日同一时段刷新时清除；"
                "每名玩家的下一次兼容抓猪独立消费自己的加成。" + _veteran_reward_note(result)
            ),
            footer="神龙赐福已在本群完成结算",
            media_visible=result.food.media_visible,
            is_animated=result.food.is_animated,
            image_fit=result.food.image_fit,
        )
    raise ValueError("该美食不属于全群大事件通告。")


_TECHNIQUE_COMMANDS = {
    TECHNIQUE_MALEVOLENT_KITCHEN: "/领域展开 伏魔御厨子",
    TECHNIQUE_LAPSE_BLUE: "/术式顺转 苍",
    TECHNIQUE_REVERSAL_RED: "/术式反转 赫",
}


def is_special_event_food(result: EatResult) -> bool:
    """Return whether eating should use the recent-mechanic event card."""

    return result.effect.queued_effect_id in {
        GROUP_COIN_TRIBUTE,
        ROULETTE_CHANCES,
        TECHNIQUE_PERMIT,
    }


def special_event_eat_view(
    result: EatResult,
    *,
    group_name: str,
) -> GroupEventViewModel:
    """Build an image-first receipt for recent exclusive food mechanics."""

    actor = _public_actor_name(
        display_name=result.food.owner_display_name,
        stable_id=result.food.owner_player_id,
    )
    event_time = _display_time(result.receipt.created_at)
    effect_id = result.effect.queued_effect_id
    if effect_id == GROUP_COIN_TRIBUTE:
        unit = int(result.effect.queued_effect_params.get("coin_per_player") or 0)
        return GroupEventViewModel(
            tone="tribute",
            eyebrow="疯狂星期四 · 全群猪币结算",
            title="炸猪全家桶开席",
            subtitle="群友已经逐笔支付，食用者一次收齐",
            actor_name=actor,
            group_name=group_name or "当前群",
            event_time=event_time,
            hero_label="本次实际收到",
            hero_value=f"{result.group_coin_total:,} 猪币",
            rows=(
                GroupEventRowViewModel(
                    "参与群友",
                    f"{result.group_rewarded_players} 人",
                    "仅统计当前群已登记的其他玩家",
                ),
                GroupEventRowViewModel(
                    "单人支付上限",
                    f"{unit} 猪币",
                    "余额不足者按现有余额支付，不会产生负债",
                ),
                GroupEventRowViewModel(
                    "食用者余额",
                    f"{result.coin_balance:,} 猪币",
                    "所有扣款与入账已在同一事务完成",
                ),
            ),
            note=(
                f"{result.food.selector} 已消耗；本次从 "
                f"{result.group_rewarded_players} 名群友处实际汇总 "
                f"{result.group_coin_total:,} 猪币。" + _veteran_reward_note(result)
            ),
            footer="全家桶猪币往来已原子结算",
            media_visible=result.food.media_visible,
            is_animated=result.food.is_animated,
            image_fit=result.food.image_fit,
            seal_top="猪币",
            seal_bottom="结算",
            committed_note="扣款、汇总与入账已经提交；重复消息不会重复收取",
        )
    if effect_id == TECHNIQUE_PERMIT:
        technique_id = str(result.effect.queued_effect_params.get("technique_id") or "")
        technique_name = TECHNIQUE_DISPLAY_NAMES.get(technique_id, "未知术式")
        command = _TECHNIQUE_COMMANDS.get(technique_id, "请查看效果说明")
        return GroupEventViewModel(
            tone="technique",
            eyebrow="专属美食 · 术式资格入账",
            title=f"{technique_name}资格已取得",
            subtitle="专属菜已消耗，发动指令已经解锁",
            actor_name=actor,
            group_name=group_name or "当前群",
            event_time=event_time,
            hero_label="可使用指令",
            hero_value=command,
            rows=(
                GroupEventRowViewModel(
                    "当前资格",
                    f"{result.available_effect_uses} 次",
                    "发动成功后才会消耗一份资格",
                ),
                GroupEventRowViewModel(
                    "品鉴经验",
                    f"+{result.base_experience + result.effect.experience_bonus}",
                    f"累计经验 {result.total_experience:,}",
                ),
                GroupEventRowViewModel(
                    "当前猪币",
                    f"{result.coin_balance:,}",
                    "本次专属菜效果已经持久化",
                ),
            ),
            note=result.effect.summary + _veteran_reward_note(result),
            footer="术式资格已入账，可在当前群发动",
            media_visible=result.food.media_visible,
            is_animated=result.food.is_animated,
            image_fit=result.food.image_fit,
            seal_top="术式",
            seal_bottom="资格",
        )
    if effect_id == ROULETTE_CHANCES:
        return GroupEventViewModel(
            tone="roulette",
            eyebrow="六星美食 · 轮盘机会入账",
            title="猪保千轮盘已开启",
            subtitle="三次基础机会已经登记，可立即抽取",
            actor_name=actor,
            group_name=group_name or "当前群",
            event_time=event_time,
            hero_label="当前可转次数",
            hero_value=f"{result.available_effect_uses} 次",
            rows=(
                GroupEventRowViewModel("使用指令", "/转轮盘", "每次只消耗一份机会"),
                GroupEventRowViewModel(
                    "奖面数量",
                    "6 种等概率",
                    "奖励在抽取事务中即时落账",
                ),
                GroupEventRowViewModel(
                    "当前猪币",
                    f"{result.coin_balance:,}",
                    "转到猪币奖励时会直接更新",
                ),
            ),
            note=result.effect.summary + _veteran_reward_note(result),
            footer="轮盘机会已持久化，重启不会丢失",
            media_visible=result.food.media_visible,
            is_animated=result.food.is_animated,
            image_fit=result.food.image_fit,
            seal_top="轮盘",
            seal_bottom="开启",
        )
    raise ValueError("该美食不属于专属图片事件。")


def roulette_event_view(
    result: RouletteResult,
    *,
    actor_name: str,
    actor_player_id: str,
    group_name: str,
) -> GroupEventViewModel:
    """Build one verifiable image receipt for a roulette settlement."""

    actor = _public_actor_name(
        display_name=actor_name,
        stable_id=actor_player_id,
    )
    return GroupEventViewModel(
        tone="roulette",
        eyebrow="猪保千猪排轮盘 · 奖励已落账",
        title=f"轮盘停在第 {result.outcome} 面",
        subtitle="本次抽取已经提交，不会因图片故障重复结算",
        actor_name=actor,
        group_name=group_name or "当前群",
        event_time=_display_time(result.receipt.created_at),
        hero_label="本次奖励",
        hero_value=result.outcome_summary,
        rows=(
            GroupEventRowViewModel(
                "轮盘点数",
                str(result.outcome),
                "六个结果使用等概率抽取",
            ),
            GroupEventRowViewModel(
                "剩余机会",
                f"{result.remaining_spins} 次",
                "第 2 面会把额外机会立即计入",
            ),
            GroupEventRowViewModel(
                "当前猪币",
                f"{result.coin_balance:,}",
                "猪币奖励已同步到账本",
            ),
        ),
        note=result.outcome_summary,
        footer="轮盘结算完成",
        seal_top="转轮",
        seal_bottom="结算",
        actor_label="转轮群友",
        committed_note="本次轮盘奖励已经提交；重复消息不会重复抽取",
        roulette_outcome=result.outcome,
    )


def _group_event_asset(asset: PigView | FoodView | TechniqueFoodView, *, kind_label: str) -> GroupEventAssetViewModel:
    """Project only a committed asset; do not reveal an out-of-scope media identity."""
    if not asset.media_visible:
        detail = "当前群未开放此素材预览"
    elif isinstance(asset, TechniqueFoodView):
        detail = "已自动出餐并写入背包"
    else:
        detail = f"价值 {asset.official_value:,} 猪币"
    return GroupEventAssetViewModel(
        key=asset.short_code,
        name=asset.display_name if asset.media_visible else "已隐藏的专属资产",
        short_code=asset.short_code,
        rarity=asset.rarity,
        kind_label=kind_label,
        owner_name=_public_actor_name(
            display_name=asset.owner_display_name,
            stable_id=asset.owner_player_id,
        ),
        detail=detail,
        image_fit=asset.image_fit,
        media_visible=asset.media_visible,
        is_animated=asset.is_animated,
    )


def technique_activation_view(
    result: TechniqueActivationResult,
    *,
    actor_name: str,
    actor_player_id: str,
    group_name: str,
) -> GroupEventViewModel:
    """Build a public activation or Hollow Purple settlement card."""

    actor = _public_actor_name(
        display_name=actor_name,
        stable_id=actor_player_id,
    )
    if result.technique_id == TECHNIQUE_HOLLOW_PURPLE:
        assets = tuple(_group_event_asset(pig, kind_label="猪猪") for pig in result.granted_pigs)
        selectors = "、".join(f"{asset.name}#{asset.short_code}" for asset in assets)
        preview_pig = result.granted_pigs[0] if result.granted_pigs else None
        return GroupEventViewModel(
            tone="technique",
            eyebrow="苍赫相合 · 虚式结算",
            title="虚式·茈发动",
            subtitle="五只六星猪已经同时写入背包",
            actor_name=actor,
            group_name=group_name or "当前群",
            event_time=_display_time(result.receipt.created_at),
            hero_label="本次获得",
            hero_value=f"{len(result.granted_pigs)} 只六星猪",
            rows=(
                GroupEventRowViewModel("实际入库", f"{len(assets)} 只", "完整清单逐只展示图片、编号与归属"),
                GroupEventRowViewModel("剩余虚式资格", f"{result.remaining_permits} 次", "重复查看不会再次发放"),
                GroupEventRowViewModel("发放状态", "已到账", "五只猪猪在同一事务内原子发放"),
            ),
            note=f"完整资产编号：{selectors}。编号、品质和归属均来自本次已提交回执。",
            footer="虚式奖励已原子发放",
            seal_top="虚式",
            seal_bottom="结算",
            media_visible=bool(preview_pig is not None and preview_pig.media_visible),
            is_animated=bool(preview_pig is not None and preview_pig.is_animated),
            image_fit=(preview_pig.image_fit if preview_pig is not None else "contain"),
            assets=assets,
        )
    technique_name = TECHNIQUE_DISPLAY_NAMES.get(
        result.technique_id,
        result.technique_id,
    )
    detail = {
        TECHNIQUE_MALEVOLENT_KITCHEN: "后续抓猪会即时做成双份美食",
        TECHNIQUE_LAPSE_BLUE: "后续抓到的猪会被吸引给发动者",
        TECHNIQUE_REVERSAL_RED: "后续抓到的猪会随机分配给群友",
    }.get(result.technique_id, "群体术式已经发动")
    rows = [
        GroupEventRowViewModel("接管次数", f"{result.total_uses} 次", detail),
        GroupEventRowViewModel(
            "同类资格剩余",
            f"{result.remaining_permits} 次",
            "当前术式结束前不能发动另一种群体术式",
        ),
    ]
    if result.purple_unlocked:
        rows.append(
            GroupEventRowViewModel(
                "苍赫组合",
                f"+{result.purple_unlocked} 次虚式",
                "可使用 /虚式 茈",
            )
        )
    return GroupEventViewModel(
        tone="technique",
        eyebrow="群体术式 · 正式发动",
        title=f"{technique_name}展开",
        subtitle=detail,
        actor_name=actor,
        group_name=group_name or "当前群",
        event_time=_display_time(result.receipt.created_at),
        hero_label="本群接管范围",
        hero_value=f"后续 {result.total_uses} 次抓猪",
        rows=tuple(rows),
        note=result.summary,
        footer="术式状态已经持久化，逐次结算会单独出图",
        seal_top="术式",
        seal_bottom="发动",
    )


def technique_catch_event_view(
    result: CatchResult,
    *,
    catcher_name: str,
    catcher_player_id: str,
    group_name: str,
) -> GroupEventViewModel:
    """Build the single public card for one technique-intercepted catch."""

    resolution = result.technique_resolution
    if resolution is None:
        raise ValueError("本次抓猪没有群体术式结算。")
    catcher = _public_actor_name(
        display_name=catcher_name,
        stable_id=catcher_player_id,
    )
    if resolution.technique_id == TECHNIQUE_MALEVOLENT_KITCHEN:
        foods = resolution.generated_foods
        preview_food = foods[0] if foods else None
        food_name = foods[0].display_name if foods else "未知美食"
        food_rarity = foods[0].rarity if foods else 0
        is_gojo_dual_recipe = result.pig.template_id == GOJO_PIG_TEMPLATE_ID
        gojo_self_caught_in_own_domain = (
            is_gojo_dual_recipe
            and bool(foods)
            and all(food.owner_player_id == resolution.source_player_id for food in foods)
        )
        assets = tuple(_group_event_asset(food, kind_label="美食") for food in foods)
        owner_summary = "、".join(f"{asset.owner_name}：{asset.name}#{asset.short_code}" for asset in assets)
        return GroupEventViewModel(
            tone="technique",
            eyebrow="伏魔御厨子 · 自动出餐结算",
            title=("五条猪化为苍蓝与赫焰" if is_gojo_dual_recipe else f"{result.pig.display_name}已化为{food_name}"),
            subtitle=(
                (
                    "发动者亲自抓获五条猪，两道专属雪山全部归发动者"
                    if gojo_self_caught_in_own_domain
                    else "两道专属雪山已随机分给两名不同群友"
                )
                if is_gojo_dual_recipe
                else "抓猪、消耗原料、做菜与双份发放已在同一事务完成"
            ),
            actor_name=_public_actor_name(
                display_name=resolution.source_display_name,
                stable_id=resolution.source_player_id,
            ),
            group_name=group_name or "当前群",
            event_time=_display_time(result.receipt.created_at),
            hero_label="本次出餐品质",
            hero_value=(f"{food_rarity} 星 · 专属双菜" if is_gojo_dual_recipe else f"{food_rarity} 星 · 双份"),
            rows=(
                GroupEventRowViewModel("抓猪群友", catcher, result.pig.selector),
                GroupEventRowViewModel(
                    "专属双菜" if is_gojo_dual_recipe else "双份美食",
                    f"{len(foods)} 份",
                    owner_summary,
                ),
                GroupEventRowViewModel(
                    "领域剩余",
                    f"{resolution.remaining_uses} 次",
                    "每有一名群友成功抓猪就消耗一次",
                ),
            ),
            note=resolution.summary + _veteran_reward_note(result),
            footer="伏魔御厨子本轮出餐完成",
            seal_top="领域",
            seal_bottom="出餐",
            actor_label="领域发动者",
            media_visible=bool(preview_food is not None and preview_food.media_visible),
            is_animated=bool(preview_food is not None and preview_food.is_animated),
            image_fit=(preview_food.image_fit if preview_food is not None else "contain"),
            assets=assets,
        )
    pig_asset = _group_event_asset(result.pig, kind_label="猪猪")
    return GroupEventViewModel(
        tone="technique",
        eyebrow=f"{resolution.technique_name} · 抓猪归属结算",
        title=f"{result.pig.display_name}归属已改写",
        subtitle="猪猪已经直接进入最终归属者的背包",
        actor_name=_public_actor_name(
            display_name=resolution.source_display_name,
            stable_id=resolution.source_player_id,
        ),
        group_name=group_name or "当前群",
        event_time=_display_time(result.receipt.created_at),
        hero_label="最终获得者",
        hero_value=_public_actor_name(
            display_name=resolution.target_display_name,
            stable_id=resolution.target_player_id,
        ),
        rows=(
            GroupEventRowViewModel("抓猪群友", catcher, "本次抓猪的指令发起者"),
            GroupEventRowViewModel(
                "获得猪猪",
                result.pig.selector,
                f"{result.pig.rarity} 星，价值 {result.pig.official_value:,} 猪币",
            ),
            GroupEventRowViewModel(
                "术式剩余",
                f"{resolution.remaining_uses} 次",
                "成功抓猪后才会递减",
            ),
        ),
        note=resolution.summary + _veteran_reward_note(result),
        footer=f"{resolution.technique_name}本轮结算完成",
        seal_top="术式",
        seal_bottom="结算",
        actor_label="术式发动者",
        media_visible=result.pig.media_visible,
        is_animated=result.pig.is_animated,
        image_fit=result.pig.image_fit,
        assets=(pig_asset,),
    )


def group_event_quota_reset_view(
    result: CatchQuotaResetResult,
    *,
    group_name: str,
) -> GroupEventViewModel:
    """Build the formal activation announcement for a sugar-ribs quota reset."""

    actor = _public_actor_name(
        display_name=result.actor_display_name,
        stable_id=result.actor_user_id,
    )
    return GroupEventViewModel(
        tone="reset",
        eyebrow="糖醋排骨 · 全群强化正式发动",
        title="全群额度重置完成",
        subtitle="酸甜号令落下，新的十连已经开启",
        actor_name=actor,
        group_name=group_name or "当前群",
        event_time=_display_time(result.created_at),
        hero_label="全群专属抓猪",
        hero_value=f"每人 {result.group_dedicated_catches} 次",
        rows=(
            GroupEventRowViewModel(
                "全群猪币",
                f"每人 +{result.group_coin_reward:,}",
                f"共惠及 {result.group_rewarded_players} 名已登记玩家",
            ),
            GroupEventRowViewModel(
                "本时段重置",
                f"归零 {result.cleared_catches} 次",
                f"涉及 {result.affected_players} 名玩家；历史资产与统计全部保留",
            ),
            GroupEventRowViewModel(
                "高星强化",
                _paired_star_multiplier(
                    five_star=result.five_star_multiplier,
                    six_star=result.six_star_multiplier,
                ),
                (
                    f"每次专属抓猪有 {result.hidden_boost_chance_percent:g}% 概率"
                    f"爆发为 ×{result.hidden_five_star_multiplier:g}"
                ),
            ),
        ),
        note=(
            f"强化持续至 {_display_time(result.group_effect_expires_at)}；"
            "专属抓猪不扣正常额度，可与普通道具和非六星菜按既定规则叠加。"
        ),
        footer="糖醋排骨全群强化已经正式生效",
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
            *(
                (
                    EconomyReceiptRowViewModel("赠送方今日剩余", f"{result.sender_remaining} 次"),
                    EconomyReceiptRowViewModel("接收方今日剩余", f"{result.recipient_remaining} 次"),
                )
                if result.sender_remaining is not None and result.recipient_remaining is not None
                else ()
            ),
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
    if result.tax_amount is not None and result.seller_net is not None:
        rows.insert(
            3,
            EconomyReceiptRowViewModel(
                "5% 交易税 / 卖方实收",
                f"{result.tax_amount} / {result.seller_net} 猪币",
            ),
        )
    return EconomyReceiptViewModel(
        eyebrow="双方确认交易 · 当前群",
        title=title,
        badge_label=result.trade.status_label,
        badge_value=result.trade.trade_id,
        summary=f"{'★' * result.trade.asset.rarity} {result.trade.asset.selector}",
        rows=tuple(rows),
        note=(
                    "接收方使用 /接受交易 交易号 完成付款；成交时买方支付原价，"
                    "卖方收入扣除5%交易税；未完成报价会在五分钟后自动解锁。"
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


def daily_giants_media_paths(
    data_dir: Path,
    result: DailyGiants,
) -> dict[str, Path]:
    """Resolve bounded previews for both daily giant lists."""

    paths: dict[str, Path] = {}
    for metric, entries in (
        ("size", result.size_entries),
        ("weight", result.weight_entries),
    ):
        for entry in entries:
            if not entry.media_visible:
                continue
            paths[f"{metric}:{entry.pig_instance_id}"] = media_path(
                data_dir,
                entry.image_relpath,
            )
    return paths


def _achievement_reward_text(rewards: Sequence[object]) -> str:
    from ..services.achievements import reward_label
    from .cosmetics import cosmetic_detail

    labels = []
    for reward in rewards:
        if reward.reward_type in {"title", "frame", "badge", "cosmetic"}:
            art = cosmetic_detail(reward.reward_id, kind=reward.reward_type)
            labels.append(f"{art['name'] or '纪念外观'} ×{reward.quantity}")
        else:
            labels.append(reward_label(reward))
    return "、".join(labels) or "成就点"


def achievement_row_view(entry: object) -> AchievementRowViewModel:
    from .cosmetics import cosmetic_cards

    return AchievementRowViewModel(
        achievement_id=entry.achievement_id,
        name=entry.name,
        category=entry.category,
        tier_label=entry.tier_label,
        unlocked=entry.unlocked,
        hidden=entry.hidden,
        description=entry.description,
        progress=entry.progress,
        target=entry.target,
        points=entry.points,
        reward_text="解锁后揭晓" if entry.hidden and not entry.unlocked else _achievement_reward_text(entry.rewards),
        unlocked_at=entry.unlocked_at,
        cosmetics=cosmetic_cards(entry.rewards, revealed=not entry.hidden or entry.unlocked),
    )


def achievement_overview_view(result: object) -> AchievementOverviewViewModel:
    from .cosmetics import cosmetic_detail

    reward_text = (
        _achievement_reward_text(tuple(
            AchievementReward(reward_type, reward_id, quantity)
            for reward_type, reward_id, quantity in result.rewards
        )) if result.rewards else "暂无库存"
    )
    return AchievementOverviewViewModel(
        display_name=result.display_name,
        points=result.points,
        unlocked_count=result.unlocked_count,
        total_count=result.total_count,
        completion_percent=(result.unlocked_count * 100 / result.total_count if result.total_count else 0),
        title_text=cosmetic_detail(result.equipped_title_id, kind="title")["name"] or "未佩戴",
        frame_text=cosmetic_detail(result.equipped_frame_id, kind="frame")["name"] or "默认淡粉",
        showcase_text=f"{sum(bool(badge) for badge in result.badge_ids)} / {result.badge_capacity} 格已佩戴",
        next_milestone_text=(f"{result.next_milestone} 点" if result.next_milestone else "已完成全部里程碑"),
        reward_inventory_text=reward_text,
        recent=tuple(achievement_row_view(entry) for entry in result.recent),
        achievement_title=result.equipped_title_id,
        achievement_frame=result.equipped_frame_id,
        achievement_badges=result.badge_ids,
        achievement_badge_capacity=result.badge_capacity,
    )


def achievement_page_view(result: object) -> AchievementPageViewModel:
    return AchievementPageViewModel(
        display_name=result.display_name,
        category=result.category,
        page=result.page,
        page_count=result.page_count,
        total_count=result.total_count,
        entries=tuple(achievement_row_view(entry) for entry in result.entries),
    )


def achievement_unlock_view(display_name: str, unlocks: Sequence[object]) -> AchievementUnlockViewModel:
    from ..domain.achievements import UNLOCK_SUMMARY_LIMIT
    from .cosmetics import cosmetic_cards

    rows = []
    for unlock in unlocks[:UNLOCK_SUMMARY_LIMIT]:
        rows.append(
            AchievementRowViewModel(
                achievement_id=unlock.achievement_id,
                name=unlock.name,
                category="新成就",
                tier_label=TIER_LABELS[unlock.tier],
                unlocked=True,
                hidden=False,
                description="成就条件已经完成，奖励已原子结算。",
                progress=1,
                target=1,
                points=unlock.points,
                reward_text=_achievement_reward_text(unlock.rewards),
                unlocked_at=unlock.unlocked_at,
                cosmetics=cosmetic_cards(unlock.rewards),
            )
        )
    return AchievementUnlockViewModel(
        display_name, sum(item.points for item in unlocks), tuple(rows), max(0, len(unlocks) - UNLOCK_SUMMARY_LIMIT)
    )


def achievement_backfill_summary_view(result: object) -> AchievementBackfillSummaryViewModel:
    return AchievementBackfillSummaryViewModel(
        display_name=result.display_name,
        unlocked_count=result.unlocked_count,
        total_points=result.total_points,
        reward_text=_achievement_reward_text(result.rewards),
        highlights=result.highlights,
    )


def achievement_ranking_view(result: object) -> AchievementRankingViewModel:
    return AchievementRankingViewModel(
        group_name=result.group_name,
        page=result.page,
        page_count=result.page_count,
        total_count=result.total_count,
        entries=tuple(
            AchievementRankingRowViewModel(item.rank, item.display_name, item.points, item.unlocked_count)
            for item in result.entries
        ),
    )


def weekly_competition_view(result: object) -> WeeklyCompetitionViewModel:
    return WeeklyCompetitionViewModel(
        season_number=result.season_number,
        name=result.name,
        status_label=result.status_label,
        group_name=result.group_name,
        metric_label=result.metric_label,
        period_text=result.period_text,
        countdown_text=result.countdown_text,
        page=result.page,
        page_count=result.page_count,
        total_count=result.total_count,
        player_position_text=(
            f"我的名次：第 {result.player_rank} 名 · {result.player_score_text}"
            if result.player_rank is not None
            else "我的名次：尚未上榜"
        ),
        entries=tuple(
            WeeklyCompetitionRowViewModel(
                rank=item.rank,
                display_name=item.display_name,
                score_text=item.score_text,
                catch_count=item.catch_count,
                highest_single_text=item.highest_single_text,
                last_update_at=item.last_update_at,
            )
            for item in result.entries
        ),
    )


def weekly_competition_award_view(result: object) -> WeeklyCompetitionAwardViewModel:
    from ..services.weekly_competitions import weekly_reward_label

    return WeeklyCompetitionAwardViewModel(
        season_number=result.season_number,
        competition_name=result.competition_name,
        display_name=result.display_name,
        final_rank=result.final_rank,
        score_text=result.score_text,
        reward_lines=tuple(weekly_reward_label(item) for item in result.rewards),
    )
