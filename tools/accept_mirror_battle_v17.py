"""从只读正式库备份验证v17迁移、恢复、四群目录和图片；从不连接QQ。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright  # noqa: E402

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection  # noqa: E402
from pig_catcher.domain import battle  # noqa: E402
from pig_catcher.domain.battle_catalog import FIGHTERS_BY_ID  # noqa: E402
from pig_catcher.domain.food_effects import effect_summary  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer, food_card_view, pig_card_view  # noqa: E402
from pig_catcher.services import EconomyService, FrameworkService, GameplayService  # noqa: E402
from pig_catcher.services.battle_views import matchup, wheels  # noqa: E402
from tests.test_economy import _identity, _insert_food, _insert_pig  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.import_asset_catalog import import_catalog  # noqa: E402
from tools.rehearse_social_rewards_release import inspect, snapshot  # noqa: E402


def table_digests(path):
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        names = [
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ]
        result = {}
        for name in names:
            digest = hashlib.sha256()
            quoted = name.replace('"', '""')
            for row in db.execute(f'SELECT * FROM "{quoted}" ORDER BY rowid'):
                digest.update(repr(row).encode("utf-8"))
                digest.update(b"\n")
            result[name] = digest.hexdigest()
        return result


async def main(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("请使用独立的新验收目录。")
    output.mkdir(parents=True)
    before = snapshot(args.source, output / "before-schema68.sqlite3")
    assert before["schema"] == 68
    data = output / "catalog"
    data.mkdir()
    target = data / "pig_catcher.sqlite3"
    shutil.copy2(output / "before-schema68.sqlite3", target)
    baseline = table_digests(target)
    db = PigCatcherDatabase(target)
    await db.open()
    await db.close()
    migrated = inspect(target)
    after_digests = table_digests(target)
    assert migrated["schema"] == 69 and migrated["quick_check"] == "ok"
    assert not migrated["foreign_key_errors"] and not migrated["ledger_mismatches"]
    unchanged = {k: v for k, v in baseline.items() if k != "schema_migrations"}
    assert all(after_digests[k] == v for k, v in unchanged.items())
    imported = await import_catalog(
        argparse.Namespace(
            manifest=ROOT / "asset_library/current/assets.json",
            data_dir=data,
            database_filename="pig_catcher.sqlite3",
            min_image_side=180,
            max_image_bytes=12 * 1024 * 1024,
            max_animation_frames=300,
            max_animation_duration_ms=30000,
        )
    )
    assert imported["entry_count"] == 360
    post_import = table_digests(target)
    metadata_tables = {
        "schema_migrations",
        "scopes",
        "pig_templates",
        "food_templates",
        "scope_pig_templates",
        "scope_food_templates",
        "asset_manifest_imports",
        "audit_events",
    }
    assert all(post_import[k] == v for k, v in unchanged.items() if k not in metadata_tables)
    scope_columns = "scope_id,platform,group_id,group_name,stream_id,enabled,created_at"
    with sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("ATTACH DATABASE ? AS old", (f"{(output / 'before-schema68.sqlite3').as_uri()}?mode=ro",))
        assert (
            connection.execute(f"SELECT {scope_columns} FROM main.scopes ORDER BY scope_id").fetchall()
            == connection.execute(f"SELECT {scope_columns} FROM old.scopes ORDER BY scope_id").fetchall()
        )
    await db.open()
    await db.backup_to(output / "verified-schema69.sqlite3")
    restored = PigCatcherDatabase(output / "verified-schema69.sqlite3")
    await restored.open()
    await restored.close()
    assert table_digests(output / "verified-schema69.sqlite3") == table_digests(target)

    who = _identity(group_id="1092931381", user_id="offline-v17-qa", message_id="offline-v17-art")
    await FrameworkService(db).touch_identity(who)
    game = GameplayService(db, CatchingSection(cooldown_seconds=0))
    economy = EconomyService(db, CookingSection(), EconomySection())
    pigs = []
    for index, fighter_id in enumerate(("luoli", "miumiu")):
        row = dict(
            await db.fetch_one(
                "SELECT * FROM pig_templates WHERE template_id=?", (FIGHTERS_BY_ID[fighter_id].template_id,)
            )
        )
        await _insert_pig(
            db,
            player_id=who.player_id,
            scope_id=who.scope.value,
            template_id=row["template_id"],
            rarity=6,
            display_name=row["display_name"],
            official_value=24000,
            short_code=f"V17P{index}",
            instance_id=f"offline-v17-pig-{index}",
        )
        pigs.append(await game.pig_detail(who, f"{row['display_name']}#V17P{index}"))
    food = dict(
        await db.fetch_one(
            "SELECT * FROM food_templates WHERE display_name=? AND template_id LIKE 'food-g1092931381-%'",
            ("翠玉抹茶芭菲",),
        )
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
        short_code="V17F0",
        instance_id="offline-v17-food",
    )
    food_view = await economy.food_detail(who, "翠玉抹茶芭菲#V17F0")
    assert "额外10次" in effect_summary(food_view.effect_id, food_view.effect_params)
    snapshots = [
        dict(
            fighter_id=f,
            level=3,
            trait_bonus=0,
            tool_id="",
            name=FIGHTERS_BY_ID[f].name,
            player_name=name,
            player_id=str(i),
            short_code=f"V17P{i}",
            rarity=6,
            template_id=pigs[i].template_id,
            image_relpath=pigs[i].image_relpath,
            size_value=75,
            weight_value=180,
        )
        for i, (f, name) in enumerate((("luoli", "抹茶验收玩家"), ("miumiu", "水镜验收玩家")))
    ]
    views = []
    for desired in (0, 1):
        for index in range(100):
            seed = f"v17-art-{desired}-{index}"
            current = battle.new_state(snapshots, seed=seed)
            current["sides"][1]["miumiu_humidity"] = 4
            current["round_origin"] = deepcopy(current["sides"])
            sequences = (
                ("parfait", "soba", "with-cat", "pet-cat", "sleep", "guitar", "mutsumi"),
                ("softening", "coupling", "split", "rebuild", "blank", "observe", "water-life"),
            )
            for side, slugs in enumerate(sequences):
                player = current["sides"][side]
                player["turn"].update(raw=5, effective=5, pending=5, done=False)
                for slug in slugs:
                    move = next(
                        m
                        for m in FIGHTERS_BY_ID[player["snapshot"]["fighter_id"]].moves
                        if m.move_id == player["snapshot"]["fighter_id"] + "-" + slug
                    )
                    event = battle.apply_move(player, move, seed=seed, side=side)
                    event.update(side=side, round=1, fighter_id=player["snapshot"]["fighter_id"])
                    player["turn"]["events"].append(event)
            current["miumiu_observations"] = [battle.mirror_battle.observation(p) for p in current["sides"]]
            result = battle.resolve_round(current, seed)
            if result["interactions"]["domain"]["hit_side"] == desired:
                match = dict(
                    battle_id=f"QA17-{desired}", status=current["status"], definition_version=17, expires_ms=600000
                )
                views.append(matchup(who, match, current, 0, round_result=result))
                break
        else:
            raise AssertionError("未找到预期领域示例")
    renders = output / "renders"
    renders.mkdir()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=args.browser)
        cap = PlaywrightRenderCapability(browser)
        await cap.open()
        renderer = PigCatcherRenderer(cap, render_options())
        for fighter_id in ("luoli", "miumiu"):
            cap.label = fighter_id + "-wheel"
            write_image(renders / (cap.label + ".png"), await renderer.render_battle(wheels(who, fighter_id), {}))
        cap.label = "luoli-pig"
        write_image(
            renders / "luoli-pig.png",
            await renderer.render_static_pig_card(
                pig_card_view(pigs[0], mode_label="功能验收"), data / pigs[0].image_relpath
            ),
        )
        cap.label = "matcha-food"
        write_image(
            renders / "matcha-food.png",
            await renderer.render_static_food_card(
                food_card_view(food_view, mode_label="功能验收"), data / food_view.image_relpath
            ),
        )
        images = {f"V17P{i}": data / p.image_relpath for i, p in enumerate(pigs)}
        for index, view in enumerate(views):
            cap.label = f"round-domain-{index}"
            write_image(renders / (cap.label + ".png"), await renderer.render_battle(view, images))
        await cap.close()
        await browser.close()
    assert not any(
        row["clippedText"] or row["outside"] or row["brokenImages"] or row["clippedMedia"] for row in cap.diagnostics
    )
    report = dict(
        source_read_only=True,
        schema_before=before["schema"],
        schema_after=migrated["schema"],
        business_tables_unchanged=True,
        restore_verified=True,
        catalog=imported,
        diagnostics=cap.diagnostics,
        integrity=inspect(target),
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    await db.close()
    print(
        json.dumps(
            {
                "schema": "68→69",
                "business_tables_unchanged": True,
                "restore_verified": True,
                "render_count": len(cap.diagnostics),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("C:/Users/Administrator/MaiBot/data/plugins/local.pig-catcher/pig_catcher.sqlite3"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", default="C:/Program Files/Google/Chrome/Application/chrome.exe")
    asyncio.run(main(parser.parse_args()))
