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
from pig_catcher.domain.battle import loads  # noqa: E402
from pig_catcher.domain.battle_catalog import FIGHTERS  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.repositories.dispatch import timestamp_ms  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, media_path  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402
from pig_catcher.services.battle import BattleService  # noqa: E402
from pig_catcher.services.battle_views import matchup, view  # noqa: E402
from tests.test_battle import BattleWorld  # noqa: E402
from tests.test_dispatch import NOW, seed_pigs  # noqa: E402
from tests.test_gameplay import MutableClock  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


async def scenarios(output: Path):
    catalog_root = PROJECT_ROOT / "asset_library/current"
    catalog = json.loads((catalog_root / "assets.json").read_text(encoding="utf-8"))
    public = [entry for entry in catalog["entries"] if entry["kind"] == "pig" and entry["scope"] == "common"]
    ids = {fighter.template_id for fighter in FIGHTERS}
    for star in range(1, 6):
        ids.add(next(entry["template_id"] for entry in public if entry["rarity"] == star))
    entries = [entry for entry in public if entry["template_id"] in ids]
    if not all(fighter.template_id in {entry["template_id"] for entry in entries} for fighter in FIGHTERS):
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
        for actor, fighter in zip((a, b), FIGHTERS, strict=True):
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
        cases.append(("10-invitation", (await w.invite()).view))
        cases.append(("11-entry", (await w.send("接受", "challenge", actor=b)).view))
        initial_match = await w.match()
        initial_state = loads(initial_match["state_json"])
        cases.append(("12-action-count", (await w.send(section="count")).view))
        cases.append(("13-moves", (await w.send(section="move")).view))
        for side, actor in enumerate((a, b)):
            while True:
                current = loads((await w.match())["state_json"])
                turn = current["sides"][side]["turn"]
                if turn["raw"] is None:
                    await w.send(section="count", actor=actor)
                elif not turn["done"]:
                    await w.send(section="move", actor=actor)
                else:
                    break
        cases.append(("13b-ready-waiting", (await w.send(section="ready", actor=a)).view))
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
        return cases, data_root
    finally:
        await db.close()


async def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请使用新的验收目录，不覆盖既有证据。")
    output.mkdir(parents=True)
    cases, data_root = await scenarios(output)
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
