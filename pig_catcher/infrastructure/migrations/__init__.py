"""数据库迁移注册表。"""

from .model import Migration
from .v0001_initial import MIGRATION_0001

MIGRATIONS: tuple[Migration, ...] = (MIGRATION_0001,)

__all__ = ["MIGRATIONS", "Migration"]
