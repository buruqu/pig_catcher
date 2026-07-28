"""白色淡粉模板、PNG 校验与发送降级。"""

from __future__ import annotations

import base64
import hashlib
import logging
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
    FrameworkPreviewViewModel,
    InventoryItemViewModel,
    InventoryViewModel,
    ItemReceiptViewModel,
    MediaSlot,
    PigCardViewModel,
    PigCatcherRenderer,
    ProfileViewModel,
    RecordItemViewModel,
    RecordsViewModel,
    RenderDelivery,
    RenderOptions,
)
from pig_catcher.version import (
    ASSET_MANIFEST_VERSION,
    FRAMEWORK_PHASE,
    PLUGIN_VERSION,
    RULESET_VERSION,
    SCHEMA_VERSION,
)

from .helpers import FakeRender, FakeSend, png_base64


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
        daily_count=2,
        daily_limit=30,
        catalog_new=True,
        size_record=True,
    )
    await renderer.render_static_pig_card(pig, source)
    pig_html, _ = capability.calls[-1]
    assert "<script>超长测试猪</script>" not in pig_html
    assert "&lt;script&gt;" in pig_html
    assert "data:image/png;base64," in pig_html
    assert "体型新纪录" in pig_html

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
            level=3,
            title="抓猪老手",
            total_experience=600,
            next_threshold=1800,
            progress_percent=8.3,
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
        )
    )
    assert "Lv.3" in capability.calls[-1][0]
    assert "Poppin&#39;Party" in capability.calls[-1][0]

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
    assert inventory_html.count("data:image/png;base64,") == 1

    catalog = CatalogViewModel(
        display_name="测试成员",
        page=1,
        page_count=1,
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

    await renderer.render_item_receipt(
        ItemReceiptViewModel(
            operation="armed",
            item_name="巨物玉米",
            action_label="抓猪",
            quantity=2,
            effect_summary="体型百分位 +0.12",
        )
    )
    assert "成功动作时才消耗" in capability.calls[-1][0]


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
