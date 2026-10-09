"""Battle v19 正式素材离线卡片与可重现对战模拟；不连接QQ或生产数据库。"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright  # noqa: E402

from pig_catcher.domain import battle, daniya_battle  # noqa: E402
from pig_catcher.domain.battle_catalog import (  # noqa: E402
    BATTLE_VERSION,
    FIGHTERS_BY_ID,
    fighter_form_moves,
    fighter_moves,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.domain.xixi_battle_catalog import HEXTECH_NAMES, XIXI_FORM_CELESTIAL  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer  # noqa: E402
from pig_catcher.services.battle_views import matchup, wheels  # noqa: E402
from pig_catcher.version import RULESET_VERSION, SCHEMA_VERSION  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


def snapshot(fighter_id, entries, index=0):
    definition = FIGHTERS_BY_ID[fighter_id]
    entry = entries[definition.template_id]
    return {
        "player_id": f"offline-{index}", "player_name": f"{definition.name}的训练员",
        "pig_instance_id": f"offline-pig-{index}", "fighter_id": fighter_id,
        "template_id": entry["template_id"], "name": entry["display_name"], "short_code": f"B{index + 1:04d}",
        "rarity": entry["rarity"], "image_relpath": entry["image"], "display_tags": entry["display_tags"],
        "size_value": 66, "weight_value": 166, "favorite": True, "level": 0, "tool_id": "", "trait_bonus": 0,
        "battle_form_id": XIXI_FORM_CELESTIAL if fighter_id == "xixi" else "",
    }


def state_for(left, right, entries, seed="offline-v19"):
    return battle.new_state(
        [snapshot(left, entries, 0), snapshot(right, entries, 1)], seed=seed, version=BATTLE_VERSION,
    )


def scripted_turn(state, side, move_ids, raw=None):
    """为命名样张选择实际盘内落点；其他判定仍使用固定seed的真实抽签。"""
    player = state["sides"][side]
    raw = len(move_ids) if raw is None else raw
    player["turn"].update(raw=raw, effective=raw, pending=raw, done=raw == 0)
    pending_ids = list(move_ids)
    original_choose = battle.choose

    def choose(seed, key, options, *, version):
        if key.startswith(f"{state['round']}:{side}:move:"):
            fighter_id = player["snapshot"]["fighter_id"]
            form = player.get("daniya_form") or player.get("xixi_form") or ""
            candidates = fighter_form_moves(fighter_id, form, version) if form else fighter_moves(fighter_id, version)
            requested = pending_ids.pop(0) if pending_ids else candidates[0].move_id
            index = next(i for i, move in enumerate(candidates) if move.move_id == requested)
            return index, sum(weight for i, weight in options if i < index)
        return original_choose(seed, key, options, version=version)

    with patch.object(battle, "choose", choose):
        for _ in range(10):
            battle.play_chunk(state, side, "offline-v19-script")
            if player["turn"]["done"]:
                return
    raise AssertionError("脚本样张连招未结束")


def settle_matching(prepared, label, predicate=lambda result: True):
    for number in range(2000):
        seed = f"v19-visual-{label}-{number}"
        state = deepcopy(prepared)
        result = battle.resolve_round(state, seed)
        if result is not None and predicate(result):
            return state, result, seed
    raise AssertionError(f"未找到命名样张结果：{label}")


def visual_cases(entries):
    identity = CommandIdentity(ScopeKey("qq", "1092931381"), "offline", "offline-a", "离线验收", "offline")
    cases = [("01-daniya-wheels", wheels(identity, "daniya")), ("02-xixi-wheels", wheels(identity, "xixi"))]
    facts = []

    def add(label, state, result=None):
        cases.append((label, matchup(identity, {"battle_id": label, "definition_version": BATTLE_VERSION,
                                                "status": state["status"], "expires_ms": 600000},
                                     state, 0, round_result=result)))
        facts.append({"case": label, "state": state, "round_result": result})

    state = state_for("xixi", "gojo", entries)
    scripted_turn(state, 0, ["xixi-surge", "xixi-prison", "xixi-overload", "xixi-attack"])
    scripted_turn(state, 1, [FIGHTERS_BY_ID["gojo"].moves[0].move_id])
    state, result, _seed = settle_matching(state, "auto")
    add("03-mark-auto-overload", state, result)

    state = state_for("xixi", "daniya", entries)
    state["sides"][1]["daniya_form"] = "disillusion"
    scripted_turn(state, 0, ["xixi-reality", "xixi-attack"])
    scripted_turn(state, 1, ["daniya-disillusion-knock"])
    state, result, _seed = settle_matching(state, "reality")
    add("04-reality-particles", state, result)

    state = state_for("xixi", "gojo", entries)
    scripted_turn(state, 0, ["xixi-hourglass"], raw=5)
    scripted_turn(state, 1, [FIGHTERS_BY_ID["gojo"].moves[0].move_id] * 3)
    state, result, _seed = settle_matching(state, "hourglass", lambda r: r["injury_skip_reason"] == "中亚沙漏")
    add("05-hourglass-no-injury-roll", state, result)

    state = state_for("daniya", "sukuna", entries)
    daniya_battle.enter_black_hole(state["sides"][0])
    state["sides"][1]["snapshot"]["level"] = 5
    state["sides"][1]["core"] = 10
    scripted_turn(state, 0, ["daniya-black-hole-red-supergiant"])
    ordinary = max((m for m in FIGHTERS_BY_ID["sukuna"].moves if "domain" not in m.tags), key=lambda m: m.gain)
    scripted_turn(state, 1, [ordinary.move_id] * 5)
    state, result, _seed = settle_matching(state, "black-light", lambda r: r["loser"] == 0 and r["injury"] == "injured")
    add("06-black-hole-light", state, result)
    scripted_turn(state, 0, ["daniya-black-hole-red-supergiant"])
    scripted_turn(state, 1, [ordinary.move_id] * 5)
    state, result, _seed = settle_matching(state, "black-heavy", lambda r: r["loser"] == 0 and r["injury"] == "injured")
    add("07-black-hole-heavy", state, result)

    state = state_for("xixi", "sukuna", entries)
    player = state["sides"][0]
    player.update(heavy=True, risk=2, injury_state="heavy",
                  xixi_hextech=["physical-to-magic", "mind-over-matter", "back-to-basics"],
                  xixi_hextech_queue=[{"due": 1, "token": "offline-fourth"}])
    battle.roll_count(state, 0, "offline-delayed-emperor")
    add("08-delayed-emperor-count", state)

    state = state_for("xixi", "daniya", entries)
    player = state["sides"][0]
    player.update(xixi_hextech=list(HEXTECH_NAMES), xixi_form="xixi-emperor")
    daniya_battle.enter_black_hole(state["sides"][1])
    scripted_turn(state, 0, ["xixi-attack"])
    scripted_turn(state, 1, ["daniya-black-hole-eternal-end"])
    state, result, _seed = settle_matching(state, "emperor-fixed", lambda r: r["injury"] == "none")
    add("09-emperor-fixed-protection", state, result)
    return cases, facts


def simulations(entries, count):
    reports = []
    for opponent in FIGHTERS_BY_ID:
        if opponent == "xixi":
            continue
        rounds, victories, emperors, hex_counts = [], 0, 0, []
        for trial in range(count):
            side = trial % 2
            fighters = ("xixi", opponent) if side == 0 else (opponent, "xixi")
            seed = f"v19-balance-{opponent}-{trial}"
            state = state_for(*fighters, entries, seed=seed)
            for _ in range(200):
                for actor in range(2):
                    battle.roll_count(state, actor, seed)
                for actor in (side, 1 - side):
                    for _chunk in range(200):
                        battle.play_chunk(state, actor, seed)
                        if state["sides"][actor]["turn"]["done"]:
                            break
                    else:
                        raise AssertionError(f"连招未结束：{seed}")
                battle.resolve_round(state, seed)
                if state["status"] == "completed":
                    break
            else:
                raise AssertionError(f"200回合仍未结束：{seed}")
            victories += state["winner"] == side
            emperors += state["sides"][side].get("xixi_form") == "xixi-emperor"
            hex_counts.append(len(state["sides"][side].get("xixi_hextech", ())))
            rounds.append(state["round"])
        reports.append({"opponent": opponent, "count": count, "xixi_win_rate": victories / count,
                        "emperor_count": emperors, "mean_rounds": statistics.mean(rounds),
                        "max_rounds": max(rounds), "mean_hextech": statistics.mean(hex_counts)})
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    return reports


async def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请使用新的证据目录")
    output.mkdir(parents=True)
    catalog = json.loads((PROJECT_ROOT / "asset_library/current/assets.json").read_text(encoding="utf-8"))
    entries = {entry["template_id"]: entry for entry in catalog["entries"]}
    cases, facts = visual_cases(entries)
    outputs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())
            for label, result in cases:
                capability.label = label
                paths = {pig.short_code: PROJECT_ROOT / "asset_library/current" / pig.image_relpath
                         for pig in result.pigs if pig.image_relpath}
                rendered = await renderer.render_battle(result, paths)
                destination = output / f"{label}.png"
                write_image(destination, rendered)
                (output / f"{label}.txt").write_text(result.text(), encoding="utf-8")
                outputs.append(destination)
        finally:
            await capability.close()
            await browser.close()
    failures = [row for row in capability.diagnostics
                if row["clippedText"] or row["outside"] or row["brokenImages"] or row.get("clippedMedia")]
    contact_sheet(outputs, output / "contact-sheet.jpg")
    numeric = simulations(entries, args.trials)
    report = {"status": "failed" if failures else "passed", "schema": SCHEMA_VERSION,
              "ruleset": RULESET_VERSION, "battle": BATTLE_VERSION, "rendered": len(outputs),
              "diagnostics": capability.diagnostics, "failures": failures, "simulations": numeric,
              "scope": "offline formal catalog; isolated scripted visual cases; deterministic simulation seeds"}
    (output / "mechanics.json").write_text(battle.dumps(facts), encoding="utf-8")
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        raise AssertionError(json.dumps(failures, ensure_ascii=False))
    print(json.dumps({"status": "passed", "rendered": len(outputs), "report": str(output / "report.json")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable", type=Path,
                        default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"))
    parser.add_argument("--trials", type=int, default=60)
    asyncio.run(run(parser.parse_args()))
