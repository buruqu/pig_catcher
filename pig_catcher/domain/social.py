"""Pure rules for body-scale labels, social transfers, and rankings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .enums import StatureProfile, TradeStatus
from .errors import DomainValidationError

RANKING_TYPES: tuple[str, ...] = (
    "综合",
    "抓猪",
    "美食",
    "价值",
    "巨物",
    "数量",
    "猪币",
)
TRADE_STATUS_LABELS: dict[TradeStatus, str] = {
    TradeStatus.PENDING: "待处理",
    TradeStatus.ACCEPTED: "已完成",
    TradeStatus.REJECTED: "已拒绝",
    TradeStatus.CANCELLED: "已取消",
    TradeStatus.EXPIRED: "已过期",
}
TRADE_STATUS_BY_LABEL = {
    label: status for status, label in TRADE_STATUS_LABELS.items()
}
_TRADE_ID_PATTERN = re.compile(r"^[A-F0-9]{8}$")


@dataclass(frozen=True, slots=True)
class BodyScale:
    """Deterministic body-scale annotation for one immutable pig."""

    label: str
    description: str
    size_qualified: bool
    weight_qualified: bool
    giant_score: float

    @property
    def is_giant_sighting(self) -> bool:
        return self.size_qualified or self.weight_qualified


def giant_score(
    *,
    size_value: float,
    weight_value: float,
    giant_size_threshold_cm: float,
    giant_weight_threshold_kg: float,
) -> float:
    """Return an uncapped absolute score where both thresholds equal 100 points."""

    if giant_size_threshold_cm <= 0 or giant_weight_threshold_kg <= 0:
        raise DomainValidationError("巨物体型和重量门槛必须大于零。")
    score = 100.0 * (
        0.55 * float(size_value) / giant_size_threshold_cm
        + 0.45 * float(weight_value) / giant_weight_threshold_kg
    )
    return round(score, 6)


def describe_body_scale(
    *,
    stature_profile: StatureProfile | str,
    size_value: float,
    size_percentile: float,
    weight_value: float,
    weight_percentile: float,
    giant_size_threshold_cm: float,
    giant_weight_threshold_kg: float,
) -> BodyScale:
    """Classify special templates and statistically extreme ordinary individuals."""

    profile = StatureProfile(str(stature_profile or StatureProfile.STANDARD.value))
    size_qualified = float(size_value) >= giant_size_threshold_cm
    weight_qualified = float(weight_value) >= giant_weight_threshold_kg
    score = giant_score(
        size_value=size_value,
        weight_value=weight_value,
        giant_size_threshold_cm=giant_size_threshold_cm,
        giant_weight_threshold_kg=giant_weight_threshold_kg,
    )

    if size_qualified and weight_qualified:
        return BodyScale(
            label="双项巨物",
            description="体型与重量同时越过全群巨物线，称重台已提交加固申请。",
            size_qualified=True,
            weight_qualified=True,
            giant_score=score,
        )
    if size_qualified:
        return BodyScale(
            label="长体巨物",
            description="体型越过全群巨物线，尾巴进场时猪鼻已经开始称重。",
            size_qualified=True,
            weight_qualified=False,
            giant_score=score,
        )
    if weight_qualified:
        return BodyScale(
            label="重量级巨物",
            description="重量越过全群巨物线，电子秤读数停顿了一下才敢继续。",
            size_qualified=False,
            weight_qualified=True,
            giant_score=score,
        )
    if profile is StatureProfile.MINI:
        return BodyScale(
            label="袖珍品种",
            description="天生迷你，站进称重盘后还给旁边空出了一大块位置。",
            size_qualified=False,
            weight_qualified=False,
            giant_score=score,
        )
    if profile is StatureProfile.GIANT:
        return BodyScale(
            label="巨型品种",
            description="天生大体格，普通围栏对它来说更像一条礼貌提示线。",
            size_qualified=False,
            weight_qualified=False,
            giant_score=score,
        )
    if size_percentile <= 0.08 and weight_percentile <= 0.15:
        return BodyScale(
            label="迷你个体",
            description="同品种里难得的小个子，转身时几乎没有占用群聊空间。",
            size_qualified=False,
            weight_qualified=False,
            giant_score=score,
        )
    if size_percentile >= 0.92 and weight_percentile >= 0.88:
        return BodyScale(
            label="壮硕个体",
            description="同品种体型和重量都很靠前，已经有了巨物候选的气势。",
            size_qualified=False,
            weight_qualified=False,
            giant_score=score,
        )
    if size_percentile >= 0.95:
        return BodyScale(
            label="修长个体",
            description="同品种体型格外突出，横着站时需要多借半个镜头。",
            size_qualified=False,
            weight_qualified=False,
            giant_score=score,
        )
    if weight_percentile >= 0.95:
        return BodyScale(
            label="沉甸甸个体",
            description="同品种重量格外突出，落秤的声音很有说服力。",
            size_qualified=False,
            weight_qualified=False,
            giant_score=score,
        )
    return BodyScale(
        label="",
        description="",
        size_qualified=False,
        weight_qualified=False,
        giant_score=score,
    )


def normalize_trade_id(value: str) -> str:
    """Validate the copyable eight-character trade number."""

    normalized = str(value or "").strip().upper()
    if not _TRADE_ID_PATTERN.fullmatch(normalized):
        raise DomainValidationError("交易号必须是 8 位十六进制字符。")
    return normalized


def normalize_ranking_type(value: str) -> str:
    """Normalize an omitted ranking type to the comprehensive board."""

    normalized = str(value or "").strip() or "综合"
    if normalized not in RANKING_TYPES:
        raise DomainValidationError(
            f"排行类型只能是：{'、'.join(RANKING_TYPES)}。"
        )
    return normalized


def trade_status_from_label(value: str) -> TradeStatus | None:
    """Map Chinese query labels to persisted statuses; 全部 maps to no filter."""

    normalized = str(value or "").strip() or "全部"
    if normalized == "全部":
        return None
    try:
        return TRADE_STATUS_BY_LABEL[normalized]
    except KeyError as exc:
        choices = "、".join(("全部", *TRADE_STATUS_BY_LABEL))
        raise DomainValidationError(f"交易状态只能是：{choices}。") from exc
