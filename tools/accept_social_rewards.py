"""Real Chromium images for offline red packets and birthday benefit delivery."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pig_catcher.domain.red_packets import PacketRequest  # noqa: E402
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, pig_card_view  # noqa: E402
from pig_catcher.services.command_state import iso_timestamp  # noqa: E402
from pig_catcher.services.gameplay import pig_view_from_row  # noqa: E402
from pig_catcher.services.red_packets import RedPacketService  # noqa: E402
from pig_catcher.services.scheduled_rewards import BIRTHDAY_AT, ScheduledRewardService  # noqa: E402
from tests.test_admin_commands import _identity  # noqa: E402
from tests.test_gameplay import MutableClock, _database_with_catalog, _food_entry, _pig_entry  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


async def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    db = await _database_with_catalog(
        output,
        [
            _pig_entry("birthday-pig", rarity=5, display_name="撅撅猪"),
            _food_entry(
                "birthday-food", rarity=5, group_id=None, effect_id="", effect_params={}, display_name="撅撅猪派"
            ),
        ],
    )
    clock = MutableClock(BIRTHDAY_AT - timedelta(hours=1))
    packets = RedPacketService(db, clock=clock)
    campaigns = ScheduledRewardService(db, clock=clock)
    actor = _identity(user_id="admin", display_name="猪管·今天生日呀")
    outputs = []
    try:
        async with db.transaction() as session:
            for index in range(12):
                player = replace(actor, user_id=f"p{index}", display_name=f"一起来庆生的快乐猪友{index}")
                await FrameworkRepository().touch_identity(session, identity=player, now=iso_timestamp(clock.now()))
        sent = await packets.send(
            actor, PacketRequest(9600, 12, "生日快乐！愿你每一次伸手，都能接住满满的好运。"), admin=True
        )
        views = [("01-system-packet", sent.view)]
        for index in range(12):
            result = await packets.claim(
                replace(
                    actor, user_id=f"p{index}", display_name=f"一起来庆生的快乐猪友{index}", message_id=f"claim{index}"
                )
            )
            if index in (0, 11):
                views.append((f"02-claim-{index}", result.view))
        await campaigns.schedule_birthday([actor.scope.value], numbering_confirmed=True)
        clock.value = BIRTHDAY_AT
        await campaigns.process_due()
        views.append(("03-birthday", (await campaigns.pending_notices())[0][1].view))
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True, executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe"
            )
            capability = PlaywrightRenderCapability(browser)
            await capability.open()
            try:
                renderer = PigCatcherRenderer(capability, render_options())
                for label, view in views:
                    capability.label = label
                    path = output / f"{label}.png"
                    write_image(path, await renderer.render_dispatch(view, {}))
                    outputs.append(path)
                row = dict(await db.fetch_one("SELECT * FROM pig_instances LIMIT 1"))
                row["media_visible"] = True
                pig = pig_view_from_row(row)
                capability.label = "04-memorial-pig"
                view = pig_card_view(pig, mode_label="生日纪念猪")
                path = output / "04-memorial-pig.png"
                write_image(path, await renderer.render_static_pig_card(view, output / "source/birthday-pig.png"))
                outputs.append(path)
            finally:
                await capability.close()
                await browser.close()
        failures = [
            row
            for row in capability.diagnostics
            if row["clippedText"] or row["outside"] or row["brokenImages"] or row["rootClient"] != row["rootScroll"]
        ]
        (output / "report.json").write_text(
            json.dumps({"failures": failures, "diagnostics": capability.diagnostics}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        contact_sheet(outputs, output / "contact-sheet.jpg")
        if failures:
            raise RuntimeError(json.dumps(failures, ensure_ascii=False))
        return {"count": len(outputs), "output": str(output), "status": "passed"}
    finally:
        await db.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(asyncio.run(run(parser.parse_args().output)), ensure_ascii=False))
