"""Deterministic launch-campaign time rules shared by gameplay and rewards."""

from __future__ import annotations

from datetime import UTC, datetime

from ..config.model import LaunchCampaignSection
from .errors import DomainValidationError


def aware_datetime(value: datetime) -> datetime:
    """Normalize a clock value to an aware UTC datetime."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def campaign_timestamp(value: str) -> datetime:
    """Parse one frozen ISO-8601 campaign timestamp and reject ambiguous values."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainValidationError("开服活动时间配置不是有效的 ISO-8601 时间。") from exc
    if parsed.tzinfo is None:
        raise DomainValidationError("开服活动时间必须包含时区。")
    return parsed.astimezone(UTC)


def campaign_started(config: LaunchCampaignSection, value: datetime) -> bool:
    return bool(config.enabled and aware_datetime(value) >= campaign_timestamp(config.starts_at))


def first_day_active(config: LaunchCampaignSection, value: datetime) -> bool:
    if not config.enabled:
        return False
    now = aware_datetime(value)
    return campaign_timestamp(config.starts_at) <= now < campaign_timestamp(config.first_day_ends_at)


def effective_window_limit(
    config: LaunchCampaignSection,
    value: datetime,
    *,
    normal_limit: int,
) -> int:
    return int(config.first_day_window_limit) if first_day_active(config, value) else int(normal_limit)


def apply_first_day_high_star_weights(
    weights: tuple[float, ...],
    config: LaunchCampaignSection,
    value: datetime,
) -> tuple[float, ...]:
    """Multiply raw 4/5/6-star weights and normalize back to 100 percent."""

    if not first_day_active(config, value):
        return weights
    if len(weights) != 6:
        raise DomainValidationError("开服概率规则需要六档品质权重。")
    multiplier = float(config.first_day_high_star_multiplier)
    adjusted = tuple(weight * (multiplier if index >= 3 else 1.0) for index, weight in enumerate(weights))
    total = sum(adjusted)
    if total <= 0:
        raise DomainValidationError("开服概率规则没有可用权重。")
    return tuple(value * 100.0 / total for value in adjusted)


__all__ = [
    "apply_first_day_high_star_weights",
    "campaign_started",
    "campaign_timestamp",
    "effective_window_limit",
    "first_day_active",
]
