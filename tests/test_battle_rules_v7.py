"""达妮娅、阿萨姆、熠～噜猪及当前Battle v14的聚焦规则验收。"""

from copy import deepcopy
from fractions import Fraction

from pig_catcher.domain.battle import (
    _domain_resolution,
    _dynamic_injury_wheel,
    _settle_interactions,
    apply_move,
    choose,
    move_weight_units,
    new_state,
    play_chunk,
)
from pig_catcher.domain.battle_catalog import (
    ASAMU_MOVES,
    BATTLE_RULE_VERSION,
    DANIYA_COMMON_MOVES,
    DANIYA_DISILLUSION_MOVES,
    DANIYA_STAGING_MOVES,
    FIGHTERS_BY_TEMPLATE,
    YILU_MOVES,
    YILU_PIG_TEMPLATE_IDS,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.services.battle_views import wheels

DANIYA_DOMAIN = next(move for move in DANIYA_COMMON_MOVES if move.move_id == "daniya-domain")


def state(left: str = "daniya", right: str = "asamu") -> dict:
    return new_state(
        [
            {"fighter_id": fighter_id, "level": 0, "trait_bonus": 0, "tool_id": ""}
            for fighter_id in (left, right)
        ],
        seed="v7",
    )


def ready(player: dict, pending: int = 1) -> None:
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def record(player: dict, move, side: int, *, seed: str = "v7", **kwargs) -> dict:
    event = apply_move(
        player,
        move,
        seed=seed,
        round_number=1,
        side=side,
        version=BATTLE_RULE_VERSION,
        **kwargs,
    )
    event.update(round=1, side=side, fighter_id=player["snapshot"]["fighter_id"])
    player["turn"]["events"].append(deepcopy(event))
    return event


def finish(player: dict) -> None:
    player["turn"].update(pending=0, done=True)


def test_v8_catalog_has_exact_new_daniya_asamu_and_yilu_definitions():
    assert BATTLE_RULE_VERSION == 14
    assert [move.gain for move in DANIYA_STAGING_MOVES] == [12, 16, 20, 40, 24]
    assert [move.opponent_reduction for move in DANIYA_DISILLUSION_MOVES] == [14, 9, 11, 22, 13]
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
    assert DANIYA_COMMON_MOVES[2].resolved_opponent_reduction_tenths == 0
    assert [move.gain for move in ASAMU_MOVES[:5]] == [10, 20, 1, 30, 0]
    assert ASAMU_MOVES[3].draws == 2 and ASAMU_MOVES[4].draws == 1
    assert len(YILU_MOVES) == 9
    assert [move.resolved_draw_weight_units for move in YILU_MOVES] == [
        10000,
        10000,
        10000,
        10000,
        10000,
        2000,
        5000,
        10000,
        10000,
    ]
    assert {FIGHTERS_BY_TEMPLATE[item].fighter_id for item in YILU_PIG_TEMPLATE_IDS} == {"yilu"}


def test_yilu_wheel_view_exposes_v8_weights_and_exact_medic_specialist_rules():
    identity = CommandIdentity(ScopeKey("qq-official", "fixture"), "stream", "player", "轮盘玩家")
    rendered = wheels(identity, "yilu", level=5).text()
    assert "熠～噜猪 · 罗德岛干员战斗盘" in rendered
    assert "权重0.2；清空指示物，重伤/力竭盘×0.5；重伤恢复轻伤，再+2指示物" in rendered
    assert "权重0.5；清空指示物，再抽2次非医疗、非特种干员，并+1指示物" in rendered
    assert "特种再部署" in rendered


def test_daniya_domain_weight_modifiers_and_timed_collapse_are_exact():
    current = state("daniya", "daniya")
    left, right = current["sides"]
    ready(left, 3)
    record(left, DANIYA_COMMON_MOVES[0], 0)
    record(left, DANIYA_COMMON_MOVES[1], 0)
    record(left, DANIYA_DOMAIN, 0)
    finish(left)
    ready(right)
    record(right, DANIYA_DOMAIN, 1)
    finish(right)
    domain = _domain_resolution(current, "v7-domain", [{}, {}])
    assert domain is not None
    assert domain["base_strengths"] == [30, 30]
    assert domain["strengths"] == [32, 28]
    assert domain["wheel"] == (("side-0", 32), ("side-1", 28), ("tie", 30))

    collapse_player = state()["sides"][0]
    ready(collapse_player)
    collapse = apply_move(collapse_player, DANIYA_COMMON_MOVES[2])
    assert collapse["gain"] == 0
    assert collapse["opponent_reduction"] == 0
    assert collapse["daniya_domain_steps_after"] == 0


def test_asamu_new_growth_chain_dynamic_weights_and_two_domain_copies():
    current = state("asamu", "daniya")
    player = current["sides"][0]
    ready(player, 6)
    apply_move(player, ASAMU_MOVES[0])
    assert move_weight_units(player, ASAMU_MOVES[1]) == 15000
    apply_move(player, ASAMU_MOVES[1])
    assert move_weight_units(player, ASAMU_MOVES[1]) == 10000
    assert move_weight_units(player, ASAMU_MOVES[3]) == 3000
    sleep = apply_move(player, ASAMU_MOVES[2])
    assert sleep["gain"] == 1 and player["asamu_future_gain_bonus"] == 5
    charge = apply_move(player, ASAMU_MOVES[4])
    assert charge["gain"] == 5 and move_weight_units(player, ASAMU_MOVES[3]) == 13000
    prime = apply_move(player, ASAMU_MOVES[3])
    assert prime["gain"] == 35 and move_weight_units(player, ASAMU_MOVES[3]) == 3000

    player["injury_state"] = "none"
    assert move_weight_units(player, ASAMU_MOVES[5]) == 5000
    player["injury_state"] = "light"
    assert move_weight_units(player, ASAMU_MOVES[5]) == 10000
    player["injury_state"] = "heavy"
    assert move_weight_units(player, ASAMU_MOVES[5]) == 20000

    domain = {
        "hit_side": 0,
        "domain_ids": [["asamu-domain"], []],
        "domain_fighter_ids": [["asamu"], []],
    }
    from pig_catcher.domain.battle import _asamu_domain_copies

    copies = _asamu_domain_copies(current, "v7-asamu-copy", domain)
    assert len(copies) == 2
    assert [item["copy_slot"] for item in copies] == [1, 2]


def test_yilu_markers_babel_redeploy_operator_cap_and_true_damage():
    current = state("yilu", "sukuna")
    player = current["sides"][0]
    ready(player)
    vanguard = apply_move(player, YILU_MOVES[0])
    assert vanguard["gain"] == 5
    assert player["yilu_markers"] == 2 and player["yilu_future_base_bonus"] == 2
    assert player["turn"]["pending"] == 1

    # 巴别塔下一抽严格来自七名干员，且完整效果重复两次。
    player["turn"].update(pending=1, done=False)
    babel = apply_move(player, YILU_MOVES[-1])
    assert babel["extra_draws"] == 1 and player["turn"]["yilu_double_operator_draws"] == 1
    generated = play_chunk(current, 0, "v7-babel", chunk_size=1)[0]
    assert generated["yilu_babel_redeploy"]
    assert generated["yilu_effect_repeats"] == 2
    assert "yilu-operator" in generated["tags"]

    # 第10名干员落地即终止己方连锁。
    player["turn"].update(pending=20, done=False, yilu_operator_placements=9)
    apply_move(player, YILU_MOVES[2])
    assert player["turn"]["yilu_operator_placements"] == 10
    assert player["turn"]["done"] and player["turn"]["pending"] == 0

    true_state = state("yilu", "sukuna")
    caster = true_state["sides"][0]
    caster["yilu_markers"] = 7
    ready(caster)
    event = record(caster, YILU_MOVES[1], 0)
    finish(caster)
    finish(true_state["sides"][1])
    assert event["yilu_consumed_markers"] == 8 and event["yilu_true_damage_added"] == 1
    interactions = _settle_interactions(true_state, "v7-true-damage")
    fact = interactions["yilu_true_damage"][0]
    assert fact["after"] == fact["before"] * 2


def test_yilu_medic_recovers_heavy_and_specialist_redeploys_only_other_operators():
    current = state("yilu", "sukuna")
    player = current["sides"][0]
    player.update(heavy=True, risk=2, injury_state="heavy", yilu_markers=7)
    ready(player)
    medic = apply_move(player, YILU_MOVES[5])
    assert medic["yilu_consumed_markers"] == 7
    assert medic["yilu_medic_recoveries"] == [
        {"repeat": 1, "before": "heavy", "after": "light", "recovered": True}
    ]
    assert not player["heavy"] and player["risk"] == 1 and player["injury_state"] == "light"
    assert player["yilu_markers"] == 2
    wheel, modifiers = _dynamic_injury_wheel(current, 0)
    assert modifiers["yilu_injury_factor"] == Fraction(1, 2)
    weights = dict(wheel)
    # 重伤恢复轻伤后使用轻伤伤势盘，再把重伤60与力竭10各减半。
    assert Fraction(weights["heavy"], modifiers["weight_scale"]) == Fraction(30)
    assert Fraction(weights["exhausted"], modifiers["weight_scale"]) == Fraction(5)

    player["yilu_markers"] = 6
    player["turn"].update(pending=1, done=False)
    specialist = apply_move(player, YILU_MOVES[6])
    assert specialist["yilu_consumed_markers"] == 6
    assert specialist["yilu_specialist_draws_added"] == 2
    assert player["yilu_markers"] == 1
    assert player["turn"]["yilu_specialist_operator_draws"] == 2
    assert player["turn"]["yilu_injury_worsen_layers"] == 0
    generated = play_chunk(current, 0, "v8-specialist", chunk_size=2)
    assert len(generated) == 2
    assert all(item["yilu_specialist_redeploy"] for item in generated)
    assert all("yilu-medic" not in item["tags"] for item in generated)
    assert all("yilu-specialist" not in item["tags"] for item in generated)
    assert all(
        set(item["draw_wheel_move_ids"])
        == {"yilu-vanguard", "yilu-guard", "yilu-defender", "yilu-caster", "yilu-sniper"}
        for item in generated
    )


def test_yilu_domain_hope_uses_real_clash_or_solo_hit():

    clash = state("yilu", "daniya")
    ready(clash["sides"][0])
    record(clash["sides"][0], YILU_MOVES[7], 0)
    finish(clash["sides"][0])
    ready(clash["sides"][1])
    record(clash["sides"][1], DANIYA_DOMAIN, 1)
    finish(clash["sides"][1])
    wheel = (("side-0", 30), ("side-1", 30), ("tie", 30))
    seed = next(
        f"v7-yilu-domain-{index}"
        for index in range(10000)
        if choose(
            f"v7-yilu-domain-{index}",
            "1:domain:clash",
            wheel,
            version=BATTLE_RULE_VERSION,
        )[0]
        == "side-0"
    )
    interactions = _settle_interactions(clash, seed)
    assert interactions["domain"]["winner"] == 0
    assert clash["sides"][0]["next_action_bonus"] == 1
    assert clash["sides"][0]["yilu_next_round_base_bonus"] == 1

    solo = state("yilu", "sukuna")
    ready(solo["sides"][0])
    domain_event = record(solo["sides"][0], YILU_MOVES[7], 0, seed="yilu-solo")
    finish(solo["sides"][0])
    finish(solo["sides"][1])
    solo_wheel = (("hit", 8), ("simple-domain", 2))
    solo_seed = next(
        f"v11-yilu-solo-{index}"
        for index in range(10000)
        if choose(
            f"v11-yilu-solo-{index}",
            "1:domain:solo:0",
            solo_wheel,
            version=BATTLE_RULE_VERSION,
        )[0]
        == "hit"
    )
    solo_interactions = _settle_interactions(solo, solo_seed)
    domain = solo_interactions["domain"]
    assert domain["boost_side"] == 0 and domain["boost_reason"] == "领域命中"
    assert domain["boosted_ordinals"] == [domain_event["ordinal"]]
    assert domain["bonus_gain"] == Fraction(65, 2)
    assert solo["sides"][0]["weight"] == 70
    assert solo["sides"][0]["next_action_bonus"] == 1
    assert solo["sides"][0]["yilu_next_round_base_bonus"] == 1
    assert "末日方舟领域命中" in domain["effect"]
