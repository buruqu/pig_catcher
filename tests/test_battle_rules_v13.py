"""Battle v13：达妮娅世界招式与熠～噜猪精确子结算。"""

from copy import deepcopy

from pig_catcher.domain.battle import (
    _dynamic_injury_wheel,
    _settle_interactions,
    apply_move,
    move_weight_units,
    new_state,
    play_chunk,
    resolve_round,
)
from pig_catcher.domain.battle_catalog import (
    BATTLE_RULE_VERSION,
    DANIYA_COMMON_MOVES,
    DANIYA_DISILLUSION_MOVES,
    DANIYA_FORM_DISILLUSION,
    DANIYA_FORM_STAGING,
    DANIYA_STAGING_MOVES,
    FIGHTERS_BY_ID,
    MOVE_WEIGHT_SCALE,
    YILU_MOVES,
    fighter_form_moves,
)


def _move(moves, move_id: str):
    return next(move for move in moves if move.move_id == move_id)


def _state(left: str = "daniya", right: str = "yilu") -> dict:
    return new_state(
        [
            {"fighter_id": fighter_id, "level": 0, "trait_bonus": 0, "tool_id": ""}
            for fighter_id in (left, right)
        ],
        seed="battle-v13",
    )


def _ready(player: dict, pending: int = 1) -> None:
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def _record(state: dict, side: int, move, *, seed: str = "battle-v13") -> dict:
    player = state["sides"][side]
    event = apply_move(
        player,
        move,
        seed=seed,
        round_number=state["round"],
        side=side,
        version=BATTLE_RULE_VERSION,
    )
    event.update(round=state["round"], side=side, fighter_id=player["snapshot"]["fighter_id"])
    player["turn"]["events"].append(deepcopy(event))
    return event


def _finish(player: dict) -> None:
    player["turn"].update(pending=0, done=True)


def _resolve_non_terminal(source: dict, prefix: str) -> tuple[dict, dict]:
    for index in range(10_000):
        candidate = deepcopy(source)
        result = resolve_round(candidate, f"{prefix}-{index}")
        if result is not None and not result["natural_end"]:
            return candidate, result
    raise AssertionError("找不到不会立即力竭的确定性种子")


def test_v13_catalog_keeps_precise_world_and_operator_draw_weights() -> None:
    assert BATTLE_RULE_VERSION == 14
    assert MOVE_WEIGHT_SCALE == 10000
    assert [move.gain for move in DANIYA_STAGING_MOVES] == [12, 16, 20, 40, 24]
    assert [move.opponent_reduction for move in DANIYA_DISILLUSION_MOVES] == [14, 9, 11, 22, 13]
    assert [move.move_id for move in DANIYA_COMMON_MOVES] == [
        "daniya-flawless",
        "daniya-unfinished-lie",
        "daniya-timed-collapse",
        "daniya-domain",
        "daniya-world-dragon-image",
        "daniya-world-114514",
        "daniya-world-work",
        "daniya-world-nmsl",
    ]
    assert [move.resolved_draw_weight_units for move in DANIYA_COMMON_MOVES] == [
        10000,
        10000,
        2000,
        10000,
        1000,
        8000,
        4444,
        2000,
    ]
    assert _move(YILU_MOVES, "yilu-medic").resolved_draw_weight_units == 2000
    assert _move(YILU_MOVES, "yilu-specialist").resolved_draw_weight_units == 5000


def test_daniya_staging_and_world_work_feed_both_domain_weights_then_clear() -> None:
    current = _state("daniya", "sukuna")
    player = current["sides"][0]
    domain = _move(DANIYA_COMMON_MOVES, "daniya-domain")
    work = _move(DANIYA_COMMON_MOVES, "daniya-world-work")
    _ready(player, 4)

    first = apply_move(player, DANIYA_STAGING_MOVES[0])
    second = apply_move(player, DANIYA_STAGING_MOVES[1])
    world = apply_move(player, work)
    assert [first["daniya_domain_steps_after"], second["daniya_domain_steps_after"]] == [3, 6]
    assert world["daniya_domain_steps_after"] == 26
    assert move_weight_units(player, domain) == 36000

    domain_event = apply_move(player, domain)
    assert domain_event["daniya_domain_carried_units"] == 26
    assert player["turn"]["domain_clash_bonus_units"] == 26
    assert player["daniya_domain_steps"] == 0
    assert move_weight_units(player, domain) == MOVE_WEIGHT_SCALE

    player["daniya_form"] = DANIYA_FORM_DISILLUSION
    player["turn"].update(pending=1, done=False)
    disillusion_work = apply_move(player, work)
    assert disillusion_work["opponent_exhaust_bonus_units"] == 20


def test_daniya_timed_collapse_is_one_passive_layer_plus_each_active_draw() -> None:
    current = _state("daniya", "sukuna")
    current["round"] = 3
    passive_wheel, passive = _dynamic_injury_wheel(current, 1)
    assert passive["daniya_passive_layers"] == 1
    assert passive["daniya_active_layers"] == 0
    assert passive["daniya_layer_multiplier"] == 15
    assert dict(passive_wheel)["exhausted"] == 75

    player = current["sides"][0]
    _ready(player)
    event = apply_move(player, _move(DANIYA_COMMON_MOVES, "daniya-timed-collapse"))
    assert event["gain"] == event["opponent_reduction"] == 0
    active_wheel, active = _dynamic_injury_wheel(current, 1)
    assert active["daniya_passive_layers"] == active["daniya_active_layers"] == 1
    assert active["daniya_multiplier"] == 225
    assert dict(active_wheel)["exhausted"] == 1125


def test_daniya_world_disable_and_force_transfer_to_exactly_the_next_round() -> None:
    source = _state("daniya", "gojo")
    caster, target = source["sides"]
    _ready(caster, 2)
    disable = _record(source, 0, _move(DANIYA_COMMON_MOVES, "daniya-world-dragon-image"))
    force = _record(source, 0, _move(DANIYA_COMMON_MOVES, "daniya-world-114514"))
    _finish(caster)
    _ready(target)
    _record(source, 1, FIGHTERS_BY_ID["gojo"].moves[0])
    _finish(target)

    assert disable["opponent_next_effects_disabled"]
    assert force["opponent_next_forced_form"] == DANIYA_FORM_STAGING
    expected_ids = [move.move_id for move in fighter_form_moves("daniya", DANIYA_FORM_STAGING)]
    assert force["opponent_next_forced_move_ids"] == expected_ids

    active, result = _resolve_non_terminal(source, "daniya-world-combined")
    transition = result["daniya_world_transitions"][0]
    assert transition == {
        "side": 1,
        "effects_disabled": True,
        "forced_form": DANIYA_FORM_STAGING,
        "forced_move_ids": expected_ids,
    }
    next_target = active["sides"][1]
    _ready(next_target)
    event = play_chunk(active, 1, "daniya-world-disabled", chunk_size=1)[0]
    assert event["daniya_world_forced"]
    assert event["functional_fighter_id"] == "daniya"
    assert event["effects_disabled"]
    assert not event["functional_tags"] and not event["domain_eligible"]
    assert event["gain"] == event["opponent_reduction"] == 0
    assert event["extra_draws"] == 0


def test_daniya_world_114514_uses_frozen_daniya_form_with_live_effects() -> None:
    source = _state("daniya", "gojo")
    caster, target = source["sides"]
    _ready(caster)
    _record(source, 0, _move(DANIYA_COMMON_MOVES, "daniya-world-114514"))
    _finish(caster)
    _ready(target)
    _record(source, 1, FIGHTERS_BY_ID["gojo"].moves[0])
    _finish(target)
    active, _result = _resolve_non_terminal(source, "daniya-world-force")

    chosen = None
    for index in range(10_000):
        candidate = deepcopy(active)
        _ready(candidate["sides"][1])
        event = play_chunk(candidate, 1, f"daniya-world-force-draw-{index}", chunk_size=1)[0]
        if event["gain"] > 0:
            chosen = event
            break
    assert chosen is not None
    assert chosen["daniya_world_forced"] and not chosen["effects_disabled"]
    assert chosen["functional_fighter_id"] == "daniya"
    assert chosen["form_before"] == DANIYA_FORM_STAGING
    assert chosen["move_id"] in {
        move.move_id for move in fighter_form_moves("daniya", DANIYA_FORM_STAGING)
    }


def test_daniya_nmsl_suppresses_direct_reduction_and_defender_damage() -> None:
    direct = _state("daniya", "daniya")
    _ready(direct["sides"][0])
    _record(direct, 0, _move(DANIYA_COMMON_MOVES, "daniya-world-nmsl"))
    _finish(direct["sides"][0])
    direct["sides"][1]["daniya_form"] = DANIYA_FORM_DISILLUSION
    _ready(direct["sides"][1])
    attack = _record(direct, 1, DANIYA_DISILLUSION_MOVES[0])
    _finish(direct["sides"][1])
    interactions = _settle_interactions(direct, "daniya-nmsl-direct")
    cross = next(
        item
        for item in interactions["cross_effects"]
        if item["source_side"] == 1 and item["source_ordinal"] == attack["ordinal"]
    )
    assert cross["round_reduction"] == 0
    assert cross["round_reduction_suppressed"]
    assert cross["round_reduction_suppression_reason"].endswith("NMSL")

    defender_result = None
    for index in range(1000):
        current = _state("daniya", "yilu")
        _ready(current["sides"][0])
        _record(current, 0, _move(DANIYA_COMMON_MOVES, "daniya-world-nmsl"))
        _finish(current["sides"][0])
        _ready(current["sides"][1])
        seed = f"daniya-nmsl-defender-{index}"
        defender = _record(current, 1, _move(YILU_MOVES, "yilu-defender"), seed=seed)
        _finish(current["sides"][1])
        if defender["yilu_defender_checks"][0]["hit"]:
            defender_result = _settle_interactions(current, seed)["yilu_defender_results"][0]
            break
    assert defender_result is not None
    assert defender_result["suppressed_by_daniya_nmsl"]
    assert defender_result["cancelled_gain"] == defender_result["opponent_reduction"] == 0


def test_yilu_guard_true_damage_requires_eight_markers_after_its_own_marker() -> None:
    guard = _move(YILU_MOVES, "yilu-guard")
    below = _state("yilu", "sukuna")["sides"][0]
    below["yilu_markers"] = 6
    _ready(below)
    below_event = apply_move(below, guard)
    assert below_event["yilu_consumed_markers"] == 7
    assert below_event["yilu_true_damage_added"] == 0

    reached = _state("yilu", "sukuna")["sides"][0]
    reached["yilu_markers"] = 7
    _ready(reached)
    reached_event = apply_move(reached, guard)
    assert reached_event["yilu_consumed_markers"] == 8
    assert reached_event["yilu_true_damage_added"] == 1


def test_yilu_sniper_each_shot_gets_base_bonuses_and_consumption_buffs_later_shots() -> None:
    sniper = _move(YILU_MOVES, "yilu-sniper")
    chosen = None
    for index in range(10_000):
        player = _state("yilu", "sukuna")["sides"][0]
        player["yilu_markers"] = 5
        player["yilu_markers_total"] = 5
        player["yilu_future_base_bonus"] = 2
        player["turn"]["yilu_round_base_bonus"] = 1
        _ready(player)
        event = apply_move(
            player,
            sniper,
            seed=f"yilu-sniper-v13-{index}",
            round_number=1,
            side=0,
        )
        shots = event["yilu_sniper_shots"]
        if len(shots) >= 3 and any(shot["consumed"] and shot["shot"] < len(shots) for shot in shots):
            chosen = event
            break
    assert chosen is not None
    shots = chosen["yilu_sniper_shots"]
    assert all(shot["base_bonus"] == 3 for shot in shots)
    assert all(shot["effective_gain"] == shot["gain"] + 3 for shot in shots)
    assert chosen["special_base"] == sum(shot["gain"] for shot in shots)
    assert chosen["yilu_future_gain"] == 3 * len(shots)
    assert chosen["gain"] == sum(shot["effective_gain"] for shot in shots)
    for current, following in zip(shots, shots[1:], strict=False):
        assert following["gain"] == 1 + current["followup_bonus_after"]
