"""通过 MaiBot 公共 HTML 渲染能力生成经过校验的 PNG。"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from PIL import Image, UnidentifiedImageError

from ..domain.errors import RenderError
from .models import (
    AssetPreviewViewModel,
    FrameworkPreviewViewModel,
    MediaSlot,
    RenderedAssetPreviewBase,
    RenderedImage,
    RenderOptions,
)

_ASSET_PREVIEW_SLOT = MediaSlot(x=38, y=154, width=500, height=500)


class HtmlRenderCapability(Protocol):
    """MaiBot `render.html2png` 能力的最小接口。"""

    async def html2png(self, html: str, **kwargs: object) -> object:
        """把完整 HTML 渲染为 PNG。"""


class PigCatcherRenderer:
    """封装本地模板、禁网渲染和 PNG 防御性校验。"""

    def __init__(
        self,
        capability: HtmlRenderCapability,
        options: RenderOptions,
        *,
        templates_root: Path | None = None,
    ) -> None:
        self.capability = capability
        self.options = options
        root = templates_root or Path(__file__).with_name("templates")
        self.templates_root = Path(root).resolve()
        self._environment = Environment(
            loader=FileSystemLoader(self.templates_root),
            autoescape=select_autoescape(("html", "xml")),
            undefined=StrictUndefined,
            enable_async=False,
        )
        self._theme_css = (self.templates_root / "theme.css").read_text(encoding="utf-8")

    async def render_framework_preview(
        self,
        view: FrameworkPreviewViewModel,
    ) -> RenderedImage:
        """渲染 2A 内部验收页；不会进入素材抽取池。"""

        template = self._environment.get_template("framework_preview.html")
        html = template.render(
            view=view,
            theme_css=self._theme_css,
            font_family=self.options.font_family,
        )
        try:
            result = await self.capability.html2png(
                html,
                selector="[data-pig-catcher-root]",
                viewport={
                    "width": self.options.card_width,
                    "height": self.options.viewport_height,
                },
                device_scale_factor=self.options.device_scale_factor,
                full_page=False,
                omit_background=False,
                wait_until="load",
                wait_for_selector="[data-render-ready='true']",
                wait_for_timeout_ms=0,
                render_timeout_ms=self.options.render_timeout_ms,
                allow_network=False,
            )
        except Exception as exc:
            raise RenderError(f"MaiBot HTML 图片渲染失败：{exc}") from exc
        return self._normalize_result(result)

    async def render_asset_preview_base(
        self,
        view: AssetPreviewViewModel,
    ) -> RenderedAssetPreviewBase:
        """渲染没有烘焙素材的底图，供静态图或逐帧动画后处理复用。"""

        template = self._environment.get_template("asset_preview.html")
        html = template.render(
            view=view,
            theme_css=self._theme_css,
            font_family=self.options.font_family,
            media_data_url="",
        )
        image = await self._render_asset_html(html)
        if (
            _ASSET_PREVIEW_SLOT.x + _ASSET_PREVIEW_SLOT.width > image.width
            or _ASSET_PREVIEW_SLOT.y + _ASSET_PREVIEW_SLOT.height > image.height
        ):
            raise RenderError("素材底图尺寸不足以容纳固定素材区域")
        return RenderedAssetPreviewBase(
            image=image,
            media_slot=_ASSET_PREVIEW_SLOT,
        )

    async def render_static_asset_preview(
        self,
        view: AssetPreviewViewModel,
        source_path: Path,
    ) -> RenderedImage:
        """把单帧正式素材以内联 data URL 交给禁网 HTML 渲染。"""

        path = Path(source_path)
        if not path.is_file():
            raise RenderError(f"静态素材不存在：{path.name}")
        try:
            payload = path.read_bytes()
            with Image.open(BytesIO(payload)) as source:
                if int(getattr(source, "n_frames", 1)) > 1:
                    raise RenderError("动画素材必须使用逐帧合成，不能截成静态预览")
                image_format = str(source.format or "").upper()
                source.load()
        except RenderError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise RenderError(f"静态素材无法解码：{path.name}") from exc
        mime_types = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "WEBP": "image/webp",
            "GIF": "image/gif",
        }
        mime_type = mime_types.get(image_format)
        if mime_type is None:
            raise RenderError(f"静态素材格式不受支持：{image_format or '未知'}")
        template = self._environment.get_template("asset_preview.html")
        html = template.render(
            view=view,
            theme_css=self._theme_css,
            font_family=self.options.font_family,
            media_data_url=(
                f"data:{mime_type};base64,"
                f"{base64.b64encode(payload).decode('ascii')}"
            ),
        )
        return await self._render_asset_html(html)

    async def _render_asset_html(self, html: str) -> RenderedImage:
        try:
            result = await self.capability.html2png(
                html,
                selector="[data-pig-catcher-root]",
                viewport={
                    "width": self.options.card_width,
                    "height": self.options.viewport_height,
                },
                device_scale_factor=self.options.device_scale_factor,
                full_page=False,
                omit_background=False,
                wait_until="load",
                wait_for_selector="[data-render-ready='true']",
                wait_for_timeout_ms=0,
                render_timeout_ms=self.options.render_timeout_ms,
                allow_network=False,
            )
        except Exception as exc:
            raise RenderError(f"MaiBot 素材图片渲染失败：{exc}") from exc
        return self._normalize_result(result)

    def _normalize_result(self, result: object) -> RenderedImage:
        image_base64 = self._result_value(result, "image_base64", "base64")
        if not isinstance(image_base64, str) or not image_base64.strip():
            raise RenderError("MaiBot 渲染结果缺少 image_base64")
        encoded = image_base64.strip()
        if encoded.startswith("data:"):
            header, separator, payload = encoded.partition(",")
            if not separator or ";base64" not in header.lower():
                raise RenderError("MaiBot 渲染结果包含不支持的 data URL")
            encoded = payload
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RenderError("MaiBot 渲染结果不是有效 Base64") from exc
        if not raw:
            raise RenderError("MaiBot 渲染结果是空图片")
        if len(raw) > self.options.max_png_bytes:
            raise RenderError(f"渲染图片大小 {len(raw)} 字节超过上限 {self.options.max_png_bytes} 字节")

        mime_type = self._result_value(result, "mime", "mime_type")
        if mime_type is not None and str(mime_type).lower() != "image/png":
            raise RenderError(f"渲染结果格式必须是 image/png，实际为 {mime_type}")
        width, height = self._inspect_png(raw)
        reported_width = self._optional_positive_int(self._result_value(result, "width"))
        reported_height = self._optional_positive_int(self._result_value(result, "height"))
        if reported_width is not None and abs(reported_width - width) > 1:
            raise RenderError(f"渲染结果宽度元数据 {reported_width} 与 PNG {width} 不一致")
        if reported_height is not None and abs(reported_height - height) > 1:
            raise RenderError(f"渲染结果高度元数据 {reported_height} 与 PNG {height} 不一致")
        return RenderedImage(
            image_base64=encoded,
            mime_type="image/png",
            width=width,
            height=height,
            byte_length=len(raw),
        )

    @staticmethod
    def _result_value(result: object, *names: str) -> Any:
        if isinstance(result, Mapping):
            for name in names:
                if name in result:
                    return result[name]
            return None
        for name in names:
            if hasattr(result, name):
                return getattr(result, name)
        return None

    @staticmethod
    def _optional_positive_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise RenderError("渲染结果包含无效尺寸元数据") from exc
        if normalized <= 0:
            raise RenderError("渲染结果包含非正尺寸元数据")
        return normalized

    @staticmethod
    def _inspect_png(raw: bytes) -> tuple[int, int]:
        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG":
                    raise RenderError(f"渲染结果并非 PNG，而是 {image.format or '未知格式'}")
                image.load()
                width, height = image.size
                rgba = image.convert("RGBA")
                alpha = rgba.getchannel("A")
                if alpha.getbbox() is None:
                    raise RenderError("渲染结果完全透明")
                if all(low == high for low, high in rgba.getextrema()):
                    raise RenderError("渲染结果是没有可见内容的纯色图片")
        except RenderError:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise RenderError("渲染结果无法作为安全 PNG 解码") from exc
        if width <= 0 or height <= 0:
            raise RenderError("渲染结果尺寸无效")
        return width, height
