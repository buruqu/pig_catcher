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
    daily_count: int | None = None
    daily_limit: int | None = None
    item_name: str = ""
    catalog_new: bool = False
    size_record: bool = False
    weight_record: bool = False


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
    collections: tuple[CollectionProgressViewModel, ...] = ()


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
    """One pig catalog page."""

    display_name: str
    page: int
    page_count: int
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


@dataclass(frozen=True, slots=True)
class ItemReceiptViewModel:
    """Item equip or cancellation receipt."""

    operation: str
    item_name: str
    action_label: str
    quantity: int
    effect_summary: str
