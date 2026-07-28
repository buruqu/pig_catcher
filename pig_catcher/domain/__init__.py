"""抓猪插件纯领域规则。"""

from .enums import AssetKind, ConsentStatus, FatProfile, Rarity, ReceiptSendStatus, TemplateScope
from .models import AssetSelector, CommandIdentity, ScopeKey

__all__ = [
    "AssetKind",
    "AssetSelector",
    "CommandIdentity",
    "ConsentStatus",
    "FatProfile",
    "Rarity",
    "ReceiptSendStatus",
    "ScopeKey",
    "TemplateScope",
]
