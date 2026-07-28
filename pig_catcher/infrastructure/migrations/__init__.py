"""数据库迁移注册表。"""

from .model import Migration
from .v0001_initial import MIGRATION_0001
from .v0002_asset_media_and_collections import MIGRATION_0002
from .v0003_catching_query_indexes import MIGRATION_0003
from .v0004_cooking_and_economy_indexes import MIGRATION_0004

MIGRATIONS: tuple[Migration, ...] = (
    MIGRATION_0001,
    MIGRATION_0002,
    MIGRATION_0003,
    MIGRATION_0004,
)

__all__ = ["MIGRATIONS", "Migration"]
