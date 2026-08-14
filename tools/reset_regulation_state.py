"""Back up the live database, then dismiss and reset all regulation state."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIBOT_ROOT = PROJECT_ROOT.parents[1]
DEFAULT_DATA_DIR = MAIBOT_ROOT / "data" / "plugins" / "local.pig-catcher"
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.config.model import RegulationSection  # noqa: E402
from pig_catcher.infrastructure import PigCatcherDatabase  # noqa: E402
from pig_catcher.services import RegulationService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "预览监管状态；只有显式传入 --execute 才会先在线备份，再撤销全部案件并清零分数。"
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--actor", default="local-codex")
    parser.add_argument(
        "--reason",
        default="自动监管灵敏度调整：撤销全部监管单并重置累计状态",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认执行生产写入；不提供时只输出只读预览。",
    )
    return parser.parse_args()


async def _state_counts(database: PigCatcherDatabase) -> dict[str, object]:
    case_row = await database.fetch_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status NOT IN ('closed', 'dismissed') THEN 1 ELSE 0 END)
                   AS active,
               COALESCE(MAX(score), 0) AS max_score,
               COALESCE(SUM(score), 0) AS score_sum
        FROM anti_abuse_cases
        """
    )
    member_row = await database.fetch_one(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(incident_count), 0) AS incident_sum,
               SUM(CASE WHEN warning_served_at IS NOT NULL THEN 1 ELSE 0 END)
                   AS warned
        FROM anti_abuse_case_members
        """
    )
    notice_row = await database.fetch_one(
        """
        SELECT SUM(CASE WHEN status IN ('pending', 'claimed') THEN 1 ELSE 0 END)
                   AS deliverable,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM anti_abuse_notices
        """
    )
    hold_row = await database.fetch_one(
        """
        SELECT SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active
        FROM anti_abuse_holds
        """
    )
    return {
        "cases": {
            "total": int(case_row["total"] or 0) if case_row else 0,
            "active": int(case_row["active"] or 0) if case_row else 0,
            "max_score": int(case_row["max_score"] or 0) if case_row else 0,
            "score_sum": int(case_row["score_sum"] or 0) if case_row else 0,
        },
        "members": {
            "total": int(member_row["total"] or 0) if member_row else 0,
            "incident_sum": int(member_row["incident_sum"] or 0) if member_row else 0,
            "warned": int(member_row["warned"] or 0) if member_row else 0,
        },
        "notices": {
            "deliverable": int(notice_row["deliverable"] or 0) if notice_row else 0,
            "failed": int(notice_row["failed"] or 0) if notice_row else 0,
        },
        "holds": {
            "active": int(hold_row["active"] or 0) if hold_row else 0,
        },
    }


async def run(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.resolve()
    database = PigCatcherDatabase(data_dir / args.database_filename)
    await database.open()
    try:
        before = await _state_counts(database)
        if not args.execute:
            return {
                "status": "preview",
                "executed": False,
                "database": str(database.path.resolve()),
                "before": before,
                "hint": "确认后重新运行并传入 --execute。",
            }
        service = RegulationService(database, RegulationSection())
        result = await service.backup_and_reset_all_state(
            data_dir=data_dir,
            actor_user_id=str(args.actor).strip(),
            reason=str(args.reason).strip(),
            source="operator-cli",
        )
        foreign_key_rows = await database.fetch_all("PRAGMA foreign_key_check")
        return {
            "status": "ok",
            "executed": True,
            "created_at": result.created_at,
            "case_count": result.case_count,
            "previously_active_case_count": result.previously_active_case_count,
            "reset_member_count": result.reset_member_count,
            "invalidated_notice_count": result.invalidated_notice_count,
            "released_hold_count": result.released_hold_count,
            "reset_event_ids": list(result.reset_event_ids),
            "audit_event_ids": list(result.audit_event_ids),
            "backup_path": str(result.backup_path),
            "before": before,
            "after": await _state_counts(database),
            "integrity_check": list(await database.integrity_check()),
            "foreign_key_check_count": len(foreign_key_rows),
        }
    finally:
        await database.close()


def main() -> None:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
