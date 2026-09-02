"""对战版本化定义。抽中招式的权重与招式增加的胜利权重是两套数值。"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import PigCatcherError
from .special_content import GOJO_PIG_TEMPLATE_ID, SUKUNA_PIG_TEMPLATE_ID

# 对战规则版本与活动成就事实版本分离：新版对战会改变随机命名空间，
# 但新增字段仍是 activity_progress v1 可以向后兼容读取的事实载荷。
BATTLE_RULE_VERSION = 11
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
INJURY_WEIGHT_SCALE = 10
# 以十倍整数保存伤势盘的0.1权重，抽签全程不使用浮点数。
# v4的 6.5/2.5/0.5/0.5、2.5/6/1/0.5、1/2.5/6/0.5 保持完全等价。
INJURY_WHEELS = (
    (("light", 65), ("heavy", 25), ("exhausted", 5), ("core", 5)),
    (("light", 25), ("heavy", 60), ("exhausted", 10), ("core", 5)),
    (("light", 10), ("heavy", 25), ("exhausted", 60), ("core", 5)),
)
INJURY_NAMES = {"light": "轻伤", "heavy": "重伤", "exhausted": "力竭倒下", "core": "我掌握了抓猪的核心！"}
MOVE_WEIGHT_SCALE = 1000
VICTORY_WEIGHT_SCALE = 10
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
    # v5的精确字段均只存整数：gain_tenths以0.1自身胜利权重为一单位，
    # opponent_reduction保留对方整数减权语义，draw_weight_units以1/1000抽取权重为一单位。
    # None 保留旧三猪的整数定义，不迫使旧数据改写或使用浮点数。
    gain_tenths: int | None = None
    opponent_reduction: int = 0
    opponent_reduction_tenths: int | None = None
    draw_weight_units: int | None = None

    @property
    def resolved_gain_tenths(self) -> int:
        return self.gain * VICTORY_WEIGHT_SCALE if self.gain_tenths is None else self.gain_tenths

    @property
    def resolved_opponent_reduction_tenths(self) -> int:
        return (
            self.opponent_reduction * VICTORY_WEIGHT_SCALE
            if self.opponent_reduction_tenths is None
            else self.opponent_reduction_tenths
        )

    @property
    def resolved_draw_weight_units(self) -> int:
        return self.draw_weight * MOVE_WEIGHT_SCALE if self.draw_weight_units is None else self.draw_weight_units


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
    initial_form_id: str = ""


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
DANIYA_PIG_TEMPLATE_IDS = (
    "pig-g1092931381-daniya",
    "pig-g237716658-daniya",
    "pig-qo5e5854406d0297d6feae696a13e3a339-daniya",
    "pig-qo9ea2810f378fbd7dc3219c56ceab3520-daniya",
)
ASAMU_PIG_TEMPLATE_IDS = (
    "pig-g1092931381-asamu",
    "pig-g237716658-asamu",
    "pig-qo5e5854406d0297d6feae696a13e3a339-asamu",
    "pig-qo9ea2810f378fbd7dc3219c56ceab3520-asamu",
)
YILU_PIG_TEMPLATE_IDS = (
    "pig-g1092931381-yilu-green-core",
    "pig-g237716658-yilu-green-core",
    "pig-qo5e5854406d0297d6feae696a13e3a339-yilu-green-core",
    "pig-qo9ea2810f378fbd7dc3219c56ceab3520-yilu-green-core",
)
JUEJUE_FORM_TIME = "time-sand"
JUEJUE_FORM_VIRTUAL = "virtual-sound"
DANIYA_FORM_STAGING = "staging"
DANIYA_FORM_DISILLUSION = "disillusion"
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
        description="撤销本回合一次加速失败产生的整笔欠招；可先挂起等待。落败时仍撤销本轮新轻伤或重伤。",
    ),
    Move(
        "sand-accelerate",
        "时之沙·加速",
        draw_weight_units=1500,
        tags=("juejue-accelerate",),
        description="进入等权三档加速盘；成功增加胜利权重并在本回合追加1/2/3次抽取，失败产生下回合欠招。",
    ),
    Move(
        "sand-delay",
        "时之沙·时延",
        draw_weight_units=1500,
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
        description="胜利权重+25；单方命中或领域战获胜后翻倍一份有效领域胜率，并令对方下回合-1招、自己下回合+1招；主招式盘基础权重1，领域战单领域权重2.5。",
    ),
)
JUEJUE_VIRTUAL_MOVES = (
    Move(
        "virtual-realm",
        "虚拟声·虚拟之境",
        5,
        draws=1,
        tags=("juejue-virtual-realm",),
        description="胜利权重+5；再抽一次；保证下一次加速或时延成功。",
    ),
    Move(
        "future-simulation",
        "虚拟声·未来模拟",
        5,
        tags=("juejue-future-simulation",),
        description="胜利权重+5；每次抽中都独立随机令对方一招仍有效的数值贡献归零，功能部分保留。",
    ),
    Move(
        "realtime-compute",
        "虚拟声·实时演算",
        5,
        draws=1,
        tags=("juejue-realtime",),
        description="胜利权重+5；再抽一次；本回合首次令两种领域的出现权重各+1。",
    ),
    Move(
        "virtual-mimic",
        "虚拟声·虚拟模仿",
        tags=("juejue-mimic",),
        description="大小盘各50%，复制其他战斗猪的数值、可移植普通功能与定向效果；领域再入被抑制，领域自动模仿不追加抽数。",
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
        description="首次令本回合随后每招胜利权重+5；重复抽中不叠层，本招额外再抽两次。",
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
        description="胜利权重+15；单方命中或领域战获胜后翻倍一份有效领域胜率，并自动模仿、自己下回合+1招、保证下一次加速或时延成功；主招式盘基础权重1，领域战单领域权重2.5。",
    ),
)
JUEJUE_FORMS = (
    FighterForm(JUEJUE_FORM_TIME, "时之沙", JUEJUE_TIME_MOVES),
    FighterForm(JUEJUE_FORM_VIRTUAL, "虚拟声", JUEJUE_VIRTUAL_MOVES),
)

DANIYA_STAGING_MOVES = (
    Move(
        "daniya-staging-virtual-particle",
        "达妮娅-布景·虚质粒子",
        7,
        tags=("daniya", "daniya-staging"),
        description="胜利权重+7；下一次蚀域主盘抽取权重+0.1。",
        draw_weight_units=1000,
    ),
    Move(
        "daniya-staging-dream-feast",
        "达妮娅-布景·织梦的飨宴",
        12,
        tags=("daniya", "daniya-staging"),
        description="胜利权重+12；下一次蚀域主盘抽取权重+0.1。",
        draw_weight_units=1000,
    ),
    Move(
        "daniya-staging-mimic-bubble",
        "达妮娅-布景·拟态泡泡",
        18,
        tags=("daniya", "daniya-staging"),
        description="胜利权重+18；下一次蚀域主盘抽取权重+0.1。",
        draw_weight_units=1000,
    ),
    Move(
        "daniya-staging-final-curtain",
        "达妮娅-布景·帷幕终景",
        40,
        tags=("daniya", "daniya-staging"),
        description="胜利权重+40；下一次蚀域主盘抽取权重+0.1。",
        draw_weight_units=1000,
    ),
    Move(
        "daniya-staging-greeting",
        "达妮娅-布景·久疏问候！",
        24,
        tags=("daniya", "daniya-staging"),
        description="胜利权重+24；下一次蚀域主盘抽取权重+0.1。",
        draw_weight_units=1000,
    ),
)
DANIYA_DISILLUSION_MOVES = (
    Move(
        "daniya-disillusion-dark-core",
        "达妮娅-幻灭·黯核",
        tags=("daniya", "daniya-disillusion"),
        description="对方胜利权重-9；对方本场后续伤势盘的力竭权重永久+0.1。",
        opponent_reduction=9,
        draw_weight_units=1000,
    ),
    Move(
        "daniya-disillusion-dream-feast",
        "达妮娅-幻灭·织梦的飨宴",
        7,
        tags=("daniya", "daniya-disillusion"),
        description="自身胜利权重+7、对方胜利权重-7；对方本场后续伤势盘的力竭权重永久+0.1。",
        opponent_reduction=7,
        draw_weight_units=1000,
    ),
    Move(
        "daniya-disillusion-banish",
        "达妮娅-幻灭·放逐",
        10,
        tags=("daniya", "daniya-disillusion"),
        description="自身胜利权重+10、对方胜利权重-10；对方本场后续伤势盘的力竭权重永久+0.1。",
        opponent_reduction=10,
        draw_weight_units=1000,
    ),
    Move(
        "daniya-disillusion-final-curtain",
        "达妮娅-幻灭·帷幕终景",
        21,
        tags=("daniya", "daniya-disillusion"),
        description="自身胜利权重+21、对方胜利权重-21；对方本场后续伤势盘的力竭权重永久+0.1。",
        opponent_reduction=21,
        draw_weight_units=1000,
    ),
    Move(
        "daniya-disillusion-knock",
        "达妮娅-幻灭·轻叩门扉",
        13,
        tags=("daniya", "daniya-disillusion"),
        description="自身胜利权重+13、对方胜利权重-13；对方本场后续伤势盘的力竭权重永久+0.1。",
        opponent_reduction=13,
        draw_weight_units=1000,
    ),
)
DANIYA_COMMON_MOVES = (
    Move(
        "daniya-flawless",
        "达妮娅·天衣无缝",
        draws=2,
        tags=("daniya", "daniya-flawless"),
        description="再抽两次；本回合自身领域战胜利权重+0.2。",
        draw_weight_units=800,
    ),
    Move(
        "daniya-unfinished-lie",
        "达妮娅·未竟的谎言",
        draws=1,
        loan=True,
        tags=("daniya", "daniya-loan"),
        description="本回合再抽一次；下一个数值招式的双方数值同步翻倍；对方本回合领域战胜利权重-0.2；下回合-1招。",
        draw_weight_units=800,
    ),
    Move(
        "daniya-timed-collapse",
        "达妮娅·计时的溃灭",
        tags=("daniya", "daniya-timed-collapse"),
        description="对方胜利权重-52.1；对方本回合力竭权重×5，若未力竭，自身下回合力竭权重×5。",
        opponent_reduction_tenths=521,
        draw_weight_units=200,
    ),
    Move(
        "daniya-domain",
        "达妮娅·蚀域",
        30,
        tags=("domain", "daniya", "daniya-domain"),
        description="领域对抗胜利或单方领域命中后翻倍一份有效领域胜率、切换幻灭形态，并使自身下回合出招数+1。",
        draw_weight_units=1000,
    ),
)
DANIYA_FORMS = (
    FighterForm(DANIYA_FORM_STAGING, "布景", DANIYA_STAGING_MOVES + DANIYA_COMMON_MOVES),
    FighterForm(DANIYA_FORM_DISILLUSION, "幻灭", DANIYA_DISILLUSION_MOVES + DANIYA_COMMON_MOVES),
)

ASAMU_MOVES = (
    Move(
        "asamu-bathe",
        "洗澡",
        10,
        tags=("asamu", "asamu-bathe"),
        description="喝奶茶的抽取权重+0.5。",
        draw_weight_units=1000,
    ),
    Move(
        "asamu-milk-tea",
        "喝奶茶",
        20,
        tags=("asamu", "asamu-milk-tea"),
        description="本场永久使全盛姿态抽取权重+0.1，并重置喝奶茶当前抽取权重。",
        draw_weight_units=1000,
    ),
    Move(
        "asamu-sleep",
        "睡觉",
        1,
        tags=("asamu", "asamu-sleep"),
        description="胜利权重+1；本场之后所有招式胜利权重额外+5，可累加。",
        draw_weight_units=1000,
    ),
    Move(
        "asamu-prime",
        "全盛姿态",
        30,
        draws=2,
        tags=("asamu", "asamu-prime"),
        description="胜利权重+30，再抽两次；清空憋个大的临时出现权重。",
        draw_weight_units=200,
    ),
    Move(
        "asamu-charge-up",
        "憋个大的",
        draws=1,
        tags=("asamu", "asamu-charge-up"),
        description="再抽一次；全盛姿态临时出现权重+1，打出全盛姿态后清空。",
        draw_weight_units=1000,
    ),
    Move(
        "asamu-pressure-king",
        "传奇耐压王",
        7,
        tags=("asamu", "asamu-pressure-king"),
        description="对方本回合每个数值招式独立33%失效；每层独立判定，只归零数值而保留功能。",
        draw_weight_units=500,
    ),
    Move(
        "asamu-misfortune-transfer",
        "厄运传递",
        13,
        tags=("asamu", "asamu-misfortune-transfer"),
        description="双方本回合力竭倒下权重均×5。",
        draw_weight_units=500,
    ),
    Move(
        "asamu-milk-dragon",
        "发奶龙",
        9,
        tags=("asamu", "asamu-milk-dragon"),
        description="依次将对方下回合第一、第二…招替换为发奶龙；被替换的发奶龙不反向影响来源玩家。",
        draw_weight_units=1000,
    ),
    Move(
        "asamu-tit-for-tat",
        "以牙还牙",
        4,
        tags=("asamu", "asamu-tit-for-tat"),
        description="回合末若自身权重较低，交换双方权重并再+4；否则自身+40。抽取权重随无伤/轻伤/重伤为0.4/0.749/0.947。",
        draw_weight_units=400,
    ),
    Move(
        "asamu-domain",
        "领域·呃呃阿萨姆奶茶",
        22,
        tags=("domain", "asamu", "asamu-domain"),
        description="领域对抗胜利或单方领域命中后翻倍一份有效领域胜率，并使用对方2个随机招式。",
        draw_weight_units=1000,
    ),
)

YILU_MOVES = (
    Move(
        "yilu-vanguard",
        "干员放置·先锋",
        5,
        tags=("yilu", "yilu-operator", "yilu-vanguard"),
        description="胜利权重+5；再抽一次并累计2指示物；此后所有招式基础胜率+2。",
    ),
    Move(
        "yilu-guard",
        "干员放置·近卫",
        tags=("yilu", "yilu-operator", "yilu-guard"),
        description="先累计1指示物，再消耗全部指示物，胜率+消耗数×5；消耗超过5时触发本回合真伤翻倍。",
    ),
    Move(
        "yilu-defender",
        "干员放置·重装",
        2,
        tags=("yilu", "yilu-operator", "yilu-defender"),
        description="胜率+2并累计1指示物；70%令对方随机一招数值归零并额外使对方胜率-5。",
    ),
    Move(
        "yilu-caster",
        "干员放置·术师",
        tags=("yilu", "yilu-operator", "yilu-caster"),
        description="先累计3指示物；每消耗6个胜率+40，可连续结算；不足6时再累计1指示物。",
    ),
    Move(
        "yilu-sniper",
        "干员放置·狙击",
        tags=("yilu", "yilu-operator", "yilu-sniper"),
        description="等概率连射1至10次；每枪+1，独立50%累计指示物、50%消耗1指示物令该枪再+2。",
    ),
    Move(
        "yilu-medic",
        "干员放置·医疗·冥土追魂",
        tags=("yilu", "yilu-operator", "yilu-medic"),
        description="消耗全部指示物，使本回合重伤和力竭权重各减半；若处于重伤则恢复为轻伤，再累计2指示物。",
        draw_weight_units=200,
    ),
    Move(
        "yilu-specialist",
        "干员放置·特种",
        tags=("yilu", "yilu-operator", "yilu-specialist"),
        description="消耗全部指示物，再抽两次非医疗、非特种的其他干员招式，并累计1指示物。",
        draw_weight_units=500,
    ),
    Move(
        "yilu-domain",
        "领域展开·末日方舟",
        tags=("domain", "yilu", "yilu-domain"),
        description="胜率+32.5；领域对抗获胜或单方领域命中后翻倍一份有效领域胜率并获得明日：下回合+1招且该回合所有招式基础胜率+1。",
        gain_tenths=325,
    ),
    Move(
        "yilu-babel-ghost",
        "巴别塔的恶灵",
        tags=("yilu", "yilu-babel"),
        description="再部署1名干员且该干员效果生效两次；下回合理智缺失，出招数-1。",
    ),
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
            Move(
                "shrine",
                "领域展开·伏魔御厨子！",
                35,
                tags=("domain",),
                description="单方领域命中或领域战获胜后，翻倍一份仍有效的领域胜率贡献；领域战基础权重4。",
            ),
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
            Move(
                "void",
                "领域展开·无量空处！",
                30,
                tags=("domain",),
                description="单方领域命中或领域战获胜后，翻倍一份仍有效的领域胜率贡献，并使对方下回合出招数-1。",
            ),
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
    FighterDefinition(
        "daniya",
        DANIYA_PIG_TEMPLATE_IDS[0],
        "达妮娅猪",
        DANIYA_STAGING_MOVES + DANIYA_DISILLUSION_MOVES + DANIYA_COMMON_MOVES,
        template_aliases=DANIYA_PIG_TEMPLATE_IDS[1:],
        forms=DANIYA_FORMS,
        initial_form_id=DANIYA_FORM_STAGING,
    ),
    FighterDefinition(
        "asamu",
        ASAMU_PIG_TEMPLATE_IDS[0],
        "阿萨姆猪",
        ASAMU_MOVES,
        template_aliases=ASAMU_PIG_TEMPLATE_IDS[1:],
    ),
    FighterDefinition(
        "yilu",
        YILU_PIG_TEMPLATE_IDS[0],
        "熠～噜猪",
        YILU_MOVES,
        template_aliases=YILU_PIG_TEMPLATE_IDS[1:],
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
    if fighter_id in {"daniya", "asamu"} and rule_version < 5:
        return ()
    if fighter_id == "yilu" and rule_version < 7:
        return ()
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
