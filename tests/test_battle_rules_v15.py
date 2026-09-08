"""Battle v15：达妮娅世界招式重做与历史114514战报兼容。"""

from copy import deepcopy

import pytest

import pig_catcher.domain.battle as battle_module
from pig_catcher.domain.battle import (
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
    DANIYA_COMMON_MOVES_V14,
    DANIYA_FORM_DISILLUSION,
    DANIYA_FORM_STAGING,
    DANIYA_STAGING_MOVES,
    FIGHTERS_BY_ID,
    MOVE_WEIGHT_SCALE,
    fighter_form_moves,
)
from pig_catcher.services.battle_views import (
    _event_move_wheel,
    _firefly_state_projection,
    _juejue_state_projection,
    move_line,
)


def _move(moves, move_id: str):
    return next(move for move in moves if move.move_id == move_id)


def _state(left: str = "daniya", right: str = "gojo") -> dict:
    return new_state(
        [
            {"fighter_id": fighter_id, "level": 0, "trait_bonus": 0, "tool_id": ""}
            for fighter_id in (left, right)
        ],
        seed="battle-v15",
    )


def _ready(player: dict, pending: int = 1) -> None:
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def _record(state: dict, side: int, move, *, seed: str = "battle-v15") -> dict:
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


def test_v15_catalog_removes_114514_and_uses_the_requested_world_weights() -> None:
    assert BATTLE_RULE_VERSION >= 15
    current = {move.move_id: move for move in DANIYA_COMMON_MOVES}
    assert "daniya-world-114514" not in current
    assert current["daniya-world-dragon-image"].gain == 10
    assert current["daniya-world-dragon-image"].resolved_draw_weight_units == MOVE_WEIGHT_SCALE
    assert current["daniya-world-work"].draws == 1
    assert current["daniya-world-work"].resolved_draw_weight_units == 5000
    assert current["daniya-world-nmsl"].resolved_draw_weight_units == 1000
    assert "daniya-world-114514" in {
        move.move_id for move in fighter_form_moves("daniya", DANIYA_FORM_STAGING, 14)
    }


def test_world_work_separates_domain_draw_weight_from_clash_weight() -> None:
    current = _state("daniya", "sukuna")
    player = current["sides"][0]
    work = _move(DANIYA_COMMON_MOVES, "daniya-world-work")
    domain = _move(DANIYA_COMMON_MOVES, "daniya-domain")
    _ready(player, 3)

    apply_move(player, DANIYA_STAGING_MOVES[0])
    work_event = apply_move(player, work)
    assert work_event["extra_draws"] == 1
    assert player["daniya_domain_steps"] == 3
    assert player["daniya_domain_draw_only_steps"] == 10
    assert move_weight_units(player, domain) == 23000

    player["turn"].update(pending=1, done=False)
    domain_event = apply_move(player, domain)
    assert domain_event["daniya_domain_carried_units"] == 3
    assert player["turn"]["domain_clash_bonus_units"] == 3
    assert player["daniya_domain_steps"] == player["daniya_domain_draw_only_steps"] == 0

    disillusion = _state("daniya", "sukuna")["sides"][0]
    disillusion["daniya_form"] = DANIYA_FORM_DISILLUSION
    _ready(disillusion)
    disillusion_event = apply_move(disillusion, work)
    assert disillusion_event["extra_draws"] == 1
    assert disillusion_event["opponent_exhaust_bonus_units"] == 5


def test_world_dragon_image_cancels_one_numeric_move_but_keeps_its_function() -> None:
    current = _state("daniya", "gojo")
    dragon = _move(DANIYA_COMMON_MOVES, "daniya-world-dragon-image")
    black_flash = _move(FIGHTERS_BY_ID["gojo"].moves, "black-flash")
    _ready(current["sides"][0])
    dragon_event = _record(current, 0, dragon)
    _finish(current["sides"][0])
    _ready(current["sides"][1])
    target_event = _record(current, 1, black_flash)
    _finish(current["sides"][1])

    interactions = _settle_interactions(current, "dragon-image-only-target")
    fact = interactions["daniya_dragon_images"][0]
    assert dragon_event["gain"] == 10
    assert fact["source_ordinal"] == dragon_event["ordinal"]
    assert fact["selected_ordinal"] == target_event["ordinal"]
    assert fact["cancelled_gain"] == target_event["gain"]
    assert current["sides"][1]["black_flash_stacks"] == 1
    assert target_event["extra_draws"] == 2


@pytest.mark.parametrize(
    ("rolled", "effective", "heavy", "risk", "core", "natural_end"),
    (
        ("light", "none", False, 0, 0, False),
        ("heavy", "light", False, 1, 0, False),
        ("exhausted", "heavy", True, 2, 0, False),
        ("core", "core", False, 0, 1, False),
    ),
)
def test_world_nmsl_reduces_the_losing_injury_by_one_level(
    monkeypatch,
    rolled: str,
    effective: str,
    heavy: bool,
    risk: int,
    core: int,
    natural_end: bool,
) -> None:
    current = _state("daniya", "sukuna")
    nmsl = _move(DANIYA_COMMON_MOVES, "daniya-world-nmsl")
    _ready(current["sides"][0])
    _record(current, 0, nmsl)
    _finish(current["sides"][0])
    current["sides"][1]["turn"].update(raw=0, effective=0, pending=0, done=True)

    original_choose = battle_module.choose
    original_randbelow = battle_module.randbelow

    def fixed_choose(seed, key, wheel, *, version=BATTLE_RULE_VERSION):
        if key.endswith(":injury"):
            return rolled, 0
        return original_choose(seed, key, wheel, version=version)

    def fixed_randbelow(seed, key, upper, *, version=BATTLE_RULE_VERSION):
        if key.endswith(":winner"):
            return upper - 1
        return original_randbelow(seed, key, upper, version=version)

    monkeypatch.setattr(battle_module, "choose", fixed_choose)
    monkeypatch.setattr(battle_module, "randbelow", fixed_randbelow)
    result = resolve_round(current, f"nmsl-{rolled}")

    assert result is not None
    assert result["loser"] == 0
    assert result["injury"] == rolled
    assert result["daniya_injury_guarded"]
    assert result["injury_effective"] == effective
    assert result["natural_end"] is natural_end
    loser = result["after"][0]
    assert loser["heavy"] is heavy
    assert loser["risk"] == risk
    assert loser["core"] == core


@pytest.mark.parametrize(
    ("fighter_id", "projection"),
    (("juejue", _juejue_state_projection), ("firefly", _firefly_state_projection)),
)
def test_v14_forced_daniya_events_do_not_pollute_native_form_tracks(fighter_id, projection) -> None:
    current = _state(fighter_id, "daniya")
    player = current["sides"][0]
    player["turn"]["daniya_world_forced_move_ids"] = [
        move.move_id for move in fighter_form_moves("daniya", DANIYA_FORM_STAGING)
    ]
    player["turn"]["daniya_world_forced_form"] = DANIYA_FORM_STAGING
    _ready(player)
    event = play_chunk(current, 0, f"forced-{fighter_id}", chunk_size=1)[0]
    assert "达妮娅" in move_line(event).note
    form, track, _summary = projection(player)
    assert form and track
    assert "布景" not in track


def test_v14_114514_wheel_can_still_be_reconstructed_for_history() -> None:
    legacy_moves = fighter_form_moves("daniya", DANIYA_FORM_STAGING, 14)
    legacy = _move(DANIYA_COMMON_MOVES_V14, "daniya-world-114514")
    event = {
        "ordinal": 1,
        "fighter_id": "juejue",
        "functional_fighter_id": "daniya",
        "daniya_world_forced": True,
        "form_before": DANIYA_FORM_STAGING,
        "draw_wheel_move_ids": [move.move_id for move in legacy_moves],
        "draw_wheel_units": [move.resolved_draw_weight_units for move in legacy_moves],
        "draw_weight_scale": MOVE_WEIGHT_SCALE,
        "move_id": legacy.move_id,
        "name": legacy.name,
    }
    wheel = _event_move_wheel(event, 14)
    assert wheel.segments[wheel.selected_index].label == legacy.name
    assert "布景" in wheel.title
