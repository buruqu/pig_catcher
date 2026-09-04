"""正式公共立绘 + 全新离线对战数据的图片验收，不连接MaiBot/QQ或用户浏览器。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.config.model import CatchingSection  # noqa: E402
from pig_catcher.domain.battle import (  # noqa: E402
    apply_move,
    fresh_turn,
    loads,
    move_weight_units,
    new_state,
    resolve_round,
)
from pig_catcher.domain.battle_catalog import (  # noqa: E402
    ASAMU_PIG_TEMPLATE_IDS,
    DANIYA_FORM_DISILLUSION,
    DANIYA_PIG_TEMPLATE_IDS,
    FIGHTERS_BY_ID,
    JUEJUE_FORM_TIME,
    JUEJUE_PIG_TEMPLATE_IDS,
    MOVE_WEIGHT_SCALE,
    fighter_form_moves,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.repositories.dispatch import timestamp_ms  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, media_path  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402
from pig_catcher.services.battle import BattleService  # noqa: E402
from pig_catcher.services.battle_views import matchup, view, wheels  # noqa: E402
from tests.test_battle import BattleWorld  # noqa: E402
from tests.test_dispatch import NOW, seed_pigs  # noqa: E402
from tests.test_gameplay import MutableClock  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


def _fresh_mechanic_state(initial_state: dict) -> dict:
    """Return a clean round-one state that retains only the two public fighter snapshots."""

    state = deepcopy(initial_state)
    state.update(round=1, status="active", winner=None)
    for side in state["sides"]:
        side["snapshot"].update(tool_id="", trait_bonus=0)
        side.update(
            weight=5,
            heavy=False,
            risk=0,
            core=0,
            next_debt=0,
            next_action_bonus=0,
            double=False,
            tool_used=False,
            black_flash_stacks=0,
            purple_weight_steps=0,
            round_start_weight=5,
            round_gains=[],
        )
        side["turn"] = fresh_turn()
    return state


def _ready(player: dict, raw: int = 1) -> None:
    player["turn"].update(raw=raw, debt=0, effective=raw, pending=raw, draws=0, done=raw == 0)


def _record_move(state: dict, side: int, move_id: str) -> dict:
    """Apply one explicit move and save a valid deterministic wheel snapshot for its card."""

    player = state["sides"][side]
    fighter_id = player["snapshot"]["fighter_id"]
    if fighter_id == "juejue":
        moves = fighter_form_moves(fighter_id, player["juejue_form"])
    elif fighter_id == "daniya":
        moves = fighter_form_moves(fighter_id, player["daniya_form"])
    else:
        moves = FIGHTERS_BY_ID[fighter_id].moves
    move_index = next(index for index, move in enumerate(moves) if move.move_id == move_id)
    wheel_units = [move_weight_units(player, move) for move in moves]
    event = apply_move(
        player,
        moves[move_index],
        seed="battle-visual-explicit-move",
        round_number=state["round"],
        side=side,
        version=state["version"],
    )
    event.update(
        roll=sum(wheel_units[:move_index]),
        round=state["round"],
        side=side,
        fighter_id=player["snapshot"]["fighter_id"],
        draw_weight_scale=MOVE_WEIGHT_SCALE,
        draw_wheel_units=wheel_units,
    )
    player["turn"]["events"].append(deepcopy(event))
    return event


def _fighter_snapshot(entry: dict, fighter_id: str, short_code: str) -> dict:
    """Build a public battle snapshot from one imported, formally catalogued pig."""

    return {
        "player_id": f"fixture-{fighter_id}",
        "player_name": "泡泡舞台的群友" if fighter_id == "daniya" else "红茶耐压的群友",
        "pig_instance_id": f"fixture-pig-{fighter_id}",
        "fighter_id": fighter_id,
        "template_id": entry["template_id"],
        "name": entry["display_name"],
        "short_code": short_code,
        "rarity": int(entry["rarity"]),
        "image_relpath": entry["image"],
        "display_tags": tuple(entry.get("display_tags", ())),
        "size_value": 66.6 if fighter_id == "daniya" else 88.8,
        "weight_value": 166.6 if fighter_id == "daniya" else 288.8,
        "favorite": True,
        "level": 5,
        "tool_id": "",
        "trait_bonus": 0,
    }


def _events(state: dict) -> list[dict]:
    return [deepcopy(event) for side in state["sides"] for event in side["turn"]["events"]]


def _resolve_fixed(prepared: dict, slug: str, *, domain_outcome: str | None = None) -> tuple[dict, dict, str]:
    """Find and return a named, reproducible seed without accepting an accidental terminal injury."""

    for index in range(10_000):
        seed = f"battle-visual-{slug}-{index}"
        state = deepcopy(prepared)
        result = resolve_round(state, seed)
        if result is None or result["natural_end"]:
            continue
        domain = result["interactions"]["domain"]
        if domain_outcome is not None and (domain is None or domain["outcome"] != domain_outcome):
            continue
        return state, result, seed
    raise AssertionError(f"无法为离线对战样张找到固定结果：{slug}/{domain_outcome}")


def _resolve_matching(prepared: dict, slug: str, predicate) -> tuple[dict, dict, str]:
    """Find a reproducible non-terminal result satisfying one mechanic predicate."""

    for index in range(20_000):
        seed = f"battle-visual-{slug}-{index}"
        state = deepcopy(prepared)
        result = resolve_round(state, seed)
        if result is None or result["natural_end"] or not predicate(result):
            continue
        return state, result, seed
    raise AssertionError(f"无法为离线对战样张找到指定机制结果：{slug}")


def deterministic_mechanic_cases(
    initial_state: dict,
    initial_match: dict,
    identity: CommandIdentity,
    now_ms: int,
    juejue_entry: dict,
    daniya_entry: dict,
    asamu_entry: dict,
) -> tuple[list[tuple[str, object]], dict]:
    """Build explicit rule cards so visual acceptance never depends on a lucky ordinary fight."""

    cases: list[tuple[str, object]] = []
    evidence: dict[str, dict] = {}

    for suffix, expected, title, banner in (
        (
            "sukuna-win",
            "side-0",
            "双领域判定 · 宿傩领域胜",
            "固定种子验收：宿傩领域战为40% / 对手30% / 平手30%，本次落在宿傩一侧。",
        ),
        (
            "tie",
            "tie",
            "双领域判定 · 平手归零",
            "固定种子验收：双方同回合展开领域，本次平手，双方领域胜利权重同时归零。",
        ),
    ):
        prepared = _fresh_mechanic_state(initial_state)
        for side, move_id in ((0, "shrine"), (1, "void")):
            _ready(prepared["sides"][side])
            _record_move(prepared, side, move_id)
        state, result, seed = _resolve_fixed(prepared, "domain-" + suffix, domain_outcome=expected)
        domain = result["interactions"]["domain"]
        assert domain and tuple(domain["wheel"]) == (("side-0", 40), ("side-1", 30), ("tie", 30))
        assert domain["weight_scale"] == 10
        match = {**initial_match, "status": state["status"]}
        name = "13c-domain-clash-sukuna-win" if expected == "side-0" else "13d-domain-clash-tie"
        cases.append(
            (
                name,
                matchup(
                    identity,
                    match,
                    state,
                    now_ms,
                    title=title,
                    banner=banner,
                    events=_events(prepared),
                    round_result=result,
                ),
            )
        )
        evidence[name] = {
            "seed": seed,
            "mode": domain["mode"],
            "wheel": domain["wheel"],
            "outcome": domain["outcome"],
            "domain_counts": domain["domain_counts"],
            "boost_side": domain.get("boost_side"),
            "boosted_ordinal": domain.get("boosted_ordinal"),
            "bonus_gain": domain.get("bonus_gain", 0),
        }

    prepared = _fresh_mechanic_state(initial_state)
    _ready(prepared["sides"][0])
    _record_move(prepared, 0, "dismantle")
    _ready(prepared["sides"][1])
    _record_move(prepared, 1, "void")
    state, result, seed = _resolve_fixed(prepared, "solo-simple-domain", domain_outcome="simple-domain")
    domain = result["interactions"]["domain"]
    assert domain and tuple(domain["wheel"]) == (("hit", 8), ("simple-domain", 2))
    assert state["sides"][0]["next_debt"] == 0
    name = "13e-solo-simple-domain"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**initial_match, "status": state["status"]},
                state,
                now_ms,
                title="单方领域 · 简易领域免疫",
                banner="固定种子验收：单方领域按8:2判定，本次落在简易领域，领域权重与无量空处效果均归零。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "mode": domain["mode"],
        "wheel": domain["wheel"],
        "outcome": domain["outcome"],
        "gojo_next_round_debt_applied": False,
    }

    prepared = _fresh_mechanic_state(initial_state)
    _ready(prepared["sides"][0])
    _record_move(prepared, 0, "dismantle")
    _ready(prepared["sides"][1])
    _record_move(prepared, 1, "void")
    state, result, seed = _resolve_fixed(prepared, "solo-gojo-hit", domain_outcome="hit")
    domain = result["interactions"]["domain"]
    assert domain and tuple(domain["wheel"]) == (("hit", 8), ("simple-domain", 2))
    assert domain["boost_side"] == 1 and domain["bonus_gain"] == 30
    assert domain["boost_reason"] == "领域命中"
    assert state["sides"][0]["next_debt"] == 1
    name = "13e2-solo-gojo-hit"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**initial_match, "status": state["status"]},
                state,
                now_ms,
                title="单方领域 · 无量空处命中加倍",
                banner="固定种子验收：无量空处通过8:2命中判定，一份仍有效领域胜率翻倍，并使对方下回合少1招。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "mode": domain["mode"],
        "wheel": domain["wheel"],
        "outcome": domain["outcome"],
        "boost_side": domain["boost_side"],
        "boosted_ordinal": domain["boosted_ordinal"],
        "bonus_gain": domain["bonus_gain"],
        "boost_reason": domain["boost_reason"],
        "gojo_next_round_debt_applied": state["sides"][0]["next_debt"] == 1,
    }

    prepared = _fresh_mechanic_state(initial_state)
    _ready(prepared["sides"][0])
    flash = _record_move(prepared, 0, "black-flash")
    loan = _record_move(prepared, 0, "loan")
    _record_move(prepared, 0, "dismantle")
    space = _record_move(prepared, 0, "world-cutting-slash")
    _ready(prepared["sides"][1])
    _record_move(prepared, 1, "defense")
    assert all(side["turn"]["done"] for side in prepared["sides"])
    state, result, seed = _resolve_fixed(prepared, "flash-loan-infinity-space")
    adjustments = result["interactions"]["adjustments"][0]
    assert loan["gain"] == 1 and prepared["sides"][0]["black_flash_stacks"] == 1
    assert any(item["ordinal"] == flash["ordinal"] and "无下限·防御" in item["reasons"] for item in adjustments)
    name = "13f-black-flash-loan-infinity-space"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**initial_match, "status": state["status"]},
                state,
                now_ms,
                title="黑闪领悟 · 贷款 · 无下限 · 空间斩",
                banner="固定招式序列：黑闪功能与层数保留，贷款获得光环且不消耗翻倍；无下限仅令首个数值招式归零。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "black_flash_gain": flash["gain"],
        "black_flash_stacks": prepared["sides"][0]["black_flash_stacks"],
        "loan_gain": loan["gain"],
        "loan_double_preserved_for_next_numeric": True,
        "space_slash_gain": space["gain"],
        "infinity_adjustments": adjustments,
    }

    prepared = _fresh_mechanic_state(initial_state)
    _ready(prepared["sides"][0])
    _record_move(prepared, 0, "dismantle")
    _ready(prepared["sides"][1], 5)
    sequence = [
        _record_move(prepared, 1, move_id)
        for move_id in ("blue", "red", "purple", "blue-fist", "unlimited-purple")
    ]
    state, result, seed = _resolve_fixed(prepared, "purple-reset-cycle")
    name = "13g-purple-reset-cycle"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**initial_match, "status": state["status"]},
                state,
                now_ms,
                title="苍赫聚合 · 两次茈归零重算",
                banner="苍、赫把茈盘推到+0.2；虚式·茈发动后归零，苍拳重新积到+0.1，再由无限制·茈消耗。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "first_purple_used_steps": sequence[2]["purple_weight_steps_used"],
        "first_purple_wheel_units": sequence[2]["draw_wheel_units"],
        "second_purple_used_steps": sequence[4]["purple_weight_steps_used"],
        "final_purple_weight_steps": result["before"][1]["purple_weight_steps"],
    }

    prepared = _fresh_mechanic_state(initial_state)
    prepared["round"] = 3
    for side in prepared["sides"]:
        side.update(weight=8, round_start_weight=8, round_gains=[1, 3])
    _ready(prepared["sides"][0])
    _record_move(prepared, 0, "elbow")
    _ready(prepared["sides"][1])
    _record_move(prepared, 1, "reverse")
    state, result, seed = _resolve_fixed(prepared, "round-carry")
    name = "13h-round-carry"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**initial_match, "status": state["status"]},
                state,
                now_ms,
                title="第三回合 · 逐轮折半继承",
                banner="固定事实验收：基础5 + 第一回合1的半额1 + 第二回合3的半额2，再叠加本回合完整新增。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "round": result["round"],
        "carryover": result["carryover"],
        "next_weights": [side["weight"] for side in state["sides"]],
    }

    juejue_snapshot = deepcopy(initial_state["sides"][0]["snapshot"])
    juejue_snapshot.update(
        fighter_id="juejue",
        template_id=juejue_entry["template_id"],
        name=juejue_entry["display_name"],
        short_code="JJFORM",
        rarity=int(juejue_entry["rarity"]),
        image_relpath=juejue_entry["image"],
        display_tags=tuple(juejue_entry.get("display_tags", ())),
        level=5,
    )
    switch_state = new_state(
        [juejue_snapshot, deepcopy(initial_state["sides"][1]["snapshot"])],
        seed="battle-visual-juejue-entry",
    )
    switch_state["sides"][0]["juejue_form"] = JUEJUE_FORM_TIME
    _ready(switch_state["sides"][0])
    switch_virtual = _record_move(switch_state, 0, "switch-virtual")
    mimic = _record_move(switch_state, 0, "virtual-mimic")
    switch_sand = _record_move(switch_state, 0, "switch-sand")
    acceleration = _record_move(switch_state, 0, "sand-accelerate")
    name = "13i-juejue-form-switch"
    switch_view = matchup(
        identity,
        {**initial_match, "status": "active"},
        switch_state,
        now_ms,
        title="撅撅猪 · 双形态即时切换",
        banner="固定招式序列：时之沙切入虚拟声完成模仿，再即时切回时之沙继续抽取。",
        events=_events(switch_state),
    )
    cases.append(
        (
            name,
            replace(switch_view, wheels=wheels(identity, "juejue", level=5).wheels),
        )
    )
    evidence[name] = {
        "form_track": [
            switch_virtual["form_before"],
            switch_virtual["form_after"],
            switch_sand["form_after"],
        ],
        "mimic_available": bool(mimic["mimic"]["available"]),
        "mimic_source_name": mimic["mimic"]["source_name"],
        "acceleration_tier": acceleration["subwheel"]["tier"],
        "acceleration_success": acceleration["subwheel"]["success"],
    }

    daniya_snapshot = _fighter_snapshot(daniya_entry, "daniya", "DANIYA")
    asamu_snapshot = _fighter_snapshot(asamu_entry, "asamu", "ASAMU")
    v5_state = new_state(
        [deepcopy(daniya_snapshot), deepcopy(asamu_snapshot)],
        seed="battle-visual-daniya-asamu-entry",
    )
    v5_match = {**initial_match, "battle_id": "BTV5DANIYAASAMU", "definition_version": 5}
    name = "13j-daniya-asamu-formal-art"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**v5_match, "status": "active"},
                v5_state,
                now_ms,
                title="Battle v5 · 达妮娅猪与阿萨姆猪",
                banner="正式素材离线验收：达妮娅猪以布景形态入场，阿萨姆猪携动态招式盘入场。",
            ),
        )
    )
    evidence[name] = {
        "fighters": [
            {
                "fighter_id": side["snapshot"]["fighter_id"],
                "template_id": side["snapshot"]["template_id"],
                "image_relpath": side["snapshot"]["image_relpath"],
            }
            for side in v5_state["sides"]
        ],
        "daniya_initial_form": v5_state["sides"][0]["daniya_form"],
    }

    prepared = deepcopy(v5_state)
    _ready(prepared["sides"][0], 3)
    staging_a = _record_move(prepared, 0, "daniya-staging-dream-feast")
    staging_b = _record_move(prepared, 0, "daniya-staging-mimic-bubble")
    daniya_domain = _record_move(prepared, 0, "daniya-domain")
    _ready(prepared["sides"][1])
    _record_move(prepared, 1, "asamu-domain")
    state, result, seed = _resolve_fixed(prepared, "daniya-domain-transition", domain_outcome="side-0")
    transition = result["interactions"]["daniya_transition"]
    assert transition and transition["after"] == DANIYA_FORM_DISILLUSION
    name = "13k-daniya-domain-transition"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**v5_match, "status": state["status"]},
                state,
                now_ms,
                title="达妮娅猪 · 布景蓄势与蚀域转幕",
                banner="两次布景令蚀域主盘从1提升至1.6，并把同一份+0.6带入领域战；蚀域抽中后清零，领域战获胜或单方命中即切换幻灭并使下回合+1招。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "staging_steps": [
            staging_a["daniya_domain_steps_after"],
            staging_b["daniya_domain_steps_after"],
        ],
        "domain_draw_weight_units": MOVE_WEIGHT_SCALE
        + daniya_domain["daniya_domain_steps_before"] * (MOVE_WEIGHT_SCALE // 10),
        "domain_steps_after_draw": daniya_domain["daniya_domain_steps_after"],
        "domain_wheel": result["interactions"]["domain"]["wheel"],
        "transition": transition,
        "next_round_form": state["sides"][0]["daniya_form"],
        "next_action_bonus": state["sides"][0]["next_action_bonus"],
    }

    prepared = deepcopy(v5_state)
    prepared["sides"][0]["daniya_form"] = DANIYA_FORM_DISILLUSION
    _ready(prepared["sides"][0])
    loan = _record_move(prepared, 0, "daniya-unfinished-lie")
    disillusion = _record_move(prepared, 0, "daniya-disillusion-final-curtain")
    _ready(prepared["sides"][1])
    pressure = _record_move(prepared, 1, "asamu-pressure-king")

    def pressure_invalidates_disillusion(result: dict) -> bool:
        return any(
            check["hit"]
            and check["source_ordinal"] == pressure["ordinal"]
            and check["target_ordinal"] == disillusion["ordinal"]
            for check in result["interactions"]["pressure_checks"]
        )

    state, result, seed = _resolve_matching(
        prepared,
        "unified-numeric-invalidation",
        pressure_invalidates_disillusion,
    )
    adjustment = next(
        row
        for row in result["interactions"]["adjustments"][0]
        if row["ordinal"] == disillusion["ordinal"]
    )
    cross_effect = next(
        row
        for row in result["interactions"]["cross_effects"]
        if row["source_side"] == 0 and row["source_ordinal"] == disillusion["ordinal"]
    )
    assert adjustment["gain"] == disillusion["gain"]
    assert cross_effect["round_reduction"] == disillusion["opponent_reduction"]
    name = "13l-unified-numeric-invalidation"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**v5_match, "status": state["status"]},
                state,
                now_ms,
                title="统一失效 · 数值归零而功能保留",
                banner="未竟的谎言令下一招双倍；传奇耐压王命中后只把该招自身胜率归零，对方减权、幻灭力竭加成与贷款跨回合效果照常结算。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "loan_gain": loan["gain"],
        "loan_next_debt": loan["next_debt"],
        "doubled_own_gain_before_invalidation": disillusion["gain"],
        "doubled_opponent_reduction_preserved": disillusion["opponent_reduction"],
        "cancelled_own_gain": adjustment["gain"],
        "invalidation_reasons": adjustment["reasons"],
        "permanent_opponent_exhaust_bonus_units": cross_effect["exhaust_bonus_units"],
    }

    prepared = deepcopy(v5_state)
    prepared["sides"][0].update(weight=100, round_start_weight=100)
    _ready(prepared["sides"][0])
    collapse = _record_move(prepared, 0, "daniya-timed-collapse")
    _ready(prepared["sides"][1])
    # 憋个大会追加抽数，不能作为单招结算样张；洗澡没有未完成抽数。
    _record_move(prepared, 1, "asamu-bathe")
    state, result, seed = _resolve_matching(
        prepared,
        "daniya-collapse-passive",
        lambda current: current["loser"] == 1,
    )
    assert result["injury_modifiers"]["daniya_passive_layers"] == 1
    assert result["injury_modifiers"]["daniya_active_layers"] == 1
    assert result["injury_modifiers"]["current_collapse_multiplier"] == 25
    name = "13m-daniya-collapse-rebound"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**v5_match, "status": state["status"]},
                state,
                now_ms,
                title="达妮娅猪 · 计时的溃灭",
                banner="达妮娅常驻一层按当前回合数×5增长的计时被动；抽中本招后，本回合再叠一层同款力竭倍率，不再产生旧版跨回合反噬。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "move_opponent_reduction": collapse["opponent_reduction"],
        "passive_layers": result["injury_modifiers"]["daniya_passive_layers"],
        "active_layers": result["injury_modifiers"]["daniya_active_layers"],
        "injury_wheel": result["injury_wheel"],
        "injury_modifiers": result["injury_modifiers"],
        "injury": result["injury"],
        "collapse_rebounds": result["collapse_rebounds"],
        "daniya_rebound_round": None,
        "daniya_rebound_multiplier": 1,
    }

    prepared = deepcopy(v5_state)
    _ready(prepared["sides"][0])
    _record_move(prepared, 0, "daniya-staging-final-curtain")
    _ready(prepared["sides"][1], 4)
    bathe = _record_move(prepared, 1, "asamu-bathe")
    tea = _record_move(prepared, 1, "asamu-milk-tea")
    sleep = _record_move(prepared, 1, "asamu-sleep")
    prime = _record_move(prepared, 1, "asamu-prime")
    pressure = _record_move(prepared, 1, "asamu-pressure-king")
    _record_move(prepared, 1, "asamu-bathe")

    def pressure_hits_staging(result: dict) -> bool:
        return any(
            check["hit"] and check["target_side"] == 0 and check["target_ordinal"] == 1
            for check in result["interactions"]["pressure_checks"]
        )

    state, result, seed = _resolve_matching(prepared, "asamu-dynamic-chain", pressure_hits_staging)
    name = "13n-asamu-dynamic-chain"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**v5_match, "status": state["status"]},
                state,
                now_ms,
                title="阿萨姆猪 · 动态招式盘与耐压王",
                banner="洗澡提高喝奶茶权重；喝奶茶永久培养全盛姿态，睡觉强化后续招式；全盛姿态再抽两次，耐压王独立判定数值失效。",
                events=_events(prepared),
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "tea_weight_after_bathe": tea["draw_wheel_units"][1],
        "sleep_weight_after_tea": sleep["draw_wheel_units"][2],
        "prime_weight_after_sleep": prime["draw_wheel_units"][3],
        "prime_extra_draws": prime["extra_draws"],
        "bathe_bonus_after": bathe["asamu_tea_bonus_after"],
        "tea_resets_own_weight": tea["asamu_tea_bonus_after"],
        "sleep_resets_own_weight": sleep["asamu_sleep_bonus_after"],
        "pressure_source_ordinal": pressure["ordinal"],
        "pressure_checks": result["interactions"]["pressure_checks"],
    }

    prepared = deepcopy(v5_state)
    _ready(prepared["sides"][0])
    _record_move(prepared, 0, "daniya-domain")
    _ready(prepared["sides"][1])
    _record_move(prepared, 1, "asamu-domain")
    state, result, seed = _resolve_fixed(prepared, "asamu-domain-copies", domain_outcome="side-1")
    copies = result["interactions"]["asamu_domain_copies"]
    assert len(copies) == 2
    name = "13o-asamu-domain-copies"
    cases.append(
        (
            name,
            matchup(
                identity,
                {**v5_match, "status": state["status"]},
                state,
                now_ms,
                title="阿萨姆猪 · 奶茶领域夺取两招",
                banner="领域战获胜后严格复制对方两个随机招式；复制到领域只保留招式本身，不递归开启第二场领域战。",
                events=_events(prepared) + [deepcopy(event) for event in copies],
                round_result=result,
            ),
        )
    )
    evidence[name] = {
        "seed": seed,
        "domain_wheel": result["interactions"]["domain"]["wheel"],
        "domain_outcome": result["interactions"]["domain"]["outcome"],
        "copy_count": len(copies),
        "copies": [
            {
                "slot": event["copy_slot"],
                "source_move_id": event["source_move_id"],
                "source_move_name": event["source_move_name"],
                "domain_reentry_suppressed": event["domain_reentry_suppressed"],
                "gain": event["gain"],
                "opponent_reduction": event["opponent_reduction"],
            }
            for event in copies
        ],
    }
    return cases, evidence


async def scenarios(output: Path):
    catalog_root = PROJECT_ROOT / "asset_library/current"
    catalog = json.loads((catalog_root / "assets.json").read_text(encoding="utf-8"))
    public = [entry for entry in catalog["entries"] if entry["kind"] == "pig" and entry["scope"] == "common"]
    public_fighters = (FIGHTERS_BY_ID["sukuna"], FIGHTERS_BY_ID["gojo"])
    juejue_entry = next(
        entry
        for entry in catalog["entries"]
        if entry.get("template_id") == JUEJUE_PIG_TEMPLATE_IDS[0]
    )
    juejue_food_entry = next(
        entry
        for entry in catalog["entries"]
        if entry.get("template_id") == juejue_entry["paired_food_template_id"]
    )
    daniya_entry = next(
        entry
        for entry in catalog["entries"]
        if entry.get("template_id") == DANIYA_PIG_TEMPLATE_IDS[0]
    )
    daniya_food_entry = next(
        entry
        for entry in catalog["entries"]
        if entry.get("template_id") == daniya_entry["paired_food_template_id"]
    )
    asamu_entry = next(
        entry
        for entry in catalog["entries"]
        if entry.get("template_id") == ASAMU_PIG_TEMPLATE_IDS[0]
    )
    asamu_food_entry = next(
        entry
        for entry in catalog["entries"]
        if entry.get("template_id") == asamu_entry["paired_food_template_id"]
    )
    ids = {fighter.template_id for fighter in public_fighters}
    for star in range(1, 6):
        ids.add(next(entry["template_id"] for entry in public if entry["rarity"] == star))
    entries = [entry for entry in public if entry["template_id"] in ids]
    entries.extend(
        (
            juejue_entry,
            juejue_food_entry,
            daniya_entry,
            daniya_food_entry,
            asamu_entry,
            asamu_food_entry,
        )
    )
    if not all(fighter.template_id in {entry["template_id"] for entry in entries} for fighter in public_fighters):
        raise ValueError("缺少两只已确认的公共战斗猪立绘。")
    source = output / "inputs"
    source.mkdir()
    for entry in entries:
        for field in ("image", "alternate_image"):
            if entry.get(field):
                destination = source / entry[field]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(catalog_root / entry[field], destination)
    catalog["entries"], catalog["catalog_id"] = entries, "battle-visual-offline"
    manifest = source / "assets.json"
    manifest.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    data_root = output / "isolated-fixture"
    db = PigCatcherDatabase(data_root / "battle-test.sqlite3")
    await db.open()
    try:
        await AssetCatalogService(
            db, AssetCatalogStorage(data_root), min_image_side=32, max_image_bytes=32 * 1024 * 1024
        ).import_manifest(manifest)
        stored_juejue = await db.fetch_one(
            "SELECT image_relpath FROM pig_templates WHERE template_id=?",
            (juejue_entry["template_id"],),
        )
        if stored_juejue is None:
            raise ValueError("离线验收库缺少撅撅猪正式立绘。")
        juejue_render_entry = {**juejue_entry, "image": stored_juejue[0]}
        stored_v5 = {}
        for fighter_id, entry in (("daniya", daniya_entry), ("asamu", asamu_entry)):
            stored = await db.fetch_one(
                "SELECT image_relpath FROM pig_templates WHERE template_id=?",
                (entry["template_id"],),
            )
            if stored is None:
                raise ValueError(f"离线验收库缺少{entry['display_name']}正式立绘。")
            stored_v5[fighter_id] = {**entry, "image": stored[0]}
        a = CommandIdentity(
            ScopeKey("qq-official", "battle-fixture"),
            "fixture-stream",
            "fixture-a",
            "下班以后来比划的群友",
            "seed",
            "离线验收群",
        )
        b = replace(a, user_id="fixture-b", display_name="今天也想掌握核心")
        clock = MutableClock(NOW)
        service = BattleService(
            db, clock=clock, seed_factory=lambda: "battle-visual-r1", catching=CatchingSection(cooldown_seconds=0)
        )
        w = BattleWorld(db, clock, service, a, b)
        cases = [("01-empty", (await w.send()).view)]
        for actor, fighter in zip((a, b), public_fighters, strict=True):
            await seed_pigs(db, actor, template_id=fighter.template_id, count=1)
            preview = await w.send("设置 " + fighter.name, actor=actor)
            if actor == a:
                cases.append(("02-assignment", preview.view))
            await w.send("确认", actor=actor)
            await w.fund(actor)
        cases.append(("03-profile", (await w.send()).view))
        cases.append(("04-upgrade-preview", (await w.send("强化")).view))
        cases.append(("05-upgraded", (await w.send("确认")).view))
        cases.append(("06-tools", (await w.send("器具")).view))
        cases.append(("07-crafted", (await w.send("制作 入场彩纸 2")).view))
        await w.send("器具 入场彩纸")
        cases.append(("08-sukuna-wheel", (await w.send("轮盘 宿傩猪")).view))
        cases.append(("09-gojo-wheel", (await w.send("轮盘 五条猪")).view))
        cases.append(("09b-juejue-dual-form-wheel", wheels(a, "juejue")))
        cases.append(("09c-daniya-dual-form-wheel", wheels(a, "daniya", level=5)))
        cases.append(("09d-asamu-dynamic-wheel", wheels(a, "asamu", level=5)))
        cases.append(("09e-yilu-operator-wheel", wheels(a, "yilu", level=5)))
        cases.append(("10-invitation", (await w.invite()).view))
        cases.append(("11-entry", (await w.send("接受", "challenge", actor=b)).view))
        initial_match = await w.match()
        initial_state = loads(initial_match["state_json"])
        cases.append(("12-action-count", (await w.send(section="count")).view))
        cases.append(("13-moves", (await w.send(section="move")).view))
        round_settlement = None
        for side, actor in enumerate((a, b)):
            while True:
                current = loads((await w.match())["state_json"])
                if current["status"] != "active" or current["round"] != initial_state["round"]:
                    break
                turn = current["sides"][side]["turn"]
                if turn["raw"] is None:
                    await w.send(section="count", actor=actor)
                elif not turn["done"]:
                    result = await w.send(section="move", actor=actor)
                    if result.view.title == "双方出招 · 回合结算":
                        round_settlement = result.view
                else:
                    break
        if round_settlement is None:
            raise AssertionError("首回合未生成双方即时结算图")
        cases.append(("13b-round-settlement", round_settlement))
        mechanic_cases, mechanic_evidence = deterministic_mechanic_cases(
            initial_state,
            initial_match,
            a,
            timestamp_ms(NOW),
            juejue_render_entry,
            stored_v5["daniya"],
            stored_v5["asamu"],
        )
        cases.extend(mechanic_cases)
        finished = await w.fight(already_started=True)
        cases.append(("14-natural-finale", (await w.send(section="status")).view))
        winner = loads(finished["state_json"])["winner"]
        loser = a if winner == 1 else b
        cases.append(("15-loot", (await w.send(section="loot", actor=loser)).view))
        cases.append(("16-history", (await w.send(section="history")).view))
        cases.append(("17-round-detail", (await w.send(finished["battle_id"] + " 1", "history")).view))
        cases.append(("18-retire-preview", (await w.send("解除保护")).view))
        await w.send("取消")
        clock.value += timedelta(seconds=61)
        await w.invite(actor=b, target=a)
        await w.send("接受", "challenge", actor=a)
        cases.append(("19-surrender-preview", (await w.send("认输", "challenge")).view))
        cases.append(("20-surrendered", (await w.send("确认认输", "challenge")).view))
        clock.value += timedelta(days=1)
        await w.send("器具 无")
        await w.invite()
        clock.value += timedelta(minutes=5)
        cases.append(("21-expired", (await w.send(section="status")).view))
        sample = deepcopy(initial_state)
        sample["sides"][0].update(core=100, heavy=False, risk=2, weight=10**5000, next_debt=37, double=True)
        sample["sides"][0]["turn"].update(raw=5, debt=37, effective=0, pending=0, done=True)
        sample["sides"][1].update(heavy=True, risk=2, weight=10**4999)
        cases.append(
            (
                "22-unlimited-core-zero-actions",
                matchup(
                    a,
                    initial_match,
                    sample,
                    timestamp_ms(NOW),
                    title="核心不封顶 · 零招也能交锋",
                    banner="视觉边界用例：5001位累计权重仍按精确整数结算，不在图片里铺满数字。",
                ),
            )
        )
        long_view = next(result for case_name, result in cases if case_name == "10-invitation")
        cases.append(
            (
                "23-long-names",
                replace(
                    long_view,
                    player_name="这是显示很长的中文英文名字ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 3,
                    fighters=tuple(
                        replace(
                            f,
                            player_name="中英文超长群昵称ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 3,
                            pig_name="中英文战斗猪名ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 3,
                        )
                        for f in long_view.fighters
                    ),
                ),
            )
        )
        cases.append(("24-missing-media", long_view))
        cases.append(
            (
                "25-error-hint",
                view(
                    a,
                    "对战提示",
                    banner="参与方存在赠送、收赠或交易限制。本次没有消耗次数，待交付战利品仍保留，请联系管理员处理。",
                ),
            )
        )
        return cases, data_root, mechanic_evidence
    finally:
        await db.close()


async def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请使用新的验收目录，不覆盖既有证据。")
    output.mkdir(parents=True)
    cases, data_root, mechanic_evidence = await scenarios(output)
    outputs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())
            for name, result in cases:
                paths = {
                    pig.short_code: media_path(data_root, pig.image_relpath) for pig in result.pigs if pig.image_relpath
                }
                if name == "24-missing-media":
                    paths = {}
                capability.label = name
                image = await renderer.render_battle(result, paths)
                destination = output / f"{name}.png"
                write_image(destination, image)
                (output / f"{name}.txt").write_text(result.text(), encoding="utf-8")
                outputs.append(destination)
        finally:
            await capability.close()
            await browser.close()
    failures = [
        row
        for row in capability.diagnostics
        if row["clippedText"] or row["outside"] or row["brokenImages"] or row.get("clippedMedia")
    ]
    report = {
        "title": "Battle v14 · 战斗图片离线验收",
        "status": "failed" if failures else "passed",
        "count": len(outputs),
        "diagnostics": capability.diagnostics,
        "failures": failures,
        "deterministic_mechanics": mechanic_evidence,
        "battle_visual_coverage": (
            "达妮娅猪与阿萨姆猪正式立绘",
            "达妮娅布景/幻灭双形态轮盘",
            "阿萨姆动态抽取权重轮盘",
            "熠～噜猪九招干员盘、近卫八枚门槛及狙击逐发加成",
            "蚀域蓄势、领域切形态与下回合加招",
            "未竟的谎言与统一数值失效",
            "计时的溃灭常驻/主动层与世界招式",
            "洗澡/喝奶茶/睡觉/全盛姿态连锁",
            "传奇耐压王独立失效判定",
            "阿萨姆领域胜利复制两招",
        ),
        "scope": "isolated offline data and public art; no production or QQ connection",
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    contact_sheet(outputs, output / "contact-sheet.jpg")
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    return {
        "status": "passed",
        "count": len(outputs),
        "report": str(output / "report.json"),
        "preview": str(output / "contact-sheet.jpg"),
    }


def main():
    parser = argparse.ArgumentParser(description="Battle v14 战斗图片离线验收")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
