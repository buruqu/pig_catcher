"""流萤九格设计逐条验收：数值、半层、轮盘、领域、重放和旧版隔离。"""

from copy import deepcopy
from fractions import Fraction

import pytest

from pig_catcher.domain import battle
from pig_catcher.domain.battle_catalog import Move, fighter_form_moves, fighter_moves
from pig_catcher.domain.firefly_battle import collapse_risk_units


def state(right="sukuna"):
    return battle.new_state(
        [{"fighter_id": f, "level": 0, "trait_bonus": 0, "tool_id": ""} for f in ("firefly", right)], seed="v18"
    )


def play(current, move_id, side=0, **kwargs):
    player = current["sides"][side]
    player["turn"].update(raw=1, effective=1, pending=1, done=False)
    move = (
        move_id
        if isinstance(move_id, Move)
        else next(
            m for m in fighter_moves(player["snapshot"]["fighter_id"], current["version"]) if m.move_id == move_id
        )
    )
    event = battle.apply_move(
        player, move, side=side, round_number=current["round"], version=current["version"], **kwargs
    )
    event.update(side=side, round=current["round"], fighter_id=player["snapshot"]["fighter_id"])
    battle._apply_firefly_event_context(current, side, event)
    player["turn"]["events"].append(event)
    return event


def sam(current, fuel=0):
    current["sides"][0].update(firefly_form="sam", firefly_sam_rounds_remaining=2, firefly_fuel=fuel)


def finish(current, monkeypatch, hit=True):
    original = battle.choose

    def forced(seed, key, wheel, **kwargs):
        if "domain:solo" in key:
            return ("hit" if hit else "simple-domain"), 0
        if key.endswith(":injury"):
            return "light", 0
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", forced)
    for p in current["sides"]:
        p["turn"].update(done=True, pending=0)
    return battle.resolve_round(current, "v18")


def test_nine_slots_preserve_the_old_eight_slot_definition():
    assert len(fighter_moves("firefly", 17)) == 8
    moves = fighter_moves("firefly", 18)
    assert len(moves) == 9
    assert [m.resolved_draw_weight_units for m in moves] == [10000, 10000, 8500, 8500, 10000, 10000, 9000, 9000, 8000]
    assert fighter_form_moves("firefly", "sam", 17) == fighter_moves("firefly", 17)
    p = state()["sides"][0]
    assert battle.move_weight_units(p, moves[4]) == 9000
    p["firefly_fuel"] = 3
    assert battle.move_weight_units(p, moves[4]) == 12000
    p["firefly_form"] = "sam"
    assert battle.move_weight_units(p, moves[4]) == 13000


def test_cocoon_bombardment_fuel_and_extra_transform_collapse():
    current = state()
    cocoon = play(current, "firefly-crimson-cocoon")
    assert cocoon["gain"] == 12
    p = current["sides"][0]
    assert p["firefly_fuel"] == 1 and p["firefly_next_sam_gain_bonus"] == 10
    assert p["turn"]["firefly_no_transform_bonus_units"] == 2500
    skyfire = play(current, "sam-skyfire-bombardment")
    assert skyfire["gain"] == 42  # 22 + 2层燃芯10 + 储备10
    assert skyfire["opponent_reduction"] == 10
    assert skyfire["firefly_collapse_to_add"] == 2
    assert p["firefly_next_sam_gain_bonus"] == 0
    assert p["firefly_sam_rounds_remaining"] == 2


@pytest.mark.parametrize(
    "layers,bonus",
    [
        (0, 0),
        (Fraction(1, 2), Fraction(2, 5)),
        (1, Fraction(4, 5)),
        (Fraction(3, 2), Fraction(13, 10)),
        (2, Fraction(9, 5)),
        (Fraction(5, 2), Fraction(53, 20)),
        (3, Fraction(7, 2)),
    ],
)
def test_exact_half_layer_risk_curve(layers, bonus):
    assert collapse_risk_units(layers) == bonus
    current = state()
    current["sides"][1]["firefly_collapse"] = layers
    wheel, modifiers = battle._dynamic_injury_wheel(current, 1)
    assert modifiers["firefly_collapse_bonus_units"] == bonus
    assert Fraction(dict(wheel)["exhausted"], modifiers["weight_scale"]) == 5 + bonus


def test_half_echo_collapse_accumulates_and_caps_without_rounding():
    current = state()
    sam(current, 2)
    current["sides"][1]["firefly_collapse"] = Fraction(5, 2)
    echo = play(current, "firefly-crimson-cocoon", firefly_echo_scale=Fraction(1, 2))
    assert echo["gain"] == 4 and echo["opponent_reduction"] == 4
    assert echo["firefly_collapse_to_add"] == Fraction(1, 2)
    assert echo["firefly_target_collapse_after_pending"] == 3
    for _ in range(5):
        play(current, "firefly-dream-destination", firefly_echo_scale=Fraction(1, 2))
    slash = play(current, "sam-bottom-fire-slash")
    assert slash["gain"] == 62  # 22 + 燃芯10 + 被动15 + 斩击15，绝不按超过3层计算
    assert current["sides"][0]["firefly_fuel"] == 2
    assert battle.loads(battle.dumps(current)) == current
    battle._settle_interactions(current, "half")
    assert current["sides"][1]["firefly_collapse"] == 3


@pytest.mark.parametrize(
    "move_id,reduction,risk,other_risk",
    [
        ("firefly-crimson-cocoon", 8, 0, 0),
        ("firefly-dream-destination", 10, Fraction(-3, 2), Fraction(3, 2)),
        ("firefly-silent-galaxy", 8, Fraction(-3, 2), 1),
    ],
)
def test_residual_echo_effects_and_half_effects(move_id, reduction, risk, other_risk):
    for scale in (Fraction(1), Fraction(1, 2)):
        current = state()
        sam(current, 2)
        current["sides"][1]["firefly_collapse"] = 2
        event = play(current, move_id, firefly_echo_scale=scale)
        assert event["opponent_reduction"] == reduction * scale
        assert event["firefly_self_exhaust_delta_units"] == risk * scale
        assert event["firefly_opponent_exhaust_delta_units"] == other_risk * scale
        assert event["firefly_collapse_to_add"] == scale
        assert current["sides"][0]["firefly_fuel"] == 2
        assert current["sides"][0]["firefly_form"] == "sam"


def test_overload_and_echo_risk_expire_but_collapse_remains(monkeypatch):
    current = state()
    current["sides"][1]["firefly_collapse"] = 3
    event = play(current, "sam-deathstar-overload")
    assert event["gain"] == 41 and event["opponent_next_debt"] == 1
    assert event["firefly_opponent_exhaust_delta_units"] == Fraction(3, 2)
    summary = finish(current, monkeypatch)
    assert summary and current["sides"][1]["next_debt"] == 1
    assert current["sides"][1]["firefly_collapse"] == 3
    assert current["sides"][1]["injury_exhaust_bonus_units"] == 0
    assert current["sides"][1]["turn"]["firefly_self_exhaust_delta_units"] == 0


def test_ignite_reaches_three_and_does_not_double_count_same_fuel_bonus():
    current = state()
    current["sides"][0]["firefly_fuel"] = 2
    current["sides"][1]["firefly_collapse"] = 1
    event = play(current, "sam-ignite-star-sea")
    assert event["gain"] == 43  # 28 + 燃芯10 + 溃败被动5
    assert event["firefly_opponent_exhaust_delta_units"] == Fraction(3, 2)
    p = current["sides"][0]
    assert p["firefly_fuel"] == 0 and p["next_action_bonus"] == 1
    again = play(current, "sam-ignite-star-sea")
    assert again["gain"] == 35 and again["opponent_reduction"] == 10
    assert again["firefly_opponent_exhaust_delta_units"] == 0
    assert p["firefly_sam_rounds_remaining"] == 3


def test_starfield_and_dream_use_frozen_opponent_snapshot_and_exact_halving():
    settled = []
    for order in ((0, 1), (1, 0)):
        current = state()
        for side in order:
            if side == 0:
                play(current, "firefly-dream-destination")
                play(current, "firefly-silent-galaxy")
            else:
                play(current, Move("odd", "奇数招式", 101), 1)
        result = battle._settle_interactions(current, "starfield")
        p, target = current["sides"]
        # 101/2，之后梦归-16-8、星河条件-8：精确余18.5。
        assert target["weight"] == Fraction(47, 2)
        assert p["firefly_fuel"] == 2
        assert p["turn"]["firefly_self_exhaust_delta_units"] == Fraction(-9, 2)
        assert p["turn"]["firefly_no_transform_bonus_units"] == 1000
        assert result["firefly_starfield_adjustments"][0]["deduction"] == Fraction(101, 2)
        settled.append(battle.dumps(current))
    assert settled[0] == settled[1]


@pytest.mark.parametrize("hit", [True, False])
def test_domain_pursuit_only_on_hit_and_next_round_effects_always_persist(monkeypatch, hit):
    current = state()
    sam(current)
    current["sides"][1]["firefly_collapse"] = 2
    play(current, "firefly-falling-sky")
    play(current, Move("target", "对手", 100), 1)
    summary = finish(current, monkeypatch, hit)
    facts = summary["interactions"]
    assert facts["domain"]["bonus_gain"] == (36 if hit else 0)
    assert facts["round_reductions"][1]["requested"] == (35 if hit else 20)
    assert bool(facts["domain"]["effects"]) == hit
    assert current["sides"][0]["next_action_bonus"] == 1
    assert current["sides"][0]["firefly_sam_rounds_remaining"] == 2
    assert current["sides"][1]["firefly_collapse"] == 3
    assert summary["firefly_next_collapse"][0]["added"] == 1
    assert battle.loads(battle.dumps(current)) == current


def test_firefly_domain_does_not_force_sam_and_next_collapse_is_not_current_round(monkeypatch):
    current = state()
    play(current, "firefly-falling-sky")
    summary = finish(current, monkeypatch)
    assert summary["interactions"]["firefly_collapse_updates"][0]["after"] == 1
    assert current["sides"][1]["firefly_collapse"] == 2
    assert current["sides"][0]["firefly_form"] == "firefly"


def test_disabled_skills_have_no_passive_or_followup():
    for move in fighter_moves("firefly"):
        current = state()
        p = current["sides"][0]
        p["turn"]["daniya_world_effects_disabled"] = True
        event = play(current, move)
        assert event["gain"] == 0 and event["opponent_reduction"] == 0
        assert not event["firefly_collapse_to_add"]
        assert not event["firefly_domain_followup"] and not event["firefly_starfield"]
        assert p["firefly_fuel"] == 0 and p["firefly_form"] == "firefly"


def test_starfield_halves_domain_and_pursuit_gains_but_not_negative_gains(monkeypatch):
    current = state("firefly")
    play(current, "firefly-silent-galaxy")
    play(current, "firefly-falling-sky", 1)
    summary = finish(current, monkeypatch)
    facts = summary["interactions"]
    assert facts["domain"]["bonus_gain"] == 18
    assert [f["deduction"] for f in facts["firefly_starfield_adjustments"]] == [18, 6]
    assert summary["interactions"]["round_reductions"][1]["requested"] == 8
    current = state()
    play(current, "firefly-silent-galaxy")
    play(current, Move("loss", "负收益", -30), 1)
    play(current, Move("gain", "正收益", 100), 1)
    battle._settle_interactions(current, "negative")
    assert current["sides"][1]["weight"] == 17  # 起始5 - 30 + 100/2 - 条件8


def test_frozen_choice_restores_and_awards_all_selection_effects(monkeypatch):
    for selected in ("firefly-crimson-cocoon", "sam-bottom-fire-slash"):
        for echo in (False, True):
            current = state()
            if echo:
                sam(current)
            moves = fighter_moves("firefly")
            index = next(i for i, m in enumerate(moves) if m.move_id == selected)
            original = battle.choose

            def fixed(seed, key, wheel, index=index, original=original, **kwargs):
                return (index, 0) if "firefly-choice" in key else original(seed, key, wheel, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(battle, "choose", fixed)
                parent = play(current, "firefly-firefly-flame")
            restored = battle.loads(battle.dumps(current))
            result = battle.play_chunk(restored, 0, "choice", chunk_size=1)[0]
            assert result["move_id"] == selected
            assert len(parent["firefly_choice"]["options"]) == (1 if echo else 2)
            if selected.startswith("sam"):
                assert result["gain"] == (34 if echo else 37)
                assert result["firefly_collapse_to_add"] == (1 if echo else 3)
            elif echo:
                assert result["gain"] == 4 and result["firefly_collapse_to_add"] == Fraction(1, 2)
                assert restored["sides"][0]["firefly_fuel"] == 0
            else:
                assert restored["sides"][0]["firefly_fuel"] == 3
                assert restored["sides"][0]["turn"]["domain_clash_bonus_units"] == 2
            assert current != restored
            assert battle.play_chunk(deepcopy(current), 0, "choice", chunk_size=1)[0] == result
