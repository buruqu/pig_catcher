"""有界、白名单驱动的原创外观资源；不从玩家输入构造文件路径。

本模块只负责表现。调用者必须先核验奖励归属/隐藏成就解锁，或传入
``revealed=False``；该分支在解析奖励 ID 和读取任何图像之前结束。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any

_ROOT = Path(__file__).resolve().parent / "assets/ui/cosmetics"
_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\Z")
_KINDS = frozenset({"title", "frame", "badge", "cosmetic"})
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_CACHE_BYTES = 4 * 1024 * 1024
_MAX_CACHE_ENTRIES = 64
_CACHE: OrderedDict[tuple[str, str, str], str] = OrderedDict()
_CACHE_BYTES = 0
_LOCK = RLock()


def load_cosmetic_definitions() -> dict[str, Any]:
    """读取内部作者清单，构建脚本与覆盖测试共用；不包含解锁条件。"""
    data = json.loads((_ROOT / "definitions.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("不支持的外观登记表版本")
    themes = data.get("themes", {})
    seen = set()
    for item in data.get("entries", []):
        key = item.get("id", "")
        if not _ID_PATTERN.fullmatch(key) or key in seen:
            raise ValueError("外观奖励 ID 不合法或重复")
        seen.add(key)
        if item.get("kind") not in _KINDS or item.get("theme") not in themes:
            raise ValueError("外观种类或主题未注册")
        if not item.get("name") or not _ID_PATTERN.fullmatch(item.get("emblem", "")):
            raise ValueError("外观缺少准确名称或原创徽记")
        color = item.get("color", themes[item["theme"]]["color"])
        if not _COLOR_PATTERN.fullmatch(color):
            raise ValueError("外观颜色必须为安全的六位十六进制值")
        if item.get("season"):
            season = item["season"]
            if type(season) is not int or season < 1 or item["theme"] != f"weekly-{season:03d}":
                raise ValueError("新的周榜活动必须先审核独立主题包")
    if not seen:
        raise ValueError("外观登记表不能为空")
    return data


_DEFINITIONS = load_cosmetic_definitions()
COSMETIC_DEFINITIONS: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {item["id"]: MappingProxyType(item) for item in _DEFINITIONS["entries"]}
)
_ALIASES: dict[str, tuple[str, ...]] = {}
for _key, _definition in COSMETIC_DEFINITIONS.items():
    for _alias in (_definition["name"], *_definition.get("aliases", ())):
        _ALIASES[_alias] = (*_ALIASES.get(_alias, ()), _key)

_MANIFEST: dict[str, dict[str, Any]] | None = None


def _manifest() -> dict[str, dict[str, Any]]:
    global _MANIFEST
    with _LOCK:
        if _MANIFEST is None:
            path = _ROOT / "manifest.json"
            if not path.is_file():
                # 允许制作时先注册 API；正式美术覆盖门禁会拒绝未导出资产。
                return {}
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != 1 or raw.get("art_version") != _DEFINITIONS["art_version"]:
                raise ValueError("外观图像清单版本不匹配")
            entries = raw.get("entries", [])
            if len({item["id"] for item in entries}) != len(entries):
                raise ValueError("外观图像清单存在重复 ID")
            if any(item["id"] not in COSMETIC_DEFINITIONS for item in entries):
                raise ValueError("外观图像清单包含未注册奖励")
            _MANIFEST = {item["id"]: item for item in entries}
        return _MANIFEST


def clear_cosmetic_cache() -> None:
    """插件卸载或素材更新后清理；不保留隐藏玩家/奖励状态。"""
    global _CACHE_BYTES, _MANIFEST
    with _LOCK:
        _CACHE.clear()
        _CACHE_BYTES = 0
        _MANIFEST = None


def cosmetic_cache_info() -> dict[str, int]:
    with _LOCK:
        return {"entries": len(_CACHE), "bytes": _CACHE_BYTES, "max_bytes": _MAX_CACHE_BYTES}


def _image(reward_id: str, variant: str) -> str:
    """只接受登记项的审核路径，首次读取校验哈希，并以字节与项数双限缓存。"""
    global _CACHE_BYTES
    item = _manifest().get(reward_id, {}).get("files", {}).get(variant)
    if not isinstance(item, dict):
        return ""
    digest = item.get("sha256", "")
    key = (reward_id, variant, digest)
    with _LOCK:
        cached = _CACHE.pop(key, None)
        if cached is not None:
            _CACHE[key] = cached
            return cached
        relative = item.get("path", "")
        if not isinstance(relative, str) or not relative:
            return ""
        path = (_ROOT / relative).resolve()
        if not path.is_relative_to(_ROOT.resolve()) or path.suffix not in {".png", ".webp"}:
            raise ValueError("外观图像路径越界或类型不支持")
        if not path.is_file():
            return ""
        if not 0 < path.stat().st_size <= _MAX_IMAGE_BYTES:
            raise ValueError("外观图像超出大小预算")
        with path.open("rb") as stream:
            payload = stream.read(_MAX_IMAGE_BYTES + 1)
        if len(payload) > _MAX_IMAGE_BYTES or len(payload) != item.get("bytes"):
            raise ValueError("外观图像字节数不匹配或超出预算")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("外观图像哈希不匹配，请重新导出或恢复正式素材")
        if path.suffix == ".png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("外观 PNG 文件签名不匹配")
        if path.suffix == ".webp" and not (payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"):
            raise ValueError("外观 WebP 文件签名不匹配")
        mime = "image/png" if path.suffix == ".png" else "image/webp"
        encoded = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
        size = len(encoded)
        if size <= _MAX_CACHE_BYTES:
            while _CACHE and (len(_CACHE) >= _MAX_CACHE_ENTRIES or _CACHE_BYTES + size > _MAX_CACHE_BYTES):
                _, old = _CACHE.popitem(last=False)
                _CACHE_BYTES -= len(old)
            _CACHE[key] = encoded
            _CACHE_BYTES += size
        return encoded


def _empty(*, masked: bool = False) -> dict[str, Any]:
    return {
        "id": "",
        "name": "未解锁外观" if masked else "",
        "kind": "",
        "glyph": "",
        "color": "#8e8188",
        "family": "none",
        "available": False,
        "masked": masked,
        "image_data_url": "",
        "frame_data_url": "",
        "is_plate": False,
        "rank": 0,
        "width": 0,
        "height": 0,
    }


def cosmetic_detail(
    reward_id: str,
    *,
    kind: str | None = None,
    revealed: bool = True,
    variant: str = "compact",
) -> dict[str, Any]:
    """Jinja 安全视图：支持稳定奖励 ID 或已有准确显示名，不支持路径/CSS。

    ``compact`` 是预制的有界 WebP；``detail`` 是准确文字 PNG 成品。
    周榜名次虽属于 badge，``is_plate`` 为真，必须按横板显示。
    """
    if not revealed:
        return _empty(masked=True)
    if not isinstance(reward_id, str) or not reward_id or len(reward_id) > 120:
        return _empty()
    candidates = (reward_id,) if reward_id in COSMETIC_DEFINITIONS else _ALIASES.get(reward_id, ())
    matches = [
        COSMETIC_DEFINITIONS[key] for key in candidates if kind is None or COSMETIC_DEFINITIONS[key]["kind"] == kind
    ]
    if not matches:
        return _empty()
    # 极少数历史名称同时指称号和边框；无 kind 的旧佩戴视图优先显示称号。
    definition = next((item for item in matches if item["kind"] == "title"), matches[0])
    key = str(definition["id"])
    theme = _DEFINITIONS["themes"][definition["theme"]]
    is_plate = definition["kind"] == "title" or bool(definition.get("rank"))
    image_variant = "png" if variant == "detail" else "compact"
    data_url = _image(key, image_variant)
    return {
        "id": key,
        "name": definition["name"],
        "kind": definition["kind"],
        "glyph": "",
        "color": definition.get("color", theme["color"]),
        "family": definition.get("family", definition["theme"]),
        "available": bool(data_url),
        "masked": False,
        "image_data_url": data_url,
        "frame_data_url": _image(key, "border") if definition["kind"] == "frame" else "",
        "is_plate": is_plate,
        "rank": int(definition.get("rank", 0)),
        "width": 1200 if is_plate else (480 if definition["kind"] == "frame" else 256),
        "height": 360 if is_plate else (600 if definition["kind"] == "frame" else 256),
    }


def cosmetic_cards(rewards: Iterable[object], *, revealed: bool = True) -> tuple[dict[str, Any], ...]:
    """全量旧/新成就、里程碑、宝箱、周榜外观；隐藏条目不访问图像。"""
    if not revealed:
        return ()
    result = []
    for reward in rewards:
        if isinstance(reward, Mapping):
            reward_id, reward_type = reward.get("reward_id", ""), reward.get("reward_type", "")
        else:
            reward_id, reward_type = reward.reward_id, reward.reward_type
        if reward_type not in _KINDS:
            continue
        detail = cosmetic_detail(reward_id, kind=reward_type)
        if detail["id"]:
            result.append(detail)
    return tuple(result)


__all__ = [
    "COSMETIC_DEFINITIONS",
    "clear_cosmetic_cache",
    "cosmetic_cache_info",
    "cosmetic_cards",
    "cosmetic_detail",
    "load_cosmetic_definitions",
]
