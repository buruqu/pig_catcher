"""版本化巡演目录；新增角色/歌曲/合奏只扩展定义，不在命令里判断猪名。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .errors import PigCatcherError

TOUR_VERSION = 1
SCORE_CAPS = {"ability": 35, "synergy": 25, "setlist": 25, "stage": 10, "equipment": 5}
SCORE_NAMES = {
    "ability": "演出与职能",
    "synergy": "默契与熟练",
    "setlist": "曲序与观众",
    "stage": "主题与高光",
    "equipment": "舞台器材",
}
INSTRUMENTS = {
    "voice": "主唱",
    "guitar": "吉他",
    "bass": "贝斯",
    "drums": "鼓",
    "keyboard": "键盘",
    "violin": "小提琴",
    "dj": "DJ",
}
FUNCTIONS = {
    "voice": ("主旋律",),
    "guitar": ("主旋律", "伴奏"),
    "bass": ("节奏", "伴奏"),
    "drums": ("节奏",),
    "keyboard": ("主旋律", "伴奏"),
    "violin": ("主旋律", "伴奏"),
    "dj": ("节奏", "伴奏"),
}
BRANCHES = {"亲近": "亲近", "技术": "技术", "叙事": "叙事"}
COLORS = {
    "粉": "#cf678f",
    "红": "#c85763",
    "橙": "#c68042",
    "黄": "#ad943b",
    "绿": "#4a9272",
    "蓝": "#5a82b4",
    "紫": "#8b64a9",
    "黑": "#635465",
    "白": "#9b8d98",
}
EMBLEMS = {"星星": "★", "音符": "♪", "花瓣": "✿", "唱片": "◎", "翅膀": "◇", "假面": "◈"}
EQUIPMENT_COSTS = (
    {"stage-components": 20, "machine-parts": 10, "agility-fiber": 5, "coins": 100},
    {"stage-components": 40, "machine-parts": 20, "agility-fiber": 10, "coins": 200},
    {"stage-components": 80, "machine-parts": 30, "agility-fiber": 20, "coins": 400},
    {"stage-components": 140, "machine-parts": 40, "agility-fiber": 30, "coins": 800},
    {"stage-components": 220, "machine-parts": 60, "agility-fiber": 40, "coins": 1400},
)
PRACTICE_COST = {"stage-components": 5, "travel-supplies": 3, "coins": 50}
BRANCH_COST = {"stage-components": 3, "travel-supplies": 2, "coins": 20}
REWARDS = {"C": (40, 200), "B": (60, 260), "A": (80, 340), "S": (100, 420), "SS": (120, 500)}


class TourError(PigCatcherError):
    """可直接向玩家展示的巡演验证错误。"""


@dataclass(frozen=True, slots=True)
class Signature:
    name: str
    effect: str
    component: str
    condition: str
    summary: str


@dataclass(frozen=True, slots=True)
class Character:
    template_id: str
    identity: str
    name: str
    character: str
    band: str
    instruments: tuple[str, ...]
    signature: Signature

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(role for instrument in self.instruments for role in FUNCTIONS[instrument])


@dataclass(frozen=True, slots=True)
class Theme:
    theme_id: str
    band_name: str
    name: str
    tags: tuple[str, ...]
    color: str
    story: str


THEMES = (
    Theme(
        "poppin",
        "Poppin'Party",
        "星星落进练习室",
        ("青春", "亲近", "互动"),
        "粉",
        "练习室的小灯一路亮到谢幕，星星不只属于原来的五个人。",
    ),
    Theme(
        "afterglow",
        "Afterglow",
        "商店街巡回日记",
        ("热烈", "亲近", "互动"),
        "红",
        "从街角到远方，把熟悉的招呼编进新的节奏里。",
    ),
    Theme(
        "pastel",
        "Pastel＊Palettes",
        "偶像成长公演",
        ("偶像", "青春", "互动"),
        "绿",
        "紧张没有消失，但这一次，所有人一起把谢幕完成了。",
    ),
    Theme(
        "roselia",
        "Roselia",
        "蔷薇之夜",
        ("技术", "幻想", "热烈"),
        "紫",
        "灯光沿着清晰的拍点展开，严谨与热爱在同一朵花里。",
    ),
    Theme(
        "hhw",
        "Hello, Happy World!",
        "把快乐带到每一站",
        ("互动", "亲近", "偶像"),
        "黄",
        "舞台边缘也有位置，今天的笑脸全部写进票根。",
    ),
    Theme(
        "morfonica",
        "Morfonica",
        "蝶翼幻想巡演",
        ("幻想", "叙事", "技术"),
        "蓝",
        "未说出口的意象化作弦音，城市像一张等待着色的谱纸。",
    ),
    Theme(
        "ras",
        "RAISE A SUILEN",
        "重混实验现场",
        ("电子", "技术", "热烈"),
        "绿",
        "把不同的声部重新排列，这一版只属于此刻的乐队。",
    ),
    Theme(
        "mygo",
        "MyGO!!!!!",
        "还没有说完的歌",
        ("叙事", "青春", "亲近"),
        "蓝",
        "唱到终曲时才发现，那句没说完的话已经被大家接住。",
    ),
    Theme(
        "mujica",
        "Ave Mujica",
        "整场演出就是一幕剧",
        ("叙事", "幻想", "偶像"),
        "红",
        "三次开幕，三次呼应。面具之后仍是一起站上台的伙伴。",
    ),
)
THEMES_BY_ID = {theme.theme_id: theme for theme in THEMES}
# 原创 UI 符号，不复制官方团标；各主题解锁后有独立的卡面识别。
THEME_EMBLEMS = {
    "poppin": "★",
    "afterglow": "↗",
    "pastel": "✿",
    "roselia": "❖",
    "hhw": "☺",
    "morfonica": "◇",
    "ras": "≋",
    "mygo": "⋯",
    "mujica": "◈",
}


@dataclass(frozen=True, slots=True)
class Song:
    song_id: str
    name: str
    theme_id: str
    tags: tuple[str, ...]
    energy: int
    motif: str
    summary: str


_SONG_NAMES = (
    ("星屑起跑线", "练习室的下午", "把星光带回家"),
    ("街角第一声", "日落鼓点", "下一条街再见"),
    ("彩纸开场白", "镜头外的努力", "属于我们的安可"),
    ("蔷薇序幕", "逐拍向前", "夜色中的誓音"),
    ("笑脸入场券", "全场一起拍手", "把快乐唱到最后"),
    ("蝶翼未展开", "玻璃城的回声", "幻想照进黎明"),
    ("低频点火", "重混实验室", "最后一拍不熄灭"),
    ("未寄出的短句", "迷路也要向前", "把余音留给你"),
    ("第一幕的烛光", "假面之间", "终幕之后的天空"),
)
SONGS = tuple(
    Song(
        f"{theme.theme_id}-{position}",
        name,
        theme.theme_id,
        (theme.tags[0], theme.tags[position - 1]),
        position,
        theme.theme_id,
        ("轻柔建立动机", "发展声部与观众呼应", "高能量终曲，回应开场")[position - 1],
    )
    for theme, names in zip(THEMES, _SONG_NAMES, strict=True)
    for position, name in enumerate(names, 1)
)
SONGS_BY_ID = {song.song_id: song for song in SONGS}


@dataclass(frozen=True, slots=True)
class Venue:
    venue_id: str
    name: str
    fans: int
    audience: str
    tags: tuple[str, ...]


VENUES = (
    Venue("street", "街头舞台", 0, "停下脚步的街角听众", ("亲近", "青春", "互动")),
    Venue("campus", "校园祭", 200, "期待全场呼应的同学", ("热烈", "青春", "偶像")),
    Venue("livehouse", "Livehouse", 1000, "在意声部细节的现场乐迷", ("技术", "电子", "热烈")),
    Venue("theatre", "城市剧场", 3000, "沿着故事走进演出的观众", ("叙事", "幻想", "偶像")),
    Venue(
        "dome",
        "梦想巨蛋",
        8000,
        "愿意跟随任何完整编排的灯海",
        ("亲近", "青春", "互动", "热烈", "偶像", "技术", "电子", "叙事", "幻想"),
    ),
)
VENUES_BY_ID = {venue.venue_id: venue for venue in VENUES}


@dataclass(frozen=True, slots=True)
class Ensemble:
    ensemble_id: str
    name: str
    identities: tuple[str, ...]
    band: str = ""
    kind: str = "关系主题·原创合奏"
    component: str = "synergy"
    story: str = "两种声部互相留出空间，合奏终于有了完整的呼吸。"


ENSEMBLES = (
    Ensemble(
        "free", "自由合奏", (), kind="原创职能组合", component="stage", story="不论来自哪支乐队，三种职能都接得住彼此。"
    ),
    Ensemble(
        "twins",
        "双子的对拍",
        ("hina", "sayo"),
        component="ability",
        story="天赋的灵光遇上逐拍的认真，两把吉他找到相同落点。",
    ),
    Ensemble(
        "double-drums",
        "两套鼓，一次接力",
        ("tomoe", "ako"),
        component="ability",
        story="一套鼓稳住街角，另一套展开黑暗之翼，接力恰好落在同一拍。",
    ),
    Ensemble("childhood", "小时候的旋律", ("tae", "layer"), story="旧日的旋律换了一种低音，这一站又听见熟悉的回答。"),
    Ensemble(
        "pareo-pastel", "终于同台了", ("pareo",), "pastel", story="键盘为憧憬的颜色留下一段旋律，今天不再只在台下应援。"
    ),
    Ensemble(
        "lock-poppin",
        "憧憬就在身边",
        ("lock",),
        "poppin",
        component="ability",
        story="练习簿上圈出的难点，终于在憧憬的伙伴身边响起来。",
    ),
    Ensemble(
        "spotlight",
        "聚光灯两侧",
        ("kaoru", "chisato"),
        component="stage",
        story="幕间的独白与镜头前的克制，共同照亮舞台的两侧。",
    ),
    Ensemble(
        "combo",
        "连击不要断",
        ("ako", "rinko"),
        component="setlist",
        story="鼓点与键盘一拍接一拍，连击在终曲前没有断开。",
    ),
    Ensemble("cafe", "咖啡店特别营业", ("eve", "tsugumi"), story="礼貌的开场问候，配上一份细心准备的特别营业单。"),
    Ensemble("after-work", "下班以后去演出", ("moca", "lisa"), story="忙完今天的工作，两位伙伴把轻松的笑意带到舞台。"),
    Ensemble(
        "guide",
        "有人陪着就不怕走错",
        ("kanon", "chisato"),
        component="stage",
        story="拐角之后不是迷路，而是有人陪着一起发现的小舞台。",
    ),
    Ensemble(
        "promotion",
        "宣传三人组",
        ("anon", "toko", "nyamu"),
        kind="原创职能组合",
        component="setlist",
        story="宣传、服装和镜头各有主张，最后共同写下这一站的封面。",
    ),
    Ensemble(
        "backstage",
        "幕后万事屋",
        ("maya", "misaki", "umiri"),
        kind="原创职能组合",
        component="equipment",
        story="线路、调度和支援低音全部就位，幕后工作也值得一个高光。",
    ),
    Ensemble(
        "arrangement",
        "从意象到完整歌曲",
        ("mashiro", "rui", "taki"),
        kind="原创职能组合",
        component="ability",
        story="词里的画面、弦乐的线条与鼓点的骨架，拼成同一首歌。",
    ),
    Ensemble(
        "cats",
        "猫猫返场",
        ("yukina", "rana"),
        kind="原创趣味主题",
        component="stage",
        story="认真演唱与随性的Solo，在安可时意外地合拍。",
    ),
    Ensemble(
        "first-room",
        "回到最初的练习室",
        ("tomori", "soyo", "taki", "sakiko", "mutsumi"),
        kind="可选故事主题",
        component="synergy",
        story="新的终曲没有抹去旧日的练习室，这次每个人都听见了彼此。",
    ),
)
ENSEMBLES_BY_ID = {ensemble.ensemble_id: ensemble for ensemble in ENSEMBLES}


@dataclass(frozen=True, slots=True)
class TourTool:
    tool_id: str
    name: str
    costs: tuple[tuple[str, int], ...]
    summary: str


TOOLS = (
    TourTool(
        "cable",
        "备用线缆",
        (("machine-parts", 2), ("travel-supplies", 1)),
        "抵消本站设备类负面现场波动；不改变其他分项",
    ),
    TourTool("cue", "提示卡", (("agility-fiber", 2), ("travel-supplies", 1)), "本站曲序与观众分项+2，不超过25分"),
    TourTool(
        "recorder",
        "留声机",
        (("stage-components", 2), ("travel-supplies", 1)),
        "额外保存一位非高光成员的照片；不增加猪币或粉丝",
    ),
    TourTool("confetti", "礼花", (("travel-supplies", 2), ("agility-fiber", 1)), "原创礼花谢幕视觉，不增加评分"),
)
TOOLS_BY_ID = {tool.tool_id: tool for tool in TOOLS}


def _load_characters() -> dict[str, Character]:
    data = json.loads((Path(__file__).parent / "data/tour_characters.json").read_text(encoding="utf-8"))
    if data["version"] != TOUR_VERSION:
        raise ValueError("巡演角色定义版本不匹配")
    result = {}
    for row in data["characters"]:
        char = Character(
            row["template"],
            row["identity"],
            row["name"],
            row["character"],
            row["band"],
            tuple(row["instruments"]),
            Signature(*row["signature"]),
        )
        if char.template_id in result or char.band not in THEMES_BY_ID or not char.roles:
            raise ValueError("巡演角色目录有重复或未知职能")
        result[char.template_id] = char
    return result


CHARACTERS = _load_characters()
MAIN_FORMS = tuple(CHARACTERS)
# 只登记已经确认的定制形态；不按名称包含“彩/祥”之类关键词猜身份。
for _prefix in (
    "pig-g1092931381",
    "pig-g237716658",
    "pig-qo5e5854406d0297d6feae696a13e3a339",
    "pig-qo9ea2810f378fbd7dc3219c56ceab3520",
):
    for _suffix, _source, _name in (
        ("aya-repair", "pig-bandori-aya-idol", "彩彩修车猪"),
        ("soft-sakiko", "pig-bandori-avemujica-sakiko", "软糯丰川祥猪"),
    ):
        _key = f"{_prefix}-{_suffix}"
        CHARACTERS[_key] = replace(CHARACTERS[_source], template_id=_key, name=_name)
GUESTS = {
    "pig-bandori-viola-green-tea": (
        "viola",
        "绿茶猪",
        "薇欧拉",
        "小小幸福的花束",
        "只作舞台客串，不提供器乐职能、评分或经济加成",
    )
}


def resolve_definition(value: str, definitions: dict, *, label: str) -> str:
    normalized = str(value).strip().casefold()
    for key, definition in definitions.items():
        if normalized in {
            key.casefold(),
            definition.name.casefold(),
            str(getattr(definition, "band_name", "")).casefold(),
        }:
            return key
    raise TourError(f"没有这个{label}：{value}。请先查看目录。")


def training_level(experience: int) -> int:
    return sum(experience >= 20 * level * (level + 1) for level in range(1, 11))


def grade(score: float) -> str:
    for threshold, result in ((92, "SS"), (80, "S"), (65, "A"), (50, "B")):
        if score >= threshold:
            return result
    return "C"


def default_plan(theme_id: str = "poppin") -> dict:
    return {
        "theme": theme_id,
        "venue": "street",
        "songs": [f"{theme_id}-{i}" for i in (1, 2, 3)],
        "highlights": [],
        "ensemble": "auto",
        "tool": "",
        "guest": "",
    }
