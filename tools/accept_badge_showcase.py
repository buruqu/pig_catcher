"""Offline three-badge layouts and original animation acceptance; never opens a live database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pig_catcher.domain.battle_views import BattleView  # noqa: E402
from pig_catcher.domain.dispatch_views import DispatchLine, DispatchPanel, DispatchView  # noqa: E402
from pig_catcher.domain.tour_views import TourView  # noqa: E402
from pig_catcher.rendering import AnimatedCardComposer, PigCatcherRenderer  # noqa: E402
from pig_catcher.rendering.models import AchievementOverviewViewModel, InventoryViewModel  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    animation_report,
    pig_card,
    render_options,
    write_contact_sheet,
    write_image,
)
from tools.accept_cooking_and_economy_views import food_card  # noqa: E402
from tools.accept_result_cosmetics import formal_row, result_geometry, thumbnail_320  # noqa: E402


async def accept(output: Path, executable: Path) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite evidence: {output}")
    output.mkdir(parents=True)
    thumbs = output / "thumbnails-320"
    thumbs.mkdir()
    library = ROOT / "asset_library/current"
    entries = json.loads((library / "assets.json").read_text(encoding="utf-8"))["entries"]
    rows = {
        name: formal_row(next(entry for entry in entries if entry["display_name"] == name), library)
        for name in ("五条猪", "糖醋排骨", "撅撅猪")
    }
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for _, path in rows.values()}
    badges = ("kfc-thursday", "achievement-choice", "weekly-001-catch-value-rank-1")
    outfit = dict(
        achievement_title="rain-love",
        achievement_frame="hollow-purple",
        achievement_badges=badges,
        achievement_badge_capacity=3,
    )
    pig, pig_path = rows["五条猪"]
    food, food_path = rows["糖醋排骨"]
    animated, animation_path = rows["撅撅猪"]
    if not animated["is_animated"]:
        raise AssertionError("Must use original animated art, not a fabricated static replacement")
    base_pig = replace(
        pig_card(pig, mode_label="抓猪成功"), **outfit, probability_line="1★40% 2★30% 3★17% 4★8% 5★4% 6★1%"
    )
    base_food = replace(
        food_card(food, cooking=True),
        **outfit,
        probability_line="5★90% 6★10%",
        effect_summary="长效果排版验收；" * 50 + "【效果结束】",
    )
    overview = AchievementOverviewViewModel(
        display_name="三槽验收员",
        points=500,
        unlocked_count=30,
        total_count=130,
        completion_percent=23.1,
        title_text="雨爱",
        frame_text="虚式边框",
        showcase_text="3 / 3 格已佩戴",
        next_milestone_text="750 点",
        reward_inventory_text="三格徽章展示架（永久外观）",
        recent=(),
        **outfit,
    )
    receipt = DispatchView(
        "我的徽章展示架",
        "三槽验收员",
        subtitle="PiG Dream! · 永久外观",
        presentation="cosmetics",
        banner="第3位已佩戴本期周榜牌；所有概率、猪币、经验保持原样。",
        stats=(DispatchLine("展示位", "3格"), DispatchLine("徽章收藏", "3枚")),
        panels=(
            DispatchPanel(
                "独立槽位",
                (
                    DispatchLine("第1位", "疯狂星期四的邀约"),
                    DispatchLine("第2位", "成就自选徽章"),
                    DispatchLine("第3位", "抓猪冲刺！！！·1牌"),
                ),
            ),
        ),
        hints=("/成就徽章 2 成就自选徽章", "/成就徽章 卸下 2"),
        **outfit,
    )
    cases = [
        ("01-catch-three", "pig", base_pig, pig_path),
        ("02-cook-three-long", "food", base_food, food_path),
        ("03-overview-three", "overview", overview, None),
        ("04-inventory-three", "inventory", InventoryViewModel("验收员", 1, 1, 0, None, "价值", (), **outfit), None),
        ("05-dispatch-three", "dispatch", DispatchView("派遣归航", "验收员", **outfit), None),
        ("06-tour-three", "tour", TourView("巡演落幕", "验收员", **outfit), None),
        ("07-battle-three", "battle", BattleView("比划结束", "验收员", **outfit), None),
        ("08-showcase-three", "dispatch", receipt, None),
        ("09-showcase-empty-three", "dispatch", replace(receipt, achievement_badges=("", "", "")), None),
        (
            "10-legacy-one",
            "pig",
            replace(base_pig, achievement_badges=(), achievement_badge=badges[0], achievement_badge_capacity=1),
            pig_path,
        ),
        (
            "11-three-weekly-plates",
            "pig",
            replace(base_pig, achievement_badges=tuple(f"weekly-001-catch-value-rank-{rank}" for rank in (1, 2, 10))),
            pig_path,
        ),
        (
            "12-animated-three",
            "animation",
            replace(pig_card(animated, mode_label="抓猪成功"), **outfit),
            animation_path,
        ),
    ]
    options = render_options()
    records, outputs = [], []
    animation = None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        renderer = PigCatcherRenderer(capability, options)
        composer = AnimatedCardComposer(max_output_bytes=options.max_animation_bytes)
        try:
            for label, kind, view, path in cases:
                capability.label = label
                if kind == "pig":
                    image = await renderer.render_static_pig_card(view, path)
                elif kind == "food":
                    image = await renderer.render_static_food_card(view, path)
                elif kind == "animation":
                    base = await renderer.render_pig_card_base(view)
                    image = await composer.compose(base=base.image, source_path=path, slot=base.media_slot)
                elif kind == "overview":
                    image = await renderer.render_achievement_overview(view)
                else:
                    image = await getattr(renderer, f"render_{kind}")(view, {})
                destination = output / f"{label}{'.gif' if image.is_animated else '.png'}"
                write_image(destination, image)
                thumbnail_320(destination, thumbs / f"{label}.png")
                outputs.append((label, destination))
                geometry = await result_geometry(capability)
                geometry.pop("text")
                slots = await capability.page.locator(".cosmetic-badge-rack").first.locator("[data-badge-slot]").count()
                assert slots == view.achievement_badge_capacity, (label, slots)
                if kind == "animation":
                    animation = animation_report(
                        path, destination, missing_duration_ms=options.missing_frame_duration_ms
                    )
                records.append(
                    dict(
                        label=label,
                        path=str(destination),
                        width=image.width,
                        height=image.height,
                        slots=slots,
                        dom=capability.diagnostics[-1],
                        geometry=geometry,
                    )
                )
                print(f"rendered {label}: {image.width}x{image.height}, slots={slots}", flush=True)
        finally:
            await capability.close()
            await browser.close()
    issues = [
        record["label"]
        for record in records
        if any(record["dom"][key] for key in ("clippedText", "outside", "brokenImages"))
        or record["geometry"]["overlaps"]
        or record["geometry"]["excessiveGaps"]
    ]
    preserved = all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    report = dict(
        scope="Offline layout fixtures and formal source art; no live state or QQ",
        count=len(records),
        records=records,
        issues=issues,
        animation=animation,
        original_hashes_unchanged=preserved,
        contact_sheet=str(write_contact_sheet(outputs, output)),
        passed=not issues and preserved and bool(animation and animation["preserved"]),
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    args = parser.parse_args()
    report = asyncio.run(accept(args.output, args.browser_executable))
    print(json.dumps({key: report[key] for key in ("count", "issues", "passed")}))
    raise SystemExit(0 if report["passed"] else 1)
