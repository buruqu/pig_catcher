"""不自行提交事务的 SQLite 仓储。"""

from .assets import AssetRepository
from .economy import EconomyRepository
from .framework import FrameworkRepository
from .gameplay import GameplayRepository
from .operations import OperationsRepository
from .receipts import ReceiptRepository
from .social import SocialRepository

__all__ = [
    "AssetRepository",
    "EconomyRepository",
    "FrameworkRepository",
    "GameplayRepository",
    "OperationsRepository",
    "ReceiptRepository",
    "SocialRepository",
]
