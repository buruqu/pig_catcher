"""数据库迁移注册表。"""

from .model import Migration
from .v0001_initial import MIGRATION_0001
from .v0002_asset_media_and_collections import MIGRATION_0002
from .v0003_catching_query_indexes import MIGRATION_0003
from .v0004_cooking_and_economy_indexes import MIGRATION_0004
from .v0005_social_ranking_and_global_records import MIGRATION_0005
from .v0006_food_effect_queue import MIGRATION_0006
from .v0007_paired_six_star_recipes import MIGRATION_0007
from .v0008_catch_quota_bonuses import MIGRATION_0008
from .v0009_player_restrictions import MIGRATION_0009
from .v0010_split_social_blacklists import MIGRATION_0010
from .v0011_pig_alternate_image import MIGRATION_0011
from .v0012_pig_display_variant import MIGRATION_0012
from .v0013_quota_window_boosts import MIGRATION_0013
from .v0014_batch_keep_highest import MIGRATION_0014
from .v0015_admin_commands import MIGRATION_0015
from .v0016_alphanumeric_short_codes import MIGRATION_0016
from .v0017_automatic_regulation import MIGRATION_0017
from .v0018_food_effect_rebalance import MIGRATION_0018
from .v0019_six_ways_effect_repair import MIGRATION_0019
from .v0020_food_effect_expansion import MIGRATION_0020
from .v0021_pig_cookie_effect_repair import MIGRATION_0021
from .v0022_group_food_effects import MIGRATION_0022
from .v0023_six_star_food_guarantees import MIGRATION_0023
from .v0024_quick_eat_and_item_queue import MIGRATION_0024
from .v0025_armed_item_last_use import MIGRATION_0025
from .v0026_six_star_progress import MIGRATION_0026
from .v0027_transfer_events_system_gift import MIGRATION_0027
from .v0028_pig_nose_extra_catch import MIGRATION_0028
from .v0029_asamu_auto_gift_rebalance import MIGRATION_0029
from .v0030_catch_quota_covering_index import MIGRATION_0030

MIGRATIONS: tuple[Migration, ...] = (
    MIGRATION_0001,
    MIGRATION_0002,
    MIGRATION_0003,
    MIGRATION_0004,
    MIGRATION_0005,
    MIGRATION_0006,
    MIGRATION_0007,
    MIGRATION_0008,
    MIGRATION_0009,
    MIGRATION_0010,
    MIGRATION_0011,
    MIGRATION_0012,
    MIGRATION_0013,
    MIGRATION_0014,
    MIGRATION_0015,
    MIGRATION_0016,
    MIGRATION_0017,
    MIGRATION_0018,
    MIGRATION_0019,
    MIGRATION_0020,
    MIGRATION_0021,
    MIGRATION_0022,
    MIGRATION_0023,
    MIGRATION_0024,
    MIGRATION_0025,
    MIGRATION_0026,
    MIGRATION_0027,
    MIGRATION_0028,
    MIGRATION_0029,
    MIGRATION_0030,
)

__all__ = ["MIGRATIONS", "Migration"]
