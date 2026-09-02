"""插件、消息和测试图片夹具。"""

from __future__ import annotations

import base64
import importlib.util
import logging
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image, ImageDraw

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_MODULE_NAME = "_pig_catcher_test_plugin"
_PLUGIN_SPEC = importlib.util.spec_from_file_location(
    _PLUGIN_MODULE_NAME,
    _PLUGIN_ROOT / "plugin.py",
    submodule_search_locations=[str(_PLUGIN_ROOT)],
)
if _PLUGIN_SPEC is None or _PLUGIN_SPEC.loader is None:
    raise RuntimeError("无法创建抓猪插件测试模块。")
_PLUGIN_MODULE = importlib.util.module_from_spec(_PLUGIN_SPEC)
sys.modules[_PLUGIN_MODULE_NAME] = _PLUGIN_MODULE
_PLUGIN_SPEC.loader.exec_module(_PLUGIN_MODULE)

PigCatcherPlugin = _PLUGIN_MODULE.PigCatcherPlugin
create_plugin = _PLUGIN_MODULE.create_plugin


def png_base64(
    *,
    width: int = 1200,
    height: int = 900,
    transparent: bool = False,
) -> str:
    mode = "RGBA"
    background = (0, 0, 0, 0) if transparent else (255, 247, 250, 255)
    image = Image.new(mode, (width, height), background)
    if not transparent:
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, width - 40, height - 40), fill=(255, 255, 255, 255))
        draw.rectangle((40, 40, width - 40, 160), fill=(255, 232, 241, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


class FakeRender:
    """记录 HTML 渲染调用并返回有效测试 PNG。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error: Exception | None = None
        self.result: object = {
            "image_base64": png_base64(),
            "mime": "image/png",
            "width": 1200,
            "height": 900,
        }

    async def html2png(self, html: str, **kwargs: object) -> object:
        self.calls.append((html, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


class FakeSend:
    """记录文字和图片发送。"""

    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.images: list[tuple[str, str]] = []
        self.hybrids: list[tuple[str, list[dict[str, str]]]] = []
        self.text_success = True
        self.image_success = True
        self.hybrid_success = True
        self.text_error: Exception | None = None
        self.image_error: Exception | None = None
        self.hybrid_error: Exception | None = None

    async def text(self, text: str, stream_id: str) -> bool:
        self.texts.append((stream_id, text))
        if self.text_error is not None:
            raise self.text_error
        return self.text_success

    async def image(self, image_base64: str, stream_id: str) -> bool:
        self.images.append((stream_id, image_base64))
        if self.image_error is not None:
            raise self.image_error
        return self.image_success

    async def hybrid(self, segments: list[dict[str, str]], stream_id: str) -> bool:
        self.hybrids.append((stream_id, segments))
        if self.hybrid_error is not None:
            raise self.hybrid_error
        return self.hybrid_success


class FakeContext:
    """插件生命周期所需的最小 SDK 上下文。"""

    def __init__(self, data_dir: Path) -> None:
        self.paths = SimpleNamespace(data_dir=str(data_dir))
        self.render = FakeRender()
        self.send = FakeSend()
        self.logger = logging.getLogger(f"test.pig_catcher.{id(self)}")


def build_message(
    *,
    platform: str = "qq",
    group_id: str = "10001",
    group_name: str = "抓猪测试群",
    user_id: str = "20001",
    display_name: str = "测试成员",
    stream_id: str = "stream-10001",
    message_id: str = "message-1",
    private: bool = False,
) -> dict[str, Any]:
    group_info: dict[str, str] = {}
    if not private:
        group_info = {"group_id": group_id, "group_name": group_name}
    return {
        "platform": platform,
        "session_id": stream_id,
        "message_id": message_id,
        "message_info": {
            "group_info": group_info,
            "user_info": {
                "user_id": user_id,
                "user_nickname": display_name,
                "user_cardname": display_name,
            },
            "additional_config": {},
        },
    }


async def create_test_plugin(
    data_dir: Path,
    *,
    config_updates: dict[str, dict[str, object]] | None = None,
) -> tuple[Any, FakeContext]:
    plugin = create_plugin()
    context = FakeContext(data_dir)
    plugin._set_context(context)
    config = plugin.get_default_config()
    config["maintenance"]["enabled"] = False
    # Legacy command-flow tests assert exact image counts.  Achievement tests
    # opt in explicitly so unlock popups are verified independently.
    config["features"]["achievements_enabled"] = False
    config["features"]["weekly_competitions_enabled"] = False
    for section, updates in (config_updates or {}).items():
        config[section].update(updates)
    plugin.set_plugin_config(config)
    await plugin.on_load()
    return plugin, context


async def invoke_help(
    plugin: Any,
    *,
    topic: str = "",
    message: dict[str, Any] | None = None,
) -> tuple[bool, str, int]:
    resolved_message = message or build_message()
    return await plugin.handle_help(
        stream_id=str(resolved_message.get("session_id") or ""),
        matched_groups={"topic": topic or None},
        raw_message="/抓猪帮助" + (f" {topic}" if topic else ""),
        message=resolved_message,
    )
