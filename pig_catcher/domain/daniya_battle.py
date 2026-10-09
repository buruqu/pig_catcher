"""达妮娅 v19 粒子、三形态与跨方结算；精确数值和可恢复来源事实。"""

from fractions import Fraction

from .daniya_catalog import BLACK_HOLE

PARTICLE_LIMIT = Fraction(25)
FIXED_BLACK_HOLE_WHEEL = (("none", 8230), ("injured", 1249), ("exhausted", 521))


def ensure_player(player):
    for key, value in {
        "daniya_particles": Fraction(0),
        "daniya_particles_total": Fraction(0),
        "daniya_permanent_reduction": Fraction(0),
        "daniya_domain_hits": 0,
        "daniya_transform_staging_units": 0,
        "daniya_transform_disillusion_units": 0,
        "daniya_permanent_domain_units": 0,
    }.items():
        player.setdefault(key, value)


def add_particles(player, amount, *, form=None):
    ensure_player(player)
    amount = max(Fraction(0), Fraction(amount))
    form = form or player.get("daniya_form", "staging")
    coefficient = Fraction(823, 1000) if form == BLACK_HOLE else Fraction(521, 1000)
    player["daniya_particles"] += amount
    player["daniya_particles_total"] += amount
    turn = player.setdefault("turn", {})
    factor = int(turn.get("daniya_debuff_factor", 1))
    raw = amount * coefficient
    turn["daniya_permanent_grant_raw"] = Fraction(turn.get("daniya_permanent_grant_raw", 0)) + raw
    player["daniya_permanent_reduction"] += raw * factor
    return {"added": amount, "coefficient": coefficient, "factor": factor, "permanent_added": raw * factor}


def enter_black_hole(player):
    ensure_player(player)
    if player.get("daniya_form") == BLACK_HOLE:
        return None
    before = player.get("daniya_form", "staging")
    player["daniya_form"] = BLACK_HOLE
    player["heavy"], player["risk"], player["injury_state"] = False, 0, "none"
    supplement = max(Fraction(0), PARTICLE_LIMIT - player["daniya_particles"])
    fact = add_particles(player, supplement, form=BLACK_HOLE)
    return {"before": before, "after": BLACK_HOLE, "supplement": fact}


def local_event(player, event, *, version):
    if version < 19 or "daniya-v19" not in (*event.get("functional_tags", ()), *event.get("tags", ())):
        return
    ensure_player(player)
    source_id = event.get("functional_move_id") or event["move_id"]
    form = next(f for f in ("staging", "disillusion", BLACK_HOLE) if source_id.startswith(f"daniya-{f}-"))
    event["daniya_source_form"] = form
    if event["move_id"] == source_id:
        event["form_before"] = form
    native = player["snapshot"]["fighter_id"] == "daniya" and not event.get("copy_context")
    suffix = source_id.removeprefix(f"daniya-{form}-")
    event.update(daniya_v19=True, daniya_native=native, daniya_particle_before=player["daniya_particles"])
    if native and form == BLACK_HOLE and not event.get("effects_disabled"):
        factor = 1 + player["daniya_particles"] * Fraction(2, 25)
        gain = Fraction(event.get("gain", 0))
        event["gain"] = gain * factor
        player["weight"] += event["gain"] - gain
        event["opponent_reduction"] = Fraction(event.get("opponent_reduction", 0)) * factor
        event["daniya_black_hole_factor"] = factor
    # 来源专属成长先落账，不受数值招被无视影响；复制不获得来源资源。
    if native:
        before_domain = int(player.get("daniya_domain_steps", 0))
        if suffix == "domain" and form == "staging":
            player["daniya_domain_steps"] = 0
            player["daniya_domain_draw_only_steps"] = 0
            player["turn"]["domain_clash_bonus_units"] += before_domain + int(
                player.pop("daniya_domain_clash_only_steps", 0)
            )
            event["daniya_domain_carried_units"] = before_domain
        if form == "staging":
            player["daniya_domain_steps"] = int(player.get("daniya_domain_steps", 0)) + 3
            player["daniya_transform_staging_units"] += 800
        elif form == "disillusion":
            player["daniya_transform_disillusion_units"] += 500
            player["daniya_unignorable_exhaust_units"] = Fraction(player.get("daniya_unignorable_exhaust_units", 0)) + 3
        event["daniya_particle_grant"] = add_particles(player, Fraction(1, 2), form=form)
    if not native and suffix == "domain" and form == "staging":
        player["turn"]["domain_clash_bonus_units"] += int(player.pop("daniya_domain_clash_only_steps", 0))
    if not event.get("effects_disabled"):
        if form == "staging":
            if suffix == "flawless":
                player["turn"]["domain_clash_bonus_units"] += 4
            elif suffix == "lie":
                player["turn"]["opponent_domain_clash_reduction_units"] += 4
            elif suffix == "timer":
                player["daniya_permanent_domain_units"] += 10
            elif suffix == "world-work":
                player["daniya_domain_clash_only_steps"] = int(player.get("daniya_domain_clash_only_steps", 0)) + 10
        elif form == "disillusion":
            extra = Fraction(0)
            if suffix == "flawless":
                event["opponent_next_debt"] += 3
                event["opponent_exhaust_bonus_units"] += 5
                extra = Fraction(1)
            elif suffix == "lie":
                event["daniya_ignore_count"] = 2
                player["turn"]["daniya_injury_recovery_layers"] = (
                    int(player["turn"].get("daniya_injury_recovery_layers", 0)) + 1
                )
                player["turn"]["daniya_debuff_double"] = int(player["turn"].get("daniya_debuff_double", 0)) + 1
                extra = Fraction(1)
            elif suffix == "timer":
                event["opponent_exhaust_bonus_units"] += 50
                extra = Fraction(2)
            elif suffix == "world-work":
                event["opponent_exhaust_bonus_units"] += 5
                extra = Fraction(3, 2)
            if extra and native:
                event["daniya_extra_particle_grant"] = add_particles(player, extra, form=form)
        elif form == BLACK_HOLE:
            event["daniya_current_move_debt"] = {
                "red-supergiant": 3,
                "wolf-rayet": 4,
                "iron-supernova": 5,
            }.get(suffix, 0)
            event["daniya_force_exhausted"] = suffix == "eternal-end"
    transition = None
    if native and (suffix == "collapse" or player["daniya_particles"] >= PARTICLE_LIMIT):
        transition = enter_black_hole(player)
    event.update(
        daniya_transition=transition,
        daniya_particle_after=player["daniya_particles"],
        daniya_permanent_reduction=player["daniya_permanent_reduction"],
        form_after=player.get("daniya_form") if native else event.get("form_after"),
        total=player["weight"],
    )


def move_weight_units(player, move, units):
    if "daniya-v19" not in move.tags:
        return units
    ensure_player(player)
    if move.move_id.endswith("-collapse"):
        form = player.get("daniya_form", "staging")
        return units + player.get(f"daniya_transform_{form}_units", 0)
    if move.move_id == "daniya-staging-domain":
        return (
            units
            + (int(player.get("daniya_domain_steps", 0)) + int(player.get("daniya_domain_draw_only_steps", 0))) * 1000
        )
    return units


def prepare_debuffs(state):
    """整轮敌向减益按同一账本倍率重算；每份来源事实只补一次倍率差。"""
    for player in state["sides"]:
        turn = player["turn"]
        factor = 2 ** int(turn.get("daniya_debuff_double", 0))
        old = int(turn.get("daniya_debuff_factor", 1))
        if factor != old:
            turn["daniya_debuff_factor"] = factor
            ratio = Fraction(factor, old)
            turn["opponent_domain_clash_reduction_units"] = int(
                turn.get("opponent_domain_clash_reduction_units", 0) * ratio
            )
            extra = Fraction(turn.get("daniya_permanent_grant_raw", 0)) * (factor - old)
            player["daniya_permanent_reduction"] = Fraction(player.get("daniya_permanent_reduction", 0)) + extra
        for event in turn["events"]:
            applied = int(event.get("daniya_debuff_factor", 1))
            if applied == factor:
                continue
            ratio = Fraction(factor, applied)
            for key in (
                "opponent_reduction",
                "opponent_next_debt",
                "opponent_exhaust_bonus_units",
                "daniya_current_move_debt",
            ):
                if key in event:
                    event[key] = Fraction(event[key]) * ratio
            event["daniya_debuff_factor"] = factor


def prepare_current_moves(state, cancelled, *, cancel):
    """冻结双方完整出招后同时取消尾部招式；来源资源不回滚，取消领域不再入战。"""
    snapshots = [tuple(p["turn"]["events"]) for p in state["sides"]]
    records = []
    for side, events in enumerate(snapshots):
        target = 1 - side
        target_player = state["sides"][target]
        if target_player["turn"].get("juejue_zero_active") or target_player["turn"].get("xixi_hourglass"):
            continue
        for event in events:
            debt = int(event.get("daniya_current_move_debt", 0))
            if event.get("effects_disabled") or not debt:
                continue
            for victim in snapshots[target][-debt:]:
                deduction = cancel(cancelled, target, victim, "黑洞星体削减当前出招")
                victim["daniya_current_cancelled"] = True
                victim["domain_eligible"] = False
                records.append(
                    {"side": side, "kind": "current-move-cancel", "ordinal": victim["ordinal"], "deduction": deduction}
                )
    return records


def prepare_interactions(state, domain, cancelled, protected, *, seed, choose, remaining, cancel):
    """在双方完整招式确定后选无视目标，双方先后输入不改变候选或来源成长。"""
    prepare_debuffs(state)
    records = []
    frozen = [
        [e for e in p["turn"]["events"] if not e.get("effects_disabled") and not e.get("daniya_current_cancelled")]
        for p in state["sides"]
    ]
    for side, player in enumerate(state["sides"]):
        for event in player["turn"]["events"]:
            if event.get("daniya_current_cancelled"):
                continue
            count = int(event.get("daniya_ignore_count", 0))
            target = 1 - side
            candidates = list(frozen[target])
            for index in range(min(count, len(candidates))):
                if target in protected:
                    break
                chosen, roll = choose(
                    seed,
                    f"{state['round']}:daniya:{side}:{event['ordinal']}:ignore:{index}",
                    tuple((i, 1) for i in range(len(candidates))),
                    version=state["version"],
                )
                victim = candidates.pop(chosen)
                victim["daniya_ignored"] = True
                records.append(
                    {
                        "side": side,
                        "target": target,
                        "ordinal": victim["ordinal"],
                        "roll": roll,
                        "deduction": cancel(cancelled, target, victim, "幻灭·未完的谎言"),
                    }
                )
    return records


def domain_effects(state, domain, cancelled, protected, *, seed, choose, remaining, cancel):
    if not domain or domain.get("hit_side") not in (0, 1):
        return []
    side = domain["hit_side"]
    player, target = state["sides"][side], state["sides"][1 - side]
    records = []
    factor = int(player["turn"].get("daniya_debuff_factor", 1))
    for event in player["turn"]["events"]:
        if not event.get("daniya_v19") or not event["move_id"].endswith("-domain") or not event.get("domain_eligible"):
            continue
        form = event["form_before"]
        native = event.get("daniya_native")
        if native:
            player["daniya_domain_hits"] += 1
        if form == "staging":
            player["next_action_bonus"] += 2
            if native and player.get("daniya_form") != BLACK_HOLE:
                player["daniya_form"] = "disillusion"
            own_gain = max(Fraction(0), remaining(cancelled, side, event))
            if domain.get("boost_side") == side and event["ordinal"] in domain.get("boosted_ordinals", ()):
                own_gain += Fraction(domain.get("bonus_gain", 0))
            if own_gain:
                player["weight"] += own_gain
                event["daniya_domain_self_bonus"] = own_gain
                records.append(
                    {"side": side, "kind": "domain-self-double", "ordinal": event["ordinal"], "gain": own_gain}
                )
            candidates = [e for e in player["turn"]["events"] if remaining(cancelled, side, e) > 0]
            if candidates:
                chosen, roll = choose(
                    seed,
                    f"{state['round']}:daniya:{side}:{event['ordinal']}:double",
                    tuple((i, 1) for i in range(len(candidates))),
                    version=state["version"],
                )
                victim = candidates[chosen]
                extra = remaining(cancelled, side, victim)
                if domain.get("boost_side") == side and victim["ordinal"] in domain.get("boosted_ordinals", ()):
                    extra += Fraction(domain.get("bonus_gain", 0))
                extra += Fraction(victim.get("daniya_domain_self_bonus", 0))
                player["weight"] += extra
                records.append(
                    {"side": side, "kind": "domain-double", "ordinal": victim["ordinal"], "gain": extra, "roll": roll}
                )
        else:
            player["next_action_bonus"] += 1
            if (
                1 - side not in protected
                and not target["turn"].get("xixi_hourglass")
                and not event.get("xixi_ignored")
                and not event.get("daniya_ignored")
            ):
                target["next_debt"] += 2 * factor
                target["injury_exhaust_bonus_units"] += 10 * factor
                candidates = [
                    e
                    for e in target["turn"]["events"]
                    if not e.get("effects_disabled") and not e.get("daniya_current_cancelled")
                ]
                if candidates:
                    chosen, roll = choose(
                        seed,
                        f"{state['round']}:daniya:{side}:{event['ordinal']}:domain-ignore",
                        tuple((i, 1) for i in range(len(candidates))),
                        version=state["version"],
                    )
                    victim = candidates[chosen]
                    deduction = cancel(cancelled, 1 - side, victim, "幻灭·蚀域")
                    victim["daniya_ignored"] = True
                    target["weight"] -= deduction
                    records.append(
                        {
                            "side": side,
                            "kind": "domain-ignore",
                            "ordinal": victim["ordinal"],
                            "deduction": deduction,
                            "roll": roll,
                        }
                    )
            if native:
                add_particles(player, Fraction(1, 2), form=form)
        if native and (player["daniya_domain_hits"] >= 7 or player["daniya_particles"] >= PARTICLE_LIMIT):
            transition = enter_black_hole(player)
            if transition:
                records.append({"side": side, "kind": "black-hole", **transition})
    return records


def finish_interactions(state, cancelled, protected, *, remaining, cancel):
    records = []
    sources = [list(p["turn"]["events"]) for p in state["sides"]]
    # 数值减益及当前出招数取消均从冻结全盘计算，不能依赖哪边先发消息。
    for side, player in enumerate(state["sides"]):
        ensure_player(player)
        target = state["sides"][1 - side]
        if player["snapshot"]["fighter_id"] == "daniya":
            raw_total = Fraction(player["daniya_permanent_reduction"])
            factor = Fraction(player["turn"].get("xixi_reduction_factor", 1))
            total = raw_total * factor if not target["turn"].get("xixi_hourglass") else Fraction(0)
            applied = (
                Fraction(player.get("daniya_reduction_applied_value", 0))
                if player.get("daniya_reduction_applied_round") == state["round"]
                else Fraction(0)
            )
            reduction = max(Fraction(0), total - applied)
            player["daniya_reduction_applied_round"] = state["round"]
            player["daniya_reduction_applied_value"] = total
            before = Fraction(target["weight"])
            if not target["turn"].get("xixi_hourglass"):
                target["weight"] = max(Fraction(1, 10), before - reduction)
            target["turn"]["daniya_permanent_applied"] = (
                Fraction(target["turn"].get("daniya_permanent_applied", 0)) + before - target["weight"]
            )
            pending_exhaust = Fraction(player.get("daniya_pending_exhaust_units", 0)) + Fraction(
                player.pop("daniya_unignorable_exhaust_units", 0)
            ) * int(player["turn"].get("daniya_debuff_factor", 1))
            if target["turn"].get("xixi_hourglass"):
                player["daniya_pending_exhaust_units"] = pending_exhaust
            else:
                target["injury_exhaust_bonus_units"] = (
                    Fraction(target.get("injury_exhaust_bonus_units", 0)) + pending_exhaust
                )
                player["daniya_pending_exhaust_units"] = Fraction(0)
            records.append(
                {
                    "side": side,
                    "kind": "permanent-reduction",
                    "value": reduction,
                    "raw_total": raw_total,
                    "equipment_factor": factor,
                    "applied": before - target["weight"],
                    "hourglass_suppressed": bool(target["turn"].get("xixi_hourglass")),
                }
            )
        for event in sources[side]:
            if (
                event.get("effects_disabled")
                or event.get("xixi_ignored")
                or event.get("daniya_ignored")
                or event.get("daniya_current_cancelled")
            ):
                continue
            if 1 - side not in protected and not target["turn"].get("xixi_hourglass"):
                if event.get("daniya_force_exhausted"):
                    player["turn"]["daniya_force_defeat_target"] = 1 - side
                    records.append({"side": side, "kind": "force-defeat", "target": 1 - side})
    return records


def fixed_injury_wheel(player):
    return FIXED_BLACK_HOLE_WHEEL if player.get("daniya_form") == BLACK_HOLE else None


def reduce_injury(player, injury):
    ensure_player(player)
    layers = 0
    if (
        injury in {"light", "heavy", "exhausted"}
        and player["snapshot"]["fighter_id"] == "daniya"
        and player.get("daniya_form") in {"staging", "disillusion"}
    ):
        particles = player["daniya_particles"]
        layers = 3 if particles > 15 else 2 if particles > 10 else 1 if particles > 5 else 0
        player["daniya_particles"] -= layers
        layers += int(player["turn"].get("daniya_injury_recovery_layers", 0))
    order = ("none", "light", "heavy", "exhausted")
    result = order[max(0, order.index(injury) - layers)] if injury in order else injury
    return result, {"layers": layers, "particles_remaining": player["daniya_particles"]}
