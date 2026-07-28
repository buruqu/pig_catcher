"""领域枚举。"""

from enum import IntEnum, StrEnum


class Rarity(IntEnum):
    """猪猪与美食共用的六档品质。"""

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6


class AssetKind(StrEnum):
    """素材和实例类型。"""

    PIG = "pig"
    FOOD = "food"


class TemplateScope(StrEnum):
    """模板发布范围。"""

    COMMON = "common"
    GROUP = "group"


class ConsentStatus(StrEnum):
    """群专属素材授权状态。"""

    NOT_REQUIRED = "not-required"
    GRANTED = "granted"
    REVOKED = "revoked"


class FitMode(StrEnum):
    """素材在渲染安全区中的裁切方式。"""

    CONTAIN = "contain"
    COVER = "cover"


class FatProfile(StrEnum):
    """猪模板的肥瘦画像。"""

    LEAN = "lean"
    BALANCED = "balanced"
    FATTY = "fatty"


class AssetState(StrEnum):
    """猪或美食实例的生命周期状态。"""

    ACTIVE = "active"
    LOCKED_FOR_TRADE = "locked-for-trade"
    SOLD = "sold"
    CONSUMED_FOR_COOKING = "consumed-for-cooking"
    CONSUMED = "consumed"


class TradeStatus(StrEnum):
    """交易报价状态。"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReceiptSendStatus(StrEnum):
    """幂等收据的公示发送状态。"""

    PENDING = "pending"
    CLAIMED = "claimed"
    SENT = "sent"
    FAILED = "failed"


class RecordType(StrEnum):
    """群纪录类型。"""

    SIZE = "size"
    WEIGHT = "weight"


class UpgradeType(StrEnum):
    """永久升级类型。"""

    FEED = "feed"
    COOKWARE = "cookware"
