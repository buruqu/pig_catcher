"""Fourth-round Chromium acceptance using synthetic players and public local art.

No live database, browser profile, network, QQ credentials or sending capability.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.commands.tour import TourRequest  # noqa: E402
from pig_catcher.domain.achievements import ACHIEVEMENT_DEFINITIONS, AchievementUnlock  # noqa: E402
from pig_catcher.domain.activity_achievements import ACTIVITY_IDS, ACTIVITY_REWARDS  # noqa: E402
from pig_catcher.domain.battle_catalog import FIGHTERS  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, media_path  # noqa: E402
from pig_catcher.rendering.adapters import (  # noqa: E402
    achievement_overview_view,
    achievement_page_view,
    achievement_unlock_view,
)
from pig_catcher.rendering.cosmetics import cosmetic_detail  # noqa: E402
from pig_catcher.rendering.models import FoodCardViewModel  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402
from pig_catcher.services.achievement_rewards import AchievementRewardService  # noqa: E402
from pig_catcher.services.achievements import AchievementService  # noqa: E402
from pig_catcher.services.battle import BattleService  # noqa: E402
from pig_catcher.services.dispatch import DispatchService  # noqa: E402
from pig_catcher.services.tour import TourService  # noqa: E402
from tests.test_battle import BattleWorld  # noqa: E402
from tests.test_dispatch import NOW, SAFE_SEED, World, seed_pigs  # noqa: E402
from tests.test_gameplay import MutableClock  # noqa: E402
from tests.test_tour import TourWorld, character  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    pig_card,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


async def scenarios(output: Path):
    inputs = output / "inputs"
    inputs.mkdir()
    source = PROJECT_ROOT / "asset_library/current"
    catalog = json.loads((source / "assets.json").read_text(encoding="utf-8"))
    ids = {character(c).template_id for c in ("kasumi", "tomoe", "layer")}
    ids.update(f.template_id for f in FIGHTERS)
    ids.update(("pig-r2-tiny", "pig-r2-elephant"))
    entries = [e for e in catalog["entries"] if e["kind"] == "pig" and e["template_id"] in ids]
    if {e["template_id"] for e in entries} != ids:
        raise ValueError("Missing confirmed public artwork")
    food_entry = next(
        e for e in catalog["entries"] if e["kind"] == "food" and e["scope"] == "common" and e["rarity"] == 5
    )
    entries.append(food_entry)
    for entry in entries:
        destination = inputs / entry["image"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / entry["image"], destination)
    catalog["entries"] = entries
    manifest = inputs / "assets.json"
    manifest.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    data_root = output / "isolated-fixture"
    db = PigCatcherDatabase(data_root / "acceptance.sqlite3")
    await db.open()
    cases = []
    clock = MutableClock(NOW)
    actor = CommandIdentity(
        ScopeKey("qq-official", "fixture-only"),
        "fixture-stream",
        "traveller",
        "把旅途写成歌的群友",
        "fixture",
        "离线验收群",
    )
    achievements = AchievementService(db, clock=clock)
    rewards = AchievementRewardService(achievements)

    async def grant(identity, reward_id, count=2):
        async with db.transaction() as session:
            await AchievementRepository().grant_reward(
                session,
                player_id=identity.player_id,
                reward_type=ACTIVITY_REWARDS[reward_id]["kind"],
                reward_id=reward_id,
                quantity=count,
                now=clock.now().isoformat(),
            )

    async def reward(identity, text, label=""):
        result = await rewards.execute(replace(identity, message_id=uuid4().hex), text)
        if label:
            cases.append(("dispatch", label, result.view))
        return result

    try:
        await AssetCatalogService(
            db, AssetCatalogStorage(data_root), min_image_side=32, max_image_bytes=32 * 1024 * 1024
        ).import_manifest(manifest)
        cases.append(
            ("achievement_overview", "01-empty-overview", achievement_overview_view(await achievements.overview(actor)))
        )
        for index, category in enumerate(("远行手账", "巡演纪念", "比划档案", "三栖生活")):
            page = await achievements.page(actor, category=category, page=2 if index < 3 else 1)
            cases.append(("achievement_page", f"02-{index}-hidden-mask", achievement_page_view(page)))

        dispatch = World(db, clock, DispatchService(db, clock=clock, seed_factory=lambda: SAFE_SEED), actor)
        for template in ("pig-r2-tiny", "pig-r2-elephant"):
            await seed_pigs(db, actor, template_id=template, count=3)
        await dispatch.team(names="特小猪、特小猪、大象")
        for rid in ("materials-choice", "training-choice", "dispatch-luggage", "dispatch-story"):
            await grant(actor, rid, 12)
        await reward(actor, "材料 基础材料自选份 训练矿石 10", "03-material-preview")
        await reward(actor, "确认", "04-material-completed")
        await reward(actor, "使用 口袋行李券")
        await reward(actor, "使用 " + ACTIVITY_REWARDS["dispatch-story"]["name"])
        cases.append(("dispatch", "05-coupon-preview", (await dispatch.send("出发 1 青草近郊 4小时")).view))
        cases.append(("dispatch", "06-coupon-departed", (await dispatch.send("确认")).view))
        await dispatch.advance(4)
        cases.append(("dispatch", "07-story-return", (await dispatch.send("返程")).view))
        unlocked = await achievements.process_activity_facts(scope_id=actor.scope.value, receipt_id="visual-trip")
        cases.append(
            ("achievement_unlock", "08-real-dispatch-unlocks", achievement_unlock_view(actor.display_name, unlocked))
        )

        owner = replace(actor, user_id="tour-owner", display_name="把每种声部都记住的群友")
        tour = TourWorld(db, clock, TourService(db, clock=clock, seed_factory=lambda: "r4-tour-visual"), owner)
        await tour.form()
        for rid in ("tour-encore-photo", "tour-steady-stage", "tour-date"):
            await grant(owner, rid)
            await reward(owner, "使用 " + ACTIVITY_REWARDS[rid]["name"])
        await reward(owner, "查看", "09-tour-coupon-bag")
        cases.append(("tour", "10-tour-rehearsal", (await tour.send("排练")).view))
        cases.append(("tour", "11-tour-confirmation", (await tour.send("出发")).view))
        cases.append(("tour", "12-tour-start", (await tour.send("确认")).view))
        cases.append(("tour", "13-steady-stage", (await tour.send("继续")).view))
        cases.append(("tour", "14-encore-photo", (await tour.send("一键")).view))
        partner = replace(owner, user_id="tour-partner", display_name="另一位自由混团的群友")
        await tour.form(partner)
        for person in (owner, partner):
            await grant(person, "tour-encore-photo")
            await reward(person, "使用 " + ACTIVITY_REWARDS["tour-encore-photo"]["name"])
        await tour.service.execute(
            replace(owner, message_id="joint-visual"), TourRequest("joint_invite", {"target_user_id": partner.user_id})
        )
        joint = await tour.send("接受", "joint", identity=partner)
        assert sum("安可之后" in p.title for p in joint.view.panels) == 2
        cases.append(("tour", "15-two-encore-photos", joint.view))

        a = replace(actor, user_id="battle-a", display_name="训练手账翻到第五页")
        b = replace(actor, user_id="battle-b", display_name="今天也要认真比划的群友")
        battle = BattleWorld(db, clock, BattleService(db, clock=clock, seed_factory=lambda: "r4-battle-visual"), a, b)
        for person, fighter in zip((a, b), FIGHTERS, strict=True):
            await seed_pigs(db, person, template_id=fighter.template_id, count=1)
            await battle.assign(person, fighter.name)
            await battle.fund(person)
            await grant(person, "battle-banner")
            await reward(person, "使用 " + ACTIVITY_REWARDS["battle-banner"]["name"])
        await grant(a, "training-rebate")
        await reward(a, "使用 " + ACTIVITY_REWARDS["training-rebate"]["name"])
        cases.append(("battle", "16-training-discount-preview", (await battle.send("强化")).view))
        cases.append(("battle", "17-training-discount-receipt", (await battle.send("确认")).view))
        cases.append(("battle", "18-two-banner-invitation", (await battle.invite()).view))
        cases.append(("battle", "19-two-banner-entry", (await battle.send("接受", "challenge", actor=b)).view))
        await battle.fight(already_started=True)
        cases.append(("battle", "20-natural-finale", (await battle.send(section="status")).view))
        await achievements.process_activity_facts(scope_id=a.scope.value, receipt_id="visual-battle")

        # All original cosmetic variants and all 48 reward cards, without
        # pretending the fixture actually earned their gameplay conditions.
        definitions = [d for d in ACHIEVEMENT_DEFINITIONS if d.achievement_id in ACTIVITY_IDS]
        for index in range(0, len(definitions), 8):
            unlocks = [
                AchievementUnlock(d.achievement_id, d.name, d.tier, d.points, d.rewards, clock.now().isoformat())
                for d in definitions[index : index + 8]
            ]
            cases.append(
                (
                    "achievement_unlock",
                    f"21-all-rewards-{index // 8 + 1}",
                    achievement_unlock_view("原创奖励册 · 离线样张", unlocks),
                )
            )
        base = cases[8][2] if cases[8][0] == "dispatch" else (await dispatch.send()).view
        for index, rid in enumerate(key for key, item in ACTIVITY_REWARDS.items() if item["kind"] == "frame"):
            cases.append(
                (
                    "dispatch",
                    f"22-frame-{index + 1}",
                    replace(
                        base,
                        achievement_frame=rid,
                        achievement_title="把日子过成三本书",
                        achievement_badge="三次贷款契约",
                    ),
                )
            )
        overview = achievement_overview_view(await achievements.overview(actor))
        cases.append(
            (
                "achievement_overview",
                "23-overview-long-name",
                replace(
                    overview,
                    display_name="ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 3,
                    frame_text="frame-three-books",
                    title_text="三个世界，都是生活",
                    showcase_text="今日份的远行与明日的舞台",
                ),
            )
        )
        for rid, definition in ACTIVITY_REWARDS.items():
            if definition["kind"] in {"title", "frame", "badge"}:
                await grant(actor, rid, 1)
        await reward(actor, "2", "24-full-reward-bag")
        all_unlocks = tuple(
            AchievementUnlock(d.achievement_id, d.name, d.tier, d.points, d.rewards, clock.now().isoformat())
            for d in definitions
        )
        cases.append(
            (
                "achievement_unlock",
                "25-bounded-forty-eight-unlocks",
                achievement_unlock_view(actor.display_name, all_unlocks),
            )
        )
        row = dict(
            await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (character("kasumi").template_id,))
        )
        pig = pig_card(row, mode_label="抓猪成功")
        food = FoodCardViewModel(
            mode_label="做菜成功",
            display_name=food_entry["display_name"],
            owner_display_name=actor.display_name,
            rarity=5,
            rarity_name="五星菜",
            short_code="QAFOOD23",
            description=food_entry["description"],
            portion_weight=12.3,
            fat_label="均衡",
            official_value=500,
            acquired_at="2026-08-28 12:00",
            source_selector="原料猪#QATEST23",
            effect_summary="离线布局样张；正式菜品效果保持原样。",
            image_fit="contain",
            media_visible=True,
            is_animated=False,
            media_format="PNG",
            coin_reward=30,
            experience_reward=20,
            coin_balance=10000,
            player_level=21,
            total_experience=22000,
            next_level_experience=23000,
            cookware_level=5,
            probability_line="4★70.000%　5★20.000%　6★10.000%",
            probability_sources="离线布局样张",
        )
        frames = [""] + [key for key, item in ACTIVITY_REWARDS.items() if item["kind"] == "frame"]
        for index, frame in enumerate(frames):
            for kind, sample, media in (
                ("static_pig_card", pig, row["image_relpath"]),
                (
                    "static_food_card",
                    food,
                    (
                        await db.fetch_one(
                            "SELECT image_relpath FROM food_templates WHERE template_id=?", (food_entry["template_id"],)
                        )
                    )[0],
                ),
            ):
                sample = replace(
                    sample, achievement_title="把日子过成三本书", achievement_frame=frame, achievement_badge="双人票根"
                )
                cases.append((kind, f"26-{kind}-{index}", (sample, media)))
        assert cosmetic_detail("frame-nine-colors")["family"] == "nine-colors"
        return cases, data_root
    finally:
        await db.close()


async def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请指定全新的隔离验收目录。")
    output.mkdir(parents=True)
    cases, data_root = await scenarios(output)
    outputs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())
            baseline_slots = {}
            for kind, name, view in cases:
                capability.label = name
                render = getattr(renderer, "render_" + kind)
                if kind in {"dispatch", "tour", "battle"}:
                    paths = {p.short_code: media_path(data_root, p.image_relpath) for p in view.pigs if p.image_relpath}
                    rendered = await render(view, paths)
                    (output / f"{name}.txt").write_text(view.text(), encoding="utf-8")
                elif kind.startswith("static_"):
                    sample, media = view
                    rendered = await render(sample, media_path(data_root, media))
                    rect = await capability.page.locator(".pig-card__media").bounding_box()
                    assert rect == baseline_slots.setdefault(kind, rect), "Cosmetics moved the animation media slot"
                else:
                    rendered = await render(view)
                destination = output / f"{name}.png"
                write_image(destination, rendered)
                outputs.append(destination)
        finally:
            await capability.close()
            await browser.close()
    failures = [d for d in capability.diagnostics if d["clippedText"] or d["outside"] or d["brokenImages"]]
    report = {
        "status": "failed" if failures else "passed",
        "count": len(outputs),
        "diagnostics": capability.diagnostics,
        "failures": failures,
        "scope": "synthetic players, public local art; no live data, network or QQ",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    contact_sheet(outputs, output / "contact-sheet.jpg")
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False))
    return {"status": "passed", "count": len(outputs), "report": str(output / "report.json")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--browser-executable", type=Path, default=Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    )
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
