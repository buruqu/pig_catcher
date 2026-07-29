"""Render and verify every active runtime asset without exposing a chat command."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from playwright.async_api import Browser, Page, async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.rendering import (  # noqa: E402
    AnimatedCardComposer,
    AssetPreviewViewModel,
    PigCatcherRenderer,
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
    """Local equivalent of MaiBot's public html2png contract for UAT only."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self.page: Page | None = None

    async def open(self) -> None:
        self.page = await self.browser.new_page(viewport={"width": 1200, "height": 1600})

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
                template_id, 'pig' AS kind, display_name, rarity, description,
                image_relpath, image_sha256, image_fit, media_format,
                is_animated, frame_count, total_duration_ms, loop_count,
                scope_type, collection_name, collection_total, character_name
            FROM pig_templates
            WHERE enabled = 1
            UNION ALL
            SELECT
                template_id, 'food' AS kind, display_name, rarity, description,
                image_relpath, image_sha256, image_fit, media_format,
                is_animated, frame_count, total_duration_ms, loop_count,
                scope_type, '' AS collection_name, 0 AS collection_total,
                '' AS character_name
            FROM food_templates
            WHERE enabled = 1
            ORDER BY kind DESC, rarity, template_id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default()


def write_contact_sheets(
    rows: list[dict[str, object]],
    rendered_paths: dict[str, Path],
    output_root: Path,
) -> list[str]:
    contacts_root = output_root / "contact-sheets"
    contacts_root.mkdir()
    page_size = 12
    outputs: list[str] = []
    for page_index in range(math.ceil(len(rows) / page_size)):
        page_rows = rows[page_index * page_size : (page_index + 1) * page_size]
        cell_w, cell_h = 390, 320
        header_h = 70
        columns = 3
        sheet = Image.new(
            "RGB",
            (columns * cell_w, header_h + math.ceil(len(page_rows) / columns) * cell_h),
            "#FFF9FB",
        )
        draw = ImageDraw.Draw(sheet)
        draw.text(
            (20, 15),
            f"2B rendered catalog {page_index + 1}",
            fill="#553846",
            font=font(24),
        )
        for index, row in enumerate(page_rows):
            path = rendered_paths[str(row["template_id"])]
            with Image.open(path) as image:
                image.seek(0)
                preview = ImageOps.contain(
                    image.convert("RGB"),
                    (350, 255),
                    method=Image.Resampling.LANCZOS,
                )
            x = (index % columns) * cell_w
            y = header_h + (index // columns) * cell_h
            sheet.paste(preview, (x + 20, y + 10))
            label = f"{row['display_name']} | {row['rarity']} star | {row['media_format']}"
            draw.text((x + 20, y + 275), label, fill="#59434E", font=font(15))
        output = contacts_root / f"{page_index + 1:02d}.jpg"
        sheet.save(output, quality=90)
        outputs.append(str(output))
    return outputs


def write_animation_strips(
    rows: list[dict[str, object]],
    rendered_paths: dict[str, Path],
    output_root: Path,
) -> list[str]:
    strips_root = output_root / "animation-strips"
    strips_root.mkdir()
    outputs: list[str] = []
    animated_rows = [row for row in rows if bool(row["is_animated"])]
    for index, row in enumerate(animated_rows, start=1):
        path = rendered_paths[str(row["template_id"])]
        with Image.open(path) as image:
            frame_count = int(getattr(image, "n_frames", 1))
            selected = (
                list(range(frame_count))
                if frame_count <= 12
                else sorted(set(round(i * (frame_count - 1) / 11) for i in range(12)))
            )
            frames = []
            durations = []
            for frame_index in selected:
                image.seek(frame_index)
                frames.append(image.convert("RGB").copy())
                durations.append(int(image.info.get("duration", 0) or 0))
        columns = 4
        cell_w, cell_h = 290, 250
        header_h = 72
        sheet = Image.new(
            "RGB",
            (
                columns * cell_w,
                header_h + math.ceil(len(frames) / columns) * cell_h,
            ),
            "#FFF9FB",
        )
        draw = ImageDraw.Draw(sheet)
        draw.text(
            (18, 12),
            f"{row['display_name']} | {frame_count} frames",
            fill="#553846",
            font=font(24),
        )
        for frame_position, frame in enumerate(frames):
            x = (frame_position % columns) * cell_w
            y = header_h + (frame_position // columns) * cell_h
            preview = ImageOps.contain(
                frame,
                (270, 200),
                method=Image.Resampling.LANCZOS,
            )
            sheet.paste(preview, (x + 10, y + 5))
            draw.text(
                (x + 10, y + 210),
                f"frame {selected[frame_position]} | {durations[frame_position]} ms",
                fill="#745B67",
                font=font(13),
            )
        output = strips_root / f"{index:02d}.jpg"
        sheet.save(output, quality=90)
        outputs.append(str(output))
    return outputs


async def accept(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.resolve(strict=True)
    output_root = args.output.resolve()
    if output_root.exists():
        raise FileExistsError(f"Output already exists: {output_root}")
    output_root.mkdir(parents=True)
    rendered_root = output_root / "rendered"
    rendered_root.mkdir()
    rows = load_rows(data_dir / args.database_filename)
    expected_assets = 102
    if len(rows) != expected_assets:
        raise RuntimeError(
            f"Expected {expected_assets} active assets, found {len(rows)}"
        )

    rendered_paths: dict[str, Path] = {}
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
        try:
            renderer = PigCatcherRenderer(
                capability,
                RenderOptions(
                    card_width=1200,
                    viewport_height=1600,
                    device_scale_factor=1.0,
                    render_timeout_ms=15000,
                    max_png_bytes=12 * 1024 * 1024,
                    max_animation_bytes=50 * 1024 * 1024,
                    missing_frame_duration_ms=100,
                    font_family='"Microsoft YaHei", sans-serif',
                ),
            )
            composer = AnimatedCardComposer(
                max_output_bytes=50 * 1024 * 1024,
                missing_frame_duration_ms=100,
            )
            for row in rows:
                source_path = data_dir / str(row["image_relpath"])
                view = AssetPreviewViewModel(
                    display_name=str(row["display_name"]),
                    description=str(row["description"]),
                    rarity=int(row["rarity"]),
                    kind_label="猪猪" if row["kind"] == "pig" else "猪猪美食",
                    media_format=str(row["media_format"]),
                    frame_count=int(row["frame_count"]),
                    collection_name=str(row["collection_name"]),
                    collection_progress=(
                        f"0/{int(row['collection_total'])}"
                        if int(row["collection_total"])
                        else ""
                    ),
                    character_name=str(row["character_name"]),
                )
                if bool(row["is_animated"]):
                    base = await renderer.render_asset_preview_base(view)
                    rendered = await composer.compose(
                        base=base.image,
                        source_path=source_path,
                        slot=base.media_slot,
                    )
                    output = rendered_root / f"{row['template_id']}.gif"
                else:
                    rendered = await renderer.render_static_asset_preview(
                        view,
                        source_path,
                    )
                    output = rendered_root / f"{row['template_id']}.png"
                output.write_bytes(base64.b64decode(rendered.image_base64))
                rendered_paths[str(row["template_id"])] = output
                results.append(
                    {
                        "template_id": row["template_id"],
                        "output": str(output),
                        "mime_type": rendered.mime_type,
                        "width": rendered.width,
                        "height": rendered.height,
                        "bytes": rendered.byte_length,
                        "frame_count": rendered.frame_count,
                        "duration_ms": rendered.total_duration_ms,
                        "loop_count": rendered.loop_count,
                    }
                )
        finally:
            await capability.close()
            await browser.close()

    contacts = write_contact_sheets(rows, rendered_paths, output_root)
    animation_strips = write_animation_strips(rows, rendered_paths, output_root)
    report = {
        "asset_count": len(rows),
        "pig_count": sum(row["kind"] == "pig" for row in rows),
        "food_count": sum(row["kind"] == "food" for row in rows),
        "animated_count": sum(bool(row["is_animated"]) for row in rows),
        "group_asset_count": sum(row["scope_type"] == "group" for row in rows),
        "results": results,
        "contact_sheets": contacts,
        "animation_strips": animation_strips,
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    report = asyncio.run(accept(parse_args()))
    print(
        json.dumps(
            {
                "asset_count": report["asset_count"],
                "pig_count": report["pig_count"],
                "food_count": report["food_count"],
                "animated_count": report["animated_count"],
                "group_asset_count": report["group_asset_count"],
                "contact_sheet_count": len(report["contact_sheets"]),
                "animation_strip_count": len(report["animation_strips"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
