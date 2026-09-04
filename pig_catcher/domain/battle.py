"""可恢复、精确整数、顺序无关的双人轮盘纯状态机。无I/O、时间或全局随机。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from math import lcm
from typing import Any

from .battle_catalog import (
    ASAMU_MOVES,
    BATTLE_RULE_VERSION,
    BATTLE_VERSION,
    COUNT_WHEEL,
    DANIYA_FORM_DISILLUSION,
    DANIYA_FORM_STAGING,
    FIGHTERS,
    FIGHTERS_BY_ID,
    FIREFLY_FORM_FIREFLY,
    FIREFLY_FORM_SAM,
    FIREFLY_MOVES,
    HEAVY_COUNT_WHEEL,
    INJURY_WHEELS,
    JUEJUE_ACCELERATION_TIERS,
    JUEJUE_DELAY_TIERS,
    JUEJUE_FORM_TIME,
    JUEJUE_FORM_VIRTUAL,
    LEGACY_LOOT_WEIGHTS,
    LOOT_WEIGHTS,
    MOVE_CHUNK_SIZE,
    MOVE_WEIGHT_SCALE,
    VICTORY_WEIGHT_SCALE,
    YILU_MOVES,
    BattleError,
    Move,
    fighter_form_moves,
)
from .food_effects import apply_six_star_progress
from .rules import catch_weights


def _pack(value: Any) -> Any:
    # 不依赖SQLite有符号64位，也不改变进程级Python整数安全设置。
    if isinstance(value, Fraction):
        return {"$battle-fraction": [hex(value.numerator), hex(value.denominator)]}
    if type(value) is int and value.bit_length() > 63:
        return {"$battle-int": hex(value)}
    if isinstance(value, dict):
        return {key: _pack(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_pack(item) for item in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(_pack(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(value: str) -> Any:
    def hook(item: dict) -> Any:
        if set(item) == {"$battle-int"}:
            return int(item["$battle-int"], 16)
        if set(item) == {"$battle-fraction"}:
            numerator, denominator = item["$battle-fraction"]
            return Fraction(int(numerator, 16), int(denominator, 16))
        return item

    return json.loads(value, object_hook=hook)


def weight_label(value: int | Fraction) -> str:
    exact = Fraction(value)
    if exact.denominator != 1:
        if exact.denominator in {2, 5, 10}:
            sign = "-" if exact < 0 else ""
            numerator = abs(exact.numerator) * (10 // exact.denominator)
            return f"{sign}{numerator // 10}.{numerator % 10}"
        return f"{exact.numerator}/{exact.denominator}"
    integer = exact.numerator
    if abs(integer).bit_length() <= 200:
        return str(integer)
    digits = (abs(integer).bit_length() * 30103) // 100000 + 1
    return f"超大权重（约{digits}位；精确整数结算）"


def _move_base(move: Move) -> Fraction:
    return Fraction(move.resolved_gain_tenths, VICTORY_WEIGHT_SCALE)


def _opponent_reduction_base(move: Move) -> Fraction:
    return Fraction(move.resolved_opponent_reduction_tenths, VICTORY_WEIGHT_SCALE)


def _ceil_fraction(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def _winner_units(value: int | Fraction) -> int:
    exact = Fraction(value) * VICTORY_WEIGHT_SCALE
    if exact.denominator != 1:
        raise BattleError("胜利权重没有落在0.1精确刻度上。")
    return max(1, exact.numerator)


def randbelow(seed: str, key: str, bound: int, *, version: int = BATTLE_RULE_VERSION) -> int:
    """带命名空间的拒绝采样；不取模、不转浮点，支持任意大的胜利权重。"""
    if type(bound) is not int or bound < 1:
        raise BattleError("轮盘总权重必须是正整数。")
    bits = (bound - 1).bit_length()
    if bits == 0:
        return 0
    size, attempt = (bits + 7) // 8, 0
    while True:
        raw = hashlib.shake_256(f"{version}|{seed}|{key}|{attempt}".encode()).digest(size)
        number = int.from_bytes(raw, "big") & ((1 << bits) - 1)
        if number < bound:
            return number
        attempt += 1


def choose(seed: str, key: str, wheel: tuple, *, version: int = BATTLE_RULE_VERSION) -> tuple[Any, int]:
    roll = randbelow(seed, key, sum(weight for _, weight in wheel), version=version)
    cursor = roll
    for value, weight in wheel:
        if cursor < weight:
            return value, roll
        cursor -= weight
    raise AssertionError("unreachable wheel result")


def fresh_turn() -> dict:
    return {
        "raw": None,
        "debt": 0,
        "effective": None,
        "pending": 0,
        "draws": 0,
        "done": False,
        "ready": False,
        "trait_used": False,
        "events": [],
        "infinity_used": False,
        "juejue_music": False,
        "juejue_music_repeat_ordinals": [],
        "juejue_realtime": False,
        "juejue_future_simulation": False,
        "juejue_future_simulation_ordinals": [],
        "juejue_sand_body": False,
        "juejue_zero_checked": False,
        "juejue_zero_active": False,
        "juejue_acceleration_tier": 0,
        "juejue_delay_tier": 0,
        "juejue_zero_first_acceleration": None,
        "juejue_zero_first_delay": None,
        "juejue_rewind": False,
        "juejue_rewind_count": 0,
        "juejue_rewind_pending_ordinals": [],
        "juejue_acceleration_failures": [],
        "daniya_collapse_count": 0,
        "daniya_domain_carried_units": 0,
        "daniya_world_damage_immunity": False,
        "daniya_world_effects_disabled": False,
        "daniya_world_forced_move_ids": [],
        "daniya_world_forced_form": "",
        "domain_clash_bonus_units": 0,
        "opponent_domain_clash_reduction_units": 0,
        "asamu_pressure_ordinals": [],
        "asamu_misfortune_count": 0,
        "asamu_retaliation_ordinals": [],
        "forced_milk_dragon_count": 0,
        "forced_milk_dragon_used": 0,
        "yilu_operator_placements": 0,
        "yilu_double_operator_draws": 0,
        "yilu_specialist_operator_draws": 0,
        "yilu_defender_chances": [],
        "yilu_injury_recovery_layers": 0,
        "yilu_injury_worsen_layers": 0,
        "yilu_true_damage_layers": 0,
        "yilu_force_end": False,
        "yilu_round_base_bonus": 0,
        "firefly_sam_draw_bonus_units": 0,
        "firefly_no_transform_bonus_units": 0,
        "firefly_entered_sam": False,
        "firefly_sam_skills_used": 0,
        "firefly_outgoing_collapse": 0,
        "firefly_forced_choices": [],
        "firefly_self_exhaust_delta_units": Fraction(0),
    }


def _frozen_mimic_pool() -> dict[str, list[dict]]:
    pool: dict[str, list[dict]] = {"large": [], "small": []}
    for fighter in FIGHTERS:
        if fighter.fighter_id == "juejue":
            continue
        for move in fighter.moves:
            base = _move_base(move)
            opponent_reduction = _opponent_reduction_base(move)
            # v6只允许有胜率数值或对手减益数值的招式进入池；抽中后才连同
            # 其普通功能复制。纯贷款、纯再抽等零数值招不进入池。
            magnitude = max(abs(base), abs(opponent_reduction))
            if magnitude == 0:
                continue
            entry = {
                "fighter_id": fighter.fighter_id,
                "move_id": move.move_id,
                "name": move.name,
                "base": base,
                "direction": move.direction,
                "opponent_reduction": opponent_reduction,
                "draws": int(move.draws),
                "loan": bool(move.loan),
                "tags": list(move.tags),
            }
            pool["large" if magnitude >= 20 else "small"].append(entry)
    return pool


def new_state(fighters: list[dict], *, seed: str = "") -> dict:
    if len(fighters) != 2:
        raise BattleError("对战必须有两名玩家。")
    sides = []
    mimic_pool = _frozen_mimic_pool()
    for side, snapshot in enumerate(fighters):
        if snapshot.get("fighter_id") not in FIGHTERS_BY_ID or not 0 <= snapshot.get("level", 0) <= 5:
            raise BattleError("未知战斗猪或养成等级。")
        fighter_id = snapshot["fighter_id"]
        is_juejue = fighter_id == "juejue"
        form, form_roll = "", None
        if is_juejue:
            form, form_roll = choose(
                seed,
                f"entry:{side}:juejue-form",
                ((JUEJUE_FORM_TIME, 1), (JUEJUE_FORM_VIRTUAL, 1)),
                version=BATTLE_VERSION,
            )
        sides.append(
            {
                "snapshot": deepcopy(snapshot),
                "weight": 5,
                "heavy": False,
                "risk": 0,
                "core": 0,
                "next_debt": 0,
                "next_action_bonus": 0,
                "double": False,
                "tool_used": snapshot.get("tool_id", "") == "confetti",
                "black_flash_stacks": 0,
                "purple_weight_steps": 0,
                "round_start_weight": 5,
                "round_gains": [],
                "juejue_form": form,
                "juejue_form_roll": form_roll,
                "juejue_sculpt_bonus": 0,
                "juejue_acceleration_bonus": 0,
                "juejue_delay_bonus": 0,
                "juejue_guaranteed": False,
                "juejue_sand_domain_steps": 0,
                "juejue_sand_domain_switch_units": 0,
                "juejue_realization_stacks": 0,
                "juejue_mimic_pool": deepcopy(mimic_pool) if is_juejue else {"large": [], "small": []},
                "injury_state": "none",
                "daniya_form": DANIYA_FORM_STAGING if fighter_id == "daniya" else "",
                "daniya_domain_steps": 0,
                "injury_exhaust_bonus_units": 0,
                "daniya_world_disable_next": False,
                "daniya_world_forced_move_ids_next": [],
                "daniya_world_forced_form_next": "",
                "asamu_big_stacks": 0,
                "asamu_tea_bonus_units": 0,
                "asamu_sleep_bonus_units": 0,
                "asamu_prime_bonus_units": 0,
                "asamu_prime_temp_bonus_units": 0,
                "asamu_future_gain_bonus": 0,
                "asamu_milk_dragon_next_count": 0,
                "yilu_markers": 0,
                "yilu_markers_total": 0,
                "yilu_future_base_bonus": 0,
                "yilu_next_round_base_bonus": 0,
                "firefly_form": FIREFLY_FORM_FIREFLY if fighter_id == "firefly" else "",
                "firefly_sam_rounds_remaining": 0,
                "firefly_fuel": 0,
                "firefly_collapse": 0,
                "firefly_next_sam_gain_bonus": 0,
                "turn": fresh_turn(),
            }
        )
    return {"version": BATTLE_VERSION, "round": 1, "status": "active", "winner": None, "sides": sides}


def _side(state: dict, side: int) -> dict:
    if state["version"] != BATTLE_VERSION:
        raise BattleError("该对战使用另一版本规则，需要相应规则引擎恢复，不能重新抽取。")
    if state["status"] != "active" or side not in (0, 1):
        raise BattleError("对战已结束或不是本场参与者。")
    player = state["sides"][side]
    # 2.0.0 已开始但尚未结束的现场没有 ready 字段；原地补默认值即可无损恢复。
    player.setdefault("black_flash_stacks", 0)
    player.setdefault("purple_weight_steps", 0)
    player.setdefault("next_action_bonus", 0)
    player.setdefault("round_start_weight", 5)
    player.setdefault("round_gains", [])
    player.setdefault("juejue_form", "")
    player.setdefault("juejue_form_roll", None)
    player.setdefault("juejue_sculpt_bonus", 0)
    player.setdefault("juejue_acceleration_bonus", 0)
    player.setdefault("juejue_delay_bonus", 0)
    player.setdefault("juejue_guaranteed", False)
    player.setdefault("juejue_sand_domain_steps", 0)
    player.setdefault("juejue_sand_domain_switch_units", 0)
    player.setdefault("juejue_realization_stacks", 0)
    player.setdefault("juejue_mimic_pool", {"large": [], "small": []})
    player.setdefault("injury_state", "heavy" if player.get("heavy") else "none")
    player.setdefault(
        "daniya_form",
        DANIYA_FORM_STAGING if player.get("snapshot", {}).get("fighter_id") == "daniya" else "",
    )
    player.setdefault("daniya_domain_steps", 0)
    player.setdefault("injury_exhaust_bonus_units", 0)
    player.setdefault("daniya_world_disable_next", False)
    player.setdefault("daniya_world_forced_move_ids_next", [])
    player.setdefault("daniya_world_forced_form_next", "")
    player.setdefault("asamu_big_stacks", 0)
    player.setdefault("asamu_tea_bonus_units", 0)
    player.setdefault("asamu_sleep_bonus_units", 0)
    player.setdefault("asamu_prime_bonus_units", 0)
    player.setdefault("asamu_prime_temp_bonus_units", 0)
    player.setdefault("asamu_future_gain_bonus", 0)
    player.setdefault("asamu_milk_dragon_next_count", 0)
    player.setdefault("yilu_markers", 0)
    player.setdefault("yilu_markers_total", 0)
    player.setdefault("yilu_future_base_bonus", 0)
    player.setdefault("yilu_next_round_base_bonus", 0)
    player.setdefault(
        "firefly_form",
        FIREFLY_FORM_FIREFLY if player.get("snapshot", {}).get("fighter_id") == "firefly" else "",
    )
    player.setdefault("firefly_sam_rounds_remaining", 0)
    player.setdefault("firefly_fuel", 0)
    player.setdefault("firefly_collapse", 0)
    player.setdefault("firefly_next_sam_gain_bonus", 0)
    for key, value in fresh_turn().items():
        player["turn"].setdefault(key, deepcopy(value))
    return player


def roll_count(state: dict, side: int, seed: str) -> dict:
    player = _side(state, side)
    turn = player["turn"]
    if turn["raw"] is not None:
        return {"changed": False, **deepcopy(turn)}
    wheel = HEAVY_COUNT_WHEEL if player["heavy"] else COUNT_WHEEL
    raw, roll = choose(seed, f"{state['round']}:{side}:count", wheel, version=state["version"])
    debt = player["next_debt"]
    bonus = player["next_action_bonus"]
    player["next_debt"] = 0  # 只扣下一回合，负数不会继续倒欠。
    player["next_action_bonus"] = 0
    effective = max(0, raw + bonus - debt)
    forced_milk = int(player.get("asamu_milk_dragon_next_count", 0))
    player["asamu_milk_dragon_next_count"] = 0
    turn.update(
        raw=raw,
        debt=debt,
        bonus=bonus,
        effective=effective,
        pending=effective,
        done=effective == 0,
        forced_milk_dragon_count=forced_milk,
        forced_milk_dragon_used=0,
    )
    return {"changed": True, "roll": roll, "wheel": wheel, **deepcopy(turn)}


def _juejue_subwheel(
    player: dict,
    kind: str,
    seed: str,
    key: str,
    version: int,
) -> tuple[Any, dict]:
    tiers = JUEJUE_ACCELERATION_TIERS if kind == "acceleration" else JUEJUE_DELAY_TIERS
    tier_wheel = tuple((tier.tier, 1) for tier in tiers)
    tier_value, tier_roll = choose(seed, f"{key}:tier", tier_wheel, version=version)
    tier = next(item for item in tiers if item.tier == tier_value)
    sculpt_bonus = int(player["juejue_sculpt_bonus"])
    specific_key = "juejue_acceleration_bonus" if kind == "acceleration" else "juejue_delay_bonus"
    specific_bonus = int(player[specific_key])
    guaranteed = bool(player["juejue_guaranteed"])
    player["juejue_sculpt_bonus"] = 0
    player[specific_key] = 0
    player["juejue_guaranteed"] = False
    chance = 100 if guaranteed else min(100, tier.success_chance + sculpt_bonus + specific_bonus)
    success_wheel = ((True, chance), (False, 100 - chance))
    success, success_roll = choose(seed, f"{key}:success", success_wheel, version=version)
    return tier, {
        "kind": kind,
        "tier": tier.tier,
        "tier_wheel": tier_wheel,
        "tier_roll": tier_roll,
        "base_chance": tier.success_chance,
        "sculpt_bonus": sculpt_bonus,
        "specific_bonus": specific_bonus,
        "guaranteed": guaranteed,
        "chance": chance,
        "success_wheel": success_wheel,
        "success_roll": success_roll,
        "success": bool(success),
    }


def _juejue_mimic(player: dict, seed: str, key: str, version: int) -> dict:
    pool = player.get("juejue_mimic_pool") or {"large": [], "small": []}
    if version < 6:
        # 已开局的v5及更早现场必须保留原有“仅正向直接数值”池及其索引顺序。
        pool = {
            band: [entry for entry in pool.get(band, ()) if Fraction(entry.get("base", 0)) > 0]
            for band in ("large", "small")
        }
    bands = tuple((name, 1) for name in ("large", "small") if pool.get(name))
    if not bands:
        return {
            "available": False,
            "band": "",
            "band_wheel": (),
            "band_roll": None,
            "source_wheel": (),
            "source_roll": None,
            "source_fighter_id": "",
            "source_move_id": "",
            "source_name": "",
            "base": 0,
            "direction": "self",
            "opponent_reduction": 0,
            "source_draws": 0,
            "source_loan": False,
            "source_tags": [],
        }
    band, band_roll = choose(seed, f"{key}:band", bands, version=version)
    source_wheel = tuple((index, 1) for index, _entry in enumerate(pool[band]))
    source_index, source_roll = choose(seed, f"{key}:source:{band}", source_wheel, version=version)
    source = pool[band][source_index]
    return {
        "available": True,
        "band": band,
        "band_wheel": bands,
        "band_roll": band_roll,
        "source_wheel": source_wheel,
        "source_roll": source_roll,
        "source_fighter_id": source["fighter_id"],
        "source_move_id": source["move_id"],
        "source_name": source["name"],
        "base": Fraction(source["base"]),
        "direction": source.get("direction", "self"),
        "opponent_reduction": Fraction(source.get("opponent_reduction", 0)),
        "source_draws": int(source.get("draws", 0)),
        "source_loan": bool(source.get("loan", False)),
        "source_tags": list(source.get("tags", ())),
    }


def _move_by_id(fighter_id: str, move_id: str) -> Move | None:
    fighter = FIGHTERS_BY_ID.get(fighter_id)
    if fighter is None:
        return None
    return next((move for move in fighter.moves if move.move_id == move_id), None)


def _yilu_add_markers(player: dict, amount: int) -> dict:
    """累计指示物并按终身累计每跨过9点返还招式，不因消费重复刷同一里程碑。"""

    amount = max(0, int(amount))
    before = int(player.get("yilu_markers", 0))
    total_before = int(player.get("yilu_markers_total", 0))
    after = before + amount
    total_after = total_before + amount
    threshold_draws = total_after // 9 - total_before // 9
    player["yilu_markers"] = after
    player["yilu_markers_total"] = total_after
    return {
        "amount": amount,
        "before": before,
        "after": after,
        "total_before": total_before,
        "total_after": total_after,
        "threshold_draws": threshold_draws,
    }


def _firefly_move_family(move: Move, form_id: str) -> str:
    if "sam-skill" in move.tags:
        return "sam"
    if "firefly-skill" in move.tags:
        return "firefly"
    return "sam" if form_id == FIREFLY_FORM_SAM else "firefly"


def _firefly_choice_score(move: Move, *, family: str) -> Fraction:
    """Stable auto-choice heuristic for a prompt-free QQ battle chain."""

    score = abs(_move_base(move)) + abs(_opponent_reduction_base(move))
    score += Fraction(max(0, int(move.draws)) * 8)
    if "domain" in move.tags:
        score += 12
    if family == "sam":
        score += 10
    return score


def _queue_firefly_choice(
    player: dict,
    *,
    seed: str,
    key: str,
    version: int,
    option_count: int,
    echo_choice: bool,
    source_ordinal: int,
) -> dict:
    """Freeze candidate rolls and queue exactly one selected skill for the next draw."""

    moves = FIREFLY_MOVES
    wheel = tuple((index, move_weight_units(player, move)) for index, move in enumerate(moves))
    options: list[dict] = []
    form_id = str(player.get("firefly_form") or FIREFLY_FORM_FIREFLY)
    for option in range(1, option_count + 1):
        index, roll = choose(
            seed,
            f"{key}:firefly-choice:{option}",
            wheel,
            version=version,
        )
        move = moves[index]
        family = _firefly_move_family(move, form_id)
        options.append(
            {
                "slot": option,
                "move_id": move.move_id,
                "name": move.name,
                "family": family,
                "roll": roll,
                "score": _firefly_choice_score(move, family=family),
            }
        )
    selected = max(options, key=lambda item: (Fraction(item["score"]), -int(item["slot"])))
    queued = {
        "source_ordinal": source_ordinal,
        "echo_choice": echo_choice,
        "options": deepcopy(options),
        "selected_slot": int(selected["slot"]),
        "selected_move_id": str(selected["move_id"]),
        "selected_name": str(selected["name"]),
        "selected_family": str(selected["family"]),
        "forced_gain_bonus": 10 if selected["family"] == "sam" else 0,
        "echo_scale": Fraction(1, 2) if echo_choice and selected["family"] == "firefly" else Fraction(1),
        "draw_wheel_move_ids": [move.move_id for move in moves],
        "draw_wheel_units": [weight for _index, weight in wheel],
    }
    player["turn"].setdefault("firefly_forced_choices", []).append(deepcopy(queued))
    return queued


def apply_move(
    player: dict,
    move: Move,
    *,
    seed: str = "",
    round_number: int = 1,
    side: int = 0,
    version: int = BATTLE_VERSION,
    consume_pending: bool = True,
    allow_extra_draws: bool = True,
    functional_fighter_id: str | None = None,
    functional_form_id: str | None = None,
    forced: bool = False,
    mimic_override: dict | None = None,
    copy_context: bool = False,
    effect_repeats: int = 1,
    forced_gain_bonus: int | Fraction = 0,
    firefly_echo_scale: int | Fraction = 1,
    firefly_choice_context: dict | None = None,
) -> dict:
    """应用一个确定招式。

    v5 把自方胜率、对方减益和功能拆成三条账：数值失效只会清空第一条，
    再抽、贷款、形态、伤势与对方减益不会被倒流。
    """

    turn = player["turn"]
    if consume_pending and turn["pending"] <= 0:
        raise BattleError("本回合没有待执行招式。")
    if consume_pending:
        turn["pending"] -= 1
    turn["draws"] += 1
    ordinal = int(turn["draws"])
    key = f"{round_number}:{side}:move:{ordinal}:nested"
    snapshot = player["snapshot"]
    drawn_move = move
    effects_disabled = bool(turn.get("daniya_world_effects_disabled"))
    if effects_disabled:
        # “世界·发龙图”保留抽到哪一招的审计事实，但该招的数值、标签、
        # 再抽、贷款与领域资格全部失效。既有跨回合状态不会被误消费。
        move = replace(
            move,
            gain=0,
            draws=0,
            loan=False,
            tags=(),
            direction="self",
            gain_tenths=0,
            opponent_reduction=0,
            opponent_reduction_tenths=0,
        )
    fighter_id = functional_fighter_id or snapshot.get("fighter_id", "")
    effect_fighter_id = fighter_id
    effect_tags = set(move.tags)
    effect_move_id = move.move_id
    effect_draws = int(move.draws)
    effect_loan = bool(move.loan)
    is_juejue = fighter_id == "juejue"
    is_daniya = fighter_id == "daniya"
    is_firefly = snapshot.get("fighter_id") == "firefly"
    if is_juejue:
        form_before = player.get("juejue_form", "")
    elif is_daniya:
        form_before = (
            functional_form_id
            or player.get("daniya_form")
            or DANIYA_FORM_STAGING
        )
    elif is_firefly:
        form_before = player.get("firefly_form", FIREFLY_FORM_FIREFLY)
    else:
        form_before = ""
    music_was_active = bool(turn.get("juejue_music")) and not effects_disabled
    music_gain = Fraction(5 if music_was_active else 0)
    special_base = _move_base(move)
    if version < 6 and move.move_id in {
        "virtual-realm",
        "future-simulation",
        "realtime-compute",
    }:
        # v5这三招仅有功能，不追改已开局战斗的胜率账。
        special_base = Fraction(0)
    numeric_direction = move.direction
    special_extra_draws = 0
    opponent_reduction = _opponent_reduction_base(move)
    opponent_next_debt = 0
    opponent_next_bonus = 0
    opponent_next_milk_dragons = 0
    opponent_exhaust_bonus_units = 0
    opponent_next_effects_disabled = False
    opponent_next_forced_move_ids: list[str] = []
    opponent_next_forced_form = ""
    subwheel = None
    mimic = None
    relative_zero = None
    zero_bonus = Fraction(0)
    sculpt_before = int(player.get("juejue_sculpt_bonus", 0))
    sand_steps_before = int(player.get("juejue_sand_domain_steps", 0))
    sand_switch_before = int(player.get("juejue_sand_domain_switch_units", 0))
    realization_before = int(player.get("juejue_realization_stacks", 0))
    guaranteed_before = bool(player.get("juejue_guaranteed", False))
    daniya_domain_before = int(player.get("daniya_domain_steps", 0))
    daniya_domain_carried_units = 0
    asamu_big_before = int(player.get("asamu_big_stacks", 0))
    tea_bonus_before = int(player.get("asamu_tea_bonus_units", 0))
    sleep_bonus_before = int(player.get("asamu_sleep_bonus_units", 0))
    prime_bonus_before = int(player.get("asamu_prime_bonus_units", 0))
    realtime_activated = False
    future_activated = False
    sand_body_activated = False
    music_activated = False
    music_repeated = False
    rewind_debt_cleared = 0
    rewind_failure_ordinal = None
    rewind_pending_count = len(turn.get("juejue_rewind_pending_ordinals", ()))
    acceleration_failure_debt = 0
    acceleration_failure_rewound = False
    acceleration_rewind_source_ordinal = None
    copied_domain_effect = ""
    copied_domain_effect_suppressed = ""
    suppressed_source_local_effects: list[str] = []
    effect_repeats = max(1, int(effect_repeats))
    yilu_marker_events: list[dict] = []
    yilu_sniper_shots: list[dict] = []
    yilu_defender_checks: list[dict] = []
    yilu_consumed_markers = 0
    yilu_true_damage_added = 0
    yilu_medic_recoveries: list[dict] = []
    yilu_specialist_draws_added = 0
    yilu_future_base_before = int(player.get("yilu_future_base_bonus", 0)) + int(
        turn.get("yilu_round_base_bonus", 0)
    )
    asamu_future_gain_before = int(player.get("asamu_future_gain_bonus", 0))
    firefly_fuel_before = int(player.get("firefly_fuel", 0))
    firefly_collapse_to_add = 0
    firefly_conditional_reduction = 0
    firefly_self_exhaust_delta_units = Fraction(0)
    firefly_choice = None
    firefly_echo = bool(
        is_firefly
        and form_before == FIREFLY_FORM_SAM
        and "firefly-skill" in effect_tags
    )
    firefly_echo_scale = Fraction(firefly_echo_scale)
    firefly_sam_skill = bool(is_firefly and "sam-skill" in effect_tags)
    firefly_sam_skill_index_before = int(turn.get("firefly_sam_skills_used", 0))
    firefly_entered_sam = False
    firefly_next_sam_bonus_used = 0

    if is_firefly and "firefly-skill" in effect_tags:
        if firefly_echo:
            if "firefly-crimson-cocoon" in effect_tags:
                special_base = Fraction(6) * firefly_echo_scale
                opponent_reduction = Fraction(6) * firefly_echo_scale
                firefly_collapse_to_add = 1
            elif "firefly-dream-destination" in effect_tags:
                special_base = Fraction(0)
                opponent_reduction = Fraction(10) * firefly_echo_scale
                firefly_self_exhaust_delta_units = Fraction(-1) * firefly_echo_scale
            elif "firefly-choice" in effect_tags:
                special_base = Fraction(0)
                firefly_choice = _queue_firefly_choice(
                    player,
                    seed=seed,
                    key=key,
                    version=version,
                    option_count=1,
                    echo_choice=True,
                    source_ordinal=ordinal,
                )
                special_extra_draws += 1
        else:
            player["firefly_fuel"] = min(3, firefly_fuel_before + 1)
            if "firefly-crimson-cocoon" in effect_tags:
                player["firefly_next_sam_gain_bonus"] = int(
                    player.get("firefly_next_sam_gain_bonus", 0)
                ) + 6
                turn["firefly_no_transform_bonus_units"] = int(
                    turn.get("firefly_no_transform_bonus_units", 0)
                ) + 2000
            elif "firefly-dream-destination" in effect_tags:
                firefly_self_exhaust_delta_units = Fraction(-3, 2)
                firefly_conditional_reduction = 5
            elif "firefly-choice" in effect_tags:
                firefly_choice = _queue_firefly_choice(
                    player,
                    seed=seed,
                    key=key,
                    version=version,
                    option_count=2,
                    echo_choice=False,
                    source_ordinal=ordinal,
                )
                special_extra_draws += 1
                if firefly_choice["selected_family"] == "firefly":
                    turn["domain_clash_bonus_units"] = int(
                        turn.get("domain_clash_bonus_units", 0)
                    ) + 2

    if firefly_sam_skill:
        if form_before == FIREFLY_FORM_FIREFLY:
            player["firefly_form"] = FIREFLY_FORM_SAM
            player["firefly_sam_rounds_remaining"] = max(
                2,
                int(player.get("firefly_sam_rounds_remaining", 0)),
            )
            turn["firefly_entered_sam"] = True
            firefly_entered_sam = True
        turn["firefly_sam_skills_used"] = firefly_sam_skill_index_before + 1
        fuel_gain = firefly_fuel_before * 5
        special_base += fuel_gain
        queued_bonus = int(player.get("firefly_next_sam_gain_bonus", 0))
        if queued_bonus:
            special_base += queued_bonus
            firefly_next_sam_bonus_used = queued_bonus
            player["firefly_next_sam_gain_bonus"] = 0
        if "sam-bottom-fire-slash" in effect_tags:
            firefly_collapse_to_add = 1
        elif "sam-skyfire-bombardment" in effect_tags:
            firefly_collapse_to_add = 1
        elif "sam-deathstar-overload" in effect_tags:
            firefly_collapse_to_add = 1
        elif "sam-ignite-star-sea" in effect_tags:
            if form_before == FIREFLY_FORM_SAM:
                special_base = Fraction(20 + fuel_gain + queued_bonus)
                opponent_reduction = Fraction(10)
                player["firefly_sam_rounds_remaining"] = max(
                    1,
                    int(player.get("firefly_sam_rounds_remaining", 0)),
                ) + 1
                firefly_collapse_to_add = 1
            else:
                player["firefly_sam_rounds_remaining"] = 2
                player["next_action_bonus"] += 1
                firefly_collapse_to_add = 2
            player["firefly_fuel"] = 0
        special_base += Fraction(forced_gain_bonus)

    if is_firefly and "firefly-domain" in effect_tags:
        # QQ链式指令不插入额外的中途交互：萨姆形态优先延长，否则回到流萤并获得下回合+1招。
        if form_before == FIREFLY_FORM_SAM:
            player["firefly_sam_rounds_remaining"] = max(
                1,
                int(player.get("firefly_sam_rounds_remaining", 0)),
            ) + 1
            firefly_domain_choice = "extend-sam"
        else:
            player["firefly_form"] = FIREFLY_FORM_FIREFLY
            player["next_action_bonus"] += 1
            firefly_domain_choice = "return-firefly"
    else:
        firefly_domain_choice = ""

    if firefly_self_exhaust_delta_units:
        turn["firefly_self_exhaust_delta_units"] = Fraction(
            turn.get("firefly_self_exhaust_delta_units", 0)
        ) + firefly_self_exhaust_delta_units

    if is_juejue and "juejue-accelerate" in move.tags:
        tier, subwheel = _juejue_subwheel(player, "acceleration", seed, key, version)
        if version >= 6 and turn.get("juejue_zero_first_acceleration") is None:
            turn["juejue_zero_first_acceleration"] = {
                "ordinal": ordinal,
                "tier": int(tier.tier),
                "success": bool(subwheel["success"]),
            }
        if subwheel["success"]:
            special_base = Fraction(tier.gain)
            special_extra_draws = tier.extra_draws
            turn["juejue_acceleration_tier"] = max(turn["juejue_acceleration_tier"], tier.tier)
        else:
            acceleration_failure_debt = int(tier.failure_debt)
            if version < 6:
                player["next_debt"] += acceleration_failure_debt
            else:
                pending_rewinds = turn.setdefault("juejue_rewind_pending_ordinals", [])
                acceleration_rewind_source_ordinal = pending_rewinds.pop(0) if pending_rewinds else None
                acceleration_failure_rewound = acceleration_rewind_source_ordinal is not None
                if not acceleration_failure_rewound:
                    player["next_debt"] += acceleration_failure_debt
                turn.setdefault("juejue_acceleration_failures", []).append(
                    {
                        "ordinal": ordinal,
                        "debt": acceleration_failure_debt,
                        "debt_applied": not acceleration_failure_rewound,
                        "rewound_by_ordinal": acceleration_rewind_source_ordinal,
                    }
                )
                rewind_pending_count = len(pending_rewinds)
    elif is_juejue and "juejue-delay" in move.tags:
        tier, subwheel = _juejue_subwheel(player, "delay", seed, key, version)
        if version >= 6 and turn.get("juejue_zero_first_delay") is None:
            turn["juejue_zero_first_delay"] = {
                "ordinal": ordinal,
                "tier": int(tier.tier),
                "success": bool(subwheel["success"]),
            }
        if subwheel["success"]:
            special_base = Fraction(tier.gain)
            opponent_reduction += Fraction(tier.opponent_reduction)
            opponent_next_debt = tier.opponent_debt
            turn["juejue_delay_tier"] = max(turn["juejue_delay_tier"], tier.tier)
        else:
            opponent_next_bonus = tier.failure_opponent_bonus
    elif is_juejue and "juejue-mimic" in move.tags:
        mimic = (
            deepcopy(mimic_override)
            if mimic_override is not None
            else _juejue_mimic(player, seed, key, version)
        )
        # 冻结entry是本场唯一事实源；目录热修后也不能回查覆盖数值或功能。
        special_base = Fraction(mimic.get("base", 0))
        numeric_direction = mimic.get("direction", "self")
        opponent_reduction = Fraction(mimic.get("opponent_reduction", 0))
        effect_tags = set(mimic.get("source_tags", ()))
        effect_fighter_id = str(mimic.get("source_fighter_id", ""))
        effect_move_id = str(mimic.get("source_move_id", ""))
        effect_draws = int(mimic.get("source_draws", 0))
        effect_loan = bool(mimic.get("source_loan", False))
        if version < 6:
            # v5及更早现场保持旧语义：只取正向数值，不复制任何功能或定向减权。
            special_base = abs(special_base)
            opponent_reduction = Fraction(0)
            effect_tags = set()
            effect_fighter_id = fighter_id
            effect_move_id = move.move_id
            effect_draws = int(move.draws)
            effect_loan = bool(move.loan)
    elif is_juejue and "juejue-make-real" in move.tags:
        special_base = Fraction(12 + 5 * realization_before)
        player["juejue_realization_stacks"] += 1

    is_copy = mimic is not None or copy_context

    # 熠～噜猪的干员效果按确定的子轮盘直接记入事件；巴别塔再部署的
    # 干员只占一个放置名额，但其完整效果顺序执行两遍。
    if "yilu-operator" in effect_tags:
        if snapshot.get("fighter_id") == "yilu":
            turn["yilu_operator_placements"] = int(turn.get("yilu_operator_placements", 0)) + 1
        if "yilu-vanguard" in effect_tags:
            special_base = Fraction(5 * effect_repeats)
            for _repeat in range(effect_repeats):
                marker = _yilu_add_markers(player, 2)
                yilu_marker_events.append(marker)
                special_extra_draws += 1 + int(marker["threshold_draws"])
            player["yilu_future_base_bonus"] = int(
                player.get("yilu_future_base_bonus", 0)
            ) + 2 * effect_repeats
        elif "yilu-guard" in effect_tags:
            special_base = Fraction(0)
            for _repeat in range(effect_repeats):
                marker = _yilu_add_markers(player, 1)
                yilu_marker_events.append(marker)
                special_extra_draws += int(marker["threshold_draws"])
                consumed = int(player.get("yilu_markers", 0))
                player["yilu_markers"] = 0
                yilu_consumed_markers += consumed
                special_base += consumed * 5
                if consumed >= 8:
                    yilu_true_damage_added += 1
            turn["yilu_true_damage_layers"] = int(
                turn.get("yilu_true_damage_layers", 0)
            ) + yilu_true_damage_added
        elif "yilu-defender" in effect_tags:
            special_base = Fraction(2 * effect_repeats)
            for repeat_index in range(1, effect_repeats + 1):
                marker = _yilu_add_markers(player, 1)
                yilu_marker_events.append(marker)
                special_extra_draws += int(marker["threshold_draws"])
                hit, hit_roll = choose(
                    seed,
                    f"{key}:yilu:defender:{repeat_index}:hit",
                    ((True, 70), (False, 30)),
                    version=version,
                )
                check = {
                    "source_ordinal": ordinal,
                    "repeat": repeat_index,
                    "hit": bool(hit),
                    "hit_roll": hit_roll,
                }
                turn.setdefault("yilu_defender_chances", []).append(check)
                yilu_defender_checks.append(deepcopy(check))
        elif "yilu-caster" in effect_tags:
            special_base = Fraction(0)
            for _repeat in range(effect_repeats):
                marker = _yilu_add_markers(player, 3)
                yilu_marker_events.append(marker)
                special_extra_draws += int(marker["threshold_draws"])
                available = int(player.get("yilu_markers", 0))
                groups = available // 6
                if groups:
                    consumed = groups * 6
                    player["yilu_markers"] = available - consumed
                    yilu_consumed_markers += consumed
                    special_base += groups * 40
                else:
                    fallback = _yilu_add_markers(player, 1)
                    yilu_marker_events.append(fallback)
                    special_extra_draws += int(fallback["threshold_draws"])
        elif "yilu-sniper" in effect_tags:
            special_base = Fraction(0)
            for repeat_index in range(1, effect_repeats + 1):
                followup_bonus = 0
                shot_roll = randbelow(
                    seed,
                    f"{key}:yilu:sniper:{repeat_index}:count",
                    10,
                    version=version,
                )
                shots = shot_roll + 1
                for shot_index in range(1, shots + 1):
                    followup_before = followup_bonus
                    shot_gain = 1 + followup_before
                    add_marker, add_roll = choose(
                        seed,
                        f"{key}:yilu:sniper:{repeat_index}:{shot_index}:add",
                        ((True, 1), (False, 1)),
                        version=version,
                    )
                    marker = None
                    if add_marker:
                        marker = _yilu_add_markers(player, 1)
                        yilu_marker_events.append(marker)
                        special_extra_draws += int(marker["threshold_draws"])
                    consume_marker, consume_roll = choose(
                        seed,
                        f"{key}:yilu:sniper:{repeat_index}:{shot_index}:consume",
                        ((True, 1), (False, 1)),
                        version=version,
                    )
                    consumed = bool(consume_marker and int(player.get("yilu_markers", 0)) > 0)
                    if consumed:
                        player["yilu_markers"] -= 1
                        yilu_consumed_markers += 1
                        # 本枪消耗的指示物从下一枪开始生效；连续命中可累积。
                        followup_bonus += 2
                    special_base += shot_gain
                    yilu_sniper_shots.append(
                        {
                            "repeat": repeat_index,
                            "shot": shot_index,
                            "shot_count": shots,
                            "shot_roll": shot_roll,
                            "gain": shot_gain,
                            "base_bonus": yilu_future_base_before,
                            "effective_gain": shot_gain + yilu_future_base_before,
                            "followup_bonus_before": followup_before,
                            "followup_bonus_after": followup_bonus,
                            "add_marker": bool(add_marker),
                            "add_roll": add_roll,
                            "consume_requested": bool(consume_marker),
                            "consume_roll": consume_roll,
                            "consumed": consumed,
                        }
                    )
        elif "yilu-medic" in effect_tags:
            special_base = Fraction(0)
            for repeat_index in range(1, effect_repeats + 1):
                consumed = int(player.get("yilu_markers", 0))
                player["yilu_markers"] = 0
                yilu_consumed_markers += consumed
                turn["yilu_injury_recovery_layers"] = int(
                    turn.get("yilu_injury_recovery_layers", 0)
                ) + 1
                injury_before = str(player.get("injury_state", "none"))
                recovered = injury_before == "heavy" or bool(player.get("heavy"))
                if recovered:
                    player["heavy"] = False
                    player["risk"] = 1
                    player["injury_state"] = "light"
                yilu_medic_recoveries.append(
                    {
                        "repeat": repeat_index,
                        "before": injury_before,
                        "after": str(player.get("injury_state", "none")),
                        "recovered": recovered,
                    }
                )
                marker = _yilu_add_markers(player, 2)
                yilu_marker_events.append(marker)
                special_extra_draws += int(marker["threshold_draws"])
        elif "yilu-specialist" in effect_tags:
            special_base = Fraction(0)
            for _repeat in range(effect_repeats):
                consumed = int(player.get("yilu_markers", 0))
                player["yilu_markers"] = 0
                yilu_consumed_markers += consumed
                marker = _yilu_add_markers(player, 1)
                yilu_marker_events.append(marker)
                special_extra_draws += int(marker["threshold_draws"])
                turn["yilu_specialist_operator_draws"] = int(
                    turn.get("yilu_specialist_operator_draws", 0)
                ) + 2
                yilu_specialist_draws_added += 2
                special_extra_draws += 2

    if "yilu-babel" in effect_tags:
        turn["yilu_double_operator_draws"] = int(
            turn.get("yilu_double_operator_draws", 0)
        ) + 1
        special_extra_draws += 1
        player["next_debt"] += 1

    if is_juejue and "juejue-sculpt" in move.tags:
        player["juejue_sculpt_bonus"] = min(20, player["juejue_sculpt_bonus"] + 5)
        player["juejue_sand_domain_steps"] += 1
    if is_juejue and "juejue-rewind" in move.tags:
        turn["juejue_rewind"] = True
        if version >= 6:
            turn["juejue_rewind_count"] = int(turn.get("juejue_rewind_count", 0)) + 1
            for failure in reversed(turn.setdefault("juejue_acceleration_failures", [])):
                if not failure.get("debt_applied") or failure.get("rewound_by_ordinal") is not None:
                    continue
                rewind_debt_cleared = int(failure.get("debt", 0))
                rewind_failure_ordinal = int(failure["ordinal"])
                player["next_debt"] = max(
                    0, int(player.get("next_debt", 0)) - rewind_debt_cleared
                )
                failure["debt_applied"] = False
                failure["rewound_by_ordinal"] = ordinal
                break
            else:
                turn.setdefault("juejue_rewind_pending_ordinals", []).append(ordinal)
            rewind_pending_count = len(turn.get("juejue_rewind_pending_ordinals", ()))
    if is_juejue and "juejue-sand-body" in move.tags and not turn["juejue_sand_body"]:
        turn["juejue_sand_body"] = True
        sand_body_activated = True
    if is_juejue and "juejue-future-simulation" in move.tags:
        if version >= 6 or not turn["juejue_future_simulation"]:
            turn["juejue_future_simulation"] = True
            turn.setdefault("juejue_future_simulation_ordinals", []).append(ordinal)
            future_activated = True
    if is_juejue and "juejue-realtime" in move.tags and not turn["juejue_realtime"]:
        turn["juejue_realtime"] = True
        realtime_activated = True
    if is_juejue and "juejue-virtual-realm" in move.tags:
        player["juejue_guaranteed"] = True
    if is_juejue and "juejue-music" in move.tags:
        if turn["juejue_music"] and version >= 6:
            music_repeated = True
            turn.setdefault("juejue_music_repeat_ordinals", []).append(ordinal)
            special_extra_draws += 2
        elif not turn["juejue_music"]:
            turn["juejue_music"] = True
            music_activated = True
    if is_juejue and "juejue-switch-virtual" in move.tags:
        player["juejue_form"] = JUEJUE_FORM_VIRTUAL
    if is_juejue and "juejue-switch-sand" in move.tags:
        player["juejue_form"] = JUEJUE_FORM_TIME
        player["juejue_sand_domain_switch_units"] = 5
        player["juejue_acceleration_bonus"] = 5
        player["juejue_delay_bonus"] = 5
    if is_juejue and "juejue-sand-domain" in move.tags:
        player["juejue_sand_domain_steps"] = 0
        player["juejue_sand_domain_switch_units"] = 0

    # 达妮娅：布景招式同时累积下一次蚀域的出现权重与领域战胜利权重；
    # 蚀域被抽到时把同一份精确加成带入本回合领域战后再清空。
    if "daniya-staging" in effect_tags and not is_copy:
        player["daniya_domain_steps"] = int(player.get("daniya_domain_steps", 0)) + 3
    elif "daniya-staging" in effect_tags:
        suppressed_source_local_effects.append("daniya-domain-draw-weight")
    if "daniya-disillusion" in effect_tags:
        opponent_exhaust_bonus_units += 3
    if "daniya-timed-collapse" in effect_tags:
        turn["daniya_collapse_count"] = int(turn.get("daniya_collapse_count", 0)) + 1
    if "daniya-domain" in effect_tags and not is_copy:
        daniya_domain_carried_units = int(player.get("daniya_domain_steps", 0))
        turn["domain_clash_bonus_units"] = int(
            turn.get("domain_clash_bonus_units", 0)
        ) + daniya_domain_carried_units
        turn["daniya_domain_carried_units"] = int(
            turn.get("daniya_domain_carried_units", 0)
        ) + daniya_domain_carried_units
        player["daniya_domain_steps"] = 0
    if "daniya-flawless" in effect_tags:
        turn["domain_clash_bonus_units"] = int(turn.get("domain_clash_bonus_units", 0)) + 2
    if "daniya-loan" in effect_tags:
        turn["opponent_domain_clash_reduction_units"] = int(
            turn.get("opponent_domain_clash_reduction_units", 0)
        ) + 2
    if "daniya-world-disable-next" in effect_tags:
        opponent_next_effects_disabled = True
    if "daniya-world-force-next" in effect_tags:
        opponent_next_forced_form = str(form_before or DANIYA_FORM_STAGING)
        opponent_next_forced_move_ids = [
            candidate.move_id
            for candidate in fighter_form_moves("daniya", opponent_next_forced_form)
        ]
    if "daniya-world-work" in effect_tags:
        if form_before == DANIYA_FORM_DISILLUSION:
            opponent_exhaust_bonus_units += 20
        elif not is_copy:
            player["daniya_domain_steps"] = int(player.get("daniya_domain_steps", 0)) + 20
        else:
            suppressed_source_local_effects.append("daniya-world-work-domain-growth")
    if "daniya-world-damage-immunity" in effect_tags:
        turn["daniya_world_damage_immunity"] = True

    # 阿萨姆：喝奶茶永久养全盛，憋个大的只给临时权重；睡觉的+5
    # 从下一招开始叠加到本场所有后续招式。
    if "asamu-bathe" in effect_tags and not is_copy:
        player["asamu_tea_bonus_units"] = int(player.get("asamu_tea_bonus_units", 0)) + 5000
    elif "asamu-bathe" in effect_tags:
        suppressed_source_local_effects.append("asamu-milk-tea-draw-weight")
    if "asamu-milk-tea" in effect_tags and not is_copy:
        player["asamu_tea_bonus_units"] = 0
        player["asamu_prime_bonus_units"] = int(player.get("asamu_prime_bonus_units", 0)) + 1000
    elif "asamu-milk-tea" in effect_tags:
        suppressed_source_local_effects.append("asamu-prime-draw-weight")
    if "asamu-sleep" in effect_tags:
        player["asamu_future_gain_bonus"] = asamu_future_gain_before + 5
    if "asamu-charge-up" in effect_tags and not is_copy:
        player["asamu_prime_temp_bonus_units"] = int(
            player.get("asamu_prime_temp_bonus_units", 0)
        ) + 10000
    elif "asamu-charge-up" in effect_tags:
        suppressed_source_local_effects.append("asamu-prime-temporary-draw-weight")
    if "asamu-prime" in effect_tags and not is_copy:
        player["asamu_prime_temp_bonus_units"] = 0
    if "asamu-pressure-king" in effect_tags:
        turn.setdefault("asamu_pressure_ordinals", []).append(ordinal)
    if "asamu-misfortune-transfer" in effect_tags:
        turn["asamu_misfortune_count"] = int(turn.get("asamu_misfortune_count", 0)) + 1
    if "asamu-milk-dragon" in effect_tags and not forced:
        opponent_next_milk_dragons = 1
    if "asamu-tit-for-tat" in effect_tags:
        turn.setdefault("asamu_retaliation_ordinals", []).append(ordinal)

    # 虚拟模仿复制领域的数值和一般命中效果，但绝不重新进入领域战。
    # 阿萨姆领域的“再复制四招”属于可扩张递归，明确抑制；其他普通定向
    # 效果继续走本回合统一的跨方结算与保护规则。
    copied_domain = is_copy and "domain" in effect_tags
    if copied_domain:
        if effect_fighter_id == "gojo":
            opponent_next_debt += 1
            copied_domain_effect = "无量空处命中：对方下回合出招数-1"
        elif effect_fighter_id == "daniya":
            player["next_action_bonus"] += 1
            copied_domain_effect = "蚀域命中：自己下回合出招数+1"
        elif effect_fighter_id == "asamu":
            copied_domain_effect_suppressed = "阿萨姆领域追加两次复制被抑制"
        else:
            copied_domain_effect = "仅复制领域数值；不重新进入领域战"

    first_acceleration = turn.get("juejue_zero_first_acceleration")
    first_delay = turn.get("juejue_zero_first_delay")
    if (
        version < 6
        and is_juejue
        and subwheel is not None
        and subwheel["success"]
        and not turn["juejue_zero_checked"]
        and turn["juejue_acceleration_tier"] + turn["juejue_delay_tier"] >= 5
    ):
        wheel = ((True, 1), (False, 1))
        zero_success, zero_roll = choose(
            seed,
            f"{key}:relative-zero",
            wheel,
            version=version,
        )
        turn["juejue_zero_checked"] = True
        turn["juejue_zero_active"] = bool(zero_success)
        zero_bonus = Fraction(40 if zero_success else 0)
        relative_zero = {
            "checked": True,
            "acceleration_tier": turn["juejue_acceleration_tier"],
            "delay_tier": turn["juejue_delay_tier"],
            "wheel": wheel,
            "roll": zero_roll,
            "success": bool(zero_success),
            "gain": zero_bonus,
        }
    elif (
        version >= 6
        and is_juejue
        and not turn["juejue_zero_checked"]
        and first_acceleration
        and first_delay
    ):
        turn["juejue_zero_checked"] = True
        tier_sum = int(first_acceleration["tier"]) + int(first_delay["tier"])
        both_success = bool(first_acceleration["success"] and first_delay["success"])
        eligible = both_success and tier_sum >= 5
        if not both_success:
            reason = "首次加速或首次时延失败"
        elif tier_sum < 5:
            reason = "首次加速与首次时延档数和不足5"
        else:
            reason = "满足判定条件"
        zero_roll = None
        zero_success = False
        wheel: tuple = ()
        if eligible:
            wheel = ((True, 1), (False, 1))
            zero_success, zero_roll = choose(
                seed,
                f"{key}:relative-zero",
                wheel,
                version=version,
            )
        turn["juejue_zero_active"] = bool(zero_success)
        zero_bonus = Fraction(40 if zero_success else 0)
        relative_zero = {
            "checked": True,
            "eligible": eligible,
            "reason": reason,
            "first_acceleration": deepcopy(first_acceleration),
            "first_delay": deepcopy(first_delay),
            "acceleration_tier": int(first_acceleration["tier"]),
            "delay_tier": int(first_delay["tier"]),
            "tier_sum": tier_sum,
            "wheel": wheel,
            "roll": zero_roll,
            "success": bool(zero_success),
            "gain": zero_bonus,
        }

    if is_firefly and forced_gain_bonus and not firefly_sam_skill:
        special_base += Fraction(forced_gain_bonus)

    positive_numeric = special_base > 0
    signed_numeric = special_base != 0
    tool = snapshot.get("tool_id", "") if not player["tool_used"] else ""
    penalty = int(player["heavy"] and positive_numeric and tool != "bandage")
    multiplier_contract = bool(signed_numeric or opponent_reduction)
    multiplier = 2 if multiplier_contract and player["double"] else 1
    if positive_numeric:
        computed_numeric = max(
            Fraction(0),
            special_base + int(snapshot.get("level", 0)) + int(player.get("core", 0)) - penalty,
        ) * multiplier
    elif special_base < 0:
        computed_numeric = special_base * multiplier
    else:
        computed_numeric = Fraction(0)
    opponent_reduction *= multiplier
    black_flash_bonus = Fraction(
        0 if effects_disabled else int(player.get("black_flash_stacks", 0))
    )
    trait = int(positive_numeric and snapshot.get("trait_bonus", 0) and not turn["trait_used"])
    tool_gain = 2 if positive_numeric and tool == "wristband" else 0
    used_tool = bool(positive_numeric and (tool == "wristband" or (tool == "bandage" and player["heavy"])))
    directed_numeric = computed_numeric + trait + tool_gain
    own_numeric = directed_numeric if numeric_direction == "self" else Fraction(0)
    if numeric_direction == "opponent":
        opponent_reduction += directed_numeric
    asamu_future_gain = Fraction(0 if effects_disabled else asamu_future_gain_before)
    yilu_independent_units = len(yilu_sniper_shots) if yilu_sniper_shots else effect_repeats
    yilu_future_gain = Fraction(
        0 if effects_disabled else yilu_future_base_before * yilu_independent_units
    )
    gain = (
        own_numeric
        + music_gain
        + black_flash_bonus
        + zero_bonus
        + asamu_future_gain
        + yilu_future_gain
    )
    if multiplier_contract:
        player["double"] = False
    if positive_numeric:
        turn["trait_used"] = True
    if used_tool:
        player["tool_used"] = True
    player["weight"] += gain
    requested_extra_draws = effect_draws + special_extra_draws
    # 主动虚拟模仿复制来源追加抽数；领域结算型自动模仿与固定复制通过
    # allow_extra_draws=False 抑制追加抽数，避免结算后重新打开pending。
    copy_draws_suppressed = mimic is not None and requested_extra_draws > 0 and not allow_extra_draws
    extra_draws = requested_extra_draws if allow_extra_draws else 0
    turn["pending"] += extra_draws
    if effect_loan:
        player["double"] = True
        player["next_debt"] += 1
    if "black-flash" in effect_tags:
        player["black_flash_stacks"] += 1
    purple_weight_steps_before = player["purple_weight_steps"]
    purple_weight_steps_used = (
        purple_weight_steps_before if "purple" in effect_tags and not is_copy else 0
    )
    if "purple" in effect_tags and not is_copy:
        player["purple_weight_steps"] = 0
    elif "purple" in effect_tags:
        suppressed_source_local_effects.append("gojo-purple-draw-weight-reset")
    if "blue-red" in effect_tags and not is_copy:
        player["purple_weight_steps"] += 1
    elif "blue-red" in effect_tags:
        suppressed_source_local_effects.append("gojo-purple-draw-weight")
    if "infinity" in effect_tags:
        turn["infinity_used"] = True
    turn["done"] = turn["pending"] == 0
    if (
        snapshot.get("fighter_id") == "yilu"
        and int(turn.get("yilu_operator_placements", 0)) >= 10
    ):
        turn["pending"] = 0
        turn["done"] = True
        turn["yilu_force_end"] = True
    has_numeric_contribution = gain != 0
    if mimic is not None:
        mimic.update(
            functional_fighter_id=effect_fighter_id,
            functional_move_id=effect_move_id,
            functional_tags=sorted(effect_tags),
            requested_extra_draws=requested_extra_draws,
            extra_draws_suppressed=requested_extra_draws if copy_draws_suppressed else 0,
            domain_reentry_suppressed=copied_domain,
            copied_domain_effect=copied_domain_effect,
            copied_domain_effect_suppressed=copied_domain_effect_suppressed,
            suppressed_source_local_effects=list(suppressed_source_local_effects),
            effect_summary={
                "own_gain": gain,
                "opponent_reduction": opponent_reduction,
                "opponent_next_debt": opponent_next_debt,
                "opponent_next_bonus": opponent_next_bonus,
                "opponent_next_milk_dragons": opponent_next_milk_dragons,
                "opponent_exhaust_bonus_units": opponent_exhaust_bonus_units,
                "loan": effect_loan,
                "black_flash": "black-flash" in effect_tags,
                "infinity": "infinity" in effect_tags,
            },
        )
    return {
        "ordinal": ordinal,
        "move_id": drawn_move.move_id,
        "name": drawn_move.name,
        "base": drawn_move.gain,
        "base_tenths": drawn_move.resolved_gain_tenths,
        "special_base": special_base,
        "numeric_base": signed_numeric,
        "has_numeric_contribution": has_numeric_contribution,
        "numeric_direction": numeric_direction,
        "training": snapshot["level"] if positive_numeric else 0,
        "core": player["core"],
        "heavy": player["heavy"],
        "risk": player["risk"],
        "penalty": penalty if positive_numeric else 0,
        "multiplier": multiplier,
        "trait_gain": trait,
        "tool_gain": tool_gain,
        "black_flash_bonus": black_flash_bonus,
        "black_flash_stacks": player["black_flash_stacks"],
        "music_gain": music_gain,
        "zero_gain": zero_bonus,
        "asamu_big_gain": 0,
        "asamu_big_stacks_before": asamu_big_before,
        "asamu_big_stacks_after": int(player.get("asamu_big_stacks", 0)),
        "asamu_future_gain": asamu_future_gain,
        "asamu_future_gain_before": asamu_future_gain_before,
        "asamu_future_gain_after": int(player.get("asamu_future_gain_bonus", 0)),
        "asamu_tea_bonus_before": tea_bonus_before,
        "asamu_tea_bonus_after": int(player.get("asamu_tea_bonus_units", 0)),
        "asamu_sleep_bonus_before": sleep_bonus_before,
        "asamu_sleep_bonus_after": int(player.get("asamu_sleep_bonus_units", 0)),
        "asamu_prime_bonus_before": prime_bonus_before,
        "asamu_prime_bonus_after": int(player.get("asamu_prime_bonus_units", 0)),
        "asamu_prime_temp_bonus_after": int(player.get("asamu_prime_temp_bonus_units", 0)),
        "yilu_effect_repeats": effect_repeats,
        "yilu_future_gain": yilu_future_gain,
        "yilu_future_base_before": yilu_future_base_before,
        "yilu_future_base_after": int(player.get("yilu_future_base_bonus", 0))
        + int(turn.get("yilu_round_base_bonus", 0)),
        "yilu_markers": int(player.get("yilu_markers", 0)),
        "yilu_markers_total": int(player.get("yilu_markers_total", 0)),
        "yilu_marker_events": deepcopy(yilu_marker_events),
        "yilu_consumed_markers": yilu_consumed_markers,
        "yilu_threshold_draws": sum(int(item["threshold_draws"]) for item in yilu_marker_events),
        "yilu_sniper_shots": deepcopy(yilu_sniper_shots),
        "yilu_defender_checks": deepcopy(yilu_defender_checks),
        "yilu_true_damage_added": yilu_true_damage_added,
        "yilu_medic_recoveries": deepcopy(yilu_medic_recoveries),
        "yilu_specialist_draws_added": yilu_specialist_draws_added,
        "yilu_operator_placements": int(turn.get("yilu_operator_placements", 0)),
        "subwheel": subwheel,
        "relative_zero": relative_zero,
        "mimic": mimic,
        "opponent_reduction": opponent_reduction,
        "opponent_next_debt": opponent_next_debt,
        "opponent_next_bonus": opponent_next_bonus,
        "opponent_next_milk_dragons": opponent_next_milk_dragons,
        "opponent_exhaust_bonus_units": opponent_exhaust_bonus_units,
        "form_before": form_before,
        "form_after": (
            player.get("juejue_form", "")
            if is_juejue
            else player.get("daniya_form") or form_before or DANIYA_FORM_STAGING
            if is_daniya
            else player.get("firefly_form", FIREFLY_FORM_FIREFLY)
            if is_firefly
            else ""
        ),
        "daniya_domain_steps_before": daniya_domain_before,
        "daniya_domain_steps_after": int(player.get("daniya_domain_steps", 0)),
        "daniya_domain_carried_units": daniya_domain_carried_units,
        "daniya_world_damage_immunity": bool(turn.get("daniya_world_damage_immunity")),
        "opponent_next_effects_disabled": opponent_next_effects_disabled,
        "opponent_next_forced_move_ids": list(opponent_next_forced_move_ids),
        "opponent_next_forced_form": opponent_next_forced_form,
        "sculpt_bonus_before": sculpt_before,
        "sculpt_bonus_after": int(player.get("juejue_sculpt_bonus", 0)),
        "sand_domain_steps_before": sand_steps_before,
        "sand_domain_steps_after": int(player.get("juejue_sand_domain_steps", 0)),
        "sand_domain_switch_units_before": sand_switch_before,
        "sand_domain_switch_units_after": int(player.get("juejue_sand_domain_switch_units", 0)),
        "realization_stacks_before": realization_before,
        "realization_stacks_after": int(player.get("juejue_realization_stacks", 0)),
        "guaranteed_before": guaranteed_before,
        "guaranteed_after": bool(player.get("juejue_guaranteed", False)),
        "realtime_activated": realtime_activated,
        "future_simulation_activated": future_activated,
        "future_simulation_source_ordinal": ordinal if future_activated else None,
        "sand_body_activated": sand_body_activated,
        "rewind_active": bool(turn.get("juejue_rewind")),
        "rewind_count": int(turn.get("juejue_rewind_count", 0)),
        "rewind_debt_cleared": rewind_debt_cleared,
        "rewind_failure_ordinal": rewind_failure_ordinal,
        "rewind_pending_count": rewind_pending_count,
        "acceleration_failure_debt": acceleration_failure_debt,
        "acceleration_failure_rewound": acceleration_failure_rewound,
        "acceleration_rewind_source_ordinal": acceleration_rewind_source_ordinal,
        "music_activated": music_activated,
        "music_repeated": music_repeated,
        "tool_used": snapshot.get("tool_id", "") if used_tool else "",
        "gain": gain,
        "total": player["weight"],
        "extra_draws": extra_draws,
        "requested_extra_draws": requested_extra_draws,
        "extra_draws_suppressed": requested_extra_draws - extra_draws,
        "loan": effect_loan,
        "double_pending": player["double"],
        "next_debt": player["next_debt"],
        "next_action_bonus": player.get("next_action_bonus", 0),
        "domain_clash_bonus_units": int(turn.get("domain_clash_bonus_units", 0)),
        "opponent_domain_clash_reduction_units": int(
            turn.get("opponent_domain_clash_reduction_units", 0)
        ),
        "pending": turn["pending"],
        "purple_weight_steps_before": purple_weight_steps_before,
        "purple_weight_steps_used": purple_weight_steps_used,
        "purple_weight_steps": player["purple_weight_steps"],
        "tags": list(drawn_move.tags),
        "functional_tags": sorted(effect_tags),
        "forced": forced,
        "copy_context": is_copy,
        "functional_fighter_id": effect_fighter_id,
        "functional_move_id": effect_move_id,
        "domain_reentry_suppressed": copied_domain,
        "domain_eligible": not copied_domain and not effects_disabled,
        "effects_disabled": effects_disabled,
        "copied_domain_effect": copied_domain_effect,
        "copied_domain_effect_suppressed": copied_domain_effect_suppressed,
        "suppressed_source_local_effects": list(suppressed_source_local_effects),
        "firefly_echo": firefly_echo,
        "firefly_echo_scale": firefly_echo_scale,
        "firefly_fuel_before": firefly_fuel_before,
        "firefly_fuel_after": int(player.get("firefly_fuel", 0)),
        "firefly_sam_skill": firefly_sam_skill,
        "firefly_sam_skill_index_before": firefly_sam_skill_index_before,
        "firefly_entered_sam": firefly_entered_sam,
        "firefly_sam_rounds_remaining": int(player.get("firefly_sam_rounds_remaining", 0)),
        "firefly_next_sam_bonus_used": firefly_next_sam_bonus_used,
        "firefly_collapse_to_add": firefly_collapse_to_add,
        "firefly_conditional_reduction": firefly_conditional_reduction,
        "firefly_self_exhaust_delta_units": firefly_self_exhaust_delta_units,
        "firefly_choice": deepcopy(firefly_choice or firefly_choice_context),
        "firefly_domain_choice": firefly_domain_choice,
    }


def move_weight_units(
    player: dict,
    move: Move,
    *,
    functional_fighter_id: str | None = None,
) -> int:
    """Return exact ten-thousandths used by the deterministic move wheel."""

    units = int(move.resolved_draw_weight_units)
    fighter_id = functional_fighter_id or player.get("snapshot", {}).get("fighter_id")
    if "purple" in move.tags:
        units += int(player.get("purple_weight_steps", 0)) * (MOVE_WEIGHT_SCALE // 10)
    if fighter_id == "juejue" and "domain" in move.tags:
        # 两个领域在主招式盘的基础出现权重均为1；塑型、切盘和实时演算只叠加动态权重。
        if player.get("turn", {}).get("juejue_realtime"):
            units += MOVE_WEIGHT_SCALE
        if "juejue-sand-domain" in move.tags:
            units += int(player.get("juejue_sand_domain_steps", 0)) * (MOVE_WEIGHT_SCALE // 10)
            units += int(player.get("juejue_sand_domain_switch_units", 0)) * (MOVE_WEIGHT_SCALE // 10)
    if fighter_id == "daniya" and "daniya-domain" in move.tags:
        units += int(player.get("daniya_domain_steps", 0)) * (MOVE_WEIGHT_SCALE // 10)
    if fighter_id == "asamu":
        if "asamu-milk-tea" in move.tags:
            units += int(player.get("asamu_tea_bonus_units", 0))
        elif "asamu-prime" in move.tags:
            units += int(player.get("asamu_prime_bonus_units", 0))
            units += int(player.get("asamu_prime_temp_bonus_units", 0))
        elif "asamu-pressure-king" in move.tags:
            injury = player.get("injury_state", "none")
            units = 20000 if injury == "heavy" else 10000 if injury == "light" else 5000
        elif "asamu-tit-for-tat" in move.tags:
            injury = player.get("injury_state", "none")
            units = 7490 if injury == "light" else 9470 if injury == "heavy" else 4000
    if fighter_id == "firefly" and "sam-skill" in move.tags:
        units += int(player.get("firefly_fuel", 0)) * (MOVE_WEIGHT_SCALE // 10)
        units += int(player.get("turn", {}).get("firefly_sam_draw_bonus_units", 0))
        if player.get("firefly_form") == FIREFLY_FORM_FIREFLY:
            units -= MOVE_WEIGHT_SCALE // 10
    return max(1, units)


def _apply_firefly_event_context(state: dict, side: int, event: dict) -> None:
    """Apply opponent-aware Firefly/Sam facts without making command order observable."""

    player = state["sides"][side]
    if player.get("snapshot", {}).get("fighter_id") != "firefly":
        return
    if event.get("effects_disabled"):
        event.update(
            firefly_collapse_passive_gain=Fraction(0),
            firefly_target_collapse_before=int(state["sides"][1 - side].get("firefly_collapse", 0)),
            firefly_target_collapse_after_pending=int(
                state["sides"][1 - side].get("firefly_collapse", 0)
            ),
        )
        return
    target = state["sides"][1 - side]
    turn = player["turn"]
    collapse_before = int(target.get("firefly_collapse", 0)) + int(
        turn.get("firefly_outgoing_collapse", 0)
    )
    event["firefly_target_collapse_before"] = collapse_before
    extra_gain = Fraction(0)
    if event.get("firefly_sam_skill"):
        extra_gain += collapse_before * 4
        if event.get("move_id") == "sam-bottom-fire-slash":
            extra_gain += collapse_before * 4
            if int(event.get("firefly_sam_skill_index_before", 0)) == 0:
                event["opponent_reduction"] = Fraction(event.get("opponent_reduction", 0)) + 8
                event["firefly_first_sam_reduction"] = 8
        if event.get("move_id") == "sam-deathstar-overload" and collapse_before >= 2:
            event["opponent_next_debt"] = int(event.get("opponent_next_debt", 0)) + 1
            event["firefly_collapse_debt_triggered"] = True
    if event.get("firefly_echo") and event.get("move_id") == "firefly-dream-destination" and collapse_before >= 2:
        event["opponent_exhaust_bonus_units"] = Fraction(
            event.get("opponent_exhaust_bonus_units", 0)
        ) + 1
        event["firefly_echo_collapse_risk_triggered"] = True
    if event.get("move_id") == "firefly-falling-sky" and collapse_before >= 3:
        event["opponent_reduction"] = Fraction(event.get("opponent_reduction", 0)) + 15
        event["firefly_domain_collapse_reduction"] = 15
    if extra_gain:
        player["weight"] += extra_gain
        event["gain"] = Fraction(event.get("gain", 0)) + extra_gain
        event["has_numeric_contribution"] = True
    event["firefly_collapse_passive_gain"] = extra_gain
    added = int(event.get("firefly_collapse_to_add", 0))
    if added:
        turn["firefly_outgoing_collapse"] = int(turn.get("firefly_outgoing_collapse", 0)) + added
    event["firefly_target_collapse_after_pending"] = collapse_before + added
    event["total"] = player["weight"]


def play_chunk(state: dict, side: int, seed: str, *, chunk_size: int = MOVE_CHUNK_SIZE) -> list[dict]:
    player = _side(state, side)
    if player["turn"]["raw"] is None:
        raise BattleError("请先输入 /出招数。")
    if type(chunk_size) is not int or chunk_size < 1:
        raise BattleError("无效的连抽分片大小。")
    events = []
    for _ in range(chunk_size):
        if player["turn"]["done"]:
            break
        fighter_id = player["snapshot"]["fighter_id"]
        forced_daniya_ids = tuple(player["turn"].get("daniya_world_forced_move_ids", ()))
        forced_daniya_world = bool(forced_daniya_ids)
        forced_daniya_form = str(
            player["turn"].get("daniya_world_forced_form") or DANIYA_FORM_STAGING
        )
        forced_milk = not forced_daniya_world and int(
            player["turn"].get("forced_milk_dragon_count", 0)
        ) > int(
            player["turn"].get("forced_milk_dragon_used", 0)
        )
        forced_yilu_operator = (
            not forced_daniya_world
            and not forced_milk
            and int(player["turn"].get("yilu_double_operator_draws", 0)) > 0
        )
        forced_yilu_specialist = (
            not forced_daniya_world
            and not forced_milk
            and not forced_yilu_operator
            and int(player["turn"].get("yilu_specialist_operator_draws", 0)) > 0
        )
        firefly_choice = None
        if (
            not forced_daniya_world
            and not forced_milk
            and player["turn"].get("firefly_forced_choices")
        ):
            firefly_choice = player["turn"]["firefly_forced_choices"].pop(0)
        # 每次抽取都重新读取当前形态。切换招式增加的 pending 会在同一
        # play_chunk 内立刻从新轮盘抽取，不会继续使用分片开始时的旧盘。
        if forced_daniya_world:
            moves = tuple(
                move
                for move_id in forced_daniya_ids
                if (move := _move_by_id("daniya", str(move_id))) is not None
            )
            if not moves:
                raise BattleError("达妮娅·世界的冻结招式盘已经失效，不能重新抽取。")
        elif firefly_choice is not None:
            moves = FIREFLY_MOVES
        elif forced_yilu_operator:
            moves = tuple(move for move in YILU_MOVES if "yilu-operator" in move.tags)
        elif forced_yilu_specialist:
            moves = tuple(
                move
                for move in YILU_MOVES
                if "yilu-operator" in move.tags
                and "yilu-medic" not in move.tags
                and "yilu-specialist" not in move.tags
            )
        elif fighter_id == "juejue":
            moves = fighter_form_moves(fighter_id, player["juejue_form"])
        elif fighter_id == "daniya":
            moves = fighter_form_moves(fighter_id, player["daniya_form"])
        elif fighter_id == "firefly":
            moves = fighter_form_moves(fighter_id, player["firefly_form"])
        else:
            moves = FIGHTERS_BY_ID[fighter_id].moves
        ordinal = player["turn"]["draws"] + 1
        wheel = tuple(
            (
                index,
                move_weight_units(
                    player,
                    move,
                    functional_fighter_id="daniya" if forced_daniya_world else None,
                ),
            )
            for index, move in enumerate(moves)
        )
        if firefly_choice is None:
            index, roll = choose(
                seed,
                f"{state['round']}:{side}:move:{ordinal}",
                wheel,
                version=state["version"],
            )
        else:
            index = next(
                index
                for index, candidate in enumerate(moves)
                if candidate.move_id == firefly_choice["selected_move_id"]
            )
            selected_option = next(
                option
                for option in firefly_choice["options"]
                if int(option["slot"]) == int(firefly_choice["selected_slot"])
            )
            roll = int(selected_option["roll"])
        original_move = moves[index]
        selected_move = ASAMU_MOVES[7] if forced_milk else original_move
        if forced_milk:
            player["turn"]["forced_milk_dragon_used"] += 1
        if forced_yilu_operator:
            player["turn"]["yilu_double_operator_draws"] -= 1
        if forced_yilu_specialist:
            player["turn"]["yilu_specialist_operator_draws"] -= 1
        event = apply_move(
            player,
            selected_move,
            seed=seed,
            round_number=state["round"],
            side=side,
            version=state["version"],
            functional_fighter_id="daniya" if forced_daniya_world else None,
            functional_form_id=forced_daniya_form if forced_daniya_world else None,
            forced=forced_milk,
            effect_repeats=2 if forced_yilu_operator else 1,
            forced_gain_bonus=(
                int(firefly_choice.get("forced_gain_bonus", 0)) if firefly_choice is not None else 0
            ),
            firefly_echo_scale=(
                firefly_choice.get("echo_scale", 1) if firefly_choice is not None else 1
            ),
            firefly_choice_context=firefly_choice,
        )
        event.update(
            roll=roll,
            round=state["round"],
            side=side,
            fighter_id=fighter_id,
            draw_weight_scale=MOVE_WEIGHT_SCALE,
            draw_wheel_move_ids=[move.move_id for move in moves],
            draw_wheel_units=[weight for _index, weight in wheel],
            original_move_id=original_move.move_id if forced_milk else "",
            original_move_name=original_move.name if forced_milk else "",
            daniya_world_forced=forced_daniya_world,
            daniya_world_forced_form=forced_daniya_form if forced_daniya_world else "",
            yilu_babel_redeploy=forced_yilu_operator,
            yilu_specialist_redeploy=forced_yilu_specialist,
            firefly_forced_choice=firefly_choice is not None,
            firefly_choice_source_ordinal=(
                int(firefly_choice["source_ordinal"]) if firefly_choice is not None else None
            ),
        )
        _apply_firefly_event_context(state, side, event)
        player["turn"]["events"].append(deepcopy(event))
        events.append(event)
    return events


def apply_injury(player: dict, injury: str) -> None:
    if injury == "light":
        player["risk"] = max(player["risk"], 1)
        if not player.get("heavy"):
            player["injury_state"] = "light"
    elif injury == "heavy":
        player["heavy"], player["risk"] = True, 2
        player["injury_state"] = "heavy"
    elif injury == "core":
        player["heavy"] = False
        player["core"] += 1
        player["injury_state"] = "none"
    elif injury != "exhausted":
        raise BattleError("未知伤势结果。")


def _remaining_event_gain(cancelled: list[dict[int, dict]], side: int, event: dict) -> Fraction:
    entry = cancelled[side].get(int(event["ordinal"]))
    return Fraction(event.get("gain", 0)) - (Fraction(entry["gain"]) if entry else Fraction(0))


def _reduce_event(
    cancelled: list[dict[int, dict]], side: int, event: dict, amount: int | Fraction, reason: str
) -> Fraction:
    """Deduct at most the still-effective part of an event and retain every reason."""

    entry = cancelled[side].setdefault(
        int(event["ordinal"]), {"ordinal": int(event["ordinal"]), "gain": Fraction(0), "reasons": []}
    )
    remaining = _remaining_event_gain(cancelled, side, event)
    requested = max(Fraction(0), Fraction(amount))
    applied = min(requested, max(Fraction(0), remaining))
    entry["gain"] += applied
    if reason not in entry["reasons"]:
        entry["reasons"].append(reason)
    return applied


def _cancel_event(cancelled: list[dict[int, dict]], side: int, event: dict, reason: str) -> Fraction:
    """令该招式当前全部自方胜率贡献归零；功能与对方减益不回滚。"""

    entry = cancelled[side].setdefault(
        int(event["ordinal"]), {"ordinal": int(event["ordinal"]), "gain": Fraction(0), "reasons": []}
    )
    applied = _remaining_event_gain(cancelled, side, event)
    entry["gain"] += applied
    if reason not in entry["reasons"]:
        entry["reasons"].append(reason)
    return applied


def _domain_ids(events: list[dict]) -> list[str]:
    return [str(event.get("move_id", "")) for event in events]


def _event_fighter_id(state: dict, side: int, event: dict) -> str:
    return str(
        event.get("functional_fighter_id")
        or event.get("fighter_id")
        or state["sides"][side]["snapshot"].get("fighter_id", "")
    )


def _domain_fighter_ids(state: dict, side: int, events: list[dict]) -> list[str]:
    return [_event_fighter_id(state, side, event) for event in events]


def _domain_has(domain: dict, side: int, fighter_id: str, move_id: str) -> bool:
    return any(
        source_fighter == fighter_id and source_move == move_id
        for source_fighter, source_move in zip(
            domain.get("domain_fighter_ids", ((), ()))[side],
            domain.get("domain_ids", ((), ()))[side],
            strict=False,
        )
    )


def _domain_strength(state: dict, side: int, events: list[dict]) -> tuple[int, bool]:
    fighter_ids = set(_domain_fighter_ids(state, side, events))
    fighter_id = next(iter(fighter_ids)) if len(fighter_ids) == 1 else ""
    distinct_juejue = {
        event.get("move_id")
        for event in events
        if event.get("move_id") in {"sand-domain", "chaos-domain"}
    }
    dual_juejue = fighter_id == "juejue" and len(distinct_juejue) == 2
    if dual_juejue:
        return 55, True
    if fighter_id == "juejue":
        return 25, False
    if fighter_id == "sukuna":
        return 40, False
    return 30, False


def _domain_resolution(state: dict, seed: str, cancelled: list[dict[int, dict]]) -> dict | None:
    domains = [
        [
            event
            for event in side["turn"].get("events", ())
            if "domain" in event.get("tags", ())
            and not event.get("domain_reentry_suppressed")
            and event.get("domain_eligible", True)
        ]
        for side in state["sides"]
    ]
    active = [index for index, events in enumerate(domains) if events]
    if not active:
        return None
    version = state["version"]
    strengths = [0, 0]
    dual_juejue = [False, False]
    for side in active:
        strengths[side], dual_juejue[side] = _domain_strength(state, side, domains[side])
    if len(active) == 2:
        # v7改用十分整数：普通30、宿傩40、撅撅单领域25/双领域55、
        # 平手30。达妮娅的±0.2直接在同一精确刻度叠加。
        base_strengths = strengths.copy()
        for side in (0, 1):
            own_bonus = int(state["sides"][side]["turn"].get("domain_clash_bonus_units", 0))
            opponent_reduction = int(
                state["sides"][1 - side]["turn"].get(
                    "opponent_domain_clash_reduction_units", 0
                )
            )
            strengths[side] = max(1, strengths[side] + own_bonus - opponent_reduction)
        wheel = (("side-0", strengths[0]), ("side-1", strengths[1]), ("tie", 30))
        outcome, roll = choose(seed, f"{state['round']}:domain:clash", wheel, version=version)
        if outcome == "tie":
            losing = (0, 1)
            winner = None
        else:
            winner = int(outcome[-1])
            losing = (1 - winner,)
        for side in losing:
            for event in domains[side]:
                _cancel_event(cancelled, side, event, "领域战落败" if winner is not None else "领域战平手")
        hit_side = winner
        mode = "clash"
    else:
        domain_side = active[0]
        wheel = (("hit", 8), ("simple-domain", 2))
        outcome, roll = choose(
            seed,
            f"{state['round']}:domain:solo:{domain_side}",
            wheel,
            version=version,
        )
        hit_side = domain_side if outcome == "hit" else None
        if hit_side is None:
            for event in domains[domain_side]:
                _cancel_event(cancelled, domain_side, event, "简易领域免疫")
        winner = hit_side
        mode = "solo"
    return {
        "mode": mode,
        "wheel": wheel,
        "weight_scale": 10 if mode == "clash" else 1,
        "base_strengths": base_strengths if mode == "clash" else strengths.copy(),
        "strengths": strengths,
        "outcome": outcome,
        "roll": roll,
        "winner": winner,
        "hit_side": hit_side,
        "domain_counts": [len(events) for events in domains],
        "domain_ids": [_domain_ids(events) for events in domains],
        "domain_fighter_ids": [
            _domain_fighter_ids(state, side, events)
            for side, events in enumerate(domains)
        ],
        "dual_juejue": dual_juejue,
        "boost_side": None,
        "boosted_ordinal": None,
        "boosted_ordinals": [],
        "bonus_gain": 0,
        "boost_reason": "",
        "effects": [],
        "effect": "",
        "auto_mimic": None,
        "nullified_side": None,
        "cross_debuff_suppressed": False,
    }


def _available_moves(player: dict) -> tuple[Move, ...]:
    fighter_id = player["snapshot"]["fighter_id"]
    if fighter_id == "juejue":
        return fighter_form_moves(fighter_id, player["juejue_form"])
    if fighter_id == "daniya":
        return fighter_form_moves(fighter_id, player["daniya_form"])
    if fighter_id == "firefly":
        return fighter_form_moves(fighter_id, player["firefly_form"])
    return FIGHTERS_BY_ID[fighter_id].moves


def _asamu_domain_copies(state: dict, seed: str, domain: dict | None) -> tuple[dict, ...]:
    """领域战胜利或单方命中后复制两个招式；复制领域不会再次开启领域战。"""

    if not domain or domain.get("hit_side") not in (0, 1):
        return ()
    side = int(domain["hit_side"])
    player = state["sides"][side]
    if not _domain_has(domain, side, "asamu", "asamu-domain"):
        return ()
    opponent_side = 1 - side
    opponent = state["sides"][opponent_side]
    source_moves = _available_moves(opponent)
    wheel = tuple((index, move_weight_units(opponent, move)) for index, move in enumerate(source_moves))
    copies: list[dict] = []
    for slot in range(1, 3):
        index, roll = choose(
            seed,
            f"{state['round']}:domain:asamu:{side}:copy:{slot}:source",
            wheel,
            version=state["version"],
        )
        source_move = source_moves[index]
        event = apply_move(
            player,
            source_move,
            seed=seed,
            round_number=state["round"],
            side=side,
            version=state["version"],
            consume_pending=False,
            allow_extra_draws=False,
            functional_fighter_id=opponent["snapshot"]["fighter_id"],
            functional_form_id=(
                opponent.get("daniya_form")
                if opponent["snapshot"].get("fighter_id") == "daniya"
                else None
            ),
            copy_context=True,
        )
        event.update(
            roll=roll,
            round=state["round"],
            side=side,
            fighter_id="asamu",
            draw_weight_scale=MOVE_WEIGHT_SCALE,
            draw_wheel_move_ids=[move.move_id for move in source_moves],
            draw_wheel_units=[weight for _index, weight in wheel],
            generated_by="asamu-domain-copy",
            copy_slot=slot,
            source_side=opponent_side,
            source_fighter_id=opponent["snapshot"]["fighter_id"],
            source_move_id=source_move.move_id,
            source_move_name=source_move.name,
            domain_reentry_suppressed="domain" in source_move.tags,
            domain_eligible=False,
        )
        player["turn"]["events"].append(deepcopy(event))
        copies.append(event)
    return tuple(copies)


def _juejue_domain_auto_mimic(state: dict, seed: str, domain: dict | None) -> dict | None:
    """乱序数虚时空固定模仿一次；与主动模仿共用完整功能及安全边界。"""

    if state.get("version", 0) < 6 or not domain or domain.get("hit_side") not in (0, 1):
        return None
    side = int(domain["hit_side"])
    player = state["sides"][side]
    if not _domain_has(domain, side, "juejue", "chaos-domain"):
        return None
    mimic_move = _move_by_id("juejue", "virtual-mimic")
    if mimic_move is None:  # pragma: no cover - 目录定义与引擎同时发布
        raise BattleError("乱序数虚时空缺少虚拟模仿定义。")
    mimic = _juejue_mimic(
        player,
        seed,
        f"{state['round']}:{side}:domain:auto-mimic",
        state["version"],
    )
    event = apply_move(
        player,
        mimic_move,
        seed=seed,
        round_number=state["round"],
        side=side,
        version=state["version"],
        consume_pending=False,
        allow_extra_draws=False,
        mimic_override=mimic,
    )
    event.update(
        roll=mimic.get("source_roll"),
        round=state["round"],
        side=side,
        fighter_id="juejue",
        draw_weight_scale=MOVE_WEIGHT_SCALE,
        draw_wheel_move_ids=["virtual-mimic"],
        draw_wheel_units=[MOVE_WEIGHT_SCALE],
        generated_by="chaos-domain-auto-mimic",
        source_side=side,
        source_fighter_id=mimic.get("source_fighter_id", ""),
        source_move_id=mimic.get("source_move_id", ""),
        source_move_name=mimic.get("source_name", ""),
    )
    player["turn"]["events"].append(deepcopy(event))
    return event


def _settle_interactions(state: dict, seed: str) -> dict:
    cancelled: list[dict[int, dict]] = [{}, {}]
    domain = _domain_resolution(state, seed, cancelled)
    version = state["version"]
    round_number = state["round"]
    daniya_damage_immunity_sides = {
        side
        for side, player in enumerate(state["sides"])
        if player["turn"].get("daniya_world_damage_immunity")
    }

    # 达妮娅在领域战获胜或单方8:2命中时都进入幻灭。
    daniya_transition = None
    if domain and domain.get("hit_side") in (0, 1):
        winner = int(domain["hit_side"])
        winner_side = state["sides"][winner]
        if _domain_has(domain, winner, "daniya", "daniya-domain"):
            before_form = winner_side.get("daniya_form", DANIYA_FORM_STAGING)
            winner_side["daniya_form"] = DANIYA_FORM_DISILLUSION
            winner_side["next_action_bonus"] += 1
            daniya_transition = {
                "side": winner,
                "before": before_form,
                "after": DANIYA_FORM_DISILLUSION,
                "next_action_bonus": 1,
            }

    asamu_domain_copies = _asamu_domain_copies(state, seed, domain)
    chaos_auto_mimic_event = _juejue_domain_auto_mimic(state, seed, domain)

    # 无下限先抵消对方第一招仍有效、具有基础数值的招式；不会回滚功能。
    for defender in (0, 1):
        if not state["sides"][defender]["turn"].get("infinity_used"):
            continue
        attacker = 1 - defender
        for event in state["sides"][attacker]["turn"].get("events", ()):
            # 贷款即使受到黑闪等全局光环而显示少量数值，仍是功能招式；
            # 无下限按规则等待下一招真正的数值招式。
            if event.get("loan"):
                continue
            if not event.get("has_numeric_contribution") or _remaining_event_gain(cancelled, attacker, event) == 0:
                continue
            _cancel_event(cancelled, attacker, event, "无下限·防御")
            break

    # 每次未来模拟都独立随机一招归零；沙之形体仍每方每轮最多结算一次。
    # 两者都只改变数值贡献，保留再抽、贷款、切换形态等功能事实。
    future_simulations = []
    sand_bodies = []
    for defender in (0, 1):
        attacker = 1 - defender
        future_turn = state["sides"][defender]["turn"]
        source_ordinals = list(future_turn.get("juejue_future_simulation_ordinals", ()))
        if not source_ordinals and future_turn.get("juejue_future_simulation"):
            # 兼容已在进行、只有旧布尔字段的现场。
            source_ordinals = [None]
        if version < 6:
            source_ordinals = source_ordinals[:1]
        if not source_ordinals:
            future_simulations.append(
                {
                    "side": defender,
                    "active": False,
                    "source_ordinal": None,
                    "chance_ordinal": None,
                    "target_side": attacker,
                    "candidate_ordinals": [],
                    "selected_ordinal": None,
                    "roll": None,
                    "cancelled_gain": 0,
                }
            )
        for chance_ordinal, source_ordinal in enumerate(source_ordinals, start=1):
            future = {
                "side": defender,
                "active": True,
                "source_ordinal": source_ordinal,
                "chance_ordinal": chance_ordinal,
                "target_side": attacker,
                "candidate_ordinals": [],
                "selected_ordinal": None,
                "roll": None,
                "cancelled_gain": 0,
            }
            candidates = [
                event
                for event in state["sides"][attacker]["turn"].get("events", ())
                if event.get("has_numeric_contribution")
                and _remaining_event_gain(cancelled, attacker, event) != 0
            ]
            future["candidate_ordinals"] = [int(event["ordinal"]) for event in candidates]
            if candidates:
                wheel = tuple((int(event["ordinal"]), 1) for event in candidates)
                namespace = (
                    f"{round_number}:juejue:future:{defender}:target"
                    if source_ordinal is None
                    else f"{round_number}:juejue:future:{defender}:{source_ordinal}:target"
                )
                selected, roll = choose(seed, namespace, wheel, version=version)
                event = next(item for item in candidates if int(item["ordinal"]) == selected)
                future.update(selected_ordinal=selected, roll=roll)
                future["cancelled_gain"] = _cancel_event(
                    cancelled,
                    attacker,
                    event,
                    f"虚拟声·未来模拟#{chance_ordinal}",
                )
            future_simulations.append(future)

        sand = {
            "side": defender,
            "active": bool(state["sides"][defender]["turn"].get("juejue_sand_body")),
            "target_side": attacker,
            "selected_ordinal": None,
            "original_gain": 0,
            "remaining_gain": 0,
            "cancelled_gain": 0,
        }
        if sand["active"]:
            for event in state["sides"][attacker]["turn"].get("events", ()):
                remaining = _remaining_event_gain(cancelled, attacker, event)
                if not event.get("has_numeric_contribution") or remaining <= 0:
                    continue
                deduction = remaining - remaining // 2
                applied = _reduce_event(cancelled, attacker, event, deduction, "时之沙·沙之形体")
                sand.update(
                    selected_ordinal=int(event["ordinal"]),
                    original_gain=remaining,
                    remaining_gain=remaining - applied,
                    cancelled_gain=applied,
                )
                break
        sand_bodies.append(sand)

    # 传奇耐压王每层、每个对方数值招式都独立做33%判定；即使别的层已
    # 清零该招，也保留全部roll作为可审计事实，但不会重复扣数值。
    pressure_checks = []
    for defender in (0, 1):
        attacker = 1 - defender
        sources = tuple(state["sides"][defender]["turn"].get("asamu_pressure_ordinals", ()))
        candidates = [
            event
            for event in state["sides"][attacker]["turn"].get("events", ())
            if event.get("has_numeric_contribution") and Fraction(event.get("gain", 0)) != 0
        ]
        for source_ordinal in sources:
            for event in candidates:
                hit, roll = choose(
                    seed,
                    f"{round_number}:asamu:pressure:{defender}:{source_ordinal}:{attacker}:{event['ordinal']}",
                    ((True, 33), (False, 67)),
                    version=version,
                )
                cancelled_gain = (
                    _cancel_event(cancelled, attacker, event, "传奇耐压王") if hit else Fraction(0)
                )
                pressure_checks.append(
                    {
                        "side": defender,
                        "source_ordinal": int(source_ordinal),
                        "target_side": attacker,
                        "target_ordinal": int(event["ordinal"]),
                        "roll": roll,
                        "hit": bool(hit),
                        "cancelled_gain": cancelled_gain,
                    }
                )

    # 重装的70%判定在双方招式齐备后再选择目标；成功只清空该招式的
    # 胜率数值，功能保留，并额外向对方申请-5回合减权。
    yilu_defender_results = []
    yilu_defender_reductions = [Fraction(0), Fraction(0)]
    for defender in (0, 1):
        attacker = 1 - defender
        for chance_index, chance in enumerate(
            state["sides"][defender]["turn"].get("yilu_defender_chances", ()),
            start=1,
        ):
            record = {
                **deepcopy(chance),
                "side": defender,
                "target_side": attacker,
                "candidate_ordinals": [],
                "selected_ordinal": None,
                "target_roll": None,
                "cancelled_gain": Fraction(0),
                "opponent_reduction": Fraction(0),
                "suppressed_by_daniya_nmsl": False,
            }
            candidates = [
                event
                for event in state["sides"][attacker]["turn"].get("events", ())
                if _remaining_event_gain(cancelled, attacker, event) > 0
            ]
            record["candidate_ordinals"] = [int(event["ordinal"]) for event in candidates]
            if chance.get("hit") and candidates and attacker not in daniya_damage_immunity_sides:
                wheel = tuple((int(event["ordinal"]), 1) for event in candidates)
                selected, target_roll = choose(
                    seed,
                    f"{round_number}:yilu:defender:{defender}:{chance_index}:target",
                    wheel,
                    version=version,
                )
                event = next(item for item in candidates if int(item["ordinal"]) == selected)
                record["selected_ordinal"] = selected
                record["target_roll"] = target_roll
                record["cancelled_gain"] = _cancel_event(
                    cancelled, attacker, event, "干员放置·重装"
                )
                record["opponent_reduction"] = Fraction(5)
                yilu_defender_reductions[attacker] += 5
            elif chance.get("hit") and attacker in daniya_damage_immunity_sides:
                record["suppressed_by_daniya_nmsl"] = True
            yilu_defender_results.append(record)

    dual_winner = None
    if domain is not None and domain["mode"] == "clash" and domain.get("winner") in (0, 1):
        candidate = int(domain["winner"])
        if domain["dual_juejue"][candidate]:
            dual_winner = candidate

    # 相对静止·零与双领域胜出的特殊效果都只清除本轮数值，不倒流抽数、
    # 形态或其他已发生的功能；但会阻止本轮对撅撅猪施加跨回合负面效果。
    zeroes = []
    protected_juejue_sides: set[int] = set()
    for defender in (0, 1):
        relative = bool(state["sides"][defender]["turn"].get("juejue_zero_active"))
        dual = defender == dual_winner
        attacker = 1 - defender
        record = {
            "side": defender,
            "active": relative or dual,
            "relative_zero": relative,
            "dual_domain": dual,
            "target_side": attacker,
            "cancelled_ordinals": [],
            "cancelled_gain": 0,
            "cross_debuff_suppressed": relative or dual,
        }
        if record["active"]:
            protected_juejue_sides.add(defender)
            reason = "相对静止·零" if relative and not dual else (
                "双领域·时空静止" if dual and not relative else "相对静止·零与双领域"
            )
            for event in state["sides"][attacker]["turn"].get("events", ()):
                before_remaining = _remaining_event_gain(cancelled, attacker, event)
                if before_remaining == 0:
                    continue
                applied = _cancel_event(cancelled, attacker, event, reason)
                if applied:
                    record["cancelled_ordinals"].append(int(event["ordinal"]))
                    record["cancelled_gain"] += applied
        zeroes.append(record)

    adjustments = []
    for side, entries in enumerate(cancelled):
        deduction = sum(entry["gain"] for entry in entries.values())
        state["sides"][side]["weight"] -= deduction
        adjustments.append(tuple(entries[key] for key in sorted(entries)))

    # 领域战胜方通常只翻倍第一份仍有效领域。Battle v10先为五条猪补齐
    # 单方命中翻倍；Battle v11起统一为所有战斗猪在8:2单方领域命中时
    # 都执行同一份翻倍。简易领域成功时仍归零。撅撅猪两个不同领域仅在
    # 真实clash中获胜时改为两份相加后整体翻倍；同领域重复不产生双领域特效。
    if domain is not None:
        winner = domain.get("winner")
        boost_side = None
        boost_reason = ""
        if domain.get("mode") == "clash" and winner in (0, 1):
            boost_side = int(winner)
            boost_reason = "领域战获胜"
        elif (
            version >= 10
            and domain.get("mode") == "solo"
            and domain.get("hit_side") in (0, 1)
        ):
            candidate = int(domain["hit_side"])
            is_v10_gojo = _domain_has(domain, candidate, "gojo", "void")
            if version >= 11 or is_v10_gojo:
                boost_side = candidate
                boost_reason = "领域命中"
        if boost_side is not None:
            effective_domains = [
                event
                for event in state["sides"][boost_side]["turn"].get("events", ())
                if "domain" in event.get("tags", ())
                and not event.get("domain_reentry_suppressed")
                and event.get("domain_eligible", True)
                and _remaining_event_gain(cancelled, boost_side, event) > 0
                and (
                    domain.get("mode") == "clash"
                    or version >= 11
                    or event.get("move_id") == "void"
                )
            ]
            if domain.get("mode") == "clash" and domain["dual_juejue"][boost_side]:
                by_id = {}
                for event in effective_domains:
                    if event.get("move_id") in {"sand-domain", "chaos-domain"}:
                        by_id.setdefault(event["move_id"], event)
                boosted = list(by_id.values())
            else:
                boosted = effective_domains[:1]
            if boosted:
                bonus = sum(
                    _remaining_event_gain(cancelled, boost_side, event) for event in boosted
                )
                ordinals = [int(event["ordinal"]) for event in boosted]
                state["sides"][boost_side]["weight"] += bonus
                domain.update(
                    boost_side=boost_side,
                    boosted_ordinal=ordinals[-1],
                    boosted_ordinals=ordinals,
                    bonus_gain=bonus,
                    boost_reason=boost_reason,
                )

    domain_effects: list[str] = []
    auto_mimic = None
    extra_round_reduction = list(yilu_defender_reductions)
    if domain is not None and domain.get("hit_side") in (0, 1):
        hit_side = int(domain["hit_side"])
        target = 1 - hit_side
        hit_fighter_ids = set(domain["domain_fighter_ids"][hit_side])
        if "gojo" in hit_fighter_ids:
            if target not in protected_juejue_sides:
                state["sides"][target]["next_debt"] += 1
                domain_effects.append("无量空处命中：对方下回合出招数-1")
            else:
                domain["cross_debuff_suppressed"] = True
        if _domain_has(domain, hit_side, "juejue", "sand-domain"):
            state["sides"][hit_side]["next_action_bonus"] += 1
            if target not in protected_juejue_sides:
                state["sides"][target]["next_debt"] += 1
                domain_effects.append("荒时之沙命中：自己下回合+1招，对方下回合-1招")
            else:
                domain["cross_debuff_suppressed"] = True
                domain_effects.append("荒时之沙命中：自己下回合+1招")
        if _domain_has(domain, hit_side, "juejue", "chaos-domain"):
            player = state["sides"][hit_side]
            player["next_action_bonus"] += 1
            player["juejue_guaranteed"] = True
            if version < 6:
                mimic = _juejue_mimic(
                    player,
                    seed,
                    f"{round_number}:{hit_side}:domain:auto-mimic",
                    version,
                )
                base = abs(int(mimic["base"]))
                numeric = max(
                    0,
                    base
                    + int(player["snapshot"].get("level", 0))
                    + int(player.get("core", 0))
                    - int(bool(player.get("heavy"))),
                )
                legacy_music_gain = 5 if player["turn"].get("juejue_music") else 0
                direction = mimic.get("direction", "self")
                raw_gain = (
                    numeric + legacy_music_gain
                    if mimic["available"] and direction == "self"
                    else 0
                )
                raw_reduction = (
                    numeric + legacy_music_gain
                    if mimic["available"] and direction == "opponent"
                    else 0
                )
                generated = None
            else:
                generated = chaos_auto_mimic_event
                mimic = (
                    deepcopy(generated.get("mimic") or {})
                    if generated
                    else {"available": False}
                )
                raw_gain = Fraction(generated.get("gain", 0)) if generated else Fraction(0)
                raw_reduction = (
                    Fraction(generated.get("opponent_reduction", 0))
                    if generated
                    else Fraction(0)
                )
                legacy_music_gain = 0
            numeric_suppressed = bool(mimic.get("available") and target in protected_juejue_sides)
            suppressed_reason = ""
            if numeric_suppressed:
                target_guard = zeroes[target]
                suppressed_reason = (
                    "相对静止·零"
                    if target_guard["relative_zero"] and not target_guard["dual_domain"]
                    else (
                        "双领域·时空静止"
                        if target_guard["dual_domain"] and not target_guard["relative_zero"]
                        else "相对静止·零与双领域"
                    )
                )
            if version < 6:
                applied_gain = Fraction(0) if numeric_suppressed else Fraction(raw_gain)
                if applied_gain:
                    player["weight"] += applied_gain
            else:
                applied_gain = (
                    _remaining_event_gain(cancelled, hit_side, generated)
                    if generated is not None
                    else Fraction(0)
                )
            applied_reduction = Fraction(0) if numeric_suppressed else raw_reduction
            if version < 6 and applied_reduction:
                extra_round_reduction[target] += applied_reduction
            auto_mimic = {
                **mimic,
                "event_ordinal": generated.get("ordinal") if generated else None,
                "generated_by": "chaos-domain-auto-mimic" if generated else "",
                "training": (
                    generated.get("training", 0)
                    if generated
                    else int(player["snapshot"].get("level", 0)) if mimic.get("available") else 0
                ),
                "core": (
                    generated.get("core", 0)
                    if generated
                    else int(player.get("core", 0)) if mimic.get("available") else 0
                ),
                "heavy_penalty": (
                    generated.get("penalty", 0)
                    if generated
                    else int(bool(player.get("heavy"))) if mimic.get("available") else 0
                ),
                "music_gain": (
                    generated.get("music_gain", 0) if generated else legacy_music_gain
                ),
                "raw_gain": raw_gain,
                "raw_opponent_reduction": raw_reduction,
                "gain": applied_gain,
                "opponent_reduction": applied_reduction,
                "numeric_suppressed": numeric_suppressed,
                "suppressed_reason": suppressed_reason,
                "effect_summary": mimic.get("effect_summary", {}),
            }
            domain_effects.append("乱序数虚时空命中：自动模仿、自己下回合+1招并保证下一次加速或时延成功")
            if numeric_suppressed:
                domain_effects.append(f"自动模仿数值被{suppressed_reason}清零，领域功能仍生效")

        if _domain_has(domain, hit_side, "yilu", "yilu-domain"):
            player = state["sides"][hit_side]
            player["next_action_bonus"] += 1
            player["yilu_next_round_base_bonus"] = int(
                player.get("yilu_next_round_base_bonus", 0)
            ) + 1
            trigger = "领域战获胜" if domain.get("mode") == "clash" else "领域命中"
            domain_effects.append(
                f"末日方舟{trigger}：获得明日，下回合+1招且该回合所有招式基础胜率+1"
            )
        if _domain_has(domain, hit_side, "firefly", "firefly-falling-sky"):
            player = state["sides"][hit_side]
            player["weight"] += 12
            player["turn"]["firefly_self_exhaust_delta_units"] = Fraction(
                player["turn"].get("firefly_self_exhaust_delta_units", 0)
            )
            target_turn = state["sides"][target]["turn"]
            target_turn["firefly_self_exhaust_delta_units"] = Fraction(
                target_turn.get("firefly_self_exhaust_delta_units", 0)
            ) + Fraction(3, 2)
            trigger = "领域战获胜" if domain.get("mode") == "clash" else "领域命中"
            domain_effects.append(
                f"自破碎的天空坠落{trigger}：追加Δ指令-焦土陨击，自己胜率+12、对手本回合力竭权重+0.15"
            )

    if domain is not None:
        domain["effects"] = domain_effects
        domain["effect"] = "；".join(domain_effects)
        domain["auto_mimic"] = auto_mimic
        domain["nullified_side"] = 1 - dual_winner if dual_winner in (0, 1) else None

    # 时延的功能结算在所有“招式数值归零”之后执行。普通抵消不回滚功能；
    # 相对零/双领域则同时保护撅撅猪免受本轮减权与跨回合欠招。
    requested_reductions = [Fraction(value) for value in extra_round_reduction]
    reduction_sources: list[list[int]] = [[], []]
    cross_effects = []
    for attacker in (0, 1):
        target = 1 - attacker
        for event in state["sides"][attacker]["turn"].get("events", ()):
            original_requested = Fraction(event.get("opponent_reduction", 0))
            conditional_reduction = int(event.get("firefly_conditional_reduction", 0))
            target_has_round_gain = Fraction(state["sides"][target]["weight"]) > Fraction(
                state["sides"][target].get("round_start_weight", 5)
            )
            if conditional_reduction and target_has_round_gain:
                original_requested += conditional_reduction
                event["firefly_conditional_reduction_applied"] = conditional_reduction
            debt = int(event.get("opponent_next_debt", 0))
            bonus = int(event.get("opponent_next_bonus", 0))
            milk_dragons = int(event.get("opponent_next_milk_dragons", 0))
            exhaust_units = int(event.get("opponent_exhaust_bonus_units", 0))
            disable_next = bool(event.get("opponent_next_effects_disabled"))
            forced_move_ids = list(event.get("opponent_next_forced_move_ids", ()))
            forced_form = str(event.get("opponent_next_forced_form", ""))
            reduction_suppressed = bool(
                original_requested
                and (
                    target in protected_juejue_sides
                    or target in daniya_damage_immunity_sides
                )
            )
            reduction_suppression_reason = (
                "丸山大姐达妮娅-世界·NMSL"
                if original_requested and target in daniya_damage_immunity_sides
                else "相对静止保护"
                if original_requested and target in protected_juejue_sides
                else ""
            )
            requested = 0 if reduction_suppressed else original_requested
            if requested:
                requested_reductions[target] += requested
                reduction_sources[target].append(int(event["ordinal"]))
            debt_suppressed = bool(debt and target in protected_juejue_sides)
            if debt and not debt_suppressed:
                state["sides"][target]["next_debt"] += debt
            if bonus:
                state["sides"][target]["next_action_bonus"] += bonus
            directed_suppressed = target in protected_juejue_sides
            if milk_dragons and not directed_suppressed:
                state["sides"][target]["asamu_milk_dragon_next_count"] += milk_dragons
            if exhaust_units and not directed_suppressed:
                state["sides"][target]["injury_exhaust_bonus_units"] += exhaust_units
            if disable_next and not directed_suppressed:
                state["sides"][target]["daniya_world_disable_next"] = True
            if forced_move_ids and not directed_suppressed:
                state["sides"][target]["daniya_world_forced_move_ids_next"] = forced_move_ids
                state["sides"][target]["daniya_world_forced_form_next"] = (
                    forced_form or DANIYA_FORM_STAGING
                )
            if (
                original_requested
                or debt
                or bonus
                or milk_dragons
                or exhaust_units
                or disable_next
                or forced_move_ids
            ):
                cross_effects.append(
                    {
                        "source_side": attacker,
                        "source_ordinal": int(event["ordinal"]),
                        "target_side": target,
                        "round_reduction": requested,
                        "round_reduction_suppressed": reduction_suppressed,
                        "round_reduction_suppression_reason": reduction_suppression_reason,
                        "next_debt": 0 if debt_suppressed else debt,
                        "next_bonus": bonus,
                        "next_milk_dragons": 0 if directed_suppressed else milk_dragons,
                        "exhaust_bonus_units": 0 if directed_suppressed else exhaust_units,
                        "next_effects_disabled": disable_next and not directed_suppressed,
                        "next_forced_move_ids": [] if directed_suppressed else forced_move_ids,
                        "next_forced_form": "" if directed_suppressed else forced_form,
                        "debt_suppressed": debt_suppressed,
                        "directed_effect_suppressed": directed_suppressed
                        and bool(milk_dragons or exhaust_units or disable_next or forced_move_ids),
                    }
                )

    round_reductions = []
    for target in (0, 1):
        floor = Fraction(state["sides"][target].get("round_start_weight", 5))
        available = max(Fraction(0), Fraction(state["sides"][target]["weight"]) - floor)
        applied = min(Fraction(requested_reductions[target]), available)
        state["sides"][target]["weight"] -= applied
        round_reductions.append(
            {
                "side": target,
                "requested": requested_reductions[target],
                "applied": applied,
                "floor": floor,
                "source_ordinals": reduction_sources[target],
            }
        )

    # 以牙还牙在全部常规数值结算后使用同一份快照判定，避免双方指令顺序
    # 改变结果。低权重方只交换一次；每个以牙还牙事件仍分别提供后置奖励。
    retaliation_counts = [
        len(side["turn"].get("asamu_retaliation_ordinals", ())) for side in state["sides"]
    ]
    retaliation_before = [Fraction(side["weight"]) for side in state["sides"]]
    retaliation_after = retaliation_before.copy()
    lower_side = None
    if retaliation_before[0] < retaliation_before[1] and retaliation_counts[0]:
        lower_side = 0
    elif retaliation_before[1] < retaliation_before[0] and retaliation_counts[1]:
        lower_side = 1
    if lower_side is not None:
        retaliation_after = [retaliation_before[1], retaliation_before[0]]
    retaliation_records = []
    for current_side, count in enumerate(retaliation_counts):
        if not count:
            continue
        opponent_side = 1 - current_side
        was_lower = retaliation_before[current_side] < retaliation_before[opponent_side]
        if was_lower:
            bonus = Fraction(4 * count)
            swapped = lower_side == current_side
        else:
            bonus = Fraction(40 * count)
            swapped = False
        retaliation_after[current_side] += bonus
        retaliation_records.append(
            {
                "side": current_side,
                "count": count,
                "was_lower": was_lower,
                "swapped": swapped,
                "bonus": bonus,
                "before": retaliation_before[current_side],
                "after": retaliation_after[current_side],
            }
        )
    for current_side, player in enumerate(state["sides"]):
        player["weight"] = max(Fraction(1, VICTORY_WEIGHT_SCALE), retaliation_after[current_side])

    yilu_true_damage = []
    for current_side, player in enumerate(state["sides"]):
        layers = int(player["turn"].get("yilu_true_damage_layers", 0))
        if not layers:
            continue
        before_true_damage = Fraction(player["weight"])
        player["weight"] *= 2**layers
        yilu_true_damage.append(
            {
                "side": current_side,
                "layers": layers,
                "before": before_true_damage,
                "after": Fraction(player["weight"]),
            }
        )

    firefly_collapse_updates = []
    for attacker, player in enumerate(state["sides"]):
        added = sum(
            int(event.get("firefly_collapse_to_add", 0))
            for event in player["turn"].get("events", ())
        )
        if not added:
            continue
        target = 1 - attacker
        before_collapse = int(state["sides"][target].get("firefly_collapse", 0))
        after_collapse = min(3, before_collapse + added)
        state["sides"][target]["firefly_collapse"] = after_collapse
        firefly_collapse_updates.append(
            {
                "source_side": attacker,
                "target_side": target,
                "added": added,
                "before": before_collapse,
                "after": after_collapse,
            }
        )

    return {
        "domain": domain,
        "daniya_transition": daniya_transition,
        "asamu_domain_copies": asamu_domain_copies,
        "generated_events": tuple(asamu_domain_copies)
        + ((chaos_auto_mimic_event,) if chaos_auto_mimic_event is not None else ()),
        "adjustments": tuple(adjustments),
        "future_simulations": tuple(future_simulations),
        "sand_bodies": tuple(sand_bodies),
        "pressure_checks": tuple(pressure_checks),
        "daniya_damage_immunity_sides": tuple(sorted(daniya_damage_immunity_sides)),
        "yilu_defender_results": tuple(yilu_defender_results),
        "yilu_true_damage": tuple(yilu_true_damage),
        "firefly_collapse_updates": tuple(firefly_collapse_updates),
        "zeroes": tuple(zeroes),
        "round_reductions": tuple(round_reductions),
        "cross_effects": tuple(cross_effects),
        "retaliations": tuple(retaliation_records),
        "retaliation_snapshot": tuple(retaliation_before),
        "retaliation_after_snapshot": tuple(retaliation_after),
    }


def _dynamic_injury_wheel(state: dict, loser: int) -> tuple[tuple, dict]:
    player = state["sides"][loser]
    base = INJURY_WHEELS[int(player["risk"])]
    weights = {name: int(weight) for name, weight in base}
    permanent_bonus = int(player.get("injury_exhaust_bonus_units", 0))
    collapse_bonus = int(player.get("firefly_collapse", 0))
    weights["exhausted"] += permanent_bonus + collapse_bonus
    misfortune_count = sum(int(side["turn"].get("asamu_misfortune_count", 0)) for side in state["sides"])
    daniya_opponents = [
        side
        for side_index, side in enumerate(state["sides"])
        if side_index != loser and side.get("snapshot", {}).get("fighter_id") == "daniya"
    ]
    daniya_passive_layers = len(daniya_opponents)
    daniya_active_layers = sum(
        int(side["turn"].get("daniya_collapse_count", 0))
        for side_index, side in enumerate(state["sides"])
        if side_index != loser
    )
    daniya_layer_multiplier = max(1, int(state["round"]) * 5)
    daniya_layers = daniya_passive_layers + daniya_active_layers
    daniya_multiplier = daniya_layer_multiplier**daniya_layers
    multiplier = (5**misfortune_count) * daniya_multiplier
    weights["exhausted"] *= multiplier
    recovery_layers = int(player["turn"].get("yilu_injury_recovery_layers", 0))
    worsen_layers = int(player["turn"].get("yilu_injury_worsen_layers", 0))
    injury_factor = Fraction(1, 2) ** recovery_layers * Fraction(3, 2) ** worsen_layers
    exact_weights = {name: Fraction(value) for name, value in weights.items()}
    current_firefly_delta = Fraction(
        player.get("turn", {}).get("firefly_self_exhaust_delta_units", 0)
    )
    exact_weights["exhausted"] = max(
        Fraction(1, 10),
        exact_weights["exhausted"] + current_firefly_delta,
    )
    for name in ("heavy", "exhausted"):
        exact_weights[name] *= injury_factor
    weight_scale = 1
    for value in exact_weights.values():
        weight_scale = lcm(weight_scale, value.denominator)
    wheel = tuple((name, int(exact_weights[name] * weight_scale)) for name, _weight in base)
    return wheel, {
        "base_wheel": base,
        "permanent_exhaust_bonus_units": permanent_bonus,
        "firefly_collapse_bonus_units": collapse_bonus,
        "firefly_current_delta_units": current_firefly_delta,
        "misfortune_count": misfortune_count,
        "daniya_passive_layers": daniya_passive_layers,
        "daniya_active_layers": daniya_active_layers,
        "daniya_layer_multiplier": daniya_layer_multiplier,
        "daniya_multiplier": daniya_multiplier,
        # 兼容旧渲染字段；Battle v13 已没有跨回合反噬。
        "current_collapse_multiplier": daniya_multiplier,
        "rebound_multiplier": 1,
        "total_exhaust_multiplier": multiplier,
        "yilu_recovery_layers": recovery_layers,
        "yilu_worsen_layers": worsen_layers,
        "yilu_injury_factor": injury_factor,
        "weight_scale": weight_scale,
    }


def resolve_round(state: dict, seed: str) -> dict | None:
    _side(state, 0)
    if not all(side["turn"]["done"] for side in state["sides"]):
        return None
    provisional = deepcopy(state["sides"])
    interactions = _settle_interactions(state, seed)
    before = deepcopy(state["sides"])
    winner_units = [_winner_units(side["weight"]) for side in before]
    roll = randbelow(
        seed,
        f"{state['round']}:winner",
        sum(winner_units),
        version=state["version"],
    )
    winner = 0 if roll < winner_units[0] else 1
    loser = 1 - winner
    wheel, injury_modifiers = _dynamic_injury_wheel(state, loser)
    injury, injury_roll = choose(seed, f"{state['round']}:{loser}:injury", wheel, version=state["version"])
    injury_rewound = bool(
        state["sides"][loser]["snapshot"].get("fighter_id") == "juejue"
        and state["sides"][loser]["turn"].get("juejue_rewind")
        and injury in {"light", "heavy"}
    )
    if not injury_rewound:
        apply_injury(state["sides"][loser], injury)
    natural_end = injury == "exhausted"

    # Battle v13 的计时溃灭是当回合被动层，不再登记旧版跨回合反噬。
    collapse_rebounds: list[dict] = []
    carryover = []
    for player in state["sides"]:
        start_weight = Fraction(player.get("round_start_weight", 5))
        round_gain = Fraction(player["weight"]) - start_weight
        retained_gain = Fraction(_ceil_fraction(round_gain / 2))
        next_round_weight = max(Fraction(1, VICTORY_WEIGHT_SCALE), start_weight + retained_gain)
        carryover.append(
            {
                "round_start_weight": start_weight,
                "round_gain": round_gain,
                "retained_gain": retained_gain,
                "settlement_weight": player["weight"],
                "next_round_weight": None if natural_end else next_round_weight,
                "applied": not natural_end,
            }
        )
    result = {
        "round": state["round"],
        "winner": winner,
        "loser": loser,
        "winner_roll": roll,
        "winner_weight_scale": VICTORY_WEIGHT_SCALE,
        "winner_weight_units": tuple(winner_units),
        "injury": injury,
        "injury_rewound": injury_rewound,
        "injury_effective": "none" if injury_rewound else injury,
        "injury_roll": injury_roll,
        "injury_wheel": wheel,
        "injury_modifiers": injury_modifiers,
        "collapse_rebounds": tuple(collapse_rebounds),
        "provisional": provisional,
        "interactions": interactions,
        "carryover": tuple(carryover),
        "before": before,
        "after": deepcopy(state["sides"]),
        "natural_end": natural_end,
        "firefly_transitions": (),
        "daniya_world_transitions": (),
    }
    if natural_end:
        state.update(status="completed", winner=winner)
    else:
        state["round"] += 1
        firefly_transitions = []
        daniya_world_transitions = []
        for index, player in enumerate(state["sides"]):
            player.setdefault("round_gains", []).append(carryover[index]["round_gain"])
            player["weight"] = carryover[index]["next_round_weight"]
            player["round_start_weight"] = carryover[index]["next_round_weight"]
            next_sam_draw_bonus_units = 0
            if player.get("snapshot", {}).get("fighter_id") == "firefly":
                if not player["turn"].get("firefly_entered_sam"):
                    next_sam_draw_bonus_units = int(
                        player["turn"].get("firefly_no_transform_bonus_units", 0)
                    )
                before_form = str(player.get("firefly_form") or FIREFLY_FORM_FIREFLY)
                before_remaining = int(player.get("firefly_sam_rounds_remaining", 0))
                if before_form == FIREFLY_FORM_SAM:
                    after_remaining = max(0, before_remaining - 1)
                    player["firefly_sam_rounds_remaining"] = after_remaining
                    if after_remaining == 0:
                        player["firefly_form"] = FIREFLY_FORM_FIREFLY
                firefly_transitions.append(
                    {
                        "side": index,
                        "before_form": before_form,
                        "after_form": str(player.get("firefly_form") or FIREFLY_FORM_FIREFLY),
                        "before_remaining": before_remaining,
                        "after_remaining": int(player.get("firefly_sam_rounds_remaining", 0)),
                        "next_sam_draw_bonus_units": next_sam_draw_bonus_units,
                    }
                )
            world_effects_disabled = bool(player.get("daniya_world_disable_next"))
            world_forced_move_ids = list(player.get("daniya_world_forced_move_ids_next", ()))
            world_forced_form = str(
                player.get("daniya_world_forced_form_next") or DANIYA_FORM_STAGING
            )
            player["daniya_world_disable_next"] = False
            player["daniya_world_forced_move_ids_next"] = []
            player["daniya_world_forced_form_next"] = ""
            player["turn"] = fresh_turn()
            player["turn"]["firefly_sam_draw_bonus_units"] = next_sam_draw_bonus_units
            player["turn"]["yilu_round_base_bonus"] = int(
                player.get("yilu_next_round_base_bonus", 0)
            )
            player["yilu_next_round_base_bonus"] = 0
            player["turn"]["daniya_world_effects_disabled"] = world_effects_disabled
            player["turn"]["daniya_world_forced_move_ids"] = world_forced_move_ids
            player["turn"]["daniya_world_forced_form"] = (
                world_forced_form if world_forced_move_ids else ""
            )
            if world_effects_disabled or world_forced_move_ids:
                daniya_world_transitions.append(
                    {
                        "side": index,
                        "effects_disabled": world_effects_disabled,
                        "forced_form": world_forced_form if world_forced_move_ids else "",
                        "forced_move_ids": world_forced_move_ids,
                    }
                )
        result["firefly_transitions"] = tuple(firefly_transitions)
        result["daniya_world_transitions"] = tuple(daniya_world_transitions)
    return result


def loot_weights(
    *, level: int, feed: int, cloud: int, six_available: bool, rule_version: int = BATTLE_RULE_VERSION
) -> tuple[float, ...]:
    base = LEGACY_LOOT_WEIGHTS if rule_version == 1 else LOOT_WEIGHTS
    weights = catch_weights(base, player_level=level, feed_level=feed, six_star_available=six_available)
    return apply_six_star_progress(weights, stacks=cloud, bonus_per_stack=0.2, action="catch")
