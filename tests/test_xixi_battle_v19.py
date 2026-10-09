"""西天帝路线的纯规则边界、持久事实与共同快照结算。"""

from copy import deepcopy
from fractions import Fraction

import pytest

from pig_catcher.domain import battle
from pig_catcher.domain import xixi_battle as xixi
from pig_catcher.domain.battle_catalog import Move
from pig_catcher.domain.xixi_battle_catalog import EMPEROR_GAIN, build_moves


def player(fighter="xixi"):
    value = {
        "snapshot": {"fighter_id": fighter},
        "weight": Fraction(5),
        "risk": 0,
        "heavy": False,
        "injury_state": "none",
        "next_action_bonus": 0,
        "turn": {"draws": 0, "pending": 0, "done": False, "events": []},
    }
    xixi.ensure_player(value)
    return value


def use(p, slug, *, round_number=1, automatic=False, copied=False):
    move = next(m for m in build_moves(Move) if m.move_id == "xixi-" + slug)
    p["turn"]["draws"] += 1
    gain, fact = xixi.local_move(p, move.move_id, move.gain, round_number, automatic=automatic, copied=copied)
    p["weight"] += gain
    event = {
        "ordinal": p["turn"]["draws"],
        "move_id": move.move_id,
        "gain": gain,
        "total": p["weight"],
        "opponent_reduction": Fraction(0),
        "domain_eligible": True,
    }
    xixi.event_context(p, event, fact)
    p["turn"]["events"].append(event)
    return event


def first(seed, key, wheel, **kwargs):
    return wheel[0][0], 0


def acquire(p, key):
    def select(seed, token, wheel, **kwargs):
        assert key in dict(wheel)
        return key, 0

    return xixi.gain_hextech(p, "seed", key, choose=select)


def test_catalog_precise_weights_and_no_prototype():
    moves = build_moves(Move)
    assert len(moves) == 9
    assert [m.gain for m in moves] == [14, 18, 24, 0, 0, 16, 0, 0, 22]
    assert [m.resolved_draw_weight_units for m in moves] == [15000, 10000, 12000, 3500, 1800, 14000, 9000, 5500, 6000]
    assert not any("prototype" in m.move_id for m in moves)


def test_count_wheel_monotone_bounded_and_heavy_excludes_five():
    p = player()
    base = ((1, 5), (2, 4), (3, 3), (4, 2), (5, 1))
    p["weight"] = -5
    assert xixi.count_wheel(p, base) == base
    p["weight"] = 100
    middle = xixi.count_wheel(p, base)
    assert middle[-1][1] / middle[0][1] == Fraction(3, 5)
    p["weight"] = EMPEROR_GAIN
    assert xixi.count_wheel(p, base)[-1][1] == 5
    assert [n for n, _w in xixi.count_wheel(p, base[:-1])] == [1, 2, 3, 4]


def test_mark_consecutive_surge_and_automatic_overload():
    p = player()
    use(p, "surge")
    use(p, "surge")
    assert p["xixi_mark"] == 1 and p["xixi_overload_weight_units"] == 7000
    prison = use(p, "prison")
    assert prison["xixi"]["auto_overload"]
    assert prison["opponent_next_debt"] == 1
    assert p["xixi_mark"] == 0
    automatic = use(p, "overload", automatic=True)
    assert automatic["gain"] == 24 and p["xixi_overload_weight_units"] == 10500
    manual = use(p, "overload")
    assert manual["gain"] == 24 and p["xixi_overload_weight_units"] == 0
    xixi.finalize_turn(p)
    assert [e["gain"] for e in p["turn"]["events"]] == [26, 26, 30, 36, 36]
    assert p["next_action_bonus"] == 1
    before = deepcopy(p)
    assert xixi.finalize_turn(p) == [] and p == before


def test_mark_double_and_realm_growth_are_permanent():
    p = player()
    use(p, "realm-warp")
    assert p["xixi_overload_bonus"] == 6
    use(p, "surge")
    event = use(p, "overload")
    assert event["gain"] == 60 and event["xixi"]["mark_consumed"]
    assert p["xixi_mark"] == 0


def test_cooldown_both_current_and_next_round_then_available():
    p = player()
    reality = next(m for m in build_moves(Move) if m.move_id == "xixi-reality")
    event = use(p, "reality", round_number=4)
    assert event["xixi"]["cooldown_until"] == 5
    assert xixi.move_weight_units(p, reality, 4) == 0
    assert xixi.move_weight_units(p, reality, 5) == 0
    assert xixi.move_weight_units(p, reality, 6) == 3500
    with pytest.raises(ValueError, match="冷却"):
        use(p, "reality", round_number=5)


def test_hourglass_stops_pending_without_deleting_previous_skills():
    p = player()
    first_move = use(p, "attack")
    p["turn"]["pending"] = 7
    hourglass = use(p, "hourglass")
    assert p["turn"]["done"] and p["turn"]["pending"] == 0
    assert hourglass["xixi_stopped_pending"] == 7
    xixi.finalize_turn(p)
    assert first_move["gain"] == 18 and hourglass["gain"] == 2
    assert xixi.injury_guard(p, consume=True) == "中亚沙漏"


def test_functional_moves_receive_permanent_and_count_bonuses():
    p = player()
    acquire(p, "physical-to-magic")
    friends = use(p, "friends")
    sidestep = use(p, "sidestep")
    xixi.finalize_turn(p)
    assert friends["gain"] == sidestep["gain"] == 8


def test_unique_hextech_pool_empty_and_emperor_recovers_injuries():
    p = player()
    p.update(heavy=True, risk=2, injury_state="heavy")
    facts = [xixi.gain_hextech(p, "seed", str(n), choose=first) for n in range(4)]
    assert len(set(p["xixi_hextech"])) == 4
    assert facts[-1]["emperor"]
    assert p["injury_state"] == "none" and not p["heavy"] and p["risk"] == 0
    assert xixi.gain_hextech(p, "seed", "empty", choose=first)["reason"] == "empty"
    assert xixi.fixed_injury_wheel(p) == (("none", 99), ("exhausted", 1))
    assert xixi.injury_wheel(p, (("exhausted", 999999), ("core", 1))) == (("none", 99), ("exhausted", 1))
    assert xixi.injury_guard(p, consume=True) == "由心及物"  # 成帝已有盾仍优先免抽
    # 巨人尚不足100同样是免抽，不是固定盘修正。
    assert xixi.injury_guard(p) == "歌莉娅巨人"
    for move in build_moves(Move):
        if move.move_id in {"xixi-reality", "xixi-hourglass"}:
            assert xixi.move_weight_units(p, move, 99) == 0
    friends = use(p, "friends")
    assert friends["gain"] == EMPEROR_GAIN + 24


def test_shield_consumes_once_and_goliath_exact_100_boundary():
    p = player()
    acquire(p, "mind-over-matter")
    assert xixi.injury_guard(p) == "由心及物"
    assert xixi.injury_guard(p, consume=True) == "由心及物"
    assert xixi.injury_guard(p) is None
    acquire(p, "goliath")
    assert xixi.observe_effective_enemy_gain(p, Fraction(999, 10), round_number=1)["guard_active"]
    assert xixi.observe_effective_enemy_gain(p, 9999, round_number=1) == {}
    assert xixi.injury_guard(p) == "歌莉娅巨人"
    assert not xixi.observe_effective_enemy_gain(p, Fraction(1, 10), round_number=2)["guard_active"]
    assert xixi.injury_guard(p) is None


def test_basics_injury_halving_delay_queue_distinct_and_deduplicated():
    p = player()
    acquire(p, "back-to-basics")
    assert p["xixi_permanent_action_bonus"] == 1
    assert xixi.injury_wheel(p, (("light", 65), ("heavy", 25), ("exhausted", 5), ("core", 5))) == (
        ("light", Fraction(65, 2)),
        ("heavy", Fraction(25, 2)),
        ("exhausted", Fraction(5, 2)),
        ("core", 5),
    )
    e1, e2 = use(p, "realm-warp", round_number=2), use(p, "realm-warp", round_number=2)
    assert not e1["domain_eligible"] and not e2["domain_eligible"]
    assert len(p["xixi_hextech_queue"]) == 2
    assert xixi.domain_hit(p, e1, 2, "seed", choose=first) == {}
    assert xixi.begin_round(p, 4, "seed", choose=first) == []
    result = xixi.begin_round(p, 5, "seed", choose=first)
    assert len(result) == 2 and not p["xixi_hextech_queue"]
    assert p["turn"]["xixi_delayed_hextech"] == result
    assert xixi.begin_round(p, 5, "seed", choose=first) == []
    assert p["xixi_overload_bonus"] == 12


def test_delay_empty_pool_does_not_draw_and_persistence_roundtrip():
    p = player()
    acquire(p, "back-to-basics")
    use(p, "realm-warp")
    saved = battle.loads(battle.dumps(p))
    assert saved == p
    for _ in range(3):
        xixi.gain_hextech(saved, "seed", "now", choose=first)
    result = xixi.begin_round(saved, 4, "seed", choose=first)
    assert len(result) == 1 and result[0]["result"]["reason"] == "empty"
    assert saved["xixi_hextech_queue"] == []


def test_copy_cannot_mint_resources_or_inherit_route_bonus():
    outsider = player("miumiu")
    base, fact = xixi.local_move(outsider, "xixi-realm-warp", 22, 1, copied=True)
    assert base == 22 and fact == {}
    assert xixi.gain_hextech(outsider, "seed", "copy", choose=first)["reason"] == "not-native"
    assert not any(k.startswith("xixi_") for k in outsider)
    p = player()
    original = deepcopy(p)
    assert xixi.local_move(p, "xixi-surge", 14, 1, copied=True) == (14, {})
    assert p == original


def settle(left, right):
    current = {"version": 19, "round": 1, "sides": [left, right]}
    cancelled = [{}, {}]
    result = xixi.prepare_interactions(
        current,
        None,
        cancelled,
        set(),
        "seed",
        choose=first,
        remaining=battle._remaining_event_gain,
        reduce=battle._reduce_event,
        cancel=battle._cancel_event,
    )
    return result, cancelled


def test_reality_is_whole_turn_retroactive_precise_and_idempotent():
    left, right = player(), player("sukuna")
    attack = use(left, "attack")
    use(left, "reality")
    right["turn"]["events"] = [{"ordinal": 1, "gain": Fraction(20), "opponent_reduction": Fraction(10)}]
    right["weight"] += 20
    records, cancelled = settle(left, right)
    assert attack["gain"] == Fraction(117, 5)  # (16 + 2) * 1.3
    assert cancelled[1][1]["gain"] == 6
    assert right["turn"]["events"][0]["opponent_reduction"] == 7
    assert left["turn"]["xixi_positive_gain_factor"] == Fraction(13, 10)
    assert xixi.gain(left, 10) == 13
    assert records
    prior = left["weight"]
    settle(left, right)
    assert left["weight"] == prior


def test_hourglass_nullifies_enemy_positive_gain_and_damage_function_preserved():
    left, right = player(), player("sukuna")
    use(left, "hourglass")
    event = {"ordinal": 1, "gain": Fraction(20), "opponent_reduction": Fraction(10), "opponent_next_bonus": 1}
    right["turn"]["events"] = [event]
    right["weight"] += 20
    _records, cancelled = settle(left, right)
    assert cancelled[1][1]["gain"] == 20
    assert event["opponent_reduction"] == 0
    assert event["opponent_next_bonus"] == 1


def test_ignore_uses_complete_snapshot_and_keeps_audit_candidates():
    left, right = player(), player()
    use(left, "sidestep")
    use(right, "sidestep")
    _records, cancelled = settle(left, right)
    assert left["turn"]["events"][0]["xixi_ignored"]
    assert right["turn"]["events"][0]["xixi_ignored"]
    assert set(cancelled[0]) == set(cancelled[1]) == {1}


def test_domain_followup_once_and_core_hextech():
    p = player()
    event = use(p, "realm-warp")
    result = xixi.domain_hit(p, event, 1, "seed", choose=first)
    assert result["ignore_count"] == 2 and p["next_action_bonus"] == 1
    assert result["hextech"]["available"]
    assert xixi.domain_hit(p, event, 1, "seed", choose=first) == {}
    assert xixi.core_hextech(p, 1, "seed", choose=first)["hextech"] == "mind-over-matter"
    assert xixi.core_hextech(p, 1, "seed", choose=first)["replayed"]
    assert len(p["xixi_hextech"]) == 2


def integrated_state(right="sukuna"):
    return battle.new_state(
        [{"fighter_id": f, "level": 0, "trait_bonus": 0, "tool_id": ""} for f in ("xixi", right)],
        seed="xixi-v19",
    )


def integrated_play(current, slug, side=0, **kwargs):
    p = current["sides"][side]
    p["turn"].update(raw=1, effective=1, pending=1, done=False)
    move = next(m for m in battle.fighter_moves(p["snapshot"]["fighter_id"], current["version"]) if m.move_id == slug)
    event = battle.apply_move(p, move, round_number=current["round"], side=side, version=current["version"], **kwargs)
    event.update(side=side, round=current["round"])
    p["turn"]["events"].append(event)
    return event


def finish_integrated(current, monkeypatch, *, loser=0, injury="light", hit=True):
    original_choose, original_randbelow = battle.choose, battle.randbelow

    def forced_choose(seed, key, wheel, **kwargs):
        if key.endswith(":injury"):
            assert injury in dict(wheel)
            return injury, 0
        if "domain:solo" in key:
            return ("hit" if hit else "simple-domain"), 0
        return original_choose(seed, key, wheel, **kwargs)

    def forced_roll(seed, key, bound, **kwargs):
        if key.endswith(":winner"):
            return bound - 1 if loser == 0 else 0
        return original_randbelow(seed, key, bound, **kwargs)

    monkeypatch.setattr(battle, "choose", forced_choose)
    monkeypatch.setattr(battle, "randbelow", forced_roll)
    for p in current["sides"]:
        p["turn"].update(done=True, pending=0)
    return battle.resolve_round(current, "xixi-v19")


def test_core_integration_form_catalog_old_v18_isolated():
    from pig_catcher.domain.battle_catalog import fighter_form_moves, fighter_moves

    current = integrated_state()
    assert current["version"] == 19
    assert len(fighter_form_moves("xixi", "xixi-celestial", 19)) == 9
    assert len(fighter_form_moves("xixi", "xixi-emperor", 19)) == 7
    assert fighter_moves("daniya", 18) != fighter_moves("daniya", 19)
    assert len(fighter_moves("firefly", 18)) == 9
    assert "xixi_hextech" in current["sides"][0]
    assert "xixi_hextech" not in current["sides"][1]


def test_core_integration_auto_overload_and_checkpoint_chunk(monkeypatch):
    current = integrated_state()
    p = current["sides"][0]
    integrated_play(current, "xixi-surge")
    p["turn"].update(raw=2, effective=2, pending=2, done=False)
    moves = battle.fighter_moves("xixi", 19)
    index = next(i for i, m in enumerate(moves) if m.move_id == "xixi-prison")
    original = battle.choose

    def prison(seed, key, wheel, **kwargs):
        if ":move:" in key and ":nested" not in key:
            return index, 0
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", prison)
    battle.play_chunk(current, 0, "xixi-v19", chunk_size=1)
    ids = [e["move_id"] for e in p["turn"]["events"]]
    assert ids == ["xixi-surge", "xixi-prison", "xixi-overload"]
    assert p["turn"]["pending"] == 2  # 禁锢+1，自动施放不吃手抽额度
    assert p["xixi_overload_weight_units"] == 7000
    saved = battle.loads(battle.dumps(current))
    battle.play_chunk(current, 0, "xixi-v19", chunk_size=1)
    battle.play_chunk(saved, 0, "xixi-v19", chunk_size=1)
    assert saved == current


@pytest.mark.parametrize("protection", ["hourglass", "shield", "goliath"])
def test_core_integration_guards_skip_injury_draw(monkeypatch, protection):
    current = integrated_state()
    p = current["sides"][0]
    if protection == "hourglass":
        integrated_play(current, "xixi-hourglass")
    elif protection == "shield":
        acquire(p, "mind-over-matter")
        integrated_play(current, "xixi-attack")
    else:
        acquire(p, "goliath")
        integrated_play(current, "xixi-attack")
    integrated_play(current, "cleave", side=1)
    original = battle.choose

    def forbid_injury(seed, key, wheel, **kwargs):
        assert not key.endswith(":injury"), "免抽仍调用了伤势抽签"
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", forbid_injury)
    monkeypatch.setattr(battle, "randbelow", lambda _seed, key, bound, **_kwargs: bound - 1)
    for side in current["sides"]:
        side["turn"].update(done=True, pending=0)
    result = battle.resolve_round(current, "xixi-v19")
    assert result["injury_effective"] == "none" and not result["natural_end"]
    assert p["injury_state"] == "none" and p["core"] == 0
    if protection == "shield":
        assert not p["xixi_shield"]


def test_core_integration_emperor_fixed_wheel_suppresses_forced_exhaustion(monkeypatch):
    current = integrated_state("daniya")
    p = current["sides"][0]
    for _ in range(4):
        xixi.gain_hextech(p, "seed", str(_), choose=first)
    p["xixi_shield"] = False
    p["xixi_goliath_gain"] = Fraction(100)
    p["turn"]["xixi_goliath_acquired_this_turn"] = False
    integrated_play(current, "xixi-friends")
    # 敌黑洞终招由核心产生强制本轮失败，伤势必须仍按固定盘。
    current["sides"][1]["daniya_form"] = "black-hole"
    integrated_play(current, "daniya-black-hole-eternal-end", side=1)
    result = finish_integrated(current, monkeypatch, injury="none")
    assert result["injury_effective"] == "none"
    assert dict(result["injury_wheel"]) == {"none": 99, "exhausted": 1}
    assert p["xixi_form"] == "xixi-emperor" and not result["natural_end"]


def test_core_integration_preserves_legacy_core_with_extra_hextech(monkeypatch):
    current = integrated_state()
    p = current["sides"][0]
    p.update(heavy=True, risk=2, injury_state="heavy")
    integrated_play(current, "xixi-attack")
    integrated_play(current, "cleave", side=1)
    result = finish_integrated(current, monkeypatch, injury="core")
    assert result["injury_effective"] == "core"
    assert p["core"] == 1 and len(p["xixi_hextech"]) == 1
    assert not p["heavy"] and p["risk"] == 2
    assert p["turn"]["pending"] == 0 and p["turn"]["raw"] is None


def test_core_integration_reality_preserves_message_order_symmetry():
    left_first, right_first = integrated_state(), integrated_state()
    for current, order in ((left_first, (0, 1)), (right_first, (1, 0))):
        for side in order:
            if side == 0:
                integrated_play(current, "xixi-attack")
                integrated_play(current, "xixi-reality")
            else:
                integrated_play(current, "cleave", side=1)
        battle._settle_interactions(current, "xixi-v19")
    assert left_first["sides"] == right_first["sides"]
    assert left_first["sides"][0]["weight"] == 31  # +18*1.3 + 2*1.3
    assert left_first["sides"][1]["weight"] == Fraction(31, 2)  # +15*0.7


def test_core_integration_basics_skips_domain_but_growth_and_delayed_hextech_remain(monkeypatch):
    current = integrated_state()
    p = current["sides"][0]
    acquire(p, "back-to-basics")
    event = integrated_play(current, "xixi-realm-warp")
    integrated_play(current, "cleave", side=1)
    result = finish_integrated(current, monkeypatch)
    assert not event["domain_eligible"] and result["interactions"]["domain"] is None
    assert p["xixi_overload_bonus"] == 6 and len(p["xixi_hextech"]) == 1
    assert p["xixi_hextech_queue"][0]["due"] == 4
    current["round"] = 4
    count = battle.roll_count(current, 0, "xixi-v19")
    assert count["effective"] == count["raw"] + 1
    assert len(p["xixi_hextech"]) == 2 and not p["xixi_hextech_queue"]


def test_core_integration_fixed_none_does_not_heal_existing_injury(monkeypatch):
    current = integrated_state()
    p = current["sides"][0]
    for index in range(4):
        xixi.gain_hextech(p, "seed", str(index), choose=first)
    p.update(xixi_shield=False, xixi_goliath_gain=100, heavy=True, risk=2, injury_state="heavy")
    integrated_play(current, "xixi-attack")
    integrated_play(current, "cleave", side=1)
    result = finish_integrated(current, monkeypatch, injury="none")
    assert result["injury_effective"] == "none"
    assert p["heavy"] and p["risk"] == 2 and p["injury_state"] == "heavy"


def test_core_integration_goliath_uses_exact_effective_gain_and_expires_at_100(monkeypatch):
    current = integrated_state()
    p = current["sides"][0]
    acquire(p, "goliath")
    p["turn"]["xixi_goliath_acquired_this_turn"] = False
    p["xixi_goliath_gain"] = Fraction(179, 2)
    integrated_play(current, "xixi-reality")
    integrated_play(current, "cleave", side=1)
    result = finish_integrated(current, monkeypatch, injury="light")
    assert p["xixi_goliath_gain"] == 100  # 89.5 + 15*0.7
    assert result["injury_effective"] == "light"


def test_core_integration_goliath_excludes_cancelled_gain(monkeypatch):
    current = integrated_state()
    p = current["sides"][0]
    acquire(p, "goliath")
    p["turn"]["xixi_goliath_acquired_this_turn"] = False
    p["xixi_goliath_gain"] = Fraction(99)
    integrated_play(current, "xixi-sidestep")
    integrated_play(current, "cleave", side=1)
    result = finish_integrated(current, monkeypatch)
    assert p["xixi_goliath_gain"] == 99
    assert result["injury_effective"] == "none"
