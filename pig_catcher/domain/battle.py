"""可恢复、精确整数、顺序无关的双人轮盘纯状态机。无I/O、时间或全局随机。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .battle_catalog import (
    BATTLE_RULE_VERSION,
    BATTLE_VERSION,
    COUNT_WHEEL,
    FIGHTERS,
    FIGHTERS_BY_ID,
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
    BattleError,
    Move,
    fighter_form_moves,
)
from .food_effects import apply_six_star_progress
from .rules import catch_weights


def _pack(value: Any) -> Any:
    # 不依赖SQLite有符号64位，也不改变进程级Python整数安全设置。
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
        return int(item["$battle-int"], 16) if set(item) == {"$battle-int"} else item

    return json.loads(value, object_hook=hook)


def weight_label(value: int) -> str:
    if value.bit_length() <= 200:
        return str(value)
    digits = (value.bit_length() * 30103) // 100000 + 1
    return f"超大权重（约{digits}位；精确整数结算）"


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
        "juejue_realtime": False,
        "juejue_future_simulation": False,
        "juejue_sand_body": False,
        "juejue_zero_checked": False,
        "juejue_zero_active": False,
        "juejue_acceleration_tier": 0,
        "juejue_delay_tier": 0,
        "juejue_rewind": False,
    }


def _frozen_mimic_pool() -> dict[str, list[dict]]:
    pool: dict[str, list[dict]] = {"large": [], "small": []}
    for fighter in FIGHTERS:
        if fighter.fighter_id == "juejue":
            continue
        for move in fighter.moves:
            if not move.gain:
                continue
            entry = {
                "fighter_id": fighter.fighter_id,
                "move_id": move.move_id,
                "name": move.name,
                "base": int(move.gain),
                "direction": move.direction,
            }
            pool["large" if abs(move.gain) >= 20 else "small"].append(entry)
    return pool


def new_state(fighters: list[dict], *, seed: str = "") -> dict:
    if len(fighters) != 2:
        raise BattleError("对战必须有两名玩家。")
    sides = []
    mimic_pool = _frozen_mimic_pool()
    for side, snapshot in enumerate(fighters):
        if snapshot.get("fighter_id") not in FIGHTERS_BY_ID or not 0 <= snapshot.get("level", 0) <= 5:
            raise BattleError("未知战斗猪或养成等级。")
        is_juejue = snapshot["fighter_id"] == "juejue"
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
    turn.update(raw=raw, debt=debt, bonus=bonus, effective=effective, pending=effective, done=effective == 0)
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
        "base": int(source["base"]),
        "direction": source.get("direction", "self"),
    }


def apply_move(
    player: dict,
    move: Move,
    *,
    seed: str = "",
    round_number: int = 1,
    side: int = 0,
    version: int = BATTLE_VERSION,
) -> dict:
    """应用单个已抽中的招式；撅撅猪的嵌套轮盘也在同一确定性命名空间内结算。"""

    turn = player["turn"]
    if turn["pending"] <= 0:
        raise BattleError("本回合没有待执行招式。")
    turn["pending"] -= 1
    turn["draws"] += 1
    ordinal = int(turn["draws"])
    key = f"{round_number}:{side}:move:{ordinal}:juejue"
    snapshot = player["snapshot"]
    is_juejue = snapshot.get("fighter_id") == "juejue"
    form_before = player.get("juejue_form", "") if is_juejue else ""
    music_was_active = bool(is_juejue and turn.get("juejue_music"))
    music_gain = 5 if music_was_active else 0
    special_base = int(move.gain)
    numeric_direction = move.direction
    special_extra_draws = 0
    opponent_reduction = 0
    opponent_next_debt = 0
    opponent_next_bonus = 0
    subwheel = None
    mimic = None
    relative_zero = None
    zero_bonus = 0
    sculpt_before = int(player.get("juejue_sculpt_bonus", 0))
    sand_steps_before = int(player.get("juejue_sand_domain_steps", 0))
    sand_switch_before = int(player.get("juejue_sand_domain_switch_units", 0))
    realization_before = int(player.get("juejue_realization_stacks", 0))
    guaranteed_before = bool(player.get("juejue_guaranteed", False))
    realtime_activated = False
    future_activated = False
    sand_body_activated = False

    if is_juejue and "juejue-accelerate" in move.tags:
        tier, subwheel = _juejue_subwheel(player, "acceleration", seed, key, version)
        if subwheel["success"]:
            special_base = tier.gain
            special_extra_draws = tier.extra_draws
            turn["juejue_acceleration_tier"] = max(turn["juejue_acceleration_tier"], tier.tier)
        else:
            player["next_debt"] += tier.failure_debt
    elif is_juejue and "juejue-delay" in move.tags:
        tier, subwheel = _juejue_subwheel(player, "delay", seed, key, version)
        if subwheel["success"]:
            special_base = tier.gain
            opponent_reduction = tier.opponent_reduction
            opponent_next_debt = tier.opponent_debt
            turn["juejue_delay_tier"] = max(turn["juejue_delay_tier"], tier.tier)
        else:
            opponent_next_bonus = tier.failure_opponent_bonus
    elif is_juejue and "juejue-mimic" in move.tags:
        mimic = _juejue_mimic(player, seed, key, version)
        special_base = abs(int(mimic["base"]))
        numeric_direction = mimic["direction"]
    elif is_juejue and "juejue-make-real" in move.tags:
        special_base = 12 + 5 * realization_before
        player["juejue_realization_stacks"] += 1

    if is_juejue and "juejue-sculpt" in move.tags:
        player["juejue_sculpt_bonus"] = min(20, player["juejue_sculpt_bonus"] + 5)
        player["juejue_sand_domain_steps"] += 1
    if is_juejue and "juejue-rewind" in move.tags:
        turn["juejue_rewind"] = True
    if is_juejue and "juejue-sand-body" in move.tags and not turn["juejue_sand_body"]:
        turn["juejue_sand_body"] = True
        sand_body_activated = True
    if is_juejue and "juejue-future-simulation" in move.tags and not turn["juejue_future_simulation"]:
        turn["juejue_future_simulation"] = True
        future_activated = True
    if is_juejue and "juejue-realtime" in move.tags and not turn["juejue_realtime"]:
        turn["juejue_realtime"] = True
        realtime_activated = True
    if is_juejue and "juejue-virtual-realm" in move.tags:
        player["juejue_guaranteed"] = True
    if is_juejue and "juejue-music" in move.tags:
        turn["juejue_music"] = True
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

    if (
        is_juejue
        and subwheel is not None
        and subwheel["success"]
        and not turn["juejue_zero_checked"]
        and turn["juejue_acceleration_tier"] + turn["juejue_delay_tier"] >= 5
    ):
        zero_success, zero_roll = choose(
            seed,
            f"{key}:relative-zero",
            ((True, 1), (False, 1)),
            version=version,
        )
        turn["juejue_zero_checked"] = True
        turn["juejue_zero_active"] = bool(zero_success)
        zero_bonus = 40 if zero_success else 0
        relative_zero = {
            "checked": True,
            "acceleration_tier": turn["juejue_acceleration_tier"],
            "delay_tier": turn["juejue_delay_tier"],
            "wheel": ((True, 1), (False, 1)),
            "roll": zero_roll,
            "success": bool(zero_success),
            "gain": zero_bonus,
        }

    numeric = special_base > 0
    tool = snapshot.get("tool_id", "") if not player["tool_used"] else ""
    penalty = int(player["heavy"] and not (numeric and tool == "bandage"))
    multiplier = 2 if numeric and player["double"] else 1
    computed_numeric = (
        max(0, special_base + snapshot["level"] + player["core"] - penalty) * multiplier if numeric else 0
    )
    black_flash_bonus = player["black_flash_stacks"]
    trait = int(numeric and snapshot.get("trait_bonus", 0) and not turn["trait_used"])
    tool_gain = 2 if numeric and tool == "wristband" else 0
    used_tool = bool(numeric and (tool == "wristband" or (tool == "bandage" and player["heavy"])))
    directed_numeric = computed_numeric + trait + tool_gain
    own_numeric = directed_numeric if numeric_direction == "self" else 0
    if numeric_direction == "opponent":
        opponent_reduction += directed_numeric
    gain = own_numeric + music_gain + black_flash_bonus + zero_bonus
    if numeric:
        player["double"] = False
        turn["trait_used"] = True
    if used_tool:
        player["tool_used"] = True
    player["weight"] += gain
    extra_draws = move.draws + special_extra_draws
    turn["pending"] += extra_draws
    if move.loan:
        player["double"] = True
        player["next_debt"] += 1
    if "black-flash" in move.tags:
        player["black_flash_stacks"] += 1
    purple_weight_steps_before = player["purple_weight_steps"]
    purple_weight_steps_used = purple_weight_steps_before if "purple" in move.tags else 0
    if "purple" in move.tags:
        player["purple_weight_steps"] = 0
    if "blue-red" in move.tags:
        player["purple_weight_steps"] += 1
    if "infinity" in move.tags:
        turn["infinity_used"] = True
    turn["done"] = turn["pending"] == 0
    return {
        "ordinal": ordinal,
        "move_id": move.move_id,
        "name": move.name,
        "base": move.gain,
        "special_base": special_base,
        "numeric_base": numeric,
        "numeric_direction": numeric_direction,
        "training": snapshot["level"] if numeric else 0,
        "core": player["core"],
        "heavy": player["heavy"],
        "risk": player["risk"],
        "penalty": penalty if numeric else 0,
        "multiplier": multiplier,
        "trait_gain": trait,
        "tool_gain": tool_gain,
        "black_flash_bonus": black_flash_bonus,
        "black_flash_stacks": player["black_flash_stacks"],
        "music_gain": music_gain,
        "zero_gain": zero_bonus,
        "subwheel": subwheel,
        "relative_zero": relative_zero,
        "mimic": mimic,
        "opponent_reduction": opponent_reduction,
        "opponent_next_debt": opponent_next_debt,
        "opponent_next_bonus": opponent_next_bonus,
        "form_before": form_before,
        "form_after": player.get("juejue_form", "") if is_juejue else "",
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
        "sand_body_activated": sand_body_activated,
        "rewind_active": bool(turn.get("juejue_rewind")),
        "tool_used": snapshot.get("tool_id", "") if used_tool else "",
        "gain": gain,
        "total": player["weight"],
        "extra_draws": extra_draws,
        "loan": move.loan,
        "double_pending": player["double"],
        "next_debt": player["next_debt"],
        "next_action_bonus": player.get("next_action_bonus", 0),
        "pending": turn["pending"],
        "purple_weight_steps_before": purple_weight_steps_before,
        "purple_weight_steps_used": purple_weight_steps_used,
        "purple_weight_steps": player["purple_weight_steps"],
        "tags": list(move.tags),
    }


def move_weight_units(player: dict, move: Move) -> int:
    """Return exact tenths used by the deterministic move wheel."""

    units = move.draw_weight * MOVE_WEIGHT_SCALE
    if "purple" in move.tags:
        units += int(player.get("purple_weight_steps", 0))
    if player.get("snapshot", {}).get("fighter_id") == "juejue" and "domain" in move.tags:
        # 两个领域在主招式盘的基础出现权重均为1；塑型、切盘和实时演算只叠加动态权重。
        if player.get("turn", {}).get("juejue_realtime"):
            units += 10
        if "juejue-sand-domain" in move.tags:
            units += int(player.get("juejue_sand_domain_steps", 0))
            units += int(player.get("juejue_sand_domain_switch_units", 0))
    return max(1, units)


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
        # 每次抽取都重新读取当前形态。切换招式增加的 pending 会在同一
        # play_chunk 内立刻从新轮盘抽取，不会继续使用分片开始时的旧盘。
        moves = (
            fighter_form_moves(fighter_id, player["juejue_form"])
            if fighter_id == "juejue"
            else FIGHTERS_BY_ID[fighter_id].moves
        )
        ordinal = player["turn"]["draws"] + 1
        wheel = tuple((index, move_weight_units(player, move)) for index, move in enumerate(moves))
        index, roll = choose(
            seed,
            f"{state['round']}:{side}:move:{ordinal}",
            wheel,
            version=state["version"],
        )
        event = apply_move(
            player,
            moves[index],
            seed=seed,
            round_number=state["round"],
            side=side,
            version=state["version"],
        )
        event.update(
            roll=roll,
            round=state["round"],
            side=side,
            fighter_id=fighter_id,
            draw_weight_scale=MOVE_WEIGHT_SCALE,
            draw_wheel_move_ids=[move.move_id for move in moves],
            draw_wheel_units=[weight for _index, weight in wheel],
        )
        player["turn"]["events"].append(deepcopy(event))
        events.append(event)
    return events


def apply_injury(player: dict, injury: str) -> None:
    if injury == "light":
        player["risk"] = max(player["risk"], 1)
    elif injury == "heavy":
        player["heavy"], player["risk"] = True, 2
    elif injury == "core":
        player["heavy"] = False
        player["core"] += 1
    elif injury != "exhausted":
        raise BattleError("未知伤势结果。")


def _remaining_event_gain(cancelled: list[dict[int, dict]], side: int, event: dict) -> int:
    entry = cancelled[side].get(int(event["ordinal"]))
    return max(0, int(event.get("gain", 0)) - (int(entry["gain"]) if entry else 0))


def _reduce_event(
    cancelled: list[dict[int, dict]], side: int, event: dict, amount: int, reason: str
) -> int:
    """Deduct at most the still-effective part of an event and retain every reason."""

    entry = cancelled[side].setdefault(
        int(event["ordinal"]), {"ordinal": int(event["ordinal"]), "gain": 0, "reasons": []}
    )
    applied = min(max(0, int(amount)), _remaining_event_gain(cancelled, side, event))
    entry["gain"] += applied
    if reason not in entry["reasons"]:
        entry["reasons"].append(reason)
    return applied


def _cancel_event(cancelled: list[dict[int, dict]], side: int, event: dict, reason: str) -> int:
    return _reduce_event(cancelled, side, event, _remaining_event_gain(cancelled, side, event), reason)


def _domain_ids(events: list[dict]) -> list[str]:
    return [str(event.get("move_id", "")) for event in events]


def _domain_strength(state: dict, side: int, events: list[dict]) -> tuple[int, bool]:
    fighter_id = state["sides"][side]["snapshot"]["fighter_id"]
    distinct_juejue = {
        event.get("move_id")
        for event in events
        if event.get("move_id") in {"sand-domain", "chaos-domain"}
    }
    dual_juejue = fighter_id == "juejue" and len(distinct_juejue) == 2
    if dual_juejue:
        return 11, True
    if fighter_id == "juejue":
        return 5, False
    if fighter_id == "sukuna":
        return 8, False
    return 6, False


def _domain_resolution(state: dict, seed: str, cancelled: list[dict[int, dict]]) -> dict | None:
    domains = [
        [event for event in side["turn"].get("events", ()) if "domain" in event.get("tags", ())]
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
        # 领域战以二倍整数保存半点：普通6、宿傩8、平手6；撅撅猪
        # 单领域5，两个不同领域同回合齐出时11。宿傩镜像因此为8:8:6。
        wheel = (("side-0", strengths[0]), ("side-1", strengths[1]), ("tie", 6))
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
        "weight_scale": 2 if mode == "clash" else 1,
        "strengths": strengths,
        "outcome": outcome,
        "roll": roll,
        "winner": winner,
        "hit_side": hit_side,
        "domain_counts": [len(events) for events in domains],
        "domain_ids": [_domain_ids(events) for events in domains],
        "dual_juejue": dual_juejue,
        "boost_side": None,
        "boosted_ordinal": None,
        "boosted_ordinals": [],
        "bonus_gain": 0,
        "effects": [],
        "effect": "",
        "auto_mimic": None,
        "nullified_side": None,
        "cross_debuff_suppressed": False,
    }


def _settle_interactions(state: dict, seed: str) -> dict:
    cancelled: list[dict[int, dict]] = [{}, {}]
    domain = _domain_resolution(state, seed, cancelled)
    version = state["version"]
    round_number = state["round"]

    # 无下限先抵消对方第一招仍有效、具有基础数值的招式；不会回滚功能。
    for defender in (0, 1):
        if not state["sides"][defender]["turn"].get("infinity_used"):
            continue
        attacker = 1 - defender
        for event in state["sides"][attacker]["turn"].get("events", ()):
            if not event.get("numeric_base") or _remaining_event_gain(cancelled, attacker, event) <= 0:
                continue
            _cancel_event(cancelled, attacker, event, "无下限·防御")
            break

    # 未来模拟与沙之形体均每方每轮最多结算一次；只改变数值贡献，保留再抽、
    # 贷款、切换形态等功能事实。
    future_simulations = []
    sand_bodies = []
    for defender in (0, 1):
        attacker = 1 - defender
        future = {
            "side": defender,
            "active": bool(state["sides"][defender]["turn"].get("juejue_future_simulation")),
            "target_side": attacker,
            "candidate_ordinals": [],
            "selected_ordinal": None,
            "roll": None,
            "cancelled_gain": 0,
        }
        if future["active"]:
            candidates = [
                event
                for event in state["sides"][attacker]["turn"].get("events", ())
                if event.get("numeric_base") and _remaining_event_gain(cancelled, attacker, event) > 0
            ]
            future["candidate_ordinals"] = [int(event["ordinal"]) for event in candidates]
            if candidates:
                wheel = tuple((int(event["ordinal"]), 1) for event in candidates)
                selected, roll = choose(
                    seed,
                    f"{round_number}:juejue:future:{defender}:target",
                    wheel,
                    version=version,
                )
                event = next(item for item in candidates if int(item["ordinal"]) == selected)
                future.update(selected_ordinal=selected, roll=roll)
                future["cancelled_gain"] = _cancel_event(
                    cancelled, attacker, event, "虚拟声·未来模拟"
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
                if not event.get("numeric_base") or remaining <= 0:
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
                if before_remaining <= 0:
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

    # 领域战胜方通常只翻倍第一份仍有效领域。撅撅猪两个不同领域在真实
    # clash 中获胜时，改为两份仍有效领域数值相加后整体翻倍；同领域重复
    # 不产生双领域特效。
    if domain is not None:
        winner = domain.get("winner")
        if domain.get("mode") == "clash" and winner in (0, 1):
            effective_domains = [
                event
                for event in state["sides"][winner]["turn"].get("events", ())
                if "domain" in event.get("tags", ())
                and _remaining_event_gain(cancelled, winner, event) > 0
            ]
            if domain["dual_juejue"][winner]:
                by_id = {}
                for event in effective_domains:
                    if event.get("move_id") in {"sand-domain", "chaos-domain"}:
                        by_id.setdefault(event["move_id"], event)
                boosted = list(by_id.values())
            else:
                boosted = effective_domains[:1]
            if boosted:
                bonus = sum(_remaining_event_gain(cancelled, winner, event) for event in boosted)
                ordinals = [int(event["ordinal"]) for event in boosted]
                state["sides"][winner]["weight"] += bonus
                domain.update(
                    boost_side=winner,
                    boosted_ordinal=ordinals[-1],
                    boosted_ordinals=ordinals,
                    bonus_gain=bonus,
                )

    domain_effects: list[str] = []
    auto_mimic = None
    extra_round_reduction = [0, 0]
    if domain is not None and domain.get("hit_side") in (0, 1):
        hit_side = int(domain["hit_side"])
        target = 1 - hit_side
        fighter_id = state["sides"][hit_side]["snapshot"]["fighter_id"]
        hit_ids = set(domain["domain_ids"][hit_side])
        if fighter_id == "gojo":
            if target not in protected_juejue_sides:
                state["sides"][target]["next_debt"] += 1
                domain_effects.append("无量空处命中：对方下回合出招数-1")
            else:
                domain["cross_debuff_suppressed"] = True
        if fighter_id == "juejue" and "sand-domain" in hit_ids:
            state["sides"][hit_side]["next_action_bonus"] += 1
            if target not in protected_juejue_sides:
                state["sides"][target]["next_debt"] += 1
                domain_effects.append("荒时之沙命中：自己下回合+1招，对方下回合-1招")
            else:
                domain["cross_debuff_suppressed"] = True
                domain_effects.append("荒时之沙命中：自己下回合+1招")
        if fighter_id == "juejue" and "chaos-domain" in hit_ids:
            player = state["sides"][hit_side]
            player["next_action_bonus"] += 1
            player["juejue_guaranteed"] = True
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
            music_gain = 5 if player["turn"].get("juejue_music") else 0
            direction = mimic.get("direction", "self")
            raw_gain = numeric + music_gain if mimic["available"] and direction == "self" else 0
            raw_reduction = numeric + music_gain if mimic["available"] and direction == "opponent" else 0
            numeric_suppressed = bool(mimic["available"] and target in protected_juejue_sides)
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
            applied_gain = 0 if numeric_suppressed else raw_gain
            applied_reduction = 0 if numeric_suppressed else raw_reduction
            if applied_gain:
                player["weight"] += applied_gain
            if applied_reduction:
                extra_round_reduction[target] += applied_reduction
            auto_mimic = {
                **mimic,
                "training": int(player["snapshot"].get("level", 0)) if mimic["available"] else 0,
                "core": int(player.get("core", 0)) if mimic["available"] else 0,
                "heavy_penalty": int(bool(player.get("heavy"))) if mimic["available"] else 0,
                "music_gain": music_gain if mimic["available"] else 0,
                "raw_gain": raw_gain,
                "raw_opponent_reduction": raw_reduction,
                "gain": applied_gain,
                "opponent_reduction": applied_reduction,
                "numeric_suppressed": numeric_suppressed,
                "suppressed_reason": suppressed_reason,
            }
            domain_effects.append("乱序数虚时空命中：自动模仿、自己下回合+1招并保证下一次加速或时延成功")
            if numeric_suppressed:
                domain_effects.append(f"自动模仿数值被{suppressed_reason}清零，领域功能仍生效")

    if domain is not None:
        domain["effects"] = domain_effects
        domain["effect"] = "；".join(domain_effects)
        domain["auto_mimic"] = auto_mimic
        domain["nullified_side"] = 1 - dual_winner if dual_winner in (0, 1) else None

    # 时延的功能结算在所有“招式数值归零”之后执行。普通抵消不回滚功能；
    # 相对零/双领域则同时保护撅撅猪免受本轮减权与跨回合欠招。
    requested_reductions = extra_round_reduction
    reduction_sources: list[list[int]] = [[], []]
    cross_effects = []
    for attacker in (0, 1):
        target = 1 - attacker
        for event in state["sides"][attacker]["turn"].get("events", ()):
            original_requested = int(event.get("opponent_reduction", 0))
            debt = int(event.get("opponent_next_debt", 0))
            bonus = int(event.get("opponent_next_bonus", 0))
            reduction_suppressed = bool(original_requested and target in protected_juejue_sides)
            requested = 0 if reduction_suppressed else original_requested
            if requested:
                requested_reductions[target] += requested
                reduction_sources[target].append(int(event["ordinal"]))
            debt_suppressed = bool(debt and target in protected_juejue_sides)
            if debt and not debt_suppressed:
                state["sides"][target]["next_debt"] += debt
            if bonus:
                state["sides"][target]["next_action_bonus"] += bonus
            if original_requested or debt or bonus:
                cross_effects.append(
                    {
                        "source_side": attacker,
                        "source_ordinal": int(event["ordinal"]),
                        "target_side": target,
                        "round_reduction": requested,
                        "round_reduction_suppressed": reduction_suppressed,
                        "next_debt": 0 if debt_suppressed else debt,
                        "next_bonus": bonus,
                        "debt_suppressed": debt_suppressed,
                    }
                )

    round_reductions = []
    for target in (0, 1):
        floor = int(state["sides"][target].get("round_start_weight", 5))
        available = max(0, int(state["sides"][target]["weight"]) - floor)
        applied = min(int(requested_reductions[target]), available)
        state["sides"][target]["weight"] -= applied
        round_reductions.append(
            {
                "side": target,
                "requested": int(requested_reductions[target]),
                "applied": applied,
                "floor": floor,
                "source_ordinals": reduction_sources[target],
            }
        )

    return {
        "domain": domain,
        "adjustments": tuple(adjustments),
        "future_simulations": tuple(future_simulations),
        "sand_bodies": tuple(sand_bodies),
        "zeroes": tuple(zeroes),
        "round_reductions": tuple(round_reductions),
        "cross_effects": tuple(cross_effects),
    }


def resolve_round(state: dict, seed: str) -> dict | None:
    _side(state, 0)
    if not all(side["turn"]["done"] for side in state["sides"]):
        return None
    provisional = deepcopy(state["sides"])
    interactions = _settle_interactions(state, seed)
    before = deepcopy(state["sides"])
    roll = randbelow(
        seed,
        f"{state['round']}:winner",
        sum(side["weight"] for side in before),
        version=state["version"],
    )
    winner = 0 if roll < before[0]["weight"] else 1
    loser = 1 - winner
    wheel = INJURY_WHEELS[before[loser]["risk"]]
    injury, injury_roll = choose(seed, f"{state['round']}:{loser}:injury", wheel, version=state["version"])
    injury_rewound = bool(
        state["sides"][loser]["snapshot"].get("fighter_id") == "juejue"
        and state["sides"][loser]["turn"].get("juejue_rewind")
        and injury in {"light", "heavy"}
    )
    if not injury_rewound:
        apply_injury(state["sides"][loser], injury)
    natural_end = injury == "exhausted"
    carryover = []
    for player in state["sides"]:
        start_weight = int(player.get("round_start_weight", 5))
        round_gain = int(player["weight"]) - start_weight
        if round_gain < 0:
            raise BattleError("本回合结算权重低于回合起始权重，拒绝生成错误继承。")
        retained_gain = (round_gain + 1) // 2
        carryover.append(
            {
                "round_start_weight": start_weight,
                "round_gain": round_gain,
                "retained_gain": retained_gain,
                "settlement_weight": int(player["weight"]),
                "next_round_weight": None if natural_end else start_weight + retained_gain,
                "applied": not natural_end,
            }
        )
    result = {
        "round": state["round"],
        "winner": winner,
        "loser": loser,
        "winner_roll": roll,
        "injury": injury,
        "injury_rewound": injury_rewound,
        "injury_effective": "none" if injury_rewound else injury,
        "injury_roll": injury_roll,
        "injury_wheel": wheel,
        "provisional": provisional,
        "interactions": interactions,
        "carryover": tuple(carryover),
        "before": before,
        "after": deepcopy(state["sides"]),
        "natural_end": natural_end,
    }
    if natural_end:
        state.update(status="completed", winner=winner)
    else:
        state["round"] += 1
        for index, player in enumerate(state["sides"]):
            player.setdefault("round_gains", []).append(carryover[index]["round_gain"])
            player["weight"] = carryover[index]["next_round_weight"]
            player["round_start_weight"] = carryover[index]["next_round_weight"]
            player["turn"] = fresh_turn()
    return result


def loot_weights(
    *, level: int, feed: int, cloud: int, six_available: bool, rule_version: int = BATTLE_RULE_VERSION
) -> tuple[float, ...]:
    base = LEGACY_LOOT_WEIGHTS if rule_version == 1 else LOOT_WEIGHTS
    weights = catch_weights(base, player_level=level, feed_level=feed, six_star_available=six_available)
    return apply_six_star_progress(weights, stacks=cloud, bonus_per_stack=0.2, action="catch")
