"""Read-only live snapshot, isolated schema rehearsal and birthday bundle preflight."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.services.scheduled_rewards import BIRTHDAY_AT, ScheduledRewardService  # noqa: E402


def inspect(path: Path) -> dict:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        tables = [
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ]
        counts = {
            table: db.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
            for table in tables
        }
        mismatch = db.execute(
            "SELECT COUNT(*) FROM players p LEFT JOIN (SELECT player_id,SUM(amount) amount FROM currency_ledger "
            "GROUP BY player_id) l ON l.player_id=p.player_id WHERE p.coin_balance<>COALESCE(l.amount,0)"
        ).fetchone()[0]
        return {
            "schema": db.execute("PRAGMA user_version").fetchone()[0],
            "quick_check": db.execute("PRAGMA quick_check").fetchone()[0],
            "foreign_key_errors": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "ledger_mismatches": mismatch,
            "counts": counts,
        }


def snapshot(source: Path, target: Path) -> dict:
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    report = inspect(target)
    if report["quick_check"] != "ok" or report["foreign_key_errors"] or report["ledger_mismatches"]:
        raise RuntimeError("备份校验失败，禁止部署：" + json.dumps(report))
    with target.open("rb") as handle:
        report["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    return report


async def rehearse(source: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    before = snapshot(source, output / "pre-live-schema63.sqlite3")
    test_path = output / "isolated-rehearsal.sqlite3"
    shutil.copy2(output / "pre-live-schema63.sqlite3", test_path)
    database = PigCatcherDatabase(test_path)
    await database.open()
    try:
        migrated = inspect(test_path)
        changes = {
            name: (count, migrated["counts"].get(name))
            for name, count in before["counts"].items()
            if count != migrated["counts"].get(name) and name != "schema_migrations"
        }
        if changes:
            raise RuntimeError("旧表行数发生意外变动：" + json.dumps(changes))
        scopes = [row[0] for row in await database.fetch_all("SELECT scope_id FROM scopes WHERE enabled=1")]
        service = ScheduledRewardService(database)
        await service.schedule_birthday(scopes, numbering_confirmed=True)
        if await service.process_due() != 0:
            raise RuntimeError("生日福利错误提前发放！")

        class BirthdayClock:
            def now(self):
                return BIRTHDAY_AT

        service = ScheduledRewardService(database, clock=BirthdayClock())
        granted = await service.process_due()
        repeated = await service.process_due()
        final = inspect(test_path)
        if repeated or final["quick_check"] != "ok" or final["foreign_key_errors"] or final["ledger_mismatches"]:
            raise RuntimeError("隔离生日发放验证失败。")
        groups = [
            dict(row)
            for row in await database.fetch_all(
                "SELECT scope_id,recipient_count,completed_at FROM scheduled_reward_scopes"
            )
        ]
        report = {
            "source_read_only": True,
            "before": before,
            "migrated": migrated,
            "old_table_changes": changes,
            "birthday_fixture_granted": granted,
            "repeated_grants": repeated,
            "scopes": groups,
            "after_grant": final,
            "note": "Only the isolated copy received birthday assets; no QQ sends.",
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "status": "passed",
            "schema_before": before["schema"],
            "schema_after": migrated["schema"],
            "backup_sha256": before["sha256"],
            "old_table_changes": changes,
            "isolated_recipients": granted,
            "output": str(output),
        }
    finally:
        await database.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(rehearse(args.source, args.output)), ensure_ascii=False, indent=2))
