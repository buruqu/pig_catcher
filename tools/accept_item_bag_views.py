"""在新隔离库验收道具背包及两券的真实美术，不连接生产或QQ。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.domain.activity_achievements import ACTIVITY_REWARDS  # noqa: E402
from pig_catcher.domain.gameplay import ITEM_DEFINITIONS  # noqa: E402
from pig_catcher.domain.item_bag import CODE_CHANGE_COUPON, PIG_CHOICE_COUPON  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.repositories.achievement_coupons import AchievementCouponRepository  # noqa: E402
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository  # noqa: E402
from pig_catcher.infrastructure.repositories.dispatch import iso_ms, timestamp_ms  # noqa: E402
from pig_catcher.infrastructure.repositories.economy import EconomyRepository  # noqa: E402
from pig_catcher.infrastructure.repositories.gameplay import GameplayRepository  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, media_path  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402
from pig_catcher.services.item_bag import ItemBagService  # noqa: E402
from tests.test_dispatch import NOW  # noqa: E402
from tests.test_gameplay import MutableClock, SequenceRandom  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


async def scenarios(root: Path):
    package = PROJECT_ROOT / "asset_library/current"
    manifest = json.loads((package / "assets.json").read_text(encoding="utf-8"))
    pigs = [
        entry
        for entry in manifest["entries"]
        if entry["kind"] == "pig"
        and (
            entry["display_name"] in {"口琴猪", "阿拉蕾猪"}
            or (entry["display_name"] == "熠～噜猪" and str(entry.get("group_scope_id", "")).endswith("CEAB3520"))
        )
    ]
    if len(pigs) != 3:
        raise ValueError("需口琴猪、阿拉蕾猪及CEAB3520作用域的熠～噜猪三张现有素材。")
    paired = {entry["paired_food_template_id"] for entry in pigs if entry.get("paired_food_template_id")}
    selected = pigs + [entry for entry in manifest["entries"] if entry["template_id"] in paired]
    source = root / "inputs"
    for entry in selected:
        destination = source / entry["image"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / entry["image"], destination)
    manifest["entries"] = selected
    (source / "assets.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    data = root / "isolated-data"
    db = PigCatcherDatabase(data / "items.sqlite3")
    await db.open()
    try:
        await AssetCatalogService(
            db, AssetCatalogStorage(data), min_image_side=32, max_image_bytes=32 * 1024 * 1024
        ).import_manifest(source / "assets.json")
        scope = ScopeKey.parse(next(entry["group_scope_id"] for entry in pigs if entry["rarity"] == 6))
        actor = CommandIdentity(
            scope, "fixture-stream", "fixture-user", "名字很长也要看清的道具试用员", "seed", "隔离验收"
        )
        service = ItemBagService(db, clock=MutableClock(NOW), random_source=SequenceRandom(*([0.5] * 20)))
        now = iso_ms(timestamp_ms(NOW))
        cases = [("01-empty-bag", (await service.bag(actor)).view)]

        async def use(text: str, message: str | None = None):
            return await service.execute(replace(actor, message_id=message or uuid4().hex), text)

        async with db.transaction() as session:
            for coupon in (CODE_CHANGE_COUPON, PIG_CHOICE_COUPON):
                await service.grant_coupon(
                    session,
                    player_id=actor.player_id,
                    scope_id=scope.value,
                    coupon_id=coupon,
                    quantity=3,
                    source_id="visual-fixture",
                    now=now,
                )
        cases.append(("02-choice-public-preview", (await use("猪猪自选券 阿拉蕾猪")).view))
        received = await use("确认")
        cases.append(("03-choice-public-receipt", received.view))
        code = received.view.pigs[0].short_code
        cases.append(("04-rename-receipt", (await use(f"编号修改券 猪猪 阿拉蕾猪#{code} Mixed947")).view))
        cases.append(("05-choice-six-preview", (await use("猪猪自选券 熠～噜猪", "private-preview")).view))
        cases.append(("06-choice-six-receipt", (await use("确认")).view))
        await use("猪猪自选券 口琴猪")
        cases.append(("07-cancelled", (await use("取消")).view))
        async with db.transaction() as session:
            for item in ITEM_DEFINITIONS:
                await EconomyRepository().add_item_inventory(
                    session, player_id=actor.player_id, item_id=item.item_id, quantity=12345, now=now
                )
            for item_id, action in (("lucky-whistle", "catching"), ("chef-spice", "cooking")):
                await GameplayRepository().arm_item(
                    session, player_id=actor.player_id, action_type=action, item_id=item_id, remaining_uses=100, now=now
                )
            for reward_id, definition in ACTIVITY_REWARDS.items():
                if definition["kind"] in {"ticket", "chest"}:
                    await AchievementRepository().grant_reward(
                        session,
                        player_id=actor.player_id,
                        reward_type=definition["kind"],
                        reward_id=reward_id,
                        quantity=48,
                        now=now,
                    )
            await AchievementCouponRepository().select(session, actor.player_id, "dispatch-luggage", now)
            await AchievementCouponRepository().select(session, actor.player_id, "tour-steady-stage", now)
            for table, tool in (
                ("dispatch_tools", "region-map"),
                ("tour_tools", "cable"),
                ("battle_tools", "wristband"),
            ):
                await session.execute(f"INSERT INTO {table} VALUES(?,?,5)", (actor.player_id, tool))
        first = (await service.bag(actor)).view
        for page in range(1, first.page_count + 1):
            cases.append((f"08-bag-page-{page}", (await service.bag(actor, page)).view))
        cases.append(("09-missing-image", cases[2][1]))
        async with db.transaction() as session:
            await session.execute("UPDATE scope_pig_templates SET authorized=0 WHERE scope_id=?", (scope.value,))
        cases.append(("10-revoked-private-replay", (await use("猪猪自选券 熠～噜猪", "private-preview")).view))
        return cases, data
    finally:
        await db.close()


async def run(args) -> dict:
    output = args.output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / "artifacts").resolve()):
        raise ValueError("验收只能写入开发仓库的artifacts目录。")
    if output.exists():
        raise FileExistsError("请指定新的验收目录，不覆盖既有结果。")
    output.mkdir(parents=True)
    cases, data = await scenarios(output)
    outputs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())
            for name, view in cases:
                paths = {pig.short_code: media_path(data, pig.image_relpath) for pig in view.pigs if pig.image_relpath}
                if name == "09-missing-image":
                    paths = {}
                capability.label = name
                rendered = await renderer.render_dispatch(view, paths)
                destination = output / f"{name}.png"
                write_image(destination, rendered)
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
        "scope": "new isolated database; no QQ connection or production mutation",
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
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
