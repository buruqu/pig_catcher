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
