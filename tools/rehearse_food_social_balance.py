"""Read-only production snapshot and isolated Schema67 upgrade; no QQ or rewards."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from tools.rehearse_social_rewards_release import inspect, snapshot  # noqa: E402


async def rehearse(source: Path, output: Path) -> dict:
    source = source.resolve(strict=True)
    output = output.resolve()
    if output == source.parent or source.is_relative_to(output):
        raise ValueError("隔离输出不可包含原数据库。")
    output.mkdir(parents=True, exist_ok=False)
    before = snapshot(source, output / "before-schema66.sqlite3")
    target = output / "isolated-schema67.sqlite3"
    shutil.copy2(output / "before-schema66.sqlite3", target)
    database = PigCatcherDatabase(target)
    await database.open()
    await database.close()
    after = inspect(target)
    changes = {
        name: (count, after["counts"].get(name))
        for name, count in before["counts"].items()
        if name != "schema_migrations" and count != after["counts"].get(name)
    }
    with sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True) as db:
        invalid = db.execute(
            "SELECT COUNT(*) FROM player_catch_window_transfers WHERE transferred_uses>17 OR target_catches_used>34"
        ).fetchone()[0]
        mousse = db.execute(
            "SELECT COUNT(*) FROM food_instances WHERE display_name_snapshot='彩彩修车猪慕斯' "
            "AND state IN('active','locked-for-trade') AND effect_id!='next-six-star-cook-duplicate'"
        ).fetchone()[0]
        active = db.execute(
            "SELECT COUNT(*) FROM battle_matches WHERE status='active' "
            "OR (status='pending' AND expires_ms>CAST(strftime('%s','now') AS INTEGER)*1000)"
        ).fetchone()[0]
    if (
        changes
        or invalid
        or mousse
        or after["quick_check"] != "ok"
        or after["foreign_key_errors"]
        or after["ledger_mismatches"]
    ):
        raise RuntimeError(f"隔离验收失败：{changes=}, {invalid=}, {mousse=}, {after=}")
    report = {
        "source_read_only": True,
        "before": before,
        "after": after,
        "old_table_count_changes": changes,
        "invalid_transfers": invalid,
        "unmigrated_mousse": mousse,
        "active_battles_at_snapshot": active,
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "passed",
        "schema_before": before["schema"],
        "schema_after": after["schema"],
        "old_table_count_changes": changes,
        "active_battles_at_snapshot": active,
        "output": str(output),
        "backup_sha256": before["sha256"],
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(rehearse(args.source, args.output)), ensure_ascii=False, indent=2))
