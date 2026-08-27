"""用正式授权猪立绘和全新离线数据验收巡演图片；不连接QQ或读取运行数据。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.commands.tour import TourRequest  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, media_path  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402
from pig_catcher.services.tour import TourService  # noqa: E402
from tests.test_dispatch import NOW  # noqa: E402
from tests.test_gameplay import MutableClock  # noqa: E402
from tests.test_tour import TourWorld, character  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


async def scenarios(root: Path):
    source = root / "inputs"
    source.mkdir()
    catalog_root = PROJECT_ROOT / "asset_library/current"
    catalog = json.loads((catalog_root / "assets.json").read_text(encoding="utf-8"))
    selected_ids = {character(c).template_id for c in ("kasumi", "tomoe", "layer", "hina", "sayo", "ako")}
    entries = [entry for entry in catalog["entries"] if entry["template_id"] in selected_ids]
    if len(entries) != len(selected_ids):
        raise ValueError("正式素材缺少已确认的巡演角色。")
    for entry in entries:
        destination = source / entry["image"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(catalog_root / entry["image"], destination)
    catalog["entries"] = entries
    manifest = source / "assets.json"
    manifest.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    data_root = root / "isolated-fixture"
    db = PigCatcherDatabase(data_root / "tour-test.sqlite3")
    await db.open()
    try:
        await AssetCatalogService(
            db, AssetCatalogStorage(data_root), min_image_side=32, max_image_bytes=32 * 1024 * 1024
        ).import_manifest(manifest)
        identity = CommandIdentity(
            ScopeKey("qq-official", "fixture-only"),
            "fixture-stream",
            "fixture-member",
            "把每一种声部都记住的群友",
            "seed",
            "离线巡演验收群",
        )
        clock = MutableClock(NOW)
        world = TourWorld(db, clock, TourService(db, clock=clock, seed_factory=lambda: "tour-visual-seed"), identity)
        cases = [("01-empty-band", (await world.send("", "band")).view)]
        await world.form(ids=("hina", "sayo", "tomoe", "ako", "layer"))
        await world.fund()
        cases.append(("02-five-member-band", (await world.send("", "band")).view))
        cases.append(("03-roster-confirmation", (await world.send("编队 2 天才猪、巴巴猪、LAYER猪", "band")).view))
        await world.send("取消")
        cases.append(("04-free-rehearsal", (await world.send("排练")).view))
        cases.append(("05-departure-confirmation", (await world.send("出发")).view))
        cases.append(("06-departed", (await world.send("确认")).view))
        cases.append(("07-first-stage", (await world.send("继续")).view))
        cases.append(("08-finale", (await world.send("一键")).view))
        cases.append(("09-practice-confirmation", (await world.send("练习 1", "band")).view))
        cases.append(("10-practice-completed", (await world.send("确认")).view))
        cases.append(("11-equipment", (await world.send("器材", "band")).view))
        cases.append(("12-upgrade-confirmation", (await world.send("器材 升级", "band")).view))
        cases.append(("13-upgrade-completed", (await world.send("确认")).view))
        cases.append(("14-tools", (await world.send("器具", "band")).view))
        cases.append(("15-craft-completed", (await world.send("制作 礼花 3", "band")).view))
        cases.append(("16-venues", (await world.send("场地")).view))
        cases.append(("17-themes", (await world.send("主题")).view))
        cases.append(("18-song-cards", (await world.send("曲库 1")).view))
        cases.append(("19-character-signatures", (await world.send("角色 8", "band")).view))
        cases.append(("20-ensembles-long", (await world.send("合奏")).view))
        cases.append(("21-collections", (await world.send("收藏 1", "journal")).view))
        cases.append(("22-journal", (await world.send("1", "journal")).view))
        partner = replace(identity, user_id="other-member", display_name="另一位自由混团的群友")
        await world.form(partner)
        await world.service.execute(
            replace(identity, message_id="invite"), TourRequest("joint_invite", {"target_user_id": partner.user_id})
        )
        cases.append(("23-joint-invitation", (await world.send("", "joint", identity=partner)).view))
        cases.append(("24-joint-finale", (await world.send("接受", "joint", identity=partner)).view))
        cases.append(("25-protection-confirmation", (await world.send("解除保护 1", "band")).view))
        cases.append(("26-missing-media", cases[1][1]))
        cases.append(
            (
                "27-long-names",
                replace(
                    cases[1][1],
                    band_name="这是完整三十二字乐队名字加上EnglishABCD也需要漂亮换行12345678901234567890",
                    player_name="ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 3,
                    pigs=tuple(
                        replace(pig, name="中英文超长名字测试ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 2)
                        for pig in cases[1][1].pigs
                    ),
                ),
            )
        )
        cases.append(
            (
                "28-original-theme-costume",
                replace(
                    cases[1][1],
                    band_name="我们的假面剧场",
                    costume="整场演出就是一幕剧",
                    emblem="◈",
                    color="#c85763",
                    celebration=True,
                ),
            )
        )
        return cases, data_root
    finally:
        await db.close()


async def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请使用新的验收目录，不能覆盖现有证据。")
    output.mkdir(parents=True)
    cases, data_root = await scenarios(output)
    outputs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())
            for name, view in cases:
                paths = {
                    pig.short_code: media_path(data_root, pig.image_relpath) for pig in view.pigs if pig.image_relpath
                }
                if name == "26-missing-media":
                    paths = {}
                capability.label = name
                image = await renderer.render_tour(view, paths)
                destination = output / f"{name}.png"
                write_image(destination, image)
                (output / f"{name}.txt").write_text(view.text(), encoding="utf-8")
                outputs.append(destination)
        finally:
            await capability.close()
            await browser.close()
    failures = [row for row in capability.diagnostics if row["clippedText"] or row["outside"] or row["brokenImages"]]
    report = {
        "status": "failed" if failures else "passed",
        "count": len(outputs),
        "outputs": [str(p) for p in outputs],
        "diagnostics": capability.diagnostics,
        "failures": failures,
        "scope": "isolated offline fixture; no production data, process or QQ connection",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_sheet(outputs, output / "contact-sheet.jpg")
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    return {
        "status": "passed",
        "count": len(outputs),
        "report": str(output / "report.json"),
        "contact_sheet": str(output / "contact-sheet.jpg"),
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
