"""Battle v17 润化与黄瓜/账单状态；仅处理确定事实，不访问数据库。"""

from copy import deepcopy
from fractions import Fraction


def observation(player):
    """只观测有效的自身正向数值，不把对方减权或负数招式误当最高增益。"""
    events = deepcopy(player["turn"].get("events", []))
    numeric = [
        event
        for event in events
        if event.get("numeric_base") and Fraction(event.get("gain", 0)) > 0 and not event.get("effects_disabled")
    ]
    top = max(numeric, key=lambda event: (Fraction(event["gain"]), -event["ordinal"]), default=None)
    return {"events": events, "top": top, "weight": Fraction(player["weight"])}


def local_move(player, move_id, base, *, disabled=False, copied=False):
    """自身资源按招式顺序处理；指向对手的作用留到共同结算。"""
    turn = player["turn"]
    fact = {}
    if disabled or copied:
        return base, fact
    native = player["snapshot"]["fighter_id"]
    if base > 0:
        bonus = int(turn.get("miumiu_rebuild_bonus", 0))
        base += bonus
        if bonus:
            fact["rebuild_bonus"] = bonus
    if native == "miumiu" and move_id.startswith("miumiu-"):
        slug = move_id.removeprefix("miumiu-")
        humidity = int(player.get("miumiu_humidity", 0))
        fact.update(kind="miumiu", humidity_before=humidity)
        if slug == "mimic" and not turn.get("miumiu_preview"):
            top = turn.get("miumiu_observed", {}).get("top")
            if top:
                reference = max(Fraction(0), Fraction(top.get("gain", 0)))
                fact["mimic_reference"] = reference
                base = max(base, reference)
        elif slug == "softening":
            humidity += 2
        elif slug == "coupling":
            humidity += 1
            fact["next_numeric_reduction"] = 10
        elif slug == "water-life" and humidity:
            turn.setdefault("miumiu_recoveries", []).append(humidity)
            fact["recovery_layers"] = humidity
        elif slug == "rebuild":
            bonus = humidity * 2
            player["miumiu_rebuild_next"] = int(player.get("miumiu_rebuild_next", 0)) + bonus
            fact["next_rebuild_bonus"] = bonus
        elif slug == "disturb":
            fact["disturb_next"] = True
        elif slug == "blank":
            fact["blank_consumed"] = humidity + 2
            humidity = 0
        player["miumiu_humidity"] = humidity
        fact["humidity_after"] = humidity
    elif native == "luoli" and move_id.startswith("luoli-"):
        slug = move_id.removeprefix("luoli-")
        seen = turn.setdefault("luoli_seen", [])
        cucumbers, bills = 0, 0
        if slug == "soba":
            bills, cucumbers = 1, int("parfait" in seen)
        elif slug == "parfait":
            bills, cucumbers = 2, int("soba" in seen)
        elif slug == "with-cat":
            player["luoli_next_bill_bonus"] = int(player.get("luoli_next_bill_bonus", 0)) + 1
        elif slug in {"mutsumi", "mortis"}:
            cucumbers, bills = 2, int(bool({"soba", "parfait"}.intersection(seen)))
        elif slug == "guitar":
            bills, cucumbers = 1, 2 + int(bool({"mutsumi", "mortis"}.intersection(seen)))
        elif slug == "matcha-feast":
            bills = 4 + int("with-cat" in seen)
        elif slug == "joint-live":
            cucumbers = 4 + int(bool({"mutsumi", "mortis"}.intersection(seen)))
        elif slug == "pet-cat" and "pet-cat" not in seen:
            turn["luoli_heart"] = True
            fact["allergy"] = True
        elif slug == "soyo":
            before = player.get("injury_state", "heavy" if player.get("heavy") else "none")
            if before == "heavy":
                player.update(heavy=False, risk=1, injury_state="light")
            elif before == "light":
                player.update(heavy=False, risk=0, injury_state="none")
            fact.update(injury_before=before, injury_after=player.get("injury_state", "none"))
        if bills:
            bills += int(player.pop("luoli_next_bill_bonus", 0))
        generation = 2 if turn.get("luoli_sleep_bonus") else 1
        cucumbers *= generation
        bills *= generation
        player["luoli_cucumbers"] = int(player.get("luoli_cucumbers", 0)) + cucumbers
        fact.update(
            kind="luoli",
            cucumbers_generated=cucumbers,
            bills_generated=bills,
            cucumbers_after=player["luoli_cucumbers"],
            generation_multiplier=generation,
        )
        seen.append(slug)
    return base, fact


def _numeric(events, remaining, cancelled, side):
    return [event for event in events if event.get("numeric_base") and remaining(cancelled, side, event) > 0]


def _add_gain(player, event, amount):
    amount = Fraction(amount)
    event["gain"] += amount
    event["has_numeric_contribution"] = event["gain"] != 0
    player["weight"] += amount
    for later in player["turn"]["events"]:
        if later["ordinal"] >= event["ordinal"]:
            later["total"] += amount


def prepare_interactions(state, domain, cancelled, protected, seed, *, choose, remaining, reduce, cancel):
    """冻结双方完整招式后统一处理，真实消息先后不能成为优势。"""
    records = []
    sides = state["sides"]
    for source, player in enumerate(sides):
        target = 1 - source
        opponent = sides[target]
        target_events = opponent["turn"]["events"]
        queued = int(opponent.pop("miumiu_pending_reductions", 0))
        if target in protected:
            queued = 0
        if queued:
            candidates = _numeric(target_events, remaining, cancelled, target)
            if candidates:
                applied = reduce(cancelled, target, candidates[0], queued, "生态耦合·跨回合")
                records.append(
                    {"side": source, "text": f"上回合生态耦合：对方第{candidates[0]['ordinal']}招减少{applied}"}
                )
            else:
                opponent["miumiu_pending_reductions"] = queued
        for event in player["turn"]["events"]:
            fact = event.get("mirror", {})
            if not fact or target in protected:
                continue
            bills = int(fact.get("bills_generated", 0))
            opponent["luoli_bills"] = int(opponent.get("luoli_bills", 0)) + bills
            if fact.get("allergy"):
                opponent["turn"]["luoli_allergy"] = True
            if fact.get("disturb_next"):
                opponent["miumiu_disturbed_next"] = True
            if fact.get("next_numeric_reduction"):
                candidates = [
                    e for e in _numeric(target_events, remaining, cancelled, target) if e["ordinal"] > event["ordinal"]
                ]
                if candidates:
                    applied = reduce(cancelled, target, candidates[0], 10, "生态耦合")
                    records.append(
                        {"side": source, "text": f"生态耦合：对方第{candidates[0]['ordinal']}招减少{applied}"}
                    )
                else:
                    opponent["miumiu_pending_reductions"] = int(opponent.get("miumiu_pending_reductions", 0)) + 10
                    records.append(
                        {"side": source, "text": "生态耦合：本回合没有后续数值招式，-10保留到下一次数值招式"}
                    )
            if event.get("functional_move_id") == "miumiu-shallow":
                candidates = _numeric(target_events, remaining, cancelled, target)
                if candidates:
                    ordinal, roll = choose(
                        seed,
                        f"{state['round']}:mirror:shallow:{source}:{event['ordinal']}",
                        tuple((e["ordinal"], 1) for e in candidates),
                        version=state["version"],
                    )
                    chosen = next(e for e in candidates if e["ordinal"] == ordinal)
                    applied = cancel(cancelled, target, chosen, "浅层非熵适应")
                    records.append(
                        {
                            "side": source,
                            "text": f"浅层非熵适应：对方第{ordinal}招数值归零，功能保留",
                            "target_ordinal": ordinal,
                            "roll": roll,
                            "cancelled_gain": applied,
                        }
                    )
    # 观测只读取共同基础预演，不让两只缪缪彼此反复放大。
    observations = state.get("miumiu_observations", [])
    for source, player in enumerate(sides):
        top = observations[1 - source].get("top") if observations else None
        for event in tuple(player["turn"]["events"]):
            if event.get("functional_move_id") != "miumiu-observe" or not top or not event.get("mirror"):
                continue
            candidates = _numeric(player["turn"]["events"], remaining, cancelled, source)
            if not candidates:
                continue
            chosen = max(candidates, key=lambda e: (remaining(cancelled, source, e), -e["ordinal"]))
            bonus = max(Fraction(0), Fraction(top.get("gain", 0))) / 4
            _add_gain(player, chosen, bonus)
            records.append(
                {
                    "side": source,
                    "text": f"流形·观测：自身第{chosen['ordinal']}招追加{bonus}",
                    "target_ordinal": chosen["ordinal"],
                    "bonus": bonus,
                }
            )
    for side, player in enumerate(sides):
        turn = player["turn"]
        hit_sleep = bool(domain and domain.get("hit_side") == side and "luoli-sleep" in domain["domain_ids"][side])
        if hit_sleep:
            turn["luoli_sleep_hit"] = True
            player["luoli_sleep_next"] = True
            domain["mirror_effects"] = ["睡觉命中：本回合获取与重伤/力竭权重减半，下回合获取与黄瓜/账单生成翻倍"]
        cucumbers = int(player.get("luoli_cucumbers", 0))
        bills = int(player.get("luoli_bills", 0))
        factor = max(
            Fraction(0),
            1
            + Fraction(cucumbers, 10) * (2 if turn.get("luoli_heart") else 1)
            - Fraction(bills, 20) * (2 if turn.get("luoli_allergy") else 1),
        )
        factor *= 2 if turn.get("luoli_sleep_bonus") else 1
        factor *= Fraction(1, 2) if hit_sleep else 1
        factor *= Fraction(4, 5) if turn.get("miumiu_disturbed") else 1
        turn["mirror_gain_factor"] = factor
        if factor != 1:
            for event in player["turn"]["events"]:
                effective = remaining(cancelled, side, event)
                if effective <= 0:
                    continue
                if factor > 1:
                    _add_gain(player, event, effective * (factor - 1))
                else:
                    reduce(cancelled, side, event, effective * (1 - factor), "本回合正向获取倍率")
            records.append(
                {"side": side, "text": f"本回合正向获取×{factor}；黄瓜{cucumbers}、账单{bills}", "factor": factor}
            )
    return records


def gain(player, amount):
    """领域追加和回合后置奖励同样遵守本回合正向获取倍率。"""
    value = Fraction(amount)
    return value * Fraction(player["turn"].get("mirror_gain_factor", 1)) if value > 0 else value


def final_effects(state, domain, protected):
    records = []
    if not domain or domain.get("hit_side") not in (0, 1):
        return records
    side = domain["hit_side"]
    target = 1 - side
    if target in protected:
        return records
    player = state["sides"][side]
    opponent = state["sides"][target]
    for event in player["turn"]["events"]:
        consumed = int(event.get("mirror", {}).get("blank_consumed", 0))
        if not consumed or not event.get("domain_eligible", True):
            continue
        before = Fraction(opponent["weight"])
        opponent["weight"] = max(Fraction(1, 10), before - consumed * 10)
        debt = int(consumed > 5)
        opponent["next_debt"] += debt
        records.append(
            {
                "side": side,
                "text": f"（空白）消耗{consumed}层润化：对方最终点数-{before - opponent['weight']}，下回合-{debt}招",
                "consumed": consumed,
                "before": before,
                "after": opponent["weight"],
                "next_debt": debt,
            }
        )
    return records


def finish_round(player):
    turn = player["turn"]
    before = {"cucumbers": int(player.get("luoli_cucumbers", 0)), "bills": int(player.get("luoli_bills", 0))}
    player["luoli_cucumbers"] = before["cucumbers"] if turn.get("luoli_heart") else max(0, before["cucumbers"] - 5)
    player["luoli_bills"] = before["bills"] if turn.get("luoli_allergy") else max(0, before["bills"] - 5)
    next_turn = {
        "luoli_sleep_bonus": bool(player.pop("luoli_sleep_next", False)),
        "miumiu_disturbed": bool(player.pop("miumiu_disturbed_next", False)),
        "miumiu_rebuild_bonus": int(player.pop("miumiu_rebuild_next", 0)),
    }
    return deepcopy(next_turn)
