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


@dataclass(frozen=True, slots=True)
class CatchQuotaLayers:
    """Explicitly stack every normal catch-quota source.

    Six-star dedicated catches are intentionally absent because they bypass the
    normal quota instead of increasing it.
    """

    configured_base: int
    permanent_bonus: int
    weekly_bonus: int
    current_window_bonus: int
    today_window_bonus: int
    extra_granted: int
    extra_consumed: int

    @property
    def base_window_limit(self) -> int:
        return sum(
            (
                self.configured_base,
                self.permanent_bonus,
                self.weekly_bonus,
                self.current_window_bonus,
                self.today_window_bonus,
            )
        )

    def effective_limit(self, *, used_count: int) -> int:
        return effective_catch_limit(
            base_limit=self.base_window_limit,
            used_count=used_count,
            extra_granted=self.extra_granted,
            extra_consumed=self.extra_consumed,
        )


def stack_catch_quota_layers(
    *,
    configured_base: int,
    permanent_bonus: int = 0,
    weekly_bonus: int = 0,
    current_window_bonus: int = 0,
    today_window_bonus: int = 0,
    extra_granted: int = 0,
    extra_consumed: int = 0,
) -> CatchQuotaLayers:
    """Build a non-negative, auditable normal-quota stack."""

    values = {
        "configured_base": configured_base,
        "permanent_bonus": permanent_bonus,
        "weekly_bonus": weekly_bonus,
        "current_window_bonus": current_window_bonus,
        "today_window_bonus": today_window_bonus,
        "extra_granted": extra_granted,
        "extra_consumed": extra_consumed,
    }
    normalized = {name: max(0, int(value)) for name, value in values.items()}
    return CatchQuotaLayers(**normalized)


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


def effective_catch_limit(
    *,
    base_limit: int,
    used_count: int,
    extra_granted: int,
    extra_consumed: int,
) -> int:
    """返回当前有效窗口还能真实达到的抓猪次数上限。

    额外次数按北京时间自然日发放和消费，而基础次数会在日内换段或手工重置后
    重新计数。分母既要保留本窗口已经用掉的额外次数，也不能把旧窗口已经消费的
    额外次数重新显示成可用额度。
    """

    normalized_base = max(0, int(base_limit))
    normalized_used = max(0, int(used_count))
    normalized_granted = max(0, int(extra_granted))
    normalized_consumed = max(0, int(extra_consumed))
    remaining_extra = max(0, normalized_granted - normalized_consumed)
    return max(normalized_base, normalized_used) + remaining_extra


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
