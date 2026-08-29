"""只在新建隔离库运行真实业务，验收全功能美术票据；不连接生产或QQ。"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import shutil
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.domain.special_content import (  # noqa: E402
    TECHNIQUE_LAPSE_BLUE,
    TECHNIQUE_MALEVOLENT_KITCHEN,
    TECHNIQUE_REVERSAL_RED,
)
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.repositories import TechniqueRepository  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer  # noqa: E402
from pig_catcher.rendering.adapters import (  # noqa: E402
    food_media_path,
    item_receipt_view,
    ledger_view,
    pig_media_path,
    purchase_receipt_view,
    roulette_event_view,
    special_event_eat_view,
    store_view,
    technique_activation_view,
    technique_catch_event_view,
)
from pig_catcher.services import AssetCatalogService, GameplayService  # noqa: E402
from pig_catcher.services.economy import EconomyService  # noqa: E402
from tests.test_economy import _grant_coins, _insert_food  # noqa: E402
from tests.test_gameplay import MutableClock  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_dispatch_views import contact_sheet  # noqa: E402


async def scenarios(output: Path):
    library = PROJECT_ROOT / "asset_library/current"
    manifest = json.loads((library / "assets.json").read_text(encoding="utf-8"))
    entries = manifest["entries"]
    six_pigs = [
        entry
        for entry in entries
        if entry["kind"] == "pig" and entry["rarity"] == 6 and str(entry.get("group_scope_id", "")).endswith("CEAB3520")
    ][:5]
    if len(six_pigs) != 5:
        raise ValueError("需要当前素材目录中同作用域的五只六星猪用于离线样图。")
    scope = ScopeKey.parse(six_pigs[0]["group_scope_id"])
    required_names = {"五条猪", "五条猪无量苍蓝雪山", "五条猪无量赫焰雪山", "炸猪全家桶", "猪保千猪排轮盘"}
    selected = list(six_pigs)
    selected.extend(
        entry
        for entry in entries
        if entry["display_name"] in required_names
        and (entry["scope"] == "common" or entry.get("group_scope_id") == scope.value)
    )
    paired_ids = {entry["paired_food_template_id"] for entry in six_pigs}
    selected.extend(entry for entry in entries if entry["template_id"] in paired_ids)
    selected_foods = {entry["template_id"] for entry in selected if entry["kind"] == "food"}
    selected_ids = {entry["template_id"] for entry in selected}
    selected.extend(
        entry
        for entry in entries
        if entry["kind"] == "pig"
        and entry.get("paired_food_template_id") in selected_foods
        and entry["template_id"] not in selected_ids
    )
    source = output / "inputs"
    for entry in selected:
        for relative in (entry["image"], entry.get("alternate_image", "")):
            if relative:
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(library / relative, destination)
    manifest["entries"] = selected
    (source / "assets.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    data = output / "isolated-data"
    db = PigCatcherDatabase(data / "feature-receipts.sqlite3")
    await db.open()
    cases = []
    try:
        await AssetCatalogService(
            db, AssetCatalogStorage(data), min_image_side=32, max_image_bytes=32 * 1024 * 1024
        ).import_manifest(source / "assets.json")
        names = ("图审·术式主理人", "图审·抓猪小队", "图审·苍蓝获得者", "图审·赫焰获得者", "图审·远行旅人")
        actors = [
            CommandIdentity(scope, "offline-stream", f"offline-member-{i}", name, f"seed-{i}", "完全离线美术验收群")
            for i, name in enumerate(names)
        ]
        for actor in actors:
            await _grant_coins(db, actor, 100_000)
        actor = actors[0]
        clock = MutableClock(datetime(2026, 8, 27, 4, tzinfo=UTC))
        now = "2026-08-27T04:00:00.000Z"
        catching = CatchingSection(
            cooldown_seconds=0,
            rarity_1_weight=0,
            rarity_2_weight=0,
            rarity_3_weight=0,
            rarity_4_weight=0,
            rarity_5_weight=100,
            rarity_6_weight=0,
        )
        game = GameplayService(db, catching, clock=clock, random_source=random.Random(947))
        economy = EconomyService(db, CookingSection(), EconomySection(), clock=clock, random_source=random.Random(321))

        def fresh(who=actor):
            return replace(who, message_id=uuid4().hex)

        cases.append(("01-store", "store", store_view(await economy.store(actor, page=1, category="全部")), None, {}))
        for slug, category in (("dispatch", "派遣"), ("tour", "巡演"), ("battle", "对战")):
            cases.append(
                (
                    f"01-store-{slug}",
                    "store",
                    store_view(await economy.store(actor, page=1, category=category)),
                    None,
                    {},
                )
            )
        purchased = await economy.purchase(fresh(), "幸运猪哨", quantity=3)
        cases.append(("02-purchase", "economy_receipt", purchase_receipt_view(purchased), None, {}))
        armed = await game.arm_item(fresh(), "幸运猪哨", quantity=3)
        cases.append(("03-item-queue", "item_receipt", item_receipt_view(armed), None, {}))
        await game.cancel_item(fresh(), "catching")
        async with db.transaction() as session:
            for technique in (TECHNIQUE_LAPSE_BLUE, TECHNIQUE_REVERSAL_RED, TECHNIQUE_MALEVOLENT_KITCHEN):
                await TechniqueRepository().grant_permit(
                    session, player_id=actor.player_id, technique_id=technique, uses=1, now=now
                )
        for title, technique in (("04-blue", TECHNIQUE_LAPSE_BLUE), ("06-red", TECHNIQUE_REVERSAL_RED)):
            activated = await game.activate_group_technique(fresh(), technique_id=technique)
            card = technique_activation_view(
                activated, actor_name=actor.display_name, actor_player_id=actor.player_id, group_name=actor.group_name
            )
            cases.append((title, "group_event", card, None, {}))
            for index in range(5):
                catcher = fresh(actors[index])
                caught = await game.catch(catcher)
                if index == 0:
                    card = technique_catch_event_view(
                        caught,
                        catcher_name=catcher.display_name,
                        catcher_player_id=catcher.player_id,
                        group_name=actor.group_name,
                    )
                    paths = {caught.pig.short_code: pig_media_path(data, caught.pig)}
                    cases.append((title + "-catch", "group_event", card, pig_media_path(data, caught.pig), paths))
        purple = await game.activate_hollow_purple(fresh())
        card = technique_activation_view(
            purple, actor_name=actor.display_name, actor_player_id=actor.player_id, group_name=actor.group_name
        )
        assert len(card.assets) == 5
        assert [entry.short_code for entry in card.assets] == [pig.short_code for pig in purple.granted_pigs]
        paths = {pig.short_code: pig_media_path(data, pig) for pig in purple.granted_pigs}
        cases.append(("08-purple-five-assets", "group_event", card, next(iter(paths.values())), paths))
        cases.append(("09-purple-missing-media", "group_event", card, None, {}))
        activated = await game.activate_group_technique(fresh(), technique_id=TECHNIQUE_MALEVOLENT_KITCHEN)
        card = technique_activation_view(
            activated, actor_name=actor.display_name, actor_player_id=actor.player_id, group_name=actor.group_name
        )
        cases.append(("10-domain-activation", "group_event", card, None, {}))
        for label, catcher in (("11-domain-two-owners", actors[1]), ("12-domain-self-owner", actor)):
            caught = await game.catch(fresh(catcher))
            card = technique_catch_event_view(
                caught,
                catcher_name=catcher.display_name,
                catcher_player_id=catcher.player_id,
                group_name=actor.group_name,
            )
            foods = caught.technique_resolution.generated_foods
            assert len(card.assets) == len(foods) == 2
            assert [item.owner_name for item in card.assets] == [food.owner_display_name for food in foods]
            paths = {food.short_code: food_media_path(data, food) for food in foods}
            cases.append((label, "group_event", card, next(iter(paths.values())), paths))

        async def eat_seeded(name, code):
            entry = next(entry for entry in selected if entry["display_name"] == name)
            await _insert_food(
                db,
                player_id=actor.player_id,
                scope_id=scope.value,
                template_id=entry["template_id"],
                display_name=name,
                official_value=100,
                short_code=code,
                instance_id=code,
                rarity=entry["rarity"],
                effect_id=entry["effect_id"],
                effect_params=entry["effect_params"],
                now=now,
            )
            return await economy.eat(fresh(), f"{name}#{code}")

        tribute = await eat_seeded("炸猪全家桶", "FXKFC50")
        card = special_event_eat_view(tribute, group_name=actor.group_name)
        cases.append(("13-kfc-total", "group_event", card, food_media_path(data, tribute.food), {}))
        roulette = await eat_seeded("猪保千猪排轮盘", "FXROLL3")
        card = special_event_eat_view(roulette, group_name=actor.group_name)
        cases.append(("14-roulette-unspun", "group_event", card, food_media_path(data, roulette.food), {}))
        spun = await economy.spin_roulette(fresh())
        card = roulette_event_view(
            spun, actor_name=actor.display_name, actor_player_id=actor.player_id, group_name=actor.group_name
        )
        assert card.roulette_outcome == spun.outcome
        cases.append(("15-roulette-result", "group_event", card, None, {}))
        cases.append(("16-ledger", "ledger", ledger_view(await economy.ledger(actor, page=1)), None, {}))
    finally:
        await db.close()
    return cases


async def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请指定新的输出目录，不覆盖既有证据。")
    output.mkdir(parents=True)
    cases = await scenarios(output)
    outputs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(args.browser_executable))
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        try:
            renderer = PigCatcherRenderer(capability, render_options())
            for label, kind, view, source, paths in cases:
                capability.label = label
                if kind == "group_event":
                    image = await renderer.render_group_event(view, source, media_paths=paths)
                else:
                    image = await getattr(renderer, f"render_{kind}")(view)
                path = output / f"{label}.png"
                write_image(path, image)
                outputs.append(path)
        finally:
            await capability.close()
            await browser.close()
    failures = [row for row in capability.diagnostics if row["clippedText"] or row["outside"] or row["brokenImages"]]
    report = {
        "status": "failed" if failures else "passed",
        "count": len(outputs),
        "failures": failures,
        "diagnostics": capability.diagnostics,
        "scope": "fresh isolated database; fixture users; local artwork",
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
