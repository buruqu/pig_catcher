"""Battle v19 三形态、精确粒子、伤势优先级与历史盘回归。"""

from copy import deepcopy
from fractions import Fraction

import pytest

from pig_catcher.domain import battle, daniya_battle
from pig_catcher.domain.battle_catalog import fighter_form_moves, fighter_moves


def state(left="daniya", right="gojo", version=19):
    return battle.new_state(
        [{"fighter_id": f, "level": 0, "tool_id": "", "trait_bonus": 0} for f in (left, right)],
        seed="v19",
        version=version,
    )


def record(s, side, suffix, form=None):
    p = s["sides"][side]
    form = form or p.get("daniya_form", "staging")
    moves = (
        fighter_form_moves("daniya", form, s["version"])
        if p["snapshot"]["fighter_id"] == "daniya"
        else fighter_moves(p["snapshot"]["fighter_id"], s["version"])
    )
    m = next(m for m in moves if m.move_id.endswith(suffix))
    p["turn"].update(raw=1, effective=1, pending=1, done=False)
    e = battle.apply_move(p, m, version=s["version"], round_number=s["round"], side=side, seed="v19")
    e.update(side=side, round=s["round"], fighter_id=p["snapshot"]["fighter_id"])
    p["turn"]["events"].append(e)
    return e


def finish(s):
    for p in s["sides"]:
        p["turn"].update(raw=1, effective=1, pending=0, done=True)


def test_version_catalog_and_restore():
    for version in (17, 18):
        s = state(version=version)
        assert s["version"] == version
        assert any(m.move_id == "daniya-world-nmsl" for m in fighter_moves("daniya", version))
        assert not any("daniya-v19" in m.tags for m in fighter_moves("daniya", version))
        assert not fighter_moves("xixi", version)
        battle.roll_count(battle.loads(battle.dumps(s)), 0, "old")
    assert [len(fighter_form_moves("daniya", f, 19)) for f in ("staging", "disillusion", "black-hole")] == [8, 8, 4]
    assert not any("nmsl" in m.move_id or "dragon-image" in m.move_id for m in fighter_moves("daniya", 19))


def test_fraction_growth_loan_and_disabled_resources():
    s = state()
    p = s["sides"][0]
    p["turn"]["daniya_world_effects_disabled"] = True
    e = record(s, 0, "curtain")
    assert e["gain"] == 0
    assert p["daniya_particles"] == Fraction(1, 2)
    assert p["daniya_permanent_reduction"] == Fraction(521, 2000)
    assert p["daniya_domain_steps"] == 3
    assert p["daniya_transform_staging_units"] == 800
    p["turn"]["daniya_world_effects_disabled"] = False
    record(s, 0, "lie")
    e = record(s, 0, "knock")
    assert e["gain"] == 60
    assert p["next_debt"] == 1
    restored = battle.loads(battle.dumps(s))
    assert restored == s


@pytest.mark.parametrize(
    "particles,layers", [(5, 0), (Fraction(11, 2), 1), (10, 1), (Fraction(21, 2), 2), (15, 2), (Fraction(31, 2), 3)]
)
def test_strict_particle_injury_boundaries(particles, layers):
    p = state()["sides"][0]
    daniya_battle.add_particles(p, particles)
    permanent = p["daniya_permanent_reduction"]
    result, fact = daniya_battle.reduce_injury(p, "exhausted")
    assert fact["layers"] == layers
    assert result == ("exhausted", "heavy", "light", "none")[layers]
    assert p["daniya_particles"] == particles - layers
    assert p["daniya_permanent_reduction"] == permanent


def test_black_hole_linear_factor_and_no_particle_guard():
    s = state()
    p = s["sides"][0]
    p.update(heavy=True, risk=2)
    record(s, 0, "collapse")
    assert p["daniya_form"] == "black-hole"
    assert p["daniya_particles"] == 25
    assert not p["heavy"] and p["risk"] == 0
    before = p["daniya_permanent_reduction"]
    e = record(s, 0, "red-supergiant")
    assert e["gain"] == 45 and e["opponent_reduction"] == 60
    assert e["daniya_black_hole_factor"] == 3
    assert p["daniya_particles"] == Fraction(51, 2)
    assert p["daniya_permanent_reduction"] == before + Fraction(823, 2000)
    assert daniya_battle.reduce_injury(p, "exhausted")[0] == "exhausted"


def test_twenty_five_particle_auto_transition():
    s = state()
    p = s["sides"][0]
    daniya_battle.add_particles(p, Fraction(49, 2))
    e = record(s, 0, "curtain")
    assert e["daniya_transition"]["after"] == "black-hole"
    assert p["daniya_particles"] == 25


def test_seven_domain_hits_and_world_clash_bonus(monkeypatch):
    s = state()
    p = s["sides"][0]
    record(s, 0, "world-work")
    assert p.get("daniya_domain_clash_only_steps") == 10
    for n in range(7):
        p["turn"] = battle.fresh_turn()
        p["daniya_form"] = "staging" if n == 0 else "disillusion"
        record(s, 0, "domain")
        assert n or p["turn"]["domain_clash_bonus_units"] == 13
        domain = {"hit_side": 0, "boost_side": None}
        daniya_battle.domain_effects(
            s,
            domain,
            [{}, {}],
            set(),
            seed="hit",
            choose=battle.choose,
            remaining=battle._remaining_event_gain,
            cancel=battle._cancel_event,
        )
        assert p["daniya_domain_hits"] == n + 1
    assert p["daniya_form"] == "black-hole"


@pytest.mark.parametrize(
    "roll,expected",
    [(0, "none"), (8229, "none"), (8230, "light"), (9478, "light"), (9479, "exhausted"), (9999, "exhausted")],
)
def test_fixed_black_hole_exact_boundaries(monkeypatch, roll, expected):
    s = state("gojo", "daniya")
    p = s["sides"][1]
    daniya_battle.enter_black_hole(p)
    p["injury_exhaust_bonus_units"] = 100000
    finish(s)
    original = battle.randbelow
    monkeypatch.setattr(
        battle,
        "randbelow",
        lambda seed, key, bound, **kw: (
            roll if key.endswith(":injury") else 0 if key.endswith(":winner") else original(seed, key, bound, **kw)
        ),
    )
    result = battle.resolve_round(s, "fixed")
    assert result["injury_effective"] == expected
    assert result["injury_wheel"] == daniya_battle.FIXED_BLACK_HOLE_WHEEL
    assert result["injury_modifiers"]["fixed"]


def test_fixed_injured_progressive_and_none_preserves_damage(monkeypatch):
    s = state("gojo", "daniya")
    p = s["sides"][1]
    daniya_battle.enter_black_hole(p)
    p.update(risk=1, injury_state="light")
    finish(s)
    original = battle.randbelow
    monkeypatch.setattr(
        battle,
        "randbelow",
        lambda seed, key, bound, **kw: (
            8230 if key.endswith(":injury") else 0 if key.endswith(":winner") else original(seed, key, bound, **kw)
        ),
    )
    assert battle.resolve_round(s, "damage")["injury_effective"] == "heavy"
    finish(s)
    monkeypatch.setattr(battle, "randbelow", lambda seed, key, bound, **kw: 0)
    assert battle.resolve_round(s, "none")["injury_effective"] == "none"
    assert p["heavy"] and p["risk"] == 2


def test_current_move_cancel_and_mutual_force_fairness():
    s = state("daniya", "daniya")
    for p in s["sides"]:
        daniya_battle.enter_black_hole(p)
    record(s, 0, "red-supergiant")
    record(s, 1, "red-supergiant")
    finish(s)
    result = battle._settle_interactions(s, "fair")
    cancelled = [r for r in result["daniya_v19"] if r["kind"] == "current-move-cancel"]
    assert len(cancelled) == 2 and {r["side"] for r in cancelled} == {0, 1}
    assert all(r["deduction"] == 45 for r in cancelled)
    s = state("daniya", "daniya")
    for p in s["sides"]:
        daniya_battle.enter_black_hole(p)
    record(s, 0, "eternal-end")
    record(s, 1, "eternal-end")
    finish(s)
    result = battle.resolve_round(s, "fair")
    assert result["forced_round_defeat"] == (0, 1)
    assert result["forced_injury_suppressed"]


def test_disillusion_lie_only_doubles_enemy_debuff_and_recovers():
    s = state()
    p = s["sides"][0]
    p["daniya_form"] = "disillusion"
    record(s, 0, "lie")
    assert not p["double"] and p["next_debt"] == 1
    e = record(s, 0, "curtain")
    finish(s)
    battle._settle_interactions(s, "lie")
    assert e["opponent_reduction"] == 40
    assert daniya_battle.reduce_injury(p, "heavy")[0] == "light"


def test_positive_injury_does_not_spend_particles():
    p = state()["sides"][0]
    daniya_battle.add_particles(p, 20)
    for injury in ("none", "core"):
        assert daniya_battle.reduce_injury(p, injury)[0] == injury
        assert p["daniya_particles"] == 20


def test_copy_keeps_receiver_functions_without_particle_or_transform():
    s = state("gojo", "daniya")
    p = s["sides"][0]
    p["turn"].update(raw=1, effective=1, pending=1)
    move = next(m for m in fighter_form_moves("daniya", "disillusion", 19) if m.move_id.endswith("-flawless"))
    event = battle.apply_move(
        p, move, version=19, copy_context=True, functional_fighter_id="daniya", functional_form_id="disillusion"
    )
    assert event["opponent_next_debt"] == 3
    assert event["opponent_exhaust_bonus_units"] == 5
    assert p["daniya_particles"] == 0
    assert not p["daniya_form"]


def test_permanent_modifier_once_each_round_without_compounding_carryover():
    s = state()
    p, target = s["sides"]
    daniya_battle.add_particles(p, 2)
    kwargs = {"remaining": battle._remaining_event_gain, "cancel": battle._cancel_event}
    daniya_battle.finish_interactions(s, [{}, {}], set(), **kwargs)
    weight = target["weight"]
    daniya_battle.finish_interactions(s, [{}, {}], set(), **kwargs)
    assert target["weight"] == weight
    assert p["daniya_permanent_reduction"] == Fraction(521, 500)
    finish(s)
    # 已扣的持续修正不能进入下轮基础点数半留存，再被下一轮扣一遍。
    result = battle.resolve_round(s, "carry")
    if not result["natural_end"]:
        assert target["weight"] == 5
        daniya_battle.finish_interactions(s, [{}, {}], set(), **kwargs)
        assert target["weight"] == weight


def test_current_action_count_includes_zero_functions_and_domain_is_removed():
    s = state()
    p = s["sides"][0]
    daniya_battle.enter_black_hole(p)
    record(s, 0, "red-supergiant")
    victim = s["sides"][1]
    victim["turn"].update(raw=1, effective=1, pending=1)
    domain = next(m for m in fighter_moves("gojo", 19) if "domain" in m.tags)
    event = battle.apply_move(victim, domain, version=19)
    victim["turn"]["events"].append(event)
    finish(s)
    result = battle._settle_interactions(s, "current")
    assert event["daniya_current_cancelled"]
    assert not event["domain_eligible"]
    assert result["domain"] is None


def test_exact_three_move_wheels_and_next_domain_growth():
    s = state()
    p = s["sides"][0]
    staging = fighter_form_moves("daniya", "staging", 19)
    disillusion = fighter_form_moves("daniya", "disillusion", 19)
    black = fighter_form_moves("daniya", "black-hole", 19)
    assert [m.resolved_draw_weight_units for m in staging] == [9000, 9000, 8000, 9000, 9000, 10000, 5000, 1000]
    assert [m.resolved_draw_weight_units for m in disillusion] == [10000, 10000, 6000, 8000, 4000, 12000, 5000, 5000]
    assert [m.resolved_draw_weight_units for m in black] == [40000, 30000, 20000, 10000]
    record(s, 0, "curtain")
    assert battle.move_weight_units(p, staging[-1], version=19) == 1800
    assert battle.move_weight_units(p, staging[5], version=19) == 13000
    p["daniya_form"] = "disillusion"
    record(s, 0, "curtain")
    assert battle.move_weight_units(p, disillusion[-1], version=19) == 5500
    assert p["daniya_unignorable_exhaust_units"] == 3


def test_black_hole_passive_is_additive_and_prior_timer_survives():
    s = state()
    p, target = s["sides"]
    p["daniya_form"] = "disillusion"
    record(s, 0, "timer")
    finish(s)
    battle._settle_interactions(s, "timer")
    stored = target["injury_exhaust_bonus_units"]
    assert stored == 53
    daniya_battle.enter_black_hole(p)
    s["round"] = 4
    wheel, modifiers = battle._dynamic_injury_wheel(s, 1)
    assert modifiers["permanent_exhaust_bonus_units"] == stored
    assert modifiers["daniya_multiplier"] == 1
    assert dict(wheel)["exhausted"] == 5 + stored + 200


def test_full_chunk_save_restore_and_command_order():
    initial = state("daniya", "xixi")
    for p in initial["sides"]:
        p["turn"].update(raw=3, effective=3, pending=3, done=False)
    left, right = deepcopy(initial), deepcopy(initial)
    for side in (0, 1):
        battle.play_chunk(left, side, "checkpoint", chunk_size=1)
        left = battle.loads(battle.dumps(left))
        while not left["sides"][side]["turn"]["done"]:
            battle.play_chunk(left, side, "checkpoint")
    for side in (1, 0):
        while not right["sides"][side]["turn"]["done"]:
            battle.play_chunk(right, side, "checkpoint")
    left_result = battle.resolve_round(left, "checkpoint")
    right_result = battle.resolve_round(right, "checkpoint")
    assert left_result["winner_weight_units"] == right_result["winner_weight_units"]
    assert left_result["injury_effective"] == right_result["injury_effective"]
    assert left["sides"] == right["sides"]


def test_lie_doubles_all_directed_debuffs_and_native_growth_once():
    s = state()
    p, target = s["sides"]
    p["daniya_form"] = "disillusion"
    record(s, 0, "lie")
    e = record(s, 0, "flawless")
    raw_permanent = p["daniya_permanent_reduction"]
    daniya_battle.prepare_debuffs(s)
    assert e["opponent_reduction"] == 70
    assert e["opponent_next_debt"] == 6
    assert e["opponent_exhaust_bonus_units"] == 10
    assert p["next_debt"] == 1  # 贷款自身代价不翻倍。
    assert p["daniya_permanent_reduction"] == raw_permanent * 2
    daniya_battle.prepare_debuffs(s)
    assert e["opponent_next_debt"] == 6
    assert p["daniya_permanent_reduction"] == raw_permanent * 2
    finish(s)
    battle._settle_interactions(s, "double")
    assert target["next_debt"] == 6
    assert target["injury_exhaust_bonus_units"] == 22  # 天衣+1和两份原生+.6。


def test_receiver_permanent_and_next_domain_buffs_from_copy():
    s = state("gojo", "daniya")
    p = s["sides"][0]
    for suffix in ("timer", "world-work"):
        move = next(m for m in fighter_form_moves("daniya", "staging", 19) if m.move_id.endswith(suffix))
        p["turn"].update(raw=1, effective=1, pending=1)
        battle.apply_move(
            p, move, version=19, copy_context=True, functional_fighter_id="daniya", functional_form_id="staging"
        )
    assert p["daniya_permanent_domain_units"] == 10
    assert p["daniya_domain_clash_only_steps"] == 10
    assert p["daniya_particles"] == 0
    domain = next(m for m in fighter_moves("gojo", 19) if "domain" in m.tags)
    p["turn"].update(raw=1, effective=1, pending=1)
    event = battle.apply_move(p, domain, version=19)
    assert event["receiver_domain_bonus_units"] == 10
    assert p["turn"]["domain_clash_bonus_units"] == 10
    assert not p.get("daniya_domain_clash_only_steps")


def test_count_wheel_is_frozen_at_draw_weight():
    s = state("xixi", "daniya")
    p = s["sides"][0]
    p["weight"] = Fraction(199, 2)
    battle.roll_count(s, 0, "count")
    wheel = deepcopy(p["turn"]["count_wheel"])
    assert wheel == battle.xixi_battle.count_wheel(p, battle.COUNT_WHEEL)
    p["weight"] = 200
    assert p["turn"]["count_wheel"] == wheel
    assert battle.loads(battle.dumps(s))["sides"][0]["turn"]["count_wheel"] == [list(row) for row in wheel]


def force_solo_hit(monkeypatch, *, random_index=None):
    original = battle.choose

    def select(seed, key, wheel, **kwargs):
        if ":domain:solo:" in key:
            return "hit", 0
        if random_index is not None and ":daniya:" in key and key.endswith(":double"):
            return random_index, random_index
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", select)


@pytest.mark.parametrize(
    "enemy,move_id,own_bonus",
    [("gojo", "void", 0), ("juejue", "sand-domain", 1), ("firefly", "firefly-falling-sky", 1), ("daniya", "domain", 2)],
)
def test_hourglass_blocks_domain_enemy_debuffs_preserves_own_functions(monkeypatch, enemy, move_id, own_bonus):
    s = state("xixi", enemy)
    record(s, 0, "hourglass")
    record(s, 1, move_id)
    finish(s)
    force_solo_hit(monkeypatch)
    result = battle._settle_interactions(s, "hourglass-domain")
    protected, caster = s["sides"]
    assert protected["next_debt"] == 0
    assert protected["injury_exhaust_bonus_units"] == 0
    assert protected.get("firefly_collapse", 0) == 0
    assert protected.get("firefly_collapse_next", 0) == 0
    assert protected["turn"].get("firefly_self_exhaust_delta_units", 0) == 0
    assert caster["next_action_bonus"] == own_bonus
    assert caster["weight"] == 5
    assert result["post_positive_gains"][1] == 0
    if enemy == "daniya":
        assert caster["daniya_form"] == "disillusion"
        assert caster["daniya_particles"] == Fraction(1, 2)
        assert caster["daniya_permanent_reduction"] == Fraction(521, 2000)


def test_hourglass_blocks_firefly_current_collapse_and_preserves_extension(monkeypatch):
    s = state("xixi", "firefly")
    record(s, 0, "hourglass")
    event = record(s, 1, "firefly-falling-sky")
    event["firefly_collapse_to_add"] = Fraction(3, 2)
    s["sides"][1]["firefly_form"] = "sam"
    s["sides"][1]["firefly_sam_rounds_remaining"] = 2
    finish(s)
    force_solo_hit(monkeypatch)
    result = battle._settle_interactions(s, "hourglass-collapse")
    assert s["sides"][0].get("firefly_collapse", 0) == 0
    assert s["sides"][0].get("firefly_collapse_next", 0) == 0
    assert s["sides"][1]["firefly_sam_rounds_remaining"] == 3
    assert result["firefly_domain_continuations"][0]["directed_suppressed"]


def test_reality_scales_persistent_particle_application_and_replay():
    s = state("xixi", "daniya")
    record(s, 0, "reality")
    record(s, 1, "curtain")
    finish(s)
    result = battle._settle_interactions(s, "reality-particle")
    caster = s["sides"][1]
    target = s["sides"][0]
    fact = next(f for f in result["daniya_v19"] if f["kind"] == "permanent-reduction")
    assert caster["daniya_permanent_reduction"] == Fraction(521, 2000)
    assert fact["equipment_factor"] == Fraction(7, 10)
    assert fact["applied"] == Fraction(3647, 20000)
    before = target["weight"]
    daniya_battle.finish_interactions(
        s, [{}, {}], set(), remaining=battle._remaining_event_gain, cancel=battle._cancel_event
    )
    assert target["weight"] == before
    s = battle.loads(battle.dumps(s))
    s["round"] += 1
    for p in s["sides"]:
        p["turn"] = battle.fresh_turn()
    # 下一轮装备结束，原永久账本重新按一次持续修正生效。
    before = s["sides"][0]["weight"]
    daniya_battle.finish_interactions(
        s, [{}, {}], set(), remaining=battle._remaining_event_gain, cancel=battle._cancel_event
    )
    assert before - s["sides"][0]["weight"] == Fraction(521, 2000)


def test_delayed_fourth_hextech_selects_healed_normal_count_wheel():
    s = state("xixi", "daniya")
    p = s["sides"][0]
    p.update(heavy=True, risk=2, injury_state="heavy")
    p["xixi_hextech"] = ["mind-over-matter", "goliath", "back-to-basics"]
    p["xixi_hextech_queue"] = [{"due": 1, "token": "fourth"}]
    battle.roll_count(s, 0, "fourth-hextech")
    assert p["xixi_form"] == "xixi-emperor"
    assert not p["heavy"] and p["risk"] == 0
    assert [count for count, weight in p["turn"]["count_wheel"]] == [1, 2, 3, 4, 5]
    assert p["turn"]["count_wheel"] == battle.xixi_battle.count_wheel(p, battle.COUNT_WHEEL)


@pytest.mark.parametrize("random_index,expected,post_gain", [(0, 205, 140), (1, 345, 280)])
def test_staging_domain_independent_extra_double_before_random_double(monkeypatch, random_index, expected, post_gain):
    s = state()
    record(s, 0, "curtain")
    domain = record(s, 0, "domain")
    finish(s)
    force_solo_hit(monkeypatch, random_index=random_index)
    result = battle._settle_interactions(s, "domain-extra")
    assert domain["daniya_domain_self_bonus"] == 80
    assert s["sides"][0]["weight"] == expected
    assert result["post_positive_gains"][0] == post_gain
    facts = [f for f in result["daniya_v19"] if f["kind"] == "domain-self-double"]
    assert len(facts) == 1 and facts[0]["gain"] == 80


def test_staging_domain_extra_double_obeys_reality(monkeypatch):
    s = state("daniya", "xixi")
    record(s, 0, "curtain")
    record(s, 0, "domain")
    record(s, 1, "reality")
    finish(s)
    force_solo_hit(monkeypatch, random_index=0)
    result = battle._settle_interactions(s, "domain-reality")
    assert s["sides"][0]["weight"] == 145
    assert next(f for f in result["daniya_v19"] if f["kind"] == "domain-self-double")["gain"] == 56


def test_reality_scales_firefly_conditional_reduction_once():
    s = state("xixi", "firefly")
    record(s, 0, "attack")
    record(s, 0, "reality")
    event = record(s, 1, "firefly-dream-destination")
    finish(s)
    result = battle._settle_interactions(s, "conditional-reality")
    cross = next(c for c in result["cross_effects"] if c["source_side"] == 1)
    assert event["opponent_reduction"] == Fraction(56, 5)
    assert event["firefly_conditional_reduction_applied"] == Fraction(28, 5)
    assert cross["round_reduction"] == Fraction(84, 5)


def test_reality_scales_starfield_higher_extra_reduction_once():
    s = state("xixi", "firefly")
    record(s, 0, "attack")
    record(s, 0, "reality")
    event = record(s, 1, "firefly-silent-galaxy")
    finish(s)
    result = battle._settle_interactions(s, "starfield-reality")
    assert event["firefly_starfield_higher_reduction"] == Fraction(28, 5)
    cross = next(c for c in result["cross_effects"] if c["source_side"] == 1)
    assert cross["round_reduction"] == event["opponent_reduction"] + Fraction(28, 5)


def test_disillusion_lie_ignores_zero_gain_enemy_reduction_retaining_resources():
    s = state("daniya", "daniya")
    for p in s["sides"]:
        p["daniya_form"] = "disillusion"
    record(s, 0, "lie")
    enemy = record(s, 1, "curtain")
    finish(s)
    result = battle._settle_interactions(s, "ignore-reduction")
    assert enemy["gain"] == 0 and enemy["daniya_ignored"]
    cross = next(c for c in result["cross_effects"] if c["source_side"] == 1)
    assert cross["round_reduction"] == 0 and cross["round_reduction_suppressed"]
    assert s["sides"][1]["daniya_particles"] == Fraction(1, 2)
    assert s["sides"][1]["daniya_transform_disillusion_units"] == 500
    assert s["sides"][0]["next_debt"] == 1


def test_disillusion_domain_ignores_zero_gain_enemy_reduction(monkeypatch):
    s = state("daniya", "daniya")
    for p in s["sides"]:
        p["daniya_form"] = "disillusion"
    record(s, 0, "domain")
    enemy = record(s, 1, "curtain")
    finish(s)
    force_solo_hit(monkeypatch)
    result = battle._settle_interactions(s, "domain-ignore-reduction")
    assert enemy["gain"] == 0 and enemy["daniya_ignored"]
    fact = next(f for f in result["daniya_v19"] if f["kind"] == "domain-ignore")
    assert fact["ordinal"] == enemy["ordinal"] and fact["deduction"] == 0
    cross = next(c for c in result["cross_effects"] if c["source_side"] == 1)
    assert cross["round_reduction"] == 0
    assert s["sides"][1]["daniya_particles"] == Fraction(1, 2)
    assert s["sides"][0]["next_action_bonus"] == 1


def test_hourglass_preserves_disillusion_growth_without_applying_enemy_exhaust():
    s = state("xixi", "daniya")
    s["sides"][1]["daniya_form"] = "disillusion"
    record(s, 0, "hourglass")
    record(s, 1, "curtain")
    finish(s)
    battle._settle_interactions(s, "hourglass-growth")
    assert s["sides"][0]["injury_exhaust_bonus_units"] == 0
    assert s["sides"][1]["daniya_pending_exhaust_units"] == 3
    assert s["sides"][1]["daniya_particles"] == Fraction(1, 2)
    s["round"] += 1
    for p in s["sides"]:
        p["turn"] = battle.fresh_turn()
    daniya_battle.finish_interactions(
        s, [{}, {}], set(), remaining=battle._remaining_event_gain, cancel=battle._cancel_event
    )
    assert s["sides"][0]["injury_exhaust_bonus_units"] == 3
    assert s["sides"][1]["daniya_pending_exhaust_units"] == 0


@pytest.mark.parametrize(
    "enemy,flag",
    [
        ("gojo", "infinity_used"),
        ("asamu", "asamu_pressure_ordinals"),
        ("juejue", "juejue_zero_active"),
        ("juejue", "juejue_future_simulation"),
        ("juejue", "juejue_sand_body"),
    ],
)
def test_hourglass_protects_previous_gain_from_legacy_cancel(monkeypatch, enemy, flag):
    s = state("xixi", enemy)
    record(s, 0, "attack")
    record(s, 0, "hourglass")
    caster = s["sides"][1]
    caster["turn"][flag] = [1] if flag.endswith("ordinals") else True
    finish(s)
    original = battle.choose
    monkeypatch.setattr(
        battle,
        "choose",
        lambda seed, key, wheel, **kw: (True, 0) if ":asamu:pressure:" in key else original(seed, key, wheel, **kw),
    )
    result = battle._settle_interactions(s, "legacy-cancel")
    assert s["sides"][0]["weight"] == 25
    assert not result["adjustments"][0]
    assert result["hourglass_suppressions"]
    assert caster["turn"][flag]  # 敌方自身功能仍保留。


def test_hourglass_blocks_retaliation_swap_but_preserves_function_event():
    s = state("xixi", "asamu")
    record(s, 0, "attack")
    record(s, 0, "hourglass")
    s["sides"][1]["turn"]["asamu_retaliation_ordinals"] = [1]
    finish(s)
    result = battle._settle_interactions(s, "swap")
    assert s["sides"][0]["weight"] == 25
    assert s["sides"][1]["weight"] == 5
    fact = result["retaliations"][0]
    assert fact["hourglass_swap_suppressed"] and not fact["swapped"]
    assert fact["bonus"] == 0


@pytest.mark.parametrize("outcome", ["side-1", "tie"])
def test_hourglass_preserves_domain_numbers_after_clash_loss_or_tie(monkeypatch, outcome):
    s = state("xixi", "gojo")
    realm = record(s, 0, "realm-warp")
    record(s, 0, "hourglass")
    record(s, 1, "void")
    finish(s)
    original = battle.choose
    monkeypatch.setattr(
        battle,
        "choose",
        lambda seed, key, wheel, **kw: (
            (outcome, 0) if key.endswith(":domain:clash") else original(seed, key, wheel, **kw)
        ),
    )
    result = battle._settle_interactions(s, "hourglass-clash")
    assert result["domain"]["outcome"] == outcome
    assert result["domain"]["hit_side"] != 0
    assert realm["gain"] == 24
    assert s["sides"][0]["weight"] == 31
    assert not result["adjustments"][0]
    assert s["sides"][0]["next_action_bonus"] == 0
    assert not s["sides"][0]["xixi_hextech"]


def test_hourglass_preserves_domain_numbers_after_solo_simple_domain(monkeypatch):
    s = state("xixi", "gojo")
    record(s, 0, "realm-warp")
    record(s, 0, "hourglass")
    finish(s)
    original = battle.choose
    monkeypatch.setattr(
        battle,
        "choose",
        lambda seed, key, wheel, **kw: (
            ("simple-domain", 0) if ":domain:solo:" in key else original(seed, key, wheel, **kw)
        ),
    )
    result = battle._settle_interactions(s, "hourglass-simple")
    assert result["domain"]["outcome"] == "simple-domain"
    assert result["domain"]["hit_side"] is None
    assert s["sides"][0]["weight"] == 31
    assert not result["adjustments"][0]
    assert s["sides"][0]["next_action_bonus"] == 0
    assert not s["sides"][0]["xixi_hextech"]


def test_old_version_does_not_apply_hourglass_domain_cancel_override(monkeypatch):
    s = state("gojo", "sukuna", version=18)
    record(s, 0, "void")
    record(s, 1, "shrine")
    s["sides"][0]["turn"]["xixi_hourglass"] = True
    finish(s)
    original = battle.choose
    monkeypatch.setattr(
        battle,
        "choose",
        lambda seed, key, wheel, **kw: (
            ("side-1", 0) if key.endswith(":domain:clash") else original(seed, key, wheel, **kw)
        ),
    )
    result = battle._settle_interactions(s, "old-domain")
    assert result["domain"]["winner"] == 1
    assert result["adjustments"][0][0]["gain"] > 0
