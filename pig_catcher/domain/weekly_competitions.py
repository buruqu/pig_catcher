"""Data-driven weekly competition definitions for PiG Dream!.

The registry describes *what* a season measures and rewards.  Runtime dates,
group-scoped entries and settlement state live in SQLite so future seasons can
be added without branching the command or rendering layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WeeklyAggregation(StrEnum):
    """Supported score aggregation strategies."""

    SUM = "sum"
    MAX = "max"
    MIN = "min"
    COUNT = "count"


class WeeklySortDirection(StrEnum):
    """Primary score direction."""

    DESCENDING = "desc"
    ASCENDING = "asc"


@dataclass(frozen=True, slots=True)
class WeeklyReward:
    """One non-tradable or ledger-backed settlement reward."""

    reward_type: str
    reward_id: str
    quantity: int = 1

    def __post_init__(self) -> None:
        if self.reward_type not in {"coin", "ticket", "title", "frame", "badge"}:
            raise ValueError(f"Unsupported weekly reward type: {self.reward_type}")
        if not self.reward_id.strip():
            raise ValueError("Weekly reward id cannot be empty")
        if self.quantity <= 0:
            raise ValueError("Weekly reward quantity must be positive")


@dataclass(frozen=True, slots=True)
class WeeklyRewardTier:
    """Rewards shared by an exact set of final ranks."""

    ranks: tuple[int, ...]
    rewards: tuple[WeeklyReward, ...]

    def __post_init__(self) -> None:
        if not self.ranks or any(rank <= 0 for rank in self.ranks):
            raise ValueError("Weekly reward ranks must be positive")
        if len(set(self.ranks)) != len(self.ranks):
            raise ValueError("Weekly reward ranks cannot repeat within one tier")
        if not self.rewards:
            raise ValueError("Weekly reward tier cannot be empty")


@dataclass(frozen=True, slots=True)
class WeeklyCompetitionDefinition:
    """Stable season definition independent from its runtime week window."""

    definition_key: str
    season_number: int
    name: str
    source_result_type: str
    source_command_names: tuple[str, ...]
    source_field: str
    aggregation: WeeklyAggregation
    sort_direction: WeeklySortDirection
    metric_label: str
    metric_unit: str
    reward_tiers: tuple[WeeklyRewardTier, ...]
    fixed_starts_at: str = ""
    fixed_ends_at: str = ""
    source_template_ids: tuple[str, ...] = ()
    tie_breaker: str = "best-single"

    @property
    def receipt_result_types(self) -> tuple[str, ...]:
        return ("cooking", "batch-cooking") if self.source_result_type == "cooking" else (self.source_result_type,)

    def __post_init__(self) -> None:
        if not self.definition_key.strip() or not self.name.strip():
            raise ValueError("Weekly competition key and name cannot be empty")
        if self.season_number <= 0:
            raise ValueError("Weekly season number must be positive")
        if not self.source_result_type.strip() or not self.source_command_names:
            raise ValueError("Weekly competition needs at least one receipt source")
        if not self.source_field.strip() and self.aggregation is not WeeklyAggregation.COUNT:
            raise ValueError("Non-count weekly competitions need a source field")
        seen: set[int] = set()
        for tier in self.reward_tiers:
            overlap = seen.intersection(tier.ranks)
            if overlap:
                raise ValueError(f"Weekly reward ranks repeat across tiers: {sorted(overlap)}")
            seen.update(tier.ranks)
        if bool(self.fixed_starts_at) != bool(self.fixed_ends_at):
            raise ValueError("Fixed weekly window needs both start and end")
        if self.tie_breaker not in {"best-single", "earliest-score"}:
            raise ValueError("Unsupported weekly tie breaker")
        if self.source_result_type in {"cooking", "food-consumed"} and not self.source_template_ids:
            raise ValueError("Food competition requires exact food template filters")

    def rewards_for_rank(self, rank: int) -> tuple[WeeklyReward, ...]:
        """Resolve one deterministic final-rank reward bundle."""

        for tier in self.reward_tiers:
            if rank in tier.ranks:
                return tier.rewards
        return ()


WEEKLY_SPRINT_TITLE_ID = "weekly-001-catch-value-title"
WEEKLY_SPRINT_FRAME_ID = "weekly-001-catch-value-frame"
WEEKLY_SPRINT_BADGE_IDS = {
    1: "weekly-001-catch-value-rank-1",
    2: "weekly-001-catch-value-rank-2",
    3: "weekly-001-catch-value-rank-3",
    10: "weekly-001-catch-value-rank-10",
}

WEEKLY_SUSHI_TITLE_ID = "weekly-002-sushi-title"
WEEKLY_SUSHI_FRAME_ID = "weekly-002-sushi-frame"
WEEKLY_SUSHI_MEDAL_ID = "weekly-002-sushi-medal"
WEEKLY_SUSHI_BADGE_IDS = {rank: f"weekly-002-sushi-rank-{rank}" for rank in (1, 2, 3, 10)}

WEEKLY_MOONCAKE_TITLE_ID = "weekly-003-mooncake-title"
WEEKLY_MOONCAKE_FRAME_ID = "weekly-003-mooncake-frame"
WEEKLY_MOONCAKE_STAMP_ID = "weekly-003-mooncake-stamp"
WEEKLY_MOONCAKE_MEDAL_ID = "weekly-003-mooncake-medal"
WEEKLY_MOONCAKE_BADGE_IDS = {rank: f"weekly-003-mooncake-rank-{rank}" for rank in (1, 2, 3, 10)}


def _sushi_rewards(rank: int, *, coins: int, catch_tickets: int, fireworks: int) -> tuple[WeeklyReward, ...]:
    return (
        WeeklyReward("coin", "pig-coin", coins),
        WeeklyReward("ticket", "achievement-catch", catch_tickets),
        WeeklyReward("ticket", "achievement-firework", fireworks),
        WeeklyReward("title", WEEKLY_SUSHI_TITLE_ID),
        WeeklyReward("frame", WEEKLY_SUSHI_FRAME_ID),
        WeeklyReward("badge", WEEKLY_SUSHI_BADGE_IDS[rank if rank <= 3 else 10]),
        WeeklyReward("badge", WEEKLY_SUSHI_MEDAL_ID),
    )


def _mooncake_rewards(rank: int, *, coins: int, catch_tickets: int, fireworks: int) -> tuple[WeeklyReward, ...]:
    return (
        WeeklyReward("coin", "pig-coin", coins),
        WeeklyReward("ticket", "achievement-catch", catch_tickets),
        WeeklyReward("ticket", "achievement-firework", fireworks),
        WeeklyReward("title", WEEKLY_MOONCAKE_TITLE_ID),
        WeeklyReward("frame", WEEKLY_MOONCAKE_FRAME_ID),
        WeeklyReward("badge", WEEKLY_MOONCAKE_BADGE_IDS[rank if rank <= 3 else 10]),
        WeeklyReward("badge", WEEKLY_MOONCAKE_MEDAL_ID),
    )


def _sprint_rewards(rank: int, *, coins: int, catch_tickets: int, fireworks: int) -> tuple[WeeklyReward, ...]:
    badge_rank = rank if rank <= 3 else 10
    return (
        WeeklyReward("coin", "pig-coin", coins),
        WeeklyReward("ticket", "achievement-catch", catch_tickets),
        WeeklyReward("ticket", "achievement-firework", fireworks),
        WeeklyReward("title", WEEKLY_SPRINT_TITLE_ID),
        WeeklyReward("frame", WEEKLY_SPRINT_FRAME_ID),
        WeeklyReward("badge", WEEKLY_SPRINT_BADGE_IDS[badge_rank]),
    )


WEEKLY_COMPETITION_DEFINITIONS: tuple[WeeklyCompetitionDefinition, ...] = (
    WeeklyCompetitionDefinition(
        definition_key="weekly-001-catch-value-sprint",
        season_number=1,
        name="抓猪冲刺！！！",
        source_result_type="pig",
        source_command_names=("pig-catcher.catch",),
        source_field="official_value",
        aggregation=WeeklyAggregation.SUM,
        sort_direction=WeeklySortDirection.DESCENDING,
        metric_label="本周抓猪累计官方价值",
        metric_unit="价值",
        reward_tiers=(
            WeeklyRewardTier((1,), _sprint_rewards(1, coins=10_000, catch_tickets=5, fireworks=2)),
            WeeklyRewardTier((2,), _sprint_rewards(2, coins=8_000, catch_tickets=4, fireworks=2)),
            WeeklyRewardTier((3,), _sprint_rewards(3, coins=6_000, catch_tickets=3, fireworks=1)),
            WeeklyRewardTier(
                tuple(range(4, 11)),
                _sprint_rewards(10, coins=3_000, catch_tickets=2, fireworks=1),
            ),
        ),
        fixed_starts_at="2026-09-01T00:00:00+08:00",
        fixed_ends_at="2026-09-08T00:00:00+08:00",
    ),
    WeeklyCompetitionDefinition(
        definition_key="weekly-002-sushi-king",
        season_number=2,
        name="寿司拼盘大王",
        source_result_type="cooking",
        source_command_names=("pig-catcher.cook", "pig-catcher.batch-cook"),
        source_field="quantity",
        source_template_ids=("food-r5-pig-sushi-platter",),
        aggregation=WeeklyAggregation.SUM,
        sort_direction=WeeklySortDirection.DESCENDING,
        tie_breaker="earliest-score",
        metric_label="本期亲手做出的猪寿司拼盘",
        metric_unit="份",
        reward_tiers=(
            WeeklyRewardTier((1,), _sushi_rewards(1, coins=10_000, catch_tickets=5, fireworks=2)),
            WeeklyRewardTier((2,), _sushi_rewards(2, coins=8_000, catch_tickets=4, fireworks=2)),
            WeeklyRewardTier((3,), _sushi_rewards(3, coins=6_000, catch_tickets=3, fireworks=1)),
            WeeklyRewardTier(tuple(range(4, 11)), _sushi_rewards(10, coins=3_000, catch_tickets=2, fireworks=1)),
        ),
        fixed_starts_at="2026-09-08T00:02:00+08:00",
        fixed_ends_at="2026-09-15T00:00:00+08:00",
    ),
    WeeklyCompetitionDefinition(
        definition_key="weekly-003-mooncake-feast",
        season_number=3,
        name="月圆猪饼宴",
        source_result_type="food-consumed",
        source_command_names=("pig-catcher.eat",),
        source_field="",
        source_template_ids=(
            "food-midautumn-2026-red-bean",
            "food-midautumn-2026-osmanthus-lava",
            "food-midautumn-2026-snow-skin",
        ),
        aggregation=WeeklyAggregation.COUNT,
        sort_direction=WeeklySortDirection.DESCENDING,
        tie_breaker="earliest-score",
        metric_label="本期吃下的中秋猪月饼",
        metric_unit="个",
        reward_tiers=(
            WeeklyRewardTier((1,), _mooncake_rewards(1, coins=10_000, catch_tickets=5, fireworks=2)),
            WeeklyRewardTier((2,), _mooncake_rewards(2, coins=8_000, catch_tickets=4, fireworks=2)),
            WeeklyRewardTier((3,), _mooncake_rewards(3, coins=6_000, catch_tickets=3, fireworks=1)),
            WeeklyRewardTier(tuple(range(4, 11)), _mooncake_rewards(10, coins=3_000, catch_tickets=2, fireworks=1)),
        ),
        fixed_starts_at="2026-09-25T00:00:00+08:00",
        fixed_ends_at="2026-10-01T00:00:00+08:00",
    ),
)

WEEKLY_COMPETITIONS_BY_KEY = {definition.definition_key: definition for definition in WEEKLY_COMPETITION_DEFINITIONS}
WEEKLY_COMPETITIONS_BY_SEASON = {definition.season_number: definition for definition in WEEKLY_COMPETITION_DEFINITIONS}


WEEKLY_REWARD_NAMES = {
    WEEKLY_MOONCAKE_TITLE_ID: "月圆猪饼宴·团圆食客",
    WEEKLY_MOONCAKE_FRAME_ID: "月圆猪饼宴·桂月流光边框",
    WEEKLY_MOONCAKE_STAMP_ID: "月下三味·团圆猪月饼印章",
    WEEKLY_MOONCAKE_MEDAL_ID: "月圆猪饼宴·月桂徽章",
    **{key: f"月圆猪饼宴·{rank}牌" for rank, key in WEEKLY_MOONCAKE_BADGE_IDS.items()},
    WEEKLY_SUSHI_TITLE_ID: "寿司拼盘大王",
    WEEKLY_SUSHI_FRAME_ID: "寿司拼盘大王·寿司宴台边框",
    WEEKLY_SUSHI_MEDAL_ID: "寿司拼盘大王·匠心寿司徽章",
    **{key: f"寿司拼盘大王·{rank}牌" for rank, key in WEEKLY_SUSHI_BADGE_IDS.items()},
    WEEKLY_SPRINT_TITLE_ID: "抓猪冲刺者",
    WEEKLY_SPRINT_FRAME_ID: "抓猪冲刺！！！·赛道边框",
    WEEKLY_SPRINT_BADGE_IDS[1]: "抓猪冲刺！！！·1牌",
    WEEKLY_SPRINT_BADGE_IDS[2]: "抓猪冲刺！！！·2牌",
    WEEKLY_SPRINT_BADGE_IDS[3]: "抓猪冲刺！！！·3牌",
    WEEKLY_SPRINT_BADGE_IDS[10]: "抓猪冲刺！！！·10牌",
}


__all__ = [
    "WEEKLY_MOONCAKE_BADGE_IDS",
    "WEEKLY_MOONCAKE_FRAME_ID",
    "WEEKLY_MOONCAKE_STAMP_ID",
    "WEEKLY_MOONCAKE_MEDAL_ID",
    "WEEKLY_MOONCAKE_TITLE_ID",
    "WEEKLY_COMPETITION_DEFINITIONS",
    "WEEKLY_COMPETITIONS_BY_KEY",
    "WEEKLY_COMPETITIONS_BY_SEASON",
    "WEEKLY_REWARD_NAMES",
    "WEEKLY_SPRINT_BADGE_IDS",
    "WEEKLY_SPRINT_FRAME_ID",
    "WEEKLY_SPRINT_TITLE_ID",
    "WEEKLY_SUSHI_BADGE_IDS",
    "WEEKLY_SUSHI_FRAME_ID",
    "WEEKLY_SUSHI_MEDAL_ID",
    "WEEKLY_SUSHI_TITLE_ID",
    "WeeklyAggregation",
    "WeeklyCompetitionDefinition",
    "WeeklyReward",
    "WeeklyRewardTier",
    "WeeklySortDirection",
]
