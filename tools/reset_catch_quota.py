"""Safely reset the current catch-quota window for exactly one group."""

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

from pig_catcher.infrastructure import PigCatcherDatabase  # noqa: E402
from pig_catcher.services import CatchQuotaResetService  # noqa: E402


def _refresh_hours(value: str) -> list[int]:
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "刷新小时必须是逗号分隔的整数，例如 0,9,12,19"
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="先在线备份，再精准重置一个群的当前抓猪时段额度。",
    )
    parser.add_argument("--group-id", required=True, help="精确群号，不含 qq: 前缀")
    parser.add_argument("--platform", default="qq")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--refresh-hours", type=_refresh_hours, default=[0, 9, 12, 19])
    parser.add_argument("--window-limit", type=int, default=5)
    parser.add_argument("--actor", default="local-codex")
    return parser.parse_args()


async def reset_quota(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.resolve()
    database = PigCatcherDatabase(data_dir / args.database_filename)
    await database.open()
    try:
        service = CatchQuotaResetService(
            database,
            refresh_hours=args.refresh_hours,
            timezone_name="Asia/Shanghai",
            window_limit=args.window_limit,
        )
        result = await service.backup_and_reset_current_window(
            data_dir=data_dir,
            group_id=str(args.group_id).strip(),
            platform=str(args.platform).strip(),
            actor_user_id=str(args.actor).strip(),
            source="operator-cli",
        )
        return {
            "status": "ok",
            "scope_id": result.scope_id,
            "window_start": result.window.start.isoformat(),
            "window_end": result.window.end.isoformat(),
            "cleared_catches": result.cleared_catches,
            "affected_players": result.affected_players,
            "audit_event_id": result.audit_event_id,
            "backup_path": str(result.backup_path),
            "integrity_check": list(await database.integrity_check()),
        }
    finally:
        await database.close()


def main() -> None:
    result = asyncio.run(reset_quota(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
