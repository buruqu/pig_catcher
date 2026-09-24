"""Mid-Autumn 2026 content rules, independent of the future activity rewards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
MID_AUTUMN_START = datetime(2026, 9, 25, tzinfo=BEIJING)
MID_AUTUMN_END = datetime(2026, 10, 1, tzinfo=BEIJING)

LANTERN_PIG_ID = "pig-midautumn-2026-lantern"
OSMANTHUS_PIG_ID = "pig-midautumn-2026-osmanthus"
JADE_RABBIT_PIG_ID = "pig-midautumn-2026-jade-rabbit"
RED_BEAN_MOONCAKE_ID = "food-midautumn-2026-red-bean"
OSMANTHUS_MOONCAKE_ID = "food-midautumn-2026-osmanthus-lava"
SNOW_SKIN_MOONCAKE_ID = "food-midautumn-2026-snow-skin"

# A matched pig produces its mooncake when the ordinary cooking roll lands on
# that mooncake's rarity. Other outcomes keep using the ordinary recipe pool.
MOONCAKE_BY_PIG_AND_RARITY = {
    (LANTERN_PIG_ID, 2): RED_BEAN_MOONCAKE_ID,
    (OSMANTHUS_PIG_ID, 4): OSMANTHUS_MOONCAKE_ID,
    (JADE_RABBIT_PIG_ID, 5): SNOW_SKIN_MOONCAKE_ID,
}
EXCLUSIVE_MOONCAKE_IDS = frozenset(MOONCAKE_BY_PIG_AND_RARITY.values())


def mid_autumn_boost_active(now: datetime) -> bool:
    """The boosted food effect is determined when it is used in Beijing time."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Mid-Autumn time must be timezone-aware")
    return MID_AUTUMN_START <= now.astimezone(BEIJING) < MID_AUTUMN_END
