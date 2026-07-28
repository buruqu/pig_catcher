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
class RenderedImage:
    """已通过解码、格式和大小校验的 PNG。"""

    image_base64: str
    mime_type: str
    width: int
    height: int
    byte_length: int
