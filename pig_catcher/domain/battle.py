"""可恢复、精确整数、顺序无关的双人轮盘纯状态机。无I/O、时间或全局随机。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .battle_catalog import (
    BATTLE_VERSION,
    COUNT_WHEEL,
    FIGHTERS_BY_ID,
    HEAVY_COUNT_WHEEL,
    INJURY_WHEELS,
    LOOT_WEIGHTS,
    MOVE_CHUNK_SIZE,
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


def randbelow(seed: str, key: str, bound: int) -> int:
    """带命名空间的拒绝采样；不取模、不转浮点，支持任意大的胜利权重。"""
    if type(bound) is not int or bound < 1:
        raise BattleError("轮盘总权重必须是正整数。")
    bits = (bound - 1).bit_length()
    if bits == 0:
        return 0
    size, attempt = (bits + 7) // 8, 0
    while True:
        raw = hashlib.shake_256(f"{BATTLE_VERSION}|{seed}|{key}|{attempt}".encode()).digest(size)
        number = int.from_bytes(raw, "big") & ((1 << bits) - 1)
        if number < bound:
            return number
        attempt += 1


def choose(seed: str, key: str, wheel: tuple) -> tuple[Any, int]:
    roll = randbelow(seed, key, sum(weight for _, weight in wheel))
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
    player["turn"].setdefault("ready", False)
    return player


def roll_count(state: dict, side: int, seed: str) -> dict:
    player = _side(state, side)
    turn = player["turn"]
    if turn["raw"] is not None:
        return {"changed": False, **deepcopy(turn)}
    wheel = HEAVY_COUNT_WHEEL if player["heavy"] else COUNT_WHEEL
    raw, roll = choose(seed, f"{state['round']}:{side}:count", wheel)
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
    trait = int(numeric and snapshot.get("trait_bonus", 0) and not turn["trait_used"])
    tool_gain = 2 if numeric and tool == "wristband" else 0
    used_tool = bool(numeric and (tool == "wristband" or (tool == "bandage" and player["heavy"])))
    gain = base_gain * multiplier + trait + tool_gain
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
        "tool_used": snapshot.get("tool_id", "") if used_tool else "",
        "gain": gain,
        "total": player["weight"],
        "extra_draws": move.draws,
        "loan": move.loan,
        "double_pending": player["double"],
        "next_debt": player["next_debt"],
        "pending": turn["pending"],
    }


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
        index, roll = choose(
            seed,
            f"{state['round']}:{side}:move:{ordinal}",
            tuple((index, move.draw_weight) for index, move in enumerate(moves)),
        )
        event = apply_move(player, moves[index])
        event.update(roll=roll, round=state["round"], side=side, fighter_id=player["snapshot"]["fighter_id"])
        events.append(event)
    return events


def mark_ready(state: dict, side: int) -> dict:
    """玩家看完双方招式后确认结算；只有双方都确认，回合才可抽胜负。"""

    player = _side(state, side)
    for other in state["sides"]:
        other["turn"].setdefault("ready", False)
    if not all(other["turn"]["done"] for other in state["sides"]):
        raise BattleError("请等待双方都出完招，再输入 /会赢的。")
    if player["turn"]["ready"]:
        return {"changed": False, "side": side, "round": state["round"]}
    player["turn"]["ready"] = True
    return {"changed": True, "side": side, "round": state["round"]}


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


def resolve_round(state: dict, seed: str) -> dict | None:
    _side(state, 0)
    for side in state["sides"]:
        side["turn"].setdefault("ready", False)
    if not all(side["turn"]["done"] and side["turn"]["ready"] for side in state["sides"]):
        return None
    before = deepcopy(state["sides"])
    roll = randbelow(seed, f"{state['round']}:winner", sum(side["weight"] for side in before))
    winner = 0 if roll < before[0]["weight"] else 1
    loser = 1 - winner
    wheel = INJURY_WHEELS[before[loser]["risk"]]
    injury, injury_roll = choose(seed, f"{state['round']}:{loser}:injury", wheel)
    apply_injury(state["sides"][loser], injury)
    result = {
        "round": state["round"],
        "winner": winner,
        "loser": loser,
        "winner_roll": roll,
        "injury": injury,
        "injury_roll": injury_roll,
        "injury_wheel": wheel,
        "before": before,
        "after": deepcopy(state["sides"]),
        "natural_end": injury == "exhausted",
    }
    if injury == "exhausted":
        state.update(status="completed", winner=winner)
    else:
        state["round"] += 1
        for player in state["sides"]:
            player["turn"] = fresh_turn()
    return result


def loot_weights(*, level: int, feed: int, cloud: int, six_available: bool) -> tuple[float, ...]:
    weights = catch_weights(LOOT_WEIGHTS, player_level=level, feed_level=feed, six_star_available=six_available)
    return apply_six_star_progress(weights, stacks=cloud, bonus_per_stack=0.2, action="catch")
