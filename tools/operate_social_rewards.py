"""Operator-only durable birthday scheduling/status. No raw player data in output."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.services.scheduled_rewards import BIRTHDAY_ID, ScheduledRewardService  # noqa: E402
from pig_catcher.version import SCHEMA_VERSION  # noqa: E402


def status(path: Path) -> dict:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        result = {
            "schema_version": version,
            "scopes": [
                dict(row)
                for row in db.execute(
                    "SELECT s.scope_id,s.group_name,s.enabled,COUNT(p.player_id) registered_players FROM scopes s "
                    "LEFT JOIN players p ON p.scope_id=s.scope_id GROUP BY s.scope_id ORDER BY s.scope_id"
                )
            ],
            "active_battles": [
                dict(row) for row in db.execute("SELECT status,COUNT(*) AS count FROM battle_matches GROUP BY status")
            ],
        }
        if version >= 65:
            result["campaigns"] = [
                dict(row)
                for row in db.execute(
                    "SELECT campaign_id,title,scheduled_at,state,created_at,completed_at "
                    "FROM scheduled_reward_campaigns"
                )
            ]
            result["birthday_scopes"] = [
                dict(row)
                for row in db.execute(
                    "SELECT g.scope_id,g.recipient_count,g.completed_at,r.send_status FROM scheduled_reward_scopes g "
                    "LEFT JOIN command_receipts r ON r.receipt_id=g.receipt_id WHERE g.campaign_id=?",
                    (BIRTHDAY_ID,),
                )
            ]
            result["birthday_grants"] = db.execute(
                "SELECT COUNT(*) FROM scheduled_reward_grants WHERE campaign_id=?", (BIRTHDAY_ID,)
            ).fetchone()[0]
            result["red_packets"] = [
                dict(row)
                for row in db.execute(
                    "SELECT status,funding,COUNT(*) AS count FROM red_packets GROUP BY status,funding"
                )
            ]
        return result


async def mutate(args) -> dict:
    if status(args.database)["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("必须先完成正式版本数据库迁移，运营脚本不会悄悄升级数据库。")
    database = PigCatcherDatabase(args.database)
    await database.open()
    try:
        service = ScheduledRewardService(database)
        if args.action == "schedule":
            return await service.schedule_birthday(args.scope, numbering_confirmed=args.confirm_commemorative)
        return {"newly_granted": await service.process_due(), "notices": "由在线插件发送，不在此脚本重复发送"}
    finally:
        await database.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("action", choices=("status", "schedule", "run-due"))
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument(
        "--confirm-commemorative",
        action="store_true",
        help="确认统一生日纪念编号、各自唯一操作编号；不放开重复资产编号",
    )
    args = parser.parse_args()
    report = status(args.database) if args.action == "status" else asyncio.run(mutate(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
