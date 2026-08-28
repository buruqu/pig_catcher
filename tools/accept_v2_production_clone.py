"""Read-only online backup, two independent migrations and offline backfill audit.

The source connection is always mode=ro. All writes, including old-code rollback
checks, are confined to a fresh directory underneath this checkout's artifacts.
Reports contain aggregate counts/digests only, never player IDs or receipt text.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.migrations.v0044_round9_food_effects import _FROZEN_RULES  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402
from pig_catcher.services.achievements import AchievementService  # noqa: E402
from pig_catcher.version import PLUGIN_VERSION, SCHEMA_VERSION  # noqa: E402


def readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def backup_readonly(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError("Never overwrite an acceptance snapshot.")
    with closing(readonly(source)) as connection, closing(sqlite3.connect(target)) as destination:
        connection.backup(destination, pages=1024, sleep=0.01)


def quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def table_specs(connection: sqlite3.Connection) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    result = {}
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        columns = list(connection.execute(f"PRAGMA table_info({quoted(table)})"))
        order = tuple(row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"])
        result[table] = (tuple(row["name"] for row in columns), order or ("rowid",))
    return result


def ordered_rows(connection, table, columns, order):
    return connection.execute(
        f"SELECT {','.join(map(quoted, columns))} FROM {quoted(table)} ORDER BY {','.join(map(quoted, order))}"
    )


def digest_row(digest, row) -> None:
    encoded = json.dumps(tuple(row), ensure_ascii=True, separators=(",", ":"), default=lambda value: value.hex())
    digest.update(encoded.encode("utf-8"))
    digest.update(b"\n")


def database_facts(path: Path) -> dict:
    with closing(readonly(path)) as connection:
        checks = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = len(list(connection.execute("PRAGMA foreign_key_check")))
        if checks != ["ok"] or foreign_keys:
            raise AssertionError("Snapshot integrity or foreign-key validation failed.")
        tables = {
            table: connection.execute(f"SELECT COUNT(*) FROM {quoted(table)}").fetchone()[0]
            for table in table_specs(connection)
        }
        mismatch = connection.execute(
            "SELECT COUNT(*) FROM players p LEFT JOIN "
            "(SELECT player_id,SUM(amount) amount FROM currency_ledger GROUP BY player_id) l "
            "ON l.player_id=p.player_id WHERE p.coin_balance!=COALESCE(l.amount,0)"
        ).fetchone()[0]
        balance = connection.execute("SELECT COALESCE(SUM(coin_balance),0) FROM players").fetchone()[0]
        return {
            "schema": connection.execute("PRAGMA user_version").fetchone()[0],
            "tables": tables,
            "integrity": checks,
            "foreign_key_errors": foreign_keys,
            "ledger_mismatches": mismatch,
            "coin_balance_total": balance,
            "database_bytes": path.stat().st_size,
        }


def expected_legacy_row(table, old, new, *, mist_food_ids, migration_started):
    """Allow only the reviewed v44/v45 changes, keeping every other old field."""
    expected = dict(old)
    updated = False
    if table in {"food_templates", "food_instances"}:
        rule = _FROZEN_RULES.get(old["template_id"])
        active = table == "food_templates" or old["state"] in {"active", "locked-for-trade"}
        if rule is not None and active:
            expected["effect_id"] = rule[0]
            expected["effect_params_json"] = json.dumps(rule[1], sort_keys=True, separators=(",", ":"))
            updated = True
        if (
            table == "food_templates"
            and old["template_id"] == "food-r4-pig-paw-lemon-tea"
            and old["rarity"] == 4
            and old["effect_id"] == "next-pig-stature"
            and json.loads(old["effect_params_json"]).get("mode") == "mini"
            and json.loads(old["effect_params_json"]).get("strength") == 0.22
        ):
            params = json.loads(old["effect_params_json"])
            params["strength"] = 0.50
            expected["effect_params_json"] = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
            expected["template_version"] = old["template_version"] + 1
            updated = True
    if table == "player_food_effects":
        expiry = old["expires_at"]
        live = not expiry or datetime.fromisoformat(expiry.replace("Z", "+00:00")) > migration_started
        if (
            old["effect_id"] == "next-high-star-catch"
            and old["consumed_uses"] < old["granted_uses"]
            and live
            and old["source_food_instance_id"] in mist_food_ids
        ):
            expected["effect_id"] = "shuffled-catch-distribution"
            expected["params_json"] = json.dumps({"uses": old["granted_uses"]}, separators=(",", ":"))
            expected["expires_at"] = None
            updated = True
    if updated:
        changed_at = datetime.fromisoformat(new["updated_at"].replace("Z", "+00:00"))
        if changed_at.timestamp() + 1 < migration_started.timestamp():
            raise AssertionError("A reviewed migration update did not receive a fresh timestamp.")
        expected["updated_at"] = new["updated_at"]
    return expected, updated


def compare_legacy(snapshot: Path, migrated: Path, migration_started: datetime) -> dict:
    report = {}
    with closing(readonly(snapshot)) as original, closing(readonly(migrated)) as target:
        mist_ids = {
            row[0]
            for row in original.execute(
                "SELECT food_instance_id FROM food_instances "
                "WHERE template_id LIKE '%-mist-blue-keyboard-daifuku'"
            )
        }
        for table, (columns, order) in table_specs(original).items():
            if table == "schema_migrations":
                continue
            old_rows = ordered_rows(original, table, columns, order)
            new_rows = ordered_rows(target, table, columns, order)
            count = changed = 0
            digest = hashlib.sha256()
            for old, new in itertools.zip_longest(old_rows, new_rows):
                if old is None or new is None:
                    raise AssertionError(f"Migration changed the row count of {table}.")
                expected, transformed = expected_legacy_row(
                    table, old, new, mist_food_ids=mist_ids, migration_started=migration_started
                )
                # Compare every pre-upgrade field, not just a count or balance total.
                if expected != dict(new):
                    differing = [key for key in columns if expected[key] != new[key]]
                    raise AssertionError(f"Unexpected migration changes in {table}, row {count}, columns {differing}.")
                count += 1
                changed += int(transformed)
                digest_row(digest, old)
            report[table] = {"rows": count, "reviewed_effect_updates": changed, "source_sha256": digest.hexdigest()}
    return report


def complete_data_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with closing(readonly(path)) as connection:
        for table, (columns, order) in table_specs(connection).items():
            digest.update(table.encode())
            for row in ordered_rows(connection, table, columns, order):
                digest_row(digest, row)
    return digest.hexdigest()


def old_code_check(legacy_code: Path, database: Path, *, rejected: bool) -> dict:
    script = """
import asyncio,json,sys
from pathlib import Path
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.domain.errors import MigrationError
async def main():
    db=PigCatcherDatabase(Path(sys.argv[1]))
    try:
        await db.open()
    except MigrationError as error:
        if '高于当前插件支持' not in str(error):
            raise
        print(json.dumps({'rejected_new_schema':True}))
        return
    try:
        print(json.dumps({'rejected_new_schema':False,'schema':await db.schema_version(),
                          'integrity':list(await db.integrity_check())}))
    finally:
        await db.close()
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(database)],
        cwd=legacy_code,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    payload = json.loads(result.stdout)
    if payload["rejected_new_schema"] != rejected:
        raise AssertionError("Old-code rollback/downgrade gate did not behave as expected.")
    if not rejected and payload["integrity"] != ["ok"]:
        raise AssertionError("Restored 1.x snapshot failed old-code integrity validation.")
    return payload


async def backfill_audit(database: PigCatcherDatabase) -> dict:
    service = AchievementService(database)
    await service.initialize()
    rows = await database.fetch_all(
        "SELECT p.player_id,p.scope_id,p.platform_user_id,p.display_name "
        "FROM players p ORDER BY p.player_id"
    )
    before = database_facts(database.path)
    timings = []
    identities = []
    for index, row in enumerate(rows):
        identity = CommandIdentity(
            ScopeKey.parse(row["scope_id"]), "offline-clone", row["platform_user_id"],
            row["display_name"], f"offline-backfill-{index}", "离线验收群",
        )
        identities.append(identity)
        started = time.perf_counter()
        await service.overview(identity)
        timings.append((time.perf_counter() - started) * 1000)
        if (index + 1) % 50 == 0:
            print(f"Backfilled {index + 1}/{len(rows)} players in the isolated clone.", flush=True)
    after = database_facts(database.path)
    rewards = await database.fetch_all(
        "SELECT reward_type,COUNT(*) AS entries,SUM(quantity) AS quantity "
        "FROM achievement_reward_inventory GROUP BY reward_type ORDER BY reward_type"
    )
    reward_rows = [dict(row) for row in rewards]
    replay_before = await database.fetch_one(
        "SELECT (SELECT COUNT(*) FROM achievement_unlocks),(SELECT SUM(coin_balance) FROM players),"
        "(SELECT SUM(quantity) FROM achievement_reward_inventory),(SELECT COUNT(*) FROM currency_ledger)"
    )
    for identity in identities:
        await service.overview(identity)
    replay_after = await database.fetch_one(
        "SELECT (SELECT COUNT(*) FROM achievement_unlocks),(SELECT SUM(coin_balance) FROM players),"
        "(SELECT SUM(quantity) FROM achievement_reward_inventory),(SELECT COUNT(*) FROM currency_ledger)"
    )
    if tuple(replay_before) != tuple(replay_after):
        raise AssertionError("Repeated historical backfill issued duplicate rewards.")
    remaining = (await database.fetch_one(
        "SELECT COUNT(*) FROM achievement_backfill_state WHERE status!='completed'"
    ))[0]
    if remaining or after["ledger_mismatches"]:
        raise AssertionError("Historical backfill is incomplete or the currency ledger does not reconcile.")
    for table in ("pig_instances", "food_instances", "asset_transfer_events", "trade_offers", "command_receipts"):
        if before["tables"][table] != after["tables"][table]:
            raise AssertionError(f"Backfill unexpectedly altered {table} counts.")
    return {
        "players": len(rows), "remaining": remaining, "replay": "no duplicate rewards",
        "new_unlocks": after["tables"]["achievement_unlocks"] - before["tables"]["achievement_unlocks"],
        "coin_rewards": after["coin_balance_total"] - before["coin_balance_total"],
        "reward_inventory": reward_rows,
        "first_query_ms": {"median": round(statistics.median(timings), 2), "max": round(max(timings), 2)}
        if timings else {},
        "ledger_mismatches": after["ledger_mismatches"],
    }


async def run(args) -> dict:
    output = args.output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / "artifacts").resolve()):
        raise ValueError("Use an isolated directory underneath this checkout's artifacts/.")
    if output.exists() and not args.resume_before_import:
        raise FileExistsError("Use a fresh output, or explicitly verify/resume a pre-import migration run.")
    source = args.source_database.resolve(strict=True)
    if source.is_relative_to(output) or output.is_relative_to(source.parent):
        raise ValueError("Source and destination must not overlap.")
    if shutil.disk_usage(output.parent if output.parent.exists() else PROJECT_ROOT).free < source.stat().st_size * 7:
        raise RuntimeError("Insufficient free space for independently recoverable acceptance clones.")
    output.mkdir(parents=True, exist_ok=args.resume_before_import)
    snapshot = output / "pre-upgrade.sqlite3"
    if not args.resume_before_import:
        backup_readonly(source, snapshot)
    facts = database_facts(snapshot)
    if facts["schema"] != args.expected_source_schema or facts["ledger_mismatches"]:
        raise AssertionError(
            f"Baseline schema={facts['schema']}, ledger mismatches={facts['ledger_mismatches']}; source is unchanged."
        )
    print(f"Read-only snapshot: schema {facts['schema']}, {facts['tables']['players']} players.", flush=True)
    report = {"version": PLUGIN_VERSION, "target_schema": SCHEMA_VERSION, "source": facts, "migrations": []}
    for name in ("migration-a", "migration-b"):
        data = output / name
        data.mkdir(exist_ok=args.resume_before_import)
        path = data / "pig_catcher.sqlite3"
        if not args.resume_before_import:
            backup_readonly(snapshot, path)
        else:
            # This bounded resume is deliberately before asset import/backfill.
            # Comparing every old column below rejects a partly-mutated run.
            with closing(readonly(path)) as existing:
                if existing.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise AssertionError("Cannot resume a migration at a different schema version.")
        database = PigCatcherDatabase(path)
        started = time.perf_counter()
        timestamp = datetime.now(UTC)
        if args.resume_before_import:
            with closing(readonly(path)) as existing:
                applied = existing.execute("SELECT applied_at FROM schema_migrations WHERE version=35").fetchone()[0]
                timestamp = datetime.fromisoformat(applied.replace("Z", "+00:00"))
        await database.open()
        await database.close()
        seconds = time.perf_counter() - started
        preservation = compare_legacy(snapshot, path, timestamp)
        before_reopen = complete_data_digest(path)
        await database.open()
        await database.close()
        if complete_data_digest(path) != before_reopen:
            raise AssertionError("Reopening an upgraded clone changed persistent data.")
        migrated_facts = database_facts(path)
        if migrated_facts["schema"] != SCHEMA_VERSION or migrated_facts["ledger_mismatches"]:
            raise AssertionError("Migrated clone did not converge to the current schema/ledger.")
        report["migrations"].append({
            "name": name, "seconds": round(seconds, 3), "facts": migrated_facts,
            "legacy_preservation": preservation, "reopen_idempotent": True,
            "mode": "verify_existing_migration" if args.resume_before_import else "fresh_migration",
        })
        print(f"{name}: migrated and compared every legacy field; {seconds:.2f}s.", flush=True)
    a_data = output / "migration-a"
    a_path = a_data / "pig_catcher.sqlite3"
    report["old_code_rejects_upgraded_clone"] = old_code_check(args.legacy_code.resolve(), a_path, rejected=True)
    restored = output / "restored-v1.sqlite3"
    if not args.resume_before_import:
        backup_readonly(snapshot, restored)
    before_restore = complete_data_digest(restored)
    report["rollback"] = old_code_check(args.legacy_code.resolve(), restored, rejected=False)
    if complete_data_digest(restored) != before_restore:
        raise AssertionError("Old-code recovery modified the pre-upgrade snapshot data.")
    database = PigCatcherDatabase(a_path)
    await database.open()
    try:
        catalog = AssetCatalogService(
            database, AssetCatalogStorage(a_data), min_image_side=180, max_image_bytes=12 * 1024 * 1024
        )
        imported = await catalog.import_manifest(args.manifest.resolve(strict=True))
        report["current_asset_import"] = {"entries": imported.entry_count, "catalog_hash": imported.catalog_hash}
        report["backfill"] = await backfill_audit(database)
        await database.backup_to(output / "verified-v2-backup.sqlite3")
    finally:
        await database.close()
    report["final"] = database_facts(a_path)
    report["status"] = "passed"
    report["boundary"] = "Offline clones only; production read-only; no QQ send or runtime deployment."
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--legacy-code", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "asset_library/current/assets.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-source-schema", type=int, default=34)
    parser.add_argument("--resume-before-import", action="store_true")
    report = asyncio.run(run(parser.parse_args()))
    print(json.dumps({"status": report["status"], "backfill": report["backfill"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
