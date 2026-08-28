"""白色淡粉模板、PNG 校验与发送降级。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from pig_catcher.domain.errors import RenderError
from pig_catcher.rendering import (
    AnimatedCardComposer,
    AssetPreviewViewModel,
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
    FrameworkPreviewViewModel,
    GroupEventRowViewModel,
    GroupEventViewModel,
    InventoryItemViewModel,
    InventoryViewModel,
    ItemReceiptViewModel,
    LedgerEntryViewModel,
    LedgerViewModel,
    MediaSlot,
    PigCardViewModel,
    PigCatcherRenderer,
    ProfileViewModel,
    RankingItemViewModel,
    RankingViewModel,
    RecordItemViewModel,
    RecordsViewModel,
    RenderDelivery,
    RenderedImage,
    RenderOptions,
    StoreConsumableProbabilityRowViewModel,
    StoreProbabilityRowViewModel,
    StoreProductViewModel,
    StoreViewModel,
    TradeListItemViewModel,
    TradeListViewModel,
)
from pig_catcher.version import (
    ASSET_MANIFEST_VERSION,
    FRAMEWORK_PHASE,
    PLUGIN_VERSION,
    RULESET_VERSION,
    SCHEMA_VERSION,
)

from .helpers import FakeRender, FakeSend, png_base64


def test_catalog_media_tracks_do_not_expand_to_intrinsic_image_size():
    css = (Path(__file__).parents[1] / "pig_catcher/rendering/templates/theme.css").read_text(encoding="utf-8")
    media = css.split(".catalog-item__media {", 1)[1].split("}", 1)[0]
    image = css.split(".catalog-item__media img {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr)" in media
    assert "grid-template-rows: minmax(0, 1fr)" in media
    assert "min-width: 0" in image and "min-height: 0" in image


def _options(**updates: object) -> RenderOptions:
    values = {
        "card_width": 1200,
        "viewport_height": 1600,
        "device_scale_factor": 1.0,
        "render_timeout_ms": 15000,
        "max_png_bytes": 12 * 1024 * 1024,
        "max_animation_bytes": 50 * 1024 * 1024,
        "missing_frame_duration_ms": 100,
        "font_family": '"Microsoft YaHei", sans-serif',
    }
    values.update(updates)
    return RenderOptions(**values)


def _view(plugin_version: str = PLUGIN_VERSION) -> FrameworkPreviewViewModel:
    return FrameworkPreviewViewModel(
        plugin_version=plugin_version,
        framework_phase=FRAMEWORK_PHASE,
        schema_version=SCHEMA_VERSION,
        asset_manifest_version=ASSET_MANIFEST_VERSION,
        ruleset_version=RULESET_VERSION,
    )


def _solid_png_base64() -> str:
    output = BytesIO()
    Image.new("RGBA", (64, 64), "white").save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_renderer_uses_local_template_disables_network_and_validates_png() -> None:
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    image = await renderer.render_framework_preview(_view())
    assert image.mime_type == "image/png"
    assert (image.width, image.height) == (1200, 900)
    html, options = capability.calls[0]
    assert "抓猪" in html
    assert "#fff0f5" in html
    assert "素材暂未公开" in html
    assert "<img" not in html
    assert "http://" not in html and "https://" not in html
    assert options["allow_network"] is False
    assert options["selector"] == "[data-pig-catcher-root]"


@pytest.mark.asyncio
async def test_renderer_escapes_view_model_text() -> None:
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    await renderer.render_framework_preview(_view("<script>alert(1)</script>"))
    html, _ = capability.calls[0]
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.asyncio
async def test_asset_preview_base_has_stable_animation_slot_and_escapes_text() -> None:
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    preview = await renderer.render_asset_preview_base(
        AssetPreviewViewModel(
            display_name="<script>星星猪</script>",
            description="闪闪心动 & 群聊演出",
            rarity=5,
            kind_label="猪猪",
            media_format="PNG",
            frame_count=1,
            collection_name="Poppin'Party",
            collection_progress="0/5",
            character_name="户山香澄",
        )
    )
    assert preview.media_slot == MediaSlot(x=38, y=154, width=500, height=500)
    html, options = capability.calls[0]
    assert "<script>星星猪</script>" not in html
    assert "&lt;script&gt;" in html
    assert "Poppin&#39;Party" in html
    assert "<img" not in html
    assert options["allow_network"] is False


@pytest.mark.asyncio
async def test_static_asset_preview_inlines_media_but_rejects_animation(
    tmp_path: Path,
) -> None:
    static_path = tmp_path / "pig.webp"
    Image.new("RGBA", (64, 64), "#F58CAD").save(static_path, format="WEBP")
    view = AssetPreviewViewModel(
        display_name="测试猪",
        description="静态素材",
        rarity=1,
        kind_label="猪猪",
        media_format="WEBP",
        frame_count=1,
    )
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    await renderer.render_static_asset_preview(view, static_path)
    html, options = capability.calls[0]
    assert "data:image/webp;base64," in html
    assert options["allow_network"] is False

    animated_path = tmp_path / "animated.jpg"
    _write_animated_gif(animated_path, durations=[100, 100, 100])
    with pytest.raises(RenderError, match="逐帧合成"):
        await renderer.render_static_asset_preview(view, animated_path)


@pytest.mark.asyncio
async def test_renderer_tolerates_one_pixel_browser_rounding() -> None:
    capability = FakeRender()
    capability.result = {
        "image_base64": png_base64(),
        "mime": "image/png",
        "width": 1199,
        "height": 901,
    }
    renderer = PigCatcherRenderer(capability, _options())
    image = await renderer.render_framework_preview(_view())
    assert (image.width, image.height) == (1200, 900)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"image_base64": "not-base64", "mime": "image/png"},
        {"image_base64": png_base64(), "mime": "image/jpeg"},
        {
            "image_base64": png_base64(),
            "mime": "image/png",
            "width": 1,
            "height": 900,
        },
        {
            "image_base64": png_base64(transparent=True),
            "mime": "image/png",
            "width": 1200,
            "height": 900,
        },
        {
            "image_base64": _solid_png_base64(),
            "mime": "image/png",
            "width": 64,
            "height": 64,
        },
    ],
)
async def test_renderer_rejects_invalid_results(result: object) -> None:
    capability = FakeRender()
    capability.result = result
    renderer = PigCatcherRenderer(capability, _options())
    with pytest.raises(RenderError):
        await renderer.render_framework_preview(_view())


@pytest.mark.asyncio
async def test_renderer_rejects_non_png_and_oversized_output() -> None:
    jpeg = BytesIO()
    Image.new("RGB", (32, 32), "white").save(jpeg, format="JPEG")
    capability = FakeRender()
    capability.result = {
        "image_base64": base64.b64encode(jpeg.getvalue()).decode("ascii"),
        "mime": "image/png",
    }
    renderer = PigCatcherRenderer(capability, _options())
    with pytest.raises(RenderError, match="并非 PNG"):
        await renderer.render_framework_preview(_view())

    capability.result = {"image_base64": png_base64(), "mime": "image/png"}
    renderer = PigCatcherRenderer(capability, _options(max_png_bytes=100))
    with pytest.raises(RenderError, match="超过上限"):
        await renderer.render_framework_preview(_view())


@pytest.mark.asyncio
async def test_delivery_sends_image_when_render_succeeds() -> None:
    send = FakeSend()
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery"),
        fallback_to_text=True,
    )
    rendered = PigCatcherRenderer(FakeRender(), _options())
    assert await delivery.send_image_or_text(
        stream_id="stream",
        render=lambda: rendered.render_framework_preview(_view()),
        fallback_text="文字兜底",
    )
    assert len(send.images) == 1
    assert send.texts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["render", "image"])
async def test_delivery_falls_back_to_text(failure_at: str) -> None:
    send = FakeSend()
    capability = FakeRender()
    if failure_at == "render":
        capability.error = TimeoutError("render timeout")
    else:
        send.image_error = RuntimeError("adapter unavailable")
    renderer = PigCatcherRenderer(capability, _options())
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery"),
        fallback_to_text=True,
    )
    assert await delivery.send_image_or_text(
        stream_id="stream",
        render=lambda: renderer.render_framework_preview(_view()),
        fallback_text="文字兜底",
    )
    assert send.texts == [("stream", "文字兜底")]


@pytest.mark.asyncio
async def test_delivery_treats_false_send_result_as_failure() -> None:
    send = FakeSend()
    send.image_success = False
    renderer = PigCatcherRenderer(FakeRender(), _options())
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery"),
        fallback_to_text=True,
    )
    assert await delivery.send_image_or_text(
        stream_id="stream",
        render=lambda: renderer.render_framework_preview(_view()),
        fallback_text="文字兜底",
    )
    assert len(send.images) == 1
    assert send.texts == [("stream", "文字兜底")]

    send.text_success = False
    assert not await delivery.send_image_or_text(
        stream_id="stream",
        render=lambda: renderer.render_framework_preview(_view()),
        fallback_text="仍然发送失败",
        rendering_enabled=False,
    )


@pytest.mark.asyncio
async def test_delivery_can_disable_rendering_or_fallback() -> None:
    send = FakeSend()
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery"),
        fallback_to_text=True,
    )
    assert await delivery.send_image_or_text(
        stream_id="stream",
        render=lambda: PigCatcherRenderer(FakeRender(), _options()).render_framework_preview(_view()),
        fallback_text="文字模式",
        rendering_enabled=False,
    )
    assert send.texts == [("stream", "文字模式")]

    no_fallback = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery"),
        fallback_to_text=False,
    )

    async def broken_render() -> object:
        raise RuntimeError("broken")

    assert not await no_fallback.send_image_or_text(
        stream_id="stream",
        render=broken_render,
        fallback_text="不得发送",
    )
    assert len(send.texts) == 1


def _write_animated_gif(
    path: Path,
    *,
    durations: list[int] | None,
    loop: int = 0,
) -> None:
    frames = [
        Image.new("RGBA", (80, 60), color)
        for color in ("#F58CAD", "#66BFA3", "#5B8FD1")
    ]
    options: dict[str, object] = {
        "format": "GIF",
        "save_all": True,
        "append_images": frames[1:],
        "loop": loop,
        "disposal": 2,
    }
    if durations is not None:
        options["duration"] = durations
    frames[0].save(path, **options)


@pytest.mark.asyncio
async def test_animated_inventory_and_catalog_use_static_middle_frame_preview(
    tmp_path: Path,
) -> None:
    source = tmp_path / "animated.gif"
    _write_animated_gif(source, durations=[80, 120, 200])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    inventory = InventoryViewModel(
        display_name="测试成员",
        page=1,
        page_count=1,
        total_count=1,
        rarity=None,
        sort="价值",
        items=(
            InventoryItemViewModel(
                key="animated",
                display_name="动画猪",
                short_code="AAAA0001",
                rarity=3,
                size_value=50.0,
                weight_value=60.0,
                fat_label="均衡",
                official_value=180,
                media_visible=True,
                is_animated=True,
                image_fit="contain",
            ),
        ),
    )
    await renderer.render_inventory(inventory, {"animated": source})
    inventory_html, _ = capability.calls[-1]
    assert "data:image/webp;base64," in inventory_html
    assert "动态猪猪<br>详情查看" not in inventory_html

    catalog = CatalogViewModel(
        display_name="测试成员",
        total_count=1,
        rarity=None,
        undiscovered_only=False,
        collected_count=1,
        visible_catalog_total=1,
        items=(
            CatalogItemViewModel(
                key="animated",
                display_name="动画猪",
                rarity=3,
                discovered=True,
                acquired_count=1,
                best_size=50.0,
                best_weight=60.0,
                collection_name="",
                character_name="",
                media_visible=True,
                is_animated=True,
                image_fit="contain",
            ),
        ),
    )
    await renderer.render_catalog(catalog, {"animated": source})
    catalog_html, _ = capability.calls[-1]
    assert "data:image/webp;base64," in catalog_html
    assert "动态猪猪<br>详情查看" not in catalog_html
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


@pytest.mark.asyncio
async def test_complete_catalog_uses_bounded_compact_previews_for_rpc(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large-noise.png"
    Image.effect_noise((1024, 1024), 100).convert("RGB").save(
        source,
        format="PNG",
    )
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    item_count = 92
    assert source.stat().st_size * item_count * 4 // 3 > 16 * 1024 * 1024

    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    catalog = CatalogViewModel(
        display_name="正式大图鉴传输测试",
        total_count=item_count,
        rarity=None,
        undiscovered_only=False,
        collected_count=item_count,
        visible_catalog_total=item_count,
        items=tuple(
            CatalogItemViewModel(
                key=f"pig-{index}",
                display_name=f"大图素材猪 {index}",
                rarity=(index % 6) + 1,
                discovered=True,
                acquired_count=1,
                best_size=50.0,
                best_weight=60.0,
                collection_name="",
                character_name="",
                media_visible=True,
                is_animated=False,
                image_fit="contain",
            )
            for index in range(item_count)
        ),
    )
    await renderer.render_catalog(
        catalog,
        {f"pig-{index}": source for index in range(item_count)},
    )
    html, _ = capability.calls[-1]
    assert len(html.encode("utf-8")) < 12 * 1024 * 1024
    assert html.count("data:image/webp;base64,") == item_count

    encoded_preview = html.split("data:image/webp;base64,", 1)[1].split('"', 1)[0]
    with Image.open(BytesIO(base64.b64decode(encoded_preview))) as preview:
        assert preview.format == "WEBP"
        assert max(preview.size) <= 256
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


@pytest.mark.asyncio
async def test_preview_cache_is_non_blocking_singleflight_and_reused_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "large-source.png"
    Image.effect_noise((1024, 1024), 100).convert("RGB").save(source, format="PNG")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    cache_root = tmp_path / "preview-cache"
    renderer = PigCatcherRenderer(
        FakeRender(),
        _options(),
        preview_cache_root=cache_root,
    )
    original = renderer._preview_bytes_sync
    calls = 0

    def slow_preview(*args: object, **kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return original(*args, **kwargs)

    monkeypatch.setattr(renderer, "_preview_bytes_sync", slow_preview)
    loop = asyncio.get_running_loop()
    started = loop.time()

    async def heartbeat_delay() -> float:
        await asyncio.sleep(0.01)
        return loop.time() - started

    first, second, heartbeat = await asyncio.gather(
        renderer._cached_preview_data_url(
            source,
            is_animated=False,
            max_side=768,
            quality=90,
        ),
        renderer._cached_preview_data_url(
            source,
            is_animated=False,
            max_side=768,
            quality=90,
        ),
        heartbeat_delay(),
    )
    assert first == second
    assert calls == 1
    assert heartbeat < 0.08
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert len(list(cache_root.glob("*.webp"))) == 1
    assert renderer._preview_key_locks == {}

    restarted = PigCatcherRenderer(
        FakeRender(),
        _options(),
        preview_cache_root=cache_root,
    )

    def unexpected_rebuild(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("warm disk cache should avoid rebuilding the preview")

    monkeypatch.setattr(restarted, "_preview_bytes_sync", unexpected_rebuild)
    warm = await restarted._cached_preview_data_url(
        source,
        is_animated=False,
        max_side=768,
        quality=90,
    )
    assert warm == first
    assert restarted._preview_key_locks == {}


@pytest.mark.asyncio
async def test_delivery_queue_overload_falls_back_without_starting_extra_render() -> None:
    send = FakeSend()
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery.queue"),
        fallback_to_text=True,
        max_concurrent_deliveries=1,
        queue_timeout_ms=20,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    render_calls = 0

    async def slow_render() -> RenderedImage:
        nonlocal render_calls
        render_calls += 1
        entered.set()
        await release.wait()
        return RenderedImage(
            image_base64=_solid_png_base64(),
            mime_type="image/png",
            width=64,
            height=64,
            byte_length=64,
        )

    first = asyncio.create_task(
        delivery.send_image_or_text(
            stream_id="first",
            render=slow_render,
            fallback_text="first fallback",
        )
    )
    await entered.wait()
    try:
        assert await delivery.send_image_or_text(
            stream_id="second",
            render=slow_render,
            fallback_text="queue fallback",
        )
    finally:
        release.set()
    assert await first
    assert render_calls == 1
    assert send.texts == [("second", "queue fallback")]
    assert len(send.images) == 1


@pytest.mark.asyncio
async def test_slow_qq_image_send_does_not_occupy_render_slot() -> None:
    class GatedImageSend(FakeSend):
        def __init__(self) -> None:
            super().__init__()
            self.first_send_started = asyncio.Event()
            self.release_first_send = asyncio.Event()

        async def image(self, image_base64: str, stream_id: str) -> bool:
            self.images.append((stream_id, image_base64))
            if stream_id == "first":
                self.first_send_started.set()
                await self.release_first_send.wait()
            return True

    send = GatedImageSend()
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery.split-slots"),
        fallback_to_text=True,
        max_concurrent_deliveries=1,
        max_concurrent_image_sends=1,
        queue_timeout_ms=100,
        image_send_queue_timeout_ms=1000,
    )
    second_rendered = asyncio.Event()

    async def render(stream_id: str) -> RenderedImage:
        if stream_id == "second":
            second_rendered.set()
        return RenderedImage(
            image_base64=_solid_png_base64(),
            mime_type="image/png",
            width=64,
            height=64,
            byte_length=64,
        )

    first = asyncio.create_task(
        delivery.send_image_or_text(
            stream_id="first",
            render=lambda: render("first"),
            fallback_text="first fallback",
        )
    )
    await send.first_send_started.wait()
    second = asyncio.create_task(
        delivery.send_image_or_text(
            stream_id="second",
            render=lambda: render("second"),
            fallback_text="second fallback",
        )
    )
    await asyncio.wait_for(second_rendered.wait(), timeout=0.2)
    send.release_first_send.set()
    assert await first
    assert await second
    assert [stream_id for stream_id, _ in send.images] == ["first", "second"]
    assert send.texts == []


@pytest.mark.asyncio
async def test_delivery_bounds_render_and_text_fallback_time() -> None:
    class SlowTextSend(FakeSend):
        async def text(self, text: str, stream_id: str) -> bool:
            self.texts.append((stream_id, text))
            await asyncio.Event().wait()
            return True

    send = SlowTextSend()
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery.deadline"),
        fallback_to_text=True,
        render_timeout_ms=20,
        text_send_timeout_ms=20,
    )

    async def stalled_render() -> RenderedImage:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    started = time.monotonic()
    assert not await delivery.send_image_or_text(
        stream_id="stream",
        render=stalled_render,
        fallback_text="bounded fallback",
    )
    assert time.monotonic() - started < 0.5
    assert send.texts == [("stream", "bounded fallback")]


@pytest.mark.asyncio
async def test_delivery_does_not_double_send_after_ambiguous_image_timeout() -> None:
    class SlowImageSend(FakeSend):
        async def image(self, image_base64: str, stream_id: str) -> bool:
            self.images.append((stream_id, image_base64))
            await asyncio.Event().wait()
            return True

    send = SlowImageSend()
    delivery = RenderDelivery(
        send,
        logger=logging.getLogger("test.render.delivery.image-timeout"),
        fallback_to_text=True,
        image_send_timeout_ms=20,
    )

    async def render() -> RenderedImage:
        return RenderedImage(
            image_base64=_solid_png_base64(),
            mime_type="image/png",
            width=64,
            height=64,
            byte_length=64,
        )

    assert not await delivery.send_image_or_text(
        stream_id="stream",
        render=render,
        fallback_text="must not duplicate",
    )
    assert len(send.images) == 1
    assert send.texts == []


@pytest.mark.asyncio
async def test_renderer_rejects_oversized_html_before_rpc() -> None:
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    with pytest.raises(RenderError, match="RPC 安全上限"):
        await renderer._render_asset_html("x" * (12 * 1024 * 1024 + 1))
    assert capability.calls == []


@pytest.mark.asyncio
async def test_animation_composer_preserves_frames_timing_loop_and_source_bytes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "animated.jpg"
    _write_animated_gif(source, durations=[80, 120, 200], loop=2)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    base = await PigCatcherRenderer(FakeRender(), _options()).render_framework_preview(_view())
    result = await AnimatedCardComposer(
        max_output_bytes=10 * 1024 * 1024,
        missing_frame_duration_ms=100,
    ).compose(
        base=base,
        source_path=source,
        slot=MediaSlot(x=100, y=100, width=240, height=180),
    )
    assert result.mime_type == "image/gif"
    assert result.frame_count == 3
    assert result.total_duration_ms == 400
    assert result.loop_count == 2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash

    raw = base64.b64decode(result.image_base64)
    with Image.open(BytesIO(raw)) as image:
        assert image.format == "GIF"
        assert image.size == (1200, 900)
        assert image.n_frames == 3
        assert image.info["loop"] == 2
        durations = []
        colors = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            durations.append(image.info.get("duration"))
            colors.append(image.convert("RGB").getpixel((220, 190)))
    assert durations == [80, 120, 200]
    assert len(set(colors)) == 3


@pytest.mark.asyncio
async def test_animation_composer_uses_compatibility_timing_only_when_source_omits_it(
    tmp_path: Path,
) -> None:
    source = tmp_path / "untimed.gif"
    _write_animated_gif(source, durations=None)
    base = await PigCatcherRenderer(FakeRender(), _options()).render_framework_preview(_view())
    result = await AnimatedCardComposer(
        max_output_bytes=10 * 1024 * 1024,
        missing_frame_duration_ms=90,
    ).compose(
        base=base,
        source_path=source,
        slot=MediaSlot(x=40, y=40, width=160, height=120, fit="cover"),
    )
    assert result.frame_count == 3
    assert result.total_duration_ms == 270


@pytest.mark.asyncio
async def test_animation_composer_rejects_over_budget_before_retaining_full_card_frames(
    tmp_path: Path,
) -> None:
    source = tmp_path / "many-frames.gif"
    frames = [
        Image.new("RGBA", (80, 60), (index * 9 % 255, index * 17 % 255, index * 29 % 255, 255))
        for index in range(24)
    ]
    frames[0].save(
        source,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[50] * len(frames),
        loop=0,
        disposal=2,
    )
    base = await PigCatcherRenderer(FakeRender(), _options()).render_framework_preview(_view())
    composer = AnimatedCardComposer(
        max_output_bytes=10 * 1024 * 1024,
        max_working_memory_bytes=32 * 1024 * 1024,
    )
    with pytest.raises(RenderError, match="估算工作内存"):
        await composer.compose(
            base=base,
            source_path=source,
            slot=MediaSlot(x=40, y=40, width=160, height=120),
        )


@pytest.mark.asyncio
async def test_third_round_templates_render_all_business_views(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pig.png"
    Image.new("RGBA", (96, 96), "#F58CAD").save(source, format="PNG")
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    pig = PigCardViewModel(
        mode_label="抓猪成功",
        display_name="<script>超长测试猪</script>",
        owner_display_name="测试成员",
        rarity=4,
        rarity_name="极品佳肴猪",
        short_code="A19F2C3D",
        description="一只用于第三轮渲染验收的猪。",
        size_value=48.2,
        size_percentile=0.71,
        weight_value=87.4,
        weight_percentile=0.66,
        fat_ratio=52.0,
        fat_label="均衡",
        official_value=530,
        acquired_at="2026-07-28 12:00",
        coin_reward=30,
        experience_reward=45,
        coin_balance=120,
        total_experience=600,
        player_level=4,
        level_title="抓猪老手",
        next_level_experience=800,
        level_progress_percent=42.86,
        daily_count=2,
        daily_limit=30,
        quota_exempt_catch=True,
        catalog_new=True,
        size_record=True,
        effect_summaries=(
            "糖醋排骨全群加成（发动群友：数佳）：5 星与 6 星相对权重 ×1.007。",
        ),
        probability_line="1★90.0% 5★8.993% 6★1.007%",
        probability_sources="美食加成 ×1",
    )
    await renderer.render_static_pig_card(pig, source)
    pig_html, _ = capability.calls[-1]
    assert "<script>超长测试猪</script>" not in pig_html
    assert "&lt;script&gt;" in pig_html
    assert "data:image/webp;base64," in pig_html
    assert "体型新纪录" in pig_html
    assert "Lv.4 · 抓猪老手" in pig_html
    assert "+45 EXP · 600/800" in pig_html
    assert 'width: 42.86%' in pig_html
    assert "六星菜专属次数 · 本次未扣正常额度" in pig_html
    assert "发动群友：数佳" in pig_html
    assert "OFFICIAL_OPEN_ID" not in pig_html
    assert "6★1.007%" in pig_html
    await renderer.render_static_pig_card(replace(pig, coin_reward=None), None)
    missing_pig_html, _ = capability.calls[-1]
    assert "素材文件暂时不可用" in missing_pig_html
    assert "文件暂缺" in missing_pig_html

    collections = (
        CollectionProgressViewModel(
            collection_name="Poppin'Party",
            collaboration_name="BanG Dream!",
            collected_count=2,
            available_count=2,
            total_count=5,
        ),
    )
    await renderer.render_profile(
        ProfileViewModel(
            display_name="测试成员",
            level=21,
            title="抓群友",
            total_experience=20000,
            next_threshold=22050,
            progress_percent=0.0,
            coin_balance=120,
            total_catches=9,
            active_pigs=8,
            catalog_count=5,
            visible_catalog_total=81,
            held_records=2,
            daily_count=2,
            daily_limit=30,
            cooldown_remaining_seconds=17,
            feed_level=1,
            armed_item_name="巨物玉米",
            armed_item_quantity=2,
            collections=collections,
            level_catch_base_high_percent=13.0,
            level_catch_adjusted_high_percent=13.16,
            level_cooking_bonus_percent=1.0,
            level_bonus_cap_level=21,
            veteran_tier=1,
            veteran_milestone_coin_reward=1_000,
            veteran_cumulative_coin_reward=1_000,
            veteran_claimed_tier=1,
            veteran_next_tier_level=31,
            veteran_next_tier_coin_reward=2_000,
        )
    )
    profile_html = capability.calls[-1][0]
    assert "Lv.21" in profile_html
    assert "Poppin&#39;Party" in profile_html
    assert "等级概率加成" in profile_html
    assert "13.00%" in profile_html
    assert "13.16%" in profile_html
    assert "荣誉称号不改变概率" in profile_html
    assert "猪币里程碑 · 1/5 档" in profile_html
    assert "本档 +1000 · 累计 +1000 猪币" in profile_html
    assert "下一档 Lv.31：+2000 猪币" in profile_html

    inventory = InventoryViewModel(
        display_name="测试成员",
        page=1,
        page_count=1,
        total_count=2,
        rarity=None,
        sort="价值",
        items=(
            InventoryItemViewModel(
                key="static",
                display_name="静态猪",
                short_code="AAAA0001",
                rarity=2,
                size_value=40.0,
                weight_value=50.0,
                fat_label="均衡",
                official_value=70,
                media_visible=True,
                is_animated=False,
                image_fit="contain",
                is_favorite=True,
            ),
            InventoryItemViewModel(
                key="animated",
                display_name="动画猪",
                short_code="BBBB0002",
                rarity=3,
                size_value=50.0,
                weight_value=60.0,
                fat_label="偏肥",
                official_value=180,
                media_visible=True,
                is_animated=True,
                image_fit="contain",
            ),
        ),
    )
    await renderer.render_inventory(inventory, {"static": source})
    inventory_html, _ = capability.calls[-1]
    assert "动态猪猪" in inventory_html
    assert "已收藏" in inventory_html and 'class="asset-icon asset-icon--favorite"' in inventory_html
    assert inventory_html.count("data:image/webp;base64,") == 1

    catalog = CatalogViewModel(
        display_name="测试成员",
        total_count=2,
        rarity=None,
        undiscovered_only=False,
        collected_count=1,
        visible_catalog_total=81,
        items=(
            CatalogItemViewModel(
                key="static",
                display_name="静态猪",
                rarity=2,
                discovered=True,
                acquired_count=3,
                best_size=41.0,
                best_weight=52.0,
                collection_name="",
                character_name="",
                media_visible=True,
                is_animated=False,
                image_fit="contain",
            ),
            CatalogItemViewModel(
                key="unknown",
                display_name="不得泄露的群友猪",
                rarity=6,
                discovered=False,
                acquired_count=0,
                best_size=None,
                best_weight=None,
                collection_name="",
                character_name="",
                media_visible=False,
                is_animated=False,
                image_fit="contain",
            ),
        ),
        collections=collections,
    )
    await renderer.render_catalog(catalog, {"static": source})
    catalog_html, _ = capability.calls[-1]
    assert "不得泄露的群友猪" not in catalog_html
    assert "尚未发现" in catalog_html
    assert "2 星品质" in catalog_html
    assert "6 星品质" in catalog_html
    assert "第 1/1 页" not in catalog_html

    await renderer.render_records(
        RecordsViewModel(
            group_name="测试群",
            page=1,
            page_count=1,
            total_count=1,
            items=(
                RecordItemViewModel(
                    record_label="体型",
                    record_value=66.6,
                    unit="cm",
                    display_name="纪录猪",
                    rarity=5,
                    short_code="CCCC0003",
                    holder_display_name="纪录保持者",
                    achieved_at="2026-07-28 12:00",
                ),
            ),
        )
    )
    assert "纪录保持者" in capability.calls[-1][0]

    await renderer.render_daily_giants(
        DailyGiantsViewModel(
            group_name="测试巨物群",
            date_label="北京时间 2026-08-12 12:00 截止",
            participant_count=2,
            catch_count=3,
            size_items=(
                DailyGiantItemViewModel(
                    key="size:giant-one",
                    rank=1,
                    holder_display_name="爱丽丝",
                    display_name="地球猪",
                    rarity=5,
                    short_code="GIANT001",
                    size_value=2694.7,
                    weight_value=75770.78,
                    acquired_at="2026-08-12 09:00",
                    media_visible=True,
                    is_animated=False,
                    image_fit="contain",
                ),
            ),
            weight_items=(
                DailyGiantItemViewModel(
                    key="weight:giant-two",
                    rank=1,
                    holder_display_name="鲍勃",
                    display_name="磁流体约束恒星物质猪",
                    rarity=5,
                    short_code="GIANT002",
                    size_value=1888.8,
                    weight_value=88888.88,
                    acquired_at="2026-08-12 10:00",
                    media_visible=True,
                    is_animated=False,
                    image_fit="contain",
                ),
            ),
        ),
        {"size:giant-one": source, "weight:giant-two": source},
    )
    daily_html, daily_options = capability.calls[-1]
    assert "最大体型榜" in daily_html
    assert "最重体重榜" in daily_html
    assert "爱丽丝" in daily_html and "鲍勃" in daily_html
    assert "26.947 m" in daily_html and "88.88888 t" in daily_html
    assert daily_html.count("data:image/webp;base64,") == 2
    assert daily_options["allow_network"] is False

    await renderer.render_item_receipt(
        ItemReceiptViewModel(
            operation="armed",
            item_name="巨物玉米",
            action_label="抓猪",
            quantity=2,
            effect_summary="体型百分位 +0.12",
            armed_uses=2,
        )
    )
    assert "成功动作时才消耗" in capability.calls[-1][0]
    assert "连续 2 次" in capability.calls[-1][0]


@pytest.mark.asyncio
async def test_pig_card_base_exposes_animation_safe_slot() -> None:
    renderer = PigCatcherRenderer(FakeRender(), _options())
    base = await renderer.render_pig_card_base(
        PigCardViewModel(
            mode_label="猪猪详情",
            display_name="动画猪",
            owner_display_name="成员",
            rarity=3,
            rarity_name="优质家养猪",
            short_code="A19F2C3D",
            description="逐帧保真。",
            size_value=40.0,
            size_percentile=0.5,
            weight_value=50.0,
            weight_percentile=0.5,
            fat_ratio=50.0,
            fat_label="均衡",
            official_value=150,
            acquired_at="2026-07-28 12:00",
            is_animated=True,
            media_format="GIF",
        )
    )
    assert base.media_slot == MediaSlot(x=38, y=164, width=480, height=480)


@pytest.mark.asyncio
async def test_fourth_round_templates_render_food_and_economy_views(
    tmp_path: Path,
) -> None:
    source = tmp_path / "food.png"
    Image.new("RGBA", (96, 96), "#F7A7C4").save(source, format="PNG")
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    food = FoodCardViewModel(
        mode_label="做菜成功",
        display_name="<script>测试美食</script>",
        owner_display_name="测试成员",
        rarity=4,
        rarity_name="极品佳肴",
        short_code="D19F2C3D",
        description="一份用于第四轮渲染验收的美食。",
        portion_weight=12.34,
        fat_label="均衡",
        official_value=388,
        acquired_at="2026-07-28 12:00",
        source_selector="测试猪#A19F2C3D",
        effect_summary="暂无额外效果",
        image_fit="contain",
        media_visible=True,
        is_animated=False,
        media_format="PNG",
        coin_reward=45,
        experience_reward=40,
        coin_balance=500,
        total_experience=900,
        player_level=5,
        level_title="抓猪老手",
        next_level_experience=1250,
        level_progress_percent=22.22,
        cookware_level=2,
        item_name="主厨香料",
        item_remaining_uses=2,
        catalog_new_count=1,
        probability_summary="1★ 75.0% · 2★ 22.0% · 3★ 3.0%",
    )
    await renderer.render_static_food_card(food, source)
    food_html, _ = capability.calls[-1]
    assert "<script>测试美食</script>" not in food_html
    assert "&lt;script&gt;" in food_html
    assert "data:image/webp;base64," in food_html
    assert "主厨香料" in food_html
    assert "剩 2 次" in food_html
    assert "本次最终概率" in food_html
    assert "1★ 75.0%" in food_html
    assert "data-food-probability" in food_html
    assert "food-card__probability-primary" in food_html
    assert "pig-card--probability" in food_html
    assert food_html.count("1★ 75.0% · 2★ 22.0% · 3★ 3.0%") == 1
    assert "Lv.5 · 抓猪老手" in food_html
    assert "+40 EXP · 900/1250" in food_html
    assert 'width: 22.22%' in food_html
    long_effect_food = replace(
        food,
        display_name="糖醋排骨",
        effect_summary=(
            "获得 1 次 /重置额度 机会；真正发动时为本群已登记玩家发放猪币、"
            "专属抓猪额度和高星权重，并让每次专属抓猪都有隐藏爆发概率。"
        ),
        probability_summary="",
        probability_line="5★ 64.0% · 6★ 36.0%",
        probability_sources="等级 Lv.18、厨具 Lv.5、道具·超级主厨香料、美食加成 ×2",
        effect_summaries=(
            "下一次用 6 星猪做菜时，6 星菜最终概率额外 +15 个百分点（最高 50%）。",
            "猪饺叠加 1 层：本次 6 星菜概率额外 +1 个百分点。",
        ),
        excluded_summaries=(
            "六星菜独占生效，普通概率加成保留且本次未叠加。",
        ),
    )
    await renderer.render_static_food_card(long_effect_food, source)
    long_effect_html, _ = capability.calls[-1]
    assert "5★ 64.0% · 6★ 36.0%" in long_effect_html
    assert "本次概率加成" in long_effect_html
    assert "猪饺叠加 1 层" in long_effect_html
    assert "互斥未叠加" in long_effect_html
    assert "普通概率加成保留且本次未叠加" in long_effect_html
    assert long_effect_html.index("本次最终概率") < long_effect_html.index("成品效果")
    assert long_effect_html.count("5★ 64.0% · 6★ 36.0%") == 1
    await renderer.render_static_food_card(replace(food, coin_reward=None), None)
    missing_food_html, _ = capability.calls[-1]
    assert "素材文件暂时不可用" in missing_food_html
    assert "文件暂缺" in missing_food_html

    base = await renderer.render_food_card_base(
        replace(food, is_animated=True, media_format="GIF")
    )
    assert base.media_slot == MediaSlot(x=38, y=164, width=480, height=480)

    inventory = FoodInventoryViewModel(
        display_name="测试成员",
        page=1,
        page_count=1,
        total_count=2,
        rarity=None,
        sort="价值",
        items=(
            FoodInventoryItemViewModel(
                key="static",
                display_name="静态菜",
                short_code="AAAA0001",
                rarity=2,
                portion_weight=8.2,
                fat_label="均衡",
                official_value=40,
                media_visible=True,
                is_animated=False,
                image_fit="contain",
                is_favorite=True,
            ),
            FoodInventoryItemViewModel(
                key="animated",
                display_name="动画菜",
                short_code="BBBB0002",
                rarity=3,
                portion_weight=9.1,
                fat_label="偏肥",
                official_value=120,
                media_visible=True,
                is_animated=True,
                image_fit="contain",
            ),
        ),
    )
    await renderer.render_food_inventory(inventory, {"static": source})
    inventory_html, _ = capability.calls[-1]
    assert "动态美食" in inventory_html
    assert "已收藏" in inventory_html and 'class="asset-icon asset-icon--favorite"' in inventory_html
    assert inventory_html.count("data:image/webp;base64,") == 1

    catalog = FoodCatalogViewModel(
        display_name="测试成员",
        total_count=2,
        rarity=None,
        undiscovered_only=False,
        collected_count=1,
        visible_catalog_total=13,
        items=(
            FoodCatalogItemViewModel(
                key="static",
                display_name="静态菜",
                rarity=2,
                discovered=True,
                acquired_count=2,
                best_portion_weight=8.2,
                media_visible=True,
                is_animated=False,
                image_fit="contain",
                effect_summary="下一次抓猪更容易遇到高星猪猪。",
            ),
            FoodCatalogItemViewModel(
                key="secret",
                display_name="不得泄露的群专属菜",
                rarity=6,
                discovered=False,
                acquired_count=0,
                best_portion_weight=None,
                media_visible=False,
                is_animated=False,
                image_fit="contain",
            ),
        ),
    )
    await renderer.render_food_catalog(catalog, {"static": source})
    catalog_html, _ = capability.calls[-1]
    assert "不得泄露的群专属菜" not in catalog_html
    assert "尚未发现" in catalog_html
    assert "2 星品质" in catalog_html
    assert "6 星品质" in catalog_html
    assert "下一次抓猪更容易遇到高星猪猪" in catalog_html
    assert "第 1/1 页" not in catalog_html

    await renderer.render_store(
        StoreViewModel(
            display_name="测试成员",
            coin_balance=800,
            page=1,
            page_count=1,
            total_count=2,
            category="全部",
            feed_level=1,
            cookware_level=2,
            feed_probability_rows=tuple(
                StoreProbabilityRowViewModel(
                    level=level,
                    value=f"{13.0 + level * 0.26:.2f}%",
                    delta="基准" if level == 0 else f"+{level * 0.26:.2f} 点",
                    current=level == 1,
                )
                for level in range(6)
            ),
            cookware_probability_rows=tuple(
                StoreProbabilityRowViewModel(
                    level=level,
                    value=f"+{level * 4}%",
                    delta="相对权重",
                    current=level == 2,
                )
                for level in range(6)
            ),
            lucky_whistle_rows=(
                StoreConsumableProbabilityRowViewModel(
                    label="6 星",
                    before="1.00%",
                    after="1.50%",
                ),
            ),
            chef_spice_rows=(
                StoreConsumableProbabilityRowViewModel(
                    label="4 星猪",
                    before="2★ 5% · 3★ 25% · 4★ 60% · 5★ 10%",
                    after="3★ 30% · 4★ 60% · 5★ 10%",
                ),
            ),
            products=(
                StoreProductViewModel(
                    display_name="幸运猪哨",
                    category="抓猪道具",
                    unit_price=180,
                    effect_summary="品质概率加成",
                    current_level=0,
                    target_level=0,
                ),
                StoreProductViewModel(
                    display_name="厨具升级",
                    category="永久升级",
                    unit_price=1200,
                    effect_summary="购买后提升至 Lv.3",
                    current_level=2,
                    target_level=3,
                ),
            ),
        )
    )
    store_html = capability.calls[-1][0]
    assert "幸运猪哨" in store_html
    assert "猪饲料" in store_html
    assert "4-6 星合计概率" in store_html
    assert "+20%" in store_html
    assert "Lv.2 · 当前" in store_html
    assert "幸运猪哨" in store_html
    assert "1.00% → 1.50%" in store_html
    assert "主厨香料" in store_html
    assert "3★ 30% · 4★ 60% · 5★ 10%" in store_html

    await renderer.render_economy_receipt(
        EconomyReceiptViewModel(
            eyebrow="猪猪商城",
            title="购买成功",
            badge_label="剩余猪币",
            badge_value="620",
            summary="幸运猪哨 ×1",
            rows=(
                EconomyReceiptRowViewModel("单价", "180 猪币"),
                EconomyReceiptRowViewModel("本次支付", "180 猪币"),
                EconomyReceiptRowViewModel("库存", "1"),
            ),
            note="同一消息不会重复扣款。",
        )
    )
    assert "一次且仅一次" in capability.calls[-1][0]

    await renderer.render_ledger(
        LedgerViewModel(
            display_name="测试成员",
            page=1,
            page_count=1,
            total_count=1,
            coin_balance=620,
            ledger_total=620,
            items=(
                LedgerEntryViewModel(
                    amount_text="-180",
                    positive=False,
                    balance_after=620,
                    reason_text="购买幸运猪哨×1",
                    created_at="2026-07-28 12:00",
                ),
            ),
        )
    )
    assert "对账状态" in capability.calls[-1][0]


@pytest.mark.asyncio
async def test_group_event_templates_render_three_distinct_major_announcements(
    tmp_path: Path,
) -> None:
    source = tmp_path / "six-star-food.png"
    Image.new("RGBA", (256, 256), "#F4A7BE").save(source, format="PNG")
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    common = {
        "actor_name": "千早の花火",
        "group_name": "官方群-CEAB3520",
        "event_time": "2026-08-12 15:20",
        "rows": (
            GroupEventRowViewModel("全群猪币", "每人 +1,007", "已经原子结算"),
            GroupEventRowViewModel("专属抓猪", "每人 10 次", "不占正常额度"),
            GroupEventRowViewModel("高星强化", "5★ / 6★ ×1.007", "10% 隐藏爆发"),
        ),
    }
    await renderer.render_group_event(
        GroupEventViewModel(
            tone="sugar",
            eyebrow="六星盛宴 · 全群事件资格已取得",
            title="糖醋排骨登场",
            subtitle="酸甜一响，全群强化蓄势待发",
            hero_label="发动资格",
            hero_value="1 次 /重置额度",
            note="这次只取得发动资格，尚未重置额度。",
            footer="等待正式发动",
            settlement_committed=False,
            media_visible=True,
            **common,
        ),
        source,
    )
    sugar_html, sugar_options = capability.calls[-1]
    assert "全群<br>通告" in sugar_html
    assert "糖醋排骨登场" in sugar_html
    assert "1 次 /重置额度" in sugar_html
    assert "尚未重置额度" in sugar_html
    assert "本次只取得发动资格；奖励将在正式发动时原子结算" in sugar_html
    assert "本次事件已经提交" not in sugar_html
    assert "data:image/webp;base64," in sugar_html
    assert sugar_options["allow_network"] is False

    await renderer.render_group_event(
        GroupEventViewModel(
            tone="cloud",
            eyebrow="六星盛宴 · 神龙临世",
            title="七星云海，福泽全群",
            subtitle="神龙化猪七星云海锅已经开席",
            hero_label="全群高星权重",
            hero_value="5★ / 6★ ×8",
            note="每名玩家各生效一次，且不与其他道具或菜品叠加。",
            footer="神龙赐福已经结算",
            media_visible=True,
            **common,
        ),
        source,
    )
    cloud_html, _ = capability.calls[-1]
    assert "七星云海，福泽全群" in cloud_html
    assert "5★ / 6★ ×8" in cloud_html
    assert "千早の花火" in cloud_html

    await renderer.render_group_event(
        GroupEventViewModel(
            tone="reset",
            eyebrow="糖醋排骨 · 全群强化正式发动",
            title="全群额度重置完成",
            subtitle="新的十连已经开启",
            hero_label="全群专属抓猪",
            hero_value="每人 10 次",
            note="强化持续到次日同一时段刷新。",
            footer="糖醋排骨全群强化已经正式生效",
            **common,
        )
    )
    reset_html, _ = capability.calls[-1]
    assert "全群额度重置完成" in reset_html
    assert "每人 10 次" in reset_html
    reset_media = reset_html.split('<div class="group-event__media">', 1)[1].split("</div>", 1)[0]
    assert '<svg class="feature-icon"' in reset_media
    assert "aria-label=" in reset_media
    assert "<img" not in reset_media  # 无素材也展示安全的事件图形，而不是伪造六星图片。
    assert "1455722694" not in reset_html


@pytest.mark.asyncio
async def test_fifth_round_templates_render_body_trade_and_ranking_views(
    tmp_path: Path,
) -> None:
    source = tmp_path / "showcase.png"
    Image.new("RGBA", (96, 96), "#F58CAD").save(source, format="PNG")
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    pig = PigCardViewModel(
        mode_label="抓猪成功",
        display_name="巨型测试猪",
        owner_display_name="测试成员",
        rarity=2,
        rarity_name="美味家养猪",
        short_code="A19F2C3D",
        description="一只用于第五轮体型与全群纪录渲染验收的猪。",
        size_value=180,
        size_percentile=0.6,
        weight_value=600,
        weight_percentile=0.7,
        fat_ratio=55,
        fat_label="均衡",
        official_value=1000,
        acquired_at="2026-07-28 12:00",
        catalog_new=True,
        body_label="双项巨物",
        body_description="体型与重量同时越过全群巨物线。",
        giant_score=159.5,
        global_size_record=True,
        global_weight_record=True,
        giant_sighting=True,
    )
    await renderer.render_static_pig_card(pig, source)
    pig_html, _ = capability.calls[-1]
    assert "NEW · 首次收集" in pig_html
    assert "双项巨物" in pig_html
    assert "全群体型最高" in pig_html
    assert "巨物目击留档" in pig_html

    await renderer.render_trade_list(
        TradeListViewModel(
            display_name="测试成员",
            page=1,
            page_count=1,
            total_count=1,
            status_label="待处理",
            items=(
                TradeListItemViewModel(
                    trade_id="AAAABBBB",
                    status_label="待处理",
                    asset_name="巨型测试猪",
                    asset_code="A19F2C3D",
                    rarity=2,
                    price=120,
                    sender_name="卖方",
                    recipient_name="买方",
                    expires_at="2026-07-28 12:05",
                ),
            ),
        )
    )
    trade_html, _ = capability.calls[-1]
    assert "AAAABBBB" in trade_html
    assert "卖方 → 买方" in trade_html
    assert "报价锁定 5 分钟" in trade_html

    await renderer.render_ranking(
        RankingViewModel(
            group_name="测试群",
            ranking_type="巨物",
            page=1,
            page_count=1,
            total_count=2,
            items=(
                RankingItemViewModel(
                    key="static",
                    rank=1,
                    display_name="第一名",
                    metric_text="巨物 159.5 分",
                    pig_progress="2/2",
                    food_progress="0/0",
                    asset_count=2,
                    coin_balance=120,
                    showcase_name="巨型测试猪",
                    showcase_detail="180.0 cm · 600.00 kg",
                    showcase_rarity=2,
                    showcase_kind="猪猪",
                    media_visible=True,
                    is_animated=False,
                    image_fit="contain",
                ),
                RankingItemViewModel(
                    key="animated",
                    rank=2,
                    display_name="第二名",
                    metric_text="巨物 90.0 分",
                    pig_progress="1/2",
                    food_progress="0/0",
                    asset_count=1,
                    coin_balance=20,
                    showcase_name="动态猪",
                    showcase_detail="120.0 cm · 300.00 kg",
                    showcase_rarity=3,
                    showcase_kind="猪猪",
                    media_visible=True,
                    is_animated=True,
                    image_fit="contain",
                ),
            ),
        ),
        {"static": source},
    )
    ranking_html, _ = capability.calls[-1]
    assert "巨物 159.5 分" in ranking_html
    assert "data:image/webp;base64," in ranking_html
    assert "GIF" in ranking_html and "动态展示" in ranking_html
