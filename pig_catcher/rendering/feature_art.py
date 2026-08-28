"""功能卡原创图形：白名单本地插画、确定性轮盘与有界的美术派生。

这里只表现已经提交的业务事实；不查询玩家、不抽奖，也不接受文件路径。
SVG 的几何是代码原生 UI 素材，背景插画由独立的美术母版提供。
"""

# 保持原创矢量路径为整行常量，便于将路径直接交给矢量工具复核。
# ruff: noqa: E501

from __future__ import annotations

import math
from base64 import b64encode
from collections.abc import Iterable, Mapping
from functools import lru_cache
from io import BytesIO
from itertools import islice
from pathlib import Path
from typing import Any

from markupsafe import Markup, escape
from PIL import Image

_ASSETS = Path(__file__).with_name("assets") / "ui" / "masters"
_FAMILIES = frozenset({"collection", "travel", "stage", "battle", "ultimate", "weekly-001"})
_COLOURS = (
    "#71b6a0",
    "#bca0d4",
    "#efacc4",
    "#92b8d6",
    "#e4c276",
    "#ad8eb4",
    "#81bdc0",
    "#cd9e88",
    "#a6b884",
    "#a5a1cf",
)

# Each silhouette has an independent identity; labels stay real text beside the icon.
_SHAPES = {
    "pig": '<path d="M25 31 19 16l20 10m23 0 16-10-4 22"/><ellipse cx="48" cy="51" rx="29" ry="27"/><ellipse cx="48" cy="59" rx="16" ry="11"/><circle cx="40" cy="58" r="2"/><circle cx="56" cy="58" r="2"/><path d="M32 43h1m30 0h1"/>',
    "whistle": '<path d="m23 49 15-13h24l12-12 9 9-15 17v10c0 16-24 21-34 8Z"/><circle cx="46" cy="53" r="9"/><path d="m20 30 6 5m11-16 1 8m-21 19 8 1"/>',
    "super-whistle": '<path d="m23 49 15-13h24l12-12 9 9-15 17v10c0 16-24 21-34 8Z"/><circle cx="46" cy="53" r="9"/><path d="m29 27 3-12 7 6 8-10 8 10 7-6 3 12Z"/>',
    "radar": '<circle cx="46" cy="43" r="25"/><path d="m64 63 18 17M46 18v50M21 43h50m-25 0 18-17"/><circle cx="39" cy="47" r="5"/><circle cx="54" cy="32" r="3"/>',
    "corn": '<path d="M37 64C22 53 32 19 46 15c16 4 24 37 8 51Z"/><path d="M21 44c2 31 10 38 26 39 0-16-5-30-26-39Zm53 0c-2 31-10 38-26 39 0-16 5-30 26-39ZM37 29h20m-23 9h25m-25 9h24M44 19v43m8-43v43"/>',
    "feed-fat": '<path d="m26 28 7-13h29l9 13-5 52H31Z"/><path d="M27 32h43"/><ellipse cx="49" cy="53" rx="16" ry="12"/><path d="M42 52h1m12 0h1M78 48v16m-8-8h16"/>',
    "feed-lean": '<path d="m26 28 7-13h29l9 13-5 52H31Z"/><path d="M27 32h43M39 67c-12-24 10-26 22-25-3 16-7 23-22 25Zm0 0 17-20"/>',
    "bounty": '<path d="m30 16 7 16h23l8-16-17 5Z"/><path d="M38 32C13 54 19 78 47 80s38-24 12-48"/><circle cx="48" cy="56" r="13"/><path d="M48 47v18m-6-14c10-9 17 6 4 6s-4 14 7 5"/>',
    "spice": '<path d="M36 14h26v14H36Zm-3 15h32l6 44c0 9-46 9-46 0Z"/><path d="M27 45h42M31 64h3m11-9h2m10 13h2M42 20h1m12 0h1"/>',
    "super-spice": '<path d="M36 18h26v13H36Zm-3 14h32l6 41c0 9-46 9-46 0Z"/><path d="m48 42 5 10 11 2-8 8 2 12-10-6-10 6 2-12-8-8 11-2Z"/>',
    "knife": '<path d="m20 70 12 11 19-23-11-9Z"/><path d="m41 49 7-34c32 10 41 21 28 33L52 61Z"/><path d="m28 68 4 4"/>',
    "pot": '<path d="M20 43h56v24c0 21-56 21-56 0ZM15 38h67M41 32v-9h16v9M20 50H10v12h10m56-12h10v12H76M40 15c-8-8 8-9 1-16m18 16c-8-8 8-9 1-16"/>',
    "lunchbox": '<rect x="17" y="28" width="65" height="49" rx="8"/><path d="M37 28v-9h26v9M18 44h63M42 47v27m19-27v27"/>',
    "lid": '<path d="M15 62h68M22 56a26 26 0 0 1 53 0M43 26v-9h13v9"/><path d="m29 67 14 12 29-21"/>',
    "stove": '<path d="M24 56h50v24H24Z"/><path d="M36 56C11 32 54 39 44 13c34 20 32 30 18 43M36 68h2m20 0h2"/>',
    "apron": '<path d="M34 30V15h28v15l16 14-4 39H22l-4-39Z"/><path d="M38 47h20v17H38ZM34 22h28M23 34 9 23m63 11 14-11"/>',
    "spear": '<path d="m25 82 38-54M61 30 67 12l15 2-7 8 10 10-10 14-2-13Z"/><path d="m47 43 14 9m-29 5 15 11"/>',
    "feed": '<path d="m26 28 7-13h29l9 13-5 52H31Z"/><path d="M27 32h43m-21 8v30m-1-15C32 53 32 44 32 44c12 0 16 6 16 11Zm1 7c15-2 15-11 15-11-12 0-15 6-15 11Z"/>',
    "cookware": '<path d="M21 47h53v29H21ZM18 43h58m-2 10h12v13H74"/><path d="M32 40V15m13 25V14m14 26V15M27 15h37M32 21h27"/>',
    "ore": '<path d="m17 55 16-25 25-13 25 35-20 26H30ZM17 55h66M33 30l11 25-14 23m14-23 19 23m-5-61L44 55"/>',
    "parts": '<path d="m41 13 15 0 3 11 9 5 11-3 8 13-8 8v10l8 8-8 13-11-3-9 5-3 11H41l-3-11-9-5-11 3-8-13 8-8V47l-8-8 8-13 11 3 9-5Z" transform="translate(2,-4) scale(.9)"/><circle cx="47" cy="46" r="13"/>',
    "fiber": '<path d="M24 29c32-11 52 20 42 43S18 83 22 57 66 9 75 40 27 65 20 44 69 20 70 64M34 75l42 10"/>',
    "stage-parts": '<path d="M18 22h60v17H18Zm12 17 18 18 18-18M38 63h20v16H38ZM24 22v17m16-17v17m16-17v17m15 24L83 72M22 61 9 72M48 14V5"/>',
    "supplies": '<rect x="25" y="25" width="48" height="57" rx="9"/><path d="M34 25v-9h29v9M25 47h48m-35 9h23v19H38ZM18 37v24m61-24v24"/>',
    "notes": '<path d="M21 16h51v66H21Zm12 0v66M17 28h11m-11 13h11m-11 13h11m-11 13h11M42 33h18m-18 11h18m-18 11h12"/>',
    "map": '<path d="m16 22 22-8 24 9 19-8v62l-20 8-24-8-21 8Zm22-8v63m24-54v62M23 56c14-21 27 16 49-19"/>',
    "camera": '<path d="m21 33 7-14h31l8 14h13v44H16V33Z"/><circle cx="48" cy="52" r="17"/><circle cx="48" cy="52" r="10"/><path d="M69 42h4"/>',
    "compass": '<circle cx="48" cy="48" r="31"/><path d="M48 12V5m0 86v-8M12 48H5m86 0h-8m-25-62L41 41 27 70l28-15Z"/><circle cx="48" cy="48" r="3"/>',
    "box": '<path d="m16 29 31-15 34 15v44L49 88 16 73Zm0 0 33 16 32-16M49 45v43M31 22l34 15v21"/>',
    "ticket": '<path d="M18 27h63v15c-11 0-11 13 0 13v15H18V55c11 0 11-13 0-13Zm15 0v7m0 7v7m0 7v7m0 5v3"/><path d="m57 35 5 8 8 2-6 6 1 9-8-4-8 4 2-9-7-6 9-2Z"/>',
    "code-ticket": '<path d="M18 27h63v15c-11 0-11 13 0 13v15H18V55c11 0 11-13 0-13Z"/><path d="m43 35-5 28m20-28-5 28M31 44h37m-38 12h36"/>',
    "pig-ticket": '<path d="M12 28h73v14c-11 0-11 14 0 14v15H12V56c11 0 11-14 0-14Z"/><ellipse cx="50" cy="50" rx="15" ry="12"/><path d="m39 40-2-8 10 5m12 3 3-8-10 5m-9 15h1m11 0h1"/>',
    "coin": '<circle cx="48" cy="48" r="31"/><circle cx="48" cy="48" r="24"/><ellipse cx="48" cy="49" rx="14" ry="10"/><path d="M41 48h1m12 0h1M39 20l4 5m15-5-4 5"/>',
    "voice": '<rect x="36" y="14" width="25" height="41" rx="12"/><path d="M28 41v7c0 25 41 25 41 0v-7M48 67v16M35 84h26M38 27h20m-20 10h20"/>',
    "guitar": '<path d="m30 47 27-28 11 11-26 27c16 25-11 38-26 22S11 47 30 47Zm31-32 9-9 12 13-10 8M35 58l27-30"/><circle cx="29" cy="66" r="5"/>',
    "bass": '<path d="m29 43 28-28 10 10-26 26c21 25-6 46-24 29-17-16-8-38 12-37Zm29-30 9-9 11 12-9 9M25 62l37-40m-31 42 37-40"/><path d="m18 71 15 9"/>',
    "drums": '<ellipse cx="48" cy="37" rx="29" ry="12"/><path d="M19 37v29c0 17 58 17 58 0V37M29 44v29m19-25v31m20-35v29M22 17l46 14m4-15L31 30"/>',
    "keyboard": '<path d="M14 25h70v47H14Zm12 15v32m12-32v32m12-32v32m12-32v32m12-32v32M14 40h70"/><path d="M28 41v14m12-14v14m24-14v14m12-14v14" stroke-width="6"/>',
    "violin": '<path d="m38 40 24-25 8 8-25 25c11 10 5 27-8 31S16 67 23 56c-4-11 5-21 15-16Z"/><path d="m29 62 37-43M48 77 84 18m-24-4 7-7 10 10-7 7"/>',
    "dj": '<path d="M13 27h70v49H13Z"/><circle cx="36" cy="52" r="17"/><circle cx="36" cy="52" r="6"/><path d="M67 37v27m-7-13h14M72 19h9v21"/>',
    "wristband": '<path d="m30 18 28 1 16 51-26 12-27-24Z"/><path d="m26 31 34-1M32 65l31-13m-25-13 16 2-10 7 6 11"/>',
    "bandage": '<path d="m17 38 21-21 42 42-21 21Z"/><path d="m34 25 37 37m-47-30 37 37M35 50h1m9-8h1m-1 17h1m8-9h1"/>',
    "confetti": '<path d="m20 76 8-41 33 33Z"/><path d="m32 51 19 11m-24 0 15 12M42 22l8 6 6-10M61 41l15-5 4 5M67 65l12 8M34 15h1m44 5h1M19 29h1m40 24h1"/>',
    "cable": '<path d="M26 26c49-29 68 59 9 47-36-7-2-50 16-25 12 17-18 26-19 7M16 33l11-7M9 29l8 12m-9-9-4 2m7 4-4 2M69 76l13 7m-2-8-5 12"/>',
    "cue": '<path d="M20 21h58v58H20Z"/><path d="m32 39 6 6 12-15m-17 30h30m-30 9h23M59 34h8m-8 11h8"/>',
    "recorder": '<path d="M24 61h51v21H24Zm8 0V45m-9-17 24 13 22-25-37-6Z"/><path d="M47 41v19m-11 11h1m15 0h11"/>',
    "star": '<path d="m48 14 10 21 23 4-17 17 4 25-20-12-21 12 4-25-17-17 23-4Z"/>',
    "flower": '<path d="M48 35C17 6 12 57 35 48 4 79 59 88 48 61 75 91 94 40 62 48 90 13 39 5 48 35Z"/><circle cx="48" cy="48" r="10"/>',
    "record": '<circle cx="48" cy="48" r="32"/><circle cx="48" cy="48" r="19"/><circle cx="48" cy="48" r="5"/><path d="M26 36a25 25 0 0 1 19-12m10 49a25 25 0 0 0 19-18"/>',
    "wings": '<path d="M48 69C7 65 4 29 18 19c5 20 15 14 24 33l6 17 6-17c9-19 19-13 24-33 14 10 11 46-30 50Z"/><path d="m19 34 21 26m36-26L55 60m-36-9 19 14m39-14-19 14"/>',
    "mask": '<path d="M17 23c25 16 40 16 64 0v26c0 16-14 25-32 35-18-10-32-19-32-35Z"/><path d="m26 45 14 4m18 0 14-4M37 65c10-7 14-7 23 0"/>',
    "infinity": '<path d="M48 49C29 9 1 35 16 58 34 89 65 3 82 38 97 69 62 89 48 49Z"/>',
    "crown": '<path d="m14 33 18 14 17-26 17 26 17-14-9 38H23Zm9 46h51M43 58h12"/>',
    "domain": '<path d="M20 78h59M26 69h47V40H26Zm-5-31L48 22l28 16M19 22 48 10l30 12M37 41v28m24-28v28M33 77v-8m28 8v-8"/>',
    "blue": '<circle cx="48" cy="48" r="20"/><path d="M10 48h18m-8-8 8 8-8 8M86 48H68m8-8-8 8 8 8M48 10v18m-8-8 8 8 8-8M48 86V68m-8 8 8-8 8 8"/>',
    "red": '<circle cx="48" cy="48" r="18"/><path d="M25 48H7m8-8-8 8 8 8M71 48h18m-8-8 8 8-8 8M48 25V7m-8 8 8-8 8 8M48 71v18m-8-8 8 8 8-8"/>',
    "purple": '<circle cx="36" cy="48" r="23"/><circle cx="61" cy="48" r="23"/><path d="m48 17 7 16-7 14-7-14Zm0 62-7-16 7-14 7 14Z"/>',
    "roulette": '<circle cx="48" cy="49" r="30"/><path d="m48 8 7 12H41Zm0 11v60M22 34l52 30M22 64l52-30"/><circle cx="48" cy="49" r="7"/>',
    "exchange": '<path d="M14 35h60L61 22m21 39H22l13 13M74 35 61 48m-39 13 13-13"/>',
    "receipt": '<path d="M22 13h52v71L61 77l-13 7-13-7-13 7Z"/><path d="M33 30h29M33 43h29m-29 13h17m10 9 5 5 9-10"/>',
    "postmark": '<circle cx="48" cy="48" r="30"/><circle cx="48" cy="48" r="23"/><path d="m31 48 11 12 25-27M10 35h14M7 44h15M8 53h15M12 62h14"/>',
    "rose": '<path d="m48 15 15 8 9 18-7 22-17 12-17-12-7-22 9-18Z"/><path d="m48 27 14 11-2 18-13 8-12-11 2-15 11-5 8 9-5 10-7-2m3 25v10m1-3c16 2 24-5 24-13-15-3-20 6-24 13"/>',
}

_NAMES = {
    "whistle": "幸运猪哨",
    "super-whistle": "超级幸运猪哨",
    "radar": "星辉探猪镜",
    "corn": "巨物玉米",
    "feed-fat": "增肥饲料",
    "feed-lean": "瘦身饲料",
    "bounty": "丰收钱袋",
    "spice": "主厨香料",
    "super-spice": "超级主厨香料",
    "knife": "锋利菜刀",
    "pot": "慢炖锅",
    "lunchbox": "大份餐盒",
    "lid": "保底锅盖",
    "stove": "升星炉芯",
    "apron": "主厨围裙",
    "spear": "天逆鉾",
    "feed": "猪饲料",
    "cookware": "厨具",
    "ore": "训练矿石",
    "parts": "机关零件",
    "fiber": "灵巧纤维",
    "stage-parts": "舞台组件",
    "supplies": "旅行补给",
    "notes": "远行手记",
    "map": "区域地图",
    "camera": "纪念相机",
    "compass": "奇遇罗盘",
    "box": "整理箱",
    "ticket": "奖励券",
    "code-ticket": "编号修改券",
    "pig-ticket": "猪猪自选券",
    "coin": "猪币",
    "voice": "主唱",
    "guitar": "吉他",
    "bass": "贝斯",
    "drums": "鼓",
    "keyboard": "键盘",
    "violin": "小提琴",
    "dj": "DJ",
    "wristband": "练习护腕",
    "bandage": "应急绷带",
    "confetti": "礼花",
    "cable": "备用线缆",
    "cue": "提示卡",
    "recorder": "留声机",
    "star": "星星",
    "flower": "花瓣",
    "record": "唱片",
    "wings": "翅膀",
    "mask": "假面",
    "infinity": "梦境信号",
    "crown": "队长",
    "domain": "伏魔御厨子",
    "blue": "苍",
    "red": "赫",
    "purple": "茈",
    "roulette": "轮盘",
    "exchange": "交易",
    "receipt": "回执",
    "postmark": "旅行邮戳",
    "rose": "蔷薇回响",
    "pig": "猪猪",
}
_ALIASES = {name: key for key, name in _NAMES.items()}
_ALIASES.update(
    {
        "lucky-whistle": "whistle",
        "super-lucky-whistle": "super-whistle",
        "star-pig-radar": "radar",
        "giant-corn": "corn",
        "fat-feed": "feed-fat",
        "lean-feed": "feed-lean",
        "bounty-bell": "bounty",
        "chef-spice": "spice",
        "super-chef-spice": "super-spice",
        "sharp-knife": "knife",
        "slow-stew-pot": "pot",
        "large-lunchbox": "lunchbox",
        "no-downgrade-lid": "lid",
        "ascension-stove-core": "stove",
        "chef-apron": "apron",
        "inverted-spear-of-heaven": "spear",
        "training-ore": "ore",
        "machine-parts": "parts",
        "agility-fiber": "fiber",
        "stage-components": "stage-parts",
        "travel-supplies": "supplies",
        "travel-notes": "notes",
        "region-map": "map",
        "souvenir-camera": "camera",
        "encounter-compass": "compass",
        "sorting-box": "box",
        "入场彩纸": "confetti",
        "音符": "voice",
        "★": "star",
        "♪": "voice",
        "✿": "flower",
        "◎": "record",
        "◇": "wings",
        "◈": "mask",
        "∞": "infinity",
        "↗": "guitar",
        "❖": "rose",
        "☺": "pig",
        "≋": "dj",
        "⋯": "notes",
        "中心": "star",
        "客串": "pig",
        "舞台器材": "stage-parts",
        "主旋律": "voice",
        "节奏": "drums",
        "伴奏": "guitar",
        "巨物饲料": "corn",
        "增肥猪粮": "feed-fat",
        "瘦身猪粮": "feed-lean",
        "赏金猪铃": "bounty",
        "丰收猪铃": "bounty",
        "保底锅盖": "lid",
        "升星灶芯": "stove",
        "防糊围裙": "apron",
        "减肥饲料": "feed-lean",
        "增膘豆饼": "feed-fat",
        "精瘦青饲料": "feed-lean",
        "猪币悬赏牌": "bounty",
        "精准刀工券": "knife",
        "慢炖调料包": "pot",
        "稳火保底锅盖": "lid",
        "丰收围裙": "apron",
        "fattening-bean-cake": "feed-fat",
        "lean-green-feed": "feed-lean",
        "coin-bounty-tag": "bounty",
        "precision-knife": "knife",
        "slow-cook-seasoning": "pot",
        "large-lunch-box": "lunchbox",
        "harvest-apron": "apron",
        "后勤": "supplies",
        "搜寻": "radar",
        "搬运": "box",
        "采掘": "ore",
        "机械": "parts",
        "研究": "notes",
        "灵巧": "fiber",
        "探路": "compass",
        "音乐器材": "stage-parts",
        "交涉": "voice",
        "露水便签": "notes",
        "四叶草书签": "flower",
        "晨雾明信片": "map",
        "小径邮戳": "postmark",
        "回声石片": "ore",
        "矿灯票根": "ticket",
        "银纹拓片": "ore",
        "矿洞邮戳": "postmark",
        "黄铜齿轮挂件": "parts",
        "旧图纸残页": "map",
        "小扳手徽记": "knife",
        "工坊邮戳": "postmark",
        "风铃叶签": "flower",
        "林间风笛": "voice",
        "月光纤维结": "fiber",
        "林地邮戳": "postmark",
        "退役拨片": "guitar",
        "老舞台票根": "ticket",
        "霓虹电路片": "stage-parts",
        "舞台邮戳": "postmark",
    }
)


def _icon_key(value: object) -> str:
    text = str(value).strip()
    if text in _SHAPES:
        return text
    if text in _ALIASES:
        return _ALIASES[text]
    # Known display names may be prefixed with a category or followed by a count.
    for name in sorted(_ALIASES, key=len, reverse=True):
        if len(name) >= 3 and name in text:
            return _ALIASES[name]
    if "邮戳" in text or "纪念" in text:
        return "postmark"
    if "券" in text or "档期" in text:
        return "ticket"
    if "币" in text or "余额" in text:
        return "coin"
    return "receipt"


@lru_cache(maxsize=96)
def _icon_svg(key: str) -> Markup:
    index = tuple(_SHAPES).index(key)
    accent = {
        "blue": "#739fc9",
        "red": "#d18b9b",
        "purple": "#a286c8",
        "domain": "#b2819f",
        "coin": "#c0a15c",
    }.get(key, _COLOURS[index % len(_COLOURS)])
    return Markup(
        f'<svg class="feature-icon" viewBox="0 0 96 96" role="img" aria-label="{escape(_NAMES[key])}" xmlns="http://www.w3.org/2000/svg">'
        f'<circle cx="48" cy="48" r="45" fill="#fffafc" stroke="{accent}" stroke-width="1.5"/>'
        f'<circle cx="48" cy="48" r="39" fill="{accent}" opacity=".14"/>'
        f'<g fill="none" stroke="{accent}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">{_SHAPES[key]}</g></svg>'
    )


def feature_icon(value: object) -> Markup:
    """Unknown labels get a neutral receipt, never interpreted as SVG or a path."""
    return _icon_svg(_icon_key(value))


_SCENES = {
    "grassland": (
        "青草近郊",
        "#79b798",
        '<path d="M0 104Q60 45 140 103T320 78V140H0Z" fill="#b9dcc5"/><path d="M150 140q-45-29 15-55" stroke="#fff5d8" stroke-width="18" fill="none"/><g fill="#73ad8b"><path d="M64 95V51m-1 21C40 77 34 59 37 49c16-2 25 6 26 23m2-10c21 6 30-10 24-22-16 0-24 10-24 22"/></g>',
    ),
    "echo-mine": (
        "回声矿洞",
        "#7c98b4",
        '<path d="M20 140 39 65 99 34 151 50 192 22 259 47 300 140Z" fill="#c2d0dc"/><path d="M116 140V95c0-45 80-45 80 0v45" fill="#64728d"/><path d="m54 123 14-24 24 17-9 24m138-20 17-33 30 43" fill="#a8dace"/><path d="M126 68h63m-58-8v76m53-76v76" fill="none" stroke="#b49c83" stroke-width="9"/>',
    ),
    "old-workshop": (
        "废旧工坊",
        "#b19b75",
        '<path d="M36 140V51l55-26 24 26h48V29h45v28h66v83Z" fill="#dec6aa"/><path d="M58 69h33v25H58Zm112 9h37v27h-37Zm-54 39h33v23h-33Z" fill="#fff5d7"/><g transform="translate(195,70) scale(.55)"><circle cx="70" cy="70" r="37" fill="none" stroke="#ad987f" stroke-width="15"/><circle cx="70" cy="70" r="12" fill="#fff3d5"/></g>',
    ),
    "windbell-forest": (
        "风铃林地",
        "#8fae94",
        '<path d="M33 140V31m67 109V18m139 122V31m48 109V20" stroke="#b09c81" stroke-width="13"/><g fill="#a8cbb6"><circle cx="30" cy="29" r="28"/><circle cx="98" cy="17" r="38"/><circle cx="238" cy="23" r="37"/><circle cx="290" cy="20" r="31"/></g><path d="M167 10v28m-13 30a14 14 0 0 1 28 0Zm13 0v31l-8 10" fill="#d1e1d4" stroke="#809e94" stroke-width="3"/>',
    ),
    "old-stage-warehouse": (
        "旧舞台仓库",
        "#b295c3",
        '<path d="M28 140V55L156 15l133 40v85" fill="#dcc9db"/><path d="M61 140V66h187v74" fill="#7d718b"/><path d="m82 66 27 69H60Zm145 0-27 69h53Z" fill="#f9e8bc" opacity=".7"/><path d="M105 126h45v14h-45Zm76-15h28v29h-28ZM32 52h258" stroke="#9c82aa" stroke-width="5" fill="#baa9c4"/>',
    ),
    "street": (
        "街头舞台",
        "#d8a190",
        '<path d="M10 128V42h65v86m-50-60h13m16 0h13M242 128V22h63v106m-48-78h13m12 0h13" fill="#f0dbcf" stroke="#d6b29d" stroke-width="4"/><path d="M95 130v-12h137v12Zm59-13V52m-7 1h14" stroke="#a88593" stroke-width="5" fill="#e4b5c5"/>',
    ),
    "campus": (
        "校园祭",
        "#d1aa62",
        '<path d="M38 130V69l50-29h144l50 29v61" fill="#f0d9b3"/><path d="M95 130V72h130v58" fill="#ce94ad"/><path d="M20 23Q150 55 300 22" fill="none" stroke="#aa8d8e" stroke-width="2"/><path d="m41 27 12 21 10-16m35 6 12 22 12-20m36 0 12 22 10-22m41-3 11 20 10-24" fill="#dd97b3"/>',
    ),
    "livehouse": (
        "Livehouse",
        "#a28bbd",
        '<rect x="31" y="17" width="259" height="119" rx="6" fill="#8d7d9e"/><path d="M55 133V41h214v92" fill="#ede0f0"/><path d="m78 43 25 87H59Zm165 0-26 87h42Z" fill="#b2d9e2"/><rect x="143" y="82" width="34" height="44" fill="#9c86ac"/><circle cx="70" cy="118" r="11" fill="#b492c0"/><circle cx="250" cy="118" r="11" fill="#b492c0"/>',
    ),
    "theatre": (
        "城市剧场",
        "#c88ba4",
        '<path d="M29 139V25h263v114" fill="#d3adc0"/><path d="M64 139V34h194v105" fill="#fae9dc"/><path d="M29 25h88c-7 58-30 75-88 81m263-81h-86c7 58 31 75 86 81" fill="#ae7897"/><path d="M57 125h205m-217 9h229" stroke="#b58e9f" stroke-width="6"/>',
    ),
    "dome": (
        "梦想巨蛋",
        "#8aa5cb",
        '<path d="M22 107C23 6 287 5 296 107Z" fill="#c4d5e6"/><path d="M69 107C64 6 249 5 249 107M117 107c-4-90 84-88 83 0M22 107h274" fill="none" stroke="#f5f3ff" stroke-width="6"/><path d="M22 107v28h274v-28Z" fill="#adb9d6"/><path d="m57 120 5-12m22 15 5-12m38 11 4-13m29 14 4-13m31 12 5-12m26 15 4-12m26 12 4-13" stroke="#fff5c5" stroke-width="4"/>',
    ),
}


def _scene_key(value: object) -> str:
    text = str(value)
    if text in _SCENES:
        return text
    return next((key for key, (name, *_rest) in _SCENES.items() if name in text), "")


@lru_cache(maxsize=10)
def _scene_svg(key: str) -> Markup:
    title, colour, shape = _SCENES[key]
    return Markup(
        '<svg class="feature-scene" viewBox="0 0 320 140" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{escape(title)}原创路线插画"><rect width="320" height="140" rx="8" fill="#fffaf8"/>'
        f'<circle cx="265" cy="26" r="18" fill="{colour}" opacity=".22"/>'
        f'<path d="M12 126h296" stroke="{colour}" stroke-width="2" opacity=".4"/>{shape}</svg>'
    )


def feature_scene(value: object) -> Markup:
    key = _scene_key(value)
    return _scene_svg(key) if key else Markup("")


@lru_cache(maxsize=6)
def _backdrop(key: str, modified_ns: int, file_size: int) -> str:
    """At most six 960×360 WebP derivatives; no full-resolution inline mother art."""
    del modified_ns, file_size
    with Image.open(_ASSETS / f"{key}.png") as source:
        if source.width * source.height > 16_000_000:
            raise ValueError("功能卡背景母版超过1600万像素上限")
        source.thumbnail((960, 360), Image.Resampling.LANCZOS)
        with source.convert("RGB") as derivative, BytesIO() as output:
            derivative.save(output, format="WEBP", quality=80, method=4)
            data = output.getvalue()
    if len(data) > 256 * 1024:
        raise ValueError("功能卡背景派生超过256KiB上限")
    return "data:image/webp;base64," + b64encode(data).decode("ascii")


def feature_backdrop(family: object) -> str:
    key = str(family)
    if key not in _FAMILIES:
        return ""
    path = _ASSETS / f"{key}.png"
    try:
        if not path.is_file():
            return ""
        stat = path.stat()
        return _backdrop(key, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, Image.DecompressionBombError):
        # Decoration is optional; corrupt art must not hide a committed receipt.
        return ""


def clear_feature_art_cache() -> None:
    """Release bounded local artwork caches on plugin unload or explicit refresh."""
    _backdrop.cache_clear()
    _icon_svg.cache_clear()
    _scene_svg.cache_clear()


def feature_wheel(segments: Iterable[Any], selected_index: int | None = None) -> Markup:
    """Draw a static proportional wheel. Only an explicit persisted result gets a marker.

    Inputs are finite positive domain draw weights, not victory-weight magnitudes.
    Labels are escaped. No secrets, seeds, animation timers or random sources exist here.
    """
    values = list(islice(segments, 17))
    if not 1 <= len(values) <= 16:
        raise ValueError("可视轮盘需1至16个选项")
    normalized = []
    for item in values:
        if isinstance(item, Mapping):
            label, weight = str(item["label"]), float(item["weight"])
        else:
            label, weight = str(item.label), float(item.weight)
        if not math.isfinite(weight) or not 0 < weight <= 1_000_000:
            raise ValueError("可视轮盘权重必须是有限正数且不超过一百万")
        if len(label) > 100:
            raise ValueError("可视轮盘标签超过100字")
        normalized.append((label, weight))
    if selected_index is not None and (
        isinstance(selected_index, bool) or not isinstance(selected_index, int) or not 0 <= selected_index < len(values)
    ):
        raise ValueError("可视轮盘选中位置不合法")
    total = sum(weight for _, weight in normalized)
    angle = -math.pi / 2
    paths, markers = [], []
    for index, (_label, weight) in enumerate(normalized):
        end = angle + 2 * math.pi * weight / total
        sx, sy = 120 + 95 * math.cos(angle), 120 + 95 * math.sin(angle)
        ex, ey = 120 + 95 * math.cos(end), 120 + 95 * math.sin(end)
        colour = _COLOURS[index % len(_COLOURS)]
        if len(values) == 1:
            paths.append(f'<circle cx="120" cy="120" r="95" fill="{colour}"/>')
        else:
            paths.append(
                f'<path d="M120 120L{sx:.3f} {sy:.3f}A95 95 0 {int(end - angle > math.pi)} 1 {ex:.3f} {ey:.3f}Z" fill="{colour}" stroke="#fff" stroke-width="2"/>'
            )
        middle = (angle + end) / 2
        tx, ty = 120 + 66 * math.cos(middle), 120 + 66 * math.sin(middle)
        paths.append(
            f'<text x="{tx:.2f}" y="{ty:.2f}" text-anchor="middle" dominant-baseline="middle" font-size="17" font-weight="700" fill="#3e3343">{index + 1}</text>'
        )
        if selected_index == index:
            px, py = 120 + 105 * math.cos(middle), 120 + 105 * math.sin(middle)
            markers.append(
                f'<circle cx="{px:.2f}" cy="{py:.2f}" r="8" fill="#ad386e" stroke="white" stroke-width="3"/>'
            )
        angle = end
    label = "轮盘权重图" if selected_index is None else "已抽中：" + normalized[selected_index][0]
    center = "规则" if selected_index is None else str(selected_index + 1)
    return Markup(
        f'<svg class="feature-wheel" viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{escape(label)}">'
        '<circle cx="120" cy="120" r="110" fill="#fffafb" stroke="#e4cdda" stroke-width="2"/>'
        + "".join(paths + markers)
        + f'<circle cx="120" cy="120" r="29" fill="white" stroke="#e6ccd9" stroke-width="2"/><text x="120" y="122" dominant-baseline="middle" text-anchor="middle" font-size="18" font-weight="700" fill="#985777">{center}</text></svg>'
    )
