"""流萤 Battle v18 的对手条件与跨回合事实，全部使用精确有理数。"""

from fractions import Fraction


def event_context(state, side, event):
    player, target = state["sides"][side], state["sides"][1 - side]
    turn = player["turn"]
    before = min(3, Fraction(target.get("firefly_collapse", 0)) + Fraction(turn.get("firefly_outgoing_collapse", 0)))
    move_id = event["move_id"]
    extra = Fraction(0)
    risk = Fraction(0)
    if event.get("firefly_sam_skill"):
        extra = before * 5
        if move_id == "sam-bottom-fire-slash":
            extra += before * 5
            if event["firefly_sam_skill_index_before"] == 0:
                event["opponent_reduction"] += 8
                event["firefly_first_sam_reduction"] = 8
        elif move_id == "sam-deathstar-overload":
            if before >= 2:
                event["opponent_next_debt"] += 1
                event["firefly_collapse_debt_triggered"] = True
            if before == 3:
                risk += Fraction(3, 2)
        elif move_id == "sam-ignite-star-sea" and event.get("firefly_entered_sam"):
            if before < 3 <= before + event["firefly_collapse_to_add"]:
                risk += Fraction(3, 2)
    if event.get("firefly_echo") and before >= 2:
        scale = Fraction(event.get("firefly_echo_scale", 1))
        if move_id == "firefly-dream-destination":
            risk += Fraction(3, 2) * scale
        elif move_id == "firefly-silent-galaxy":
            risk += scale
    if extra:
        player["weight"] += extra
        event["gain"] += extra
        event["has_numeric_contribution"] = True
    added = Fraction(event.get("firefly_collapse_to_add", 0))
    turn["firefly_outgoing_collapse"] = min(3, Fraction(turn.get("firefly_outgoing_collapse", 0)) + added)
    event.update(
        firefly_target_collapse_before=before,
        firefly_target_collapse_after_pending=min(3, before + added),
        firefly_collapse_passive_gain=extra,
        firefly_opponent_exhaust_delta_units=risk,
        total=player["weight"],
    )


def prepare_starfield(state, cancelled, protected, *, remaining, reduce):
    """使用双方完整招式的同一快照，避免真实消息先后改变条件判定。"""
    sides = state["sides"]
    weights = [Fraction(p["weight"]) - sum(e["gain"] for e in cancelled[i].values()) for i, p in enumerate(sides)]
    records = []
    for side, player in enumerate(sides):
        target = 1 - side
        player["turn"]["firefly_condition_target_has_gain"] = weights[target] > sides[target]["round_start_weight"]
        player["turn"]["firefly_condition_target_higher"] = weights[target] > weights[side]
        sources = [e for e in player["turn"]["events"] if e.get("firefly_starfield")]
        factor = Fraction(1, 2) ** len(sources) if target not in protected else Fraction(1)
        sides[target]["turn"]["firefly_positive_gain_factor"] = factor
        if factor == 1:
            continue
        for event in sides[target]["turn"]["events"]:
            value = max(Fraction(0), remaining(cancelled, target, event))
            if value:
                amount = reduce(cancelled, target, event, value * (1 - factor), "沉睡在静默的星河")
                records.append(
                    {
                        "source_side": side,
                        "target_side": target,
                        "ordinal": event["ordinal"],
                        "factor": factor,
                        "deduction": amount,
                    }
                )
    return records


def starfield_followups(state, baseline, domain):
    """领域翻倍已使用折后招式；再处理不属于招式事件的领域追加正收益。"""
    records = []
    for side, player in enumerate(state["sides"]):
        factor = Fraction(player["turn"].get("firefly_positive_gain_factor", 1))
        if factor == 1:
            continue
        doubled = Fraction((domain or {}).get("bonus_gain", 0)) if (domain or {}).get("boost_side") == side else 0
        extra = max(Fraction(0), Fraction(player["weight"]) - baseline[side] - doubled)
        deduction = extra * (1 - factor)
        if deduction:
            player["weight"] -= deduction
            records.append(
                {
                    "source_side": 1 - side,
                    "target_side": side,
                    "ordinal": None,
                    "factor": factor,
                    "deduction": deduction,
                }
            )
    return records


def domain_continuation(state):
    records = []
    for side, player in enumerate(state["sides"]):
        count = sum(bool(e.get("firefly_domain_followup")) for e in player["turn"]["events"])
        if not count:
            continue
        extended = player.get("firefly_form") == "sam"
        if extended:
            player["firefly_sam_rounds_remaining"] += count
        player["next_action_bonus"] += count
        target = state["sides"][1 - side]
        target["firefly_collapse_next"] = int(target.get("firefly_collapse_next", 0)) + count
        records.append({"side": side, "count": count, "extended_sam": extended})
    return records


def begin_next_round(state):
    records = []
    for side, player in enumerate(state["sides"]):
        added = int(player.pop("firefly_collapse_next", 0))
        if added:
            before = Fraction(player.get("firefly_collapse", 0))
            player["firefly_collapse"] = min(3, before + added)
            records.append({"side": side, "before": before, "after": player["firefly_collapse"], "added": added})
    return records


def collapse_risk_units(layers):
    """0/1/2/3层对应0/.08/.18/.35；半层线性插值，返回伤势盘十倍刻度。"""
    value = min(Fraction(3), max(Fraction(0), Fraction(layers)))
    knots = (Fraction(0), Fraction(4, 5), Fraction(9, 5), Fraction(7, 2))
    lower = int(value)
    if lower == 3:
        return knots[3]
    return knots[lower] + (knots[lower + 1] - knots[lower]) * (value - lower)
