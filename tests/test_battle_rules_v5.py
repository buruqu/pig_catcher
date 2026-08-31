"""Battle v5：达妮娅猪、阿萨姆猪与统一数值失效的纯规则验收。"""

from copy import deepcopy
from fractions import Fraction

import pytest

from pig_catcher.domain.battle import (
    _dynamic_injury_wheel,
    apply_move,
    choose,
    dumps,
    loads,
    move_weight_units,
    new_state,
    play_chunk,
    resolve_round,
    roll_count,
)
from pig_catcher.domain.battle_catalog import (
    ASAMU_MOVES,
    ASAMU_PIG_TEMPLATE_IDS,
    BATTLE_RULE_VERSION,
    DANIYA_COMMON_MOVES,
    DANIYA_DISILLUSION_MOVES,
    DANIYA_FORM_DISILLUSION,
    DANIYA_FORM_STAGING,
    DANIYA_PIG_TEMPLATE_IDS,
    DANIYA_STAGING_MOVES,
    FIGHTERS_BY_ID,
    FIGHTERS_BY_TEMPLATE,
    INJURY_WEIGHT_SCALE,
    JUEJUE_TIME_MOVES,
    JUEJUE_VIRTUAL_MOVES,
    MOVE_WEIGHT_SCALE,
    VICTORY_WEIGHT_SCALE,
    fighter_form_moves,
)
from pig_catcher.services.battle_views import (
    _daniya_state_projection,
    _event_move_wheel,
    _juejue_state_projection,
    move_line,
)


def state(left="daniya", right="asamu", *, seed="v5", left_level=0, right_level=0):
    return new_state(
        [
            {"fighter_id": left, "level": left_level, "trait_bonus": 0, "tool_id": ""},
            {"fighter_id": right, "level": right_level, "trait_bonus": 0, "tool_id": ""},
        ],
        seed=seed,
    )


def ready(player: dict, pending: int = 1) -> None:
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def record(player: dict, move, side: int, *, seed="v5", functional_fighter_id=None) -> dict:
    event = apply_move(
        player,
        move,
        seed=seed,
        round_number=1,
        side=side,
        functional_fighter_id=functional_fighter_id,
    )
    event.update(round=1, side=side, fighter_id=player["snapshot"]["fighter_id"])
    player["turn"]["events"].append(deepcopy(event))
    return event


def seed_for(key: str, wheel: tuple, expected, *, prefix="v5") -> str:
    return next(
        seed
        for seed in (f"{prefix}-{index}" for index in range(100_000))
        if choose(seed, key, wheel, version=BATTLE_RULE_VERSION)[0] == expected
    )


def finish_turn(player: dict) -> None:
    player["turn"].update(pending=0, done=True)


def test_v5_catalog_maps_daniya_and_asamu_in_all_four_scopes():
    assert BATTLE_RULE_VERSION == 5
    assert MOVE_WEIGHT_SCALE == 1000
    assert VICTORY_WEIGHT_SCALE == INJURY_WEIGHT_SCALE == 10
    assert len(DANIYA_PIG_TEMPLATE_IDS) == len(ASAMU_PIG_TEMPLATE_IDS) == 4
    assert all(FIGHTERS_BY_TEMPLATE[item].fighter_id == "daniya" for item in DANIYA_PIG_TEMPLATE_IDS)
    assert all(FIGHTERS_BY_TEMPLATE[item].fighter_id == "asamu" for item in ASAMU_PIG_TEMPLATE_IDS)
    assert FIGHTERS_BY_ID["daniya"].template_aliases == DANIYA_PIG_TEMPLATE_IDS[1:]
    assert FIGHTERS_BY_ID["asamu"].template_aliases == ASAMU_PIG_TEMPLATE_IDS[1:]


def test_juejue_domain_draw_weight_stays_one_while_clash_strength_stays_two_point_five():
    current = state(left="juejue", right="daniya", seed="separate-juejue-domain-weights")
    player = current["sides"][0]
    sand_domain = JUEJUE_TIME_MOVES[-1]
    chaos_domain = JUEJUE_VIRTUAL_MOVES[-1]

    assert sand_domain.resolved_draw_weight_units == MOVE_WEIGHT_SCALE
    assert chaos_domain.resolved_draw_weight_units == MOVE_WEIGHT_SCALE
    assert move_weight_units(player, sand_domain) == MOVE_WEIGHT_SCALE
    assert move_weight_units(player, chaos_domain) == MOVE_WEIGHT_SCALE

    player["turn"].update(raw=1, effective=1, pending=1, done=False)
    record(player, sand_domain, 0, functional_fighter_id="juejue")
    finish_turn(player)
    opponent = current["sides"][1]
    ready(opponent)
    record(opponent, DANIYA_COMMON_MOVES[-1], 1)
    finish_turn(opponent)
    summary = resolve_round(current, "separate-juejue-domain-weights")
    assert summary["interactions"]["domain"]["strengths"] == [5, 6]
    assert summary["interactions"]["domain"]["weight_scale"] == 2


def test_daniya_two_forms_share_four_common_moves_and_keep_four_distinct_moves_each():
    staging = fighter_form_moves("daniya", DANIYA_FORM_STAGING)
    disillusion = fighter_form_moves("daniya", DANIYA_FORM_DISILLUSION)
    common_ids = {move.move_id for move in DANIYA_COMMON_MOVES}
    assert staging == DANIYA_STAGING_MOVES + DANIYA_COMMON_MOVES
    assert disillusion == DANIYA_DISILLUSION_MOVES + DANIYA_COMMON_MOVES
    assert len(staging) == len(disillusion) == 8
    assert {move.move_id for move in staging} & {move.move_id for move in disillusion} == common_ids
    assert state()["sides"][0]["daniya_form"] == DANIYA_FORM_STAGING


def test_daniya_staging_accumulates_domain_draw_weight_and_domain_draw_clears_it():
    player = state()["sides"][0]
    domain = DANIYA_COMMON_MOVES[-1]
    assert move_weight_units(player, domain) == 1000
    ready(player, 3)
    first = apply_move(player, DANIYA_STAGING_MOVES[0])
    second = apply_move(player, DANIYA_STAGING_MOVES[1])
    assert first["daniya_domain_steps_after"] == 1
    assert second["daniya_domain_steps_after"] == 2
    assert move_weight_units(player, domain) == 1200
    domain_event = apply_move(player, domain)
    assert domain_event["daniya_domain_steps_before"] == 2
    assert domain_event["daniya_domain_steps_after"] == 0
    assert move_weight_units(player, domain) == 1000


def test_v5_move_cards_display_real_numeric_base_and_tenth_step_scales():
    daniya = state()["sides"][0]
    ready(daniya)
    staging = apply_move(daniya, DANIYA_STAGING_MOVES[0])
    staging.update(fighter_id="daniya")
    staging_line = move_line(staging)
    assert staging_line.value.startswith("+14")
    assert "蚀域抽取加权 0 → 0.1" in staging_line.note

    collapse_player = state()["sides"][0]
    ready(collapse_player)
    collapse = apply_move(collapse_player, DANIYA_COMMON_MOVES[2])
    collapse.update(fighter_id="daniya")
    collapse_line = move_line(collapse)
    assert collapse_line.value.startswith("-52.1")
    assert "(-52.1 + 强化0" in collapse_line.note

    juejue = state(left="juejue")["sides"][0]
    ready(juejue)
    sculpt = apply_move(juejue, JUEJUE_TIME_MOVES[0], functional_fighter_id="juejue")
    sculpt.update(fighter_id="juejue")
    assert "荒时之沙出现权重累计+0.1" in move_line(sculpt).note
    assert "荒时之沙抽取权重+0.1" in _juejue_state_projection(juejue)[2]


def test_daniya_rendered_form_track_keeps_staging_to_disillusion_order():
    player = state()["sides"][0]
    ready(player)
    event = apply_move(player, DANIYA_COMMON_MOVES[-1])
    event.update(fighter_id="daniya")
    player["turn"]["events"].append(event)
    player["daniya_form"] = DANIYA_FORM_DISILLUSION
    assert _daniya_state_projection(player)[1] == "布景 → 幻灭"


def _daniya_domain_state(*, opponent="sukuna") -> tuple[dict, tuple]:
    current = state(right=opponent)
    ready(current["sides"][0])
    record(current["sides"][0], DANIYA_COMMON_MOVES[-1], 0)
    ready(current["sides"][1])
    opponent_domain = next(move for move in FIGHTERS_BY_ID[opponent].moves if "domain" in move.tags)
    record(current["sides"][1], opponent_domain, 1)
    # 达妮娅普通领域=3，宿傩=4，领域战内部统一按2倍整数权重抽签。
    return current, (("side-0", 6), ("side-1", 8), ("tie", 6))


def test_daniya_only_real_clash_win_switches_to_disillusion_and_grants_next_action():
    current, wheel = _daniya_domain_state()
    seed = seed_for("1:domain:clash", wheel, "side-0", prefix="daniya-clash")
    summary = resolve_round(current, seed)
    assert summary["interactions"]["domain"]["mode"] == "clash"
    assert summary["interactions"]["domain"]["outcome"] == "side-0"
    assert summary["interactions"]["daniya_transition"] == {
        "side": 0,
        "before": DANIYA_FORM_STAGING,
        "after": DANIYA_FORM_DISILLUSION,
        "next_action_bonus": 1,
    }
    assert summary["before"][0]["daniya_form"] == DANIYA_FORM_DISILLUSION
    assert summary["before"][0]["next_action_bonus"] == 1


def test_daniya_solo_domain_hit_does_not_switch_form():
    current = state(right="sukuna")
    ready(current["sides"][0])
    record(current["sides"][0], DANIYA_COMMON_MOVES[-1], 0)
    current["sides"][1]["turn"]["done"] = True
    wheel = (("hit", 8), ("simple-domain", 2))
    seed = seed_for("1:domain:solo:0", wheel, "hit", prefix="daniya-solo")
    summary = resolve_round(current, seed)
    assert summary["interactions"]["domain"]["mode"] == "solo"
    assert summary["interactions"]["daniya_transition"] is None
    assert summary["before"][0]["daniya_form"] == DANIYA_FORM_STAGING


def test_daniya_minus_52_point_1_is_exact_and_loan_doubles_signed_value():
    player = state()["sides"][0]
    ready(player, 2)
    loan = apply_move(player, DANIYA_COMMON_MOVES[1])
    collapse = apply_move(player, DANIYA_COMMON_MOVES[2])
    assert loan["loan"] and loan["gain"] == 0 and loan["double_pending"]
    assert collapse["special_base"] == Fraction(-521, 10)
    assert collapse["multiplier"] == 2
    assert collapse["gain"] == Fraction(-521, 5)
    assert player["weight"] == Fraction(-496, 5)
    assert not player["double"]


def test_daniya_loan_doubles_both_own_gain_and_opponent_reduction():
    player = state()["sides"][0]
    player["daniya_form"] = DANIYA_FORM_DISILLUSION
    ready(player, 2)
    apply_move(player, DANIYA_COMMON_MOVES[1])
    event = apply_move(player, DANIYA_DISILLUSION_MOVES[0])
    assert event["gain"] == 16
    assert event["opponent_reduction"] == 16
    assert event["opponent_exhaust_bonus_units"] == 1


def test_daniya_disillusion_reduces_opponent_and_adds_exact_point_one_exhaust_weight():
    current = state()
    attacker, target = current["sides"]
    attacker["daniya_form"] = DANIYA_FORM_DISILLUSION
    ready(attacker)
    event = record(attacker, DANIYA_DISILLUSION_MOVES[1], 0)
    finish_turn(attacker)
    target["weight"] = 30
    target["round_start_weight"] = 5
    finish_turn(target)
    summary = resolve_round(current, "daniya-disillusion-cross")
    cross = next(
        item
        for item in summary["interactions"]["cross_effects"]
        if item["source_ordinal"] == event["ordinal"]
    )
    assert cross["round_reduction"] == 11
    assert cross["exhaust_bonus_units"] == 1
    assert summary["before"][1]["injury_exhaust_bonus_units"] == 1
    wheel, modifiers = _dynamic_injury_wheel(current, 1)
    assert modifiers["permanent_exhaust_bonus_units"] == 1
    assert dict(wheel)["exhausted"] in {6, 11, 61}


def test_unified_invalidation_zeroes_daniya_own_gain_but_keeps_directed_effects():
    current = state(left="juejue", right="daniya")
    defender, attacker = current["sides"]
    ready(defender)
    record(defender, JUEJUE_VIRTUAL_MOVES[1], 0, functional_fighter_id="juejue")
    finish_turn(defender)
    attacker["daniya_form"] = DANIYA_FORM_DISILLUSION
    ready(attacker)
    event = record(attacker, DANIYA_DISILLUSION_MOVES[0], 1)
    finish_turn(attacker)

    summary = resolve_round(current, "invalidate-daniya-directed-effects")
    future = summary["interactions"]["future_simulations"][0]
    cross = next(
        item
        for item in summary["interactions"]["cross_effects"]
        if item["source_ordinal"] == event["ordinal"]
    )
    assert future["cancelled_gain"] == event["gain"]
    assert cross["round_reduction"] == 8
    assert cross["exhaust_bonus_units"] == 1


def test_daniya_timed_collapse_multiplies_target_exhaustion_and_schedules_one_round_rebound():
    source = state()
    ready(source["sides"][0])
    record(source["sides"][0], DANIYA_COMMON_MOVES[2], 0)
    finish_turn(source["sides"][0])
    source["sides"][1]["weight"] = 100
    finish_turn(source["sides"][1])
    wheel, modifiers = _dynamic_injury_wheel(source, 1)
    assert dict(wheel)["exhausted"] == 25
    assert modifiers["current_collapse_multiplier"] == 5

    for index in range(100_000):
        current = deepcopy(source)
        summary = resolve_round(current, f"daniya-collapse-{index}")
        if summary["loser"] == 1 and not summary["natural_end"]:
            break
    else:
        raise AssertionError("没有找到计时的溃灭未使对手力竭的固定种子")
    assert summary["collapse_rebounds"] == ({
        "caster_side": 0,
        "target_side": 1,
        "target_exhausted": False,
        "rebound_round": 2,
    },)
    assert current["sides"][0]["next_exhaust_multiplier"] == 5
    assert current["sides"][0]["next_exhaust_multiplier_round"] == 2
    rebound_wheel, rebound = _dynamic_injury_wheel(current, 0)
    assert rebound["rebound_multiplier"] == 5
    assert dict(rebound_wheel)["exhausted"] == dict(rebound["base_wheel"])["exhausted"] * 5


def test_asamu_dynamic_move_weights_use_exact_thousandths_and_reset_at_use():
    player = state(left="asamu")["sides"][0]
    bathe, tea, sleep, prime = ASAMU_MOVES[:4]
    assert [move_weight_units(player, move) for move in (bathe, tea, sleep, prime)] == [1000, 1000, 1000, 200]
    ready(player, 3)
    apply_move(player, bathe)
    assert move_weight_units(player, tea) == 1500
    apply_move(player, tea)
    assert move_weight_units(player, tea) == 1000
    assert move_weight_units(player, sleep) == 1250
    apply_move(player, sleep)
    assert move_weight_units(player, sleep) == 1000
    assert move_weight_units(player, prime) == 300


def test_asamu_charge_up_adds_three_to_every_later_move_but_not_itself():
    player = state(left="asamu")["sides"][0]
    charge, hit = ASAMU_MOVES[4], ASAMU_MOVES[0]
    ready(player, 4)
    first = apply_move(player, charge)
    second = apply_move(player, hit)
    third = apply_move(player, charge)
    fourth = apply_move(player, hit)
    assert first["gain"] == 1 and first["asamu_big_gain"] == 0
    assert second["gain"] == 13 and second["asamu_big_gain"] == 3
    assert third["gain"] == 4 and third["asamu_big_gain"] == 3
    assert fourth["gain"] == 16 and fourth["asamu_big_gain"] == 6


def _pressure_hit_state() -> tuple[dict, dict, dict, str]:
    source = state(left="asamu", right="sukuna")
    ready(source["sides"][0])
    pressure = record(source["sides"][0], ASAMU_MOVES[5], 0)
    finish_turn(source["sides"][0])
    ready(source["sides"][1])
    flash = record(source["sides"][1], FIGHTERS_BY_ID["sukuna"].moves[0], 1)
    finish_turn(source["sides"][1])
    key = f"1:asamu:pressure:0:{pressure['ordinal']}:1:{flash['ordinal']}"
    seed = seed_for(key, ((True, 33), (False, 67)), True, prefix="pressure")
    return source, pressure, flash, seed


def test_asamu_pressure_zeroes_the_entire_numeric_gain_but_keeps_black_flash_function():
    current, _pressure, flash, seed = _pressure_hit_state()
    summary = resolve_round(current, seed)
    check = next(item for item in summary["interactions"]["pressure_checks"] if item["hit"])
    assert check["target_ordinal"] == flash["ordinal"]
    assert check["cancelled_gain"] == flash["gain"]
    assert summary["before"][1]["weight"] == 5
    assert summary["before"][1]["black_flash_stacks"] == 1
    assert flash["extra_draws"] == 2


def test_asamu_milk_dragon_overrides_consecutive_enemy_moves_without_reflection():
    current = state(left="asamu", right="gojo", seed="milk-dragon")
    target = current["sides"][1]
    target["asamu_milk_dragon_next_count"] = 2
    roll_count(current, 1, "milk-dragon")
    target["turn"].update(raw=2, effective=2, pending=2, done=False)
    events = play_chunk(current, 1, "milk-dragon", chunk_size=2)
    assert [event["move_id"] for event in events] == ["asamu-milk-dragon", "asamu-milk-dragon"]
    assert all(event["forced"] for event in events)
    assert all(event["original_move_id"] for event in events)
    assert all(event["opponent_next_milk_dragons"] == 0 for event in events)
    assert target["turn"]["forced_milk_dragon_used"] == 2
    card = _event_move_wheel(events[-1], BATTLE_RULE_VERSION)
    labels = [segment.label for segment in card.segments]
    assert labels == [move.name for move in FIGHTERS_BY_ID["gojo"].moves]
    assert card.selected_index is not None
    assert labels[card.selected_index] == events[-1]["original_move_name"]
    assert "奶龙覆盖" in card.title and "随后被覆盖" in card.note


def test_asamu_misfortune_multiplies_exhaustion_for_both_sides_and_each_stack_is_times_five():
    current = state(left="asamu", right="asamu")
    current["sides"][0]["turn"]["asamu_misfortune_count"] = 1
    for loser in (0, 1):
        wheel, modifiers = _dynamic_injury_wheel(current, loser)
        assert dict(wheel)["exhausted"] == 25
        assert modifiers["misfortune_count"] == 1
        assert modifiers["total_exhaust_multiplier"] == 5
    current["sides"][1]["turn"]["asamu_misfortune_count"] = 1
    wheel, modifiers = _dynamic_injury_wheel(current, 0)
    assert dict(wheel)["exhausted"] == 125
    assert modifiers["total_exhaust_multiplier"] == 25


@pytest.mark.parametrize(
    "own_before,opponent_before,expected",
    [
        (9, 20, (24, 9)),
        (9, 5, (49, 5)),
    ],
)
def test_asamu_tit_for_tat_uses_one_shared_snapshot_then_swaps_or_adds_forty(
    own_before, opponent_before, expected
):
    current = state(left="asamu", right="sukuna")
    ready(current["sides"][0])
    event = record(current["sides"][0], ASAMU_MOVES[8], 0)
    finish_turn(current["sides"][0])
    # 招式自身已+4；设置目标快照时保留该事实，确保断言关注结算分支。
    current["sides"][0]["weight"] = own_before
    current["sides"][1]["weight"] = opponent_before
    finish_turn(current["sides"][1])
    summary = resolve_round(current, f"tit-for-tat-{own_before}-{opponent_before}")
    retaliation = summary["interactions"]["retaliations"][0]
    assert retaliation["side"] == 0 and retaliation["count"] == 1
    assert retaliation["was_lower"] is (own_before < opponent_before)
    assert tuple(item["weight"] for item in summary["before"]) == expected
    assert event["move_id"] == "asamu-tit-for-tat"


def test_asamu_domain_clash_win_copies_exactly_four_opponent_moves_deterministically():
    def source():
        current = state(left="asamu", right="sukuna")
        ready(current["sides"][0])
        record(current["sides"][0], ASAMU_MOVES[-1], 0)
        finish_turn(current["sides"][0])
        ready(current["sides"][1])
        record(current["sides"][1], FIGHTERS_BY_ID["sukuna"].moves[4], 1)
        finish_turn(current["sides"][1])
        return current

    wheel = (("side-0", 6), ("side-1", 8), ("tie", 6))
    seed = seed_for("1:domain:clash", wheel, "side-0", prefix="asamu-domain")
    first, second = source(), source()
    first_summary = resolve_round(first, seed)
    second_summary = resolve_round(second, seed)
    copies = first_summary["interactions"]["asamu_domain_copies"]
    assert len(copies) == 4
    assert [event["copy_slot"] for event in copies] == [1, 2, 3, 4]
    assert all(event["generated_by"] == "asamu-domain-copy" for event in copies)
    assert all(event["source_fighter_id"] == "sukuna" for event in copies)
    assert all(event["source_move_id"] in {move.move_id for move in FIGHTERS_BY_ID["sukuna"].moves} for event in copies)
    assert copies == second_summary["interactions"]["asamu_domain_copies"]
    card = _event_move_wheel(copies[-1], BATTLE_RULE_VERSION)
    labels = [segment.label for segment in card.segments]
    assert labels == [move.name for move in FIGHTERS_BY_ID["sukuna"].moves]
    assert card.selected_index is not None
    assert labels[card.selected_index] == copies[-1]["source_move_name"]
    assert "复制自宿傩猪" in card.title and "真实来源落点" in card.note


def test_future_simulation_zeroes_buffed_numeric_gain_but_keeps_functional_effects():
    current = state(left="juejue", right="sukuna")
    defender, attacker = current["sides"]
    ready(defender)
    record(defender, JUEJUE_VIRTUAL_MOVES[1], 0, functional_fighter_id="juejue")
    finish_turn(defender)
    attacker["black_flash_stacks"] = 3
    attacker["double"] = True
    ready(attacker)
    flash = record(attacker, FIGHTERS_BY_ID["sukuna"].moves[0], 1)
    finish_turn(attacker)
    summary = resolve_round(current, "future-simulation-single-target")
    future = summary["interactions"]["future_simulations"][0]
    assert future["candidate_ordinals"] == [flash["ordinal"]]
    assert future["cancelled_gain"] == flash["gain"]
    assert summary["before"][1]["weight"] == 5
    assert summary["before"][1]["black_flash_stacks"] == 4
    assert flash["multiplier"] == 2 and flash["extra_draws"] == 2


def test_v5_fraction_state_and_random_results_are_json_deterministic():
    first = state(seed="json-v5")
    ready(first["sides"][0])
    event = record(first["sides"][0], DANIYA_COMMON_MOVES[2], 0, seed="json-v5")
    finish_turn(first["sides"][0])
    first["sides"][1]["turn"]["done"] = True
    assert event["gain"] == Fraction(-521, 10)
    payload = dumps(first)
    assert payload == dumps(first)
    assert loads(payload) == first

    second = loads(payload)
    first_summary = resolve_round(first, "json-v5")
    second_summary = resolve_round(second, "json-v5")
    assert first_summary == second_summary
    assert first == second
