"""Render and inspect representative cooking and economy views with Chromium."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.config.model import EconomySection  # noqa: E402
from pig_catcher.domain.economy import build_store_products  # noqa: E402
from pig_catcher.domain.rules import BASE_CATCH_WEIGHTS  # noqa: E402
from pig_catcher.rendering import (  # noqa: E402
    BatchCookingItemViewModel,
    BatchCookingViewModel,
    EconomyReceiptRowViewModel,
    EconomyReceiptViewModel,
    FoodCardViewModel,
    FoodCatalogItemViewModel,
    FoodCatalogViewModel,
    FoodInventoryItemViewModel,
    FoodInventoryViewModel,
    GroupEventRowViewModel,
    GroupEventViewModel,
    LedgerEntryViewModel,
    LedgerViewModel,
    PigCatcherRenderer,
    ProfileViewModel,
    RenderedImage,
    StoreViewModel,
)
from pig_catcher.rendering import store_view as build_store_view  # noqa: E402
from pig_catcher.services import StorePage  # noqa: E402
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
        item_remaining_uses=2 if cooking else 0,
        catalog_new_count=1 if cooking else 0,
        bonus_selector="命令测试菜#B19F2C3D" if cooking else "",
        probability_line=("1★ 14.0% · 2★ 58.0% · 3★ 24.0% · 4★ 4.0%" if cooking else ""),
        probability_sources=("等级 Lv.5、厨具 Lv.3、道具·主厨香料" if cooking else ""),
    )


def long_effect_cooking_card(row: Mapping[str, object]) -> FoodCardViewModel:
    """Reproduce the production sugar-ribs result that previously hid probability."""

    return replace(
        food_card(row, cooking=True),
        display_name="糖醋排骨",
        owner_display_name="千早の花火",
        short_code="9QHWWSWI",
        portion_weight=65.37,
        fat_label="偏瘦",
        official_value=23564,
        source_selector="ob一串猪#SDPA67DF",
        effect_summary=(
            "获得 1 次 /重置额度 机会；每次重置会让本群已登记玩家各获得 1007 猪币和 "
            "10 次专属抓猪额度，并在次日同一时段刷新前令 5 星与 6 星相对权重分别 "
            "×1.007 和 ×1.007；每次专属抓猪有 10% 概率令本次 5 星/6 星相对权重"
            "爆发为 ×10.04/×10.04。"
        ),
        coin_reward=1500,
        experience_reward=800,
        coin_balance=5180,
        total_experience=15685,
        player_level=18,
        level_title="抓猪大神",
        next_level_experience=16200,
        level_progress_percent=80.0,
        cookware_level=5,
        item_name="超级主厨香料",
        item_remaining_uses=6,
        bonus_selector="",
        probability_line="5★ 64.0% · 6★ 36.0%",
        probability_sources=(
            "等级 Lv.18、厨具 Lv.5、道具·超级主厨香料、美食加成 ×2"
        ),
        effect_summaries=(
            "下一次用 6 星猪做菜时，6 星菜最终概率额外 +15 个百分点（最高 50%）。",
            "猪饺叠加 1 层：本次 6 星菜概率额外 +1 个百分点。",
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
    """Build the acceptance card from production domain rules, not fixtures."""

    economy = EconomySection()
    products = build_store_products(
        feed_level=2,
        cookware_level=3,
        feed_prices=economy.feed_upgrade_prices,
        cookware_prices=economy.cookware_upgrade_prices,
    )
    return build_store_view(
        StorePage(
            display_name="Ruleset 13 商城验收成员",
            coin_balance=16888,
            page=1,
            page_count=1,
            total_count=len(products),
            page_size=len(products),
            category="全部",
            feed_level=2,
            cookware_level=3,
            products=products,
            catch_base_weights=BASE_CATCH_WEIGHTS,
        )
    )


def receipt_view(kind: str) -> EconomyReceiptViewModel:
    if kind == "purchase":
        return EconomyReceiptViewModel(
            eyebrow="猪猪商城 · 原子扣款与发货",
            title="购买成功",
            badge_label="剩余猪币",
            badge_value="16408",
            summary="幸运猪哨 ×1",
            rows=(
                EconomyReceiptRowViewModel("单价", "480 猪币"),
                EconomyReceiptRowViewModel("本次支付", "480 猪币"),
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
        daily_limit=5,
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
        level_catch_base_high_percent=13.0,
        level_catch_adjusted_high_percent=13.20,
        level_cooking_bonus_percent=0.75,
        level_bonus_cap_level=21,
    )


def group_event_view(kind: str) -> GroupEventViewModel:
    actor = "千早の花火（很有排面的六星盛宴发动群友）"
    common = {
        "actor_name": actor,
        "group_name": "官方群-CEAB3520 · 抓猪六星盛宴视觉验收群",
        "event_time": "2026-08-12 15:20",
    }
    if kind == "sugar":
        return GroupEventViewModel(
            tone="sugar",
            eyebrow="六星盛宴 · 全群事件资格已取得",
            title="糖醋排骨登场",
            subtitle="酸甜一响，全群强化蓄势待发",
            hero_label="发动资格",
            hero_value="1 次 /重置额度",
            rows=(
                GroupEventRowViewModel(
                    "全群猪币",
                    "每人 +1,007",
                    "实际执行 /重置额度 后发放",
                ),
                GroupEventRowViewModel(
                    "专属抓猪",
                    "每人 10 次",
                    "不占用正常抓猪额度",
                ),
                GroupEventRowViewModel(
                    "高星强化",
                    "5★ / 6★ ×1.007",
                    "每次专属抓猪另有 10% 隐藏爆发",
                ),
            ),
            note=(
                "本次食用只取得发动资格，尚未重置任何额度。请由食用者在本群发送 "
                "/重置额度，届时将再次发布正式发动通告。"
            ),
            footer="全群事件将在真正发动时原子结算",
            settlement_committed=False,
            media_visible=True,
            **common,
        )
    if kind == "cloud":
        return GroupEventViewModel(
            tone="cloud",
            eyebrow="六星盛宴 · 神龙临世",
            title="七星云海，福泽全群",
            subtitle="神龙化猪七星云海锅已经开席",
            hero_label="全群高星权重",
            hero_value="5★ / 6★ ×8",
            rows=(
                GroupEventRowViewModel("食用者奖励", "+18,888 猪币", actor),
                GroupEventRowViewModel(
                    "其余群友奖励",
                    "每人 +1,680 猪币",
                    "本次共惠及 91 名已登记玩家",
                ),
                GroupEventRowViewModel(
                    "下一次抓猪",
                    "纯基础独占 ×8",
                    "每名玩家各生效 1 次，不与其他道具或菜品叠加",
                ),
            ),
            note=(
                "全群效果从当前抓猪时段开始，到次日同一时段刷新时清除；"
                "每名玩家的下一次兼容抓猪独立消费自己的加成。"
            ),
            footer="神龙赐福已在本群完成结算",
            media_visible=True,
            **common,
        )
    return GroupEventViewModel(
        tone="reset",
        eyebrow="糖醋排骨 · 全群强化正式发动",
        title="全群额度重置完成",
        subtitle="酸甜号令落下，新的十连已经开启",
        hero_label="全群专属抓猪",
        hero_value="每人 10 次",
        rows=(
            GroupEventRowViewModel(
                "全群猪币",
                "每人 +1,007",
                "共惠及 91 名已登记玩家",
            ),
            GroupEventRowViewModel(
                "本时段重置",
                "归零 322 次",
                "涉及 91 名玩家；历史资产与统计全部保留",
            ),
            GroupEventRowViewModel(
                "高星强化",
                "5★ / 6★ ×1.007",
                "每次专属抓猪有 10% 概率爆发为 ×10.04",
            ),
        ),
        note=(
            "强化持续至 2026-08-13 12:00；专属抓猪不扣正常额度，"
            "可与普通道具和非六星菜按既定规则叠加。"
        ),
        footer="糖醋排骨全群强化已经正式生效",
        **common,
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
        sugar_result = next(
            (
                row
                for row in rows
                if str(row["display_name"]) == "糖醋排骨"
            ),
            rows[-1],
        )
        await record(
            "cooking-long-effect-probability",
            renderer.render_static_food_card(
                long_effect_cooking_card(sugar_result),
                data_dir / str(sugar_result["image_relpath"]),
            ),
        )
        await record(
            "batch-cooking-ordered-effects",
            renderer.render_batch_cook(
                BatchCookingViewModel(
                    display_name="连续道具与普通菜品效果验收成员",
                    pig_count=3,
                    food_count=3,
                    coin_reward=180,
                    experience_reward=210,
                    catalog_new_count=2,
                    rarity=None,
                    items=tuple(
                        BatchCookingItemViewModel(
                            key=f"batch-{index}",
                            display_name=f"批量料理示例 {index}",
                            short_code=f"BATCH{index:03d}",
                            rarity=index,
                            portion_weight=12.5 + index,
                            fat_label="均衡",
                            official_value=120 * index,
                            media_visible=False,
                            is_animated=False,
                            image_fit="contain",
                            source_pig_name=f"原料猪 {index}",
                        )
                        for index in range(1, 4)
                    ),
                    item_use_summaries=("主厨香料 ×3（队列剩余 2 次）",),
                    effect_use_summaries=(
                        "猪籽军舰：下一次做菜五星概率提升（本次结算后剩余 2/5 次）"
                        "（本批共触发 3 次）",
                    ),
                ),
                {},
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
        sugar_media = next(
            (
                row
                for row in rows
                if str(row["display_name"]) == "糖醋排骨"
            ),
            rows[-1],
        )
        cloud_media = next(
            (
                row
                for row in rows
                if str(row["display_name"]) == "神龙化猪七星云海锅"
            ),
            rows[-1],
        )
        await record(
            "group-event-sugar-opportunity",
            renderer.render_group_event(
                group_event_view("sugar"),
                data_dir / str(sugar_media["image_relpath"]),
            ),
        )
        await record(
            "group-event-cloud-feast",
            renderer.render_group_event(
                group_event_view("cloud"),
                data_dir / str(cloud_media["image_relpath"]),
            ),
        )
        await record(
            "group-event-sugar-reset",
            renderer.render_group_event(group_event_view("reset")),
        )
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
