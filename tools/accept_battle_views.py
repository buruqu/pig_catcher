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
    moves = (
        fighter_form_moves(fighter_id, player["juejue_form"])
        if fighter_id == "juejue"
        else FIGHTERS_BY_ID[fighter_id].moves
    )
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


def deterministic_mechanic_cases(
    initial_state: dict,
    initial_match: dict,
    identity: CommandIdentity,
    now_ms: int,
    juejue_entry: dict,
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
        assert domain and tuple(domain["wheel"]) == (("side-0", 8), ("side-1", 6), ("tie", 6))
        assert domain["weight_scale"] == 2
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
    ids = {fighter.template_id for fighter in public_fighters}
    for star in range(1, 6):
        ids.add(next(entry["template_id"] for entry in public if entry["rarity"] == star))
    entries = [entry for entry in public if entry["template_id"] in ids]
    entries.extend((juejue_entry, juejue_food_entry))
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
        long_view = cases[10][1]
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
    failures = [row for row in capability.diagnostics if row["clippedText"] or row["outside"] or row["brokenImages"]]
    report = {
        "status": "failed" if failures else "passed",
        "count": len(outputs),
        "diagnostics": capability.diagnostics,
        "failures": failures,
        "deterministic_mechanics": mechanic_evidence,
        "scope": "isolated offline data and public art; no production or QQ connection",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
