"""只读生产源、隔离迁移与午夜交接彩排，真实Chromium公告图；绝不发送QQ消息。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer  # noqa: E402
from pig_catcher.services.receipts import ReceiptService  # noqa: E402
from pig_catcher.services.weekly_competitions import WeeklyCompetitionService  # noqa: E402
from pig_catcher.services.weekly_transition import WeeklyTransitionService  # noqa: E402
from tests.test_weekly_competitions import MutableClock  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.rehearse_social_rewards_release import inspect, snapshot  # noqa: E402


async def run(source: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    before = snapshot(source, output / "source-snapshot.sqlite3")
    path = output / "isolated.sqlite3"
    shutil.copy2(output / "source-snapshot.sqlite3", path)
    db = PigCatcherDatabase(path)
    await db.open()
    clock = MutableClock(datetime(2026, 9, 7, 15, 59, tzinfo=UTC))
    try:
        weekly = WeeklyCompetitionService(db, clock=clock)
        await weekly.initialize()
        transition = WeeklyTransitionService(weekly)
        scopes = [
            str(row["scope_id"])
            for row in await db.fetch_all(
                "SELECT scope_id FROM scopes WHERE platform='qq-official' AND enabled=1 ORDER BY scope_id"
            )
        ]
        assert len(scopes) == 2
        await transition.schedule(previous_season=1, next_season=2, scope_ids=scopes)
        scheduled = inspect(path)
        for table in (
            "players",
            "pig_instances",
            "food_instances",
            "currency_ledger",
            "achievement_reward_inventory",
            "weekly_competition_awards",
            "command_receipts",
        ):
            assert before["counts"][table] == scheduled["counts"][table], table
        await transition.process_due()
        assert not await transition.pending_notices()
        clock.value += timedelta(minutes=1)
        await transition.process_due()
        notices = await transition.pending_notices()
        assert len(notices) == 2
        views = [(f"close-{index + 1}", result.view) for index, (_, result) in enumerate(notices)]
        receipts = ReceiptService(db, clock=clock)
        for _, result in notices:
            assert await receipts.claim_send(result.receipt.receipt_id)
            assert await receipts.mark_sent(result.receipt.receipt_id)
        clock.value += timedelta(seconds=119)
        await transition.process_due()
        assert not await transition.pending_notices()
        clock.value += timedelta(seconds=1)
        await transition.process_due()
        notices = await transition.pending_notices()
        assert len(notices) == 2
        views += [(f"open-{index + 1}", result.view) for index, (_, result) in enumerate(notices)]
        for _, result in notices:
            assert await receipts.claim_send(result.receipt.receipt_id)
            assert await receipts.mark_sent(result.receipt.receipt_id)
        after = inspect(path)
        await transition.process_due()
        await weekly.initialize()
        assert after["counts"] == inspect(path)["counts"]
        assert after["quick_check"] == "ok" and not after["foreign_key_errors"] and not after["ledger_mismatches"]
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
                    write_image(output / f"{label}.png", await renderer.render_dispatch(view, {}))
                    (output / f"{label}.txt").write_text(view.text(), encoding="utf-8")
            finally:
                await capability.close()
                await browser.close()
        failures = [
            row
            for row in capability.diagnostics
            if row["clippedText"] or row["outside"] or row["brokenImages"] or row["rootClient"] != row["rootScroll"]
        ]
        report = {
            "source_read_only": True,
            "real_QQ_sends": 0,
            "before": before,
            "scheduled": scheduled,
            "after": after,
            "diagnostics": capability.diagnostics,
            "failures": failures,
            "note": "隔离快照中的提前彩排，不是正式结算或实际群内送达。",
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if failures:
            raise RuntimeError(json.dumps(failures, ensure_ascii=False))
        return {
            "status": "passed",
            "images": len(views),
            "award_count": after["counts"]["weekly_competition_awards"],
            "output": str(output),
        }
    finally:
        await db.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.source, args.output)), ensure_ascii=False))
