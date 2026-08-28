"""Offline Chromium acceptance for old collection views and real-title slots.

Only synthetic view models and explicitly public local art are used. This tool
does not open a database, connect to MaiBot, or touch a user's browser session.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pig_catcher.rendering import PigCatcherRenderer  # noqa: E402
from pig_catcher.rendering.asset_icons import ASSET_ICON_KEYS, asset_icon  # noqa: E402
from pig_catcher.rendering.models import (  # noqa: E402
    CatalogItemViewModel,
    CatalogViewModel,
    FoodCatalogItemViewModel,
    FoodCatalogViewModel,
    FoodInventoryItemViewModel,
    FoodInventoryViewModel,
    InventoryItemViewModel,
    InventoryViewModel,
    RenderedImage,
)
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_social_and_ranking_views import (  # noqa: E402
    daily_giants_view,
    profile_view,
    records_view,
    write_contact_sheet,
)


def _public_library() -> tuple[dict[str, dict], dict[str, Path]]:
    library_root = ROOT / "asset_library/current"
    entries = json.loads((library_root / "assets.json").read_text(encoding="utf-8"))["entries"]
    selected_ids = {
        "pig-r1-upscale",
        "pig-r2-tiny",
        "pig-r5-earth",
        "pig-bandori-aya-idol",
        "food-r4-pig-tamagoyaki",
    }
    selected = {row["template_id"]: row for row in entries if row["template_id"] in selected_ids}
    if set(selected) != selected_ids:
        raise ValueError("The public visual fixtures are missing from the current manifest")
    for row in selected.values():
        if row["scope"] != "common":
            raise ValueError("Visual acceptance must not decode a scoped or private source")
    paths = {key: library_root / row["image"] for key, row in selected.items()}
    return selected, paths


def _fixtures(rows: dict[str, dict], sources: dict[str, Path]) -> tuple[dict, dict[str, Path]]:
    pig_ids = ("pig-r1-upscale", "pig-r2-tiny", "pig-r5-earth", "pig-bandori-aya-idol")
    items = []
    media = {}
    for index in range(12):
        source_id = pig_ids[index % len(pig_ids)]
        row = rows[source_id]
        key = f"inventory-{index}"
        media[key] = sources[source_id]
        label = ("标准体型", "迷你", "双项巨物", "标准体型")[index % 4]
        items.append(
            InventoryItemViewModel(
                key=key,
                display_name=row["display_name"],
                short_code=f"VIS{index:05d}",
                rarity=row["rarity"],
                size_value=2694.7 if index % 4 == 2 else 42.3,
                weight_value=75770.78 if index % 4 == 2 else 60.8,
                fat_label="均衡",
                official_value=2818 if index % 4 == 2 else 88,
                media_visible=index != 10,
                is_animated=index == 9,
                image_fit="contain",
                body_label=label,
                extreme_label="双顶壮硕" if index % 4 == 2 else "双顶迷你" if index % 4 == 1 else "",
                is_favorite=index % 2 == 0,
                activity_label="巡演中 · 第三站" if index == 3 else "派遣中" if index == 4 else "",
                display_tags=tuple(row.get("display_tags", ()))[:3],
            )
        )
    # Keep missing/animated/private slots distinguishable and never read a hidden source.
    for index in (9, 10, 11):
        media.pop(f"inventory-{index}")
    inventory = InventoryViewModel(
        "雨中也要认真收藏的群友",
        1,
        2,
        19,
        None,
        "价值",
        tuple(items),
        achievement_title="rain-love",
        achievement_frame="frame-nine-colors",
    )
    food = rows["food-r4-pig-tamagoyaki"]
    food_items = tuple(
        FoodInventoryItemViewModel(
            key=f"food-{index}",
            display_name=food["display_name"] if index < 3 else "很长名称的美食视觉回归测试条目",
            short_code=f"FOOD{index:04d}",
            rarity=food["rarity"],
            portion_weight=28.35,
            fat_label="均衡",
            official_value=540,
            media_visible=index != 3,
            is_animated=index == 4,
            image_fit="contain",
            is_favorite=index % 2 == 0,
        )
        for index in range(6)
    )
    media.update({f"food-{index}": sources["food-r4-pig-tamagoyaki"] for index in range(3)})
    food_inventory = FoodInventoryViewModel(
        "同名美食收藏家", 1, 1, 6, None, "价值", food_items, achievement_title="title-traveler"
    )
    catalog_items = tuple(
        CatalogItemViewModel(
            key=item.key,
            display_name=item.display_name,
            rarity=item.rarity,
            discovered=index not in (0, 5),
            acquired_count=7 + index,
            best_size=item.size_value,
            best_weight=item.weight_value,
            collection_name="Pastel＊Palettes" if index % 4 == 3 else "",
            character_name="丸山彩" if index % 4 == 3 else "",
            media_visible=item.media_visible and index not in (0, 5),
            is_animated=item.is_animated,
            image_fit="contain",
            display_tags=item.display_tags,
        )
        for index, item in enumerate(items)
    ) + (
        CatalogItemViewModel(
            "hidden-six", "不得显示的六星真名", 6, False, 0, None, None, "", "", False, False, "contain"
        ),
        CatalogItemViewModel("missing-three", "已发现 · 图片暂缺", 3, True, 1, 55, 48, "", "", True, False, "contain"),
        CatalogItemViewModel(
            "revoked-four", "历史已发现 · 授权撤回", 4, True, 1, 55, 48, "", "", False, False, "contain"
        ),
    )
    catalog = CatalogViewModel(
        "群友的完整发现记录",
        len(catalog_items),
        None,
        False,
        12,
        15,
        catalog_items,
        achievement_title="title-three-world-master",
    )
    food_catalog_items = tuple(
        FoodCatalogItemViewModel(
            key=item.key,
            display_name=item.display_name,
            rarity=item.rarity,
            discovered=index != 0,
            acquired_count=3,
            best_portion_weight=item.portion_weight,
            media_visible=item.media_visible and index != 0,
            is_animated=item.is_animated,
            image_fit="contain",
            effect_summary="视觉验收示例：效果文字保持可读，不覆盖原图。",
        )
        for index, item in enumerate(food_items)
    ) + (
        FoodCatalogItemViewModel(
            "food-hidden", "不得显示的专属菜名", 6, False, 0, None, False, False, "contain", "不得显示的专属效果"
        ),
    )
    food_catalog = FoodCatalogViewModel("美食历史图鉴", 7, None, False, 5, 7, food_catalog_items)
    profile = replace(
        profile_view(),
        display_name="记录属于每一位认真游玩的群友",
        daily_count=3,
        daily_limit=7,
        achievement_title="rain-love",
        achievement_frame="frame-nine-colors",
    )
    records = records_view()
    records = replace(
        records,
        group_name="体型与重量纪录 · 本群视觉验收",
        items=tuple(replace(item, record_label="体型", achievement_title="title-traveler") for item in records.items),
        global_items=tuple(
            replace(
                item,
                record_label="体型" if index == 0 else "重量",
                achievement_title="rain-love",
                holder_display_name="同名也不会串称号的巨物观察员",
            )
            for index, item in enumerate(records.global_items)
        ),
        giant_sightings=tuple(
            replace(item, achievement_title="title-three-world-master") for item in records.giant_sightings
        ),
    )
    daily = daily_giants_view()
    daily = replace(
        daily,
        group_name="今日巨物 · 原始抓取者的双榜",
        size_items=tuple(
            replace(item, achievement_title="rain-love" if index == 0 else "title-traveler" if index == 8 else "")
            for index, item in enumerate(daily.size_items)
        ),
        weight_items=tuple(
            replace(item, achievement_title="title-three-world-master" if index == 0 else "")
            for index, item in enumerate(daily.weight_items)
        ),
    )
    media.update(
        {item.key: sources["pig-r5-earth"] for item in (*daily.size_items, *daily.weight_items) if item.media_visible}
    )
    return {
        "profile": profile,
        "inventory": inventory,
        "food_inventory": food_inventory,
        "catalog": catalog,
        "food_catalog": food_catalog,
        "records": records,
        "daily_giants": daily,
        "inventory_empty": replace(inventory, total_count=0, page_count=1, items=()),
        "records_empty": replace(records, total_count=0, items=(), global_items=(), giant_sightings=()),
        "daily_giants_empty": replace(daily, participant_count=0, catch_count=0, size_items=(), weight_items=()),
    }, media


async def run(args: argparse.Namespace) -> dict:
    output: Path = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows, sources = _public_library()
    digests = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in sources.items()}
    fixtures, media = _fixtures(rows, sources)
    outputs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        renderer = PigCatcherRenderer(capability, render_options())
        try:
            for label, view in fixtures.items():
                capability.label = label
                method = getattr(renderer, f"render_{label.removesuffix('_empty')}")
                if label.removesuffix("_empty") in {"profile", "records"}:
                    rendered = await method(view)
                else:
                    rendered = await method(view, media)
                destination = output / f"{label}.png"
                write_image(destination, rendered)
                assert capability.page is not None
                (output / f"{label}.html").write_text(await capability.page.content(), encoding="utf-8")
                with Image.open(destination) as source:
                    thumb = source.convert("RGB")
                    thumb.thumbnail((320, 12000), Image.Resampling.LANCZOS)
                    thumb.save(output / f"{label}-320.png")
                outputs.append((label, destination))
            # A native-code atlas proves that the status vocabulary uses real paths.
            capability.label = "icon_atlas"
            cells = "".join(
                f"<article>{asset_icon(key)}<strong>{key}</strong></article>" for key in sorted(ASSET_ICON_KEYS)
            )
            html = (
                '<html><meta charset="utf-8"><style>body{margin:0;background:#fff9fc;'
                'font-family:"Microsoft YaHei",sans-serif}main{width:1120px;padding:40px}'
                'h1{color:#765168}section{display:grid;grid-template-columns:repeat(6,1fr);gap:16px}'
                'article{display:flex;align-items:center;flex-direction:column;gap:10px;padding:24px 8px;'
                'background:white;border:1px solid #ead5df}svg{width:58px;height:58px}'
                'strong{font-size:15px;color:#654559}</style><main><h1>原生状态图标 · 巨物、收藏与隐私</h1><section>'
                + cells
                + "</section></main></html>"
            )
            result = await capability.html2png(html, selector="main", viewport={"width": 1200, "height": 1600})
            rendered = RenderedImage(result["image_base64"], result["mime"], result["width"], result["height"], 0)
            destination = output / "icon_atlas.png"
            write_image(destination, rendered)
            (output / "icon_atlas.html").write_text(html, encoding="utf-8")
            outputs.append(("icon_atlas", destination))
        finally:
            await capability.close()
            await browser.close()
    after = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in sources.items()}
    report = {
        "fixture_mode": "synthetic view models; public local media only; no database or MaiBot",
        "source_sha256": digests,
        "source_unchanged": digests == after,
        "page_count": len(outputs),
        "diagnostics": capability.diagnostics,
        "contact_sheet": str(write_contact_sheet(outputs, output)),
    }
    report["passed"] = report["source_unchanged"] and all(
        not diagnostic[key]
        for diagnostic in capability.diagnostics
        for key in ("clippedText", "outside", "brokenImages", "clippedMedia")
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    report = asyncio.run(run(parser.parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
