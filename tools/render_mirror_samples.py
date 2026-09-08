"""Offline visual QA using the imported isolated catalogue and synthetic players."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain import battle
from pig_catcher.domain.battle_catalog import FIGHTERS_BY_ID
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.rendering import PigCatcherRenderer, food_card_view, pig_card_view
from pig_catcher.services import EconomyService, FrameworkService, GameplayService
from pig_catcher.services.battle_views import matchup, wheels
from tests.test_economy import _identity, _insert_food, _insert_pig
from tools.accept_catching_and_collection_views import PlaywrightRenderCapability, render_options, write_image

ROOT = Path("D:/MaiBotArchives/pig_catcher/preview-prep/mirror-20260908/catalog")
OUT = ROOT.parent / "renders-final"


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    db = PigCatcherDatabase(ROOT / "pig_catcher.sqlite3")
    await db.open()
    who = _identity(group_id="1092931381", user_id="offline-art-qa", message_id="art-seed")
    await FrameworkService(db).touch_identity(who)
    game = GameplayService(db, CatchingSection(cooldown_seconds=0))
    economy = EconomyService(db, CookingSection(), EconomySection())
    pairs = (("luoli-c", "luoli-jade-matcha-parfait"), ("miumiu-flow", "miumiu-water-mirror-jelly"))
    samples = []
    for index, (pig_slug, food_slug) in enumerate(pairs):
        pig = dict(
            await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (f"pig-g1092931381-{pig_slug}",))
        )
        food = dict(
            await db.fetch_one("SELECT * FROM food_templates WHERE template_id=?", (f"food-g1092931381-{food_slug}",))
        )
        if not await db.fetch_one("SELECT 1 FROM pig_instances WHERE pig_instance_id=?", (f"art-pig-{index}",)):
            await _insert_pig(
                db,
                player_id=who.player_id,
                scope_id=who.scope.value,
                template_id=pig["template_id"],
                rarity=6,
                display_name=pig["display_name"],
                official_value=24000,
                short_code=f"ARTP{index}",
                instance_id=f"art-pig-{index}",
            )
            await _insert_food(
                db,
                player_id=who.player_id,
                scope_id=who.scope.value,
                template_id=food["template_id"],
                rarity=6,
                display_name=food["display_name"],
                official_value=25000,
                effect_id=food["effect_id"],
                effect_params=json.loads(food["effect_params_json"]),
                short_code=f"ARTF{index}",
                instance_id=f"art-food-{index}",
            )
        samples.append(
            (
                await game.pig_detail(who, f"{pig['display_name']}#ARTP{index}"),
                await economy.food_detail(who, f"{food['display_name']}#ARTF{index}"),
            )
        )
    gojo = dict(
        await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (FIGHTERS_BY_ID["gojo"].template_id,))
    )
    state = battle.new_state(
        [
            {
                "fighter_id": f,
                "level": 3,
                "trait_bonus": 0,
                "tool_id": "",
                "name": FIGHTERS_BY_ID[f].name,
                "player_name": name,
                "player_id": str(i),
                "short_code": f"ARTB{i}",
                "rarity": 6,
                "template_id": samples[1][0].template_id if f == "miumiu" else gojo["template_id"],
                "image_relpath": samples[1][0].image_relpath if f == "miumiu" else gojo["image_relpath"],
                "size_value": 75,
                "weight_value": 180,
            }
            for i, (f, name) in enumerate((("miumiu", "水镜测试"), ("gojo", "对手测试")))
        ],
        seed="render-miu",
    )
    for side in (0, 1):
        battle.roll_count(state, side, "render-miu")
        while not state["sides"][side]["turn"]["done"]:
            battle.play_chunk(state, side, "render-miu")
    result = battle.resolve_round(state, "render-miu")
    match = {"battle_id": "OFFLINE", "status": state["status"], "definition_version": 16, "expires_ms": 600000}
    battle_view = matchup(who, match, state, 0, round_result=result)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe"
        )
        capability = PlaywrightRenderCapability(browser)
        await capability.open()
        renderer = PigCatcherRenderer(capability, render_options())
        for index, (pig, food) in enumerate(samples):
            capability.label = f"new-pig-{index}"
            write_image(
                OUT / f"pig-{index}.png",
                await renderer.render_static_pig_card(
                    pig_card_view(pig, mode_label="新品预览"), ROOT / pig.image_relpath
                ),
            )
            capability.label = f"new-food-{index}"
            write_image(
                OUT / f"food-{index}.png",
                await renderer.render_static_food_card(
                    food_card_view(food, mode_label="新品预览"), ROOT / food.image_relpath
                ),
            )
        capability.label = "miumiu-round"
        write_image(OUT / "miumiu-wheel.png", await renderer.render_battle(wheels(who, "miumiu"), {}))
        write_image(
            OUT / "battle.png",
            await renderer.render_battle(
                battle_view, {"ARTB0": ROOT / samples[1][0].image_relpath, "ARTB1": ROOT / gojo["image_relpath"]}
            ),
        )
        await capability.close()
        await browser.close()
    report = capability.diagnostics
    (OUT / "dom.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    assert not any(r["clippedText"] or r["outside"] or r["brokenImages"] or r["clippedMedia"] for r in report)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
