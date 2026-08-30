"""Offline visual QA for result cards and owned-cosmetic receipts.

All player data, probabilities, and effect counts are explicit layout fixtures.
Only catalog art is real. No game settlement, DB, network, or live bot is used.
The original local media are read-only and their hashes are verified afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.domain.dispatch_views import (  # noqa: E402
    DispatchLine,
    DispatchPanel,
    DispatchView,
)
from pig_catcher.rendering import AnimatedCardComposer, PigCatcherRenderer  # noqa: E402
from pig_catcher.rendering.cosmetics import COSMETIC_DEFINITIONS  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    animation_report,
    pig_card,
    render_options,
    write_contact_sheet,
    write_image,
)
from tools.accept_cooking_and_economy_views import food_card  # noqa: E402


def formal_row(entry: dict[str, Any], library: Path) -> tuple[dict[str, Any], Path]:
    """Decode actual media contents, never infer animation from the suffix."""
    path = (library / entry["image"]).resolve(strict=True)
    if not path.is_relative_to(library.resolve()):
        raise ValueError("Formal asset is outside the catalog")
    with Image.open(path) as media:
        animated = getattr(media, "n_frames", 1) > 1
        media_format = media.format
    collection = entry.get("collection") or {}
    row = {
        **entry,
        "image_fit": entry.get("fit", "contain"),
        "is_animated": animated,
        "media_format": media_format,
        "length_min": entry.get("length_min_cm", 20),
        "length_max": entry.get("length_max_cm", 100),
        "weight_min": entry.get("weight_min_kg", 15),
        "weight_max": entry.get("weight_max_kg", 200),
        "collection_name": collection.get("collection_name", ""),
        "character_name": collection.get("character_name", ""),
        "display_tags_json": json.dumps(entry.get("display_tags", []), ensure_ascii=False),
    }
    return row, path


def thumbnail_320(path: Path, target: Path) -> None:
    with Image.open(path) as image:
        image.seek(0)
        frame = image.convert("RGB")
    height = max(1, round(frame.height * 320 / frame.width))
    frame.resize((320, height), Image.Resampling.LANCZOS).save(target)


async def result_geometry(capability: PlaywrightRenderCapability) -> dict[str, Any]:
    assert capability.page is not None
    return await capability.page.locator("[data-pig-catcher-root]").evaluate(
        """root => {
          const rb = root.getBoundingClientRect();
          const box = selector => {
            const el = root.querySelector(selector);
            if (!el) return null;
            const b = el.getBoundingClientRect();
            return {x:b.left-rb.left, y:b.top-rb.top,
              width:b.width, height:b.height, right:b.right-rb.left, bottom:b.bottom-rb.top};
          };
          const styledBox = selector => {
            const el = root.querySelector(selector);
            if (!el) return null;
            const b = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            const left = parseFloat(style.borderLeftWidth) || 0;
            const top = parseFloat(style.borderTopWidth) || 0;
            const right = parseFloat(style.borderRightWidth) || 0;
            const bottom = parseFloat(style.borderBottomWidth) || 0;
            return {
              x:b.left-rb.left, y:b.top-rb.top, width:b.width, height:b.height,
              right:b.right-rb.left, bottom:b.bottom-rb.top,
              inner:{x:b.left-rb.left+left, y:b.top-rb.top+top,
                width:b.width-left-right, height:b.height-top-bottom},
              border:{left,top,right,bottom}, position:style.position,
              pointerEvents:style.pointerEvents, overflow:style.overflow
            };
          };
          const media = box('.pig-card__media');
          const header = box('.pig-card__header');
          const heading = box('.pig-card__header h1');
          const eyebrow = box('.pig-card__header .game-header__eyebrow');
          const detail = box('.pig-card__detail');
          const receipt = box('.pig-card__receipt');
          const footer = box('.game-footer');
          const facts = box('.pig-card__facts');
          const description = box('.pig-card__description');
          const cosmetics = box('.result-cosmetics');
          const cosmeticFrame = {
            layer:styledBox('.cosmetic-frame-layer'),
            edge:styledBox('.cosmetic-edge'),
            mediaEdge:styledBox('.cosmetic-media-edge'),
            headerRail:styledBox('.cosmetic-header-rail')
          };
          const overlaps=[];
          if (header && media && header.bottom > media.y+1) overlaps.push('header/media');
          if (heading && header && heading.bottom > header.bottom+1) overlaps.push('heading/header');
          if (eyebrow && heading && eyebrow.bottom > heading.y+1) overlaps.push('eyebrow/heading');
          if (detail && receipt && detail.bottom > receipt.y+1) overlaps.push('detail/receipt');
          if (media && receipt && media.bottom > receipt.y+1) overlaps.push('media/receipt');
          if (receipt && footer && receipt.bottom > footer.y+1) overlaps.push('receipt/footer');
          if (facts && description && facts.bottom > description.y+1) overlaps.push('facts/description');
          if (cosmetics && facts && cosmetics.bottom > facts.y+1) overlaps.push('cosmetics/facts');
          const excessiveGaps=[];
          if (detail && receipt && receipt.y-detail.bottom > 80) {
            excessiveGaps.push({after:'detail', before:'receipt', pixels:receipt.y-detail.bottom});
          }
          return {root:{width:rb.width,height:rb.height,clientWidth:root.clientWidth,clientHeight:root.clientHeight},
            media,header,heading,eyebrow,detail,receipt,footer,
            facts,description,cosmetics,overlaps,excessiveGaps,
            cosmeticFrame,
            probability:root.querySelector('[data-catch-probability], [data-food-probability]')
              ?.textContent.trim() || '',
            text:root.textContent,
            legacyFramePseudo:getComputedStyle(root,'::after').content,
            outline:getComputedStyle(root).outlineStyle,
            cosmeticImages:root.querySelectorAll('.cosmetic-plate img, .cosmetic-preview img').length};
        }"""
    )


def result_frame_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    """Validate that decorative frame geometry preserves the fixed animation slot."""
    root = geometry["root"]
    frame = geometry["cosmeticFrame"]
    layer = frame["layer"]
    media_edge = frame["mediaEdge"]
    header_rail = frame["headerRail"]

    def near(value: float, expected: float, tolerance: float = 1.0) -> bool:
        return abs(value - expected) <= tolerance

    layer_matches_root = bool(
        layer
        and near(layer["x"], 0)
        and near(layer["y"], 0)
        and near(layer["width"], root["clientWidth"])
        and near(layer["height"], root["clientHeight"])
    )
    media_inner = media_edge["inner"] if media_edge else None
    media_inner_matches_slot = bool(
        media_inner
        and all(
            near(media_inner[key], expected)
            for key, expected in {"x": 38, "y": 164, "width": 480, "height": 480}.items()
        )
    )
    media_outer_matches_design = bool(
        media_edge
        and all(
            near(media_edge[key], expected)
            for key, expected in {"x": 14, "y": 140, "width": 528, "height": 528}.items()
        )
    )
    header_rail_matches_design = bool(
        header_rail
        and near(header_rail["x"], 38)
        and near(header_rail["y"], 122)
        and near(header_rail["height"], 4)
        and near(root["width"] - header_rail["right"], 38)
    )
    checks = {
        "layer_present": layer is not None,
        "layer_absolute": bool(layer and layer["position"] == "absolute"),
        "layer_pointer_events_none": bool(layer and layer["pointerEvents"] == "none"),
        "layer_matches_root": layer_matches_root,
        "media_edge_present": media_edge is not None,
        "media_edge_absolute": bool(media_edge and media_edge["position"] == "absolute"),
        "media_edge_outer_matches_design": media_outer_matches_design,
        "media_edge_inner_matches_38_164_480_slot": media_inner_matches_slot,
        "header_rail_present": header_rail is not None,
        "header_rail_absolute": bool(header_rail and header_rail["position"] == "absolute"),
        "header_rail_matches_design": header_rail_matches_design,
    }
    return {"checks": checks, "passed": all(checks.values())}


async def accept(args: argparse.Namespace) -> dict[str, Any]:
    library = (PROJECT_ROOT / "asset_library/current").resolve(strict=True)
    entries = json.loads((library / "assets.json").read_text(encoding="utf-8"))["entries"]
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite previous evidence: {output}")
    output.mkdir(parents=True)
    thumbs = output / "thumbnails-320"
    thumbs.mkdir()

    def select(name: str) -> tuple[dict[str, Any], Path]:
        entry = next(item for item in entries if item["display_name"] == name)
        return formal_row(entry, library)

    pig_row, pig_path = select("五条猪")
    animated_row, animated_path = select("撅撅猪")
    food_row, food_path = select("糖醋排骨")
    kfc_row, kfc_path = select("炸猪全家桶")
    originals = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (pig_path, animated_path, food_path, kfc_path)
    }
    if not animated_row["is_animated"]:
        raise AssertionError("Expected the formal animated pig; do not substitute fabricated animation")

    longest_title = max(
        (key for key, value in COSMETIC_DEFINITIONS.items() if value["kind"] == "title"),
        key=lambda key: len(COSMETIC_DEFINITIONS[key]["name"]),
    )
    rain = {
        "achievement_title": "rain-love",
        "achievement_frame": "hollow-purple",
        "achievement_badge": "weekly-001-catch-value-rank-1",
    }
    long_cosmetics = {
        "achievement_title": longest_title,
        "achievement_frame": "all-giants-dynamic",
        "achievement_badge": "weekly-001-catch-value-rank-10",
    }
    frame_ids = sorted(key for key, value in COSMETIC_DEFINITIONS.items() if value["kind"] == "frame")
    if len(frame_ids) != 16:
        raise AssertionError(f"Expected 16 registered frames for this acceptance matrix, found {len(frame_ids)}")
    blank_cosmetics = {key: "" for key in rain}
    player = "离线视觉验收员 · 非实服数据"
    base_pig = replace(
        pig_card(pig_row, mode_label="抓猪成功"),
        owner_display_name=player,
        acquired_at="2026-08-28 12:00（离线样例）",
        daily_count=3,
        daily_limit=7,
        item_name="幸运猪哨",
        item_remaining_uses=3,
        probability_sources="离线概率排版样例：等级、饲料、道具与菜品汇总；六档合计 100%。",
        effect_summaries=(
            "排版样例：珍猪奶茶复制效果剩余 1 次，本次已经结算，不重复抽签。",
            "排版样例：今天额外额度剩余 4 次，正常额度与专属额度分开展示。",
        ),
        excluded_summaries=("排版样例：排队中的独立六星效果未叠加，剩余次数未消耗。",),
        **rain,
    )
    long_pig = replace(
        base_pig,
        owner_display_name="离线长昵称验收员·星光与夜雨的巡演道路·星光与夜雨的巡演道路",
        description=(pig_row["description"] + " 这是离线长文本压力样例，验证描述不会遮挡概率与剩余次数。") * 4
        + "【猪猪长描述结束】",
        display_tags=("咒术回战", "白发眼罩", "联动猪", "主题标签排版样例", "可读性优先"),
        **long_cosmetics,
    )
    animated_pig = replace(
        pig_card(animated_row, mode_label="抓猪成功"),
        owner_display_name=player,
        acquired_at="2026-08-28 12:00（离线样例）",
        daily_count=3,
        daily_limit=7,
        effect_summaries=("动画原图逐帧保留，卡片数字为离线排版样例；效果剩余 3 次。",),
        **rain,
    )
    base_food = replace(
        food_card(food_row, cooking=True),
        owner_display_name=player,
        acquired_at="2026-08-28 12:00（离线样例）",
        source_selector="ob一串猪#OFFLINE6",
        item_name="超级主厨香料",
        item_remaining_uses=6,
        effect_summary=(
            "离线排版样例：获得一次重置机会，使用后为已登记的本群玩家发放猪币与专属抓猪额度。"
            "每名玩家的专属次数单独结算；有效期、互斥效果与剩余次数均应完整展示。"
            "这是长菜品效果的视觉压力数据，不代表新的概率或线上结算规则。【菜效结束】"
        ),
        probability_line="1★ 0.000% · 2★ 0.000% · 3★ 0.000% · 4★ 0.000% · 5★ 80.000% · 6★ 20.000%",
        probability_sources="离线概率排版样例；六星原料使用；六档合计 100%；不是平衡模拟结果。",
        effect_summaries=(
            "多次菜品排版样例：做菜效果本次已消耗，剩余 2 次。",
            "保底返还排版样例：成功不消耗次数，剩余 3 次失败保护。",
            "队列排版样例：同名道具剩余 6 次，后续效果依实际使用顺序结算。",
        ),
        excluded_summaries=("独立六星效果生效时其他概率加成暂不参与，未消耗效果保留。",),
        bonus_selector="",
        **rain,
    )
    extreme_food = replace(
        base_food,
        description=(food_row["description"] + " 本段只用于检查长文本换行，所有实际概率均在下方单独展示。") * 4
        + "【美食长描述结束】",
        effect_summary=base_food.effect_summary * 3 + "【超长菜效最终结束】",
        effect_summaries=base_food.effect_summaries * 2,
        **long_cosmetics,
    )
    detail_food = replace(
        food_card(kfc_row, cooking=False),
        owner_display_name=player,
        source_selector="KFC猪#OFFLINE4",
        effect_summary="离线详情排版样例：所有已登记群友向使用者转移 50 猪币；具体人数与总额在使用回执中逐项列出。",
        **rain,
    )
    cosmetic_view = DispatchView(
        title="佩戴成功 · 离线陈列样例",
        player_name=player,
        presentation="cosmetics",
        subtitle="外观陈列室",
        banner="离线视觉样例：以下外观均设为已拥有，只用于验证实际佩戴展示。",
        stats=(
            DispatchLine("称号", "雨爱"),
            DispatchLine("周榜牌", "第 1 期 · 第 1 名"),
            DispatchLine("边框", "虚式尽头"),
        ),
        panels=(
            DispatchPanel(
                "本次佩戴", (DispatchLine("状态", "已切换（离线样例）"), DispatchLine("概率与收益", "不因外观改变"))
            ),
        ),
        hints=("通过 /猪猪成就 查看自己已经解锁的奖励。",),
        **rain,
    )
    frame_matrix_view = replace(
        base_pig,
        achievement_title="",
        achievement_frame="",
        achievement_badge="",
        effect_summaries=(),
        excluded_summaries=(),
    )
    cases = [
        ("01-pig-rain-weekly-frame", "pig", base_pig, pig_path),
        ("02-pig-long-title-description", "pig", long_pig, pig_path),
        ("03-pig-pure-missing-media", "pig", replace(base_pig, **blank_cosmetics), None),
        ("04-pig-original-animation", "animated-pig", animated_pig, animated_path),
        ("05-food-effects-probability", "food", base_food, food_path),
        ("06-food-extreme-text", "food", extreme_food, food_path),
        ("07-food-pure-missing-media", "food", replace(base_food, **blank_cosmetics), None),
        ("08-food-detail-with-cosmetics", "food", detail_food, kfc_path),
        ("09-cosmetic-receipt-rain", "cosmetics", cosmetic_view, None),
        (
            "10-cosmetic-receipt-long-title",
            "cosmetics",
            replace(
                cosmetic_view,
                **long_cosmetics,
                stats=(
                    DispatchLine("称号", COSMETIC_DEFINITIONS[longest_title]["name"]),
                    DispatchLine("周榜牌", "第 1 期 · 前十"),
                    DispatchLine("边框", "万猪之巅"),
                ),
            ),
            None,
        ),
        (
            "11-cosmetic-receipt-unwear",
            "cosmetics",
            replace(
                cosmetic_view, **blank_cosmetics, title="已卸下外观 · 离线样例", stats=(DispatchLine("当前佩戴", "无"),)
            ),
            None,
        ),
        (
            "12-cosmetic-receipt-frame-prefix",
            "cosmetics",
            replace(
                cosmetic_view,
                achievement_frame="frame-five-city-lights",
                achievement_badge="weekly-001-catch-value-rank-3",
                stats=(
                    DispatchLine("称号", "雨爱"),
                    DispatchLine("周榜牌", "第 1 期 · 第 3 名"),
                    DispatchLine("边框", "五城灯光（旧前缀兼容）"),
                ),
            ),
            None,
        ),
    ]
    frame_matrix: dict[str, str] = {"13-frame-matrix-baseline": ""}
    cases.append(("13-frame-matrix-baseline", "pig", frame_matrix_view, pig_path))
    for index, frame_id in enumerate(frame_ids, 1):
        label = f"14-frame-matrix-{index:02d}-{frame_id}"
        frame_matrix[label] = frame_id
        cases.append((label, "pig", replace(frame_matrix_view, achievement_frame=frame_id), pig_path))
    options = render_options()
    outputs: list[tuple[str, Path]] = []
    records: list[dict[str, Any]] = []
    animation = None
    async with async_playwright() as playwright:
        launch = {"headless": True}
        if args.browser_executable:
            launch["executable_path"] = str(args.browser_executable)
        browser = await playwright.chromium.launch(**launch)
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        renderer = PigCatcherRenderer(capability, options)
        composer = AnimatedCardComposer(max_output_bytes=options.max_animation_bytes)
        try:
            for label, kind, view, path in cases:
                capability.label = label
                if kind == "pig":
                    rendered = await renderer.render_static_pig_card(view, path)
                elif kind == "animated-pig":
                    base = await renderer.render_pig_card_base(view)
                    rendered = await composer.compose(base=base.image, source_path=path, slot=base.media_slot)
                elif kind == "food":
                    rendered = await renderer.render_static_food_card(view, path)
                else:
                    rendered = await renderer.render_dispatch(view, {})
                suffix = ".gif" if rendered.is_animated else ".png"
                destination = output / f"{label}{suffix}"
                write_image(destination, rendered)
                outputs.append((label, destination))
                thumbnail_320(destination, thumbs / f"{label}.png")
                geometry = await result_geometry(capability)
                text = geometry.pop("text")
                requirements: list[str] = []
                if kind in {"pig", "animated-pig", "food"}:
                    requirements.extend((view.display_name, view.short_code))
                    if view.probability_line:
                        requirements.extend(f"{star}★" for star in range(1, 7))
                    if view.item_name:
                        requirements.append(f"剩 {view.item_remaining_uses} 次")
                for sentinel in ("【猪猪长描述结束】", "【美食长描述结束】", "【超长菜效最终结束】"):
                    if hasattr(view, "description") and sentinel in (
                        view.description + getattr(view, "effect_summary", "")
                    ):
                        requirements.append(sentinel)
                missing = [needle for needle in requirements if needle not in text]
                media = geometry["media"]
                if media:
                    slot_aligned = all(
                        abs(media[key] - expected) <= 1
                        for key, expected in {"x": 38, "y": 164, "width": 480, "height": 480}.items()
                    )
                else:
                    slot_aligned = None
                diagnostic = capability.diagnostics[-1]
                is_frame_matrix = label in frame_matrix
                frame_geometry = (
                    result_frame_geometry(geometry) if is_frame_matrix and frame_matrix[label] else None
                )
                records.append(
                    {
                        "label": label,
                        "path": str(destination),
                        "kind": kind,
                        "width": rendered.width,
                        "height": rendered.height,
                        "bytes": rendered.byte_length,
                        "frame_count": rendered.frame_count,
                        "geometry": geometry,
                        "media_slot_aligned_within_css_border": slot_aligned,
                        "frame_matrix_id": frame_matrix.get(label) if is_frame_matrix else None,
                        "frame_geometry": frame_geometry,
                        "missing_text": missing,
                        "dom": diagnostic,
                    }
                )
                if kind == "animated-pig":
                    animation = animation_report(
                        path, destination, missing_duration_ms=options.missing_frame_duration_ms
                    )
                    with Image.open(destination) as media_image:
                        for index, name in ((0, "first"), (media_image.n_frames - 1, "last")):
                            media_image.seek(index)
                            media_image.convert("RGB").save(output / f"{label}-{name}-frame.png")
                print(
                    f"rendered {label}: {rendered.width}x{rendered.height} ({rendered.frame_count} frames)", flush=True
                )
        finally:
            await capability.close()
            await browser.close()
    baseline = next(record for record in records if record["label"] == "13-frame-matrix-baseline")
    baseline_root = baseline["geometry"]["root"]
    baseline_frame = baseline["geometry"]["cosmeticFrame"]
    baseline["frame_baseline_is_unframed"] = not any(baseline_frame.values())
    for record in records:
        if record["label"] not in frame_matrix or not frame_matrix[record["label"]]:
            continue
        root = record["geometry"]["root"]
        record["root_size_matches_frame_baseline"] = all(
            abs(root[key] - baseline_root[key]) <= 1 for key in ("width", "height", "clientWidth", "clientHeight")
        )
    preserved = all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in originals.items())
    contacts = write_contact_sheet(outputs, output)
    issues = [
        record["label"]
        for record in records
        if record["missing_text"]
        or record["geometry"]["overlaps"]
        or record["geometry"]["excessiveGaps"]
        or record["geometry"]["legacyFramePseudo"] not in {"none", "normal"}
        or record["geometry"]["outline"] != "none"
        or record["media_slot_aligned_within_css_border"] is False
        or (record["frame_geometry"] is not None and not record["frame_geometry"]["passed"])
        or record.get("root_size_matches_frame_baseline") is False
        or record.get("frame_baseline_is_unframed") is False
        or any(record["dom"][key] for key in ("clippedText", "outside", "brokenImages"))
    ]
    report = {
        "scope": "Isolated phase-3 visual DTO fixtures; no business acceptance, no live data, no settlement.",
        "longest_registered_title": longest_title,
        "count": len(records),
        "frame_matrix": {
            "count": len(frame_ids),
            "ids": frame_ids,
            "baseline_root": baseline_root,
            "all_geometry_passed": all(
                record["frame_geometry"] and record["frame_geometry"]["passed"]
                for record in records
                if record["label"] in frame_matrix and frame_matrix[record["label"]]
            ),
            "all_root_sizes_unchanged": all(
                record.get("root_size_matches_frame_baseline") is True
                for record in records
                if record["label"] in frame_matrix and frame_matrix[record["label"]]
            ),
        },
        "contact_sheet": str(contacts),
        "records": records,
        "thumbnails": {"width": 320, "directory": str(thumbs)},
        "animation": animation,
        "original_hashes_unchanged": preserved,
        "issues": issues,
        "passed": not issues and preserved and bool(animation and animation["preserved"]),
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("count", "passed", "issues")}), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    args = parser.parse_args()
    report = asyncio.run(accept(args))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
