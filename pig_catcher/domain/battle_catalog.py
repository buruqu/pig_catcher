"""对战版本化定义。抽中招式的权重与招式增加的胜利权重是两套数值。"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import PigCatcherError
from .special_content import GOJO_PIG_TEMPLATE_ID, SUKUNA_PIG_TEMPLATE_ID

BATTLE_VERSION = 1
INVITE_TTL_MS = 5 * 60 * 1000
ACTION_TTL_MS = 10 * 60 * 1000
CONFIRM_TTL_MS = 2 * 60 * 1000
INVITE_COOLDOWN_MS = 60 * 1000
# 只是单个数据库事务的工作分片，不是连抽或核心次数的上限。
MOVE_CHUNK_SIZE = 32
COUNT_WHEEL = ((1, 5), (2, 4), (3, 3), (4, 2), (5, 1))
HEAVY_COUNT_WHEEL = COUNT_WHEEL[:-1]
INJURY_WHEELS = (
    (("light", 6), ("heavy", 2), ("exhausted", 1), ("core", 1)),
    (("light", 2), ("heavy", 6), ("exhausted", 1), ("core", 1)),
    (("light", 1), ("heavy", 2), ("exhausted", 6), ("core", 1)),
)
INJURY_NAMES = {"light": "轻伤", "heavy": "重伤", "exhausted": "力竭倒下", "core": "我掌握了抓猪的核心！"}
LOOT_WEIGHTS = (5, 10, 10, 25, 30, 20)
UPGRADE_COSTS = (
    {"ore": 60, "parts": 20, "fiber": 20, "supplies": 20, "coins": 300},
    {"ore": 150, "parts": 50, "fiber": 50, "supplies": 50, "coins": 800},
    {"ore": 300, "parts": 100, "fiber": 100, "supplies": 100, "coins": 2000},
    {"ore": 540, "parts": 180, "fiber": 180, "supplies": 180, "coins": 4500},
    {"ore": 900, "parts": 300, "fiber": 300, "supplies": 300, "coins": 9000},
)


class BattleError(PigCatcherError):
    """可直接向玩家解释的对战操作拒绝。"""


@dataclass(frozen=True, slots=True)
class Move:
    move_id: str
    name: str
    gain: int = 0
    draws: int = 0
    loan: bool = False
    draw_weight: int = 1


@dataclass(frozen=True, slots=True)
class FighterDefinition:
    fighter_id: str
    template_id: str
    name: str
    moves: tuple[Move, ...]


FIGHTERS = (
    FighterDefinition(
        "sukuna",
        SUKUNA_PIG_TEMPLATE_ID,
        "宿傩猪",
        (
            Move("black-flash", "黑闪！", draws=2),
            Move("dismantle", "解", 10),
            Move("cleave", "捌", 15),
            Move("furnace", "灶·开", 21),
            Move("shrine", "领域展开·伏魔御厨子！", 35),
            Move("loan", "束缚·贷款", draws=1, loan=True),
            Move("reverse", "反转·修复", 14),
            Move("elbow", "肘击", 7),
            Move("net", "网格斩", 12),
        ),
    ),
    FighterDefinition(
        "gojo",
        GOJO_PIG_TEMPLATE_ID,
        "五条猪",
        (
            Move("blue", "术式顺转·苍！", 13),
            Move("red", "术式反转·赫", 20),
            Move("blue-fist", "肘击·苍拳！", 14),
            Move("defense", "无下限·防御", 10),
            Move("black-flash", "黑闪！", draws=2),
            Move("teleport", "无下限·瞬移", 14),
            Move("purple", "虚式·茈", 24),
            Move("void", "领域展开·无量空处！", 30),
            Move("reverse", "反转·修复", 14),
            Move("unlimited-purple", "无限制·茈！", 35),
        ),
    ),
)
FIGHTERS_BY_ID = {item.fighter_id: item for item in FIGHTERS}
FIGHTERS_BY_TEMPLATE = {item.template_id: item for item in FIGHTERS}


@dataclass(frozen=True, slots=True)
class BattleTool:
    tool_id: str
    name: str
    description: str
    costs: tuple[tuple[str, int], ...]


# 仅使用派遣共用材料制作；不加入永久概率、不改变固定转盘、不使用成就专属券。
TOOLS = (
    BattleTool(
        "wristband",
        "练习护腕",
        "本场首次数值招式额外+2胜利权重，不参与贷款翻倍。",
        (("ore", 4), ("fiber", 2), ("supplies", 2)),
    ),
    BattleTool(
        "bandage",
        "应急绷带",
        "本场首次重伤数值招式免除该招-1；不治疗伤势或降低风险。",
        (("fiber", 4), ("parts", 2), ("supplies", 3)),
    ),
    BattleTool("confetti", "入场彩纸", "入场时展示彩纸庆典，不改变战斗数值。", (("fiber", 2), ("supplies", 1))),
)
TOOLS_BY_ID = {item.tool_id: item for item in TOOLS}
MATERIAL_IDS = {
    "ore": "training-ore",
    "parts": "machine-parts",
    "fiber": "agility-fiber",
    "supplies": "travel-supplies",
    "coins": "coins",
}


def tool_id(value: str) -> str:
    for item in TOOLS:
        if value in {item.tool_id, item.name}:
            return item.tool_id
    raise BattleError("没有这种对战器具，请使用 /战斗猪 器具 查看。")
