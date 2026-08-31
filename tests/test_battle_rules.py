"""用户定稿轮盘、核心无上限、分块精确重放和永久概率的纯规则验收。"""

from copy import deepcopy

import pytest

from pig_catcher.domain.battle import (
    apply_injury,
    apply_move,
    choose,
    dumps,
    loads,
    loot_weights,
    move_weight_units,
    new_state,
    play_chunk,
    randbelow,
    resolve_round,
    roll_count,
    weight_label,
)
from pig_catcher.domain.battle_catalog import (
    BATTLE_RULE_VERSION,
    COUNT_WHEEL,
    FIGHTERS,
    FIGHTERS_BY_ID,
    FIGHTERS_BY_TEMPLATE,
    HEAVY_COUNT_WHEEL,
    INJURY_WHEELS,
    JUEJUE_ACCELERATION_TIERS,
    JUEJUE_DELAY_TIERS,
    JUEJUE_FORM_TIME,
    JUEJUE_FORM_VIRTUAL,
    JUEJUE_PIG_TEMPLATE_IDS,
    JUEJUE_TIME_MOVES,
    JUEJUE_VIRTUAL_MOVES,
    UPGRADE_COSTS,
    Move,
    fighter_moves,
)
from pig_catcher.services.battle_views import effective_total_after, move_line


def state(level=0, trait=0, tool="", left="sukuna", right="gojo", seed=""):
    return new_state(
        [
            {"fighter_id": fighter_id, "level": level, "trait_bonus": trait, "tool_id": tool}
            for fighter_id in (left, right)
        ],
        seed=seed,
    )


def ready(player, pending=1):
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def test_exact_catalog_and_growth_costs():
    assert [len(f.moves) for f in FIGHTERS] == [10, 10, 16]
    assert all(move.draw_weight == 1 for fighter in FIGHTERS for move in fighter.moves)
    assert [m.gain for m in FIGHTERS[0].moves] == [10, 10, 15, 21, 35, 0, 14, 7, 12, 28]
    assert [m.gain for m in FIGHTERS[1].moves] == [13, 20, 14, 10, 10, 14, 24, 30, 14, 35]
    assert COUNT_WHEEL == ((1, 5), (2, 4), (3, 3), (4, 2), (5, 1))
    assert HEAVY_COUNT_WHEEL == COUNT_WHEEL[:-1]
    assert [[w for _, w in wheel] for wheel in INJURY_WHEELS] == [[13, 5, 1, 1], [5, 12, 2, 1], [2, 5, 12, 1]]
    assert {k: sum(row[k] for row in UPGRADE_COSTS) for k in UPGRADE_COSTS[0]} == {
        "ore": 1950,
        "parts": 650,
        "fiber": 650,
        "supplies": 650,
        "coins": 16600,
    }


@pytest.mark.parametrize("move", [move for fighter in FIGHTERS[:2] for move in fighter.moves])
def test_upgrade_only_positive_numeric_part(move):
    a, b = state()["sides"][0], state(5)["sides"][0]
    ready(a)
    ready(b)
    low, high = apply_move(a, move), apply_move(b, move)
    assert high["gain"] - low["gain"] == (5 if move.gain else 0)
    assert high["extra_draws"] == low["extra_draws"] == move.draws
    assert a["next_debt"] == b["next_debt"]


def test_future_mixed_move_has_numeric_upgrade_but_same_function():
    p = state(5)["sides"][0]
    ready(p)
    result = apply_move(p, Move("future-flash", "黑闪+5", 5, draws=2))
    assert result["gain"] == 10 and p["turn"]["pending"] == 2


@pytest.mark.parametrize("loans", [1, 2, 3, 50])
def test_loans_keep_only_one_double_and_debt_once(loans):
    s = state(5)
    p = s["sides"][0]
    ready(p)
    for _ in range(loans):
        event = apply_move(p, FIGHTERS[0].moves[5])
        assert event["gain"] == 0 and p["double"] and p["turn"]["pending"] == 1
    apply_move(p, Move("functional", "纯功能连抽", draws=2))
    assert p["double"] and p["turn"]["pending"] == 2
    hit = apply_move(p, FIGHTERS[0].moves[1])
    assert hit["gain"] == 30 and hit["multiplier"] == 2
    assert apply_move(p, FIGHTERS[0].moves[1])["gain"] == 15
    p["turn"]["raw"] = None
    result = roll_count(s, 0, "debt")
    assert result["debt"] == loans and result["effective"] == max(0, result["raw"] - loans)
    assert p["next_debt"] == 0
    assert not roll_count(s, 0, "debt")["changed"]


@pytest.mark.parametrize(
    "cores", [1, 5, 6, 50, 100, 2**70, 10**5000], ids=["1", "5", "6", "50", "100", "beyond-int64", "5001-digits"]
)
def test_core_unlimited_integer_persistence(cores):
    s = state(5)
    p = s["sides"][0]
    p.update(core=cores, heavy=True, risk=2, weight=10**1000)
    apply_injury(p, "core")
    assert p["core"] == cores + 1 and not p["heavy"] and p["risk"] == 2
    ready(p)
    p["double"] = True
    assert apply_move(p, Move("n", "数值招式", 10))["gain"] == (10 + 5 + cores + 1) * 2
    assert loads(dumps(s)) == s
    assert len(weight_label(p["weight"])) < 90


def test_injury_never_regresses_history_or_recalculates_weight():
    p = state()["sides"][0]
    p["weight"] = 947
    apply_injury(p, "heavy")
    apply_injury(p, "light")
    assert p["heavy"] and p["risk"] == 2
    apply_injury(p, "heavy")
    ready(p)
    assert apply_move(p, Move("a", "普通", 10))["gain"] == 9
    apply_injury(p, "core")
    assert not p["heavy"] and p["risk"] == 2 and p["weight"] == 956
    apply_injury(p, "light")
    assert p["risk"] == 2 and not p["heavy"]
    apply_injury(p, "heavy")
    ready(p)
    assert apply_move(p, Move("a", "普通", 10))["gain"] == 10


def test_zero_actions_still_resolve_and_only_half_current_round_gain_carries():
    s = state()
    for index, p in enumerate(s["sides"]):
        p.update(next_debt=50, weight=10 + index * 5)
        assert roll_count(s, index, "zero")["effective"] == 0
        assert play_chunk(s, index, "zero") == []
    summary = resolve_round(s, "zero")
    assert summary and [p["weight"] for p in s["sides"]] == [8, 10]
    assert [item["round_gain"] for item in summary["carryover"]] == [5, 10]
    assert [item["retained_gain"] for item in summary["carryover"]] == [3, 5]
    assert all(p["next_debt"] == 0 for p in s["sides"])


@pytest.mark.parametrize("seed", [f"chunk-{i}" for i in range(15)])
def test_command_order_and_chunk_size_do_not_change_outcome(seed):
    a, b = state(3), state(3)
    for s, order, size in ((a, [0, 1], 32), (b, [1, 0], 1)):
        for side in order:
            roll_count(s, side, seed)
            while not s["sides"][side]["turn"]["done"]:
                play_chunk(s, side, seed, chunk_size=size)
                s.update(loads(dumps(s)))
        resolve_round(s, seed)
    assert a == b


def test_exact_random_with_huge_bounds_no_float_overflow():
    bound = 10**5000
    assert 0 <= randbelow("huge", "winner", bound) < bound
    assert randbelow("huge", "winner", bound) == randbelow("huge", "winner", bound)
    assert randbelow("huge", "x", 1) == 0
    assert {choose(str(i), "small", (("a", 1), ("b", 1)))[0] for i in range(40)} == {"a", "b"}
    s = state()
    for side in s["sides"]:
        side["weight"] = bound
        side["turn"]["done"] = True
    assert resolve_round(s, "huge")["winner"] in (0, 1)


@pytest.mark.parametrize("tool,expected", [("", 21), ("wristband", 23), ("bandage", 23)])
def test_traits_and_tools_are_small_one_time_adjustments(tool, expected):
    p = state(0, trait=1, tool=tool)["sides"][0]
    p.update(heavy=True, double=True, core=1)
    ready(p, 2)
    # (10+0+1-1)*2 + trait1, 绷带取消-1会参与本次数值翻倍。
    result = apply_move(p, Move("n", "普通", 10))
    assert result["gain"] == expected
    assert apply_move(p, Move("n", "普通", 10))["gain"] == 10


@pytest.mark.parametrize("available", [True, False])
def test_loot_permanent_distribution_monotonic(available):
    base = loot_weights(level=1, feed=0, cloud=0, six_available=available)
    full = loot_weights(level=21, feed=10, cloud=5, six_available=available)
    assert sum(full) == pytest.approx(100)
    assert all(full[i] >= base[i] - 1e-9 for i in (3, 4, 5))
    if available:
        assert base == pytest.approx((5, 10, 10, 30, 30, 15))
        assert full == pytest.approx((3.2417, 6.4833, 6.4833, 30, 33.2692, 20.5225), abs=0.001)
    else:
        assert full[5] == 0 and base[4] == 45
        assert full == loot_weights(level=21, feed=10, cloud=0, six_available=False)


def _record(player: dict, move: Move, side: int) -> dict:
    event = apply_move(player, move)
    event.update(round=1, side=side, fighter_id=player["snapshot"]["fighter_id"])
    player["turn"]["events"].append(event.copy())
    return event


def _seed_for(key: str, wheel: tuple, expected: str) -> str:
    return next(seed for seed in (f"seed-{index}" for index in range(10000)) if choose(seed, key, wheel)[0] == expected)


def test_black_flash_adds_weight_draws_and_unlimited_match_stacks():
    p = state()["sides"][0]
    ready(p, 4)
    first = apply_move(p, FIGHTERS[0].moves[0])
    assert first["gain"] == 10 and first["extra_draws"] == 2 and p["black_flash_stacks"] == 1
    assert apply_move(p, FIGHTERS[0].moves[1])["gain"] == 11
    second = apply_move(p, FIGHTERS[0].moves[0])
    assert second["gain"] == 11 and p["black_flash_stacks"] == 2
    p["turn"] = {**p["turn"], "pending": 1, "done": False}
    assert apply_move(p, FIGHTERS[0].moves[1])["gain"] == 12


def test_black_flash_aura_reaches_loan_without_consuming_double_or_becoming_infinity_target():
    s = state()
    ready(s["sides"][0], 3)
    apply_move(s["sides"][0], FIGHTERS[0].moves[0])
    loan = _record(s["sides"][0], FIGHTERS[0].moves[5], 0)
    hit = _record(s["sides"][0], FIGHTERS[0].moves[1], 0)
    s["sides"][0]["turn"].update(pending=0, done=True)
    ready(s["sides"][1])
    _record(s["sides"][1], FIGHTERS[1].moves[3], 1)
    summary = resolve_round(s, "flash-loan-infinity")
    assert loan["gain"] == 1 and loan["base"] == 0 and loan["multiplier"] == 1
    assert hit["multiplier"] == 2 and hit["gain"] == 21
    assert summary["interactions"]["adjustments"][0][0]["ordinal"] == hit["ordinal"]


def test_cancelled_domain_updates_every_following_displayed_cumulative_total():
    s = state()
    ready(s["sides"][0], 2)
    domain = _record(s["sides"][0], FIGHTERS[0].moves[4], 0)
    follow_up = _record(s["sides"][0], FIGHTERS[0].moves[1], 0)
    ready(s["sides"][1])
    _record(s["sides"][1], FIGHTERS[1].moves[7], 1)
    wheel = (("side-0", 8), ("side-1", 6), ("tie", 6))
    summary = resolve_round(s, _seed_for("1:domain:clash", wheel, "side-1"))
    adjustments = {item["ordinal"]: item for item in summary["interactions"]["adjustments"][0]}
    assert adjustments[domain["ordinal"]]["gain"] == domain["gain"]
    final_total = effective_total_after(follow_up, adjustments)
    assert final_total == summary["before"][0]["weight"] == 15
    assert "累计15" in move_line(follow_up, effective_total=final_total).value


def test_blue_and_red_raise_both_purple_moves_then_either_purple_resets_the_bonus():
    p = state()["sides"][1]
    moves = FIGHTERS[1].moves
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 10
    ready(p, 2)
    apply_move(p, moves[0])
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 11
    apply_move(p, moves[1])
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 12
    assert all(move_weight_units(p, move) == 10 for move in moves if "purple" not in move.tags)
    p["turn"].update(pending=1, done=False)
    purple = apply_move(p, moves[6])
    assert purple["purple_weight_steps_before"] == purple["purple_weight_steps_used"] == 2
    assert purple["purple_weight_steps"] == 0
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 10
    p["turn"].update(pending=2, done=False)
    apply_move(p, moves[2])
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 11
    unlimited = apply_move(p, moves[9])
    assert unlimited["purple_weight_steps_used"] == 1 and p["purple_weight_steps"] == 0


def test_purple_reset_is_not_refunded_when_infinity_cancels_its_numeric_gain():
    s = state()
    s["sides"][1]["purple_weight_steps"] = 1
    ready(s["sides"][1])
    purple = _record(s["sides"][1], FIGHTERS[1].moves[6], 1)
    ready(s["sides"][0])
    _record(s["sides"][0], FIGHTERS[0].moves[1], 0)
    # 直接标记宿傩一侧的无下限，隔离验证结算取消不会倒流招式使用状态。
    s["sides"][0]["turn"]["infinity_used"] = True
    summary = resolve_round(s, "purple-cancelled")
    assert purple["purple_weight_steps_used"] == 1
    assert s["sides"][1]["purple_weight_steps"] == 0
    assert any(item["ordinal"] == 1 for item in summary["interactions"]["adjustments"][1])


@pytest.mark.parametrize("outcome", ["side-0", "side-1", "tie"])
def test_domain_clash_is_once_and_zeroes_every_losing_domain_gain(outcome):
    s = state()
    for side, move in ((0, FIGHTERS[0].moves[4]), (1, FIGHTERS[1].moves[7])):
        ready(s["sides"][side], 2)
        _record(s["sides"][side], move, side)
        _record(s["sides"][side], move, side)
    wheel = (("side-0", 8), ("side-1", 6), ("tie", 6))
    summary = resolve_round(s, _seed_for("1:domain:clash", wheel, outcome))
    domain = summary["interactions"]["domain"]
    assert domain["outcome"] == outcome and domain["domain_counts"] == [2, 2]
    assert domain["wheel"] == wheel
    if outcome == "side-0":
        assert summary["before"][1]["weight"] == 5
        assert domain["boost_side"] == 0 and domain["boosted_ordinal"] == 1
        assert domain["bonus_gain"] == 35 and summary["before"][0]["weight"] == 110
    elif outcome == "side-1":
        assert summary["before"][0]["weight"] == 5
        assert summary["before"][0]["next_debt"] == 1
        assert domain["boost_side"] == 1 and domain["boosted_ordinal"] == 1
        assert domain["bonus_gain"] == 30 and summary["before"][1]["weight"] == 95
    else:
        assert [side["weight"] for side in summary["before"]] == [5, 5]
        assert domain["boost_side"] is None and domain["bonus_gain"] == 0


@pytest.mark.parametrize("outcome", ["hit", "simple-domain"])
def test_single_gojo_domain_uses_eight_two_and_effect_only_on_hit(outcome):
    s = state()
    s["sides"][0]["turn"]["done"] = True
    ready(s["sides"][1])
    _record(s["sides"][1], FIGHTERS[1].moves[7], 1)
    wheel = (("hit", 8), ("simple-domain", 2))
    summary = resolve_round(s, _seed_for("1:domain:solo:1", wheel, outcome))
    assert summary["interactions"]["domain"]["outcome"] == outcome
    assert summary["interactions"]["domain"]["bonus_gain"] == 0
    if outcome == "hit":
        assert summary["before"][0]["next_debt"] == 1 and summary["before"][1]["weight"] == 35
    else:
        assert summary["before"][0]["next_debt"] == 0 and summary["before"][1]["weight"] == 5


def test_infinity_skips_loan_and_cancels_only_first_still_effective_numeric_move():
    s = state()
    ready(s["sides"][0], 2)
    _record(s["sides"][0], FIGHTERS[0].moves[5], 0)
    first = _record(s["sides"][0], FIGHTERS[0].moves[1], 0)
    second = _record(s["sides"][0], FIGHTERS[0].moves[1], 0)
    ready(s["sides"][1])
    _record(s["sides"][1], FIGHTERS[1].moves[3], 1)
    summary = resolve_round(s, "infinity")
    adjustment = summary["interactions"]["adjustments"][0]
    assert adjustment == ({"ordinal": 2, "gain": first["gain"], "reasons": ["无下限·防御"]},)
    assert summary["before"][0]["weight"] == 5 + second["gain"]


def test_infinity_zeroes_black_flash_gain_but_keeps_its_function_and_stack():
    s = state()
    ready(s["sides"][0])
    flash = _record(s["sides"][0], FIGHTERS[0].moves[0], 0)
    s["sides"][0]["turn"].update(pending=0, done=True)
    ready(s["sides"][1])
    _record(s["sides"][1], FIGHTERS[1].moves[3], 1)
    summary = resolve_round(s, "flash-infinity")
    assert summary["before"][0]["weight"] == 5
    assert summary["before"][0]["black_flash_stacks"] == 1
    assert flash["extra_draws"] == 2


def _resolve_non_terminal(source: dict, prefix: str) -> tuple[dict, dict]:
    for index in range(10_000):
        candidate = deepcopy(source)
        result = resolve_round(candidate, f"{prefix}-{index}")
        if result and not result["natural_end"]:
            return candidate, result
    raise AssertionError("无法找到非终局固定种子")


def test_each_round_gain_is_halved_once_rounded_up_and_kept_for_later_rounds():
    s = state()
    for player, gain in zip(s["sides"], (1, 2), strict=True):
        player["weight"] += gain
        player["turn"]["done"] = True
    s, first = _resolve_non_terminal(s, "carry-r1")
    assert [item["retained_gain"] for item in first["carryover"]] == [1, 1]
    assert [side["weight"] for side in s["sides"]] == [6, 6]

    for player, gain in zip(s["sides"], (3, 4), strict=True):
        player["weight"] += gain
        player["turn"]["done"] = True
    s, second = _resolve_non_terminal(s, "carry-r2")
    assert [item["retained_gain"] for item in second["carryover"]] == [2, 2]
    assert [side["weight"] for side in s["sides"]] == [8, 8]
    assert [side["round_gains"] for side in s["sides"]] == [[1, 3], [2, 4]]
    assert all(side["round_start_weight"] == 5 + 1 + 2 for side in s["sides"])

    s["sides"][0]["weight"] += 5
    assert s["sides"][0]["weight"] == 5 + 1 + 2 + 5


def test_domain_bonus_enters_net_round_gain_and_displayed_cumulative_total():
    s = state()
    for side, move in ((0, FIGHTERS[0].moves[4]), (1, FIGHTERS[1].moves[7])):
        ready(s["sides"][side])
        event = _record(s["sides"][side], move, side)
        if side == 0:
            boosted_event = event
    wheel = (("side-0", 8), ("side-1", 6), ("tie", 6))
    summary = resolve_round(s, _seed_for("1:domain:clash", wheel, "side-0"))
    domain = summary["interactions"]["domain"]
    bonus = {"ordinal": domain["boosted_ordinal"], "gain": domain["bonus_gain"]}
    assert summary["carryover"][0]["round_gain"] == 70
    assert effective_total_after(boosted_event, {}, bonus) == summary["before"][0]["weight"] == 75
    assert "领域战获胜" in move_line(
        boosted_event,
        effective_total=summary["before"][0]["weight"],
        domain_bonus=bonus,
    ).note


def test_huge_odd_round_gain_uses_integer_ceiling_without_float_conversion():
    s = state()
    huge = 10**5000 + 1
    for player in s["sides"]:
        player["weight"] = 5 + huge
        player["turn"]["done"] = True
    next_state, result = _resolve_non_terminal(s, "huge-carry")
    assert result["carryover"][0]["retained_gain"] == (huge + 1) // 2
    assert next_state["sides"][0]["weight"] == 5 + (huge + 1) // 2
    assert loads(dumps(next_state)) == next_state


def test_juejue_catalog_aliases_forms_and_legacy_query_boundary():
    assert BATTLE_RULE_VERSION == 4
    assert all(FIGHTERS_BY_TEMPLATE[template_id].fighter_id == "juejue" for template_id in JUEJUE_PIG_TEMPLATE_IDS)
    assert {move.move_id for move in JUEJUE_TIME_MOVES}.isdisjoint(
        {move.move_id for move in JUEJUE_VIRTUAL_MOVES}
    )
    assert len(JUEJUE_TIME_MOVES) == len(JUEJUE_VIRTUAL_MOVES) == 8
    assert fighter_moves("juejue", 3) == ()
    assert fighter_moves("juejue", 4) == JUEJUE_TIME_MOVES + JUEJUE_VIRTUAL_MOVES


def test_juejue_entry_form_is_seeded_fifty_fifty_and_persists_exactly():
    observed = set()
    for index in range(100):
        seed = f"entry-form-{index}"
        first = state(left="juejue", right="gojo", seed=seed)
        second = state(left="juejue", right="gojo", seed=seed)
        observed.add(first["sides"][0]["juejue_form"])
        assert first["sides"][0]["juejue_form"] == second["sides"][0]["juejue_form"]
        assert first["sides"][0]["juejue_form_roll"] == second["sides"][0]["juejue_form_roll"]
        assert loads(dumps(first))["sides"][0]["juejue_form"] == first["sides"][0]["juejue_form"]
    assert observed == {JUEJUE_FORM_TIME, JUEJUE_FORM_VIRTUAL}


@pytest.mark.parametrize(
    "start_form,switch_id,target_form,target_ids",
    [
        (JUEJUE_FORM_TIME, "switch-virtual", JUEJUE_FORM_VIRTUAL, {m.move_id for m in JUEJUE_VIRTUAL_MOVES}),
        (JUEJUE_FORM_VIRTUAL, "switch-sand", JUEJUE_FORM_TIME, {m.move_id for m in JUEJUE_TIME_MOVES}),
    ],
)
def test_juejue_switch_reloads_the_new_form_before_the_next_draw_in_same_chunk(
    start_form, switch_id, target_form, target_ids
):
    for index in range(10_000):
        seed = f"switch-form-{start_form}-{index}"
        candidate = state(left="juejue", right="gojo", seed=seed)
        player = candidate["sides"][0]
        if player["juejue_form"] != start_form:
            continue
        ready(player)
        events = play_chunk(candidate, 0, seed, chunk_size=2)
        if events[0]["move_id"] != switch_id:
            continue
        assert events[0]["form_before"] == start_form
        assert events[0]["form_after"] == target_form
        assert events[1]["form_before"] == target_form
        assert events[1]["move_id"] in target_ids
        assert events[1]["draw_wheel_move_ids"] == [move.move_id for move in (
            JUEJUE_TIME_MOVES if target_form == JUEJUE_FORM_TIME else JUEJUE_VIRTUAL_MOVES
        )]
        return
    raise AssertionError("没有找到固定种子触发形态切换")


def test_juejue_domain_main_wheel_base_and_dynamic_draw_weights_are_exact():
    s = state(left="juejue", right="gojo", seed="draw-weights")
    player = s["sides"][0]
    sand_domain = JUEJUE_TIME_MOVES[-1]
    chaos_domain = JUEJUE_VIRTUAL_MOVES[-1]
    assert sand_domain.draw_weight == chaos_domain.draw_weight == 1
    assert move_weight_units(player, sand_domain) == move_weight_units(player, chaos_domain) == 10
    player["juejue_sand_domain_steps"] = 3
    player["juejue_sand_domain_switch_units"] = 5
    player["turn"]["juejue_realtime"] = True
    assert move_weight_units(player, sand_domain) == 28
    assert move_weight_units(player, chaos_domain) == 20
    ready(player)
    event = apply_move(player, sand_domain)
    assert event["sand_domain_steps_before"] == 3
    assert event["sand_domain_steps_after"] == 0
    assert event["sand_domain_switch_units_after"] == 0
    assert move_weight_units(player, sand_domain) == move_weight_units(player, chaos_domain) == 20


def _zero_sequence(expected: bool) -> tuple[dict, dict, dict]:
    for index in range(20_000):
        seed = f"relative-zero-{expected}-{index}"
        s = state(left="juejue", right="gojo", seed=seed)
        player = s["sides"][0]
        ready(player, 2)
        player["juejue_guaranteed"] = True
        acceleration = apply_move(
            player, JUEJUE_TIME_MOVES[2], seed=seed, round_number=1, side=0, version=4
        )
        player["juejue_guaranteed"] = True
        delay = apply_move(player, JUEJUE_TIME_MOVES[3], seed=seed, round_number=1, side=0, version=4)
        if (
            acceleration["subwheel"]["tier"] + delay["subwheel"]["tier"] >= 5
            and delay["relative_zero"] is not None
            and delay["relative_zero"]["success"] is expected
        ):
            return s, acceleration, delay
    raise AssertionError("没有找到相对静止·零固定种子")


@pytest.mark.parametrize("expected", [True, False])
def test_juejue_acceleration_delay_and_relative_zero_are_exact_and_once_per_round(expected):
    s, acceleration, delay = _zero_sequence(expected)
    player = s["sides"][0]
    assert acceleration["subwheel"]["tier_wheel"] == tuple((tier.tier, 1) for tier in JUEJUE_ACCELERATION_TIERS)
    assert delay["subwheel"]["tier_wheel"] == tuple((tier.tier, 1) for tier in JUEJUE_DELAY_TIERS)
    assert acceleration["subwheel"]["success"] and delay["subwheel"]["success"]
    assert acceleration["subwheel"]["guaranteed"] and delay["subwheel"]["guaranteed"]
    assert delay["relative_zero"]["wheel"] == ((True, 1), (False, 1))
    assert delay["zero_gain"] == (40 if expected else 0)
    assert player["turn"]["juejue_zero_checked"]
    player["juejue_guaranteed"] = True
    follow = apply_move(
        player,
        JUEJUE_TIME_MOVES[2],
        seed="zero-does-not-recheck",
        round_number=1,
        side=0,
        version=4,
    )
    assert follow["relative_zero"] is None


def _juejue_delay_event(*, success: bool) -> tuple[dict, dict]:
    for index in range(20_000):
        seed = f"juejue-delay-{success}-{index}"
        s = state(left="juejue", right="juejue", seed=seed)
        player = s["sides"][0]
        ready(player)
        event = apply_move(
            player, JUEJUE_TIME_MOVES[3], seed=seed, round_number=1, side=0, version=4
        )
        if event["subwheel"]["success"] is success and (
            (success and event["opponent_next_debt"] > 0)
            or (not success and event["opponent_next_bonus"] > 0)
        ):
            event.update(round=1, side=0, fighter_id="juejue")
            player["turn"]["events"].append(deepcopy(event))
            player["turn"].update(pending=0, done=True)
            return s, event
    raise AssertionError("没有找到撅撅猪时延固定种子")


def test_relative_zero_suppresses_juejue_on_juejue_round_reduction_and_cross_round_debt():
    s, delay = _juejue_delay_event(success=True)
    defender = s["sides"][1]
    defender["turn"].update(done=True, juejue_zero_active=True)
    summary = resolve_round(s, "juejue-zero-defense")
    before = summary["before"]
    assert before[0]["weight"] == 5
    assert before[1]["weight"] == 5
    assert before[1]["next_debt"] == 0
    cross = summary["interactions"]["cross_effects"][0]
    assert cross["round_reduction_suppressed"] and cross["debt_suppressed"]
    assert cross["round_reduction"] == cross["next_debt"] == 0
    assert delay["ordinal"] in summary["interactions"]["zeroes"][1]["cancelled_ordinals"]


def test_relative_zero_keeps_failed_delay_bonus_for_the_defender():
    s, delay = _juejue_delay_event(success=False)
    defender = s["sides"][1]
    defender["turn"].update(done=True, juejue_zero_active=True)
    summary = resolve_round(s, "juejue-zero-delay-failure")
    assert summary["before"][1]["next_action_bonus"] == delay["opponent_next_bonus"]


def _chaos_domain_hits_relative_zero(*, direction: str = "self") -> dict:
    s = state(left="juejue", right="juejue", seed=f"zero-auto-mimic-{direction}")
    attacker, defender = s["sides"]
    if direction == "opponent":
        attacker["juejue_mimic_pool"] = {
            "large": [],
            "small": [
                {
                    "fighter_id": "future",
                    "move_id": "future-opponent-reduction",
                    "name": "未来减权招式",
                    "base": 13,
                    "direction": "opponent",
                }
            ],
        }
    ready(attacker)
    _record(attacker, JUEJUE_VIRTUAL_MOVES[-1], 0)
    defender["turn"].update(raw=0, effective=0, pending=0, done=True, juejue_zero_active=True)
    seed = _seed_for("1:domain:solo:0", (("hit", 8), ("simple-domain", 2)), "hit")
    return resolve_round(s, seed)


@pytest.mark.parametrize("direction", ["self", "opponent"])
def test_relative_zero_suppresses_late_domain_auto_mimic_numeric_but_keeps_functions(direction):
    summary = _chaos_domain_hits_relative_zero(direction=direction)
    domain = summary["interactions"]["domain"]
    mimic = domain["auto_mimic"]
    assert domain["outcome"] == "hit" and mimic["available"]
    assert mimic["direction"] == direction
    assert mimic["numeric_suppressed"] and mimic["suppressed_reason"] == "相对静止·零"
    assert mimic["gain"] == mimic["opponent_reduction"] == 0
    assert mimic["raw_gain"] > 0 if direction == "self" else mimic["raw_opponent_reduction"] > 0
    assert summary["before"][0]["weight"] == summary["before"][1]["weight"] == 5
    assert summary["before"][0]["next_action_bonus"] == 1
    assert summary["before"][0]["juejue_guaranteed"]
    assert "领域功能仍生效" in domain["effect"]


def test_domain_auto_mimic_numeric_still_applies_without_relative_zero():
    s = state(left="juejue", right="juejue", seed="auto-mimic-control")
    ready(s["sides"][0])
    chaos = _record(s["sides"][0], JUEJUE_VIRTUAL_MOVES[-1], 0)
    s["sides"][1]["turn"].update(raw=0, effective=0, pending=0, done=True)
    seed = _seed_for("1:domain:solo:0", (("hit", 8), ("simple-domain", 2)), "hit")
    summary = resolve_round(s, seed)
    mimic = summary["interactions"]["domain"]["auto_mimic"]
    assert mimic["available"] and not mimic["numeric_suppressed"]
    assert mimic["gain"] == mimic["raw_gain"] > 0
    assert summary["before"][0]["weight"] == 5 + chaos["gain"] + mimic["gain"]


def test_juejue_mimic_uses_frozen_non_juejue_numeric_pool_and_growth_once():
    s = state(level=5, left="juejue", right="gojo", seed="mimic")
    player = s["sides"][0]
    player.update(core=2, heavy=True)
    ready(player)
    event = apply_move(
        player, JUEJUE_VIRTUAL_MOVES[3], seed="mimic", round_number=1, side=0, version=4
    )
    mimic = event["mimic"]
    assert mimic["available"] and mimic["source_fighter_id"] in {"sukuna", "gojo"}
    assert mimic["source_fighter_id"] != "juejue"
    assert event["special_base"] == abs(mimic["base"])
    assert event["gain"] == abs(mimic["base"]) + 5 + 2 - 1
    assert event["tags"] == ["juejue-mimic"]
    assert event["extra_draws"] == 0 and not event["loan"]


def test_make_real_grows_only_its_direct_numeric_value_for_the_whole_match():
    player = state(left="juejue", right="gojo", seed="make-real")["sides"][0]
    ready(player, 3)
    events = [apply_move(player, JUEJUE_VIRTUAL_MOVES[4]) for _ in range(3)]
    assert [event["special_base"] for event in events] == [12, 17, 22]
    assert [event["gain"] for event in events] == [12, 17, 22]
    assert player["juejue_realization_stacks"] == 3


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("gojo", "gojo", (("side-0", 6), ("side-1", 6), ("tie", 6))),
        ("sukuna", "sukuna", (("side-0", 8), ("side-1", 8), ("tie", 6))),
        ("juejue", "gojo", (("side-0", 5), ("side-1", 6), ("tie", 6))),
    ],
)
def test_domain_clash_uses_scaled_integer_strengths(left, right, expected):
    s = state(left=left, right=right, seed=f"domain-{left}-{right}")
    moves = {
        "gojo": FIGHTERS_BY_ID["gojo"].moves[7],
        "sukuna": FIGHTERS_BY_ID["sukuna"].moves[4],
        "juejue": JUEJUE_TIME_MOVES[-1],
    }
    for side, fighter_id in enumerate((left, right)):
        ready(s["sides"][side])
        _record(s["sides"][side], moves[fighter_id], side)
    summary = resolve_round(s, f"domain-{left}-{right}")
    assert summary["interactions"]["domain"]["wheel"] == expected
    assert summary["interactions"]["domain"]["weight_scale"] == 2


def test_juejue_distinct_dual_domain_has_eleven_strength_and_only_winning_clash_special():
    s = state(left="juejue", right="sukuna", seed="dual-domain")
    ready(s["sides"][0], 2)
    sand = _record(s["sides"][0], JUEJUE_TIME_MOVES[-1], 0)
    chaos = _record(s["sides"][0], JUEJUE_VIRTUAL_MOVES[-1], 0)
    ready(s["sides"][1])
    shrine = _record(s["sides"][1], FIGHTERS_BY_ID["sukuna"].moves[4], 1)
    wheel = (("side-0", 11), ("side-1", 8), ("tie", 6))
    seed = _seed_for("1:domain:clash", wheel, "side-0")
    summary = resolve_round(s, seed)
    domain = summary["interactions"]["domain"]
    assert domain["wheel"] == wheel and domain["dual_juejue"] == [True, False]
    assert domain["boosted_ordinals"] == [sand["ordinal"], chaos["ordinal"]]
    assert domain["bonus_gain"] == sand["gain"] + chaos["gain"] == 40
    assert domain["nullified_side"] == 1
    assert domain["auto_mimic"] and domain["auto_mimic"]["available"]
    assert summary["before"][0]["weight"] == 5 + 25 + 15 + 40 + domain["auto_mimic"]["gain"]
    assert summary["before"][0]["next_action_bonus"] == 2
    assert summary["before"][0]["juejue_guaranteed"]
    assert summary["before"][1]["weight"] == 5
    assert shrine["ordinal"] in summary["interactions"]["zeroes"][0]["cancelled_ordinals"] or any(
        shrine["ordinal"] == item["ordinal"] for item in summary["interactions"]["adjustments"][1]
    )


def test_juejue_rewind_removes_only_the_new_light_or_heavy_injury():
    source = state(left="juejue", right="sukuna", seed="rewind")
    source["sides"][0]["turn"].update(done=True, juejue_rewind=True)
    source["sides"][1].update(weight=100)
    source["sides"][1]["turn"]["done"] = True
    for index in range(20_000):
        candidate = deepcopy(source)
        summary = resolve_round(candidate, f"rewind-{index}")
        if summary["loser"] == 0 and summary["injury"] in {"light", "heavy"}:
            assert summary["injury_rewound"] and summary["injury_effective"] == "none"
            assert not summary["after"][0]["heavy"] and summary["after"][0]["risk"] == 0
            return
    raise AssertionError("没有找到回溯伤势固定种子")


def test_juejue_command_order_chunking_and_serialization_are_deterministic():
    seed = "juejue-order-and-chunk"
    a = state(level=3, left="juejue", right="gojo", seed=seed)
    b = state(level=3, left="juejue", right="gojo", seed=seed)
    for current, order, size in ((a, (0, 1), 32), (b, (1, 0), 1)):
        for side in order:
            roll_count(current, side, seed)
            guard = 0
            while not current["sides"][side]["turn"]["done"]:
                play_chunk(current, side, seed, chunk_size=size)
                current.update(loads(dumps(current)))
                guard += 1
                assert guard < 10_000
        resolve_round(current, seed)
    assert a == b


def test_v3_payload_remains_lossless_but_is_not_silently_resumed_by_v4_engine():
    legacy = state()
    legacy["version"] = 3
    restored = loads(dumps(legacy))
    assert restored == legacy
    with pytest.raises(Exception, match="另一版本规则"):
        roll_count(restored, 0, "must-not-recalculate")
