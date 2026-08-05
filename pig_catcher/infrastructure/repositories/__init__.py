"""不自行提交事务的 SQLite 仓储。"""

from .assets import AssetRepository
from .economy import EconomyRepository
from .framework import FrameworkRepository
from .gameplay import GameplayRepository
from .operations import OperationsRepository
from .quota import QuotaRepository
from .receipts import ReceiptRepository
from .restrictions import RestrictionRepository
from .social import SocialRepository

__all__ = [
    "AssetRepository",
    "EconomyRepository",
    "FrameworkRepository",
    "GameplayRepository",
    "OperationsRepository",
    "QuotaRepository",
    "ReceiptRepository",
    "RestrictionRepository",
    "SocialRepository",
]
