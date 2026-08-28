"""真实素材、真实吃菜结算的离线奖励图验收；只写新的 artifacts 子目录。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops, ImageStat
from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.config.model import CookingSection, EconomySection  # noqa: E402
from pig_catcher.domain.economy import generate_food_attributes  # noqa: E402
from pig_catcher.domain.food_lottery import HINA_PIG_TEMPLATE_ID, choose_lottery_prize  # noqa: E402
from pig_catcher.domain.food_supplies import FOOD_SUPPLY_PACKS  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.repositories.economy import EconomyRepository  # noqa: E402
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, eat_receipt_view, food_card_view, media_path  # noqa: E402
from pig_catcher.rendering.food_rewards import FoodRewardView, food_reward_view  # noqa: E402
from pig_catcher.services import AssetCatalogService, EconomyService  # noqa: E402
from pig_catcher.version import RULESET_VERSION  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402

FIXTURE_SCOPE = "qq-official:9EA2810F378FBD7DC3219C56CEAB3520"
FIXTURE_NOW = "2026-08-28T03:00:00.000Z"
BRANCH_ROLLS = (0.1, 0.5, 0.85, 0.95, 0.999)
GIF_DURATIONS = [120, 120, 140, 140, 160, 4500]
GIF_BUDGET = 8 * 1024 * 1024
ART_DIRECTORY = PROJECT_ROOT / "pig_catcher/rendering/assets/947"


class FixtureClock:
    def now(self) -> datetime:
        return datetime.fromisoformat(FIXTURE_NOW.replace("Z", "+00:00"))


class FixtureRandom:
    def __init__(self, values=()):
        self.values = list(values)

    def random(self):
        if not self.values:
            raise AssertionError("验收随机序列耗尽，不能悄悄使用真实随机数。")
        return self.values.pop(0)


@dataclass(frozen=True)
class Scenario:
    name: str
    view: object
    kind: str = "rewards"
    source_image: str = ""
    missing_media: bool = False


def file_hashes(paths) -> dict[str, str]:
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def inspect_animation(rendered, static_payload: bytes) -> dict:
    """Verify the emitted bytes, including the final palette-quantized receipt."""
    raw = base64.b64decode(rendered.image_base64, validate=True)
    with Image.open(BytesIO(raw)) as decoded:
        durations = []
        for index in range(decoded.n_frames):
            decoded.seek(index)
            durations.append(int(decoded.info.get("duration") or 0))
        with Image.open(BytesIO(static_payload)) as original:
            final = decoded.convert("RGB")
            base = original.convert("RGB")
            delta = ImageChops.difference(final, base)
            mean_error = sum(ImageStat.Stat(delta).mean) / 3
    return {
        "format": rendered.mime_type,
        "bytes": len(raw),
        "dimensions": [rendered.width, rendered.height],
        "frames": rendered.frame_count,
        "durations_ms": durations,
        "loop": rendered.loop_count,
        "final_mean_pixel_error": round(mean_error, 4),
    }


class RewardRenderCapability(PlaywrightRenderCapability):
    def __init__(self, browser):
        super().__init__(browser)
        self.static_payloads = {}
        self.html = {}
        self.fonts = {}

    async def html2png(self, html: str, **kwargs):
        result = await super().html2png(html, **kwargs)
        self.html[self.label] = html
        self.static_payloads[self.label] = base64.b64decode(result["image_base64"])
        self.fonts[self.label] = await self.page.evaluate(
            """() => ({
              ready: document.fonts.status,
              body: getComputedStyle(document.body).fontFamily,
              title: getComputedStyle(document.querySelector('h1')).fontFamily,
              yahei: document.fonts.check('18px "Microsoft YaHei"', '猪猪奖励')
            })"""
        )
        return result


async def scenarios(root: Path):
    """Use only checked-in artwork and a new disposable, self-contained database."""
    package = PROJECT_ROOT / "asset_library/current"
    manifest = json.loads((package / "assets.json").read_text(encoding="utf-8"))
    entries = manifest["entries"]
    selected = [
        entry
        for entry in entries
        if (entry["kind"] == "food" and entry["rarity"] == 5)
        or (entry["kind"] == "food" and entry.get("effect_id") == "food-supply-pack")
        or entry.get("group_scope_id") == FIXTURE_SCOPE
        or entry["template_id"] == HINA_PIG_TEMPLATE_ID
    ]
    relative_paths = {str(entry[key]) for entry in selected for key in ("image", "alternate_image") if entry.get(key)}
    paths = {package / relative for relative in relative_paths}
    paths.update(ART_DIRECTORY / f"{name}.png" for name in ("pure-947", "original-947"))
    source_hashes = file_hashes(paths)
    source = root / "inputs"
    for relative in relative_paths:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / relative, target)
    manifest["entries"] = selected
    (source / "assets.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    data = root / "isolated-data"
    db = PigCatcherDatabase(data / "food-reward-views.sqlite3")
    await db.open()
    try:
        await AssetCatalogService(
            db, AssetCatalogStorage(data), min_image_side=32, max_image_bytes=32 * 1024 * 1024
        ).import_manifest(source / "assets.json")
        actor = CommandIdentity(
            ScopeKey.parse(FIXTURE_SCOPE),
            "fixture-stream",
            "fixture-player",
            "名字很长也要看清的绿芯幸运试吃员",
            "fixture",
            "离线奖励验收群",
        )
        service = EconomyService(db, CookingSection(), EconomySection(), clock=FixtureClock())
        by_name = {entry["display_name"]: entry for entry in selected if entry["kind"] == "food"}

        async def seed_food(name: str):
            template_id = by_name[name]["template_id"]
            food_id, code = uuid4().hex, uuid4().hex[:8].upper()
            async with db.transaction() as session:
                await FrameworkRepository().touch_identity(session, identity=actor, now=FIXTURE_NOW)
                template = await session.fetch_one("SELECT * FROM food_templates WHERE template_id=?", (template_id,))
                attributes = generate_food_attributes(
                    rarity=int(template["rarity"]),
                    template_id=template_id,
                    source_weight=60,
                    source_weight_percentile=0.5,
                    portion_roll=0.5,
                )
                await EconomyRepository().insert_food_instance(
                    session,
                    values={
                        "food_instance_id": food_id,
                        "short_code": code,
                        "scope_id": actor.scope.value,
                        "owner_player_id": actor.player_id,
                        "template_id": template_id,
                        "template_version": int(template["template_version"]),
                        "source_pig_instance_id": None,
                        "rarity": int(template["rarity"]),
                        "display_name_snapshot": name,
                        "portion_weight": attributes.portion_weight,
                        "fat_category": "balanced",
                        "official_value": attributes.official_value,
                        "effect_id": str(template["effect_id"]),
                        "effect_params_json": str(template["effect_params_json"]),
                        "ruleset_version": RULESET_VERSION,
                        "random_snapshot_json": '{"source":"visual-fixture"}',
                        "acquired_at": FIXTURE_NOW,
                        "updated_at": FIXTURE_NOW,
                    },
                )
            return food_id, f"{name}#{code}"

        async def eat(name: str, random=()):
            _, selector = await seed_food(name)
            current = replace(actor, message_id=uuid4().hex)
            service.random_source = FixtureRandom(random)
            result = await service.eat(current, selector)
            if service.random_source.values:
                raise AssertionError("结算未按预期耗尽随机序列。")
            return result, current, selector

        cases = []
        all_results = []
        for roll in BRANCH_ROLLS:
            prize = choose_lottery_prize(roll)
            values = (
                [0.5] * 5
                if prize.kind == "pig"
                else [number for i in range(prize.quantity) for number in ((i + 0.5) / prize.quantity, 0.5)]
            )
            result, current, selector = await eat("熠～噜猪绿芯小猪派", [roll, *values])
            all_results.append((result, current, selector))
            visible = await service.visible_eat_result(current, result)
            cases.append(Scenario(f"01-lottery-{prize.prize_id}", food_reward_view(visible)))
        for pack in FOOD_SUPPLY_PACKS.values():
            result, _, _ = await eat(pack.food_name)
            cases.append(Scenario(f"02-supply-{pack.pack_id}", food_reward_view(result)))
        for name, label in (("撅撅猪派", "juejue"), ("达妮娅泡泡云冻", "daniya")):
            for _ in range(6):
                result, _, _ = await eat(name)
            cases.append(Scenario(f"03-overflow-{label}", food_reward_view(result)))
        mist, _, _ = await eat("雾蓝键盘大福")
        cases.extend(
            (
                Scenario("04-mist-eat-receipt", eat_receipt_view(mist), "receipt"),
                Scenario(
                    "05-mist-food-detail",
                    food_card_view(mist.food, mode_label="美食详情"),
                    "food",
                    mist.food.image_relpath,
                ),
            )
        )
        result, current, selector = all_results[-1]
        replay = await service.eat(current, selector)
        if replay.receipt_created or replay.reward_payload != result.reward_payload:
            raise AssertionError("同消息回放必须复用冻结抽奖结果。")
        cases.append(Scenario("06-replayed-jackpot", food_reward_view(replay)))
        cases.append(Scenario("07-missing-media", replace(cases[0].view, animation=""), missing_media=True))
        base = cases[0].view
        cases.append(
            Scenario(
                "08-long-escaped-text",
                replace(
                    base,
                    player_name="<script>仅验证转义</script>" + "很长的群昵称" * 8,
                    food_name="熠～噜猪绿芯小猪派" * 4,
                    items=tuple(replace(item, name=item.name + "超长奖励名称" * 6) for item in base.items),
                ),
            )
        )
        async with db.transaction() as session:
            await session.execute("UPDATE scope_food_templates SET authorized=0 WHERE scope_id=?", (actor.scope.value,))
        hidden = await service.visible_eat_result(current, result)
        cases.append(Scenario("09-revoked-reward-art", food_reward_view(hidden)))
        return cases, data, source_hashes
    finally:
        await db.close()


async def run(args) -> dict:
    output = args.output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / "artifacts").resolve()):
        raise ValueError("验收只能写入开发仓库的 artifacts 目录。")
    if output.exists():
        raise FileExistsError("请使用新的输出目录，不覆盖已验收结果。")
    output.mkdir(parents=True)
    cases, data, original_hashes = await scenarios(output)
    if args.case:
        cases = [case for case in cases if case.name in args.case]
        if not cases:
            raise ValueError("未找到指定验收场景。")
    database_path = data / "food-reward-views.sqlite3"
    database_hash = file_hashes([database_path])
    outputs, previews, animation_reports = [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = RewardRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, replace(render_options(), max_animation_bytes=GIF_BUDGET))
            for case in cases:
                capability.label = case.name
                if case.kind == "receipt":
                    rendered = await renderer.render_economy_receipt(case.view)
                elif case.kind == "food":
                    rendered = await renderer.render_static_food_card(case.view, media_path(data, case.source_image))
                else:
                    paths = {
                        item.key: media_path(data, item.image_relpath) for item in case.view.items if item.image_relpath
                    }
                    rendered = await renderer.render_food_rewards(case.view, {} if case.missing_media else paths)
                suffix = ".gif" if rendered.is_animated else ".png"
                destination = output / f"{case.name}{suffix}"
                write_image(destination, rendered)
                outputs.append(destination)
                (output / f"{case.name}.html").write_text(capability.html[case.name], encoding="utf-8")
                if isinstance(case.view, FoodRewardView):
                    (output / f"{case.name}.txt").write_text(
                        case.view.summary
                        + "\n"
                        + "\n".join(
                            f"{item.name} #{item.short_code} ×{item.quantity}: {item.detail}"
                            for item in case.view.items
                        ),
                        encoding="utf-8",
                    )
                with Image.open(destination) as image:
                    if rendered.is_animated:
                        for index in (0, 2, 5):
                            image.seek(index)
                            image.convert("RGB").save(output / f"{case.name}-frame-{index}.png")
                        report = inspect_animation(rendered, capability.static_payloads[case.name])
                        report["case"] = case.name
                        animation_reports.append(report)
                        if (
                            report["durations_ms"] != GIF_DURATIONS
                            or report["frames"] != 6
                            or report["loop"] is not None
                        ):
                            raise AssertionError("揭晓GIF帧数、时长或循环不符。")
                        if report["bytes"] > GIF_BUDGET or report["final_mean_pixel_error"] > 8:
                            raise AssertionError("揭晓GIF超出预算或最终帧丢失结算内容。")
                    image.seek(image.n_frames - 1)
                    final = image.convert("RGB")
                    final_path = output / f"{case.name}-final.png"
                    final.save(final_path)
                    previews.append(final_path)
                    final.resize((320, round(final.height * 320 / final.width)), Image.Resampling.LANCZOS).save(
                        output / f"{case.name}-320.png"
                    )
                if isinstance(case.view, FoodRewardView) and case.view.animation and not rendered.is_animated:
                    raise AssertionError(f"947分支未产出轻量GIF：{case.name}")
        finally:
            await capability.close()
            await browser.close()
    failures = [row for row in capability.diagnostics if row["clippedText"] or row["outside"] or row["brokenImages"]]
    unchanged = original_hashes == file_hashes(map(Path, original_hashes))
    database_unchanged = database_hash == file_hashes([database_path])
    if not unchanged or not database_unchanged:
        raise AssertionError("渲染修改了源插画或结算数据库。")
    font_failures = {
        key: value for key, value in capability.fonts.items() if not value["yahei"] or value["ready"] != "loaded"
    }
    escaped = capability.html.get("08-long-escaped-text")
    if escaped is not None and ("<script>仅验证转义</script>" in escaped or "&lt;script&gt;" not in escaped):
        raise AssertionError("奖励图未正确HTML转义。")
    hidden_html = capability.html.get("09-revoked-reward-art")
    if hidden_html is not None and "素材授权已撤回" not in hidden_html:
        raise AssertionError("撤权奖励缺少明确占位说明。")
    report = {
        "status": "failed" if failures or font_failures else "passed",
        "outputs": [str(path) for path in outputs],
        "diagnostics": capability.diagnostics,
        "animations": animation_reports,
        "font_checks": capability.fonts,
        "failures": failures,
        "font_failures": font_failures,
        "source_sha256": original_hashes,
        "source_bytes_unchanged": unchanged,
        "rendering_database_unchanged": database_unchanged,
        "animation_kind": (
            "six-frame code-generated receipt reveal; supplied illustrations unchanged; not an original game animation"
        ),
        "scope": "new isolated database and source-art copies only; no production database or QQ connection",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_sheet(previews, output / "contact-sheet.jpg")
    if failures or font_failures:
        raise RuntimeError(json.dumps({"layout": failures, "fonts": font_failures}, ensure_ascii=False, indent=2))
    return {
        "status": "passed",
        "count": len(outputs),
        "report": str(output / "report.json"),
        "contact_sheet": str(output / "contact-sheet.jpg"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", help="只渲染指定场景；不传则完整验收。")
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
