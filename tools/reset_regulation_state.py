"""Back up the live database, then dismiss and reset regulation state."""

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
            "预览监管状态；只有显式传入 --execute 才会先在线备份，再撤销案件并清零分数。"
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--actor", default="local-codex")
    parser.add_argument(
        "--reason",
        default="",
        help="写入审计的操作原因；留空时按全局或指定群范围生成默认原因。",
    )
    parser.add_argument(
        "--scope-id",
        default="",
        help=(
            "只处理这一精确群范围，例如 "
            "qq-official:9EA2810F378FBD7DC3219C56CEAB3520；留空才处理全部范围。"
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认执行生产写入；不提供时只输出只读预览。",
    )
    return parser.parse_args()


async def _state_counts(
    database: PigCatcherDatabase,
    *,
    scope_id: str | None = None,
) -> dict[str, object]:
    where = "WHERE incident.scope_id = ?" if scope_id else ""
    parameters = (scope_id,) if scope_id else ()
    case_row = await database.fetch_one(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status NOT IN ('closed', 'dismissed') THEN 1 ELSE 0 END)
                   AS active,
               COALESCE(MAX(score), 0) AS max_score,
               COALESCE(SUM(score), 0) AS score_sum
        FROM anti_abuse_cases AS incident
        {where}
        """,
        parameters,
    )
    member_row = await database.fetch_one(
        f"""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(member.incident_count), 0) AS incident_sum,
               SUM(CASE WHEN member.warning_served_at IS NOT NULL THEN 1 ELSE 0 END)
                   AS warned
        FROM anti_abuse_case_members AS member
        JOIN anti_abuse_cases AS incident ON incident.case_id = member.case_id
        {where}
        """,
        parameters,
    )
    notice_row = await database.fetch_one(
        f"""
        SELECT SUM(CASE WHEN notice.status IN ('pending', 'claimed') THEN 1 ELSE 0 END)
                   AS deliverable,
               SUM(CASE WHEN notice.status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM anti_abuse_notices AS notice
        JOIN anti_abuse_cases AS incident ON incident.case_id = notice.case_id
        {where}
        """,
        parameters,
    )
    hold_row = await database.fetch_one(
        f"""
        SELECT SUM(CASE WHEN hold.status = 'active' THEN 1 ELSE 0 END) AS active
        FROM anti_abuse_holds AS hold
        JOIN anti_abuse_cases AS incident ON incident.case_id = hold.case_id
        {where}
        """,
        parameters,
    )
    return {
        "scope_id": scope_id or "all",
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
    scope_id = str(args.scope_id or "").strip() or None
    database = PigCatcherDatabase(data_dir / args.database_filename)
    await database.open()
    try:
        before = await _state_counts(database, scope_id=scope_id)
        if not args.execute:
            return {
                "status": "preview",
                "executed": False,
                "database": str(database.path.resolve()),
                "scope_id": scope_id or "all",
                "before": before,
                "hint": "确认后重新运行并传入 --execute。",
            }
        service = RegulationService(database, RegulationSection())
        if scope_id is None:
            result = await service.backup_and_reset_all_state(
                data_dir=data_dir,
                actor_user_id=str(args.actor).strip(),
                reason=str(args.reason).strip(),
                source="operator-cli",
            )
        else:
            result = await service.backup_and_reset_scope_state(
                data_dir=data_dir,
                scope_id=scope_id,
                actor_user_id=str(args.actor).strip(),
                reason=str(args.reason).strip(),
                source="operator-cli",
            )
        foreign_key_rows = await database.fetch_all("PRAGMA foreign_key_check")
        return {
            "status": "ok",
            "executed": True,
            "scope_id": scope_id or "all",
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
            "after": await _state_counts(database, scope_id=scope_id),
            "integrity_check": list(await database.integrity_check()),
            "foreign_key_check_count": len(foreign_key_rows),
        }
    finally:
        await database.close()


def main() -> None:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
