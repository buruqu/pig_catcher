"""数据库迁移注册表。"""

from .model import Migration
from .v0001_initial import MIGRATION_0001
from .v0002_asset_media_and_collections import MIGRATION_0002

MIGRATIONS: tuple[Migration, ...] = (MIGRATION_0001, MIGRATION_0002)

__all__ = ["MIGRATIONS", "Migration"]
