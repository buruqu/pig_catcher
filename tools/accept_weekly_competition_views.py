"""Render and inspect 2.0 weekly competition views with Chromium."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.rendering import (  # noqa: E402
    PigCatcherRenderer,
    WeeklyCompetitionAwardViewModel,
    WeeklyCompetitionRowViewModel,
    WeeklyCompetitionViewModel,
)
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--season", type=int, choices=(1, 2), default=1)
    return parser.parse_args()


def ranking_rows() -> tuple[WeeklyCompetitionRowViewModel, ...]:
    names = (
        "千早の花火（冲刺322）",
        "纯良小白",
        "软糯丰川祥",
        "古咕固？古咕固！",
        "本期名字特别特别长也要完整显示的参赛群友",
        "栖夜莉丝嘶呀",
        "撅撅",
        "ヽ_(ツ)_/￣",
        "7号机",
        "9号机",
    )
    scores = (94794, 79128, 68520, 62110, 59007, 51688, 48004, 42420, 39876, 36500)
    return tuple(
        WeeklyCompetitionRowViewModel(
            rank=index,
            display_name=name,
            score_text=f"{score:,} 价值",
            catch_count=41 - index,
            highest_single_text=f"{max(1007, score // 5):,} 价值",
            last_update_at=f"08-{26 - index // 4:02d} {20 - index:02d}:18",
        )
        for index, (name, score) in enumerate(zip(names, scores, strict=True), start=1)
    )


def leaderboard(*, status: str, empty: bool = False, season: int = 1) -> WeeklyCompetitionViewModel:
    entries = () if empty else ranking_rows()
    if season == 2:
        entries = tuple(
            replace(
                item,
                score_text=f"{70 - index * 4} 份",
                catch_count=30 - index,
                last_update_at=f"09-10 1{index // 2}:18",
            )
            for index, item in enumerate(entries)
        )
    return WeeklyCompetitionViewModel(
        season_number=season,
        name="寿司拼盘大王" if season == 2 else "抓猪冲刺！！！",
        status_label=status,
        group_name="官方群-CEAB3520",
        metric_label="本期亲手做出的猪寿司拼盘" if season == 2 else "本周抓猪累计官方价值",
        period_text="2026-09-08 00:02 — 2026-09-15 00:00" if season == 2 else "2026-09-01 00:00 — 2026-09-08 00:00",
        countdown_text="已完成结算" if status == "已结算" else "距离结算 4 天 12 小时",
        page=1,
        page_count=1,
        total_count=len(entries),
        player_position_text=("我的名次：第 3 名 · 62 份" if season == 2 else "我的名次：第 3 名 · 68,520 价值")
        if entries
        else "我的名次：尚未上榜",
        entries=entries,
        cooking_metric=season == 2,
    )


def award(season: int = 1) -> WeeklyCompetitionAwardViewModel:
    if season == 2:
        from pig_catcher.domain.weekly_competitions import WEEKLY_COMPETITIONS_BY_SEASON
        from pig_catcher.services.weekly_competitions import weekly_reward_label

        definition = WEEKLY_COMPETITIONS_BY_SEASON[season]
        return WeeklyCompetitionAwardViewModel(
            season_number=season,
            competition_name=definition.name,
            display_name="寿司主厨·团子",
            final_rank=1,
            score_text="70 份",
            reward_lines=tuple(weekly_reward_label(item) for item in definition.rewards_for_rank(1)),
        )
    return WeeklyCompetitionAwardViewModel(
        season_number=1,
        competition_name="抓猪冲刺！！！",
        display_name="千早の花火（冲刺322）",
        final_rank=1,
        score_text="94,794 价值",
        reward_lines=(
            "10,000 猪币",
            "成就抓猪券 ×5",
            "成就礼花券 ×2",
            "称号：抓猪冲刺者",
            "边框：抓猪冲刺！！！·赛道边框",
            "徽章：抓猪冲刺！！！·1牌",
        ),
    )


def write_thumbnail(source_path: Path, destination: Path) -> None:
    with Image.open(source_path) as source:
        source.seek(0)
        image = source.convert("RGB")
    height = max(1, round(image.height * 320 / image.width))
    image.resize((320, height), Image.Resampling.LANCZOS).save(destination, quality=92)


def write_contact_sheet(outputs: list[tuple[str, Path]], destination: Path) -> None:
    sheet = Image.new("RGB", (1240, 1180), "#fff7fa")
    draw = ImageDraw.Draw(sheet)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    title_font = ImageFont.truetype(str(font_path), 30) if font_path.is_file() else ImageFont.load_default()
    label_font = ImageFont.truetype(str(font_path), 17) if font_path.is_file() else ImageFont.load_default()
    draw.text((24, 22), "PiG Dream! 周冲榜 Chromium 视觉验收", fill="#513f47", font=title_font)
    for index, (label, path) in enumerate(outputs):
        with Image.open(path) as source:
            preview = ImageOps.contain(source.convert("RGB"), (570, 470), method=Image.Resampling.LANCZOS)
        x = 24 + (index % 2) * 606
        y = 78 + (index // 2) * 540
        sheet.paste(preview, (x, y))
        draw.text((x, y + 480), label, fill="#59434e", font=label_font)
    sheet.save(destination, quality=92)


async def run(args: argparse.Namespace) -> dict[str, object]:
    output_root = args.output.resolve()
    if output_root.exists():
        raise FileExistsError(f"Output already exists: {output_root}")
    output_root.mkdir(parents=True)
    outputs: list[tuple[str, Path]] = []

    async with async_playwright() as playwright:
        launch_options: dict[str, object] = {"headless": True}
        if args.browser_executable is not None:
            launch_options["executable_path"] = str(args.browser_executable.resolve(strict=True))
        browser = await playwright.chromium.launch(**launch_options)
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        renderer = PigCatcherRenderer(capability, render_options())

        jobs = (
            (
                "weekly-active-full",
                renderer.render_weekly_competition(leaderboard(status="进行中", season=args.season)),
            ),
            (
                "weekly-empty",
                renderer.render_weekly_competition(leaderboard(status="进行中", empty=True, season=args.season)),
            ),
            ("weekly-settled", renderer.render_weekly_competition(leaderboard(status="已结算", season=args.season))),
            ("weekly-award-first", renderer.render_weekly_competition_award(award(args.season))),
        )
        for label, job in jobs:
            capability.label = label
            rendered = await job
            destination = output_root / f"{label}.png"
            write_image(destination, rendered)
            outputs.append((label, destination))

        await capability.close()
        await browser.close()

    failures = [
        item for item in capability.diagnostics if item["clippedText"] or item["outside"] or item["brokenImages"]
    ]
    if failures:
        raise RuntimeError("Chromium DOM diagnostics failed:\n" + json.dumps(failures, ensure_ascii=False, indent=2))

    thumbnail_root = output_root / "thumbnails-320"
    thumbnail_root.mkdir()
    for label, path in outputs:
        write_thumbnail(path, thumbnail_root / f"{label}.jpg")
    contact_sheet = output_root / "contact-sheet.jpg"
    write_contact_sheet(outputs, contact_sheet)
    report = {
        "outputs": [str(path) for _, path in outputs],
        "thumbnails_320": [str(thumbnail_root / f"{label}.jpg") for label, _ in outputs],
        "contact_sheet": str(contact_sheet),
        "diagnostics": capability.diagnostics,
        "status": "passed",
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
