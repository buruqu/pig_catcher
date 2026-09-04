"""Battle v6：撅撅猪回溯、零、虚拟声和安全模仿的纯规则验收。"""

from copy import deepcopy
from fractions import Fraction

from pig_catcher.domain import battle as battle_module
from pig_catcher.domain.battle import (
    _settle_interactions,
    apply_move,
    choose,
    dumps,
    loads,
    new_state,
    resolve_round,
)
from pig_catcher.domain.battle_catalog import (
    FIGHTERS_BY_ID,
    JUEJUE_ACCELERATION_TIERS,
    JUEJUE_DELAY_TIERS,
    JUEJUE_TIME_MOVES,
    JUEJUE_VIRTUAL_MOVES,
)

V6 = 6


def state(left: str = "juejue", right: str = "sukuna", *, seed: str = "v6") -> dict:
    current = new_state(
        [
            {"fighter_id": fighter_id, "level": 0, "trait_bonus": 0, "tool_id": ""}
            for fighter_id in (left, right)
        ],
        seed=seed,
    )
    # 版本号由发布轮统一提升；本文件只验收提前落地的v6协议分支。
    current["version"] = V6
    return current


def ready(player: dict, pending: int = 1) -> None:
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)


def finish(player: dict) -> None:
    player["turn"].update(pending=0, done=True)


def record(player: dict, move, side: int, *, seed: str = "v6", **kwargs) -> dict:
    event = apply_move(
        player,
        move,
        seed=seed,
        round_number=1,
        side=side,
        version=V6,
        **kwargs,
    )
    event.update(round=1, side=side, fighter_id=player["snapshot"]["fighter_id"])
    player["turn"]["events"].append(deepcopy(event))
    return event


def seed_for(key: str, wheel: tuple, expected, *, prefix: str = "v6") -> str:
    return next(
        seed
        for seed in (f"{prefix}-{index}" for index in range(100_000))
        if choose(seed, key, wheel, version=V6)[0] == expected
    )


def subwheel_seed(kind: str, ordinal: int, tier: int, success: bool) -> str:
    tiers = JUEJUE_ACCELERATION_TIERS if kind == "acceleration" else JUEJUE_DELAY_TIERS
    item = next(current for current in tiers if current.tier == tier)
    key = f"1:0:move:{ordinal}:nested"
    tier_wheel = tuple((current.tier, 1) for current in tiers)
    success_wheel = ((True, item.success_chance), (False, 100 - item.success_chance))
    return next(
        seed
        for seed in (f"{kind}-{ordinal}-{tier}-{success}-{index}" for index in range(100_000))
        if choose(seed, f"{key}:tier", tier_wheel, version=V6)[0] == tier
        and choose(seed, f"{key}:success", success_wheel, version=V6)[0] is success
    )


def frozen_entry(
    fighter_id: str,
    move_id: str,
    *,
    base: int | Fraction,
    opponent_reduction: int | Fraction = 0,
    direction: str = "self",
    draws: int = 0,
    loan: bool = False,
    tags: tuple[str, ...] = (),
) -> dict:
    return {
        "available": True,
        "band": "large" if max(abs(Fraction(base)), abs(Fraction(opponent_reduction))) >= 20 else "small",
        "band_wheel": (("small", 1),),
        "band_roll": 0,
        "source_wheel": ((0, 1),),
        "source_roll": 0,
        "source_fighter_id": fighter_id,
        "source_move_id": move_id,
        "source_name": f"冻结-{move_id}",
        "base": Fraction(base),
        "direction": direction,
        "opponent_reduction": Fraction(opponent_reduction),
        "source_draws": draws,
        "source_loan": loan,
        "source_tags": list(tags),
    }


def test_current_catalog_gains_and_mimic_pool_exclude_zero_numeric_pure_functions():
    assert [move.gain for move in JUEJUE_VIRTUAL_MOVES[:3]] == [5, 15, 5]
    current = state()
    entries = [
        entry
        for band in ("large", "small")
        for entry in current["sides"][0]["juejue_mimic_pool"][band]
    ]
    ids = {(entry["fighter_id"], entry["move_id"]) for entry in entries}
    assert ("sukuna", "loan") not in ids
    assert ("daniya", "daniya-flawless") not in ids
    assert ("daniya", "daniya-unfinished-lie") not in ids
    assert ("daniya", "daniya-timed-collapse") not in ids
    assert all(
        max(abs(Fraction(entry["base"])), abs(Fraction(entry["opponent_reduction"]))) > 0
        for entry in entries
    )


def test_rewind_clears_one_whole_prior_acceleration_failure_but_not_other_debt():
    player = state()["sides"][0]
    ready(player, 2)
    player["next_debt"] = 1  # 模拟贷款等非加速欠招。
    failed = apply_move(
        player,
        JUEJUE_TIME_MOVES[2],
        seed=subwheel_seed("acceleration", 1, 3, False),
        round_number=1,
        side=0,
        version=V6,
    )
    assert failed["acceleration_failure_debt"] == 3
    assert not failed["acceleration_failure_rewound"]
    assert player["next_debt"] == 4

    rewind = apply_move(
        player,
        JUEJUE_TIME_MOVES[1],
        round_number=1,
        side=0,
        version=V6,
    )
    assert rewind["gain"] == 10
    assert rewind["rewind_debt_cleared"] == 3
    assert rewind["rewind_failure_ordinal"] == failed["ordinal"]
    assert player["next_debt"] == 1
    assert player["turn"]["juejue_acceleration_failures"] == [
        {"ordinal": 1, "debt": 3, "debt_applied": False, "rewound_by_ordinal": 2}
    ]
    assert loads(dumps(player)) == player


def test_rewind_before_failure_hangs_once_and_only_matches_acceleration_failure():
    player = state()["sides"][0]
    ready(player, 3)
    rewind = apply_move(
        player,
        JUEJUE_TIME_MOVES[1],
        round_number=1,
        side=0,
        version=V6,
    )
    assert rewind["rewind_pending_count"] == 1
    player["next_debt"] += 1  # 后加的非加速欠招也不被挂起回溯吞掉。
    failed = apply_move(
        player,
        JUEJUE_TIME_MOVES[2],
        seed=subwheel_seed("acceleration", 2, 2, False),
        round_number=1,
        side=0,
        version=V6,
    )
    assert failed["acceleration_failure_debt"] == 2
    assert failed["acceleration_failure_rewound"]
    assert failed["acceleration_rewind_source_ordinal"] == rewind["ordinal"]
    assert failed["rewind_pending_count"] == 0
    assert player["next_debt"] == 1


def test_rewind_does_not_save_exhaustion_or_erase_historical_risk(monkeypatch):
    monkeypatch.setattr(battle_module, "BATTLE_VERSION", V6)
    source = state()
    source["sides"][0].update(weight=1, risk=1, injury_state="light")
    source["sides"][0]["turn"].update(done=True, juejue_rewind=True)
    source["sides"][1].update(weight=10_000)
    source["sides"][1]["turn"]["done"] = True

    exhausted = None
    rewound = None
    for index in range(20_000):
        candidate = deepcopy(source)
        summary = resolve_round(candidate, f"v6-rewind-injury-{index}")
        if summary["loser"] != 0:
            continue
        if summary["injury"] == "exhausted" and exhausted is None:
            exhausted = summary
        if summary["injury"] in {"light", "heavy"} and rewound is None:
            rewound = summary
        if exhausted and rewound:
            break
    assert exhausted is not None and exhausted["natural_end"] and not exhausted["injury_rewound"]
    assert rewound is not None and rewound["injury_rewound"]
    assert rewound["after"][0]["risk"] == 1


def test_relative_zero_uses_only_first_failed_pair_and_repetition_cannot_rescue_it():
    player = state()["sides"][0]
    ready(player, 3)
    first = apply_move(
        player,
        JUEJUE_TIME_MOVES[2],
        seed=subwheel_seed("acceleration", 1, 3, False),
        round_number=1,
        side=0,
        version=V6,
    )
    second = apply_move(
        player,
        JUEJUE_TIME_MOVES[3],
        seed=subwheel_seed("delay", 2, 3, True),
        round_number=1,
        side=0,
        version=V6,
    )
    assert not first["subwheel"]["success"] and second["subwheel"]["success"]
    assert second["relative_zero"]["eligible"] is False
    assert second["relative_zero"]["reason"] == "首次加速或首次时延失败"
    follow = apply_move(
        player,
        JUEJUE_TIME_MOVES[2],
        seed=subwheel_seed("acceleration", 3, 3, True),
        round_number=1,
        side=0,
        version=V6,
    )
    assert follow["relative_zero"] is None
    assert player["turn"]["juejue_zero_checked"]
    assert not player["turn"]["juejue_zero_active"]


def test_relative_zero_first_successful_but_low_tier_pair_is_final():
    player = state()["sides"][0]
    ready(player, 3)
    apply_move(
        player,
        JUEJUE_TIME_MOVES[2],
        seed=subwheel_seed("acceleration", 1, 1, True),
        round_number=1,
        side=0,
        version=V6,
    )
    low = apply_move(
        player,
        JUEJUE_TIME_MOVES[3],
        seed=subwheel_seed("delay", 2, 1, True),
        round_number=1,
        side=0,
        version=V6,
    )
    assert low["relative_zero"]["tier_sum"] == 2
    assert not low["relative_zero"]["eligible"]
    assert low["relative_zero"]["wheel"] == () and low["relative_zero"]["roll"] is None
    repeat = apply_move(
        player,
        JUEJUE_TIME_MOVES[3],
        seed=subwheel_seed("delay", 3, 3, True),
        round_number=1,
        side=0,
        version=V6,
    )
    assert repeat["relative_zero"] is None


def test_relative_zero_qualifying_first_pair_has_one_exact_fifty_fifty_fact():
    player = state()["sides"][0]
    ready(player, 2)
    first = apply_move(
        player,
        JUEJUE_TIME_MOVES[2],
        seed=subwheel_seed("acceleration", 1, 2, True),
        round_number=1,
        side=0,
        version=V6,
    )
    second = apply_move(
        player,
        JUEJUE_TIME_MOVES[3],
        seed=subwheel_seed("delay", 2, 3, True),
        round_number=1,
        side=0,
        version=V6,
    )
    fact = second["relative_zero"]
    assert fact["eligible"] and fact["wheel"] == ((True, 1), (False, 1))
    assert fact["first_acceleration"]["ordinal"] == first["ordinal"]
    assert fact["first_delay"]["ordinal"] == second["ordinal"]


def test_each_future_simulation_draw_has_an_independent_invalidation_fact():
    current = state()
    defender, attacker = current["sides"]
    ready(defender, 2)
    first = record(defender, JUEJUE_VIRTUAL_MOVES[1], 0, seed="future-a")
    second = record(defender, JUEJUE_VIRTUAL_MOVES[1], 0, seed="future-b")
    finish(defender)
    ready(attacker, 2)
    hit_a = record(attacker, FIGHTERS_BY_ID["sukuna"].moves[1], 1)
    hit_b = record(attacker, FIGHTERS_BY_ID["sukuna"].moves[2], 1)
    finish(attacker)

    interactions = _settle_interactions(current, "future-independent")
    active = [item for item in interactions["future_simulations"] if item["active"]]
    assert [item["source_ordinal"] for item in active] == [first["ordinal"], second["ordinal"]]
    assert [item["chance_ordinal"] for item in active] == [1, 2]
    assert {item["selected_ordinal"] for item in active} == {hit_a["ordinal"], hit_b["ordinal"]}
    assert sum((item["cancelled_gain"] for item in active), Fraction(0)) == hit_a["gain"] + hit_b["gain"]
    assert attacker["weight"] == 5


def test_music_first_activates_and_repeat_adds_two_draws_without_stacking():
    player = state()["sides"][0]
    ready(player, 3)
    first = apply_move(player, JUEJUE_VIRTUAL_MOVES[5], round_number=1, side=0, version=V6)
    repeated = apply_move(player, JUEJUE_VIRTUAL_MOVES[5], round_number=1, side=0, version=V6)
    follow = apply_move(player, JUEJUE_TIME_MOVES[5], round_number=1, side=0, version=V6)
    assert first["music_activated"] and not first["music_repeated"]
    assert first["gain"] == 0 and first["extra_draws"] == 0
    assert repeated["music_repeated"] and not repeated["music_activated"]
    assert repeated["music_gain"] == 5 and repeated["extra_draws"] == 2
    assert follow["music_gain"] == 5 and follow["gain"] == 35
    assert player["turn"]["juejue_music_repeat_ordinals"] == [repeated["ordinal"]]


def test_active_mimic_uses_frozen_numeric_and_general_effects_and_suppresses_draws():
    player = state()["sides"][0]
    ready(player)
    # 故意用与当前目录同ID但不同冻结数值，证明热修目录不会覆盖本场事实。
    frozen = frozen_entry(
        "sukuna",
        "black-flash",
        base=17,
        draws=2,
        tags=("black-flash",),
    )
    event = apply_move(
        player,
        JUEJUE_VIRTUAL_MOVES[3],
        round_number=1,
        side=0,
        version=V6,
        mimic_override=frozen,
    )
    assert event["special_base"] == 17 and event["gain"] == 17
    assert event["tags"] == ["juejue-mimic"]
    assert event["functional_fighter_id"] == "sukuna"
    assert event["functional_move_id"] == "black-flash"
    assert event["functional_tags"] == ["black-flash"]
    assert event["requested_extra_draws"] == event["extra_draws"] == 2
    assert event["extra_draws_suppressed"] == 0
    assert player["turn"]["pending"] == 2
    assert player["black_flash_stacks"] == 1


def test_mimic_does_not_import_source_only_wheel_growth():
    player = state()["sides"][0]
    ready(player, 2)
    bathe = apply_move(
        player,
        JUEJUE_VIRTUAL_MOVES[3],
        version=V6,
        mimic_override=frozen_entry(
            "asamu",
            "asamu-bathe",
            base=10,
            tags=("asamu", "asamu-bathe"),
        ),
    )
    blue = apply_move(
        player,
        JUEJUE_VIRTUAL_MOVES[3],
        version=V6,
        mimic_override=frozen_entry(
            "gojo",
            "blue",
            base=13,
            tags=("blue-red",),
        ),
    )
    assert player["asamu_tea_bonus_units"] == 0
    assert player["purple_weight_steps"] == 0
    assert bathe["suppressed_source_local_effects"] == ["asamu-milk-tea-draw-weight"]
    assert blue["suppressed_source_local_effects"] == ["gojo-purple-draw-weight"]


def test_active_mimic_copies_directed_and_pressure_effects_with_auditable_facts():
    current = state(right="gojo")
    defender, attacker = current["sides"]
    ready(defender)
    pressure = record(
        defender,
        JUEJUE_VIRTUAL_MOVES[3],
        0,
        mimic_override=frozen_entry(
            "asamu",
            "asamu-pressure-king",
            base=7,
            tags=("asamu", "asamu-pressure-king"),
        ),
    )
    finish(defender)
    ready(attacker)
    hit = record(attacker, FIGHTERS_BY_ID["gojo"].moves[0], 1)
    finish(attacker)
    key = f"1:asamu:pressure:0:{pressure['ordinal']}:1:{hit['ordinal']}"
    seed = seed_for(key, ((True, 33), (False, 67)), True, prefix="mimic-pressure")
    interactions = _settle_interactions(current, seed)
    check = next(item for item in interactions["pressure_checks"] if item["hit"])
    assert check["source_ordinal"] == pressure["ordinal"]
    assert check["target_ordinal"] == hit["ordinal"]
    assert check["cancelled_gain"] == hit["gain"]
    assert pressure["mimic"]["effect_summary"]["own_gain"] == pressure["gain"]


def test_copied_domain_keeps_general_hit_effect_but_never_reenters_domain_clash():
    current = state(right="sukuna")
    caster, target = current["sides"]
    ready(caster)
    copied = record(
        caster,
        JUEJUE_VIRTUAL_MOVES[3],
        0,
        mimic_override=frozen_entry(
            "gojo",
            "void",
            base=30,
            tags=("domain",),
        ),
    )
    finish(caster)
    finish(target)
    interactions = _settle_interactions(current, "copied-domain-no-reentry")
    assert interactions["domain"] is None
    assert copied["domain_reentry_suppressed"] and not copied["domain_eligible"]
    assert copied["opponent_next_debt"] == 1
    assert target["next_debt"] == 1
    assert copied["mimic"]["copied_domain_effect"] == "无量空处命中：对方下回合出招数-1"


def test_fixed_copy_context_suppresses_draws_and_domain_reentry_but_keeps_hit_effect():
    player = state(left="asamu")["sides"][0]
    ready(player)
    copied = apply_move(
        player,
        FIGHTERS_BY_ID["gojo"].moves[7],
        version=V6,
        functional_fighter_id="gojo",
        allow_extra_draws=False,
        copy_context=True,
    )
    assert copied["copy_context"]
    assert copied["domain_reentry_suppressed"] and not copied["domain_eligible"]
    assert copied["opponent_next_debt"] == 1

    player["turn"]["pending"] = 1
    flash = apply_move(
        player,
        FIGHTERS_BY_ID["gojo"].moves[4],
        version=V6,
        functional_fighter_id="gojo",
        allow_extra_draws=False,
        copy_context=True,
    )
    assert flash["requested_extra_draws"] == flash["extra_draws_suppressed"] == 2
    assert flash["extra_draws"] == 0 and player["black_flash_stacks"] == 1


def test_chaos_domain_auto_mimic_uses_same_effect_engine_and_returns_generated_event():
    current = state(right="sukuna")
    caster, target = current["sides"]
    frozen = frozen_entry("gojo", "void", base=30, tags=("domain",))
    caster["juejue_mimic_pool"] = {
        "large": [
            {
                "fighter_id": frozen["source_fighter_id"],
                "move_id": frozen["source_move_id"],
                "name": frozen["source_name"],
                "base": frozen["base"],
                "direction": frozen["direction"],
                "opponent_reduction": frozen["opponent_reduction"],
                "draws": frozen["source_draws"],
                "loan": frozen["source_loan"],
                "tags": frozen["source_tags"],
            }
        ],
        "small": [],
    }
    ready(caster)
    chaos = record(caster, JUEJUE_VIRTUAL_MOVES[-1], 0)
    finish(caster)
    finish(target)
    seed = seed_for("1:domain:solo:0", (("hit", 8), ("simple-domain", 2)), "hit", prefix="chaos-v6")
    interactions = _settle_interactions(current, seed)
    generated = interactions["generated_events"]
    assert len(generated) == 1
    event = generated[0]
    assert event["generated_by"] == "chaos-domain-auto-mimic"
    assert event["move_id"] == "virtual-mimic" and event["source_move_id"] == "void"
    assert event["domain_reentry_suppressed"] and not event["domain_eligible"]
    assert event["requested_extra_draws"] == event["extra_draws"] == 0
    assert target["next_debt"] == 1
    domain = interactions["domain"]
    assert domain["domain_ids"][0] == [chaos["move_id"]]
    assert domain["auto_mimic"]["event_ordinal"] == event["ordinal"]
    assert domain["auto_mimic"]["effect_summary"]["opponent_next_debt"] == 1
    restored = loads(dumps(generated))
    assert dumps(restored) == dumps(generated)
    assert isinstance(restored[0]["gain"], Fraction)


def test_chaos_domain_auto_mimic_suppresses_copied_extra_draws_only_in_settlement_context():
    current = state(right="sukuna")
    caster, target = current["sides"]
    caster["juejue_mimic_pool"] = {
        "large": [],
        "small": [
            {
                "fighter_id": "sukuna",
                "move_id": "black-flash",
                "name": "黑闪！",
                "base": Fraction(10),
                "direction": "self",
                "opponent_reduction": Fraction(0),
                "draws": 2,
                "loan": False,
                "tags": ["black-flash"],
            }
        ],
    }
    ready(caster)
    record(caster, JUEJUE_VIRTUAL_MOVES[-1], 0)
    finish(caster)
    finish(target)
    seed = seed_for(
        "1:domain:solo:0",
        (("hit", 8), ("simple-domain", 2)),
        "hit",
        prefix="chaos-draw-suppression",
    )
    event = _settle_interactions(current, seed)["generated_events"][0]
    assert event["requested_extra_draws"] == event["extra_draws_suppressed"] == 2
    assert event["extra_draws"] == 0
    assert caster["turn"]["pending"] == 0 and caster["turn"]["done"]
    assert caster["black_flash_stacks"] == 1


def test_v5_protocol_keeps_old_once_only_and_numeric_only_behaviour():
    current = new_state(
        [
            {"fighter_id": "juejue", "level": 0, "trait_bonus": 0, "tool_id": ""},
            {"fighter_id": "sukuna", "level": 0, "trait_bonus": 0, "tool_id": ""},
        ],
        seed="v5-compat",
    )
    player = current["sides"][0]
    ready(player, 4)
    first_future = apply_move(player, JUEJUE_VIRTUAL_MOVES[1], version=5)
    second_future = apply_move(player, JUEJUE_VIRTUAL_MOVES[1], version=5)
    first_music = apply_move(player, JUEJUE_VIRTUAL_MOVES[5], version=5)
    second_music = apply_move(player, JUEJUE_VIRTUAL_MOVES[5], version=5)
    assert first_future["gain"] == second_future["gain"] == 0
    assert first_future["future_simulation_activated"]
    assert not second_future["future_simulation_activated"]
    assert first_music["extra_draws"] == second_music["extra_draws"] == 0
    assert not second_music["music_repeated"]

    another = new_state(
        [
            {"fighter_id": "juejue", "level": 0, "trait_bonus": 0, "tool_id": ""},
            {"fighter_id": "sukuna", "level": 0, "trait_bonus": 0, "tool_id": ""},
        ]
    )["sides"][0]
    ready(another)
    old_mimic = apply_move(
        another,
        JUEJUE_VIRTUAL_MOVES[3],
        version=5,
        mimic_override=frozen_entry(
            "sukuna",
            "black-flash",
            base=10,
            draws=2,
            tags=("black-flash",),
        ),
    )
    assert old_mimic["gain"] == 10
    assert old_mimic["extra_draws"] == old_mimic["requested_extra_draws"] == 0
    assert another["black_flash_stacks"] == 0
