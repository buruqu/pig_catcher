"""通用道具背包与食物奖励券的公开规则，不依赖成就功能开关。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .activity_achievements import ACTIVITY_REWARDS
from .errors import DomainValidationError

CODE_CHANGE_COUPON = "asset-code-change"
PIG_CHOICE_COUPON = "pig-choice"
FOOD_CHOICE_COUPON = "food-choice"
BATTLE_PIG_CHOICE_COUPON = "battle-pig-choice"
FIVE_STAR_COLLAB_RANDOM_COUPON = "five-star-collab-random"
LEGACY_CODE_CHANGE_COUPON = "identifier-reforge"
CHOICE_TTL_MS = 30_000
BAG_PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class CouponDefinition:
    coupon_id: str
    name: str
    summary: str


COUPONS = MappingProxyType(
    {
        CODE_CHANGE_COUPON: CouponDefinition(
            CODE_CHANGE_COUPON,
            "编号修改券",
            "修改自己一件空闲猪猪或美食的编号；4～16位英文字母或数字，大小写等同。",
        ),
        PIG_CHOICE_COUPON: CouponDefinition(
            PIG_CHOICE_COUPON,
            "猪猪自选券",
            "选择当前群已启用、已授权的任意一只猪猪，含本群六星；自然随机体型和重量。",
        ),
        FOOD_CHOICE_COUPON: CouponDefinition(
            FOOD_CHOICE_COUPON,
            "美食自选券",
            "选择当前群已启用、已授权的任意一道美食；自然随机份量和价值。",
        ),
        BATTLE_PIG_CHOICE_COUPON: CouponDefinition(
            BATTLE_PIG_CHOICE_COUPON,
            "战斗猪自选券",
            "选择当前版本拥有战斗盘且在本群已授权的任意一只猪猪。",
        ),
        FIVE_STAR_COLLAB_RANDOM_COUPON: CouponDefinition(
            FIVE_STAR_COLLAB_RANDOM_COUPON,
            "五星联动猪随机券",
            "随机获得当前群已启用、已授权的一只五星联动猪猪；每次使用一张。",
        ),
    }
)

REWARD_NAMES = MappingProxyType(
    {
        "achievement-catch": "成就抓猪券",
        "catalog-guide": "图鉴引路券",
        "food-inspiration": "美食灵感券",
        "giant-rescale": "巨物复秤券",
        "mini-rescale": "迷你复秤券",
        "recook": "回锅重做券",
        LEGACY_CODE_CHANGE_COUPON: "编号重铸券",
        "achievement-firework": "成就礼花券",
        "achievement-choice": "成就自选宝箱",
        "regular-five-star-memorial": "常规成就毕业纪念猪礼盒",
        **{key: value["name"] for key, value in ACTIVITY_REWARDS.items()},
        **{key: value.name for key, value in COUPONS.items()},
    }
)

COUPON_HELP = (
    "/使用奖励券 编号修改券 猪猪 猪名#旧编号 新编号（美食同理；卷也可识别）。",
    "/使用奖励券 猪猪自选券 猪名 → 30秒内 /使用奖励券 确认；/使用奖励券 取消。",
    "/使用奖励券 美食自选券 菜名；/使用奖励券 战斗猪自选券 猪名 → 30秒内确认。",
    "/使用奖励券 五星联动猪随机券（每次随机发放一只当前群五星联动猪）。",
    "编号修改也可 /重铸编号 猪猪 旧编号 新编号；优先使用编号修改券，再使用旧编号重铸券。",
    "奖励券不可赠送或交易；未确认自选猪前不扣券，不占抓猪额度、不发抓猪收益。",
)


def coupon_definition(value: str) -> CouponDefinition:
    normalized = str(value or "").strip().replace("卷", "券")
    for key, definition in COUPONS.items():
        if normalized in {key, definition.name}:
            return definition
    raise DomainValidationError("请选择道具背包中实际持有的奖励券；/道具背包 查看库存和用法。")


@dataclass(frozen=True, slots=True)
class BagEntry:
    """总数已包含待命、排队和已激活份数，展示时不能再相加。"""

    item_id: str
    category: str
    name: str
    total: int
    available: int
    state: str
    summary: str
