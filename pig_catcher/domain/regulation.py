"""赠送与成交交易共用的确定性资产流向图分析。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

AccountTrustTier = Literal["established", "regular", "likely-alt", "unknown"]


@dataclass(frozen=True, slots=True)
class AccountActivityProfile:
    """Deterministic account-maturity snapshot without reading message content."""

    player_id: str
    tier: AccountTrustTier
    chat_history_available: bool
    chat_message_count: int
    chat_active_day_count: int
    plugin_age_days: int
    game_action_count: int


def classify_account_activity(
    *,
    player_id: str,
    chat_history_available: bool,
    chat_message_count: int,
    chat_active_day_count: int,
    plugin_age_days: int,
    game_action_count: int,
    established_min_messages: int,
    established_min_active_days: int,
    likely_alt_max_messages: int,
    likely_alt_max_active_days: int,
    likely_alt_max_plugin_age_days: int,
    likely_alt_max_game_actions: int,
) -> AccountActivityProfile:
    """Classify one account using counts and dates only.

    Missing chat history never makes an account a likely alt and never grants
    the established-account mutual-gift relaxation.
    """

    messages = max(0, int(chat_message_count))
    chat_days = max(0, int(chat_active_day_count))
    plugin_days = max(0, int(plugin_age_days))
    actions = max(0, int(game_action_count))
    if not chat_history_available:
        tier: AccountTrustTier = "unknown"
    elif (
        messages >= int(established_min_messages)
        and chat_days >= int(established_min_active_days)
    ):
        tier = "established"
    elif (
        messages <= int(likely_alt_max_messages)
        and chat_days <= int(likely_alt_max_active_days)
        and plugin_days <= int(likely_alt_max_plugin_age_days)
        and actions <= int(likely_alt_max_game_actions)
    ):
        tier = "likely-alt"
    else:
        tier = "regular"
    return AccountActivityProfile(
        player_id=str(player_id),
        tier=tier,
        chat_history_available=bool(chat_history_available),
        chat_message_count=messages,
        chat_active_day_count=chat_days,
        plugin_age_days=plugin_days,
        game_action_count=actions,
    )


@dataclass(frozen=True, slots=True)
class TransferSignal:
    """一条资产或异常猪币收益方向，不包含显示名等可变身份信息。"""

    event_id: str
    from_player_id: str
    to_player_id: str
    asset_key: str
    channel: str
    transfer_type: str
    created_at: datetime
    rarity: int
    official_value: int
    price: int | None


@dataclass(frozen=True, slots=True)
class RegulationAnalysis:
    """一组至少含两个主动上游账号的可解释监管候选。"""

    score: int
    target_player_ids: tuple[str, ...]
    upstream_player_ids: tuple[str, ...]
    source_player_ids: tuple[str, ...]
    relay_player_ids: tuple[str, ...]
    path_event_ids: tuple[str, ...]
    unique_asset_count: int
    concentration_percent: float
    active_day_count: int
    max_path_depth: int
    high_rarity_asset_count: int
    six_star_asset_count: int
    price_anomaly_level: str
    strict_source_count: int = 0


def relax_established_mutual_gifts(
    signals: tuple[TransferSignal, ...],
    *,
    established_player_ids: frozenset[str],
) -> tuple[tuple[TransferSignal, ...], tuple[str, ...]]:
    """Exclude reciprocal gifts between two established chat accounts.

    Trades, one-way gifts, and any transfer involving a non-established account
    remain visible to the graph. The returned event ids are retained as internal
    audit evidence by the service layer.
    """

    directions = {
        (signal.from_player_id, signal.to_player_id)
        for signal in signals
        if signal.channel == "asset"
        and signal.transfer_type == "gift"
        and signal.from_player_id in established_player_ids
        and signal.to_player_id in established_player_ids
    }
    relaxed_ids = {
        signal.event_id
        for signal in signals
        if signal.channel == "asset"
        and signal.transfer_type == "gift"
        and signal.from_player_id in established_player_ids
        and signal.to_player_id in established_player_ids
        and (signal.to_player_id, signal.from_player_id) in directions
    }
    return (
        tuple(signal for signal in signals if signal.event_id not in relaxed_ids),
        tuple(sorted(relaxed_ids)),
    )


def _weak_component(
    signals: tuple[TransferSignal, ...],
    start_nodes: tuple[str, ...],
) -> set[str]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for signal in signals:
        neighbors[signal.from_player_id].add(signal.to_player_id)
        neighbors[signal.to_player_id].add(signal.from_player_id)
    component: set[str] = set()
    queue = deque(start_nodes)
    while queue:
        player_id = queue.popleft()
        if player_id in component:
            continue
        component.add(player_id)
        queue.extend(neighbors[player_id] - component)
    return component


def _reverse_distances(
    signals: tuple[TransferSignal, ...],
    targets: tuple[str, ...],
    *,
    max_depth: int = 8,
) -> dict[str, int]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for signal in signals:
        reverse[signal.to_player_id].add(signal.from_player_id)
    distances = {target: 0 for target in targets}
    queue = deque(targets)
    while queue:
        current = queue.popleft()
        depth = distances[current]
        if depth >= max_depth:
            continue
        for predecessor in reverse[current]:
            candidate_depth = depth + 1
            if candidate_depth >= distances.get(predecessor, max_depth + 1):
                continue
            distances[predecessor] = candidate_depth
            queue.append(predecessor)
    return distances


def _targets_are_linked(
    signals: tuple[TransferSignal, ...],
    left: str,
    right: str,
) -> bool:
    # 双汇聚账号必须确实共享直接转出方。仅仅处在同一弱连通社交图、或通过
    # 若干无关资产间接相连，不足以把两个普通收取方合并为同一监管候选。
    left_senders = {
        signal.from_player_id
        for signal in signals
        if signal.to_player_id == left and signal.from_player_id != right
    }
    right_senders = {
        signal.from_player_id
        for signal in signals
        if signal.to_player_id == right and signal.from_player_id != left
    }
    return len(left_senders & right_senders) >= 2


def _price_anomaly(signal: TransferSignal) -> int:
    if signal.transfer_type != "trade" or signal.price is None:
        return 0
    if signal.official_value <= 0:
        return 0
    ratio = signal.price / signal.official_value
    if signal.channel == "asset":
        if ratio <= 0.25:
            return 2
        if ratio < 0.70:
            return 1
        return 0
    if signal.channel == "coin":
        if ratio > 3.0:
            return 2
        if ratio > 1.30:
            return 1
    return 0


def _score_upstream_count(count: int) -> int:
    if count >= 8:
        return 80
    if count >= 5:
        return 65
    if count >= 3:
        return 45
    return 30


def _trace_asset_paths(
    signals: tuple[TransferSignal, ...],
    targets: tuple[str, ...],
) -> tuple[TransferSignal, ...]:
    """只沿同一资产/猪到菜血缘回溯，避免把普通社交网络误连成中转链。"""

    target_set = set(targets)
    incoming: dict[tuple[str, str], list[TransferSignal]] = defaultdict(list)
    for signal in signals:
        incoming[(signal.to_player_id, signal.asset_key)].append(signal)
    for rows in incoming.values():
        rows.sort(key=lambda item: (item.created_at, item.event_id))
    stack = [signal for signal in signals if signal.to_player_id in target_set]
    selected: dict[str, TransferSignal] = {}
    while stack:
        signal = stack.pop()
        if signal.event_id in selected:
            continue
        selected[signal.event_id] = signal
        for predecessor in incoming.get(
            (signal.from_player_id, signal.asset_key),
            (),
        ):
            if predecessor.created_at <= signal.created_at:
                stack.append(predecessor)
    return tuple(
        sorted(selected.values(), key=lambda item: (item.created_at, item.event_id))
    )


def _analyze_targets(
    signals: tuple[TransferSignal, ...],
    targets: tuple[str, ...],
    anchor_event_ids: frozenset[str],
    strict_player_ids: frozenset[str],
) -> RegulationAnalysis | None:
    path_signals = _trace_asset_paths(signals, targets)
    if not path_signals or not (
        anchor_event_ids & {item.event_id for item in path_signals}
    ):
        return None
    distances = _reverse_distances(path_signals, targets)
    upstream = tuple(
        sorted(
            player_id
            for player_id, distance in distances.items()
            if player_id not in targets and distance > 0
        )
    )
    # 用户明确要求一对一完全豁免；至少两个主动上游账号才成立案件。
    if len(upstream) < 2:
        return None
    upstream_set = set(upstream)
    raw_sources: set[str] = set()
    for signal in path_signals:
        player_id = signal.from_player_id
        if player_id not in upstream_set:
            continue
        has_predecessor = any(
            predecessor.to_player_id == player_id
            and predecessor.asset_key == signal.asset_key
            and predecessor.created_at <= signal.created_at
            for predecessor in path_signals
        )
        if not has_predecessor:
            raw_sources.add(player_id)
    dedicated_sources: set[str] = set()
    repeated_sources: set[str] = set()
    for player_id in raw_sources:
        outgoing = tuple(
            signal for signal in signals if signal.from_player_id == player_id
        )
        toward_targets = tuple(
            signal for signal in path_signals if signal.from_player_id == player_id
        )
        source_concentration = (
            100.0 * len(toward_targets) / len(outgoing) if outgoing else 0.0
        )
        if source_concentration < 80.0:
            continue
        dedicated_sources.add(player_id)
        if len(toward_targets) >= 2:
            repeated_sources.add(player_id)
    strict_sources = dedicated_sources & strict_player_ids
    # 普通账号仍要求两个重复来源或三个集中来源；已被客观活跃度指标归为
    # 疑似小号的来源，只要有两个集中流向即可进入后续可解释评分。
    if (
        len(repeated_sources) < 2
        and len(dedicated_sources) < 3
        and len(strict_sources) < 2
    ):
        return None

    relevant_nodes = set(dedicated_sources)
    relevant_states: set[tuple[str, str]] = set()
    relevant_signals: list[TransferSignal] = []
    pending = list(path_signals)
    while pending:
        changed = False
        remaining: list[TransferSignal] = []
        for signal in pending:
            if (
                signal.from_player_id not in dedicated_sources
                and (signal.from_player_id, signal.asset_key)
                not in relevant_states
            ):
                remaining.append(signal)
                continue
            relevant_signals.append(signal)
            if signal.to_player_id not in targets:
                relevant_nodes.add(signal.to_player_id)
                relevant_states.add((signal.to_player_id, signal.asset_key))
            changed = True
        if not changed:
            break
        pending = remaining
    path_signals = tuple(relevant_signals)
    if not (anchor_event_ids & {item.event_id for item in path_signals}):
        return None
    relevant_upstream = relevant_nodes - set(targets)
    all_upstream_outgoing = tuple(
        signal for signal in signals if signal.from_player_id in relevant_upstream
    )
    concentration = (
        100.0 * len(path_signals) / len(all_upstream_outgoing)
        if all_upstream_outgoing
        else 0.0
    )
    asset_rarities: dict[str, int] = {}
    for signal in path_signals:
        if signal.channel == "asset":
            asset_rarities[signal.asset_key] = max(
                signal.rarity,
                asset_rarities.get(signal.asset_key, 0),
            )
    unique_units = {signal.asset_key for signal in path_signals}
    active_days = {signal.created_at.date() for signal in path_signals}
    max_depth = max(distances[player_id] for player_id in relevant_upstream)
    relevant_relays = {
        signal.to_player_id
        for signal in path_signals
        if signal.to_player_id in relevant_upstream
    }
    relays = tuple(sorted(relevant_relays))
    sources = tuple(sorted(dedicated_sources))
    high_rarity_count = sum(rarity >= 4 for rarity in asset_rarities.values())
    six_star_count = sum(rarity >= 6 for rarity in asset_rarities.values())
    anomaly = max((_price_anomaly(signal) for signal in path_signals), default=0)
    # 纯正常价格成交只留作图谱上下文，不单独触发监管；交易必须出现价格偏离，
    # 或与已经存在的赠送路径混合，才能成为处罚候选。
    if anomaly == 0 and not any(
        signal.transfer_type == "gift" for signal in path_signals
    ):
        return None

    score = _score_upstream_count(len(sources))
    if concentration >= 95.0:
        score += 25
    elif concentration >= 80.0:
        score += 15
    if len(unique_units) >= 30:
        score += 20
    elif len(unique_units) >= 12:
        score += 10
    elif len(unique_units) >= 6:
        score += 5
    if len(active_days) >= 4:
        score += 20
    elif len(active_days) >= 2:
        score += 10
    if max_depth >= 3:
        score += 20
    elif max_depth >= 2:
        score += 10
    if high_rarity_count >= 5 or six_star_count >= 2:
        score += 10
    if anomaly == 2:
        score += 15
    elif anomaly == 1:
        score += 8
    score += min(30, 15 * len(strict_sources))

    return RegulationAnalysis(
        score=score,
        target_player_ids=tuple(sorted(targets)),
        upstream_player_ids=tuple(sorted(relevant_upstream)),
        source_player_ids=sources,
        relay_player_ids=relays,
        path_event_ids=tuple(sorted({signal.event_id for signal in path_signals})),
        unique_asset_count=len(unique_units),
        concentration_percent=round(concentration, 2),
        active_day_count=len(active_days),
        max_path_depth=max_depth,
        high_rarity_asset_count=high_rarity_count,
        six_star_asset_count=six_star_count,
        price_anomaly_level={0: "none", 1: "moderate", 2: "severe"}[anomaly],
        strict_source_count=len(strict_sources),
    )


def analyze_transfer_graph(
    signals: tuple[TransferSignal, ...],
    *,
    anchor_event_ids: frozenset[str],
    strict_player_ids: frozenset[str] = frozenset(),
) -> RegulationAnalysis | None:
    """返回包含本次操作且得分最高的单/双汇聚账号候选。"""

    anchors = tuple(signal for signal in signals if signal.event_id in anchor_event_ids)
    if not anchors:
        return None
    candidates: list[RegulationAnalysis] = []
    for anchor in anchors:
        component_nodes = _weak_component(
            signals,
            (anchor.from_player_id, anchor.to_player_id),
        )
        component_signals = tuple(
            signal
            for signal in signals
            if signal.from_player_id in component_nodes
            and signal.to_player_id in component_nodes
        )
        primary_target = anchor.to_player_id
        single = _analyze_targets(
            component_signals,
            (primary_target,),
            anchor_event_ids,
            strict_player_ids,
        )
        if single is not None:
            candidates.append(single)
            # 单汇聚路径已经可以独立解释本次动作时，保持案件目标稳定，避免
            # 再把普通互动中的第二个收取方拼入案件造成签名漂移。
            continue
        net_inflow: dict[str, int] = defaultdict(int)
        for signal in component_signals:
            net_inflow[signal.to_player_id] += 1
            net_inflow[signal.from_player_id] -= 1
        companion_targets = tuple(
            sorted(
                player_id
                for player_id in component_nodes
                if player_id != primary_target and net_inflow[player_id] > 0
            )
        )
        for companion in companion_targets:
            if not _targets_are_linked(component_signals, primary_target, companion):
                continue
            paired = _analyze_targets(
                component_signals,
                tuple(sorted((primary_target, companion))),
                anchor_event_ids,
                strict_player_ids,
            )
            if paired is not None:
                candidates.append(paired)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.score,
            len(item.upstream_player_ids),
            item.unique_asset_count,
            -len(item.target_player_ids),
            item.target_player_ids,
        ),
    )


__all__ = [
    "AccountActivityProfile",
    "AccountTrustTier",
    "RegulationAnalysis",
    "TransferSignal",
    "analyze_transfer_graph",
    "classify_account_activity",
    "relax_established_mutual_gifts",
]
