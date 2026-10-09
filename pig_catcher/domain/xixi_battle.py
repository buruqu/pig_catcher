"""西西 Battle v19 本源资源和共同结算事实；无 I/O，无浮点抽签。

玩家字段和事件可以直接使用 battle.dumps/loads 保存。随机性仅由调用者
注入的 choose 决定；复制招式永远不能创建海克斯、装备冷却或帝境资源。
"""

from copy import deepcopy
from fractions import Fraction

from .xixi_battle_catalog import (
    BACK_TO_BASICS_SIMPLE_DOMAIN_CHANCE,
    EMPEROR_GAIN,
    EQUIPMENT_IDS,
    HEXTECH_NAMES,
    HEXTECH_SPECS,
    XIXI_FORM_CELESTIAL,
    XIXI_FORM_EMPEROR,
)


def native(player):
    return player.get("snapshot", {}).get("fighter_id") == "xixi"


def ensure_player(player):
    """只为原生西西创建资源；拟态/复制不可据此获得源授权。"""
    if not native(player):
        return False
    for key, value in {
        "xixi_form": XIXI_FORM_CELESTIAL,
        "xixi_mark": 0,
        "xixi_overload_bonus": 0,
        "xixi_overload_weight_units": 0,
        "xixi_hextech": [],
        "xixi_hextech_queue": [],
        "xixi_cooldowns": {},
        "xixi_shield": False,
        "xixi_goliath_gain": Fraction(0),
        "xixi_permanent_action_bonus": 0,
        "xixi_core_hextech_results": {},
    }.items():
        player.setdefault(key, deepcopy(value))
    return True


def emperor(player):
    return native(player) and player.get("xixi_form") == XIXI_FORM_EMPEROR


def count_wheel(player, wheel):
    if not native(player):
        return wheel
    ratio = min(Fraction(200), max(Fraction(0), Fraction(player.get("weight", 0)))) / 200
    return tuple((n, Fraction(weight) * (1 + ratio * (n - 1))) for n, weight in wheel)


def move_weight_units(player, move, round_number, base_units=None):
    units = Fraction(move.resolved_draw_weight_units if base_units is None else base_units)
    if not native(player):
        return units
    ensure_player(player)
    if move.move_id in EQUIPMENT_IDS:
        if emperor(player) or int(player["xixi_cooldowns"].get(move.move_id, 0)) >= round_number:
            return Fraction(0)
    if move.move_id == "xixi-overload":
        units += int(player["xixi_overload_weight_units"])
    return max(Fraction(0), units)


def permanent_gain(player):
    if not native(player):
        return 0
    total = sum(gain for key, _name, gain, _w in HEXTECH_SPECS if key in player.get("xixi_hextech", ()))
    return total + (EMPEROR_GAIN if emperor(player) else 0)


def action_bonus(player):
    return int(player.get("xixi_permanent_action_bonus", 0)) if native(player) else 0


def simple_domain_chance(player):
    """回归基本功只增强简易领域，不让失去资格的大招重入领域战。"""
    if native(player) and "back-to-basics" in player.get("xixi_hextech", ()):
        return BACK_TO_BASICS_SIMPLE_DOMAIN_CHANCE
    return None


def local_move(player, move_id, base, round_number, *, automatic=False, disabled=False, copied=False):
    """在主引擎计算等级等通用收益之前调用，返回基础数值与可审计功能事实。

    符文禁锢的 auto_overload 由引擎紧接本事件后施放，不消费 pending；
    event_context 接收同一 fact，finalize_turn 在完整出招结束后回溯被动。
    """
    if disabled or copied or not native(player) or not move_id.startswith("xixi-"):
        return Fraction(base), {}
    ensure_player(player)
    turn = player["turn"]
    before = min(1, int(player["xixi_mark"]))
    fact = {
        "native": True,
        "mark_before": before,
        "automatic": automatic,
        "form_before": player["xixi_form"],
        "permanent_gain": permanent_gain(player),
    }
    value = Fraction(base) + fact["permanent_gain"]
    if move_id == "xixi-surge":
        if turn.get("xixi_previous_move") == "xixi-surge":
            turn["xixi_surge_chain_bonus"] = int(turn.get("xixi_surge_chain_bonus", 0)) + 4
            fact["consecutive_surge_bonus"] = 4
        player["xixi_mark"] = 1
        player["xixi_overload_weight_units"] += 3500
    elif move_id == "xixi-prison":
        fact["ignore_count"] = 1
        player["xixi_overload_weight_units"] += 3500
        if before:
            player["xixi_mark"] = 0
            fact.update(auto_overload=True, opponent_next_debt=1, mark_consumed=True)
    elif move_id == "xixi-overload":
        value += int(player["xixi_overload_bonus"])
        fact["overload_growth"] = int(player["xixi_overload_bonus"])
        if before:
            value *= 2
            player["xixi_mark"] = 0
            fact["mark_consumed"] = True
        if not automatic:
            fact["overload_weight_cleared"] = int(player["xixi_overload_weight_units"])
            player["xixi_overload_weight_units"] = 0
        turn["xixi_overload_count"] = int(turn.get("xixi_overload_count", 0)) + 1
    elif move_id in EQUIPMENT_IDS:
        if emperor(player) or int(player["xixi_cooldowns"].get(move_id, 0)) >= round_number:
            raise ValueError("西西装备处于冷却中或已被帝境移除")
        player["xixi_cooldowns"][move_id] = round_number + 1
        fact["cooldown_until"] = round_number + 1
        if move_id == "xixi-reality":
            turn["xixi_reality"] = True
            fact["reality"] = True
        else:
            turn["xixi_hourglass"] = True
            fact["stop"] = True
    elif move_id == "xixi-sidestep":
        fact["ignore_count"] = 1
    elif move_id == "xixi-realm-warp":
        player["xixi_overload_bonus"] += 6
        fact["overload_growth_after"] = int(player["xixi_overload_bonus"])
        if "back-to-basics" in player["xixi_hextech"]:
            fact.update(domain_eligible=False, delayed_hextech_round=round_number + 3)
            token = f"{round_number}:{int(turn.get('draws', 0))}"
            item = {"due": round_number + 3, "token": token}
            if not any(q["token"] == token for q in player["xixi_hextech_queue"]):
                player["xixi_hextech_queue"].append(item)
        else:
            fact["domain_followup"] = True
    turn["xixi_previous_move"] = move_id
    fact.update(
        mark_after=int(player["xixi_mark"]),
        overload_weight_after=int(player["xixi_overload_weight_units"]),
        form_after=player["xixi_form"],
    )
    return value, fact


def event_context(player, event, fact):
    event["xixi"] = deepcopy(fact)
    if not fact:
        return
    event["opponent_next_debt"] = int(event.get("opponent_next_debt", 0)) + int(fact.get("opponent_next_debt", 0))
    if fact.get("domain_eligible") is False:
        event["domain_eligible"] = False
    if fact.get("stop"):
        turn = player["turn"]
        event["xixi_stopped_pending"] = int(turn.get("pending", 0))
        turn.update(pending=0, done=True)
        event["pending"] = 0


def _add_gain(player, event, amount):
    amount = Fraction(amount)
    player["weight"] = Fraction(player["weight"]) + amount
    event["gain"] = Fraction(event.get("gain", 0)) + amount
    event["has_numeric_contribution"] = event["gain"] != 0
    # 保持战报累计值一致；当前event可能尚未append。
    for later in player["turn"]["events"]:
        if later["ordinal"] >= event["ordinal"]:
            later["total"] = Fraction(later.get("total", 0)) + amount


def finalize_turn(player):
    """对最终有效的整轮总招数统一加成；幂等，不以动作先后提供优势。"""
    if not native(player):
        return []
    turn = player["turn"]
    total = int(turn.get("draws", 0)) + (0 if turn.get("xixi_hourglass") else int(turn.get("pending", 0)))
    bonus = max(0, total - 1) * 2 + int(turn.get("xixi_surge_chain_bonus", 0))
    records = []
    for event in turn.get("events", ()):
        if not event.get("xixi", {}).get("native") or event.get("effects_disabled"):
            continue
        previous = Fraction(event.get("xixi_all_moves_bonus", 0))
        delta = bonus - previous
        if delta:
            _add_gain(player, event, delta)
            event["xixi_all_moves_bonus"] = bonus
            records.append({"ordinal": event["ordinal"], "bonus": bonus, "delta": delta, "effective_total": total})
    if int(turn.get("xixi_overload_count", 0)) >= 2 and not turn.get("xixi_overload_next_granted"):
        player["next_action_bonus"] = int(player.get("next_action_bonus", 0)) + 1
        turn["xixi_overload_next_granted"] = True
        records.append({"overload_next_action_bonus": 1})
    turn["xixi_effective_total"] = total
    return records


def gain_hextech(player, seed, key, *, choose, version=19):
    if not ensure_player(player):
        return {"available": False, "reason": "not-native"}
    pool = tuple((item, weight) for item, _name, _gain, weight in HEXTECH_SPECS if item not in player["xixi_hextech"])
    if not pool:
        return {"available": False, "reason": "empty", "wheel": ()}
    selected, roll = choose(seed, key, pool, version=version)
    if selected not in dict(pool):
        raise ValueError("海克斯抽签返回池外结果")
    player["xixi_hextech"].append(selected)
    fact = {
        "available": True,
        "hextech": selected,
        "name": HEXTECH_NAMES[selected],
        "roll": roll,
        "wheel": pool,
        "count": len(player["xixi_hextech"]),
    }
    if selected == "mind-over-matter":
        player["xixi_shield"] = True
    elif selected == "goliath":
        # 起点仅记本轮当前有效事件的标识，后续共同结算从这些事件之后开始累计。
        player["xixi_goliath_gain"] = Fraction(0)
        player["xixi_goliath_acquired"] = {
            "round": int(player["turn"].get("round", 0)),
            "ordinal": int(player["turn"].get("draws", 0)),
        }
        player["turn"]["xixi_goliath_acquired_this_turn"] = True
    elif selected == "back-to-basics":
        player["xixi_permanent_action_bonus"] = 1
    if len(player["xixi_hextech"]) == len(HEXTECH_SPECS):
        player.update(xixi_form=XIXI_FORM_EMPEROR, heavy=False, risk=0, injury_state="none")
        fact.update(
            emperor=True,
            fixed_injury_wheel=(("none", 99), ("exhausted", 1)),
            all_moves_gain=EMPEROR_GAIN,
            removed_moves=tuple(sorted(EQUIPMENT_IDS)),
        )
    fact["hextech_after"] = tuple(player["xixi_hextech"])
    return fact


def begin_round(player, round_number, seed="", *, choose=None, version=19):
    if not ensure_player(player):
        return []
    player["turn"]["round"] = round_number
    records, pending = [], []
    for queued in player["xixi_hextech_queue"]:
        if queued["due"] <= round_number and choose is not None:
            result = gain_hextech(
                player, seed, f"{round_number}:xixi:delayed:{queued['token']}", choose=choose, version=version
            )
            records.append({"token": queued["token"], "due": queued["due"], "result": result})
            if result.get("hextech") == "goliath":
                # 延迟抽取在开轮时兑现；这一轮所有敌方有效收益都在取得之后。
                player["turn"]["xixi_goliath_acquired_this_turn"] = False
                result["acquisition_phase"] = "round-start"
        else:
            pending.append(queued)
    player["xixi_hextech_queue"] = pending
    if records:
        player["turn"].setdefault("xixi_delayed_hextech", []).extend(deepcopy(records))
    return records


def domain_hit(player, event, round_number, seed, *, choose, version=19):
    if not native(player) or not event.get("xixi", {}).get("domain_followup"):
        return {}
    if event.get("xixi_domain_followup_done"):
        return {}
    event["xixi_domain_followup_done"] = True
    player["next_action_bonus"] = int(player.get("next_action_bonus", 0)) + 1
    fact = {
        "ignore_count": 2,
        "next_action_bonus": 1,
        "hextech": gain_hextech(
            player, seed, f"{round_number}:xixi:domain:{event['ordinal']}", choose=choose, version=version
        ),
    }
    event["xixi_domain_hit"] = deepcopy(fact)
    return fact


def core_hextech(player, round_number, seed, *, choose, version=19):
    if not ensure_player(player):
        return {"available": False, "reason": "not-native"}
    key = f"{round_number}:xixi:core:{player.get('core', 0)}"
    cached = player["xixi_core_hextech_results"].get(key)
    if cached is not None:
        return {**deepcopy(cached), "replayed": True}
    fact = gain_hextech(player, seed, key, choose=choose, version=version)
    player["xixi_core_hextech_results"][key] = deepcopy(fact)
    return fact


def injury_guard(player, *, consume=False):
    if not native(player):
        return None
    if player["turn"].get("xixi_hourglass"):
        return "中亚沙漏"
    if player.get("xixi_shield"):
        if consume:
            player["xixi_shield"] = False
        return "由心及物"
    if "goliath" in player.get("xixi_hextech", ()) and Fraction(player.get("xixi_goliath_gain", 0)) < 100:
        return "歌莉娅巨人"
    return None


def fixed_injury_wheel(player):
    """固定盘不允许领域强制力竭或伤势修正覆写落点。"""
    return (("none", Fraction(99)), ("exhausted", Fraction(1))) if emperor(player) else None


def injury_wheel(player, wheel):
    """必须作为最终覆写调用：帝境固定盘不能被其他效果再次修正。"""
    fixed = fixed_injury_wheel(player)
    if fixed is not None:
        return fixed
    if native(player) and "back-to-basics" in player.get("xixi_hextech", ()):
        return tuple(
            (kind, Fraction(w) / 2 if kind in {"light", "heavy", "exhausted"} else Fraction(w)) for kind, w in wheel
        )
    return wheel


def gain(player, amount):
    value = Fraction(amount)
    factor = Fraction(player["turn"].get("xixi_positive_gain_factor", 1))
    return value * factor if value > 0 else value


def prepare_interactions(state, domain, cancelled, protected, seed, *, choose, remaining, reduce, cancel):
    """双方完整事件快照：无视选取不依赖另一方消息是否已输入。

    主引擎须把 xixi_hourglass side 加入 directed-suppressed 集合而非
    全面protected集合，以保留敌方赠予自己的功能增益。
    """
    sides = state["sides"]
    snapshots = [tuple(p["turn"].get("events", ())) for p in sides]
    records = []
    for side, player in enumerate(sides):
        if native(player):
            records.extend({"side": side, **r} for r in finalize_turn(player))
    # 先冻结所有候选序号，再取消：双方的无视不相互删去候选。
    candidates = [[e for e in events if not e.get("effects_disabled")] for events in snapshots]
    for side, player in enumerate(sides):
        if not native(player):
            continue
        target = 1 - side
        for event in snapshots[side]:
            if event.get("daniya_current_cancelled"):
                continue
            requested = int(event.get("xixi", {}).get("ignore_count", 0))
            if domain and domain.get("hit_side") == side and event.get("xixi", {}).get("domain_followup"):
                hit = domain_hit(player, event, state["round"], seed, choose=choose, version=state["version"])
                requested += int(hit.get("ignore_count", 0))
                if hit:
                    records.append({"side": side, "domain_hit": hit, "ordinal": event["ordinal"]})
            if target in protected:
                continue
            pool = list(candidates[target])
            for index in range(min(requested, len(pool))):
                ordinal, roll = choose(
                    seed,
                    f"{state['round']}:xixi:ignore:{side}:{event['ordinal']}:{index}",
                    tuple((e["ordinal"], 1) for e in pool),
                    version=state["version"],
                )
                chosen = next(e for e in pool if e["ordinal"] == ordinal)
                pool.remove(chosen)
                amount = cancel(cancelled, target, chosen, "西西无视")
                # cancel只管理数值账；禁止伤害/debuff，功能赠益仍可由核心识别保留。
                chosen["xixi_ignored"] = True
                records.append(
                    {
                        "side": side,
                        "target_side": target,
                        "target_ordinal": ordinal,
                        "roll": roll,
                        "cancelled_gain": amount,
                        "ignore": True,
                    }
                )
    for side, player in enumerate(sides):
        own_reality = bool(player["turn"].get("xixi_reality"))
        enemy = sides[1 - side]
        enemy_reality = bool(enemy["turn"].get("xixi_reality"))
        factor = (Fraction(13, 10) if own_reality else 1) * (Fraction(7, 10) if enemy_reality else 1)
        if enemy["turn"].get("xixi_hourglass"):
            factor = Fraction(0)
        turn = player["turn"]
        turn["xixi_positive_gain_factor"] = Fraction(factor)
        turn["xixi_reduction_factor"] = Fraction(factor)
        if turn.get("xixi_factors_applied"):
            continue
        turn["xixi_factors_applied"] = True
        for event in snapshots[side]:
            value = max(Fraction(0), Fraction(remaining(cancelled, side, event)))
            if factor > 1 and value:
                amount = value * (factor - 1)
                _add_gain(player, event, amount)
            elif factor < 1 and value:
                amount = reduce(cancelled, side, event, value * (1 - factor), "西西整轮收益倍率")
            else:
                amount = Fraction(0)
            reduction = Fraction(event.get("opponent_reduction", 0))
            if reduction > 0:
                event["opponent_reduction"] = reduction * factor
            event["xixi_gain_factor"] = Fraction(factor)
            event["xixi_reduction_factor"] = Fraction(factor)
            if amount or factor != 1:
                records.append(
                    {"side": side, "ordinal": event["ordinal"], "factor": Fraction(factor), "amount": amount}
                )
    return records


def observe_effective_enemy_gain(player, amount, *, round_number=None):
    """共同结算完成后调用，amount须是获取巨人后敌方真正有效正收益。"""
    if not native(player) or "goliath" not in player.get("xixi_hextech", ()):
        return {}
    if round_number is not None and player.get("xixi_goliath_observed_round") == round_number:
        return {}
    before = Fraction(player.get("xixi_goliath_gain", 0))
    added = max(Fraction(0), Fraction(amount))
    player["xixi_goliath_gain"] = before + added
    if round_number is not None:
        player["xixi_goliath_observed_round"] = round_number
    return {
        "before": before,
        "effective_enemy_gain": added,
        "after": before + added,
        "guard_active": before + added < 100,
    }
