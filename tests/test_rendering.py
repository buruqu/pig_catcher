"""白色淡粉模板、PNG 校验与发送降级。"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

import pytest
from PIL import Image

from pig_catcher.domain.errors import RenderError
from pig_catcher.rendering import (
    FrameworkPreviewViewModel,
    PigCatcherRenderer,
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
