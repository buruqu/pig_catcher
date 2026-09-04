"""Battle v12：栖夜流萤抱抱猪双形态、燃芯、溃败与领域规则验收。"""

from copy import deepcopy
from fractions import Fraction

from pig_catcher.domain.battle import (
    _apply_firefly_event_context,
    _dynamic_injury_wheel,
    _settle_interactions,
    apply_move,
    choose,
    dumps,
    loads,
    move_weight_units,
    new_state,
    play_chunk,
    resolve_round,
)
from pig_catcher.domain.battle_catalog import (
    BATTLE_RULE_VERSION,
    FIGHTERS_BY_TEMPLATE,
    FIREFLY_FORM_FIREFLY,
    FIREFLY_FORM_SAM,
    FIREFLY_MOVES,
    FIREFLY_PIG_TEMPLATE_IDS,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.services.battle_views import wheels


def _state(right: str = "sukuna") -> dict:
    return new_state(
        [
            {"fighter_id": "firefly", "level": 0, "trait_bonus": 0, "tool_id": ""},
            {"fighter_id": right, "level": 0, "trait_bonus": 0, "tool_id": ""},
        ],
        seed="firefly-v12",
    )


def _ready(player: dict, pending: int = 1) -> None:
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def _record(state: dict, side: int, move, *, seed: str = "firefly-v12") -> dict:
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
    _apply_firefly_event_context(state, side, event)
    player["turn"]["events"].append(deepcopy(event))
    return event


def _finish(player: dict) -> None:
    player["turn"].update(pending=0, done=True)


def _non_terminal_resolution(state: dict, *, prefix: str) -> tuple[dict, dict]:
    for index in range(10_000):
        candidate = deepcopy(state)
        summary = resolve_round(candidate, f"{prefix}-{index}")
        if summary is not None and not summary["natural_end"]:
            return candidate, summary
    raise AssertionError("找不到非力竭的确定性回合种子")


def test_firefly_catalog_aliases_weights_and_battle_v12_gate() -> None:
    assert BATTLE_RULE_VERSION == 13
    assert len(FIREFLY_PIG_TEMPLATE_IDS) == 4
    assert {FIGHTERS_BY_TEMPLATE[item].fighter_id for item in FIREFLY_PIG_TEMPLATE_IDS} == {"firefly"}
    assert [move.resolved_draw_weight_units for move in FIREFLY_MOVES] == [
        10000,
        10000,
        8500,
        10000,
        10000,
        9000,
        9000,
        7500,
    ]


def test_fuel_changes_sam_gain_and_draw_weight_but_direct_transform_costs_point_one() -> None:
    state = _state()
    player = state["sides"][0]
    player["firefly_fuel"] = 2
    sam_move = FIREFLY_MOVES[3]
    assert move_weight_units(player, sam_move) == 11000
    player["firefly_form"] = FIREFLY_FORM_SAM
    assert move_weight_units(player, sam_move) == 12000

    player["firefly_form"] = FIREFLY_FORM_FIREFLY
    player["firefly_fuel"] = 0
    _ready(player, 2)
    cocoon = _record(state, 0, FIREFLY_MOVES[0])
    assert cocoon["gain"] == 8
    assert player["firefly_fuel"] == 1
    assert player["firefly_next_sam_gain_bonus"] == 6

    bombardment = _record(state, 0, sam_move)
    assert bombardment["gain"] == 31  # 20基础 + 1层燃芯5 + 赤染之茧6
    assert bombardment["firefly_entered_sam"]
    assert bombardment["firefly_next_sam_bonus_used"] == 6
    assert player["firefly_form"] == FIREFLY_FORM_SAM
    assert player["firefly_sam_rounds_remaining"] == 2
    assert player["firefly_next_sam_gain_bonus"] == 0


def test_collapse_passive_and_bottom_slash_each_add_four_per_existing_layer() -> None:
    state = _state()
    player, target = state["sides"]
    player["firefly_form"] = FIREFLY_FORM_SAM
    player["firefly_sam_rounds_remaining"] = 2
    player["firefly_fuel"] = 1
    target["firefly_collapse"] = 2
    _ready(player)
    slash = _record(state, 0, FIREFLY_MOVES[4])
    assert slash["gain"] == 43  # 22 + 燃芯5 + 被动8 + 招式自身8
    assert slash["opponent_reduction"] == 8
    assert slash["firefly_first_sam_reduction"] == 8
    assert slash["firefly_collapse_to_add"] == 1

    _finish(player)
    _finish(target)
    interactions = _settle_interactions(state, "collapse-settlement")
    assert target["firefly_collapse"] == 3
    assert interactions["firefly_collapse_updates"][0]["after"] == 3
    wheel, modifiers = _dynamic_injury_wheel(state, 1)
    assert modifiers["firefly_collapse_bonus_units"] == 3
    assert dict(wheel)["exhausted"] > dict(modifiers["base_wheel"])["exhausted"]


def test_sam_residual_echoes_do_not_leave_sam_or_gain_fuel() -> None:
    state = _state()
    player, target = state["sides"]
    player["firefly_form"] = FIREFLY_FORM_SAM
    player["firefly_sam_rounds_remaining"] = 2
    player["firefly_fuel"] = 2
    target["firefly_collapse"] = 2
    _ready(player, 2)

    cocoon_echo = _record(state, 0, FIREFLY_MOVES[0])
    dream_echo = _record(state, 0, FIREFLY_MOVES[1])
    assert cocoon_echo["firefly_echo"] and cocoon_echo["gain"] == 6
    assert cocoon_echo["opponent_reduction"] == 6
    assert cocoon_echo["firefly_collapse_to_add"] == 1
    assert dream_echo["firefly_echo"] and dream_echo["gain"] == 0
    assert dream_echo["opponent_reduction"] == 10
    assert dream_echo["opponent_exhaust_bonus_units"] == 1
    assert player["firefly_fuel"] == 2
    assert player["firefly_form"] == FIREFLY_FORM_SAM


def test_firefly_choice_is_frozen_replayable_and_executes_inside_same_chain() -> None:
    state = _state()
    player = state["sides"][0]
    _ready(player)
    choice = apply_move(
        player,
        FIREFLY_MOVES[2],
        seed="frozen-choice",
        round_number=1,
        side=0,
        version=BATTLE_RULE_VERSION,
    )
    queued = choice["firefly_choice"]
    assert len(queued["options"]) == 2
    assert player["turn"]["pending"] == 1
    assert loads(dumps(state)) == state

    restored = loads(dumps(state))
    event = play_chunk(restored, 0, "frozen-choice", chunk_size=1)[0]
    assert event["firefly_forced_choice"]
    assert event["move_id"] == queued["selected_move_id"]
    assert event["firefly_choice_source_ordinal"] == choice["ordinal"]
    if queued["selected_family"] == "sam":
        assert event["firefly_entered_sam"]
        assert event["firefly_choice"]["forced_gain_bonus"] == 10


def test_sam_form_lasts_entry_round_plus_one_following_round() -> None:
    state = _state()
    player = state["sides"][0]
    _ready(player)
    _record(state, 0, FIREFLY_MOVES[3])
    _finish(player)
    _finish(state["sides"][1])

    state, first = _non_terminal_resolution(state, prefix="sam-duration-one")
    assert state["sides"][0]["firefly_form"] == FIREFLY_FORM_SAM
    assert state["sides"][0]["firefly_sam_rounds_remaining"] == 1
    assert first["firefly_transitions"][0]["before_remaining"] == 2

    _finish(state["sides"][0])
    _finish(state["sides"][1])
    state, second = _non_terminal_resolution(state, prefix="sam-duration-two")
    assert state["sides"][0]["firefly_form"] == FIREFLY_FORM_FIREFLY
    assert state["sides"][0]["firefly_sam_rounds_remaining"] == 0
    assert second["firefly_transitions"][0]["after_form"] == FIREFLY_FORM_FIREFLY


def test_firefly_domain_hit_doubles_only_own_domain_gain_then_adds_focal_strike() -> None:
    state = _state()
    player, target = state["sides"]
    target["firefly_collapse"] = 3
    _ready(player)
    domain_event = _record(state, 0, FIREFLY_MOVES[7], seed="falling-sky")
    _finish(player)
    _finish(target)
    wheel = (("hit", 8), ("simple-domain", 2))
    seed = next(
        f"falling-sky-{index}"
        for index in range(10_000)
        if choose(
            f"falling-sky-{index}",
            "1:domain:solo:0",
            wheel,
            version=BATTLE_RULE_VERSION,
        )[0]
        == "hit"
    )
    interactions = _settle_interactions(state, seed)
    domain = interactions["domain"]
    assert domain["boosted_ordinals"] == [domain_event["ordinal"]]
    assert domain["bonus_gain"] == 36
    assert domain_event["opponent_reduction"] == 35  # 基础20 + 满溃败15，不随命中翻倍
    assert player["weight"] == 89  # 5 + 36 + 领域翻倍追加36 + 焦土陨击12
    assert target["turn"]["firefly_self_exhaust_delta_units"] == Fraction(3, 2)


def test_firefly_wheel_view_exposes_both_forms_passive_and_domain_rule() -> None:
    identity = CommandIdentity(ScopeKey("qq-official", "fixture"), "stream", "player", "轮盘玩家")
    rendered = wheels(identity, "firefly", level=5).text()
    assert "栖夜流萤抱抱猪 · 双形态共鸣战斗盘" in rendered
    assert "流萤形态" in rendered and "萨姆形态" in rendered
    assert "燃芯" in rendered and "溃败" in rendered
    assert "自破碎的天空坠落" in rendered
    assert "残梦回声" in rendered
