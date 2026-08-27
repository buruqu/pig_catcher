"""仅在新建隔离目录生成派遣完整场景与 Chromium 图片，绝不读取运行数据库。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont, ImageOps
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.domain.dispatch import random_at  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, media_path  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402
from pig_catcher.services.dispatch import DispatchService  # noqa: E402
from tests.test_dispatch import NOW, SAFE_SEED, World, seed_pigs  # noqa: E402
from tests.test_gameplay import MutableClock  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)


async def scenarios(root: Path):
    source = root / "inputs"
    source.mkdir()
    catalog_root = PROJECT_ROOT / "asset_library/current"
    catalog = json.loads((catalog_root / "assets.json").read_text(encoding="utf-8"))
    names = ("口琴猪", "特小猪", "大象")
    selected = [entry for entry in catalog["entries"] if entry["kind"] == "pig" and entry["display_name"] in names]
    if len(selected) != 3:
        raise ValueError("视觉验收素材必须包含口琴猪、特小猪和大象。")
    for entry in selected:
        destination = source / entry["image"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(catalog_root / entry["image"], destination)
    catalog["entries"] = selected
    manifest = source / "assets.json"
    manifest.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    data_root = root / "isolated-fixture"
    db = PigCatcherDatabase(data_root / "dispatch-test.sqlite3")
    await db.open()
    try:
        await AssetCatalogService(
            db, AssetCatalogStorage(data_root), min_image_side=32, max_image_bytes=32 * 1024 * 1024
        ).import_manifest(manifest)
        identity = CommandIdentity(
            ScopeKey("qq-official", "fixture-only"),
            "fixture-stream",
            "fixture-member",
            "名字特别长也要看清的远行社群友",
            "seed",
            "离线验收群",
        )
        clock = MutableClock(NOW)
        world = World(db, clock, DispatchService(db, clock=clock, seed_factory=lambda: SAFE_SEED), identity)
        results = [
            ("01-empty", (await world.send()).view),
            ("02-routes", (await world.send("路线")).view),
            ("03-recipes", (await world.send("配方", "bag")).view),
        ]
        for entry in selected:
            await seed_pigs(db, identity, template_id=entry["template_id"], count=3)
        for key, qty in (("travel-supplies", 40), ("machine-parts", 20), ("travel-notes", 3)):
            await world.material(key, qty)
        await world.send("制作 奇遇罗盘 2", "bag")
        async with db.transaction() as session:
            await session.execute("UPDATE players SET coin_balance=10000 WHERE player_id=?", (identity.player_id,))
        members = "、".join(names)
        results.append(("04-team-preview", (await world.send(f"编队 1 {members}")).view))
        await world.send("确认")
        results.append(("05-start-preview", (await world.send("出发 1 回声矿洞 24小时 奇遇罗盘 2")).view))
        results.append(("06-departed", (await world.send("确认")).view))
        first_trip = (await db.fetch_one("SELECT trip_id FROM dispatch_trips"))[0]
        await world.advance(1)
        results.append(("07-countdown", (await world.send()).view))
        # 仅用于长页验收的确定性随机源；不修改任何真实数据或生产规则。
        with patch(
            "pig_catcher.infrastructure.repositories.dispatch.random_at",
            side_effect=lambda seed, block, key: 0.0 if key == "encounter" else random_at(seed, block, key),
        ):
            await world.advance(23)
        results.append(("08-return", (await world.send("返程")).view))
        results.append(("09-six-encounters", (await world.send(first_trip, "journal")).view))
        results.append(("10-pending-choice", (await world.send("", "encounters")).view))
        choice = (await db.fetch_one("SELECT choice_id FROM dispatch_choices"))[0]
        results.append(("11-choice-result", (await world.send(f"{choice} 1", "encounters")).view))
        await world.start(hours=24)
        await world.advance(5)
        results.append(("12-recall-preview", (await world.send("召回 1")).view))
        results.append(("13-recalled", (await world.send("确认")).view))
        async with db.transaction() as session:
            await session.execute(
                "UPDATE dispatch_profiles SET effective_seconds=? WHERE player_id=?", (72 * 3600, identity.player_id)
            )
        for slot, route in ((1, "回声矿洞"), (2, "废旧工坊"), (3, "风铃林地")):
            await world.team(slot, members)
            await world.start(route, hours=8, slot=slot)
        results.append(("14-three-teams", (await world.send()).view))
        await world.advance(8)
        results.append(("15-three-returns", (await world.send("返程")).view))
        results.append(("16-material-bag", (await world.send("", "bag")).view))
        results.append(("17-journal", (await world.send("1", "journal")).view))
        results.append(("18-souvenirs", (await world.send("纪念品", "journal")).view))
        results.append(("19-missing-media", results[3][1]))
        results.append(
            (
                "20-long-text",
                replace(
                    results[3][1],
                    pigs=tuple(
                        replace(pig, name="这是一个含超长名字的猪猪用来测试中英文自动换行ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                        for pig in results[3][1].pigs
                    ),
                ),
            )
        )
        return results, data_root
    finally:
        await db.close()


def contact_sheet(outputs: list[Path], target: Path) -> None:
    width, height = 1600, ((len(outputs) + 3) // 4) * 510 + 40
    canvas = Image.new("RGB", (width, height), "#fff7fa")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
    for i, source in enumerate(outputs):
        with Image.open(source) as image:
            preview = ImageOps.contain(image.convert("RGB"), (375, 468))
        x, y = 15 + (i % 4) * 400, 15 + (i // 4) * 510
        canvas.paste(preview, (x, y))
        draw.text((x, y + 477), source.stem, fill="#70445d", font=font)
    canvas.save(target, quality=91)


async def run(args) -> dict:
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("验收输出必须是新的隔离目录，避免覆盖旧证据。")
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
                if name == "19-missing-media":
                    paths = {}
                capability.label = name
                image = await renderer.render_dispatch(view, paths)
                destination = output / f"{name}.png"
                write_image(destination, image)
                outputs.append(destination)
                (output / f"{name}.txt").write_text(view.text(), encoding="utf-8")
        finally:
            await capability.close()
            await browser.close()
    failures = [row for row in capability.diagnostics if row["clippedText"] or row["outside"] or row["brokenImages"]]
    report = {
        "status": "failed" if failures else "passed",
        "outputs": [str(path) for path in outputs],
        "diagnostics": capability.diagnostics,
        "failures": failures,
        "scope": "isolated synthetic fixture only; no production database or QQ connection",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
