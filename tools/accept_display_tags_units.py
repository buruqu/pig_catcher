"""离线验收展示标签、吨/米、长说明和稳定动画槽；不连接任何生产数据库或 QQ。"""

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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.domain.food_effects import effect_summary  # noqa: E402
from pig_catcher.rendering import (  # noqa: E402
    AnimatedCardComposer,
    CatalogItemViewModel,
    CatalogViewModel,
    DailyGiantItemViewModel,
    DailyGiantsViewModel,
    FoodCardViewModel,
    FoodInventoryItemViewModel,
    FoodInventoryViewModel,
    InventoryItemViewModel,
    InventoryViewModel,
    PigCatcherRenderer,
    RecordItemViewModel,
    RecordsViewModel,
)
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    pig_card,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


def _row(entry: dict) -> dict:
    collection = entry.get("collection") or {}
    return {
        **entry,
        "length_min": entry["length_min_cm"],
        "length_max": entry["length_max_cm"],
        "weight_min": entry["weight_min_kg"],
        "weight_max": entry["weight_max_kg"],
        "image_fit": entry.get("fit", "contain"),
        "media_format": "PNG",
        "is_animated": False,
        "collection_name": collection.get("collection_name", ""),
        "character_name": collection.get("character_name", ""),
        "display_tags_json": json.dumps(entry.get("display_tags", []), ensure_ascii=False),
    }


async def run(args) -> dict:
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请使用新的隔离验收目录，不覆盖既有证据")
    output.mkdir(parents=True)
    source = PROJECT_ROOT / "asset_library/current"
    entries = json.loads((source / "assets.json").read_text(encoding="utf-8"))["entries"]
    pigs = [e for e in entries if e["kind"] == "pig" and e["scope"] == "common"]
    main_entry = next(e for e in pigs if e["rarity"] == 5)
    main_source = source / main_entry["image"]
    food_entry = next(e for e in entries if e["kind"] == "food" and e["scope"] == "common")
    food_source = source / food_entry["image"]
    original_hash = hashlib.sha256(main_source.read_bytes()).hexdigest()
    base = replace(
        pig_card(_row(main_entry), mode_label="抓猪成功"),
        display_tags=("效率曲", "EXIST", "谱面梗", "BanG Dream!"),
        daily_count=3,
        size_value=72.4,
        weight_value=153.25,
    )
    tags = ("ABCDEFGHIJKLMNOPQRST", "五条猪与谱面联动的长标签示例", "MyGO!!!!!", "效率曲 SOS!", "吨级巨物")
    extreme = replace(
        base,
        display_tags=tags,
        description=("这段较长描述用于验证标签、正文与概率区不会互相遮挡。" * 20)[:500],
        size_value=2694.7,
        weight_value=75770.78,
        global_size_record=True,
        global_weight_record=True,
        giant_sighting=True,
        body_label="双顶巨物",
        body_description="既有实例的实际尺寸、重量和价值保持原样。",
        achievement_title="猪猪巡演与战斗收藏的特别荣誉称号",
        effect_summaries=("本次参与的全群效果，发动者是昵称正常的群友，尚余 8/10 次。" * 3,),
        excluded_summaries=("互斥道具未消耗，留待下次兼容操作。" * 3,),
    )
    outputs = []
    slots = []
    animation_evidence = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())

            async def save(name, render):
                capability.label = name
                image = await render
                destination = output / f"{name}.png"
                write_image(destination, image)
                outputs.append(destination)
                return image

            for name, view, path in (
                ("01-tagged-catch", base, main_source),
                ("02-extreme-tagged-catch", extreme, main_source),
                (
                    "03-tonne-detail",
                    replace(base, coin_reward=None, size_value=1000.1, weight_value=1234.56),
                    main_source,
                ),
                ("04-mini-detail", replace(base, coin_reward=None, size_value=4, weight_value=0.35), main_source),
                ("05-missing-media", extreme, None),
                (
                    "06-revoked-private",
                    replace(base, coin_reward=None, media_visible=False, display_tags=("严禁泄露的身份标签",)),
                    None,
                ),
            ):
                await save(name, renderer.render_static_pig_card(view, path))
                slots.append(
                    await capability.page.locator(".pig-card__media").evaluate(
                        "el => { const a=el.getBoundingClientRect(), b=el.closest('main').getBoundingClientRect();"
                        "return [a.x-b.x,a.y-b.y,a.width,a.height]; }"
                    )
                )
                if not view.media_visible:
                    assert "严禁泄露的身份标签" not in await capability.page.locator("main").inner_text()

            inventory_items = tuple(
                InventoryItemViewModel(
                    key=str(i),
                    display_name="超级无敌漫长名字的猪猪" + str(i),
                    short_code=f"TAG{i:05d}",
                    rarity=i % 5 + 1,
                    size_value=2694.7 + i,
                    weight_value=75770.78 + i,
                    fat_label="均衡",
                    official_value=800 + i,
                    media_visible=True,
                    is_animated=False,
                    image_fit="contain",
                    is_favorite=i % 2 == 0,
                    activity_label="巡演中" if i % 3 == 0 else "",
                    display_tags=tags,
                )
                for i in range(12)
            )
            await save(
                "07-inventory-12",
                renderer.render_inventory(
                    InventoryViewModel("标签验收成员", 1, 1, 12, None, "重量", inventory_items),
                    {i.key: main_source for i in inventory_items},
                ),
            )
            catalog_items = tuple(
                CatalogItemViewModel(
                    key=str(i),
                    display_name="谱面联动猪猪" + str(i) if i < 25 else "保密六星猪",
                    rarity=i % 5 + 1 if i < 25 else 6,
                    discovered=i < 25,
                    acquired_count=i + 1,
                    best_size=2694.7,
                    best_weight=75770.78,
                    collection_name="MyGO!!!!!",
                    character_name="",
                    media_visible=i < 25,
                    is_animated=False,
                    image_fit="contain",
                    display_tags=tags if i < 25 else ("严禁泄露的身份标签",),
                )
                for i in range(26)
            )
            await save(
                "08-catalog-privacy",
                renderer.render_catalog(
                    CatalogViewModel("标签验收成员", 26, None, False, 25, 26, catalog_items),
                    {i.key: main_source for i in catalog_items if i.media_visible},
                ),
            )
            assert "严禁泄露" not in await capability.page.locator("main").inner_text()
            records = (
                RecordItemViewModel("体型", 2694.7, "cm", "地球猪", 5, "BIG00001", "吨级纪录群友", "08-28 12:00"),
                RecordItemViewModel("重量", 75770.78, "kg", "地球猪", 5, "BIG00002", "吨级纪录群友", "08-28 12:00"),
            )
            await save(
                "09-tonne-records", renderer.render_records(RecordsViewModel("离线验收群", 1, 1, 2, records, records))
            )
            giant = DailyGiantItemViewModel(
                "giant",
                1,
                "吨级纪录群友",
                "地球猪",
                5,
                "BIG00001",
                2694.7,
                75770.78,
                "08-28 12:00",
                True,
                False,
                "contain",
            )
            await save(
                "10-daily-tonnes",
                renderer.render_daily_giants(
                    DailyGiantsViewModel("离线验收群", "2026-08-28", 1, 1, (giant,), (giant,)),
                    {"giant": main_source},
                ),
            )
            food = FoodCardViewModel(
                "美食详情",
                food_entry["display_name"],
                "离线验收成员",
                food_entry["rarity"],
                "美味大餐",
                "FOOD0001",
                food_entry["description"],
                38888.88,
                "均衡",
                300,
                "08-28 12:00",
                "地球猪#BIG0001",
                "数值规则未修改，展示单位使用吨。",
                "contain",
                True,
                False,
                "PNG",
            )
            await save("11-tonne-food", renderer.render_static_food_card(food, food_source))
            food_items = (
                FoodInventoryItemViewModel(
                    "food",
                    food_entry["display_name"],
                    "FOOD0001",
                    food_entry["rarity"],
                    38888.88,
                    "均衡",
                    300,
                    True,
                    False,
                    "contain",
                ),
            )
            await save(
                "12-tonne-food-inventory",
                renderer.render_food_inventory(
                    FoodInventoryViewModel("离线验收成员", 1, 1, 1, None, "份量", food_items),
                    {"food": food_source},
                ),
            )
            animated_entry = None
            for entry in pigs:
                with Image.open(source / entry["image"]) as media:
                    if 1 < getattr(media, "n_frames", 1) <= 20:
                        animated_entry = entry
                        break
            if animated_entry is None:
                raise RuntimeError("未找到小于等于20帧的公共动画素材")
            animated_source = source / animated_entry["image"]
            before_hash = hashlib.sha256(animated_source.read_bytes()).hexdigest()
            animation_view = replace(base, display_name=animated_entry["display_name"], is_animated=True)
            capability.label = "13-animated-tagged-base"
            animated_base = await renderer.render_pig_card_base(animation_view)
            animated = await AnimatedCardComposer(max_output_bytes=50 * 1024 * 1024).compose(
                base=animated_base.image,
                source_path=animated_source,
                slot=animated_base.media_slot,
            )
            animated_path = output / "13-animated-tagged.gif"
            write_image(animated_path, animated)
            outputs.append(animated_path)
            with Image.open(animated_source) as media:
                expected_frames = media.n_frames
                durations = []
                for i in range(expected_frames):
                    media.seek(i)
                    media.load()
                    durations.append(int(media.info.get("duration", 0) or 100))
                assert animated.frame_count == expected_frames
                assert animated.total_duration_ms == sum(durations)
                assert animated.loop_count == media.info.get("loop")
            assert before_hash == hashlib.sha256(animated_source.read_bytes()).hexdigest()
            animation_evidence = {
                "frames": animated.frame_count,
                "duration_ms": animated.total_duration_ms,
                "loop": animated.loop_count,
                "bytes": animated.byte_length,
            }
        finally:
            await capability.close()
            await browser.close()
    assert original_hash == hashlib.sha256(main_source.read_bytes()).hexdigest()
    assert all(slot == slots[0] for slot in slots), slots
    failures = [row for row in capability.diagnostics if row["clippedText"] or row["outside"] or row["brokenImages"]]
    report = {
        "status": "failed" if failures else "passed",
        "count": len(outputs),
        "diagnostics": capability.diagnostics,
        "failures": failures,
        "media_slots": slots,
        "animation": animation_evidence,
        "scope": "offline synthetic views only; no production database or QQ",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_sheet(outputs, output / "contact-sheet.jpg")
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False))
    return {
        "status": "passed",
        "count": len(outputs),
        "report": str(output / "report.json"),
        "animation": animation_evidence,
    }


async def run_formal_round9(args) -> dict:
    """Render every new formal asset with its real description, tags and range midpoint.

    Instances/rewards remain synthetic QA data; animation originals are read only
    and animated cards retain their complete frame/timing sequence.
    """
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请使用新的隔离验收目录，不覆盖既有证据")
    definitions = json.loads(
        (PROJECT_ROOT / "catalogs/formal/pig-and-food-definitions.json").read_text(encoding="utf-8")
    )["entries"]
    # 六星素材仍按群隔离保存；本页仅取同名的一份做像素验收，跨群边界另由集成测试覆盖。
    ids = {entry["template_id"] for entry in definitions if entry["source_path"].startswith("第九期/")}
    ids.update({"pig-g1092931381-yilu-green-core", "food-g1092931381-yilu-green-core-pie"})
    source = PROJECT_ROOT / "asset_library/current"
    entries = [
        entry
        for entry in json.loads((source / "assets.json").read_text(encoding="utf-8"))["entries"]
        if entry["template_id"] in ids
    ]
    assert len(entries) == len(ids) == 70, "第九期两批合计47猪和23菜；六星同名仅取一份验收"
    output.mkdir(parents=True)
    outputs = []
    hashes = {}
    animation_results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())
            for index, entry in enumerate(entries, start=1):
                path = source / entry["image"]
                hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
                with Image.open(path) as media:
                    media_format = media.format or "PNG"
                    animated = bool(getattr(media, "is_animated", False))
                capability.label = entry["template_id"]
                if entry["kind"] == "pig":
                    view = replace(
                        pig_card(_row(entry), mode_label="抓猪成功"),
                        owner_display_name="第九期离线验收 · 示例数据",
                        media_format=media_format,
                        is_animated=animated,
                        acquired_at="2026-08-28 12:00",
                    )
                    if animated:
                        base = await renderer.render_pig_card_base(view)
                    else:
                        image = await renderer.render_static_pig_card(view, path)
                else:
                    view = FoodCardViewModel(
                        mode_label="第九期离线美食验收",
                        display_name=entry["display_name"],
                        owner_display_name="示例实例 · 非正式价值",
                        rarity=entry["rarity"],
                        rarity_name=f"{entry['rarity']}星美食",
                        short_code=f"FOOD{index:04}",
                        description=entry["description"],
                        portion_weight=88.8,
                        fat_label="均衡",
                        official_value=588,
                        acquired_at="2026-08-28 12:00",
                        source_selector="离线验收原料",
                        effect_summary=effect_summary(
                            str(entry.get("effect_id") or ""), entry.get("effect_params") or {}
                        ),
                        image_fit=entry.get("fit", "contain"),
                        media_visible=True,
                        media_format=media_format,
                        is_animated=animated,
                    )
                    if animated:
                        base = await renderer.render_food_card_base(view)
                    else:
                        image = await renderer.render_static_food_card(view, path)
                if animated:
                    image = await AnimatedCardComposer(max_output_bytes=50 * 1024 * 1024).compose(
                        base=base.image, source_path=path, slot=base.media_slot
                    )
                    with Image.open(path) as media:
                        assert image.frame_count == media.n_frames
                        durations = []
                        for frame in range(media.n_frames):
                            media.seek(frame)
                            media.load()
                            durations.append(int(media.info.get("duration", 0) or 100))
                        assert image.total_duration_ms == sum(durations)
                        assert image.loop_count == media.info.get("loop")
                    animation_results.append(
                        {
                            "template_id": entry["template_id"],
                            "frames": image.frame_count,
                            "duration_ms": image.total_duration_ms,
                            "bytes": image.byte_length,
                        }
                    )
                suffix = ".gif" if image.is_animated else ".png"
                destination = output / f"{index:02}-{entry['template_id']}{suffix}"
                write_image(destination, image)
                outputs.append(destination)
        finally:
            await capability.close()
            await browser.close()
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    failures = [row for row in capability.diagnostics if row["clippedText"] or row["outside"] or row["brokenImages"]]
    report = {
        "status": "failed" if failures else "passed",
        "count": len(outputs),
        "pigs": sum(entry["kind"] == "pig" for entry in entries),
        "foods": sum(entry["kind"] == "food" for entry in entries),
        "animations": animation_results,
        "diagnostics": capability.diagnostics,
        "failures": failures,
        "scope": "formal round9 media/metadata with synthetic instance data; no production database or QQ",
        "original_media_unchanged": True,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_sheet(outputs, output / "contact-sheet.jpg")
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False))
    return {"status": "passed", "count": len(outputs), "report": str(output / "report.json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    parser.add_argument("--formal-round9", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run_formal_round9(args) if args.formal_round9 else run(args)), ensure_ascii=False, indent=2
        )
    )
