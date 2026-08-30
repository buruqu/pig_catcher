"""用户定稿轮盘、核心无上限、分块精确重放和永久概率的纯规则验收。"""

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
    COUNT_WHEEL,
    FIGHTERS,
    HEAVY_COUNT_WHEEL,
    INJURY_WHEELS,
    UPGRADE_COSTS,
    Move,
)
from pig_catcher.services.battle_views import effective_total_after, move_line


def state(level=0, trait=0, tool=""):
    return new_state(
        [{"fighter_id": item.fighter_id, "level": level, "trait_bonus": trait, "tool_id": tool} for item in FIGHTERS]
    )


def ready(player, pending=1):
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def test_exact_catalog_and_growth_costs():
    assert [len(f.moves) for f in FIGHTERS] == [10, 10]
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


@pytest.mark.parametrize("move", [move for fighter in FIGHTERS for move in fighter.moves])
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


def test_zero_actions_still_resolve_and_weight_is_cumulative():
    s = state()
    for index, p in enumerate(s["sides"]):
        p.update(next_debt=50, weight=10 + index * 5)
        assert roll_count(s, index, "zero")["effective"] == 0
        assert play_chunk(s, index, "zero") == []
    summary = resolve_round(s, "zero")
    assert summary and [p["weight"] for p in s["sides"]] == [10, 15]
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
    wheel = (("side-0", 4), ("side-1", 3), ("tie", 3))
    summary = resolve_round(s, _seed_for("1:domain:clash", wheel, "side-1"))
    adjustments = {item["ordinal"]: item for item in summary["interactions"]["adjustments"][0]}
    assert adjustments[domain["ordinal"]]["gain"] == domain["gain"]
    final_total = effective_total_after(follow_up, adjustments)
    assert final_total == summary["before"][0]["weight"] == 15
    assert "累计15" in move_line(follow_up, effective_total=final_total).value


def test_blue_and_red_raise_both_purple_move_draw_weights_by_exact_tenths():
    p = state()["sides"][1]
    moves = FIGHTERS[1].moves
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 10
    ready(p, 2)
    apply_move(p, moves[0])
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 11
    apply_move(p, moves[1])
    assert move_weight_units(p, moves[6]) == move_weight_units(p, moves[9]) == 12
    assert all(move_weight_units(p, move) == 10 for move in moves if "purple" not in move.tags)


@pytest.mark.parametrize("outcome", ["side-0", "side-1", "tie"])
def test_domain_clash_is_once_and_zeroes_every_losing_domain_gain(outcome):
    s = state()
    for side, move in ((0, FIGHTERS[0].moves[4]), (1, FIGHTERS[1].moves[7])):
        ready(s["sides"][side], 2)
        _record(s["sides"][side], move, side)
        _record(s["sides"][side], move, side)
    wheel = (("side-0", 4), ("side-1", 3), ("tie", 3))
    summary = resolve_round(s, _seed_for("1:domain:clash", wheel, outcome))
    domain = summary["interactions"]["domain"]
    assert domain["outcome"] == outcome and domain["domain_counts"] == [2, 2]
    assert domain["wheel"] == wheel
    if outcome == "side-0":
        assert summary["before"][1]["weight"] == 5
    elif outcome == "side-1":
        assert summary["before"][0]["weight"] == 5
        assert summary["before"][0]["next_debt"] == 1
    else:
        assert [side["weight"] for side in summary["before"]] == [5, 5]


@pytest.mark.parametrize("outcome", ["hit", "simple-domain"])
def test_single_gojo_domain_uses_eight_two_and_effect_only_on_hit(outcome):
    s = state()
    s["sides"][0]["turn"]["done"] = True
    ready(s["sides"][1])
    _record(s["sides"][1], FIGHTERS[1].moves[7], 1)
    wheel = (("hit", 8), ("simple-domain", 2))
    summary = resolve_round(s, _seed_for("1:domain:solo:1", wheel, outcome))
    assert summary["interactions"]["domain"]["outcome"] == outcome
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
