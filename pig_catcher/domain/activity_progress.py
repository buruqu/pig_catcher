"""Incremental factual reducers for the three-system achievement pack.

Only committed outbox facts enter here. Battle observations are provisional
until a natural ending. Neither display names nor current asset ownership are
evidence of somebody's past participation or expenditure.
"""

from __future__ import annotations

from itertools import combinations

from .activity_achievements import FIXED_SETS
from .dispatch import MATERIAL_SCALE
from .tour_catalog import CHARACTERS

MAIN_MATERIALS = frozenset(("training-ore", "machine-parts", "agility-fiber", "stage-components"))
THEME_ALIASES = {
    "poppin": "poppin-party",
    "pastel": "pastel-palettes",
    "hhw": "hello-happy-world",
    "ras": "raise-a-suilen",
    "mujica": "ave-mujica",
}
VENUE_ALIASES = {"campus": "school-festival", "theatre": "city-theatre", "dome": "dream-dome"}
MOVE_ALIASES = {
    "sukuna": dict(
        zip(
            ("black-flash", "dismantle", "cleave", "furnace", "shrine", "loan", "reverse", "elbow", "net"),
            (
                "black-flash",
                "sukuna-dismantle",
                "sukuna-cleave",
                "sukuna-furnace",
                "sukuna-domain",
                "binding-loan",
                "reverse-repair",
                "elbow-strike",
                "grid-slash",
            ),
            strict=True,
        )
    ),
    "gojo": dict(
        zip(
            (
                "blue",
                "red",
                "blue-fist",
                "defense",
                "black-flash",
                "teleport",
                "purple",
                "void",
                "reverse",
                "unlimited-purple",
            ),
            (
                "gojo-blue",
                "gojo-red",
                "gojo-blue-punch",
                "infinity-defense",
                "black-flash",
                "infinity-teleport",
                "gojo-purple",
                "gojo-domain",
                "reverse-repair",
                "gojo-unlimited-purple",
            ),
            strict=True,
        )
    ),
}
MOVE_ALIASES["sukuna"]["world-cutting-slash"] = "world-cutting-slash"


def add(state: dict, metric: str, amount: int = 1) -> None:
    values = state.setdefault("values", {})
    values[metric] = values.get(metric, 0) + amount


def flag(state: dict, metric: str) -> None:
    state.setdefault("values", {})[metric] = 1


def collect(state: dict, metric: str, values) -> None:
    groups = state.setdefault("sets", {})
    groups[metric] = sorted(set(groups.get(metric, ())) | set(values))


def _natural(state: dict, rewards: list, kinds: set[str]) -> None:
    # Finish contains an informational copy of encounter rewards. Those are
    # credited from their own event/choice only, not again at completion.
    units = sum(
        int(r.get("delta_units", 0))
        for r in rewards
        if r.get("source_kind") in kinds and r.get("material_id") in MAIN_MATERIALS and int(r.get("delta_units", 0)) > 0
    )
    add(state, "dispatch.natural_main_units", units)
    state["values"]["dispatch.natural_main_materials"] = (
        state["values"]["dispatch.natural_main_units"] // MATERIAL_SCALE
    )


def dispatch(state: dict, event: str, source: str, at: int, data: dict) -> None:
    if event.startswith("block:"):
        add(state, "dispatch.effective_seconds", int(data["effective_seconds_added"]))
        state["values"]["dispatch.effective_hours"] = state["values"]["dispatch.effective_seconds"] // 3600
        if data.get("forced") and data.get("hit"):
            flag(state, "dispatch.pity_tenth_hit")
    elif event.startswith(("event:", "choice:")):
        rewards = data.get("rewards", [])
        _natural(state, rewards, {"dispatch-encounter"})
        collect(state, "dispatch.natural_souvenirs", (r["souvenir_id"] for r in rewards if "souvenir_id" in r))
    elif event in {"completed", "recalled"}:
        snap, progress = data["snapshot"], data["progress"]
        _natural(state, progress.get("rewards", []), {"dispatch-base", "dispatch-bonus"})
        if event != "completed" or progress["settled_hours"] < 4:
            return
        hours, members = progress["settled_hours"], snap["members"]
        full_low = len(members) == 3 and all(1 <= m["rarity"] <= 3 for m in members)
        add(state, "dispatch.completed_trips")
        collect(state, "journey.three_systems", ["dispatch"])
        if full_low:
            add(state, "dispatch.low_full_team_trips")
        collect(state, "dispatch.completed_regions", [snap["region_id"]])
        collect(state, "dispatch.completed_templates", (m["template_id"] for m in members))
        if snap.get("tool_id"):
            collect(state, "dispatch.completed_tool_types", [snap["tool_id"]])
        own = state.setdefault("dispatch_instances", {})
        for member in members:
            pig_id = member["pig_instance_id"]
            record = own.setdefault(pig_id, {"hours": 0, "music_at": None})
            record["hours"] += hours
            if record["hours"] >= 192:
                flag(state, "dispatch.own_companion_level5")
            if record["hours"] >= 48 and member["template_id"] in CHARACTERS and record["music_at"] is None:
                record["music_at"] = at
        templates = {m["template_id"] for m in members}
        if templates == {"pig-r2-tiny"} and len(members) == 1 and hours >= 24 and snap["region_id"] == "echo-mine":
            flag(state, "dispatch.solo_tiny_mine_24h")
        if (
            full_low
            and hours >= 12
            and snap["region_id"] == "windbell-forest"
            and {"pig-r2-elephant", "pig-r2-tiny"} <= templates
        ):
            flag(state, "dispatch.elephant_tiny_forest")
        if full_low and snap["hours"] >= 8 and not state.get("values", {}).get("dispatch.three_route_cohort"):
            trips = state.setdefault("cohort_returns", [])
            trips.append({"id": source, "start": data["starts_ms"], "region": snap["region_id"], "slot": snap["slot"]})
            # Compare the current return only. Claims may be arbitrarily late,
            # so retain successful candidates until the achievement is proven.
            near = [t for t in trips[:-1] if abs(t["start"] - data["starts_ms"]) <= 900_000]
            for left, right in combinations(near, 2):
                batch = [left, right, trips[-1]]
                if (
                    len({t["slot"] for t in batch}) == len({t["region"] for t in batch}) == 3
                    and max(t["start"] for t in batch) - min(t["start"] for t in batch) <= 900_000
                ):
                    flag(state, "dispatch.three_route_cohort")
                    state.pop("cohort_returns", None)
                    break


def tour(state: dict, event: str, source: str, at: int, data: dict) -> None:
    if "own_experience_after" in data:
        if data["own_experience_after"] >= 2200 and data["experience_after"] >= 2200:
            flag(state, "tour.own_member_level10")
    elif event == "equipment-upgraded":
        amount = data["costs"].get("stage-components", 0)
        if data.get("paid") and amount > 0 and data["natural_stage_components_before_units"] >= amount * MATERIAL_SCALE:
            state.setdefault("own_stage_equipment", {})[str(data["level"])] = at
    elif event == "completed":
        stages = data.get("stages", [])
        if len(stages) != 3 or any(s.get("preview") for s in stages):
            return
        add(state, "tour.completed_tours")
        collect(state, "journey.three_systems", ["tour"])
        all_s = all(s["grade"] in {"S", "SS"} for s in stages)
        if all(s["grade"] == "SS" for s in stages):
            flag(state, "tour.three_stage_ss")
        if all_s and all(len(set(s["bands"])) >= 3 for s in stages):
            flag(state, "tour.mixed_three_band_all_s")
        if all_s and all(len(s["members"]) == len(set(s["canonical_ids"])) == 3 for s in stages):
            flag(state, "tour.three_member_all_s")
        collect(state, "tour.highlight_characters", (h["identity"] for s in stages for h in s["highlights"]))
        collect(
            state, "tour.completed_venues", (VENUE_ALIASES.get(s["plan"]["venue"], s["plan"]["venue"]) for s in stages)
        )
        theme = stages[0]["plan"]["theme"]
        if all(s["plan"]["theme"] == theme and s["theme_qualified"] and s["grade"] in {"A", "S", "SS"} for s in stages):
            collect(state, "tour.nine_band_themes", [THEME_ALIASES.get(theme, theme)])
        if data.get("verified_partner"):
            collect(state, "tour.coop_partners", [data["verified_partner"]])
        if all_s:
            if all({"tomoe", "ako", "layer"} <= set(s["canonical_ids"]) for s in stages):
                no_keys = all(
                    "keyboard" not in CHARACTERS[m["template_id"]].instruments
                    for s in stages
                    for m in s["members"]
                    if m["template_id"] in CHARACTERS
                )
                if no_keys and any({"tomoe", "ako"} <= {h["identity"] for h in s["highlights"]} for s in stages):
                    flag(state, "tour.double_drums_layer_all_s")
            templates = set.intersection(*({m["template_id"] for m in s["members"]} for s in stages))
            for form in ("pig-bandori-hhw-misaki", "pig-bandori-hhw-michelle"):
                if form in templates:
                    forms = state.setdefault("dj_forms", {})
                    # A band's identity is the owner, not its freely editable name.
                    forms[form] = at
                    if len(forms) == 2:
                        flag(state, "tour.misaki_michelle_all_s")
            if all(set(s["canonical_ids"]) == {"tomori", "soyo", "taki", "sakiko", "mutsumi"} for s in stages):
                flag(state, "tour.crychic_all_s")
            eligible = state.get("own_stage_equipment", {})
            if all(
                str(s["equipment"]) in eligible and eligible[str(s["equipment"])] <= s["occurred_ms"] for s in stages
            ):
                flag(state, "journey.dispatch_gear_tour")
        own = state.get("dispatch_instances", {})
        common = set.intersection(*({m["pig_instance_id"] for m in s["members"]} for s in stages))
        for pig_id in common:
            before = own.get(pig_id, {}).get("music_at")
            if before is not None and all(s["occurred_ms"] >= before for s in stages):
                tours = state.setdefault("companion_tours", {})
                tours[pig_id] = tours.get(pig_id, 0) + 1
                if tours[pig_id] >= 3:
                    flag(state, "journey.same_companion_dispatch_tour")


def battle(state: dict, event: str, source: str, at: int, data: dict, player_id: str) -> None:
    if event == "upgrade" and data.get("payer_id") == player_id:
        pig_id, level = data["pig_instance_id"], data["to_level"]
        own = state.setdefault("own_battle_upgrades", {}).setdefault(pig_id, [])
        if level not in own:
            own.append(level)
        if level == 1 and data["from_level"] == 0:
            flag(state, "battle.own_upgrade_one")
            if data["natural_ore_units_before"] >= 60 * MATERIAL_SCALE:
                state.setdefault("mine_trained", {})[pig_id] = at
        if set(own) >= {1, 2, 3, 4, 5}:
            flag(state, "battle.own_full_training")
        if data.get("archetype") in {"sukuna", "gojo"}:
            collect(state, "battle.own_trained_archetypes", [data["archetype"]])
        return
    if event.startswith("loot:"):
        if data.get("role") == "actor":
            deliveries = state.setdefault("loot_deliveries", {}).setdefault(source, [])
            ordinal = int(event.split(":")[1])
            if ordinal not in deliveries:
                deliveries.append(ordinal)
            total_uses = int(data.get("total_uses", 5))
            if set(deliveries) == set(range(1, total_uses + 1)):
                flag(state, "journey.five_trophies_delivered")
                state.pop("loot_deliveries", None)
        return
    matches = state.setdefault("battles", {})
    match = matches.setdefault(source, {"moves": [], "flags": [], "loans": 0, "loan_ready": False, "round": 0})
    if event == "accepted":
        match.update(side=data["side"], role=data["role"], snapshot=data["snapshot"])
    elif event.startswith("move:"):
        round_number = int(event.split(":")[1])
        if round_number != match["round"]:
            match.update(loans=0, loan_ready=False, round=round_number)
        if data["move_id"] not in match["moves"]:
            match["moves"].append(data["move_id"])
        if data.get("loan"):
            match["loans"] += 1
            if match["loans"] >= 3:
                match["loan_ready"] = True
        else:
            match["loans"] = 0
            if data.get("gain", 0) > 0:
                if match["loan_ready"] and data.get("multiplier") == 2:
                    match["flags"].append("battle.three_consecutive_loans")
                match["loan_ready"] = False
    elif event.startswith("round:"):
        result, index = data["result"], data["side"]
        own = result["before"][index]
        turn = own["turn"]
        if result["winner"] == index and turn["effective"] == 0 and turn["draws"] == 0 and turn["debt"] > 0:
            match["flags"].append("battle.zero_action_round_win")
    elif event == "finished":
        if data.get("natural_end") and data.get("status") == "completed":
            sides = data["state"]["sides"]
            index = next((i for i, s in enumerate(sides) if s["snapshot"]["player_id"] == player_id), None)
            if index is None:
                raise ValueError("Battle fact participant does not belong to the match")
            own, other = sides[index], sides[1 - index]
            snap = own["snapshot"]
            archetype = snap["fighter_id"]
            add(state, "battle.natural_finishes")
            collect(state, "journey.three_systems", ["battle"])
            collect(state, "battle.finished_opponents", [other["snapshot"]["player_id"]])
            collect(state, "battle.finished_roles", ["initiator" if index == 0 else "opponent"])
            recorded_moves = match["moves"]
            aliases = MOVE_ALIASES.get(archetype)
            if aliases is not None:
                # The v1 movebook achievements are frozen to the two launch
                # fighters.  New fighters still settle every generic battle
                # fact, but must not create or expand a legacy movebook.
                collect(
                    state,
                    f"battle.{archetype}_moves",
                    (aliases[move] for move in recorded_moves if move in aliases),
                )
            for metric in set(match["flags"]):
                flag(state, metric)
            if own["core"] >= 3:
                flag(state, "battle.three_cores_in_match")
            if own["risk"] >= 2 and data["winner_id"] == player_id:
                flag(state, "battle.heavy_risk_match_win")
            if (
                snap["pig_instance_id"] in state.get("mine_trained", {})
                and state["mine_trained"][snap["pig_instance_id"]] <= at
            ):
                flag(state, "journey.mine_upgrade_battle")
        matches.pop(source, None)


REDUCERS = {"dispatch": dispatch, "tour": tour, "battle": battle}


def reduce_fact(state: dict, fact: dict, payload: dict) -> None:
    handler = REDUCERS.get(fact["source_type"])
    if handler is None:
        return
    if fact.get("definition_version", 1) != 1:
        # Do not silently dequeue evidence whose meaning this build cannot read.
        raise ValueError("Unsupported activity fact definition version")
    args = (state, fact["subevent_id"], fact["source_id"], fact["occurred_ms"], payload)
    if fact["source_type"] == "battle":
        handler(*args, fact["player_id"])
    else:
        handler(*args)


def progress(state: dict, definition, unlocked: set[str]) -> tuple[int, dict]:
    condition = definition.condition
    metric = condition.metric
    if metric == "journey.public_pack_complete":
        items = unlocked & FIXED_SETS["three-systems-public-v1"]
        return len(items), {"items": sorted(items), "target": condition.target}
    if condition.kind.value == "set":
        items = set(state.get("sets", {}).get(metric, ()))
        snapshot = condition.parameters.get("snapshot")
        if snapshot:
            items &= FIXED_SETS[snapshot]
        return min(condition.target, len(items)), {"items": sorted(items), "target": condition.target}
    return min(condition.target, state.get("values", {}).get(metric, 0)), {"target": condition.target}
