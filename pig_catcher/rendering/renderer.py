"""通过 MaiBot 公共 HTML 渲染能力生成经过校验的 PNG。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
from collections import OrderedDict
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from PIL import Image, UnidentifiedImageError

from ..domain.dispatch_views import DispatchView
from ..domain.errors import RenderError
from .models import (
    AchievementBackfillSummaryViewModel,
    AchievementOverviewViewModel,
    AchievementPageViewModel,
    AchievementRankingViewModel,
    AchievementUnlockViewModel,
    AssetPreviewViewModel,
    BatchCookingViewModel,
    CatalogViewModel,
    DailyGiantsViewModel,
    EconomyReceiptViewModel,
    FoodCardViewModel,
    FoodCatalogViewModel,
    FoodInventoryViewModel,
    FrameworkPreviewViewModel,
    GroupEventViewModel,
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
    WeeklyCompetitionAwardViewModel,
    WeeklyCompetitionViewModel,
)

_ASSET_PREVIEW_SLOT = MediaSlot(x=38, y=154, width=500, height=500)
_PIG_CARD_SLOT = MediaSlot(x=38, y=164, width=480, height=480)
_FOOD_CARD_SLOT = MediaSlot(x=38, y=164, width=480, height=480)
_COMPACT_PREVIEW_MAX_SIDE = 256
_COMPACT_PREVIEW_WEBP_QUALITY = 82
_SINGLE_PREVIEW_WEBP_QUALITY = 90
_PREVIEW_CACHE_VERSION = "v1"
_MAX_HTML_RPC_BYTES = 12 * 1024 * 1024


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
        preview_cache_root: Path | None = None,
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
        self.preview_cache_root = Path(preview_cache_root).resolve() if preview_cache_root is not None else None
        self._preview_cache: OrderedDict[tuple[object, ...], str] = OrderedDict()
        self._preview_cache_size = 0
        self._preview_cache_lock = asyncio.Lock()
        self._preview_key_locks: dict[tuple[object, ...], asyncio.Lock] = {}
        self._preprocess_semaphore = asyncio.Semaphore(max(1, int(self.options.media_preprocess_concurrency)))
        self._disk_cache_lock = Lock()

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
        return await asyncio.to_thread(self._normalize_result, result)

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
        media_data_url = await self._cached_preview_data_url(
            source_path,
            is_animated=False,
            max_side=self.options.single_media_preview_max_side,
            quality=_SINGLE_PREVIEW_WEBP_QUALITY,
        )
        html = template.render(
            view=view,
            theme_css=self._theme_css,
            font_family=self.options.font_family,
            media_data_url=media_data_url,
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
            if source_path is not None and source_path.is_file():
                media_data_url = await self._cached_preview_data_url(
                    source_path,
                    is_animated=False,
                    max_side=self.options.single_media_preview_max_side,
                    quality=_SINGLE_PREVIEW_WEBP_QUALITY,
                )
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
        """Render one inventory page with deterministic animated-media previews."""

        media_data_urls = await self._list_media_data_urls(
            ((item.key, item.media_visible, item.is_animated) for item in view.items),
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

        media_data_urls = await self._list_media_data_urls(
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

    async def render_daily_giants(
        self,
        view: DailyGiantsViewModel,
        media_paths: Mapping[str, Path],
    ) -> RenderedImage:
        """Render today's current-group size and weight rankings."""

        items = (*view.size_items, *view.weight_items)
        media_data_urls = await self._list_media_data_urls(
            ((item.key, item.media_visible, item.is_animated) for item in items),
            media_paths,
        )
        return await self._render_template(
            "daily_giants.html",
            view=view,
            media_data_urls=media_data_urls,
        )

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
            if source_path is not None and source_path.is_file():
                media_data_url = await self._cached_preview_data_url(
                    source_path,
                    is_animated=False,
                    max_side=self.options.single_media_preview_max_side,
                    quality=_SINGLE_PREVIEW_WEBP_QUALITY,
                )
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

        media_data_urls = await self._list_media_data_urls(
            ((item.key, item.media_visible, item.is_animated) for item in view.items),
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

        media_data_urls = await self._list_media_data_urls(
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

    async def render_batch_cook(
        self,
        view: BatchCookingViewModel,
        media_paths: Mapping[str, Path],
    ) -> RenderedImage:
        """Render one batch-cooking grid with all produced foods."""

        media_data_urls = await self._list_media_data_urls(
            ((item.key, item.media_visible, item.is_animated) for item in view.items),
            media_paths,
        )
        return await self._render_template(
            "batch_cook.html",
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

    async def render_group_event(
        self,
        view: GroupEventViewModel,
        source_path: Path | None = None,
    ) -> RenderedImage:
        """Render a high-impact group-wide announcement with bounded food media."""

        media_data_url = ""
        if view.media_visible and source_path is not None and source_path.is_file():
            media_data_url = await self._cached_preview_data_url(
                source_path,
                is_animated=view.is_animated,
                max_side=_COMPACT_PREVIEW_MAX_SIDE,
                quality=_COMPACT_PREVIEW_WEBP_QUALITY,
            )
        return await self._render_template(
            "group_event.html",
            view=view,
            media_data_url=media_data_url,
        )

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

        media_data_urls = await self._list_media_data_urls(
            ((item.key, item.media_visible, item.is_animated) for item in view.items),
            media_paths,
        )
        return await self._render_template(
            "leaderboard.html",
            view=view,
            media_data_urls=media_data_urls,
        )

    async def render_achievement_overview(self, view: AchievementOverviewViewModel) -> RenderedImage:
        return await self._render_template("achievement_overview.html", view=view)

    async def render_achievement_page(self, view: AchievementPageViewModel) -> RenderedImage:
        return await self._render_template("achievement_page.html", view=view)

    async def render_achievement_unlock(self, view: AchievementUnlockViewModel) -> RenderedImage:
        return await self._render_template("achievement_unlock.html", view=view)

    async def render_achievement_backfill_summary(self, view: AchievementBackfillSummaryViewModel) -> RenderedImage:
        return await self._render_template("achievement_backfill_summary.html", view=view)

    async def render_achievement_ranking(self, view: AchievementRankingViewModel) -> RenderedImage:
        return await self._render_template("achievement_ranking.html", view=view)

    async def render_weekly_competition(self, view: WeeklyCompetitionViewModel) -> RenderedImage:
        return await self._render_template("weekly_competition.html", view=view)

    async def render_weekly_competition_award(self, view: WeeklyCompetitionAwardViewModel) -> RenderedImage:
        return await self._render_template("weekly_competition_award.html", view=view)

    async def render_dispatch(self, view: DispatchView, media_paths: Mapping[str, Path]) -> RenderedImage:
        previews = await self._list_media_data_urls(
            ((pig.short_code, bool(pig.image_relpath), False) for pig in view.pigs), media_paths,
        )
        return await self._render_template("dispatch.html", view=view, previews=previews)

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

    async def _list_media_data_urls(
        self,
        items: object,
        media_paths: Mapping[str, Path],
    ) -> dict[str, str]:
        requested: list[tuple[str, Path, bool]] = []
        for key, media_visible, is_animated in items:
            if not media_visible:
                continue
            path = media_paths.get(key)
            if path is None or not path.is_file():
                continue
            requested.append((str(key), Path(path), bool(is_animated)))
        if not requested:
            return {}
        previews = await asyncio.gather(
            *(
                self._cached_preview_data_url(
                    path,
                    is_animated=is_animated,
                    max_side=_COMPACT_PREVIEW_MAX_SIDE,
                    quality=_COMPACT_PREVIEW_WEBP_QUALITY,
                )
                for _, path, is_animated in requested
            )
        )
        return {key: preview for (key, _, _), preview in zip(requested, previews, strict=True)}

    async def _cached_preview_data_url(
        self,
        source_path: Path,
        *,
        is_animated: bool,
        max_side: int,
        quality: int,
    ) -> str:
        path = Path(source_path).resolve()
        try:
            stat = path.stat()
        except OSError as exc:
            raise RenderError(f"预览素材不存在：{path.name}") from exc
        if not path.is_file():
            raise RenderError(f"预览素材不存在：{path.name}")
        key: tuple[object, ...] = (
            _PREVIEW_CACHE_VERSION,
            str(path),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            bool(is_animated),
            int(max_side),
            int(quality),
        )
        async with self._preview_cache_lock:
            cached = self._preview_cache.get(key)
            if cached is not None:
                self._preview_cache.move_to_end(key)
                return cached
            key_lock = self._preview_key_locks.setdefault(key, asyncio.Lock())

        try:
            async with key_lock:
                async with self._preview_cache_lock:
                    cached = self._preview_cache.get(key)
                    if cached is not None:
                        self._preview_cache.move_to_end(key)
                        return cached
                async with self._preprocess_semaphore:
                    preview = await asyncio.to_thread(
                        self._load_or_build_preview_data_url,
                        key,
                        path,
                        is_animated,
                        int(max_side),
                        int(quality),
                    )
                await self._remember_preview(key, preview)
                return preview
        finally:
            # 单飞锁只服务一次 cache key 构建；素材换版/mtime 变化时不让旧锁
            # 在长时间运行的机器人进程中缓慢累积。
            async with self._preview_cache_lock:
                if self._preview_key_locks.get(key) is key_lock and not key_lock.locked():
                    self._preview_key_locks.pop(key, None)

    async def _remember_preview(self, key: tuple[object, ...], value: str) -> None:
        value_size = len(value)
        limit = max(1, int(self.options.media_preview_cache_bytes))
        async with self._preview_cache_lock:
            previous = self._preview_cache.pop(key, None)
            if previous is not None:
                self._preview_cache_size -= len(previous)
            if value_size <= limit:
                self._preview_cache[key] = value
                self._preview_cache_size += value_size
            while self._preview_cache and self._preview_cache_size > limit:
                _, stale_value = self._preview_cache.popitem(last=False)
                self._preview_cache_size -= len(stale_value)

    def _load_or_build_preview_data_url(
        self,
        key: tuple[object, ...],
        source_path: Path,
        is_animated: bool,
        max_side: int,
        quality: int,
    ) -> str:
        payload: bytes | None = None
        cache_path: Path | None = None
        if self.preview_cache_root is not None:
            root = self.preview_cache_root
            with self._disk_cache_lock:
                root.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()
                cache_path = root / f"{digest}.webp"
                if cache_path.is_file() and not cache_path.is_symlink():
                    try:
                        candidate = cache_path.read_bytes()
                        self._validate_preview_payload(candidate, max_side=max_side)
                        payload = candidate
                        os.utime(cache_path, None)
                    except (OSError, RenderError):
                        cache_path.unlink(missing_ok=True)
        if payload is None:
            payload = self._preview_bytes_sync(
                source_path,
                is_animated=is_animated,
                max_side=max_side,
                quality=quality,
            )
            if cache_path is not None:
                with self._disk_cache_lock:
                    temporary = cache_path.with_name(f".{cache_path.name}.{uuid4().hex}.tmp")
                    try:
                        temporary.write_bytes(payload)
                        os.replace(temporary, cache_path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    self._prune_disk_preview_cache()
        return f"data:image/webp;base64,{base64.b64encode(payload).decode('ascii')}"

    def _prune_disk_preview_cache(self) -> None:
        root = self.preview_cache_root
        if root is None or not root.is_dir():
            return
        limit = max(1, int(self.options.media_preview_disk_cache_bytes))
        files = sorted(
            (
                path
                for path in root.glob("*.webp")
                if path.is_file() and not path.is_symlink() and path.resolve().parent == root
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        total = sum(path.stat().st_size for path in files)
        for path in reversed(files):
            if total <= limit:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size

    @staticmethod
    def _preview_bytes_sync(
        source_path: Path,
        *,
        is_animated: bool,
        max_side: int,
        quality: int,
    ) -> bytes:
        path = Path(source_path)
        if not path.is_file():
            raise RenderError(f"预览素材不存在：{path.name}")
        try:
            with Image.open(path) as source:
                frame_count = int(getattr(source, "n_frames", 1))
                if is_animated:
                    source.seek(max(0, frame_count // 2))
                elif frame_count > 1:
                    raise RenderError("动画素材必须使用逐帧合成，不能截成静态图片")
                frame = source.convert("RGBA")
                frame.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                output = BytesIO()
                frame.save(output, format="WEBP", quality=quality, method=4)
                return output.getvalue()
        except RenderError:
            raise
        except (OSError, EOFError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise RenderError(f"预览素材无法解码：{path.name}") from exc

    @staticmethod
    def _validate_preview_payload(payload: bytes, *, max_side: int) -> None:
        try:
            with Image.open(BytesIO(payload)) as image:
                if image.format != "WEBP" or int(getattr(image, "n_frames", 1)) != 1:
                    raise RenderError("预览缓存格式无效")
                if max(image.size) > max_side:
                    raise RenderError("预览缓存尺寸超过上限")
                image.verify()
        except RenderError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise RenderError("预览缓存无法解码") from exc

    @staticmethod
    def _compact_preview_data_url(
        source_path: Path,
        *,
        is_animated: bool,
    ) -> str:
        """为紧凑列表生成有界静态预览，避免原图 Base64 撑破 RPC 帧。"""

        payload = PigCatcherRenderer._preview_bytes_sync(
            source_path,
            is_animated=is_animated,
            max_side=_COMPACT_PREVIEW_MAX_SIDE,
            quality=_COMPACT_PREVIEW_WEBP_QUALITY,
        )
        return f"data:image/webp;base64,{base64.b64encode(payload).decode('ascii')}"

    @staticmethod
    def _animated_preview_data_url(source_path: Path) -> str:
        """兼容旧调用：提取确定性的中间帧紧凑预览。"""

        return PigCatcherRenderer._compact_preview_data_url(
            source_path,
            is_animated=True,
        )

    @staticmethod
    def _validate_slot(slot: MediaSlot, image: RenderedImage) -> None:
        if slot.x + slot.width > image.width or slot.y + slot.height > image.height:
            raise RenderError("图片底图尺寸不足以容纳固定素材区域")

    async def _render_asset_html(self, html: str) -> RenderedImage:
        html_size = len(html.encode("utf-8"))
        if html_size > _MAX_HTML_RPC_BYTES:
            raise RenderError(f"HTML 渲染请求为 {html_size} 字节，超过插件的 {_MAX_HTML_RPC_BYTES} 字节 RPC 安全上限")
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
        return await asyncio.to_thread(self._normalize_result, result)

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
