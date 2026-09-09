"""正式库只读副本迁移/恢复及流萤九格浏览器验收；不连接QQ。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright  # noqa: E402

from pig_catcher.domain import battle  # noqa: E402
from pig_catcher.domain.battle_catalog import FIGHTERS_BY_ID, FIREFLY_PIG_TEMPLATE_IDS  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.rendering import PigCatcherRenderer  # noqa: E402
from pig_catcher.services.battle_views import matchup, wheels  # noqa: E402
from tools.accept_catching_and_collection_views import (  # noqa: E402
    PlaywrightRenderCapability,
    render_options,
    write_image,
)
from tools.accept_mirror_battle_v17 import table_digests  # noqa: E402
from tools.rehearse_social_rewards_release import inspect, snapshot  # noqa: E402


async def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=args.render_only)
    before_path = output / "before-schema70.sqlite3"
    before = inspect(before_path) if args.render_only else snapshot(args.source, before_path)
    assert before["schema"] == 70
    target = output / "migrated-schema71.sqlite3"
    if not args.render_only:
        shutil.copy2(before_path, target)
    baseline = table_digests(before_path)
    db = PigCatcherDatabase(target)
    await db.open()
    migrated = inspect(target)
    assert migrated["schema"] == 71 and migrated["quick_check"] == "ok"
    assert not migrated["foreign_key_errors"] and not migrated["ledger_mismatches"]
    after = table_digests(target)
    assert after.keys() == baseline.keys()
    assert all(after[k] == v for k, v in baseline.items() if k != "schema_migrations")
    restored_path = output / "restored-schema71.sqlite3"
    if not args.render_only:
        await db.backup_to(restored_path)
    restored = PigCatcherDatabase(restored_path)
    await restored.open()
    await restored.close()
    assert table_digests(restored_path) == table_digests(target)
    source_data = args.source.parent
    snapshots = []
    scope_parity = []
    for template_id in FIREFLY_PIG_TEMPLATE_IDS:
        row = dict(await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (template_id,)))
        scope_parity.append((row["display_name"], row["rarity"], row["description"]))
    assert len(set(scope_parity)) == 1
    for i, name in enumerate(("流萤验收玩家", "半层溃败验收玩家")):
        definition = FIGHTERS_BY_ID["firefly"]
        row = dict(await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (definition.template_id,)))
        assert (source_data / row["image_relpath"]).is_file()
        snapshots.append(
            dict(
                fighter_id="firefly",
                level=3,
                trait_bonus=0,
                tool_id="",
                name=definition.name,
                player_name=name,
                player_id=str(i),
                short_code=f"QA18P{i}",
                rarity=6,
                template_id=definition.template_id,
                image_relpath=row["image_relpath"],
                size_value=75,
                weight_value=180,
            )
        )
    who = CommandIdentity(ScopeKey("qq", "offline-v18"), "offline", "qa", "流萤验收")
    views = [("firefly-nine-wheel", wheels(who, "firefly", level=3))]
    for echo in (False, True):
        current = battle.new_state(snapshots, seed="v18-render")
        left, right = current["sides"]
        right["firefly_collapse"] = Fraction(3, 2)
        if echo:
            left.update(firefly_form="sam", firefly_sam_rounds_remaining=2, firefly_fuel=2)
        for side, ids in enumerate(
            (
                (
                    "firefly-silent-galaxy",
                    "firefly-dream-destination",
                    "firefly-crimson-cocoon",
                    "sam-skyfire-bombardment",
                    "sam-bottom-fire-slash",
                    "firefly-falling-sky",
                ),
                ("sam-deathstar-overload", "sam-ignite-star-sea"),
            )
        ):
            player = current["sides"][side]
            player["turn"].update(raw=5, effective=5, pending=len(ids), done=False)
            for move_id in ids:
                move = next(m for m in FIGHTERS_BY_ID["firefly"].moves if m.move_id == move_id)
                event = battle.apply_move(
                    player, move, side=side, seed="v18-render", firefly_echo_scale=Fraction(1, 2) if echo else 1
                )
                event.update(side=side, round=1, fighter_id="firefly")
                battle._apply_firefly_event_context(current, side, event)
                player["turn"]["events"].append(event)
        match = dict(battle_id=f"QA18-{int(echo)}", status="active", definition_version=18, expires_ms=600000)
        views.append((f"firefly-{'echo' if echo else 'normal'}-moves", matchup(who, match, current, 0)))
        for hit in (True, False):
            for index in range(100):
                seed = f"v18-render-{hit}-{index}"
                attempt = deepcopy(current)
                result = battle.resolve_round(attempt, seed)
                if (result["interactions"]["domain"]["hit_side"] == 0) == hit:
                    views.append(
                        (
                            f"firefly-{'echo' if echo else 'normal'}-{'hit' if hit else 'miss'}",
                            matchup(who, match, attempt, 0, round_result=result),
                        )
                    )
                    break
            else:
                raise AssertionError("未找到领域验收分支")
    images = {s["short_code"]: source_data / s["image_relpath"] for s in snapshots}
    renders = output / "renders"
    renders.mkdir(exist_ok=args.render_only)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=args.browser)
        cap = PlaywrightRenderCapability(browser)
        await cap.open()
        renderer = PigCatcherRenderer(cap, render_options())
        for label, view in views:
            cap.label = label
            write_image(renders / f"{label}.png", await renderer.render_battle(view, images))
            (renders / f"{label}.txt").write_text(view.text(), encoding="utf-8")
        await cap.close()
        await browser.close()
    assert not any(r["clippedText"] or r["outside"] or r["brokenImages"] or r["clippedMedia"] for r in cap.diagnostics)
    report = dict(
        source_read_only=True,
        schema_before=70,
        schema_after=71,
        business_tables_unchanged=True,
        restore_verified=True,
        four_scope_semantic_parity=True,
        diagnostics=cap.diagnostics,
        backup=before,
        integrity=migrated,
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    await db.close()
    print(
        json.dumps(
            dict(
                schema="70→71",
                render_count=len(cap.diagnostics),
                restore_verified=True,
                business_tables_unchanged=True,
                output=str(output),
            ),
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
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--browser", default="C:/Program Files/Google/Chrome/Application/chrome.exe")
    asyncio.run(main(parser.parse_args()))
