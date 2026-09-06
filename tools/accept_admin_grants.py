"""在全新隔离库执行真实发放并验收图片；不连接生产库或QQ。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.infrastructure.repositories.framework import FrameworkRepository  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer  # noqa: E402
from pig_catcher.rendering.admin_grants import admin_grant_view  # noqa: E402
from pig_catcher.services.administration import AdministrationService  # noqa: E402
from tests.test_admin_commands import _identity  # noqa: E402
from tests.test_gameplay import _database_with_catalog, _food_entry, _pig_entry  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


async def run(output: Path, executable: Path) -> dict[str, object]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    db = await _database_with_catalog(
        output,
        [
            _pig_entry("demo-pig", rarity=5, display_name="离线验收猪"),
            _food_entry(
                "demo-food",
                rarity=5,
                group_id=None,
                effect_id="",
                effect_params={},
                display_name="离线验收超长名称美食",
            ),
        ],
    )
    outputs = []
    try:
        actor = _identity(user_id="admin", display_name="离线管理员")
        target = _identity(user_id="member", display_name="群昵称特别长也要完整看清的奖励获得者")
        async with db.transaction() as session:
            for identity in (actor, target):
                await FrameworkRepository().touch_identity(session, identity=identity, now="2026-09-06T00:00:00Z")
        service = AdministrationService(db, refresh_hours=(0, 9, 12, 19), timezone_name="Asia/Shanghai")
        cases = [
            ("01-single-coupon", "编号修改券", 3, False),
            ("02-all-shop-item", "超级幸运猪哨", 5, True),
            ("03-all-pigs", "离线验收猪", 10, True),
            ("04-single-foods", "离线验收超长名称美食", 20, False),
            ("05-capped-upgrade", "厨具", 30, False),
        ]
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(executable))
            capability = PlaywrightRenderCapability(browser)
            await capability.open()
            try:
                renderer = PigCatcherRenderer(capability, render_options())
                for label, name, quantity, all_players in cases:
                    result = await service.grant_resource(
                        replace(actor, message_id=label),
                        command_name="pig-catcher.admin-grant-resource",
                        selector=name,
                        quantity=quantity,
                        all_players=all_players,
                        target_user_id="" if all_players else target.user_id,
                    )
                    capability.label = label
                    image = await renderer.render_economy_receipt(admin_grant_view(result.receipt))
                    path = output / f"{label}.png"
                    write_image(path, image)
                    outputs.append(path)
            finally:
                await capability.close()
                await browser.close()
    finally:
        await db.close()
    failures = [
        row
        for row in capability.diagnostics
        if row["clippedText"] or row["outside"] or row["brokenImages"] or row["rootClient"] != row["rootScroll"]
    ]
    report = {
        "count": len(outputs),
        "failures": failures,
        "diagnostics": capability.diagnostics,
        "scope": "fresh isolated database, fixture users, no QQ sends",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_sheet(outputs, output / "contact-sheet.jpg")
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False))
    return {"status": "passed", "count": len(outputs), "output": str(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output, args.browser_executable)), ensure_ascii=False, indent=2))
