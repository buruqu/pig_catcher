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
    FIGHTERS_BY_ID,
    HEAVY_COUNT_WHEEL,
    INJURY_WHEELS,
    LEGACY_LOOT_WEIGHTS,
    LOOT_WEIGHTS,
    MOVE_CHUNK_SIZE,
    MOVE_WEIGHT_SCALE,
    BattleError,
    Move,
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
    }


def new_state(fighters: list[dict]) -> dict:
    if len(fighters) != 2:
        raise BattleError("对战必须有两名玩家。")
    sides = []
    for snapshot in fighters:
        if snapshot.get("fighter_id") not in FIGHTERS_BY_ID or not 0 <= snapshot.get("level", 0) <= 5:
            raise BattleError("未知战斗猪或养成等级。")
        sides.append(
            {
                "snapshot": deepcopy(snapshot),
                "weight": 5,
                "heavy": False,
                "risk": 0,
                "core": 0,
                "next_debt": 0,
                "double": False,
                "tool_used": snapshot.get("tool_id", "") == "confetti",
                "black_flash_stacks": 0,
                "purple_weight_steps": 0,
                "round_start_weight": 5,
                "round_gains": [],
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
    player.setdefault("round_start_weight", 5)
    player.setdefault("round_gains", [])
    player["turn"].setdefault("ready", False)
    player["turn"].setdefault("events", [])
    player["turn"].setdefault("infinity_used", False)
    return player


def roll_count(state: dict, side: int, seed: str) -> dict:
    player = _side(state, side)
    turn = player["turn"]
    if turn["raw"] is not None:
        return {"changed": False, **deepcopy(turn)}
    wheel = HEAVY_COUNT_WHEEL if player["heavy"] else COUNT_WHEEL
    raw, roll = choose(seed, f"{state['round']}:{side}:count", wheel, version=state["version"])
    debt = player["next_debt"]
    player["next_debt"] = 0  # 只扣下一回合，负数不会继续倒欠。
    turn.update(raw=raw, debt=debt, effective=max(0, raw - debt), pending=max(0, raw - debt), done=raw <= debt)
    return {"changed": True, "roll": roll, "wheel": wheel, **deepcopy(turn)}


def apply_move(player: dict, move: Move) -> dict:
    """应用单个已抽中的招式；功能效果不享受升级或贷款倍率。"""
    turn = player["turn"]
    if turn["pending"] <= 0:
        raise BattleError("本回合没有待执行招式。")
    turn["pending"] -= 1
    turn["draws"] += 1
    numeric = move.gain > 0
    snapshot = player["snapshot"]
    tool = snapshot.get("tool_id", "") if not player["tool_used"] else ""
    penalty = int(player["heavy"] and not (numeric and tool == "bandage"))
    multiplier = 2 if numeric and player["double"] else 1
    base_gain = max(0, move.gain + snapshot["level"] + player["core"] - penalty) if numeric else 0
    # 黑闪领悟是独立的整场光环，作用于后续所有招式；贷款等纯功能招式
    # 仍不吃强化/核心/伤势/翻倍，也不会因此成为无下限的拦截目标。
    black_flash_bonus = player["black_flash_stacks"]
    trait = int(numeric and snapshot.get("trait_bonus", 0) and not turn["trait_used"])
    tool_gain = 2 if numeric and tool == "wristband" else 0
    used_tool = bool(numeric and (tool == "wristband" or (tool == "bandage" and player["heavy"])))
    gain = base_gain * multiplier + trait + tool_gain + black_flash_bonus
    if numeric:
        player["double"] = False
        turn["trait_used"] = True
    if used_tool:
        player["tool_used"] = True
    player["weight"] += gain
    turn["pending"] += move.draws
    if move.loan:
        player["double"] = True  # 连续贷款保留同一x2，不是x4，也不排队多次翻倍。
        player["next_debt"] += 1
    if "black-flash" in move.tags:
        player["black_flash_stacks"] += 1
    purple_weight_steps_before = player["purple_weight_steps"]
    purple_weight_steps_used = purple_weight_steps_before if "purple" in move.tags else 0
    if "purple" in move.tags:
        # 茈享受抽取前已经累计的苍/赫加成；一经抽中即视为使用并清零，
        # 即使该招的数值随后被无下限或领域结算抵消，也不会返还加成。
        player["purple_weight_steps"] = 0
    if "blue-red" in move.tags:
        player["purple_weight_steps"] += 1
    if "infinity" in move.tags:
        turn["infinity_used"] = True
    turn["done"] = turn["pending"] == 0
    return {
        "ordinal": turn["draws"],
        "move_id": move.move_id,
        "name": move.name,
        "base": move.gain,
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
        "purple_weight_steps_before": purple_weight_steps_before,
        "purple_weight_steps_used": purple_weight_steps_used,
        "purple_weight_steps": player["purple_weight_steps"],
        "tool_used": snapshot.get("tool_id", "") if used_tool else "",
        "gain": gain,
        "total": player["weight"],
        "extra_draws": move.draws,
        "loan": move.loan,
        "double_pending": player["double"],
        "next_debt": player["next_debt"],
        "pending": turn["pending"],
        "tags": list(move.tags),
    }


def move_weight_units(player: dict, move: Move) -> int:
    """Return exact tenths used by the deterministic move wheel."""

    return move.draw_weight * MOVE_WEIGHT_SCALE + (
        player.get("purple_weight_steps", 0) if "purple" in move.tags else 0
    )


def play_chunk(state: dict, side: int, seed: str, *, chunk_size: int = MOVE_CHUNK_SIZE) -> list[dict]:
    player = _side(state, side)
    if player["turn"]["raw"] is None:
        raise BattleError("请先输入 /出招数。")
    if type(chunk_size) is not int or chunk_size < 1:
        raise BattleError("无效的连抽分片大小。")
    moves = FIGHTERS_BY_ID[player["snapshot"]["fighter_id"]].moves
    events = []
    for _ in range(chunk_size):
        if player["turn"]["done"]:
            break
        ordinal = player["turn"]["draws"] + 1
        wheel = tuple((index, move_weight_units(player, move)) for index, move in enumerate(moves))
        index, roll = choose(
            seed,
            f"{state['round']}:{side}:move:{ordinal}",
            wheel,
            version=state["version"],
        )
        event = apply_move(player, moves[index])
        event.update(
            roll=roll,
            round=state["round"],
            side=side,
            fighter_id=player["snapshot"]["fighter_id"],
            draw_weight_scale=MOVE_WEIGHT_SCALE,
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


def _cancel_event(cancelled: list[dict[int, dict]], side: int, event: dict, reason: str) -> None:
    entry = cancelled[side].setdefault(
        int(event["ordinal"]), {"ordinal": int(event["ordinal"]), "gain": int(event["gain"]), "reasons": []}
    )
    if reason not in entry["reasons"]:
        entry["reasons"].append(reason)


def _domain_resolution(state: dict, seed: str, cancelled: list[dict[int, dict]]) -> dict | None:
    domains = [
        [event for event in side["turn"].get("events", ()) if "domain" in event.get("tags", ())]
        for side in state["sides"]
    ]
    active = [index for index, events in enumerate(domains) if events]
    if not active:
        return None
    version = state["version"]
    if len(active) == 2:
        fighters = [side["snapshot"]["fighter_id"] for side in state["sides"]]
        if fighters.count("sukuna") == 1:
            sukuna = fighters.index("sukuna")
            weights = [3, 3]
            weights[sukuna] = 4
            wheel = (("side-0", weights[0]), ("side-1", weights[1]), ("tie", 3))
        else:
            wheel = (("side-0", 1), ("side-1", 1), ("tie", 1))
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
    effect = ""
    if hit_side is not None and state["sides"][hit_side]["snapshot"]["fighter_id"] == "gojo":
        state["sides"][1 - hit_side]["next_debt"] += 1
        effect = "无量空处命中：对方下回合出招数-1"
    return {
        "mode": mode,
        "wheel": wheel,
        "outcome": outcome,
        "roll": roll,
        "winner": winner,
        "domain_counts": [len(events) for events in domains],
        "effect": effect,
    }


def _settle_interactions(state: dict, seed: str) -> dict:
    cancelled: list[dict[int, dict]] = [{}, {}]
    domain = _domain_resolution(state, seed, cancelled)
    for defender in (0, 1):
        if not state["sides"][defender]["turn"].get("infinity_used"):
            continue
        attacker = 1 - defender
        for event in state["sides"][attacker]["turn"].get("events", ()):
            if event.get("base", 0) <= 0 or int(event["ordinal"]) in cancelled[attacker]:
                continue
            _cancel_event(cancelled, attacker, event, "无下限·防御")
            break
    adjustments = []
    for side, entries in enumerate(cancelled):
        deduction = sum(entry["gain"] for entry in entries.values())
        state["sides"][side]["weight"] -= deduction
        adjustments.append(tuple(entries[key] for key in sorted(entries)))
    # 只有双领域战的明确胜者获得一次领域权重翻倍。若第一份领域数值被
    # 无下限抵消，则顺延到第一份仍有效的领域；同回合多开也只加一份。
    if domain is not None:
        domain.update(boost_side=None, boosted_ordinal=None, bonus_gain=0)
        winner = domain.get("winner")
        if domain.get("mode") == "clash" and winner in (0, 1):
            for event in state["sides"][winner]["turn"].get("events", ()):
                ordinal = int(event["ordinal"])
                if "domain" not in event.get("tags", ()) or ordinal in cancelled[winner]:
                    continue
                bonus = int(event["gain"])
                state["sides"][winner]["weight"] += bonus
                domain.update(boost_side=winner, boosted_ordinal=ordinal, bonus_gain=bonus)
                break
    return {"domain": domain, "adjustments": tuple(adjustments)}


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
