"""Render complete rarity-grouped catalogs for one authorized group."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from PIL import Image, ImageStat
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.domain.food_effects import effect_summary  # noqa: E402
from pig_catcher.rendering import (  # noqa: E402
    CatalogItemViewModel,
    CatalogViewModel,
    CollectionProgressViewModel,
    FoodCatalogItemViewModel,
    FoodCatalogViewModel,
    PigCatcherRenderer,
)
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--browser-executable", type=Path)
    return parser.parse_args()


def _load_rows(
    database_path: Path,
    *,
    scope_id: str,
    kind: str,
) -> list[dict[str, object]]:
    if kind == "pig":
        table = "pig_templates"
        scope_table = "scope_pig_templates"
        fields = """
            template.template_id, template.display_name, template.rarity,
            template.image_relpath, template.image_fit, template.is_animated,
            template.scope_type, template.collection_name,
            template.collection_total, template.character_name
        """
    elif kind == "food":
        table = "food_templates"
        scope_table = "scope_food_templates"
        fields = """
            template.template_id, template.display_name, template.rarity,
            template.image_relpath, template.image_fit, template.is_animated,
            template.scope_type, template.effect_id, template.effect_params_json
        """
    else:
        raise ValueError(f"Unsupported catalog kind: {kind}")

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT {fields}
            FROM {table} AS template
            LEFT JOIN {scope_table} AS allowed
              ON allowed.template_id = template.template_id
             AND allowed.scope_id = ?
            WHERE template.enabled = 1
              AND (
                  template.scope_type = 'common'
                  OR (
                      template.scope_type = 'group'
                      AND allowed.authorized = 1
                      AND allowed.consent_status = 'granted'
                  )
              )
            ORDER BY template.rarity, template.display_name, template.template_id
            """,
            (scope_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _collection_views(
    rows: Sequence[Mapping[str, object]],
) -> tuple[CollectionProgressViewModel, ...]:
    counts: dict[str, tuple[int, int]] = {}
    for row in rows:
        name = str(row.get("collection_name") or "")
        if not name:
            continue
        current, total = counts.get(name, (0, int(row["collection_total"])))
        counts[name] = (current + 1, total)
    return tuple(
        CollectionProgressViewModel(
            collection_name=name,
            collaboration_name="BanG Dream!",
            collected_count=available,
            available_count=available,
            total_count=total,
        )
        for name, (available, total) in counts.items()
    )


def _pig_view(rows: Sequence[Mapping[str, object]]) -> CatalogViewModel:
    items = tuple(
        CatalogItemViewModel(
            key=str(row["template_id"]),
            display_name=str(row["display_name"]),
            rarity=int(row["rarity"]),
            discovered=str(row["scope_type"]) == "common",
            acquired_count=index + 1,
            best_size=38.0 + index * 0.7,
            best_weight=45.0 + index * 1.3,
            collection_name=str(row.get("collection_name") or ""),
            character_name=str(row.get("character_name") or ""),
            media_visible=str(row["scope_type"]) == "common",
            is_animated=bool(row["is_animated"]),
            image_fit=str(row["image_fit"]),
        )
        for index, row in enumerate(rows)
    )
    return CatalogViewModel(
        display_name="白名单群完整图鉴验收",
        total_count=len(items),
        rarity=None,
        undiscovered_only=False,
        collected_count=sum(item.discovered for item in items),
        visible_catalog_total=len(items),
        items=items,
        collections=_collection_views(rows),
    )


def _food_view(rows: Sequence[Mapping[str, object]]) -> FoodCatalogViewModel:
    items = tuple(
        FoodCatalogItemViewModel(
            key=str(row["template_id"]),
            display_name=str(row["display_name"]),
            rarity=int(row["rarity"]),
            discovered=str(row["scope_type"]) == "common",
            acquired_count=index + 1,
            best_portion_weight=2.5 + index * 0.9,
            media_visible=str(row["scope_type"]) == "common",
            is_animated=bool(row["is_animated"]),
            image_fit=str(row["image_fit"]),
            effect_summary=(
                effect_summary(
                    str(row.get("effect_id") or ""),
                    json.loads(str(row.get("effect_params_json") or "{}")),
                )
                if str(row["scope_type"]) == "common"
                and str(row.get("effect_id") or "")
                else ""
            ),
        )
        for index, row in enumerate(rows)
    )
    return FoodCatalogViewModel(
        display_name="白名单群完整美食图鉴验收",
        total_count=len(items),
        rarity=None,
        undiscovered_only=False,
        collected_count=sum(item.discovered for item in items),
        visible_catalog_total=len(items),
        items=items,
    )


def _media_paths(
    data_dir: Path,
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Path]:
    return {
        str(row["template_id"]): data_dir / str(row["image_relpath"])
        for row in rows
        if str(row["scope_type"]) == "common" and not bool(row["is_animated"])
    }


def _image_diagnostics(path: Path) -> dict[str, object]:
    with Image.open(path) as image:
        image.load()
        extrema = ImageStat.Stat(image.convert("RGB")).extrema
        return {
            "width": image.width,
            "height": image.height,
            "bytes": path.stat().st_size,
            "nonblank": any(low != high for low, high in extrema),
        }


async def accept(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.resolve(strict=True)
    output_root = args.output.resolve()
    if output_root.exists():
        raise FileExistsError(f"Output already exists: {output_root}")
    output_root.mkdir(parents=True)
    database_path = data_dir / args.database_filename
    pig_rows = _load_rows(database_path, scope_id=args.scope_id, kind="pig")
    food_rows = _load_rows(database_path, scope_id=args.scope_id, kind="food")
    if not pig_rows or not food_rows:
        raise RuntimeError("No visible catalog entries were found")

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
        try:
            capability.label = "pig-catalog-complete"
            pig_render = await renderer.render_catalog(
                _pig_view(pig_rows),
                _media_paths(data_dir, pig_rows),
            )
            pig_path = output_root / "pig-catalog-complete.png"
            write_image(pig_path, pig_render)

            capability.label = "food-catalog-complete"
            food_render = await renderer.render_food_catalog(
                _food_view(food_rows),
                _media_paths(data_dir, food_rows),
            )
            food_path = output_root / "food-catalog-complete.png"
            write_image(food_path, food_render)
        finally:
            await capability.close()
            await browser.close()

    diagnostics = capability.diagnostics
    for item in diagnostics:
        if item["clippedText"] or item["outside"] or item["brokenImages"]:
            raise RuntimeError(f"Catalog DOM diagnostics failed: {item}")
    image_results = {
        "pig": _image_diagnostics(pig_path),
        "food": _image_diagnostics(food_path),
    }
    if not all(result["nonblank"] for result in image_results.values()):
        raise RuntimeError("A rendered catalog image is blank")

    report = {
        "scope_id": args.scope_id,
        "pig_count": len(pig_rows),
        "food_count": len(food_rows),
        "pig_rarities": dict(sorted(Counter(int(row["rarity"]) for row in pig_rows).items())),
        "food_rarities": dict(sorted(Counter(int(row["rarity"]) for row in food_rows).items())),
        "private_pig_slots": sum(row["scope_type"] == "group" for row in pig_rows),
        "private_food_slots": sum(row["scope_type"] == "group" for row in food_rows),
        "images": image_results,
        "dom": diagnostics,
    }
    (output_root / "acceptance-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    report = asyncio.run(accept(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
