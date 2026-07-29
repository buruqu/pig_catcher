"""Render and inspect representative third-round business views with Chromium."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import sqlite3
import sys
from collections.abc import Awaitable, Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence
from playwright.async_api import Browser, Page, async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.domain import PIG_RARITY_NAMES, Rarity  # noqa: E402
from pig_catcher.rendering import (  # noqa: E402
    AnimatedCardComposer,
    CatalogItemViewModel,
    CatalogViewModel,
    CollectionProgressViewModel,
    InventoryItemViewModel,
    InventoryViewModel,
    ItemReceiptViewModel,
    PigCardViewModel,
    PigCatcherRenderer,
    ProfileViewModel,
    RecordItemViewModel,
    RecordsViewModel,
    RenderedImage,
    RenderOptions,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--browser-executable", type=Path)
    return parser.parse_args()


class PlaywrightRenderCapability:
    """Local MaiBot html2png equivalent with DOM diagnostics."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self.page: Page | None = None
        self.label = ""
        self.diagnostics: list[dict[str, object]] = []

    async def open(self) -> None:
        self.page = await self.browser.new_page(
            viewport={"width": 1200, "height": 1600}
        )

        async def block_network(route: Any) -> None:
            if str(route.request.url).startswith(("http://", "https://")):
                await route.abort()
            else:
                await route.continue_()

        await self.page.route("**/*", block_network)

    async def close(self) -> None:
        if self.page is not None:
            await self.page.close()
            self.page = None

    async def html2png(self, html: str, **kwargs: object) -> object:
        if self.page is None:
            raise RuntimeError("Playwright page is not open")
        viewport = dict(kwargs.get("viewport") or {})
        await self.page.set_viewport_size(
            {
                "width": int(viewport.get("width", 1200)),
                "height": int(viewport.get("height", 1600)),
            }
        )
        await self.page.set_content(html, wait_until="load")
        selector = str(kwargs.get("selector") or "body")
        locator = self.page.locator(selector)
        await locator.wait_for(state="visible")
        diagnostic = await locator.evaluate(
            """
            root => {
              const rootRect = root.getBoundingClientRect();
              const leafText = [...root.querySelectorAll("h1,h2,h3,p,strong,span,em")];
              const clippedText = leafText
                .filter(el => el.textContent.trim() && el.getBoundingClientRect().width > 0)
                .filter(el => {
                  const style = getComputedStyle(el);
                  const clippedX = el.scrollWidth > el.clientWidth + 1 &&
                    !["visible", "clip"].includes(style.overflowX);
                  const clippedY = el.scrollHeight > el.clientHeight + 1 &&
                    (
                      !["visible", "clip"].includes(style.overflowY) ||
                      el.scrollHeight > el.clientHeight + 6
                    );
                  return clippedX || clippedY;
                })
                .map(el => ({
                  tag: el.tagName,
                  className: el.className,
                  text: el.textContent.trim().slice(0, 120),
                  client: [el.clientWidth, el.clientHeight],
                  scroll: [el.scrollWidth, el.scrollHeight],
                }));
              const outside = [...root.querySelectorAll("*")]
                .filter(el => {
                  const rect = el.getBoundingClientRect();
                  if (rect.width <= 0 || rect.height <= 0) return false;
                  return rect.left < rootRect.left - 1 ||
                    rect.right > rootRect.right + 1 ||
                    rect.top < rootRect.top - 1 ||
                    rect.bottom > rootRect.bottom + 1;
                })
                .slice(0, 30)
                .map(el => ({
                  tag: el.tagName,
                  className: el.className,
                  text: el.textContent.trim().slice(0, 80),
                }));
              const brokenImages = [...root.querySelectorAll("img")]
                .filter(image => !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0)
                .map(image => image.alt);
              return {
                root: [Math.round(rootRect.width), Math.round(rootRect.height)],
                rootClient: [root.clientWidth, root.clientHeight],
                rootScroll: [root.scrollWidth, root.scrollHeight],
                clippedText,
                outside,
                brokenImages,
              };
            }
            """
        )
        self.diagnostics.append({"label": self.label, **dict(diagnostic)})
        payload = await locator.screenshot(type="png", animations="disabled")
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
        return {
            "image_base64": base64.b64encode(payload).decode("ascii"),
            "mime": "image/png",
            "width": width,
            "height": height,
        }


def load_rows(database_path: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                template_id, display_name, rarity, description, image_relpath,
                image_fit, media_format, is_animated, frame_count,
                length_min, length_max, weight_min, weight_max,
                collection_name, collection_total, character_name
            FROM pig_templates
            WHERE enabled = 1 AND scope_type = 'common'
            ORDER BY rarity, template_id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def collection_views(rows: Sequence[Mapping[str, object]]) -> tuple[CollectionProgressViewModel, ...]:
    definitions: dict[str, tuple[int, int]] = {}
    for row in rows:
        name = str(row["collection_name"] or "")
        if not name:
            continue
        available, total = definitions.get(name, (0, int(row["collection_total"])))
        definitions[name] = (available + 1, total)
    return tuple(
        CollectionProgressViewModel(
            collection_name=name,
            collaboration_name="BanG Dream!",
            collected_count=min(index + 1, available),
            available_count=available,
            total_count=total,
        )
        for index, (name, (available, total)) in enumerate(definitions.items())
    )


def pig_card(
    row: Mapping[str, object],
    *,
    mode_label: str,
    media_visible: bool = True,
    long_description: bool = False,
) -> PigCardViewModel:
    size = (float(row["length_min"]) + float(row["length_max"])) / 2
    weight = (float(row["weight_min"]) + float(row["weight_max"])) / 2
    description = str(row["description"])
    if long_description:
        description = (description + " 这段文字用于验证极端长度仍然完整可读。") * 10
    return PigCardViewModel(
        mode_label=mode_label,
        display_name=str(row["display_name"]),
        owner_display_name="视觉验收成员",
        rarity=int(row["rarity"]),
        rarity_name=PIG_RARITY_NAMES[Rarity(int(row["rarity"]))],
        short_code="A19F2C3D",
        description=description[:500],
        size_value=size,
        size_percentile=0.62,
        size_label="标准体型",
        weight_value=weight,
        weight_percentile=0.58,
        weight_label="匀称",
        fat_ratio=53.4,
        fat_label="均衡",
        official_value=588,
        acquired_at="2026-07-28 18:30",
        image_fit=str(row["image_fit"]),
        media_visible=media_visible,
        is_animated=bool(row["is_animated"]),
        media_format=str(row["media_format"]),
        collection_name=str(row["collection_name"] or ""),
        character_name=str(row["character_name"] or ""),
        coin_reward=30 if mode_label == "抓猪成功" else None,
        experience_reward=45 if mode_label == "抓猪成功" else None,
        coin_balance=1260 if mode_label == "抓猪成功" else None,
        total_experience=720 if mode_label == "抓猪成功" else None,
        player_level=4 if mode_label == "抓猪成功" else None,
        level_title="抓猪老手" if mode_label == "抓猪成功" else "",
        next_level_experience=800 if mode_label == "抓猪成功" else None,
        level_progress_percent=77.14 if mode_label == "抓猪成功" else 0.0,
        daily_count=12 if mode_label == "抓猪成功" else None,
        daily_limit=20 if mode_label == "抓猪成功" else None,
        item_name="巨物玉米" if mode_label == "抓猪成功" else "",
        catalog_new=True,
        size_record=True,
        weight_record=mode_label == "抓猪成功",
    )


def inventory_model(rows: Sequence[Mapping[str, object]]) -> InventoryViewModel:
    return InventoryViewModel(
        display_name="视觉验收成员",
        page=1,
        page_count=3,
        total_count=21,
        rarity=None,
        sort="价值",
        items=tuple(
            InventoryItemViewModel(
                key=str(row["template_id"]),
                display_name=str(row["display_name"]),
                short_code=f"{index + 1:08X}",
                rarity=int(row["rarity"]),
                size_value=(float(row["length_min"]) + float(row["length_max"])) / 2,
                weight_value=(float(row["weight_min"]) + float(row["weight_max"])) / 2,
                fat_label=("偏瘦", "均衡", "偏肥")[index % 3],
                official_value=30 + index * 71,
                media_visible=True,
                is_animated=bool(row["is_animated"]),
                image_fit=str(row["image_fit"]),
            )
            for index, row in enumerate(rows)
        ),
    )


def catalog_model(
    rows: Sequence[Mapping[str, object]],
    collections: tuple[CollectionProgressViewModel, ...],
) -> CatalogViewModel:
    return CatalogViewModel(
        display_name="视觉验收成员",
        total_count=81,
        rarity=None,
        undiscovered_only=False,
        collected_count=26,
        visible_catalog_total=81,
        items=tuple(
            CatalogItemViewModel(
                key=str(row["template_id"]),
                display_name=str(row["display_name"]),
                rarity=int(row["rarity"]),
                discovered=index < 8,
                acquired_count=index + 1 if index < 8 else 0,
                best_size=40.0 + index if index < 8 else None,
                best_weight=55.0 + index * 2 if index < 8 else None,
                collection_name=str(row["collection_name"] or ""),
                character_name=str(row["character_name"] or ""),
                media_visible=index < 8,
                is_animated=bool(row["is_animated"]),
                image_fit=str(row["image_fit"]),
            )
            for index, row in enumerate(rows)
        ),
        collections=collections,
    )


def records_model(rows: Sequence[Mapping[str, object]]) -> RecordsViewModel:
    return RecordsViewModel(
        group_name="第三轮视觉验收群",
        page=1,
        page_count=2,
        total_count=20,
        items=tuple(
            RecordItemViewModel(
                record_label="体型" if index % 2 == 0 else "重量",
                record_value=42.0 + index * 3.17,
                unit="cm" if index % 2 == 0 else "kg",
                display_name=str(row["display_name"]),
                rarity=int(row["rarity"]),
                short_code=f"{index + 101:08X}",
                holder_display_name=f"纪录保持者{index + 1}",
                achieved_at=f"2026-07-{28 - index // 3:02d} 18:30",
            )
            for index, row in enumerate(rows)
        ),
    )


def render_options() -> RenderOptions:
    return RenderOptions(
        card_width=1200,
        viewport_height=1600,
        device_scale_factor=1.0,
        render_timeout_ms=15000,
        max_png_bytes=12 * 1024 * 1024,
        max_animation_bytes=50 * 1024 * 1024,
        missing_frame_duration_ms=100,
        font_family='"Microsoft YaHei", sans-serif',
    )


def write_image(path: Path, rendered: RenderedImage) -> None:
    raw = base64.b64decode(rendered.image_base64)
    path.write_bytes(raw)
    with Image.open(BytesIO(raw)) as image:
        image.seek(0)
        frame = image.convert("RGBA")
        if frame.getbbox() is None:
            raise RuntimeError(f"Rendered output is blank: {path.name}")
        extrema = frame.getextrema()
        if all(low == high for low, high in extrema):
            raise RuntimeError(f"Rendered output is a single color: {path.name}")


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default()


def write_thumbnails(
    outputs: Sequence[tuple[str, Path]],
    output_root: Path,
) -> list[str]:
    thumbnail_root = output_root / "thumbnails-480"
    thumbnail_root.mkdir()
    written: list[str] = []
    for label, path in outputs:
        with Image.open(path) as source:
            source.seek(0)
            image = source.convert("RGB")
        height = max(1, round(image.height * 480 / image.width))
        thumbnail = image.resize((480, height), Image.Resampling.LANCZOS)
        destination = thumbnail_root / f"{label}.jpg"
        thumbnail.save(destination, quality=92)
        written.append(str(destination))
    return written


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
        "Pig Catcher third-round Chromium acceptance",
        fill="#553846",
        font=font(26),
    )
    for index, (label, path) in enumerate(outputs):
        with Image.open(path) as source:
            source.seek(0)
            preview = ImageOps.contain(
                source.convert("RGB"),
                (380, 320),
                method=Image.Resampling.LANCZOS,
            )
        x = (index % columns) * cell_width
        y = 72 + (index // columns) * cell_height
        sheet.paste(preview, (x + 20, y + 8))
        draw.text((x + 20, y + 340), label, fill="#59434E", font=font(16))
    destination = output_root / "contact-sheet.jpg"
    sheet.save(destination, quality=92)
    return destination


def animation_report(
    source_path: Path,
    output_path: Path,
    *,
    missing_duration_ms: int,
) -> dict[str, object]:
    def inspect(path: Path) -> dict[str, object]:
        with Image.open(path) as image:
            durations = [
                int(frame.info.get("duration", image.info.get("duration", 0)) or 0)
                for frame in ImageSequence.Iterator(image)
            ]
            return {
                "frames": int(getattr(image, "n_frames", 1)),
                "durations": durations,
                "loop": image.info.get("loop"),
            }

    source = inspect(source_path)
    output = inspect(output_path)
    expected_durations = [
        duration if duration > 0 else missing_duration_ms
        for duration in source["durations"]
    ]
    preserved = (
        source["frames"] == output["frames"]
        and source["loop"] == output["loop"]
        and expected_durations == output["durations"]
    )
    return {
        "source": source,
        "output": output,
        "expected_output_durations": expected_durations,
        "used_missing_duration_fallback": any(
            duration <= 0 for duration in source["durations"]
        ),
        "preserved": preserved,
    }


async def accept(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.resolve(strict=True)
    output_root = args.output.resolve()
    if output_root.exists():
        raise FileExistsError(f"Output already exists: {output_root}")
    output_root.mkdir(parents=True)
    rows = load_rows(data_dir / args.database_filename)
    if len(rows) < 20:
        raise RuntimeError(f"Expected at least 20 common pig templates, found {len(rows)}")
    static_rows = [row for row in rows if not bool(row["is_animated"])]
    animated_rows = sorted(
        (row for row in rows if bool(row["is_animated"])),
        key=lambda row: int(row["frame_count"]),
    )
    if not static_rows or not animated_rows:
        raise RuntimeError("Formal catalog must contain both static and animated pigs")
    selected_rows = [
        *animated_rows[:2],
        *static_rows[:10],
    ]
    collections = collection_views(rows)
    outputs: list[tuple[str, Path]] = []
    results: list[dict[str, object]] = []

    async with async_playwright() as playwright:
        launch_options: dict[str, object] = {"headless": True}
        if args.browser_executable is not None:
            launch_options["executable_path"] = str(
                args.browser_executable.resolve(strict=True)
            )
        browser = await playwright.chromium.launch(**launch_options)
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        renderer = PigCatcherRenderer(capability, render_options())
        composer = AnimatedCardComposer(
            max_output_bytes=50 * 1024 * 1024,
            missing_frame_duration_ms=100,
        )

        async def save(
            label: str,
            render: Awaitable[RenderedImage],
            *,
            extension: str = ".png",
        ) -> Path:
            capability.label = label
            rendered = await render
            destination = output_root / f"{label}{extension}"
            write_image(destination, rendered)
            outputs.append((label, destination))
            results.append(
                {
                    "label": label,
                    "path": str(destination),
                    "mime_type": rendered.mime_type,
                    "width": rendered.width,
                    "height": rendered.height,
                    "bytes": rendered.byte_length,
                    "frames": rendered.frame_count,
                    "duration_ms": rendered.total_duration_ms,
                    "loop": rendered.loop_count,
                }
            )
            return destination

        try:
            static_row = static_rows[0]
            await save(
                "01-catch-static",
                renderer.render_static_pig_card(
                    pig_card(static_row, mode_label="抓猪成功"),
                    data_dir / str(static_row["image_relpath"]),
                ),
            )

            animated_row = animated_rows[0]
            animated_view = pig_card(animated_row, mode_label="抓猪成功")
            capability.label = "02-catch-animated-base"
            base = await renderer.render_pig_card_base(animated_view)
            animated_render = await composer.compose(
                base=base.image,
                source_path=data_dir / str(animated_row["image_relpath"]),
                slot=base.media_slot,
            )
            animated_output = await save(
                "02-catch-animated",
                asyncio.sleep(0, result=animated_render),
                extension=".gif",
            )

            await save(
                "03-detail-revoked-long",
                renderer.render_static_pig_card(
                    pig_card(
                        static_rows[1],
                        mode_label="猪猪详情",
                        media_visible=False,
                        long_description=True,
                    ),
                    None,
                ),
            )
            await save(
                "04-profile-rich",
                renderer.render_profile(
                    ProfileViewModel(
                        display_name="视觉验收成员",
                        level=4,
                        title="抓猪高手",
                        total_experience=2600,
                        next_threshold=6000,
                        progress_percent=21.43,
                        coin_balance=12880,
                        total_catches=138,
                        active_pigs=96,
                        catalog_count=57,
                        visible_catalog_total=81,
                        held_records=11,
                        daily_count=18,
                        daily_limit=30,
                        cooldown_remaining_seconds=27,
                        feed_level=3,
                        armed_item_name="巨物玉米",
                        armed_item_quantity=4,
                        collections=collections,
                    )
                ),
            )
            await save(
                "05-profile-long-empty",
                renderer.render_profile(
                    ProfileViewModel(
                        display_name="极端长度昵称" * 20,
                        level=1,
                        title="被猪拱",
                        total_experience=0,
                        next_threshold=100,
                        progress_percent=0,
                        coin_balance=0,
                        total_catches=0,
                        active_pigs=0,
                        catalog_count=0,
                        visible_catalog_total=81,
                        held_records=0,
                        daily_count=0,
                        daily_limit=30,
                        cooldown_remaining_seconds=0,
                        feed_level=0,
                        armed_item_name="",
                        armed_item_quantity=0,
                    )
                ),
            )

            inventory = inventory_model(selected_rows[:12])
            inventory_paths = {
                str(row["template_id"]): data_dir / str(row["image_relpath"])
                for row in selected_rows[:12]
                if not bool(row["is_animated"])
            }
            await save(
                "06-inventory-full",
                renderer.render_inventory(inventory, inventory_paths),
            )
            await save(
                "07-inventory-empty",
                renderer.render_inventory(
                    InventoryViewModel(
                        display_name="空背包成员",
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

            catalog = catalog_model(selected_rows[:12], collections)
            catalog_paths = {
                str(row["template_id"]): data_dir / str(row["image_relpath"])
                for index, row in enumerate(selected_rows[:12])
                if index < 8 and not bool(row["is_animated"])
            }
            await save(
                "08-catalog-full",
                renderer.render_catalog(catalog, catalog_paths),
            )
            await save(
                "09-catalog-empty-filter",
                renderer.render_catalog(
                    CatalogViewModel(
                        display_name="图鉴筛选成员",
                        total_count=0,
                        rarity=6,
                        undiscovered_only=True,
                        collected_count=0,
                        visible_catalog_total=81,
                        items=(),
                        collections=collections,
                    ),
                    {},
                ),
            )

            await save(
                "10-records-full",
                renderer.render_records(records_model(selected_rows[:10])),
            )
            await save(
                "11-records-empty",
                renderer.render_records(
                    RecordsViewModel(
                        group_name="尚无纪录的群",
                        page=1,
                        page_count=1,
                        total_count=0,
                        items=(),
                    )
                ),
            )
            await save(
                "12-item-armed",
                renderer.render_item_receipt(
                    ItemReceiptViewModel(
                        operation="armed",
                        item_name="巨物玉米",
                        action_label="抓猪",
                        quantity=4,
                        effect_summary="体型百分位 +0.12",
                    )
                ),
            )
            await save(
                "13-item-cancelled",
                renderer.render_item_receipt(
                    ItemReceiptViewModel(
                        operation="cancelled",
                        item_name="幸运猪哨",
                        action_label="抓猪",
                        quantity=2,
                        effect_summary="3 至 5 星相对权重 +12%，6 星 +2%",
                    )
                ),
            )
        finally:
            await capability.close()
            await browser.close()

    diagnostics = capability.diagnostics
    diagnostic_failures = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic["clippedText"]
        or diagnostic["outside"]
        or diagnostic["brokenImages"]
        or diagnostic["rootClient"] != diagnostic["rootScroll"]
    ]
    thumbnails = write_thumbnails(outputs, output_root)
    contact_sheet = write_contact_sheet(outputs, output_root)
    animation = animation_report(
        data_dir / str(animated_row["image_relpath"]),
        animated_output,
        missing_duration_ms=100,
    )
    report = {
        "view_count": len(outputs),
        "results": results,
        "diagnostics": diagnostics,
        "diagnostic_failures": diagnostic_failures,
        "animation": animation,
        "thumbnails": thumbnails,
        "contact_sheet": str(contact_sheet),
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if diagnostic_failures:
        raise RuntimeError(
            f"DOM diagnostics found {len(diagnostic_failures)} failing views; "
            f"see {output_root / 'report.json'}"
        )
    if not bool(animation["preserved"]):
        raise RuntimeError("Animated catch card did not preserve source frame timing")
    return report


async def main() -> None:
    report = await accept(parse_args())
    print(
        json.dumps(
            {
                "view_count": report["view_count"],
                "diagnostic_failures": len(report["diagnostic_failures"]),
                "animation_preserved": report["animation"]["preserved"],
                "contact_sheet": report["contact_sheet"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
