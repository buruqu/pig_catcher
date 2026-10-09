"""Battle v19 西天帝路线。只定义常量，避免与主战斗盘形成循环依赖。"""

from fractions import Fraction

XIXI_PIG_TEMPLATE_IDS = (
    "pig-g1092931381-xixi",
    "pig-g237716658-xixi",
    "pig-qo5e5854406d0297d6feae696a13e3a339-xixi",
    "pig-qo9ea2810f378fbd7dc3219c56ceab3520-xixi",
)
XIXI_FORM_CELESTIAL = "xixi-celestial"
XIXI_FORM_EMPEROR = "xixi-emperor"
XIXI_CORE_NAME = "我参透了符文，你输了"
EMPEROR_GAIN = 2_100_000_000
BACK_TO_BASICS_SIMPLE_DOMAIN_CHANCE = Fraction(2, 5)
EQUIPMENT_IDS = frozenset({"xixi-reality", "xixi-hourglass"})
HEXTECH_SPECS = (
    ("physical-to-magic", "物理转魔法", 6, 3),
    ("mind-over-matter", "由心及物", 0, 1),
    ("goliath", "歌莉娅巨人", 8, 1),
    ("back-to-basics", "回归基本功", 10, 2),
)
HEXTECH_NAMES = {key: name for key, name, _gain, _weight in HEXTECH_SPECS}


def build_moves(move):
    """招式胜率和出现权重分别定义；功能招也接受本源的全招加成。"""
    specs = (
        ("surge", "法术涌动", 14, 15000, 0, (), "标记至多1层；连续涌动每次全轮各招+4，超负荷出现权重+0.35。"),
        (
            "prison",
            "符文禁锢",
            18,
            10000,
            1,
            (),
            "随机无视敌1招，再抽1；消费标记自动超负荷，敌下轮-1招；超负荷出现权重+0.35。",
        ),
        ("overload", "超负荷", 24, 12000, 0, (), "手抽清空额外出现权重；消费标记本招数值翻倍；本轮至少2次，下轮+1招。"),
        (
            "reality",
            "现实器",
            0,
            3500,
            0,
            ("xixi-equipment",),
            "全轮己方正收益及减权×1.3，敌方正收益及减权×0.7；本轮及下轮禁抽。",
        ),
        (
            "hourglass",
            "中亚沙漏",
            0,
            1800,
            0,
            ("xixi-equipment",),
            "停止己方后续出招；敌正收益及对己伤害、减益失效，功能增益保留；本轮失败不抽伤势；本轮及下轮禁抽。",
        ),
        ("attack", "法师的至尊平a", 16, 14000, 0, (), "胜率+16。"),
        ("sidestep", "走位", 0, 9000, 1, (), "随机无视敌本轮1招，再抽1。"),
        ("friends", "摇人", 0, 5500, 2, (), "再抽2。"),
        (
            "realm-warp",
            "曲径折跃",
            22,
            6000,
            0,
            ("domain", "xixi-domain"),
            "每次使用永久超负荷+6；领域获胜或命中无视敌2招、下轮+1、抽海克斯。",
        ),
    )
    return tuple(
        move(
            "xixi-" + slug,
            name,
            gain,
            draws=draws,
            draw_weight_units=weight,
            tags=("xixi", "xixi-" + slug, *tags),
            description=description,
        )
        for slug, name, gain, weight, draws, tags, description in specs
    )
