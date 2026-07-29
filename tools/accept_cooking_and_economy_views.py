"""Render and inspect representative cooking and economy views with Chromium."""

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
    FoodCardViewModel,
    FoodCatalogItemViewModel,
    FoodCatalogViewModel,
    FoodInventoryItemViewModel,
    FoodInventoryViewModel,
    LedgerEntryViewModel,
    LedgerViewModel,
    PigCatcherRenderer,
    ProfileViewModel,
    RenderedImage,
    StoreProductViewModel,
    StoreViewModel,
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


def load_food_rows(database_path: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                template_id, display_name, rarity, description, image_relpath,
                image_fit, media_format, is_animated, frame_count, scope_type
            FROM food_templates
            WHERE enabled = 1
            ORDER BY rarity, scope_type, template_id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def food_card(
    row: Mapping[str, object],
    *,
    display_name: str | None = None,
    cooking: bool,
) -> FoodCardViewModel:
    return FoodCardViewModel(
        mode_label="做菜成功" if cooking else "美食详情",
        display_name=display_name or str(row["display_name"]),
        owner_display_name="第四轮验收成员",
        rarity=int(row["rarity"]),
        rarity_name=("漂亮定制菜" if int(row["rarity"]) == 6 else "美食"),
        short_code="F00D2026",
        description=str(row["description"]),
        portion_weight=18.66,
        fat_label="均衡",
        official_value=388,
        acquired_at="2026-07-28 12:00",
        source_selector="命令测试猪#A19F2C3D",
        effect_summary="暂无额外效果",
        image_fit=str(row["image_fit"]),
        media_visible=True,
        is_animated=bool(row["is_animated"]),
        media_format=str(row["media_format"]),
        coin_reward=45 if cooking else None,
        experience_reward=40 if cooking else None,
        coin_balance=1280 if cooking else None,
        total_experience=920 if cooking else None,
        player_level=5 if cooking else None,
        level_title="抓猪老手" if cooking else "",
        next_level_experience=1250 if cooking else None,
        level_progress_percent=26.67 if cooking else 0.0,
        cookware_level=3 if cooking else None,
        item_name="主厨香料" if cooking else "",
        catalog_new_count=1 if cooking else 0,
        bonus_selector="命令测试菜#B19F2C3D" if cooking else "",
        probability_summary=(
            "1★ 14.0% · 2★ 58.0% · 3★ 24.0% · 4★ 4.0%"
            if cooking
            else ""
        ),
    )


def inventory_view(rows: Sequence[Mapping[str, object]]) -> FoodInventoryViewModel:
    return FoodInventoryViewModel(
        display_name="很长但必须完整显示的第四轮美食收藏验收成员",
        page=1,
        page_count=2,
        total_count=len(rows),
        rarity=None,
        sort="价值",
        items=tuple(
            FoodInventoryItemViewModel(
                key=str(row["template_id"]),
                display_name=str(row["display_name"]),
                short_code=f"{index + 1:08X}",
                rarity=int(row["rarity"]),
                portion_weight=8.0 + index * 1.13,
                fat_label=("偏瘦", "均衡", "偏肥")[index % 3],
                official_value=25 + index * 77,
                media_visible=True,
                is_animated=bool(row["is_animated"]),
                image_fit=str(row["image_fit"]),
            )
            for index, row in enumerate(rows[:12])
        ),
    )


def catalog_view(rows: Sequence[Mapping[str, object]]) -> FoodCatalogViewModel:
    return FoodCatalogViewModel(
        display_name="第四轮图鉴验收成员",
        total_count=len(rows),
        rarity=None,
        undiscovered_only=False,
        collected_count=max(0, len(rows) - 2),
        visible_catalog_total=len(rows),
        items=tuple(
            FoodCatalogItemViewModel(
                key=str(row["template_id"]),
                display_name=str(row["display_name"]),
                rarity=int(row["rarity"]),
                discovered=index < 10,
                acquired_count=index + 1 if index < 10 else 0,
                best_portion_weight=12.5 + index if index < 10 else None,
                media_visible=index < 10,
                is_animated=bool(row["is_animated"]),
                image_fit=str(row["image_fit"]),
            )
            for index, row in enumerate(rows[:12])
        ),
    )


def store_view() -> StoreViewModel:
    definitions = (
        ("幸运猪哨", "抓猪道具", 180, "下一次抓猪更容易遇到高星猪猪", "/购买 幸运猪哨"),
        ("巨物玉米", "抓猪道具", 140, "下一次抓猪更容易遇到大体型猪猪", "/购买 巨物玉米"),
        ("增膘豆饼", "抓猪道具", 100, "下一次抓猪的猪猪更肥、更重", "/购买 增膘豆饼"),
        ("精瘦青饲料", "抓猪道具", 100, "下一次抓猪的猪猪更精瘦、体型略大", "/购买 精瘦青饲料"),
        ("主厨香料", "做菜道具", 180, "下一次做菜更容易提升品质", "/购买 主厨香料"),
        ("精准刀工券", "做菜道具", 120, "下一次做菜优先偏瘦食谱", "/购买 精准刀工券"),
        ("慢炖调料包", "做菜道具", 120, "下一次做菜优先偏肥食谱", "/购买 慢炖调料包"),
        ("大份餐盒", "做菜道具", 240, "符合条件时有机会额外出餐", "/购买 大份餐盒"),
        ("猪饲料升级", "永久升级", 260, "Lv.2 → Lv.3，永久改善高星抓猪权重", "/升级 猪饲料"),
        ("厨具升级", "永久升级", 520, "Lv.3 → Lv.4，永久改善做菜品质", "/升级 厨具"),
    )
    return StoreViewModel(
        display_name="第四轮商城验收成员",
        coin_balance=16888,
        page=1,
        page_count=1,
        total_count=10,
        category="全部",
        feed_level=2,
        cookware_level=3,
        products=tuple(
            StoreProductViewModel(
                display_name=name,
                category=category,
                unit_price=price,
                effect_summary=effect,
                current_level=0,
                target_level=0,
                command=command,
            )
            for name, category, price, effect, command in definitions
        ),
    )


def receipt_view(kind: str) -> EconomyReceiptViewModel:
    if kind == "purchase":
        return EconomyReceiptViewModel(
            eyebrow="猪猪商城 · 原子扣款与发货",
            title="购买成功",
            badge_label="剩余猪币",
            badge_value="16708",
            summary="幸运猪哨 ×1",
            rows=(
                EconomyReceiptRowViewModel("单价", "180 猪币"),
                EconomyReceiptRowViewModel("本次支付", "180 猪币"),
                EconomyReceiptRowViewModel("当前库存", "3"),
            ),
            note="同一消息重复投递不会再次扣款或发货。",
        )
    if kind == "eat":
        return EconomyReceiptViewModel(
            eyebrow="美食品鉴 · 成功后消耗一份",
            title="开饭啦",
            badge_label="当前猪币",
            badge_value="16708",
            summary="★★★★★ 黑猪麻汤圆#E19F2C3D",
            rows=(
                EconomyReceiptRowViewModel("品鉴经验", "+45"),
                EconomyReceiptRowViewModel("累计经验", "1780"),
            ),
            note="暂无额外效果，本次仅获得品鉴经验。",
        )
    return EconomyReceiptViewModel(
        eyebrow="官方售卖 · 美食已离开背包",
        title="售卖成功",
        badge_label="当前猪币",
        badge_value="17808",
        summary="★★★★★ 黑猪麻汤圆#E19F2C3D",
        rows=(
            EconomyReceiptRowViewModel("官方价值", "1100 猪币"),
            EconomyReceiptRowViewModel("到账", "+1100 猪币"),
            EconomyReceiptRowViewModel("图鉴", "已解锁记录保留"),
        ),
        note="售卖不可撤销；同一消息重复投递不会重复到账。",
    )


def ledger_view() -> LedgerViewModel:
    return LedgerViewModel(
        display_name="第四轮账本验收成员",
        page=1,
        page_count=2,
        total_count=18,
        coin_balance=17808,
        ledger_total=17808,
        items=tuple(
            LedgerEntryViewModel(
                amount_text=f"{'+' if index % 3 else '-'}{45 + index * 17}",
                positive=index % 3 != 0,
                balance_after=17808 - index * 90,
                reason_text=(
                    "做菜奖励"
                    if index % 3 == 1
                    else "抓猪奖励"
                    if index % 3 == 2
                    else "购买大份餐盒×1"
                ),
                created_at=f"2026-07-28 {18 - index:02d}:30",
            )
            for index in range(10)
        ),
    )


def profile_view() -> ProfileViewModel:
    return ProfileViewModel(
        display_name="第四轮档案完整字段验收成员",
        level=4,
        title="抓猪高手",
        total_experience=2200,
        next_threshold=6000,
        progress_percent=10.5,
        coin_balance=17808,
        total_catches=38,
        active_pigs=21,
        catalog_count=25,
        visible_catalog_total=81,
        held_records=4,
        daily_count=20,
        daily_limit=20,
        cooldown_remaining_seconds=0,
        feed_level=2,
        armed_item_name="幸运猪哨",
        armed_item_quantity=3,
        cookware_level=3,
        total_cooks=16,
        active_foods=9,
        food_catalog_count=11,
        visible_food_catalog_total=13,
        armed_cooking_item_name="大份餐盒",
        armed_cooking_item_quantity=2,
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
    rows = math.ceil(len(outputs) / columns)
    sheet = Image.new(
        "RGB",
        (columns * cell_width, 72 + rows * cell_height),
        "#FFF7FA",
    )
    draw = ImageDraw.Draw(sheet)
    draw.text(
        (22, 18),
        "Pig Catcher cooking and economy Chromium acceptance",
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
    rows = load_food_rows(database_path)
    if len(rows) < 12:
        raise RuntimeError("Formal data must contain at least 12 enabled food templates.")
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
            *,
            extension: str = "png",
        ) -> None:
            capability.label = label
            rendered = await render
            destination = rendered_root / f"{label}.{extension}"
            write_image(destination, rendered)
            outputs.append((label, destination))
            metadata.append(
                {
                    "label": label,
                    "path": str(destination),
                    "width": rendered.width,
                    "height": rendered.height,
                    "bytes": rendered.byte_length,
                    "frames": rendered.frame_count,
                    "duration_ms": rendered.total_duration_ms,
                }
            )

        first = rows[0]
        await record(
            "food-detail",
            renderer.render_static_food_card(
                food_card(first, cooking=False),
                data_dir / str(first["image_relpath"]),
            ),
        )
        await record(
            "cooking-long-name",
            renderer.render_static_food_card(
                food_card(
                    rows[8],
                    display_name="超长名称测试之群聊里也必须完整可读的漂亮猪猪料理",
                    cooking=True,
                ),
                data_dir / str(rows[8]["image_relpath"]),
            ),
        )
        inventory = inventory_view(rows)
        await record(
            "food-inventory-full",
            renderer.render_food_inventory(
                inventory,
                {
                    str(row["template_id"]): data_dir / str(row["image_relpath"])
                    for row in rows[:12]
                },
            ),
        )
        await record(
            "food-inventory-empty",
            renderer.render_food_inventory(
                FoodInventoryViewModel(
                    display_name="空背包验收成员",
                    page=1,
                    page_count=1,
                    total_count=0,
                    rarity=6,
                    sort="获得时间",
                    items=(),
                ),
                {},
            ),
        )
        catalog = catalog_view(rows)
        await record(
            "food-catalog-privacy",
            renderer.render_food_catalog(
                catalog,
                {
                    str(row["template_id"]): data_dir / str(row["image_relpath"])
                    for row in rows[:10]
                },
            ),
        )
        await record("store-full", renderer.render_store(store_view()))
        await record(
            "receipt-purchase",
            renderer.render_economy_receipt(receipt_view("purchase")),
        )
        await record(
            "receipt-eat",
            renderer.render_economy_receipt(receipt_view("eat")),
        )
        await record(
            "receipt-sale",
            renderer.render_economy_receipt(receipt_view("sale")),
        )
        await record("ledger-full", renderer.render_ledger(ledger_view()))
        await record("profile-round-four", renderer.render_profile(profile_view()))
        await capability.close()
        await browser.close()

    failures: list[dict[str, object]] = []
    for diagnostic in capability.diagnostics:
        if (
            diagnostic["clippedText"]
            or diagnostic["outside"]
            or diagnostic["brokenImages"]
        ):
            failures.append(diagnostic)
    if failures:
        raise RuntimeError(
            "Chromium DOM diagnostics failed:\n"
            + json.dumps(failures, ensure_ascii=False, indent=2)
        )
    thumbnails = write_thumbnails(outputs, output_root)
    contact_sheet = write_contact_sheet(outputs, output_root)
    report = {
        "database": str(database_path),
        "food_templates": len(rows),
        "outputs": metadata,
        "diagnostics": capability.diagnostics,
        "thumbnails": thumbnails,
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
