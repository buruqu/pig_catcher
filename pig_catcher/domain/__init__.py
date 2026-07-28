"""抓猪插件纯领域规则。"""

from .enums import AssetKind, ConsentStatus, FatProfile, Rarity, ReceiptSendStatus, TemplateScope
from .gameplay import (
    CATCH_COIN_REWARDS,
    CATCH_EXPERIENCE_REWARDS,
    ITEM_DEFINITIONS,
    ITEMS_BY_ID,
    LEVEL_THRESHOLDS,
    PIG_BASE_VALUES,
    PIG_RARITY_NAMES,
    ItemDefinition,
    LevelProgress,
    PigAttributes,
    generate_pig_attributes,
    item_by_id,
    item_by_name,
    level_progress,
)
from .models import AssetSelector, CommandIdentity, ScopeKey

__all__ = [
    "AssetKind",
    "AssetSelector",
    "CATCH_COIN_REWARDS",
    "CATCH_EXPERIENCE_REWARDS",
    "CommandIdentity",
    "ConsentStatus",
    "FatProfile",
    "ITEM_DEFINITIONS",
    "ITEMS_BY_ID",
    "ItemDefinition",
    "LEVEL_THRESHOLDS",
    "LevelProgress",
    "PIG_BASE_VALUES",
    "PIG_RARITY_NAMES",
    "PigAttributes",
    "Rarity",
    "ReceiptSendStatus",
    "ScopeKey",
    "TemplateScope",
    "generate_pig_attributes",
    "item_by_id",
    "item_by_name",
    "level_progress",
]
