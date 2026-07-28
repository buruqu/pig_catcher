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
    CatalogViewModel,
    EconomyReceiptViewModel,
    FoodCardViewModel,
    FoodCatalogViewModel,
    FoodInventoryViewModel,
    FrameworkPreviewViewModel,
    InventoryViewModel,
    ItemReceiptViewModel,
    LedgerViewModel,
    MediaSlot,
    PigCardViewModel,
    ProfileViewModel,
    RankingViewModel,
    RecordsViewModel,
    RenderedAssetPreviewBase,
    RenderedImage,
    RenderOptions,
    StoreViewModel,
    TradeListViewModel,
)

_ASSET_PREVIEW_SLOT = MediaSlot(x=38, y=154, width=500, height=500)
_PIG_CARD_SLOT = MediaSlot(x=38, y=164, width=480, height=480)
_FOOD_CARD_SLOT = MediaSlot(x=38, y=164, width=480, height=480)


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

        template = self._environment.get_template("asset_preview.html")
        html = template.render(
            view=view,
            theme_css=self._theme_css,
            font_family=self.options.font_family,
            media_data_url=self._source_data_url(source_path),
        )
        return await self._render_asset_html(html)

    async def render_pig_card_base(
        self,
        view: PigCardViewModel,
    ) -> RenderedAssetPreviewBase:
        """Render a single-pig card without baking animated source media."""

        image = await self._render_template(
            "pig_card.html",
            view=view,
            media_data_url="",
        )
        self._validate_slot(_PIG_CARD_SLOT, image)
        return RenderedAssetPreviewBase(image=image, media_slot=_PIG_CARD_SLOT)

    async def render_static_pig_card(
        self,
        view: PigCardViewModel,
        source_path: Path | None,
    ) -> RenderedImage:
        """Render one catch/detail card with static media or a privacy placeholder."""

        media_data_url = ""
        if view.media_visible:
            if source_path is None:
                raise RenderError("猪猪素材路径为空")
            media_data_url = self._source_data_url(source_path)
        return await self._render_template(
            "pig_card.html",
            view=view,
            media_data_url=media_data_url,
        )

    async def render_profile(self, view: ProfileViewModel) -> RenderedImage:
        """Render a player profile card."""

        return await self._render_template("profile.html", view=view)

    async def render_inventory(
        self,
        view: InventoryViewModel,
        media_paths: Mapping[str, Path],
    ) -> RenderedImage:
        """Render one inventory page without flattening animated assets."""

        media_data_urls = self._list_media_data_urls(
            (
                (item.key, item.media_visible, item.is_animated)
                for item in view.items
            ),
            media_paths,
        )
        return await self._render_template(
            "inventory.html",
            view=view,
            media_data_urls=media_data_urls,
        )

    async def render_catalog(
        self,
        view: CatalogViewModel,
        media_paths: Mapping[str, Path],
    ) -> RenderedImage:
        """Render one privacy-aware catalog page."""

        media_data_urls = self._list_media_data_urls(
            (
                (
                    item.key,
                    item.media_visible and item.discovered,
                    item.is_animated,
                )
                for item in view.items
            ),
            media_paths,
        )
        return await self._render_template(
            "catalog.html",
            view=view,
            media_data_urls=media_data_urls,
        )

    async def render_records(self, view: RecordsViewModel) -> RenderedImage:
        """Render current-group size and weight records."""

        return await self._render_template("records.html", view=view)

    async def render_item_receipt(
        self,
        view: ItemReceiptViewModel,
    ) -> RenderedImage:
        """Render an item equip or cancellation receipt."""

        return await self._render_template("item_receipt.html", view=view)

    async def render_food_card_base(
        self,
        view: FoodCardViewModel,
    ) -> RenderedAssetPreviewBase:
        """Render a food card without baking animated source media."""

        image = await self._render_template(
            "food_card.html",
            view=view,
            media_data_url="",
        )
        self._validate_slot(_FOOD_CARD_SLOT, image)
        return RenderedAssetPreviewBase(image=image, media_slot=_FOOD_CARD_SLOT)

    async def render_static_food_card(
        self,
        view: FoodCardViewModel,
        source_path: Path | None,
    ) -> RenderedImage:
        """Render one cooking/detail card with static media."""

        media_data_url = ""
        if view.media_visible:
            if source_path is None:
                raise RenderError("美食素材路径为空")
            media_data_url = self._source_data_url(source_path)
        return await self._render_template(
            "food_card.html",
            view=view,
            media_data_url=media_data_url,
        )

    async def render_food_inventory(
        self,
        view: FoodInventoryViewModel,
        media_paths: Mapping[str, Path],
    ) -> RenderedImage:
        """Render one food inventory page."""

        media_data_urls = self._list_media_data_urls(
            (
                (item.key, item.media_visible, item.is_animated)
                for item in view.items
            ),
            media_paths,
        )
        return await self._render_template(
            "food_inventory.html",
            view=view,
            media_data_urls=media_data_urls,
        )

    async def render_food_catalog(
        self,
        view: FoodCatalogViewModel,
        media_paths: Mapping[str, Path],
    ) -> RenderedImage:
        """Render one privacy-aware food catalog page."""

        media_data_urls = self._list_media_data_urls(
            (
                (
                    item.key,
                    item.media_visible and item.discovered,
                    item.is_animated,
                )
                for item in view.items
            ),
            media_paths,
        )
        return await self._render_template(
            "food_catalog.html",
            view=view,
            media_data_urls=media_data_urls,
        )

    async def render_store(self, view: StoreViewModel) -> RenderedImage:
        """Render one store page."""

        return await self._render_template("store.html", view=view)

    async def render_economy_receipt(
        self,
        view: EconomyReceiptViewModel,
    ) -> RenderedImage:
        """Render one purchase, eating, or sale receipt."""

        return await self._render_template("economy_receipt.html", view=view)

    async def render_ledger(self, view: LedgerViewModel) -> RenderedImage:
        """Render one reconciled pig-coin ledger page."""

        return await self._render_template("ledger.html", view=view)

    async def render_trade_list(
        self,
        view: TradeListViewModel,
    ) -> RenderedImage:
        """Render one current-player bilateral trade page."""

        return await self._render_template("trade_list.html", view=view)

    async def render_ranking(
        self,
        view: RankingViewModel,
        media_paths: Mapping[str, Path],
    ) -> RenderedImage:
        """Render one group leaderboard with static showcase media."""

        media_data_urls = self._list_media_data_urls(
            (
                (item.key, item.media_visible, item.is_animated)
                for item in view.items
            ),
            media_paths,
        )
        return await self._render_template(
            "leaderboard.html",
            view=view,
            media_data_urls=media_data_urls,
        )

    async def _render_template(
        self,
        template_name: str,
        **context: object,
    ) -> RenderedImage:
        template = self._environment.get_template(template_name)
        html = template.render(
            **context,
            theme_css=self._theme_css,
            font_family=self.options.font_family,
        )
        return await self._render_asset_html(html)

    def _list_media_data_urls(
        self,
        items: object,
        media_paths: Mapping[str, Path],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, media_visible, is_animated in items:
            if not media_visible or is_animated:
                continue
            path = media_paths.get(key)
            if path is None:
                raise RenderError(f"列表素材映射缺少项目：{key}")
            result[key] = self._source_data_url(path)
        return result

    @staticmethod
    def _source_data_url(source_path: Path) -> str:
        path = Path(source_path)
        if not path.is_file():
            raise RenderError(f"静态素材不存在：{path.name}")
        try:
            payload = path.read_bytes()
            with Image.open(BytesIO(payload)) as source:
                if int(getattr(source, "n_frames", 1)) > 1:
                    raise RenderError("动画素材必须使用逐帧合成，不能截成静态图片")
                image_format = str(source.format or "").upper()
                source.load()
        except RenderError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise RenderError(f"静态素材无法解码：{path.name}") from exc
        mime_type = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "WEBP": "image/webp",
            "GIF": "image/gif",
        }.get(image_format)
        if mime_type is None:
            raise RenderError(f"静态素材格式不受支持：{image_format or '未知'}")
        return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"

    @staticmethod
    def _validate_slot(slot: MediaSlot, image: RenderedImage) -> None:
        if slot.x + slot.width > image.width or slot.y + slot.height > image.height:
            raise RenderError("图片底图尺寸不足以容纳固定素材区域")

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
