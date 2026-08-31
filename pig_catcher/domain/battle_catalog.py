"""对战版本化定义。抽中招式的权重与招式增加的胜利权重是两套数值。"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import PigCatcherError
from .special_content import GOJO_PIG_TEMPLATE_ID, SUKUNA_PIG_TEMPLATE_ID

# 对战规则版本与活动成就事实版本分离：新版对战会改变随机命名空间，
# 但新增字段仍是 activity_progress v1 可以向后兼容读取的事实载荷。
BATTLE_RULE_VERSION = 4
BATTLE_FACT_VERSION = 1
BATTLE_VERSION = BATTLE_RULE_VERSION
INVITE_TTL_MS = 5 * 60 * 1000
ACTION_TTL_MS = 10 * 60 * 1000
CONFIRM_TTL_MS = 2 * 60 * 1000
INVITE_COOLDOWN_MS = 60 * 1000
# 只是单个数据库事务的工作分片，不是连抽或核心次数的上限。
MOVE_CHUNK_SIZE = 32
COUNT_WHEEL = ((1, 5), (2, 4), (3, 3), (4, 2), (5, 1))
HEAVY_COUNT_WHEEL = COUNT_WHEEL[:-1]
INJURY_WEIGHT_SCALE = 2
# 以二倍整数保存半点权重，抽签全程不使用浮点数。
INJURY_WHEELS = (
    (("light", 13), ("heavy", 5), ("exhausted", 1), ("core", 1)),
    (("light", 5), ("heavy", 12), ("exhausted", 2), ("core", 1)),
    (("light", 2), ("heavy", 5), ("exhausted", 12), ("core", 1)),
)
INJURY_NAMES = {"light": "轻伤", "heavy": "重伤", "exhausted": "力竭倒下", "core": "我掌握了抓猪的核心！"}
MOVE_WEIGHT_SCALE = 10
LEGACY_LOOT_WEIGHTS = (5, 10, 10, 25, 30, 20)
LOOT_WEIGHTS = (5, 10, 10, 30, 30, 15)
LEGACY_LOOT_ATTEMPTS = 5
LOOT_ATTEMPTS = 3
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
    tags: tuple[str, ...] = ()
    direction: str = "self"
    description: str = ""


@dataclass(frozen=True, slots=True)
class FighterForm:
    form_id: str
    name: str
    moves: tuple[Move, ...]


@dataclass(frozen=True, slots=True)
class FighterDefinition:
    fighter_id: str
    template_id: str
    name: str
    moves: tuple[Move, ...]
    template_aliases: tuple[str, ...] = ()
    forms: tuple[FighterForm, ...] = ()


@dataclass(frozen=True, slots=True)
class JuejueAccelerationTier:
    tier: int
    success_chance: int
    gain: int
    extra_draws: int
    failure_debt: int


@dataclass(frozen=True, slots=True)
class JuejueDelayTier:
    tier: int
    success_chance: int
    gain: int
    opponent_reduction: int
    opponent_debt: int
    failure_opponent_bonus: int


JUEJUE_PIG_TEMPLATE_IDS = (
    "pig-g1092931381-juejue",
    "pig-g237716658-juejue",
    "pig-qo5e5854406d0297d6feae696a13e3a339-juejue",
    "pig-qo9ea2810f378fbd7dc3219c56ceab3520-juejue",
)
JUEJUE_FORM_TIME = "time-sand"
JUEJUE_FORM_VIRTUAL = "virtual-sound"
JUEJUE_ACCELERATION_TIERS = (
    JuejueAccelerationTier(1, 100, 15, 1, 0),
    JuejueAccelerationTier(2, 75, 20, 2, 2),
    JuejueAccelerationTier(3, 50, 25, 3, 3),
)
JUEJUE_DELAY_TIERS = (
    JuejueDelayTier(1, 100, 15, 5, 0, 0),
    JuejueDelayTier(2, 75, 15, 10, 1, 1),
    JuejueDelayTier(3, 50, 15, 15, 2, 2),
)
JUEJUE_TIME_MOVES = (
    Move(
        "sand-sculpt",
        "时之沙·塑型",
        5,
        tags=("juejue-sculpt",),
        description="荒时之沙出现权重+0.1；下一次加速或时延成功率+5个百分点，累计最多20。",
    ),
    Move(
        "sand-rewind",
        "时之沙·回溯",
        10,
        tags=("juejue-rewind",),
        description="挂起本回合一次回溯；若本回合失败并抽到轻伤或重伤，撤销本轮新伤势与风险变化。",
    ),
    Move(
        "sand-accelerate",
        "时之沙·加速",
        tags=("juejue-accelerate",),
        description="进入等权三档加速盘；成功增加胜利权重并在本回合追加1/2/3次抽取，失败产生下回合欠招。",
    ),
    Move(
        "sand-delay",
        "时之沙·时延",
        tags=("juejue-delay",),
        description="进入等权三档时延盘；成功削减对方本轮数值并可能扣对方下回合招数，失败可能令对方加招。",
    ),
    Move(
        "sand-body",
        "时之沙·沙之形体",
        16,
        tags=("juejue-sand-body",),
        description="本回合首次生效：对方第一招仍有效的数值贡献减半并向下取整。",
    ),
    Move("sand-seal", "时之沙·时空间封印术", 30, description="胜利权重+30。"),
    Move(
        "switch-virtual",
        "切换·虚拟声",
        draws=2,
        tags=("juejue-switch-virtual",),
        description="立即切换为虚拟声轮盘，并从新轮盘再抽两次。",
    ),
    Move(
        "sand-domain",
        "领域展开·荒时之沙",
        25,
        tags=("domain", "juejue-sand-domain"),
        description="胜利权重+25；领域命中后对方下回合-1招、自己下回合+1招；主招式盘基础权重1，领域战单领域权重2.5。",
    ),
)
JUEJUE_VIRTUAL_MOVES = (
    Move(
        "virtual-realm",
        "虚拟声·虚拟之境",
        draws=1,
        tags=("juejue-virtual-realm",),
        description="再抽一次；保证下一次加速或时延成功。",
    ),
    Move(
        "future-simulation",
        "虚拟声·未来模拟",
        tags=("juejue-future-simulation",),
        description="本回合首次生效：随机令对方一招仍有效的数值贡献归零，功能部分保留。",
    ),
    Move(
        "realtime-compute",
        "虚拟声·实时演算",
        draws=1,
        tags=("juejue-realtime",),
        description="再抽一次；本回合首次令两种领域的出现权重各+1。",
    ),
    Move(
        "virtual-mimic",
        "虚拟声·虚拟模仿",
        tags=("juejue-mimic",),
        description="大小盘各50%，仅复制其他战斗猪的直接有符号数值与方向，不复制功能。",
    ),
    Move(
        "make-real",
        "虚拟声·化虚为实",
        12,
        tags=("juejue-make-real",),
        description="第n次基础胜利权重为12+5×(n-1)，层数持续整场。",
    ),
    Move(
        "louder",
        "虚拟声·把音乐开大声点！",
        tags=("juejue-music",),
        description="本回合进入音乐状态；不叠加，仅令随后每招胜利权重+5。",
    ),
    Move(
        "switch-sand",
        "切换·时之沙",
        draws=1,
        tags=("juejue-switch-sand",),
        description="立即切换为时之沙并再抽一次；荒时之沙下次出现权重+0.5，下一次加速和时延各+5个百分点。",
    ),
    Move(
        "chaos-domain",
        "领域展开·乱序数虚时空",
        15,
        tags=("domain", "juejue-chaos-domain"),
        description="胜利权重+15；领域命中后自动模仿、自己下回合+1招并保证下一次加速或时延成功；主招式盘基础权重1，领域战单领域权重2.5。",
    ),
)
JUEJUE_FORMS = (
    FighterForm(JUEJUE_FORM_TIME, "时之沙", JUEJUE_TIME_MOVES),
    FighterForm(JUEJUE_FORM_VIRTUAL, "虚拟声", JUEJUE_VIRTUAL_MOVES),
)


FIGHTERS = (
    FighterDefinition(
        "sukuna",
        SUKUNA_PIG_TEMPLATE_ID,
        "宿傩猪",
        (
            Move("black-flash", "黑闪！", 10, draws=2, tags=("black-flash",)),
            Move("dismantle", "解", 10),
            Move("cleave", "捌", 15),
            Move("furnace", "灶·开", 21),
            Move("shrine", "领域展开·伏魔御厨子！", 35, tags=("domain",)),
            Move("loan", "束缚·贷款", draws=1, loan=True),
            Move("reverse", "反转·修复", 14),
            Move("elbow", "肘击", 7),
            Move("net", "网格斩", 12),
            Move("world-cutting-slash", "空间斩", 28),
        ),
    ),
    FighterDefinition(
        "gojo",
        GOJO_PIG_TEMPLATE_ID,
        "五条猪",
        (
            Move("blue", "术式顺转·苍！", 13, tags=("blue-red",)),
            Move("red", "术式反转·赫", 20, tags=("blue-red",)),
            Move("blue-fist", "肘击·苍拳！", 14, tags=("blue-red",)),
            Move("defense", "无下限·防御", 10, tags=("infinity",)),
            Move("black-flash", "黑闪！", 10, draws=2, tags=("black-flash",)),
            Move("teleport", "无下限·瞬移", 14),
            Move("purple", "虚式·茈", 24, tags=("purple",)),
            Move("void", "领域展开·无量空处！", 30, tags=("domain",)),
            Move("reverse", "反转·修复", 14),
            Move("unlimited-purple", "无限制·茈！", 35, tags=("purple",)),
        ),
    ),
    FighterDefinition(
        "juejue",
        JUEJUE_PIG_TEMPLATE_IDS[0],
        "撅撅猪",
        JUEJUE_TIME_MOVES + JUEJUE_VIRTUAL_MOVES,
        template_aliases=JUEJUE_PIG_TEMPLATE_IDS[1:],
        forms=JUEJUE_FORMS,
    ),
)
FIGHTERS_BY_ID = {item.fighter_id: item for item in FIGHTERS}
FIGHTERS_BY_TEMPLATE = {
    template_id: item
    for item in FIGHTERS
    for template_id in (item.template_id, *item.template_aliases)
}
FIGHTER_FORMS_BY_ID = {
    (fighter.fighter_id, form.form_id): form
    for fighter in FIGHTERS
    for form in fighter.forms
}
LEGACY_MOVE_IDS = {
    "sukuna": frozenset(
        ("black-flash", "dismantle", "cleave", "furnace", "shrine", "loan", "reverse", "elbow", "net")
    ),
    "gojo": frozenset(move.move_id for move in FIGHTERS_BY_ID["gojo"].moves),
}


def fighter_moves(fighter_id: str, rule_version: int = BATTLE_RULE_VERSION) -> tuple[Move, ...]:
    moves = FIGHTERS_BY_ID[fighter_id].moves
    if fighter_id == "juejue" and rule_version < 4:
        return ()
    if rule_version == 1:
        return tuple(move for move in moves if move.move_id in LEGACY_MOVE_IDS[fighter_id])
    return moves


def fighter_form_moves(fighter_id: str, form_id: str) -> tuple[Move, ...]:
    try:
        return FIGHTER_FORMS_BY_ID[(fighter_id, form_id)].moves
    except KeyError as exc:
        raise BattleError("未知战斗猪形态。") from exc


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
