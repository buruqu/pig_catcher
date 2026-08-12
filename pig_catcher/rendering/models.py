"""渲染层稳定输入输出模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """一次插件生命周期内固定的渲染选项。"""

    card_width: int
    viewport_height: int
    device_scale_factor: float
    render_timeout_ms: int
    max_png_bytes: int
    max_animation_bytes: int
    missing_frame_duration_ms: int
    font_family: str


@dataclass(frozen=True, slots=True)
class FrameworkPreviewViewModel:
    """仅用于 2A 本地视觉验收，不暴露成玩法命令。"""

    plugin_version: str
    framework_phase: str
    schema_version: int
    asset_manifest_version: int
    ruleset_version: int


@dataclass(frozen=True, slots=True)
class AssetPreviewViewModel:
    """2B 正式素材视觉验收卡片的静态文字。"""

    display_name: str
    description: str
    rarity: int
    kind_label: str
    media_format: str
    frame_count: int
    collection_name: str = ""
    collection_progress: str = ""
    character_name: str = ""


@dataclass(frozen=True, slots=True)
class RenderedImage:
    """已通过解码、格式和大小校验的静态或动画图片。"""

    image_base64: str
    mime_type: str
    width: int
    height: int
    byte_length: int
    frame_count: int = 1
    total_duration_ms: int = 0
    loop_count: int | None = None

    @property
    def is_animated(self) -> bool:
        return self.frame_count > 1


@dataclass(frozen=True, slots=True)
class MediaSlot:
    """动画素材在静态卡片底图中的稳定像素区域。"""

    x: int
    y: int
    width: int
    height: int
    fit: str = "contain"


@dataclass(frozen=True, slots=True)
class RenderedAssetPreviewBase:
    """HTML 渲染底图与后处理素材槽的组合。"""

    image: RenderedImage
    media_slot: MediaSlot


@dataclass(frozen=True, slots=True)
class PigCardViewModel:
    """Single catch or owned-pig detail card."""

    mode_label: str
    display_name: str
    owner_display_name: str
    rarity: int
    rarity_name: str
    short_code: str
    description: str
    size_value: float
    size_percentile: float
    weight_value: float
    weight_percentile: float
    fat_ratio: float
    fat_label: str
    official_value: int
    acquired_at: str
    image_fit: str = "contain"
    media_visible: bool = True
    is_animated: bool = False
    media_format: str = "PNG"
    collection_name: str = ""
    character_name: str = ""
    coin_reward: int | None = None
    experience_reward: int | None = None
    coin_balance: int | None = None
    total_experience: int | None = None
    player_level: int | None = None
    level_title: str = ""
    next_level_experience: int | None = None
    level_progress_percent: float = 0.0
    daily_count: int | None = None
    daily_limit: int | None = None
    quota_exempt_catch: bool = False
    item_name: str = ""
    catalog_new: bool = False
    size_record: bool = False
    weight_record: bool = False
    body_label: str = ""
    body_description: str = ""
    giant_score: float = 0.0
    global_size_record: bool = False
    global_weight_record: bool = False
    giant_sighting: bool = False
    size_label: str = ""
    weight_label: str = ""
    effect_summaries: tuple[str, ...] = ()
    excluded_summaries: tuple[str, ...] = ()
    tutorial_text: str = ""
    probability_line: str = ""
    probability_sources: str = ""


@dataclass(frozen=True, slots=True)
class CollectionProgressViewModel:
    """Compact collaboration collection progress."""

    collection_name: str
    collaboration_name: str
    collected_count: int
    available_count: int
    total_count: int


@dataclass(frozen=True, slots=True)
class ProfileViewModel:
    """Player profile image."""

    display_name: str
    level: int
    title: str
    total_experience: int
    next_threshold: int | None
    progress_percent: float
    coin_balance: int
    total_catches: int
    active_pigs: int
    catalog_count: int
    visible_catalog_total: int
    held_records: int
    daily_count: int
    daily_limit: int
    cooldown_remaining_seconds: int
    feed_level: int
    armed_item_name: str
    armed_item_quantity: int
    cookware_level: int = 0
    total_cooks: int = 0
    active_foods: int = 0
    food_catalog_count: int = 0
    visible_food_catalog_total: int = 0
    armed_cooking_item_name: str = ""
    armed_cooking_item_quantity: int = 0
    collections: tuple[CollectionProgressViewModel, ...] = ()
    showcase_pig: str = ""
    showcase_food: str = ""
    level_catch_base_high_percent: float = 0.0
    level_catch_adjusted_high_percent: float = 0.0
    level_cooking_bonus_percent: float = 0.0
    level_bonus_cap_level: int = 21


@dataclass(frozen=True, slots=True)
class InventoryItemViewModel:
    """One stable inventory tile."""

    key: str
    display_name: str
    short_code: str
    rarity: int
    size_value: float
    weight_value: float
    fat_label: str
    official_value: int
    media_visible: bool
    is_animated: bool
    image_fit: str
    body_label: str = ""


@dataclass(frozen=True, slots=True)
class InventoryViewModel:
    """One filtered pig inventory page."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    rarity: int | None
    sort: str
    items: tuple[InventoryItemViewModel, ...]


@dataclass(frozen=True, slots=True)
class CatalogItemViewModel:
    """One privacy-aware catalog tile."""

    key: str
    display_name: str
    rarity: int
    discovered: bool
    acquired_count: int
    best_size: float | None
    best_weight: float | None
    collection_name: str
    character_name: str
    media_visible: bool
    is_animated: bool
    image_fit: str


@dataclass(frozen=True, slots=True)
class CatalogViewModel:
    """One complete pig catalog."""

    display_name: str
    total_count: int
    rarity: int | None
    undiscovered_only: bool
    collected_count: int
    visible_catalog_total: int
    items: tuple[CatalogItemViewModel, ...]
    collections: tuple[CollectionProgressViewModel, ...] = ()


@dataclass(frozen=True, slots=True)
class RecordItemViewModel:
    """One current group record row."""

    record_label: str
    record_value: float
    unit: str
    display_name: str
    rarity: int
    short_code: str
    holder_display_name: str
    achieved_at: str


@dataclass(frozen=True, slots=True)
class RecordsViewModel:
    """One current-group records page."""

    group_name: str
    page: int
    page_count: int
    total_count: int
    items: tuple[RecordItemViewModel, ...]
    global_items: tuple[RecordItemViewModel, ...] = ()
    giant_sightings: tuple[GiantSightingViewModel, ...] = ()


@dataclass(frozen=True, slots=True)
class GiantSightingViewModel:
    """One recent group-wide giant sighting."""

    display_name: str
    rarity: int
    short_code: str
    holder_display_name: str
    size_value: float
    weight_value: float
    giant_score: float
    qualification_label: str
    achieved_at: str


@dataclass(frozen=True, slots=True)
class DailyGiantItemViewModel:
    """One player's best pig in a daily size or weight ranking."""

    key: str
    rank: int
    holder_display_name: str
    display_name: str
    rarity: int
    short_code: str
    size_value: float
    weight_value: float
    acquired_at: str
    media_visible: bool
    is_animated: bool
    image_fit: str


@dataclass(frozen=True, slots=True)
class DailyGiantsViewModel:
    """Two current-group giant rankings for the Beijing calendar day."""

    group_name: str
    date_label: str
    participant_count: int
    catch_count: int
    size_items: tuple[DailyGiantItemViewModel, ...]
    weight_items: tuple[DailyGiantItemViewModel, ...]


@dataclass(frozen=True, slots=True)
class GroupEventRowViewModel:
    """One headline benefit in a group-wide major-event announcement."""

    label: str
    value: str
    detail: str


@dataclass(frozen=True, slots=True)
class GroupEventViewModel:
    """A high-impact announcement for one committed group-wide effect."""

    tone: str
    eyebrow: str
    title: str
    subtitle: str
    actor_name: str
    group_name: str
    event_time: str
    hero_label: str
    hero_value: str
    rows: tuple[GroupEventRowViewModel, ...]
    note: str
    footer: str
    settlement_committed: bool = True
    media_visible: bool = False
    is_animated: bool = False
    image_fit: str = "contain"


@dataclass(frozen=True, slots=True)
class ItemReceiptViewModel:
    """Item equip or cancellation receipt."""

    operation: str
    item_name: str
    action_label: str
    quantity: int
    effect_summary: str


@dataclass(frozen=True, slots=True)
class FoodCardViewModel:
    """Single food detail or cooking-result card."""

    mode_label: str
    display_name: str
    owner_display_name: str
    rarity: int
    rarity_name: str
    short_code: str
    description: str
    portion_weight: float
    fat_label: str
    official_value: int
    acquired_at: str
    source_selector: str
    effect_summary: str
    image_fit: str
    media_visible: bool
    is_animated: bool
    media_format: str
    coin_reward: int | None = None
    experience_reward: int | None = None
    coin_balance: int | None = None
    total_experience: int | None = None
    player_level: int | None = None
    level_title: str = ""
    next_level_experience: int | None = None
    level_progress_percent: float = 0.0
    cookware_level: int | None = None
    item_name: str = ""
    catalog_new_count: int = 0
    bonus_selector: str = ""
    probability_summary: str = ""
    effect_summaries: tuple[str, ...] = ()
    excluded_summaries: tuple[str, ...] = ()
    probability_line: str = ""
    probability_sources: str = ""


@dataclass(frozen=True, slots=True)
class FoodInventoryItemViewModel:
    """One stable food inventory tile."""

    key: str
    display_name: str
    short_code: str
    rarity: int
    portion_weight: float
    fat_label: str
    official_value: int
    media_visible: bool
    is_animated: bool
    image_fit: str


@dataclass(frozen=True, slots=True)
class FoodInventoryViewModel:
    """One filtered food inventory page."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    rarity: int | None
    sort: str
    items: tuple[FoodInventoryItemViewModel, ...]


@dataclass(frozen=True, slots=True)
class FoodCatalogItemViewModel:
    """One privacy-aware food catalog tile."""

    key: str
    display_name: str
    rarity: int
    discovered: bool
    acquired_count: int
    best_portion_weight: float | None
    media_visible: bool
    is_animated: bool
    image_fit: str
    effect_summary: str = ""


@dataclass(frozen=True, slots=True)
class FoodCatalogViewModel:
    """One complete food catalog."""

    display_name: str
    total_count: int
    rarity: int | None
    undiscovered_only: bool
    collected_count: int
    visible_catalog_total: int
    items: tuple[FoodCatalogItemViewModel, ...]


@dataclass(frozen=True, slots=True)
class BatchCookingItemViewModel:
    """One food tile produced by batch cooking."""

    key: str
    display_name: str
    short_code: str
    rarity: int
    portion_weight: float
    fat_label: str
    official_value: int
    media_visible: bool
    is_animated: bool
    image_fit: str
    source_pig_name: str = ""


@dataclass(frozen=True, slots=True)
class BatchCookingViewModel:
    """One grid of foods produced by batch cooking, ordered by rarity desc."""

    display_name: str
    pig_count: int
    food_count: int
    coin_reward: int
    experience_reward: int
    catalog_new_count: int
    rarity: int | None
    items: tuple[BatchCookingItemViewModel, ...]


@dataclass(frozen=True, slots=True)
class StoreProductViewModel:
    """One store product row."""

    display_name: str
    category: str
    unit_price: int
    effect_summary: str
    current_level: int
    target_level: int
    command: str = ""


@dataclass(frozen=True, slots=True)
class StoreProbabilityRowViewModel:
    """One permanent-upgrade probability level shown in the store."""

    level: int
    value: str
    delta: str
    current: bool


@dataclass(frozen=True, slots=True)
class StoreConsumableProbabilityRowViewModel:
    """One before/after probability row for a one-shot store item."""

    label: str
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class StoreViewModel:
    """One current-player store page."""

    display_name: str
    coin_balance: int
    page: int
    page_count: int
    total_count: int
    category: str
    feed_level: int
    cookware_level: int
    products: tuple[StoreProductViewModel, ...]
    feed_probability_rows: tuple[StoreProbabilityRowViewModel, ...] = ()
    cookware_probability_rows: tuple[StoreProbabilityRowViewModel, ...] = ()
    lucky_whistle_rows: tuple[StoreConsumableProbabilityRowViewModel, ...] = ()
    super_lucky_whistle_rows: tuple[StoreConsumableProbabilityRowViewModel, ...] = ()
    star_pig_radar_rows: tuple[StoreConsumableProbabilityRowViewModel, ...] = ()
    chef_spice_rows: tuple[StoreConsumableProbabilityRowViewModel, ...] = ()
    super_chef_spice_rows: tuple[StoreConsumableProbabilityRowViewModel, ...] = ()


@dataclass(frozen=True, slots=True)
class EconomyReceiptRowViewModel:
    """One label/value row in an economy receipt."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class EconomyReceiptViewModel:
    """Purchase, eating, or official-sale success receipt."""

    eyebrow: str
    title: str
    badge_label: str
    badge_value: str
    summary: str
    rows: tuple[EconomyReceiptRowViewModel, ...]
    note: str


@dataclass(frozen=True, slots=True)
class LedgerEntryViewModel:
    """One rendered pig-coin ledger entry."""

    amount_text: str
    positive: bool
    balance_after: int
    reason_text: str
    created_at: str


@dataclass(frozen=True, slots=True)
class LedgerViewModel:
    """One reconciled ledger page."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    coin_balance: int
    ledger_total: int
    items: tuple[LedgerEntryViewModel, ...]


@dataclass(frozen=True, slots=True)
class TradeListItemViewModel:
    """One compact bilateral trade row."""

    trade_id: str
    status_label: str
    asset_name: str
    asset_code: str
    rarity: int
    price: int
    sender_name: str
    recipient_name: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class TradeListViewModel:
    """One current-player trade page."""

    display_name: str
    page: int
    page_count: int
    total_count: int
    status_label: str
    items: tuple[TradeListItemViewModel, ...]


@dataclass(frozen=True, slots=True)
class RankingItemViewModel:
    """One group leaderboard row with one optional showcase asset."""

    key: str
    rank: int
    display_name: str
    metric_text: str
    pig_progress: str
    food_progress: str
    asset_count: int
    coin_balance: int
    showcase_name: str
    showcase_detail: str
    showcase_rarity: int
    showcase_kind: str
    media_visible: bool
    is_animated: bool
    image_fit: str


@dataclass(frozen=True, slots=True)
class RankingViewModel:
    """One original white-and-pale-pink group leaderboard."""

    group_name: str
    ranking_type: str
    page: int
    page_count: int
    total_count: int
    items: tuple[RankingItemViewModel, ...]
