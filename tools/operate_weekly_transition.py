"""显式预定周榜交接；状态查询只读。发奖由原周榜事务完成，群公告仅由在线插件投递。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.services.weekly_competitions import WeeklyCompetitionService  # noqa: E402
from pig_catcher.services.weekly_transition import NOTICE_COMMAND, WeeklyTransitionService  # noqa: E402
from pig_catcher.version import SCHEMA_VERSION  # noqa: E402


def status(path: Path) -> dict:
    with sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        schema = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        result = {
            "schema": schema,
            "competitions": [
                dict(row)
                for row in db.execute(
                    "SELECT season_number,name,status,starts_at,ends_at FROM weekly_competitions ORDER BY season_number"
                )
            ],
            "awards": [
                dict(row)
                for row in db.execute(
                    "SELECT c.season_number,a.scope_id,COUNT(*) AS winners FROM weekly_competition_awards a "
                    "JOIN weekly_competitions c ON c.competition_id=a.competition_id "
                    "GROUP BY c.season_number,a.scope_id"
                )
            ],
        }
        if schema >= 66:
            result["transitions"] = [dict(row) for row in db.execute("SELECT * FROM weekly_transitions")]
            result["notices"] = [
                dict(row)
                for row in db.execute(
                    "SELECT scope_id,idempotency_key,send_status,send_error,sent_at,created_at,"
                    "json_extract(result_json,'$.stage') AS stage FROM command_receipts WHERE command_name=?",
                    (NOTICE_COMMAND,),
                )
            ]
        return result


async def operate(args) -> dict:
    if status(args.database)["schema"] != SCHEMA_VERSION:
        raise RuntimeError("请先完成正式版本迁移；本运营脚本不会自动升级数据库。")
    database = PigCatcherDatabase(args.database)
    await database.open()
    try:
        service = WeeklyTransitionService(WeeklyCompetitionService(database))
        if args.action == "schedule":
            return await service.schedule(
                previous_season=args.previous, next_season=args.following, scope_ids=args.scope
            )
        await service.process_due()
        return status(args.database)
    finally:
        await database.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("action", choices=("status", "schedule", "run-due"))
    parser.add_argument("--previous", type=int, default=1)
    parser.add_argument("--following", type=int, default=2)
    parser.add_argument("--scope", action="append", default=[])
    args = parser.parse_args()
    print(
        json.dumps(
            status(args.database) if args.action == "status" else asyncio.run(operate(args)),
            ensure_ascii=False,
            indent=2,
        )
    )
