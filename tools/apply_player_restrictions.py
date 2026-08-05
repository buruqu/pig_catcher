"""为精确群成员批量写入可到期的社交与抓猪额度处罚。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pig_catcher.infrastructure import PigCatcherDatabase  # noqa: E402
from pig_catcher.services import RestrictionAdminService  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按精确群号和平台用户 ID 应用一批可自动到期的玩家处罚。"
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--user-id", action="append", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--catch-window-limit", type=int, default=1)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--source", default="manual-moderation")
    parser.add_argument("--created-by", default="plugin-operator")
    return parser


def _online_backup(source_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return destination


async def _run(args: argparse.Namespace) -> dict[str, object]:
    data_dir = Path(args.data_dir).resolve()
    database_path = data_dir / "pig_catcher.sqlite3"
    if not database_path.is_file():
        raise RuntimeError(f"找不到抓猪生产数据库：{database_path}")

    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    backup_path = data_dir / "backups" / f"pig_catcher-pre-player-restrictions-{stamp}.sqlite3"
    _online_backup(database_path, backup_path)

    database = PigCatcherDatabase(database_path)
    await database.open()
    try:
        scope_rows = await database.fetch_all(
            """
            SELECT scope_id, platform, group_name
            FROM scopes
            WHERE group_id = ?
            ORDER BY scope_id
            """,
            (str(args.group_id).strip(),),
        )
        if len(scope_rows) != 1:
            raise RuntimeError(
                f"群号必须精确匹配一个现有范围，实际匹配 {len(scope_rows)} 个。"
            )
        scope = scope_rows[0]
        scope_id = str(scope["scope_id"])
        platform = str(scope["platform"])
        player_ids = tuple(
            f"{scope_id}:{str(user_id).strip()}"
            for user_id in args.user_id
            if str(user_id).strip()
        )
        result = await RestrictionAdminService(database).apply_batch(
            scope_id=scope_id,
            player_ids=player_ids,
            duration=timedelta(days=int(args.days)),
            catch_window_limit=int(args.catch_window_limit),
            reason=str(args.reason).strip(),
            source=str(args.source).strip(),
            created_by=str(args.created_by).strip(),
            backup_path=backup_path,
        )
        return {
            "batch_id": result.batch_id,
            "scope_id": result.scope_id,
            "platform": platform,
            "group_name": str(scope["group_name"]),
            "display_names": list(result.display_names),
            "starts_at": result.starts_at,
            "gift_transfer_ban_expires_at": None,
            "trade_ban_expires_at": None,
            "catch_limit_expires_at": result.catch_limit_expires_at,
            "catch_window_limit": result.catch_window_limit,
            "cancelled_pending_trades": result.cancelled_pending_trades,
            "backup_path": str(result.backup_path.resolve()),
            "schema_version": await database.schema_version(),
            "integrity_check": list(await database.integrity_check()),
        }
    finally:
        await database.close()


def main() -> int:
    args = _parser().parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
