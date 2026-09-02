"""Prepare a fresh 2.0 data directory without touching the live MaiBot data."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import tomllib
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.build_production_release import (  # noqa: E402
    REQUIRED_SCOPE_IDS,
    verify_production_package,
)

PRESERVED_RESTRICTIONS = {
    "plugin-access-ban",
    "gift-transfer-ban",
    "trade-ban",
}


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _export_operational_metadata(database: Path, now: str) -> dict[str, object]:
    with closing(_readonly(database)) as connection:
        scope_rows = connection.execute(
            "SELECT scope_id,platform,group_id,group_name,stream_id,enabled,created_at,updated_at "
            "FROM scopes WHERE scope_id IN ({}) ORDER BY scope_id".format(
                ",".join("?" for _ in REQUIRED_SCOPE_IDS)
            ),
            REQUIRED_SCOPE_IDS,
        ).fetchall()
        restriction_rows = connection.execute(
            """
            SELECT r.restriction_id,r.restriction_type,r.limit_value,r.starts_at,
                   r.expires_at,r.reason,r.source,r.created_by,r.created_at,r.updated_at,
                   p.player_id,p.scope_id,p.platform_user_id,p.display_name
            FROM player_restrictions r
            JOIN players p ON p.player_id=r.player_id
            WHERE r.restriction_type IN ('plugin-access-ban','gift-transfer-ban','trade-ban')
              AND r.starts_at <= ?
              AND r.expires_at IS NULL
            ORDER BY p.scope_id,p.platform_user_id,r.restriction_type
            """,
            (now,),
        ).fetchall()
        return {
            "scopes": [dict(row) for row in scope_rows],
            "restrictions": [dict(row) for row in restriction_rows],
            "source_schema": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            "source_quick_check": str(connection.execute("PRAGMA quick_check").fetchone()[0]),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def prepare(args: argparse.Namespace) -> dict[str, object]:
    package = args.package.resolve(strict=True)
    verify_production_package(package)
    source_data = args.source_data.resolve(strict=True)
    source_database = source_data / args.database_filename
    if not source_database.is_file():
        raise FileNotFoundError(f"找不到正式数据库：{source_database}")
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"新数据输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True, exist_ok=False)
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    metadata = _export_operational_metadata(source_database, now)
    if metadata["source_quick_check"] != "ok":
        raise RuntimeError("正式旧库 quick_check 未通过，拒绝准备发布数据。")

    # Import the exact code contained in the already verified release package.
    # A rehearsal is read-only with respect to that package: Python's default
    # bytecode cache would otherwise invalidate the signed file inventory.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(package))
    from pig_catcher.assets import AssetCatalogStorage
    from pig_catcher.infrastructure import PigCatcherDatabase
    from pig_catcher.services import AssetCatalogService

    with (package / "config.toml").open("rb") as handle:
        config = tomllib.load(handle)
    database_path = output / args.database_filename
    database = PigCatcherDatabase(database_path)
    await database.open()
    try:
        assets = config["assets"]
        service = AssetCatalogService(
            database,
            AssetCatalogStorage(output),
            min_image_side=int(assets["min_image_side"]),
            max_image_bytes=int(assets["max_image_bytes"]),
            max_animation_frames=int(assets["max_animation_frames"]),
            max_animation_duration_ms=int(assets["max_animation_duration_ms"]),
        )
        catalog = await service.import_manifest(package / "asset_library/current/assets.json")
        async with database.transaction() as session:
            for scope_id in REQUIRED_SCOPE_IDS:
                platform, group_id = scope_id.split(":", 1)
                await session.execute(
                    """
                    INSERT INTO scopes(scope_id,platform,group_id,group_name,stream_id,enabled,created_at,updated_at)
                    VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(scope_id) DO NOTHING
                    """,
                    (scope_id, platform, group_id, "", "", now, now),
                )
            for row in metadata["scopes"]:
                await session.execute(
                    """
                    UPDATE scopes SET group_name=?,stream_id=?,enabled=1,updated_at=?
                    WHERE scope_id=?
                    """,
                    (row["group_name"], row["stream_id"], now, row["scope_id"]),
                )
            restored_players: set[str] = set()
            for row in metadata["restrictions"]:
                player_id = str(row["player_id"])
                if player_id not in restored_players:
                    await session.execute(
                        """
                        INSERT INTO players(
                            player_id,scope_id,platform_user_id,display_name,
                            coin_balance,experience,created_at,updated_at
                        ) VALUES(?,?,?,?,0,0,?,?)
                        """,
                        (
                            player_id, row["scope_id"], row["platform_user_id"],
                            row["display_name"], now, now,
                        ),
                    )
                    await session.execute(
                        "INSERT INTO player_statistics(player_id,updated_at) VALUES(?,?)",
                        (player_id, now),
                    )
                    restored_players.add(player_id)
                await session.execute(
                    """
                    INSERT INTO player_restrictions(
                        restriction_id,player_id,restriction_type,limit_value,
                        starts_at,expires_at,reason,source,created_by,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["restriction_id"], player_id, row["restriction_type"],
                        row["limit_value"], row["starts_at"], row["expires_at"],
                        row["reason"], row["source"], row["created_by"],
                        row["created_at"], row["updated_at"],
                    ),
                )
        counts = await database.fetch_one(
            """
            SELECT
              (SELECT COUNT(*) FROM players) players,
              (SELECT COUNT(*) FROM pig_instances) pigs,
              (SELECT COUNT(*) FROM food_instances) foods,
              (SELECT COUNT(*) FROM currency_ledger) ledger,
              (SELECT COUNT(*) FROM launch_campaign_grants) launch_grants,
              (SELECT COUNT(*) FROM player_restrictions) restrictions,
              (SELECT COUNT(*) FROM pig_templates) pig_templates,
              (SELECT COUNT(*) FROM food_templates) food_templates,
              (SELECT COUNT(*) FROM scopes WHERE stream_id<>'') scopes_with_stream
            """
        )
        integrity = list(await database.integrity_check())
        foreign_keys = await database.fetch_all("PRAGMA foreign_key_check")
        schema = await database.schema_version()
    finally:
        await database.close()
    count_map = {key: int(counts[key]) for key in counts.keys()}
    if count_map["pigs"] or count_map["foods"] or count_map["ledger"] or count_map["launch_grants"]:
        raise AssertionError("新库混入旧玩法资产或预发奖励。")
    if count_map["players"] != count_map["restrictions"] and count_map["restrictions"]:
        # A player can have more than one blacklist, so only disallow orphan-like inflation.
        if count_map["players"] > count_map["restrictions"]:
            raise AssertionError("新库包含非黑名单玩家。")
    if integrity != ["ok"] or foreign_keys:
        raise AssertionError("新库完整性或外键检查失败。")
    report = {
        "prepared_at": now,
        "source_schema": metadata["source_schema"],
        "target_schema": schema,
        "catalog_id": catalog.catalog_id,
        "catalog_hash": catalog.catalog_hash,
        "catalog_entries": catalog.entry_count,
        "counts": count_map,
        "preserved_scope_ids": [row["scope_id"] for row in metadata["scopes"]],
        "preserved_permanent_restrictions": len(metadata["restrictions"]),
        "reset_semantics": "fresh-database-no-gameplay-state",
        "integrity_check": integrity,
        "foreign_key_errors": len(foreign_keys),
        "database_bytes": database_path.stat().st_size,
        "database_sha256": _sha256(database_path),
    }
    (output / "V2_FRESH_DATA_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备抓猪 2.0 全群删档后的全新数据目录。")
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    return parser.parse_args()


def main() -> None:
    report = asyncio.run(prepare(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
