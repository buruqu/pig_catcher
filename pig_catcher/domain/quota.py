"""北京时间分时抓猪额度窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone

from .errors import DomainValidationError

_BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class CatchQuotaWindow:
    """一次独立计数和冷却周期。"""

    start: datetime
    end: datetime
    label: str
    next_refresh_label: str


def normalize_quota_refresh_hours(hours: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    """校验并规范化每天的整点刷新时刻。"""

    normalized = tuple(sorted({int(hour) for hour in hours}))
    if not normalized:
        raise DomainValidationError("抓猪额度刷新时间不能为空。")
    if any(hour < 0 or hour > 23 for hour in normalized):
        raise DomainValidationError("抓猪额度刷新小时必须位于 0 至 23。")
    if len(normalized) != len(hours):
        raise DomainValidationError("抓猪额度刷新小时不能重复。")
    if 0 not in normalized:
        raise DomainValidationError("抓猪额度刷新时间必须包含 00:00。")
    return normalized


def catch_quota_window(
    now: datetime,
    *,
    refresh_hours: list[int] | tuple[int, ...],
    timezone_name: str,
) -> CatchQuotaWindow:
    """返回当前时刻所属的额度窗口及下一次刷新时间。"""

    if timezone_name != "Asia/Shanghai":
        raise DomainValidationError("额度刷新时区当前只支持 Asia/Shanghai。")
    normalized_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    local_now = normalized_now.astimezone(_BEIJING_TIMEZONE)
    hours = normalize_quota_refresh_hours(refresh_hours)
    current_hour = max(hour for hour in hours if hour <= local_now.hour)
    local_start = datetime.combine(
        local_now.date(),
        time(hour=current_hour),
        tzinfo=_BEIJING_TIMEZONE,
    )
    later_hours = tuple(hour for hour in hours if hour > current_hour)
    if later_hours:
        local_end = datetime.combine(
            local_now.date(),
            time(hour=later_hours[0]),
            tzinfo=_BEIJING_TIMEZONE,
        )
    else:
        local_end = datetime.combine(
            local_now.date() + timedelta(days=1),
            time(hour=hours[0]),
            tzinfo=_BEIJING_TIMEZONE,
        )
    label = f"{local_start:%m-%d %H:%M}–{local_end:%m-%d %H:%M}"
    next_refresh_label = f"{local_end:%m月%d日 %H:%M}"
    return CatchQuotaWindow(
        start=local_start.astimezone(UTC),
        end=local_end.astimezone(UTC),
        label=label,
        next_refresh_label=next_refresh_label,
    )
