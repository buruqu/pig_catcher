"""旧收藏页面的原生状态图标；只展示现有事实，不访问媒体或判定玩法。"""

# 原创路径为完整矢量常量，避免为了格式化拆断图形指令。
# ruff: noqa: E501

from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType

from markupsafe import Markup

from .feature_art import feature_icon

# 与功能页面共用已登记图形；只把严格白名单键传给 feature_icon。
_FEATURES = MappingProxyType(
    {
        "pig": "pig",
        "food": "pot",
        "catalog": "notes",
        "coin": "coin",
        "feed": "feed",
        "cookware": "cookware",
        "catching": "whistle",
        "showcase": "camera",
        "collection": "ticket",
        "level": "crown",
    }
)
_SHAPES = MappingProxyType(
    {
        "giant": (
            "巨物体格",
            "#9e7640",
            '<path d="m7 38 11-15 10 11 8-15 11 15"/><path d="m19 24-2-9 10 5m13 3 9-7-1 11"/><ellipse cx="32" cy="35" rx="18" ry="15"/><ellipse cx="35" cy="38" rx="10" ry="7"/><path d="M30 38h1m8 0h1M14 50h39M13 54h42"/>',
        ),
        "mini": (
            "迷你体格",
            "#568e84",
            '<path d="M12 41c3 19 37 19 40 0M18 42l-2-10 9 4m13-1 8-5-1 10"/><ellipse cx="31" cy="39" rx="13" ry="10"/><ellipse cx="31" cy="42" rx="6" ry="4"/><path d="M13 12v12m-5-5 5 5 5-5M49 12v12m-5-5 5 5 5-5M24 56h15"/>',
        ),
        "dual-giant": (
            "体型与重量双项巨物",
            "#a37b42",
            '<path d="M9 13h13v39H9Zm0 8h7m-7 9h7m-7 9h7M30 28h23l5 23H25Z"/><path d="M36 28v-7c0-8 12-8 12 0v7M31 15l6-7m13 6 7-6"/>',
        ),
        "double-top": (
            "同品种双顶壮硕",
            "#b38634",
            '<path d="m5 49 14-28 12 28Zm25 0 14-28 15 28ZM12 16l1-8 6 4 6-4 1 8Zm25 0 1-8 6 4 6-4 1 8ZM9 54h47"/>',
        ),
        "double-mini": (
            "同品种双顶迷你",
            "#65998e",
            '<path d="M15 10v20m-6-6 6 6 6-6m28-14v20m-6-6 6 6 6-6"/><ellipse cx="16" cy="43" rx="10" ry="8"/><ellipse cx="48" cy="43" rx="10" ry="8"/><path d="m9 37-2-6 8 4m25 2-1-6 8 4M6 55h52m-47-12h1m8 0h1m21 0h1m8 0h1"/>',
        ),
        "favorite": (
            "已收藏保护",
            "#c57796",
            '<path d="M32 50C-2 31 8 8 25 17l7 7 7-7c17-9 27 14-7 33Z"/><path d="M42 40l6 7 11-15"/>',
        ),
        "busy": (
            "活动占用中",
            "#9680b2",
            '<path d="M18 10h28m-28 44h28M21 10v9c0 10 22 14 22 24v11M43 10v9c0 10-22 14-22 24v11M25 18h14m-14 29h14"/><path d="M10 30H4m50 0h6"/>',
        ),
        "protected": (
            "养成保护",
            "#7599b1",
            '<path d="M32 7 52 15v17c0 13-20 23-20 23S12 45 12 32V15Z"/><path d="m22 30 7 7 14-16"/>',
        ),
        "private": (
            "授权已撤回",
            "#938591",
            '<path d="M6 31s9-16 26-16 26 16 26 16-9 16-26 16S6 31 6 31Z"/><circle cx="32" cy="31" r="9"/><path d="M10 8l44 46"/>',
        ),
        "hidden": (
            "群专属未发现",
            "#9b8caf",
            '<path d="M17 29h30v26H17ZM23 29V18c0-15 18-15 18 0v11"/><circle cx="32" cy="40" r="3"/><path d="M32 43v5M10 34V18h6m38 16V18h-6"/>',
        ),
        "unseen": (
            "尚未发现",
            "#aa93a3",
            '<path d="M10 14h21v39H10Zm21 0h23v39H31M16 23h8m-8 8h8"/><path d="M37 26c1-9 13-8 12 0-1 4-7 3-7 10m0 7h.1"/>',
        ),
        "missing": (
            "图片文件暂缺",
            "#9e9198",
            '<path d="M10 10h44v44H10Z"/><path d="m10 45 13-15 10 11 10-15 11 14M23 17h1"/><path d="m40 45 14 14m0-14L40 59"/>',
        ),
        "animated": (
            "动态素材",
            "#8d8bb8",
            '<path d="M8 14h48v36H8Zm8 0v36m32-36v36M8 22h8m-8 10h8m-8 10h8m32-20h8m-8 10h8m-8 10h8"/><path d="m27 23 14 9-14 9Z"/>',
        ),
        "length": (
            "体型尺度",
            "#7098b8",
            '<path d="M9 23h46v21H9Zm9 0v9m9-9v6m10-6v9m9-9v6M9 11h46m-40-5-6 5 6 5m34-10 6 5-6 5"/>',
        ),
        "weight": (
            "重量尺度",
            "#b39152",
            '<path d="M19 26h26l10 29H9ZM25 25V16c0-10 14-10 14 0v9"/><path d="M24 40h16m-8-6v12"/>',
        ),
        "record": (
            "群纪录",
            "#b48d46",
            '<path d="M20 10h24v19c0 18-24 18-24 0ZM20 17H9v9c0 10 10 10 13 9m22-18h11v9c0 10-10 10-13 9M32 42v10m-13 3h26"/>',
        ),
        "tag": (
            "素材标签",
            "#ac8d9e",
            '<path d="M10 11h24l21 21-23 23L10 33Z"/><circle cx="23" cy="23" r="4"/><path d="m31 35 6 6m-1-14 7 7"/>',
        ),
    }
)
_ALIASES = MappingProxyType(
    {
        "体型": "length",
        "重量": "weight",
        "体型巨物": "giant",
        "重量巨物": "giant",
        "巨物": "giant",
        "巨型": "giant",
        "巨型品种": "giant",
        "长体巨物": "giant",
        "重量级巨物": "giant",
        "超巨物": "giant",
        "海洋巨物": "giant",
        "建筑巨物": "giant",
        "神话巨物": "giant",
        "蓬松巨物": "giant",
        "双项巨物": "dual-giant",
        "双顶壮硕": "double-top",
        "壮硕个体": "double-top",
        "双顶迷你": "double-mini",
        "迷你": "mini",
        "迷你个体": "mini",
        "袖珍品种": "mini",
        "修长个体": "length",
        "沉甸甸个体": "weight",
        "已收藏": "favorite",
        "收藏": "favorite",
        "派遣中": "busy",
        "巡演中": "busy",
        "对战中": "busy",
        "乐队保护": "protected",
        "战斗保护": "protected",
        "乐队保护 / 战斗保护": "protected",
    }
)
ASSET_ICON_KEYS = frozenset((*_SHAPES, *_FEATURES))


@lru_cache(maxsize=32)
def _native_icon(key: str) -> Markup:
    label, color, shape = _SHAPES[key]
    return Markup(
        f'<svg class="asset-icon asset-icon--{key}" viewBox="0 0 64 64" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg">'
        f'<g fill="none" stroke="{color}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round">{shape}</g></svg>'
    )


def asset_icon(value: object) -> Markup:
    """未知输入显示通用标签，绝不将输入解释为 SVG、路径或外部地址。"""
    key = (
        value
        if isinstance(value, str) and value in ASSET_ICON_KEYS
        else _ALIASES.get(value, "tag")
        if isinstance(value, str)
        else "tag"
    )
    if key in _FEATURES:
        return Markup('<span class="asset-feature-icon">') + feature_icon(_FEATURES[key]) + Markup("</span>")
    return _native_icon(key)


__all__ = ["ASSET_ICON_KEYS", "asset_icon"]
