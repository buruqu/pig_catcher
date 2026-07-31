"""Render and inspect representative social and ranking views with Chromium."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
from collections.abc import Awaitable, Mapping, Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.rendering import (  # noqa: E402
    EconomyReceiptRowViewModel,
    EconomyReceiptViewModel,
    GiantSightingViewModel,
    PigCardViewModel,
    PigCatcherRenderer,
    ProfileViewModel,
    RankingItemViewModel,
    RankingViewModel,
    RecordItemViewModel,
    RecordsViewModel,
    RenderedImage,
    TradeListItemViewModel,
    TradeListViewModel,
)
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
    write_thumbnails,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--browser-executable", type=Path)
    return parser.parse_args()


def load_special_pigs(database_path: Path) -> dict[str, dict[str, object]]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                template_id, display_name, rarity, description, image_relpath,
                image_fit, media_format, is_animated, length_min, length_max,
                weight_min, weight_max, stature_profile
            FROM pig_templates
            WHERE enabled = 1
              AND template_id IN ('pig-r2-tiny', 'pig-r2-elephant')
            ORDER BY template_id
            """
        ).fetchall()
    finally:
        connection.close()
    result = {str(row["template_id"]): dict(row) for row in rows}
    if set(result) != {"pig-r2-tiny", "pig-r2-elephant"}:
        raise RuntimeError("正式目录缺少特小猪或大象模板。")
    return result


def pig_card(
    row: Mapping[str, object],
    *,
    short_code: str,
    size_value: float,
    weight_value: float,
    body_label: str,
    body_description: str,
    giant_score: float = 0.0,
    catalog_new: bool = False,
    global_size_record: bool = False,
    global_weight_record: bool = False,
    giant_sighting: bool = False,
) -> PigCardViewModel:
    return PigCardViewModel(
        mode_label="抓猪成功",
        display_name=str(row["display_name"]),
        owner_display_name="第五轮视觉验收成员",
        rarity=int(row["rarity"]),
        rarity_name="绿色品质",
        short_code=short_code,
        description=str(row["description"]),
        size_value=size_value,
        size_percentile=0.99 if giant_sighting else 0.03,
        weight_value=weight_value,
        weight_percentile=0.99 if giant_sighting else 0.03,
        fat_ratio=52.6,
        fat_label="均衡",
        official_value=1888 if giant_sighting else 88,
        acquired_at="2026-07-28 18:30",
        image_fit=str(row["image_fit"]),
        media_visible=True,
        is_animated=bool(row["is_animated"]),
        media_format=str(row["media_format"]),
        coin_reward=128,
        experience_reward=96,
        coin_balance=9288,
        total_experience=3680,
        player_level=9,
        level_title="抓猪高手",
        next_level_experience=4050,
        level_progress_percent=56.47,
        daily_count=8,
        daily_limit=5,
        catalog_new=catalog_new,
        body_label=body_label,
        body_description=body_description,
        giant_score=giant_score,
        global_size_record=global_size_record,
        global_weight_record=global_weight_record,
        giant_sighting=giant_sighting,
    )


def records_view() -> RecordsViewModel:
    return RecordsViewModel(
        group_name="第五轮巨物观察群",
        page=1,
        page_count=1,
        total_count=2,
        items=(
            RecordItemViewModel(
                record_label="本群品种最长",
                record_value=15.6,
                unit="cm",
                display_name="特小猪",
                rarity=2,
                short_code="MINI2026",
                holder_display_name="袖珍收藏家",
                achieved_at="2026-07-28 18:20",
            ),
        ),
        global_items=(
            RecordItemViewModel(
                record_label="全群绝对体型",
                record_value=259.8,
                unit="cm",
                display_name="大象",
                rarity=2,
                short_code="GIANT526",
                holder_display_name="巨物观察员",
                achieved_at="2026-07-28 18:30",
            ),
            RecordItemViewModel(
                record_label="全群绝对重量",
                record_value=1798.4,
                unit="kg",
                display_name="大象",
                rarity=2,
                short_code="GIANT526",
                holder_display_name="巨物观察员",
                achieved_at="2026-07-28 18:30",
            ),
        ),
        giant_sightings=(
            GiantSightingViewModel(
                display_name="大象",
                rarity=2,
                short_code="GIANT526",
                holder_display_name="巨物观察员",
                size_value=259.8,
                weight_value=1798.4,
                giant_score=350.3,
                qualification_label="双项巨物",
                achieved_at="2026-07-28 18:30",
            ),
        ),
    )


def social_receipt(kind: str) -> EconomyReceiptViewModel:
    if kind == "gift":
        return EconomyReceiptViewModel(
            eyebrow="同群赠送 · 原子转移",
            title="赠送成功",
            badge_label="接收方",
            badge_value="粉色收藏家",
            summary="★★ 特小猪#MINI2026",
            rows=(
                EconomyReceiptRowViewModel("赠送方", "第五轮视觉验收成员"),
                EconomyReceiptRowViewModel("接收方", "粉色收藏家"),
                EconomyReceiptRowViewModel("费用", "0 猪币"),
            ),
            note="赠送立即完成；同一消息重复投递不会再次转移。",
        )
    return EconomyReceiptViewModel(
        eyebrow="双方确认交易 · 资产已解锁",
        title="交易完成",
        badge_label="成交价",
        badge_value="2,680 猪币",
        summary="★★ 大象#GIANT526",
        rows=(
            EconomyReceiptRowViewModel("发起方", "巨物观察员"),
            EconomyReceiptRowViewModel("接收方", "粉色收藏家"),
            EconomyReceiptRowViewModel("交易号", "T5A9C2026"),
        ),
        note="猪币零和转移，物品和双方余额在同一个事务中提交。",
    )


def trade_list_view() -> TradeListViewModel:
    statuses = ("等待确认", "已完成", "已拒绝", "已取消", "已过期")
    return TradeListViewModel(
        display_name="名称很长但仍需完整可读的第五轮交易成员",
        page=1,
        page_count=1,
        total_count=len(statuses),
        status_label="全部",
        items=tuple(
            TradeListItemViewModel(
                trade_id=f"T5A9C20{index + 20}",
                status_label=status,
                asset_name=("大象" if index % 2 == 0 else "特小猪"),
                asset_code=("GIANT526" if index % 2 == 0 else "MINI2026"),
                rarity=2,
                price=2680 + index * 320,
                sender_name="巨物观察员",
                recipient_name="粉色收藏家",
                expires_at=f"2026-07-28 18:{35 + index:02d}",
            )
            for index, status in enumerate(statuses)
        ),
    )


def profile_view() -> ProfileViewModel:
    return ProfileViewModel(
        display_name="第五轮展示位验收成员",
        level=6,
        title="粉色猪猪收藏家",
        total_experience=5680,
        next_threshold=9000,
        progress_percent=63.1,
        coin_balance=9288,
        total_catches=86,
        active_pigs=38,
        catalog_count=44,
        visible_catalog_total=83,
        held_records=7,
        daily_count=8,
        daily_limit=5,
        cooldown_remaining_seconds=12,
        feed_level=3,
        armed_item_name="幸运猪哨",
        armed_item_quantity=2,
        cookware_level=4,
        total_cooks=31,
        active_foods=22,
        food_catalog_count=12,
        visible_food_catalog_total=15,
        armed_cooking_item_name="主厨香料",
        armed_cooking_item_quantity=3,
        showcase_pig="大象#GIANT526 · 双项巨物",
        showcase_food="巧克力猪猪蛋糕#FOOD0526",
        level_catch_base_high_percent=13.0,
        level_catch_adjusted_high_percent=13.34,
        level_cooking_bonus_percent=1.25,
    )


def ranking_view() -> RankingViewModel:
    return RankingViewModel(
        group_name="第五轮排行榜验收群",
        ranking_type="综合榜",
        page=1,
        page_count=1,
        total_count=10,
        items=tuple(
            RankingItemViewModel(
                key=("pig-r2-elephant" if index == 0 else "pig-r2-tiny"),
                rank=index + 1,
                display_name=f"群友{index + 1}号"
                + ("·名称很长也不能遮挡统计" if index == 8 else ""),
                metric_text=f"综合 {96.8 - index * 4.7:.1f}",
                pig_progress=f"{44 - index}/83",
                food_progress=f"{12 - index // 2}/15",
                asset_count=60 - index * 3,
                coin_balance=9288 - index * 417,
                showcase_name=("大象" if index == 0 else "特小猪"),
                showcase_detail=(
                    "259.8 cm · 1798.4 kg · 双项巨物"
                    if index == 0
                    else "15.6 cm · 5.8 kg · 袖珍品种"
                ),
                showcase_rarity=2,
                showcase_kind="猪猪",
                media_visible=index < 2,
                is_animated=False,
                image_fit="contain",
            )
            for index in range(10)
        ),
    )


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = Path("C:/Windows/Fonts/msyh.ttc")
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def write_contact_sheet(
    outputs: Sequence[tuple[str, Path]],
    output_root: Path,
) -> Path:
    columns = 3
    cell_width = 420
    cell_height = 390
    row_count = math.ceil(len(outputs) / columns)
    sheet = Image.new(
        "RGB",
        (columns * cell_width, 72 + row_count * cell_height),
        "#FFF7FA",
    )
    draw = ImageDraw.Draw(sheet)
    draw.text(
        (22, 18),
        "Pig Catcher social and ranking Chromium acceptance",
        fill="#553846",
        font=_font(26),
    )
    for index, (label, path) in enumerate(outputs):
        with Image.open(path) as source:
            source.seek(0)
            preview = ImageOps.contain(
                source.convert("RGB"),
                (380, 320),
                method=Image.Resampling.LANCZOS,
            )
        x = (index % columns) * cell_width + 20
        y = 72 + (index // columns) * cell_height
        sheet.paste(preview, (x, y))
        draw.text((x, y + 326), label, fill="#553846", font=_font(18))
    destination = output_root / "contact-sheet.jpg"
    sheet.save(destination, quality=92)
    return destination


async def run(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.resolve()
    database_path = data_dir / args.database_filename
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    special = load_special_pigs(database_path)
    tiny = special["pig-r2-tiny"]
    elephant = special["pig-r2-elephant"]
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    rendered_root = output_root / "rendered"
    rendered_root.mkdir()

    launch_options: dict[str, object] = {"headless": True}
    if args.browser_executable:
        launch_options["executable_path"] = str(args.browser_executable.resolve())
    outputs: list[tuple[str, Path]] = []
    metadata: list[dict[str, object]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_options)
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        renderer = PigCatcherRenderer(capability, render_options())

        async def record(
            label: str,
            render: Awaitable[RenderedImage],
        ) -> None:
            capability.label = label
            rendered = await render
            destination = rendered_root / f"{label}.png"
            write_image(destination, rendered)
            outputs.append((label, destination))
            metadata.append(
                {
                    "label": label,
                    "path": str(destination),
                    "width": rendered.width,
                    "height": rendered.height,
                    "bytes": rendered.byte_length,
                }
            )

        await record(
            "catch-mini-new",
            renderer.render_static_pig_card(
                pig_card(
                    tiny,
                    short_code="MINI2026",
                    size_value=4.2,
                    weight_value=0.42,
                    body_label="袖珍品种",
                    body_description="小得像从称重台边缘漏下去，但收藏价值一点没缩水。",
                    catalog_new=True,
                ),
                data_dir / str(tiny["image_relpath"]),
            ),
        )
        await record(
            "catch-giant-global",
            renderer.render_static_pig_card(
                pig_card(
                    elephant,
                    short_code="GIANT526",
                    size_value=259.8,
                    weight_value=1798.4,
                    body_label="双项巨物",
                    body_description="长度和重量同时越过巨物线，已载入本群观察记录。",
                    giant_score=350.3,
                    catalog_new=True,
                    global_size_record=True,
                    global_weight_record=True,
                    giant_sighting=True,
                ),
                data_dir / str(elephant["image_relpath"]),
            ),
        )
        await record("records-global", renderer.render_records(records_view()))
        await record(
            "gift-receipt",
            renderer.render_economy_receipt(social_receipt("gift")),
        )
        await record(
            "trade-receipt",
            renderer.render_economy_receipt(social_receipt("trade")),
        )
        await record(
            "trade-list-all-statuses",
            renderer.render_trade_list(trade_list_view()),
        )
        await record("profile-showcases", renderer.render_profile(profile_view()))
        ranking = ranking_view()
        await record(
            "leaderboard-comprehensive",
            renderer.render_ranking(
                ranking,
                {
                    "pig-r2-elephant": data_dir / str(elephant["image_relpath"]),
                    "pig-r2-tiny": data_dir / str(tiny["image_relpath"]),
                },
            ),
        )
        await capability.close()
        await browser.close()

    failures = [
        diagnostic
        for diagnostic in capability.diagnostics
        if (
            diagnostic["clippedText"]
            or diagnostic["outside"]
            or diagnostic["brokenImages"]
        )
    ]
    if failures:
        raise RuntimeError(
            "Chromium DOM diagnostics failed:\n"
            + json.dumps(failures, ensure_ascii=False, indent=2)
        )
    thumbnails = write_thumbnails(outputs, output_root)
    contact_sheet = write_contact_sheet(outputs, output_root)
    report = {
        "database": str(database_path),
        "special_ranges": {
            "特小猪": {
                "length_cm": [tiny["length_min"], tiny["length_max"]],
                "weight_kg": [tiny["weight_min"], tiny["weight_max"]],
                "profile": tiny["stature_profile"],
            },
            "大象": {
                "length_cm": [elephant["length_min"], elephant["length_max"]],
                "weight_kg": [elephant["weight_min"], elephant["weight_max"]],
                "profile": elephant["stature_profile"],
            },
        },
        "outputs": metadata,
        "diagnostics": capability.diagnostics,
        "thumbnails_480": thumbnails,
        "contact_sheet": str(contact_sheet),
        "status": "passed",
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    report = asyncio.run(run(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
