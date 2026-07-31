"""数据库迁移注册表。"""

from .model import Migration
from .v0001_initial import MIGRATION_0001
from .v0002_asset_media_and_collections import MIGRATION_0002
from .v0003_catching_query_indexes import MIGRATION_0003
from .v0004_cooking_and_economy_indexes import MIGRATION_0004
from .v0005_social_ranking_and_global_records import MIGRATION_0005
from .v0006_food_effect_queue import MIGRATION_0006
from .v0007_paired_six_star_recipes import MIGRATION_0007

MIGRATIONS: tuple[Migration, ...] = (
    MIGRATION_0001,
    MIGRATION_0002,
    MIGRATION_0003,
    MIGRATION_0004,
    MIGRATION_0005,
    MIGRATION_0006,
    MIGRATION_0007,
)

__all__ = ["MIGRATIONS", "Migration"]
