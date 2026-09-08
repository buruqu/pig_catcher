"""Adaptive wheel helpers with explicit observation snapshots and replay boundaries."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from fractions import Fraction

from .battle_catalog import FIGHTERS_BY_ID, MOVE_WEIGHT_SCALE, Move


def effective_fighter(player):
    return player.get("miumiu_wheel_fighter_id") or player["snapshot"]["fighter_id"]


def move_type(event):
    tags = event.get("functional_tags", event.get("tags", ()))
    if "domain" in tags:
        return "domain"
    if event.get("opponent_reduction", 0):
        return "mixed" if event.get("gain", 0) > 0 else "reduction"
    return "gain" if event.get("gain", 0) > 0 else "functional"


def magnitude(event):
    return max(abs(Fraction(event.get("gain", 0))), abs(Fraction(event.get("opponent_reduction", 0))))


def observe(player):
    events = deepcopy(player["turn"].get("events", []))
    numeric = [event for event in events if magnitude(event) > 0]
    top = max(numeric, key=magnitude, default=None)
    return {
        "events": events,
        "top": top,
        "weight": Fraction(player["weight"]),
        "positive": sum(max(Fraction(0), Fraction(event.get("gain", 0))) for event in events),
        "special": any(
            event.get("extra_draws")
            or event.get("loan")
            or event.get("subwheel")
            or "domain" in event.get("tags", ())
            or event.get("form_before") != event.get("form_after")
            for event in events
        ),
    }


def prepare_move(player, move):
    """Compare against the common base-round preview; never opponent arrival order."""
    if "miumiu" not in move.tags:
        return move
    turn = player["turn"]
    if turn.get("miumiu_preview"):
        return move
    seen = turn.get("miumiu_observed", {"events": [], "top": None, "weight": 5, "positive": 0, "special": False})
    own = observe(player)
    gain = Fraction(move.resolved_gain_tenths, 10)
    bonus = Fraction(0)
    extra = 0
    top = seen["top"]
    slug = move.move_id.removeprefix("miumiu-")
    if slug == "water-life" and player["weight"] < seen["weight"]:
        bonus += 7
        if not turn.get("miumiu_lag_draw_used"):
            extra = 1
            turn["miumiu_lag_draw_used"] = True
    elif slug == "softening" and any(e.get("gain", 0) > e.get("base", e.get("gain", 0)) for e in own["events"]):
        bonus += 5
    elif slug == "coupling":
        bonus += min(Fraction(9), Fraction(seen["positive"]) * Fraction(3, 10)) if seen["positive"] > 0 else 4
    elif slug == "shallow":
        if top and magnitude(top) > 20:
            bonus += 6
        total = max(Fraction(1, 10), Fraction(player["weight"]) + seen["weight"])
        if (seen["weight"] - player["weight"]) / total >= Fraction(1, 5):
            bonus += 4
    elif slug == "mimic" and top:
        candidates = [e for e in own["events"] if magnitude(e) and move_type(e) == move_type(top)]
        if candidates:
            target = max(candidates, key=magnitude)
            actual = next(e for e in turn["events"] if e["ordinal"] == target["ordinal"])
            actual["gain"] += 8
            actual["miumiu_mimic_bonus"] = actual.get("miumiu_mimic_bonus", 0) + 8
            for event in turn["events"]:
                if event["ordinal"] >= actual["ordinal"]:
                    event["total"] += 8
            player["weight"] += 8
    elif slug == "rebuild":
        types = Counter(move_type(e) for e in seen["events"])
        player["miumiu_rebuild"] = types.most_common(1)[0][0] if types else "numeric"
    elif slug == "adapt":
        bonus += 6 if seen["special"] else 0
        if player["weight"] + gain + bonus < seen["weight"]:
            bonus += 4
    elif slug == "domain" and top:
        own_top = own["top"]
        bonus += min(Fraction(18), max(magnitude(top), magnitude(own_top) if own_top else 0) / 2)
    # Keep fractional bonuses exactly; apply_move accepts Fraction tenths.
    return replace(move, gain_tenths=(gain + bonus) * 10, draws=move.draws + extra)


def frozen_moves(player):
    entries = player.get("miumiu_wheel")
    if not entries:
        return None
    return tuple(Move(**{**entry, "tags": tuple(entry["tags"])}) for entry in entries)


def draw_bonus(player, move):
    kind = player.get("miumiu_rebuild")
    event = {
        "gain": move.resolved_gain_tenths,
        "opponent_reduction": move.resolved_opponent_reduction_tenths,
        "tags": move.tags,
    }
    if kind == "numeric" and magnitude(event):
        return 3000
    return 6000 if kind and kind == move_type(event) else 0


def flow_settlement(state):
    snapshots = state.get("miumiu_observations") or [observe(p) for p in state["sides"]]
    records = []
    for side, player in enumerate(state["sides"]):
        top = snapshots[1 - side]["top"]
        if top is None:
            continue
        for event in player["turn"]["events"]:
            if event.get("functional_move_id", event["move_id"]) != "miumiu-flow" or event.get("miumiu_flow_settled"):
                continue
            bonus = max(Fraction(5), min(Fraction(10), magnitude(top) / 4))
            event["gain"] += bonus
            event["miumiu_flow_bonus"] = bonus
            event["miumiu_flow_settled"] = True
            for later in player["turn"]["events"]:
                if later["ordinal"] >= event["ordinal"]:
                    later["total"] += bonus
            player["weight"] += bonus
            records.append({"side": side, "ordinal": event["ordinal"], "bonus": bonus})
    return records


def activate_blank(state, side, available_moves, weights):
    """Restore round origin, clear scores, then install only the copied move system."""
    old = deepcopy(state["sides"])
    origin = deepcopy(state["round_origin"])
    counts = [p["turn"]["effective"] for p in old]
    for index, player in enumerate(origin):
        # Keep already-activated transformations in a dual-mirror round.
        for key in (
            "miumiu_mode",
            "miumiu_wheel",
            "miumiu_wheel_fighter_id",
            "miumiu_domain_triggers",
            "miumiu_exhaust_guard",
            "miumiu_blanks",
        ):
            if key in old[index]:
                player[key] = deepcopy(old[index][key])
        player["weight"] = Fraction(0)
        player["round_start_weight"] = Fraction(0)
        player["round_gains"] = []
        turn = player["turn"]
        turn.update(
            raw=old[index]["turn"]["raw"],
            effective=counts[index],
            pending=counts[index],
            debt=old[index]["turn"]["debt"],
            done=counts[index] == 0,
            draws=old[index]["turn"]["draws"],
            events=[],
        )
        # Count roll's debt and bonus consumption is retained; action effects are not.
        player["next_debt"] = 0
        player["next_action_bonus"] = 0
    player = origin[side]
    player["miumiu_mode"] = True
    player["miumiu_exhaust_guard"] = True
    player["miumiu_wheel_fighter_id"] = effective_fighter(old[1 - side])
    player["miumiu_wheel"] = [
        asdict(replace(move, draw_weight_units=weight)) for move, weight in zip(available_moves, weights, strict=True)
    ]
    # Forms and progression are local, never copied from the opponent.
    fighter = FIGHTERS_BY_ID[player["miumiu_wheel_fighter_id"]]
    if fighter.fighter_id == "juejue":
        player["juejue_form"] = "time-sand"
        player["juejue_mimic_pool"] = deepcopy(state.get("mimic_pool", {"large": [], "small": []}))
    elif fighter.fighter_id == "daniya":
        player["daniya_form"] = "staging"
    elif fighter.fighter_id == "firefly":
        player["firefly_form"] = "firefly"
    state["sides"] = origin
    state["miumiu_epoch"] = int(state.get("miumiu_epoch", 0)) + 1
    state.pop("miumiu_observations", None)
    state["round_origin"] = deepcopy(origin)
    return {"side": side, "old_sides": old, "counts": counts, "epoch": state["miumiu_epoch"]}


def add_blank_slots(state):
    for side, player in enumerate(state["sides"]):
        if player.get("miumiu_mode"):
            target = state["sides"][1 - side]
            target.setdefault("miumiu_blanks", []).append(max(1, player.get("miumiu_domain_triggers", 1)) * 2)


def blank_moves(player):
    return tuple(
        Move(f"miumiu-noop-{index}", "\u2800", draw_weight_units=int(weight * MOVE_WEIGHT_SCALE), tags=("miumiu-noop",))
        for index, weight in enumerate(player.get("miumiu_blanks", []))
    )
