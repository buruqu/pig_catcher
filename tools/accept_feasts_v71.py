"""正式库只读副本的双菜迁移、恢复及真实浏览器图片验收，不连接QQ。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright  # noqa: E402

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection  # noqa: E402
from pig_catcher.domain.feasts import CLOVER_FEAST, MOON_FEAST  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.rendering import (  # noqa: E402
    PigCatcherRenderer,
    food_card_view,
    pig_card_view,
    profile_view,
    store_view,
)
from pig_catcher.rendering.food_rewards import food_reward_view  # noqa: E402
from pig_catcher.services import EconomyService, FrameworkService, GameplayService  # noqa: E402
from tests.test_economy import FixedClock, SequenceRandom, _identity, _insert_food, _insert_pig  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_mirror_battle_v17 import table_digests  # noqa: E402
from tools.import_asset_catalog import import_catalog  # noqa: E402
from tools.rehearse_social_rewards_release import inspect, snapshot  # noqa: E402

METADATA = {
    "schema_migrations",
    "scopes",
    "pig_templates",
    "food_templates",
    "scope_pig_templates",
    "scope_food_templates",
    "asset_manifest_imports",
    "audit_events",
}
NEW_TABLES = {"player_clover_chains", "player_moon_feasts"}


def verify_food_migration(before, after):
    """逐行验证仅未食用两种菜的效果字段变化，包括收藏和交易锁定状态。"""
    with (
        sqlite3.connect(f"{before.resolve().as_uri()}?mode=ro", uri=True) as old,
        sqlite3.connect(f"{after.resolve().as_uri()}?mode=ro", uri=True) as new,
    ):
        columns = [row[1] for row in old.execute("PRAGMA table_info(food_instances)")]
        eid, params, state = [columns.index(name) for name in ("effect_id", "effect_params_json", "state")]
        mapping = {"window-six-star-resonance": CLOVER_FEAST, "catch-window-transfer": MOON_FEAST}
        changed = 0
        expected = []
        for raw in old.execute("SELECT * FROM food_instances ORDER BY rowid"):
            row = list(raw)
            if row[state] in {"active", "locked-for-trade"} and row[eid] in mapping:
                row[eid], row[params] = mapping[row[eid]], "{}"
                changed += 1
            expected.append(tuple(row))
        assert new.execute("SELECT * FROM food_instances ORDER BY rowid").fetchall() == expected
        return changed


async def prepare(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    old = out / "before-schema69.sqlite3"
    baseline = snapshot(args.source, old)
    assert baseline["schema"] == 69
    data = out / "catalog"
    data.mkdir()
    target = data / "pig_catcher.sqlite3"
    shutil.copy2(old, target)
    digests = table_digests(old)
    db = PigCatcherDatabase(target)
    await db.open()
    await db.close()
    after = table_digests(target)
    assert set(after) == set(digests) | NEW_TABLES
    assert all(after[k] == v for k, v in digests.items() if k not in {"schema_migrations", "food_instances"})
    changed = verify_food_migration(old, target)
    catalog = await import_catalog(
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
    assert catalog["entry_count"] == 360 and catalog["schema_version"] == 70
    after = table_digests(target)
    assert all(after[k] == v for k, v in digests.items() if k not in METADATA | {"food_instances"})
    assert verify_food_migration(old, target) == changed
    integrity = inspect(target)
    assert (
        integrity["quick_check"] == "ok" and not integrity["foreign_key_errors"] and not integrity["ledger_mismatches"]
    )
    # All four group definitions must agree while retaining separate template identities.
    with sqlite3.connect(target) as con:
        for eid in (CLOVER_FEAST, MOON_FEAST):
            rows = con.execute(
                "SELECT effect_id,effect_params_json FROM food_templates WHERE effect_id=?", (eid,)
            ).fetchall()
            assert len(rows) == 4 and len(set(rows)) == 1
    await db.open()
    restore = out / "verified-schema70.sqlite3"
    await db.backup_to(restore)
    restored = PigCatcherDatabase(restore)
    await restored.open()
    await restored.close()
    assert table_digests(restore) == table_digests(target)
    await db.close()
    report = dict(
        schema_before=69,
        schema_after=70,
        source_read_only=True,
        restore_verified=True,
        unchanged_other_business_tables=True,
        updated_uneaten_foods=changed,
        catalog=catalog,
        integrity=integrity,
    )
    (out / "migration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({k: v for k, v in report.items() if k not in {"catalog", "integrity"}}, ensure_ascii=False),
        flush=True,
    )


async def render(args):
    out = args.output.resolve()
    # Each render pass starts from a verified migrated copy, never the online database.
    target = out / "render-work.sqlite3"
    with (
        sqlite3.connect(f"{(out / 'verified-schema70.sqlite3').as_uri()}?mode=ro", uri=True) as src,
        sqlite3.connect(target) as dst,
    ):
        src.backup(dst)
    db = PigCatcherDatabase(target)
    await db.open()
    clock = FixedClock()
    clock.value = clock.value.replace(year=2027, hour=0)
    who = _identity(group_id="1092931381", user_id="offline-feasts-qa", message_id="qa-seed")
    who = replace(who, display_name="粉蓝与月栖验收玩家")
    await FrameworkService(db).touch_identity(who)
    game = GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(*([0, 0, 0.5, 0.5, 0.5, 0.5, 0.5] * 40)),
    )
    economy = EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        random_source=SequenceRandom(0.999, 0, 0.5, *([0.5, 0.5] * 7)),
    )
    views = []
    foods = []
    for i, eid in enumerate((CLOVER_FEAST, MOON_FEAST)):
        row = dict(
            await db.fetch_one(
                "SELECT * FROM food_templates WHERE effect_id=? AND template_id LIKE 'food-g1092931381-%'", (eid,)
            )
        )
        code = f"FEAST{i}"
        await _insert_food(
            db,
            player_id=who.player_id,
            scope_id=who.scope.value,
            template_id=row["template_id"],
            rarity=6,
            display_name=row["display_name"],
            official_value=25000,
            effect_id=eid,
            effect_params={},
            short_code=code,
            instance_id=f"offline-feasts-food-{i}",
        )
        food = await economy.food_detail(who, f"{row['display_name']}#{code}")
        assert not food.is_animated
        foods.append(food)
        views.append((f"{eid}-detail", "food", food_card_view(food, mode_label="美食详情"), food.image_relpath))
    result = await economy.eat(replace(who, message_id="qa-clover-eat"), foods[0].selector)
    views.append(("clover-eat", "reward", food_reward_view(result), ""))
    for i in range(10):
        last = await game.catch(replace(who, message_id=f"qa-catch-{i}"))
    assert last.weights[5] == 4.07
    views.append(
        (
            "clover-tenth-catch",
            "pig",
            pig_card_view(last.pig, mode_label="专属抓猪", catch=last),
            last.pig.image_relpath,
        )
    )
    views.append(("clover-ready-profile", "profile", profile_view(await game.profile(who)), ""))
    source = dict(
        await db.fetch_one("SELECT * FROM pig_templates WHERE paired_food_template_id=?", (foods[0].template_id,))
    )
    await _insert_pig(
        db,
        player_id=who.player_id,
        scope_id=who.scope.value,
        template_id=source["template_id"],
        rarity=6,
        display_name=source["display_name"],
        official_value=25000,
        short_code="FEASTPIG",
        instance_id="offline-feasts-pig",
    )
    cooked = await economy.cook(replace(who, message_id="qa-cook"), f"{source['display_name']}#FEASTPIG")
    assert cooked.foods[0].rarity == 6 and abs(cooked.weights[5] - 23.07) < 1e-6
    views.append(
        (
            "clover-cook-rewards",
            "food",
            food_card_view(cooked.foods[0], mode_label="做菜成功", cooking=cooked),
            cooked.foods[0].image_relpath,
        )
    )
    eaten = await economy.eat(replace(who, message_id="qa-moon-eat"), foods[1].selector)
    views.append(("moon-eat", "reward", food_reward_view(eaten), ""))
    clock.value += timedelta(hours=1)
    views.append(("moon-blocked-profile", "profile", profile_view(await game.profile(who)), ""))
    clock.value += timedelta(hours=3)
    caught = await game.catch(replace(who, message_id="qa-moon-catch"))
    views.append(
        (
            "moon-target-catch",
            "pig",
            pig_card_view(caught.pig, mode_label="月栖专属抓猪", catch=caught),
            caught.pig.image_relpath,
        )
    )
    views.append(("moon-discount-store", "store", store_view(await economy.store(who, page=1, category="全部")), ""))
    renders = out / "renders"
    renders.mkdir(exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=args.browser)
        cap = PlaywrightRenderCapability(browser)
        await cap.open()
        renderer = PigCatcherRenderer(cap, render_options())
        for label, kind, view, path in views:
            cap.label = label
            if kind == "food":
                image = await renderer.render_static_food_card(view, out / "catalog" / path)
            elif kind == "pig":
                image = await renderer.render_static_pig_card(view, out / "catalog" / path)
            elif kind == "profile":
                image = await renderer.render_profile(view)
            elif kind == "reward":
                image = await renderer.render_food_rewards(view, {})
            else:
                image = await renderer.render_store(view)
            write_image(renders / (label + ".png"), image)
        await cap.close()
        await browser.close()
    report = dict(render_count=len(views), diagnostics=cap.diagnostics)
    (out / "renders.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not any(
        row["clippedText"] or row["outside"] or row["brokenImages"] or row["clippedMedia"] for row in cap.diagnostics
    )
    await db.close()
    print(
        json.dumps({"render_count": len(views), "layout_checks": "passed", "output": str(renders)}, ensure_ascii=False),
        flush=True,
    )


async def main(args):
    if args.phase in {"all", "prepare"}:
        await prepare(args)
    if args.phase in {"all", "render"}:
        await render(args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("C:/Users/Administrator/MaiBot/data/plugins/local.pig-catcher/pig_catcher.sqlite3"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["all", "prepare", "render"], default="all")
    parser.add_argument("--browser", default="C:/Program Files/Google/Chrome/Application/chrome.exe")
    asyncio.run(main(parser.parse_args()))
