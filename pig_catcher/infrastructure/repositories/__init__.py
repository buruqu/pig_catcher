"""不自行提交事务的 SQLite 仓储。"""

from .administration import AdministrationRepository
from .assets import AssetRepository
from .economy import EconomyRepository
from .framework import FrameworkRepository
from .gameplay import GameplayRepository
from .operations import OperationsRepository
from .quota import QuotaRepository
from .receipts import ReceiptRepository
from .regulation import RegulationRepository
from .restrictions import RestrictionRepository
from .social import SocialRepository
from .techniques import TechniqueRepository

__all__ = [
    "AssetRepository",
    "AdministrationRepository",
    "EconomyRepository",
    "FrameworkRepository",
    "GameplayRepository",
    "OperationsRepository",
    "QuotaRepository",
    "RegulationRepository",
    "ReceiptRepository",
    "RestrictionRepository",
    "SocialRepository",
    "TechniqueRepository",
]
