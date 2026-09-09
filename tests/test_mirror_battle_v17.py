"""新输入规则的边界与结算验收，不使用真实玩家或正式数据库。"""

from copy import deepcopy
from fractions import Fraction

import pytest

from pig_catcher.domain import battle, mirror_battle
from pig_catcher.domain.battle_catalog import FIGHTERS, FIGHTERS_BY_ID, Move, fighter_moves
from tests.test_battle_miumiu import finish_moves


def state(left="miumiu", right="luoli"):
    return battle.new_state(
        [{"fighter_id": f, "level": 0, "trait_bonus": 0, "tool_id": ""} for f in (left, right)], seed="v17"
    )


def play(current, side, move_id):
    player = current["sides"][side]
    player["turn"].update(raw=1, effective=1, pending=1, done=False)
    move = next(m for m in FIGHTERS_BY_ID[player["snapshot"]["fighter_id"]].moves if m.move_id == move_id)
    event = battle.apply_move(player, move, side=side, round_number=current["round"])
    event.update(side=side, round=current["round"], fighter_id=player["snapshot"]["fighter_id"])
    player["turn"]["events"].append(event)
    return event


def plain(current, side, amount=200):
    player = current["sides"][side]
    player["turn"].update(raw=1, effective=1, pending=1, done=False)
    event = battle.apply_move(player, Move("test-numeric", "测试数值", amount), side=side)
    player["turn"]["events"].append(event)
    return event


def settle(current, monkeypatch, hit=True):
    original = battle.choose

    def forced(seed, key, wheel, **kwargs):
        if "domain:solo" in key:
            return ("hit" if hit else "simple-domain"), 0
        if key.endswith(":injury"):
            return "light", 0
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", forced)
    for player in current["sides"]:
        player["turn"].update(done=True, pending=0)
    return battle.resolve_round(current, "v17")


def test_catalog_matches_both_sources_and_preserves_v16():
    assert [m.gain for m in fighter_moves("miumiu")] == [20, 18, 16, 24, 20, 10, 0, 21, 26, 30]
    assert [m.resolved_draw_weight_units for m in fighter_moves("miumiu")] == [
        10000,
        7000,
        10000,
        10000,
        8000,
        10000,
        9000,
        8000,
        7000,
        5000,
    ]
    assert [m.resolved_draw_weight_units for m in fighter_moves("luoli")] == [10000] * 6 + [
        7000,
        6000,
        5000,
        2000,
        2000,
    ]
    assert [m.move_id for m in fighter_moves("miumiu", 16)][0] == "miumiu-flow"
    assert fighter_moves("miumiu", 16)[-1].gain == 24
    assert not fighter_moves("luoli", 16)


def test_observation_ignores_reductions_negative_and_functional_auras():
    player = {
        "weight": 100,
        "turn": {
            "events": [
                {"ordinal": 1, "numeric_base": True, "gain": 5, "opponent_reduction": 100},
                {"ordinal": 2, "numeric_base": True, "gain": -200},
                {"ordinal": 3, "numeric_base": False, "gain": 100},
                {"ordinal": 4, "numeric_base": True, "gain": 30},
            ]
        },
    }
    assert mirror_battle.observation(player)["top"]["ordinal"] == 4


@pytest.mark.parametrize("humidity,consumed,debt", [(0, 2, 0), (3, 5, 0), (4, 6, 1), (8, 10, 1)])
def test_blank_exact_consumption_boundary_and_final_points(monkeypatch, humidity, consumed, debt):
    current = state()
    current["sides"][0]["miumiu_humidity"] = humidity
    play(current, 0, "miumiu-blank")
    plain(current, 1)
    result = settle(current, monkeypatch)
    fact = next(f for f in result["interactions"]["mirror"] if "consumed" in f)
    assert fact["consumed"] == consumed and fact["next_debt"] == debt
    assert fact["after"] == 205 - consumed * 10
    assert current["sides"][0]["miumiu_humidity"] == 0
    assert current["sides"][1]["next_debt"] == debt
    assert "miumiu_reconstructions" not in result


def test_blank_failed_domain_preserves_opponent(monkeypatch):
    current = state()
    current["sides"][0]["miumiu_humidity"] = 8
    play(current, 0, "miumiu-blank")
    plain(current, 1)
    result = settle(current, monkeypatch, hit=False)
    assert result["before"][1]["weight"] == 205
    assert result["before"][1]["next_debt"] == 0
    assert current["sides"][0]["miumiu_humidity"] == 0


def test_water_life_recovers_100_points_to_20_without_consumption(monkeypatch):
    current = state()
    current["sides"][0].update(miumiu_humidity=4, injury_exhaust_bonus_units=995)
    play(current, 0, "miumiu-water-life")
    plain(current, 1)
    result = settle(current, monkeypatch)
    recovery = next(f for f in result["interactions"]["mirror"] if "exhaust_after_units" in f)
    assert recovery["exhaust_before_units"] == 1000
    assert recovery["exhaust_after_units"] == 200
    assert current["sides"][0]["miumiu_humidity"] == 4
    _, injury = battle._dynamic_injury_wheel(current, 0)
    assert injury["mirror_exhaust_before_sleep_units"] >= 200


def test_water_has_one_point_floor_and_zero_layers_do_nothing(monkeypatch):
    current = state()
    play(current, 0, "miumiu-water-life")
    assert not current["sides"][0]["turn"].get("miumiu_recoveries")
    current["sides"][0]["miumiu_humidity"] = 100
    play(current, 0, "miumiu-water-life")
    plain(current, 1)
    result = settle(current, monkeypatch)
    assert next(f["exhaust_after_units"] for f in result["interactions"]["mirror"] if "exhaust_after_units" in f) == 10


def test_observe_has_no_cap_and_mimic_does_not_copy_functions(monkeypatch):
    current = state()
    top = {"gain": Fraction(200), "extra_draws": 9, "loan": True}
    current["miumiu_observations"] = [{"top": None}, {"top": top}]
    current["sides"][0]["turn"]["miumiu_observed"] = {"top": top}
    mimic = play(current, 0, "miumiu-mimic")
    assert mimic["gain"] == 200 and not mimic["loan"] and mimic["extra_draws"] == 0
    play(current, 0, "miumiu-observe")
    plain(current, 1)
    result = settle(current, monkeypatch)
    assert next(f["bonus"] for f in result["interactions"]["mirror"] if "bonus" in f) == 50


def test_coupling_targets_next_logical_numeric_then_carries(monkeypatch):
    current = state()
    play(current, 0, "miumiu-coupling")
    first = plain(current, 1, 20)
    second = plain(current, 1, 20)
    result = settle(current, monkeypatch)
    assert result["interactions"]["adjustments"][1][0]["ordinal"] == second["ordinal"]
    assert result["interactions"]["adjustments"][1][0]["gain"] == 10
    assert first["gain"] == 20
    play(current, 0, "miumiu-coupling")
    plain(current, 1, 20)
    settle(current, monkeypatch)
    assert current["sides"][1]["miumiu_pending_reductions"] == 10


def test_shallow_zeroes_numeric_and_preserves_function_draws(monkeypatch):
    current = state()
    event = play(current, 0, "miumiu-shallow")
    assert event["extra_draws"] == 1
    target = plain(current, 1, 20)
    target["extra_draws"] = 2
    result = settle(current, monkeypatch)
    assert result["interactions"]["adjustments"][1][0]["gain"] == 20
    assert result["before"][1]["turn"]["events"][0]["extra_draws"] == 2


def test_rebuild_and_disturb_last_exactly_next_round(monkeypatch):
    current = state()
    play(current, 0, "miumiu-softening")
    play(current, 0, "miumiu-rebuild")
    play(current, 0, "miumiu-disturb")
    plain(current, 1, 20)
    settle(current, monkeypatch)
    assert current["sides"][0]["turn"]["miumiu_rebuild_bonus"] == 4
    assert current["sides"][1]["turn"]["miumiu_disturbed"]
    assert play(current, 0, "miumiu-softening")["gain"] == 20
    plain(current, 1, 20)
    result = settle(current, monkeypatch)
    assert result["interactions"]["adjustments"][1][0]["gain"] == 4
    assert current["sides"][0]["turn"]["miumiu_rebuild_bonus"] == 0
    assert not current["sides"][1]["turn"]["miumiu_disturbed"]


def test_luoli_combos_cat_once_and_preserved_stacks(monkeypatch):
    current = state("luoli", "miumiu")
    for slug in (
        "parfait",
        "soba",
        "mutsumi",
        "guitar",
        "with-cat",
        "matcha-feast",
        "joint-live",
        "pet-cat",
        "pet-cat",
    ):
        play(current, 0, "luoli-" + slug)
    assert current["sides"][0]["luoli_cucumbers"] == 11
    assert sum(e["mirror"].get("allergy", False) for e in current["sides"][0]["turn"]["events"]) == 1
    plain(current, 1, 200)
    result = settle(current, monkeypatch)
    assert result["before"][1]["luoli_bills"] == 11
    assert current["sides"][0]["luoli_cucumbers"] == 11
    assert current["sides"][1]["luoli_bills"] == 11
    assert not current["sides"][0]["turn"].get("luoli_heart")
    assert not current["sides"][1]["turn"].get("luoli_allergy")
    assert current["sides"][1]["turn"].get("mirror_gain_factor", 1) == 1
    mirror_battle.finish_round(current["sides"][0])
    mirror_battle.finish_round(current["sides"][1])
    assert current["sides"][0]["luoli_cucumbers"] == 6
    assert current["sides"][1]["luoli_bills"] == 6


def test_sleep_hit_halves_injury_then_doubles_next_generation(monkeypatch):
    current = state("luoli", "miumiu")
    play(current, 0, "luoli-soba")
    play(current, 0, "luoli-sleep")
    plain(current, 1)
    result = settle(current, monkeypatch)
    assert result["before"][0]["weight"] == 15
    frozen = {"version": 17, "round": 1, "sides": result["before"]}
    injury_wheel, modifiers = battle._dynamic_injury_wheel(frozen, 0)
    assert modifiers["luoli_sleep_hit"]
    assert dict(injury_wheel)["heavy"] / modifiers["weight_scale"] == Fraction(25, 2)
    assert current["sides"][0]["turn"]["luoli_sleep_bonus"]
    event = play(current, 0, "luoli-mutsumi")
    assert event["mirror"]["cucumbers_generated"] == 4
    plain(current, 1)
    settle(current, monkeypatch)
    assert not current["sides"][0]["turn"]["luoli_sleep_bonus"]


def test_soyo_heals_heavy_to_light_then_none():
    current = state("luoli", "miumiu")
    current["sides"][0].update(heavy=True, risk=2, injury_state="heavy", core=3)
    play(current, 0, "luoli-soyo")
    assert not current["sides"][0]["heavy"] and current["sides"][0]["risk"] == 1
    play(current, 0, "luoli-soyo")
    assert current["sides"][0]["injury_state"] == "none" and current["sides"][0]["risk"] == 0
    assert current["sides"][0]["core"] == 3


@pytest.mark.parametrize("left", ["luoli", "miumiu"])
@pytest.mark.parametrize("right", [f.fighter_id for f in FIGHTERS])
def test_message_order_chunk_size_and_serialized_restart(left, right):
    a = state(left, right)
    b = deepcopy(a)
    for index in range(5):
        if a["status"] != "active":
            break
        finish_moves(a, "mirror-cross-order", (0, 1))
        for side in (1, 0):
            battle.roll_count(b, side, "mirror-cross-order")
            while not b["sides"][side]["turn"]["done"]:
                battle.play_chunk(b, side, "mirror-cross-order", chunk_size=1)
                b = battle.loads(battle.dumps(b))
        ar = battle.resolve_round(a, "mirror-cross-order")
        br = battle.resolve_round(b, "mirror-cross-order")
        # JSON 持久化把历史模仿池的 tuple 变为 list；比较实际持久化事实。
        assert battle.dumps(ar) == battle.dumps(br), (left, right, index)
        assert battle.dumps(a) == battle.dumps(b)
