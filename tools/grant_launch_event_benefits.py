"""Preview or atomically grant the week-one launch coupon bonus."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.config.model import LaunchCampaignSection  # noqa: E402
from pig_catcher.domain.item_bag import CODE_CHANGE_COUPON  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.services.launch_campaign import LaunchCampaignService  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


async def _preview(database: PigCatcherDatabase, config: LaunchCampaignSection) -> dict[str, object]:
    source_id = f"{config.campaign_id}:{CODE_CHANGE_COUPON}"
    rows = await database.fetch_all(
        "SELECT p.scope_id,COUNT(*) AS registered,"
        "SUM(CASE WHEN g.player_id IS NULL THEN 0 ELSE 1 END) AS granted "
        "FROM players p LEFT JOIN reward_coupon_grants g ON g.player_id=p.player_id "
        "AND g.source_kind='launch-campaign' AND g.source_id=? AND g.coupon_id=? "
        "GROUP BY p.scope_id ORDER BY p.scope_id",
        (source_id, CODE_CHANGE_COUPON),
    )
    scopes = {
        str(row["scope_id"]): {
            "registered": int(row["registered"]),
            "already_granted": int(row["granted"]),
            "missing": int(row["registered"]) - int(row["granted"]),
        }
        for row in rows
    }
    return {
        "campaign_id": config.campaign_id,
        "coupon_id": CODE_CHANGE_COUPON,
        "quantity_per_player": int(config.starter_code_change_tickets),
        "registered_players": sum(int(value["registered"]) for value in scopes.values()),
        "already_granted": sum(int(value["already_granted"]) for value in scopes.values()),
        "missing": sum(int(value["missing"]) for value in scopes.values()),
        "scopes": scopes,
    }


async def _run(args: argparse.Namespace) -> None:
    database_path = args.database.resolve(strict=True)
    with args.config.resolve(strict=True).open("rb") as handle:
        document = tomllib.load(handle)
    config = LaunchCampaignSection.model_validate(document.get("launch_campaign", {}))
    database = PigCatcherDatabase(database_path)
    await database.open()
    try:
        before = await _preview(database, config)
        result: dict[str, object] = {"mode": "preview", "before": before}
        if args.execute:
            if args.backup is None:
                raise ValueError("执行补发必须显式提供 --backup 备份路径。")
            backup = args.backup.resolve()
            await database.backup_to(backup)
            service = LaunchCampaignService(database, config)
            summary = await service.grant_code_change_bonus_to_registered_players(
                actor_user_id=args.actor
            )
            after = await _preview(database, config)
            result = {
                "mode": "execute",
                "backup": str(backup),
                "backup_sha256": _sha256(backup),
                "before": before,
                "grant": {
                    "registered_players": summary.registered_players,
                    "already_granted": summary.already_granted,
                    "newly_granted": summary.newly_granted,
                    "quantity_per_player": summary.quantity_per_player,
                    "scope_counts": summary.scope_counts,
                },
                "after": after,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        await database.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="预览或幂等补发第一期活动编号修改券。")
    parser.add_argument("database", type=Path)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.toml")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--actor", default="system:week1-launch")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.execute and args.backup is None:
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
        raise SystemExit(f"执行补发必须提供 --backup，例如 pre-week1-code-coupon-{stamp}.sqlite3")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
