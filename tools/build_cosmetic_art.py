"""导出完整原创外观包：AI 无字插画母版 + 精确文字 + 原生矢量徽记。

只在独立无头 Chromium 内绘制；不连接用户浏览器、MaiBot、QQ 或数据库。
原插画/猪/菜源文件不改动。PNG/WebP/SVG 和哈希清单是可重建的发布资产。
"""

# ruff: noqa: E501
# 图形路径、CSS 与本地 HTML 是可审阅的连续美术数据，不切断 SVG 坐标串。

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import sys
from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pig_catcher.rendering.cosmetics import load_cosmetic_definitions  # noqa: E402

ART_ROOT = ROOT / "pig_catcher/rendering/assets/ui/cosmetics"
MASTER_ROOT = ART_ROOT.parent / "masters"
QA_ROOT = ROOT / "artifacts/cosmetics-art-v1"
DEFAULT_BROWSER = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
DEFAULT_FONT = Path("C:/Windows/Fonts/msyhbd.ttc")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _star(cx: float, cy: float, size: float, points: int = 5) -> str:
    coordinates = []
    for index in range(points * 2):
        angle = index * math.pi / points - math.pi / 2
        radius = size if index % 2 == 0 else size * 0.43
        coordinates.append(f"{cx + radius * math.cos(angle):.2f},{cy + radius * math.sin(angle):.2f}")
    return f'<polygon points="{" ".join(coordinates)}"/>'


def _laurel() -> str:
    leaves = []
    for sign in (-1, 1):
        for index in range(5):
            x, y = 50 + sign * (22 + index * 2), 82 - index * 12
            leaves.append(f'<ellipse cx="{x}" cy="{y}" rx="5" ry="10" transform="rotate({sign * 40} {x} {y})"/>')
    return '<path d="M23 24Q12 68 48 88M77 24Q88 68 52 88" fill="none"/>' + "".join(leaves)


def _snout(cx: int = 50, cy: int = 57, scale: float = 1) -> str:
    return (
        f'<g transform="translate({cx} {cy}) scale({scale})">'
        '<ellipse cx="0" cy="0" rx="21" ry="15"/>'
        '<path d="M-9 -3v6M9 -3v6" stroke-width="5" fill="none"/></g>'
    )


def _note(x: int = 50, y: int = 28) -> str:
    return f'<path d="M{x} {y}v40m0-40 20-4v13l-20 4" fill="none"/><ellipse cx="{x - 7}" cy="{y + 41}" rx="9" ry="6"/>'


def emblem_svg(name: str, color: str, gold: str) -> str:
    """逐项登记的原创图案；不能用默认字符冒充遗漏的外观。"""
    shared = {
        "gift": '<path d="M19 45h62v39H19zM15 33h70v16H15zM50 32v53"/><path d="M49 33C19 33 24 8 39 18zM51 33C81 33 76 8 61 18z"/>',
        "exchange": '<path d="M20 34h56l-12-12m12 12L64 46M80 65H24l12 12M24 65l12-12" fill="none"/><circle cx="50" cy="50" r="10"/>',
        "star-guitar": _star(30, 33, 23) + '<path d="M49 65 75 26l8 5-24 41C69 88 41 93 35 79c-7-15 1-23 14-14z"/>',
        "sunset": '<path d="M15 64h70M21 74h58M31 84h38" fill="none"/><path d="M22 59a28 28 0 0 1 56 0"/><path d="M50 15v12M19 31l9 8M81 31l-9 8" fill="none"/>',
        "compass-note": '<circle cx="50" cy="50" r="33" fill="none"/><path d="m50 15 10 29-10 8-10-8z"/>'
        + _note(53, 41),
        "butterfly": '<path d="M50 48C22 0 1 28 31 56 2 88 37 96 50 64 63 96 98 88 69 56 99 28 78 0 50 48z"/><path d="M50 41v31m0-31-9-14m9 14 9-14" fill="none"/>',
        "dream-star": _star(50, 44, 31) + '<path d="M15 78q35 17 70 0M22 88q28 12 56 0" fill="none"/>',
        "petal-microphone": "".join(
            f'<ellipse cx="50" cy="24" rx="9" ry="14" transform="rotate({i * 72} 50 50)"/>' for i in range(5)
        )
        + '<rect x="43" y="32" width="14" height="38" rx="7"/><path d="M50 70v18m-10 0h20" fill="none"/>',
        "smile-globe": '<circle cx="50" cy="50" r="35"/><path d="M15 50h70M50 15c-28 18-28 52 0 70 28-18 28-52 0-70" fill="none"/><path d="M34 61q16 18 32 0" fill="none"/>',
        "rose": '<path d="M50 17 68 23 80 41 72 62 51 72 29 64 20 44 31 27z"/><path d="M35 40 49 29 65 39 62 55 45 59 37 49 47 39 56 46" fill="none"/><path d="M50 71v19m0-6 21-11m-21 7-20-11" fill="none"/>',
        "lightning-record": '<circle cx="50" cy="50" r="34"/><circle cx="50" cy="50" r="23" fill="none"/><path d="m58 14-26 41h20l-9 32 29-44H52z"/>',
        "moon-mask": '<path d="M67 15A36 36 0 1 0 81 73 33 33 0 0 1 67 15" fill="none"/><path d="M36 37q22 10 42-3l-3 33q-19 23-37 2z"/><path d="m44 51 10 2m8-1 10-4" fill="none"/>',
        "dual-orbit": '<ellipse cx="39" cy="50" rx="26" ry="17" transform="rotate(-35 39 50)" fill="none"/><ellipse cx="61" cy="50" rx="26" ry="17" transform="rotate(35 61 50)" fill="none"/><circle cx="50" cy="50" r="9"/>',
        "bucket": '<path d="M22 35h56L68 86H32zM19 35q31-18 62 0M37 26l-4-14m16 9 3-14m11 17 9-11" fill="none"/>'
        + _snout(50, 60, 0.65),
        "wheel": '<circle cx="50" cy="50" r="35"/><circle cx="50" cy="50" r="8"/>'
        + "".join(f'<path d="M50 15v27" transform="rotate({i * 45} 50 50)" fill="none"/>' for i in range(8)),
        "eye": '<path d="M10 50Q50 1 90 50 50 99 10 50z"/><circle cx="50" cy="50" r="17"/><circle cx="50" cy="50" r="5"/>',
        "candy-burst": '<path d="m25 40-15-7v34l16-9m48-17 16-8v34l-17-9"/><rect x="26" y="34" width="48" height="31" rx="10"/>'
        + _star(50, 50, 11),
        "dragon-pot": '<path d="M16 46h68q0 35-34 35T16 46zM16 49H7v16h14M84 49h9v16H79M38 40q-17-15 0-24m12 24q-9-13 7-27m5 27q20-16 5-24" fill="none"/>',
        "tea-mist": '<path d="M20 44h51v34H20zM72 49q30-1 9 22H70M16 85h62M30 33q-12-8 1-19m20 19q-12-8 1-19m17 19q-12-8 1-19" fill="none"/>',
        "three-cups": '<path d="M12 45h22l-4 38H16zM39 33h22l-4 50H43zM66 45h22l-4 38H70z"/>' + _star(50, 19, 9),
        "repair-heart": '<path d="M17 62 60 19q19-7 25 4L72 34l3 10 12 2Q85 65 65 58L27 94z"/><path d="M29 47C1 30 22 11 31 26 48 13 61 35 29 47z"/>',
        "umbrella": '<path d="M12 46a38 38 0 0 1 76 0q-13-10-25 0-13-10-25 0-13-10-26 0z"/><path d="M50 10v63q0 18 14 9M26 64l-4 10m52-10-4 10M37 77l-4 10" fill="none"/>',
        "rain-orbits": '<circle cx="37" cy="47" r="23" fill="none"/><circle cx="64" cy="47" r="23" fill="none"/><path d="m26 80-4 9m27-9-4 9m27-9-4 9M21 14l-4 9m34-9-4 9m30-9-4 9" fill="none"/>',
        "rainbow": "".join(
            f'<path d="M{14 + i * 8} 78V60a{36 - i * 8} {36 - i * 8} 0 0 1 {72 - i * 16} 0v18" fill="none"/>'
            for i in range(4)
        ),
        "sushi": '<ellipse cx="50" cy="41" rx="33" ry="20"/><path d="M17 41v27a33 20 0 0 0 66 0V41"/><ellipse cx="50" cy="41" rx="17" ry="10"/><path d="M42 28v51M58 28v51" fill="none"/>',
        "chef": '<path d="M27 63C-2 54 14 25 32 31 31 4 70 4 69 31 92 25 103 55 73 63v23H27z"/><path d="M27 69h46M37 48v15m13-22v22m13-15v15" fill="none"/>',
        "coins": '<ellipse cx="35" cy="69" rx="25" ry="11"/><path d="M10 69v12q25 20 50 0V69"/><circle cx="63" cy="40" r="25"/>'
        + _snout(63, 42, 0.6),
        "mountain": '<path d="m6 79 24-38 15 19 20-44 29 63z"/><path d="m53 43 12 9 10-13M24 51l7 8 7-6" fill="none"/>'
        + _star(65, 9, 7),
        "glass-world": '<path d="M26 17h48M35 18v14q-28 14-19 43 10 25 34 25 29-1 36-25 9-29-20-43V18" fill="none"/><path d="M17 68q33-12 68 0" fill="none"/>'
        + _snout(50, 68, 0.68),
        "postmarks": "".join(
            f'<circle cx="{x}" cy="{y}" r="12" fill="none"/>'
            for x, y in ((50, 20), (23, 42), (77, 42), (33, 74), (67, 74))
        )
        + '<path d="m48 47 5 6 11-14" fill="none"/>',
        "paired-compasses": '<circle cx="34" cy="52" r="23"/><circle cx="68" cy="52" r="23"/><path d="m34 30 8 22-8 22-8-22zM68 30l8 22-8 22-8-22z"/>',
        "compass": '<circle cx="50" cy="54" r="34" fill="none"/><path d="m50 22 12 32-12 32-12-32z"/><path d="M50 5v10M5 54h10m70 0h10" fill="none"/>',
        "globe": '<circle cx="50" cy="47" r="32" fill="none"/><ellipse cx="50" cy="47" rx="16" ry="32" fill="none"/><path d="M18 47h64M50 79v12m-19 0h38" fill="none"/>',
        "three-routes": '<path d="M50 89V43M50 59 18 27m32 32 32-32M50 44V11M18 27v18m0-18h18M82 27v18m0-18H64M50 11 40 22m10-11 10 11" fill="none"/>',
        "big-small": _snout(39, 57, 1.05)
        + _snout(78, 73, 0.52)
        + '<path d="M23 34 12 16l23 6M56 33l11-18-23 7M12 86h75" fill="none"/>',
        "mix-star": _star(50, 47, 31)
        + '<circle cx="17" cy="22" r="7"/><circle cx="83" cy="22" r="7"/><circle cx="16" cy="77" r="7"/><circle cx="84" cy="77" r="7"/>',
        "spotlights": '<path d="m23 19 15 5-26 59M45 17h11L38 83M69 19l-15 5 31 59M12 83h73" fill="none"/>'
        + _note(52, 40),
        "stage-ticket": '<path d="M14 25h72v14q-15 7 0 14v23H14V53q15-7 0-14z"/><path d="M31 27v47" stroke-dasharray="4 5" fill="none"/>'
        + _note(57, 30),
        "linked-tickets": '<path d="M12 21h53v18q-12 6 0 13v17H12zM37 37h51v18q-12 6 0 13v18H37z"/><path d="M24 30v29m26-12v28" fill="none"/>',
        "drums": '<ellipse cx="29" cy="49" rx="23" ry="12"/><path d="M6 49v23q23 24 46 0V49"/><ellipse cx="73" cy="44" rx="21" ry="11"/><path d="M52 44v23q21 20 42 0V44M23 12l57 21M80 12 20 35" fill="none"/>',
        "nine-petals": "".join(
            f'<ellipse cx="50" cy="23" rx="8" ry="15" transform="rotate({i * 40} 50 50)"/>' for i in range(9)
        )
        + '<circle cx="50" cy="50" r="12"/>',
        "curtain": '<path d="M14 14h72v72H14zM16 16q40 22 7 57m61-57Q44 38 77 73M50 14v15" fill="none"/>'
        + _star(50, 57, 15),
        "door-note": '<path d="M18 88V13h49v75M67 88l-36-9V24l36-11" fill="none"/><circle cx="54" cy="58" r="3"/>'
        + _note(80, 26),
        "crossed-sticks": '<path d="m18 18 65 65m0-65L18 83M9 30l20-20m42 0 20 20M10 70l20 20m40 0 20-20" fill="none"/><circle cx="50" cy="50" r="11"/>',
        "notebook": '<path d="M26 12h53v77H26zM17 25h18M17 42h18M17 59h18M17 76h18" fill="none"/>' + _star(54, 48, 19),
        "five-shields": "".join(
            f'<path d="M39 8h22v16q-11 12-22 0z" transform="rotate({i * 72} 50 50)"/>' for i in range(5)
        ),
        "paired-banners": '<path d="M18 86V14l28 9-28 17M81 86V14l-28 9 28 17M20 72h59M32 54l18 14 18-14" fill="none"/>',
        "laurel-five": _laurel() + '<path d="M43 28h17M43 28v22h13q15 1 10 17-6 14-27 3" fill="none"/>',
        "two-paths": '<path d="M18 83V17l32 18 32-18v66L50 65zM50 35v30" fill="none"/><path d="M30 34v27m40-27v27" fill="none"/>',
        "nine-blades": "".join(f'<path d="M47 9h6l-3 30z" transform="rotate({i * 40} 50 50)"/>' for i in range(9))
        + '<circle cx="50" cy="50" r="10"/>',
        "ten-orbits": "".join(f'<circle cx="50" cy="16" r="6" transform="rotate({i * 36} 50 50)"/>' for i in range(10))
        + '<circle cx="50" cy="50" r="22" fill="none"/>',
        "flame": '<path d="M49 8Q73 24 67 46L80 34Q104 80 55 94 2 89 23 39L38 55Q29 29 49 8z"/><path d="M52 49q24 35 0 36-24-1 0-36" fill="none"/>',
        "clock-ribbon": '<circle cx="50" cy="42" r="29" fill="none"/><path d="M50 21v22l17 8M26 69l-8 21 21-6 10 12 6-24M74 66l10 25-23-5" fill="none"/>',
        "three-arrows": "".join(
            f'<path d="M31 17q30-13 43 13l-15-1m15 1-1-15" transform="rotate({i * 120} 50 50)" fill="none"/>'
            for i in range(3)
        ),
        "open-rings": '<path d="M83 64a36 36 0 1 1 1-28M73 59a25 25 0 1 1 1-19M62 54a13 13 0 1 1 0-8" fill="none"/>'
        + _star(83, 24, 9),
        "waveform": '<path d="M9 54h16l7-20 11 43 11-59 10 47 10-29 9 18h9" fill="none"/>',
        "three-books": '<path d="M13 25h22v62H13zM39 13h22v74H39zM65 32h22v55H65zM15 72h18m8 0h18m8 0h18" fill="none"/>',
        "triple-crown": '<path d="m15 47 8 35h55l8-35-25 17-11-29-12 29z"/>'
        + "".join(_star(x, y, 7) for x, y in ((17, 30), (50, 14), (83, 30))),
        "paw-heart": '<path d="M50 53C19 24 6 62 50 87 94 62 81 24 50 53z"/>'
        + "".join(f'<ellipse cx="{x}" cy="{y}" rx="8" ry="11"/>' for x, y in ((20, 30), (42, 19), (65, 19), (84, 33))),
        "ribbon-check": '<path d="m28 69-7 25 28-15 25 14-7-24"/><circle cx="49" cy="42" r="29"/><path d="m32 43 11 11 24-27" fill="none"/>',
        "petals": "".join(
            f'<path d="M50 49Q25 7 50 7 75 7 50 49z" transform="rotate({i * 60} 50 50)"/>' for i in range(6)
        ),
        "three-medals": '<path d="M10 12h80v9H10zM25 21v20M50 21v20M75 21v20" fill="none"/>'
        + "".join(f'<circle cx="{x}" cy="58" r="13"/>' for x in (22, 50, 78)),
        "laurel": _laurel() + _star(50, 45, 19),
        "crown": '<path d="m14 35 13 45h47l13-45-26 17-11-30-12 30zM26 88h49"/>'
        + "".join(f'<circle cx="{x}" cy="{y}" r="5"/>' for x, y in ((13, 27), (50, 13), (88, 27))),
        "finish-flag": '<path d="M23 91V14h56v44H23" fill="none"/>'
        + "".join(
            f'<rect x="{25 + x * 13}" y="{16 + y * 13}" width="13" height="13"/>'
            for y in range(3)
            for x in range(4)
            if (x + y) % 2 == 0
        ),
        "diamond-wings": '<path d="m50 20 22 29-22 31-22-31zM27 45 5 29l7 26 17 12M73 45l22-16-7 26-17 12"/><path d="M29 49h42M50 20v60" fill="none"/>',
        "gift-ribbon": '<path d="M17 42h66v43H17zM50 42v44M15 30h70v13H15zM49 30C10 27 31 2 49 30M51 30C90 27 69 2 51 30" fill="none"/>',
    }
    if name not in shared:
        raise ValueError(f"原创徽记未制作：{name}")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-4 -4 108 108" width="100" height="100">'
        f'<g fill="{gold}" fill-opacity=".20" stroke="{color}" stroke-width="3.5" '
        f'stroke-linecap="round" stroke-linejoin="round">{shared[name]}</g></svg>'
    )


def _data_url(payload: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _border_svg(item: dict[str, Any], color: str, gold: str) -> str:
    corner = emblem_svg(item["emblem"], color, gold).replace('width="100" height="100"', 'width="48" height="48"')
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="192" height="192" viewBox="0 0 192 192">',
        f'<path d="M31 9H161Q183 9 183 31V161Q183 183 161 183H31Q9 183 9 161V31Q9 9 31 9z" '
        f'fill="none" stroke="{color}" stroke-width="5"/>',
        f'<rect x="18" y="18" width="156" height="156" rx="3" fill="none" stroke="{gold}" stroke-width="2"/>',
    ]
    for x, y, rotation in ((3, 3, 0), (141, 3, 90), (141, 141, 180), (3, 141, 270)):
        pieces.append(f'<g transform="translate({x} {y}) rotate({rotation} 24 24)">{corner}</g>')
    pieces.append("</svg>")
    return "".join(pieces)


def _plate_html(item: dict[str, Any], theme: dict[str, str], master: str) -> str:
    color, gold = item.get("color", theme["color"]), theme["gold"]
    rank = int(item.get("rank", 0))
    variant = f" rank-{rank}" if rank else f" theme-{item['theme']}"
    emblem = _data_url(emblem_svg(item["emblem"], color, gold).encode(), "image/svg+xml")
    title = theme["label"] if rank else item["name"]
    kicker = (
        f"第 {item['season']} 期 · {theme.get('metric', '活动纪念')}"
        if item.get("season")
        else f"PiG Dream! · {theme['label']}"
    )
    rank_html = (
        f'<div class="rank-seal"><b>{"TOP 10" if rank == 10 else rank}</b><span>{"10牌" if rank == 10 else "第" + str(rank) + "名"}</span></div>'
        if rank
        else ""
    )
    if not rank and item.get("season"):
        rank_html = f'<div class="rank-seal title-seal"><img src="{emblem}" alt=""></div>'
    bottom = "第 4–10 名 · TOP 10" if rank == 10 else ("活动色段 · " + str(rank) + " 牌" if rank else "原创收藏称号")
    return (
        f'<article class="plate{variant}" style="--ink:{color};--gold:{gold}" data-export>'
        f'<div class="plate-shape"><img class="master" src="{master}" alt="">'
        '<div class="foil-line"></div><div class="foil-line inner"></div>'
        '<div class="side-rail rail-a"></div><div class="side-rail rail-b"></div>'
        f'<img class="plate-emblem" src="{emblem}" alt="">{rank_html}'
        f'<div class="title-copy"><p>{escape(kicker)}</p><h1 data-fit>{escape(title)}</h1>'
        f'<div class="title-rule"></div><small>{escape(bottom)}</small></div></div></article>'
    )


def _badge_html(item: dict[str, Any], theme: dict[str, str]) -> str:
    color, gold = item.get("color", theme["color"]), theme["gold"]
    icon = _data_url(emblem_svg(item["emblem"], color, gold).encode(), "image/svg+xml")
    return (
        f'<article class="badge" style="--ink:{color};--gold:{gold}" data-export>'
        '<svg class="medal-edge" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        f'<path d="m78 155-26 81 75-37 77 37-27-81" fill="{color}" fill-opacity=".80"/>'
        f'<path d="m87 168-14 44 52-27 53 27-15-44" fill="none" stroke="{gold}" stroke-width="3"/>'
        f'<circle cx="128" cy="115" r="101" fill="#fffdf9" stroke="{gold}" stroke-width="5"/>'
        f'<circle cx="128" cy="115" r="92" fill="none" stroke="{color}" stroke-width="2"/>'
        f'<circle cx="128" cy="115" r="84" fill="{color}" fill-opacity=".055" stroke="{gold}" '
        'stroke-width="1" stroke-dasharray="3 7"/></svg>'
        f'<img class="badge-emblem" src="{icon}" alt=""></article>'
    )


def _frame_html(item: dict[str, Any], theme: dict[str, str], border: str) -> str:
    icon = _data_url(
        emblem_svg(item["emblem"], item.get("color", theme["color"]), theme["gold"]).encode(), "image/svg+xml"
    )
    return (
        f'<article class="frame-preview" data-export style="--ink:{item.get("color", theme["color"])}">'
        f'<div class="frame-border" style="border-image-source:url(\'{border}\')"></div>'
        f'<img class="frame-emblem" src="{icon}" alt=""><p>PiG Dream!</p>'
        f"<h2>{escape(item['name'])}</h2><span>边缘装饰 · 内容留白</span></article>"
    )


STYLE = """
*{box-sizing:border-box}html,body{margin:0;background:transparent;color:#443947}
body{font-family:CosmeticCJK,'Microsoft YaHei',sans-serif}.plate{width:1200px;height:360px;padding:9px}
.plate-shape{position:relative;width:100%;height:100%;overflow:hidden;border:3px solid var(--gold);background:#fffdfb;
clip-path:polygon(2% 0,98% 0,100% 10%,100% 90%,98% 100%,2% 100%,0 90%,0 10%)}
.master{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
.foil-line{position:absolute;inset:8px;border:1px solid var(--gold);pointer-events:none}
.foil-line.inner{inset:15px;border-color:var(--ink);opacity:.38}
.side-rail{position:absolute;top:20px;bottom:20px;width:6px;background:var(--gold);opacity:.68}
.rail-a{left:19px}.rail-b{right:19px}.title-copy{position:absolute;left:400px;right:90px;top:67px;text-align:center;
padding:15px 0 10px;background:radial-gradient(ellipse,#fffdfcfa 48%,#fffdfcc2 70%,#ffffff00 75%)}
.title-copy p{margin:0 0 10px;font-size:21px;font-weight:600;letter-spacing:2px;color:var(--ink)}
.title-copy h1{margin:0;white-space:nowrap;font-size:72px;font-weight:900;color:var(--ink);line-height:1.35;
text-shadow:0 2px 0 white,2px 0 0 white,-2px 0 0 white,0 -2px 0 white}
.title-copy small{font-size:19px;letter-spacing:3px;color:#786550}
.title-rule{height:2px;background:linear-gradient(90deg,transparent,var(--gold),transparent);margin:11px auto 9px;width:80%}
.plate-emblem{position:absolute;right:18px;top:136px;width:83px;height:83px;filter:drop-shadow(0 0 4px white)}
.rank-seal{position:absolute;left:78px;top:70px;width:190px;height:190px;border-radius:50%;
display:flex;align-items:center;justify-content:center;flex-direction:column;background:radial-gradient(ellipse,#fffaf0ee,#fffdf9b5);
border:4px double var(--gold);color:var(--ink)}.rank-seal b{font-size:98px;line-height:1.3}.rank-seal span{font-size:20px;margin-top:9px}
.rank-1 .plate-shape{clip-path:polygon(0 10%,5% 10%,7% 0,93% 0,95% 10%,100% 10%,100% 90%,95% 90%,93% 100%,7% 100%,5% 90%,0 90%);border-width:5px}
.rank-2 .plate-shape{clip-path:polygon(4% 0,96% 0,100% 50%,96% 100%,4% 100%,0 50%)}
.rank-3 .plate-shape{clip-path:polygon(0 0,100% 0,98% 50%,100% 100%,0 100%,2% 50%)}
.rank-10 .plate-shape{clip-path:polygon(0 8%,3% 8%,3% 0,97% 0,97% 8%,100% 8%,100% 92%,97% 92%,97% 100%,3% 100%,3% 92%,0 92%)}
.rank-10 .rank-seal{border-radius:8px;width:204px;left:70px}.rank-10 .rank-seal b{font-size:50px}
.rank-2 .rank-seal{border-radius:8px;transform:rotate(5deg)}.rank-2 .rank-seal>*{transform:rotate(-5deg)}
.rank-3 .rank-seal{border-radius:44% 44% 48% 48%;border-width:5px}
.title-seal img{width:126px;height:126px}
.rank-1 .title-copy,.rank-2 .title-copy,.rank-3 .title-copy,.rank-10 .title-copy{left:446px;right:76px}
.rank-1 .title-copy h1,.rank-2 .title-copy h1,.rank-3 .title-copy h1,.rank-10 .title-copy h1{font-size:67px}
.theme-ultimate .foil-line.inner{border-width:3px}.theme-battle .foil-line.inner{border-style:dashed}
.badge{position:relative;width:256px;height:256px}.medal-edge{position:absolute;inset:0;width:256px;height:256px}
.badge-emblem{position:absolute;left:60px;top:47px;width:136px;height:136px}
.frame-preview{position:relative;width:480px;height:600px;text-align:center;padding:90px 45px;background:#fffafc}
.frame-border{position:absolute;inset:0;border:32px solid transparent;border-image-slice:64;border-image-repeat:stretch;pointer-events:none}
.frame-emblem{width:170px;height:170px;display:block;margin:0 auto 25px}.frame-preview p{font-size:20px;letter-spacing:2px;color:var(--ink)}
.frame-preview h2{font-size:29px;line-height:1.6;margin:22px 0;color:var(--ink)}.frame-preview span{font-size:17px;color:#81707d}
.border-export{width:192px;height:192px}.border-export img{width:192px;height:192px;display:block}
"""


def _file_record(path: Path) -> dict[str, Any]:
    record = {"path": path.relative_to(ART_ROOT).as_posix(), "sha256": _sha(path), "bytes": path.stat().st_size}
    if path.suffix != ".svg":
        with Image.open(path) as picture:
            record.update(
                width=picture.width,
                height=picture.height,
                format=picture.format,
                alpha=picture.mode == "RGBA",
                transparent=picture.mode == "RGBA" and picture.getchannel("A").getextrema()[0] < 255,
            )
    return record


async def _shot(page, html: str, width: int, height: int, target: Path) -> dict[str, Any]:
    await page.set_viewport_size({"width": width, "height": height})
    await page.locator("#canvas").evaluate("(e,html)=>e.innerHTML=html", html)
    await page.evaluate("document.fonts.ready")
    await page.locator("[data-fit]").evaluate_all("""nodes=>nodes.forEach(e=>{
      let size=parseFloat(getComputedStyle(e).fontSize); while(e.scrollWidth>e.clientWidth && size>36){e.style.fontSize=(--size)+'px';}
    })""")
    await page.evaluate("Promise.all([...document.images].map(i=>i.decode().catch(()=>null)))")
    diagnostics = await page.locator("[data-export]").evaluate("""root=>{
      const r=root.getBoundingClientRect(), t=[...root.querySelectorAll('h1,h2,p,small,span,b')];
      return {width:Math.round(r.width),height:Math.round(r.height),
        clipped:t.filter(e=>e.scrollWidth>e.clientWidth+2||e.scrollHeight>e.clientHeight+2).map(e=>e.textContent),
        outside:t.filter(e=>{let b=e.getBoundingClientRect();return b.left<r.left||b.right>r.right||b.top<r.top||b.bottom>r.bottom}).map(e=>e.textContent),
        broken:[...root.querySelectorAll('img')].filter(i=>!i.complete||!i.naturalWidth).length};
    }""")
    if diagnostics["clipped"] or diagnostics["outside"] or diagnostics["broken"]:
        raise RuntimeError(f"外观导出布局检查失败：{target.name} {diagnostics}")
    await page.locator("[data-export]").screenshot(path=str(target), omit_background=True, animations="disabled")
    with Image.open(target) as picture:
        if picture.size != (width, height):
            raise ValueError(f"外观输出尺寸错误：{target.name}")
    return diagnostics


async def build(browser_path: Path, font_path: Path) -> None:
    data = load_cosmetic_definitions()
    ART_ROOT.mkdir(parents=True, exist_ok=True)
    QA_ROOT.mkdir(parents=True, exist_ok=True)
    source_hashes: dict[Path, str] = {}
    masters: dict[str, str] = {}
    weekly_hashes: set[str] = set()
    for key, theme in data["themes"].items():
        source = (MASTER_ROOT / theme["master"]).resolve()
        if not source.is_relative_to(MASTER_ROOT.resolve()) or not source.is_file():
            raise FileNotFoundError(f"缺少经审核的无字插画母版：{theme['master']}")
        with Image.open(source) as picture:
            if picture.width < 900 or picture.height < 270:
                raise ValueError(f"插画母版清晰度不足：{theme['master']}")
            original_width, original_height = picture.size
        source_hashes[source] = _sha(source)
        if key.startswith("weekly-"):
            if source_hashes[source] in weekly_hashes:
                raise ValueError("不同期数不能复用同一周榜插画母版")
            weekly_hashes.add(source_hashes[source])
        masters[key] = _data_url(source.read_bytes(), "image/png")
        if "crop" in theme:
            x, y, width, height = theme["crop"]
            if not (0 <= x < x + width <= original_width and 0 <= y < y + height <= original_height):
                raise ValueError("母版裁切视窗超出原图")
            # 仅在排版阶段裁掉审核确认的外部留白，原 PNG 保持原字节与比例。
            viewport = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {width} {height}">'
                f'<image width="{original_width}" height="{original_height}" href="{masters[key]}"/></svg>'
            )
            masters[key] = _data_url(viewport.encode(), "image/svg+xml")
    if not font_path.is_file():
        raise FileNotFoundError("需要 --font 指定本机可用的中文字体；字体只用于导出，不随素材包分发")
    source_hashes[font_path] = _sha(font_path)
    source_hashes[ART_ROOT / "definitions.json"] = _sha(ART_ROOT / "definitions.json")
    source_hashes[Path(__file__).resolve()] = _sha(Path(__file__).resolve())
    page_path = QA_ROOT / "export-host.html"
    page_path.write_text(
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>'
        f'@font-face{{font-family:CosmeticCJK;src:url("{font_path.resolve().as_uri()}")}}{STYLE}'
        '</style></head><body><div id="canvas"></div></body></html>',
        encoding="utf-8",
    )
    exported, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=str(browser_path))
        try:
            page = await browser.new_page()
            await page.route("http://**/*", lambda route: route.abort())
            await page.route("https://**/*", lambda route: route.abort())
            await page.goto(page_path.as_uri(), wait_until="load")
            browser_version = browser.version
            for index, item in enumerate(data["entries"], 1):
                theme = data["themes"][item["theme"]]
                color, gold = item.get("color", theme["color"]), theme["gold"]
                directory = ART_ROOT / item["id"]
                directory.mkdir(exist_ok=True)
                icon = directory / "emblem.svg"
                icon.write_text(emblem_svg(item["emblem"], color, gold), encoding="utf-8")
                files = {"emblem": _file_record(icon)}
                if item["kind"] == "frame":
                    border_source = directory / "border.svg"
                    border_source.write_text(_border_svg(item, color, gold), encoding="utf-8")
                    border_data = _data_url(border_source.read_bytes(), "image/svg+xml")
                    border = directory / "border.png"
                    await _shot(
                        page,
                        f'<div class="border-export" data-export><img src="{border_data}"></div>',
                        192,
                        192,
                        border,
                    )
                    files.update(border=_file_record(border), border_source=_file_record(border_source))
                    html = _frame_html(item, theme, _data_url(border.read_bytes(), "image/png"))
                    width, height = 480, 600
                elif item["kind"] == "title" or item.get("rank"):
                    html = _plate_html(item, theme, masters[item["theme"]])
                    width, height = 1200, 360
                else:
                    html = _badge_html(item, theme)
                    width, height = 256, 256
                png = directory / "art.png"
                diagnostic = await _shot(page, html, width, height, png)
                compact = directory / "compact.webp"
                with Image.open(png) as picture:
                    picture.thumbnail((600, 240) if width == 1200 else ((240, 300) if width == 480 else (128, 128)))
                    picture.save(compact, "WEBP", quality=90, method=6)
                files.update(png=_file_record(png), compact=_file_record(compact))
                exported.append({"id": item["id"], "kind": item["kind"], "files": files})
                checks.append({"id": item["id"], **diagnostic})
                print(f"{index:02}/{len(data['entries'])} {item['id']}", flush=True)
        finally:
            await browser.close()
    if not all(_sha(path) == digest for path, digest in source_hashes.items()):
        raise RuntimeError("导出期间来源文件发生变化，未发布图像清单")
    sources = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": digest}
        for path, digest in source_hashes.items()
        if path.is_relative_to(ROOT)
    ]
    manifest = {
        "schema_version": 1,
        "art_version": data["art_version"],
        "origin": "原创无字AI插画母版 + 原生SVG徽记 + 本地Chromium精确文字；没有官方队标素材",
        "source_policy": "母版按实际PNG模式保留；白底不冒充透明插画。成品外轮廓和徽章/边框有alpha。",
        "build": {"browser": browser_version, "font_filename": font_path.name, "font_sha256": source_hashes[font_path]},
        "sources": sources,
        "counts": dict(Counter(item["kind"] for item in data["entries"])),
        "entries": exported,
    }
    (ART_ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (QA_ROOT / "layout-check.json").write_text(
        json.dumps({"sources_unchanged": True, "items": checks}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    gallery = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>body{font-family:Microsoft YaHei;background:#fff7fb;padding:24px}section{display:inline-block;vertical-align:top;width:600px;margin:12px;padding:10px;background:white}img{max-width:100%;max-height:300px}p{font-size:18px}</style><h1>原创外观生产资产 · 本地全量校阅</h1>'
    ]
    for item in data["entries"]:
        gallery.append(
            f'<section><p>{escape(item["name"])}</p><img src="{(ART_ROOT / item["id"] / "art.png").as_uri()}"></section>'
        )
    (QA_ROOT / "index.html").write_text("".join(gallery), encoding="utf-8")
    print(
        json.dumps(
            {"exported": len(exported), "counts": manifest["counts"], "sources_unchanged": True}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, default=DEFAULT_BROWSER)
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    arguments = parser.parse_args()
    asyncio.run(build(arguments.browser, arguments.font))
