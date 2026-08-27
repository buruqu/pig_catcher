"""Synthetic outbox capacity, file-size and backup/recovery checks; never production."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.infrastructure.database import PigCatcherDatabase  # noqa: E402
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository  # noqa: E402
from pig_catcher.infrastructure.repositories.materials import MaterialRepository  # noqa: E402
from pig_catcher.services.achievements import AchievementService  # noqa: E402


async def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Use a fresh isolated evidence directory.")
    if not 1 <= args.players <= 10000 or not args.players <= args.facts <= 100000:
        raise ValueError("players 1..10000; facts players..100000")
    output.mkdir(parents=True)
    database = PigCatcherDatabase(output / "synthetic.sqlite3")
    await database.open()
    scope = ScopeKey("qq-official", "offline-capacity-only")
    now = "2026-08-28T00:00:00+00:00"
    players = []
    try:
        async with database.transaction() as session:
            for index in range(args.players):
                identity = CommandIdentity(scope, "offline", f"fixture-{index}", f"离线玩家{index}", "seed", "容量验收")
                players.append(identity.player_id)
                await FrameworkRepository().touch_identity(session, identity=identity, now=now)

            def facts():
                for index in range(args.facts):
                    player = players[index % len(players)]
                    if index < len(players):
                        event = "completed"
                        payload = {
                            "snapshot": {
                                "members": [
                                    {"pig_instance_id": player + "-pig", "template_id": "fixture-low", "rarity": 1}
                                ],
                                "region_id": "grassland",
                                "hours": 4,
                                "slot": 1,
                                "tool_id": "",
                            },
                            "progress": {"settled_hours": 4, "rewards": []},
                            "starts_ms": 0,
                        }
                    else:
                        event = f"block:{index}"
                        payload = {"effective_seconds_added": 14400, "forced": False, "hit": False}
                    yield (
                        hashlib.sha256(str(index).encode()).hexdigest(),
                        player,
                        scope.value,
                        "dispatch",
                        f"fixture-trip-{index}",
                        event,
                        1,
                        index,
                        json.dumps(payload, ensure_ascii=False),
                    )

            await session.executemany("INSERT INTO activity_facts VALUES(?,?,?,?,?,?,?,?,?)", facts())
        service = AchievementService(database)
        await service.initialize()
        times = []
        tracemalloc.start()
        while (await database.fetch_one("SELECT COUNT(*) FROM achievement_activity_queue WHERE processed_at IS NULL"))[
            0
        ]:
            before = time.perf_counter()
            await service.process_activity_facts(scope_id=scope.value, receipt_id=f"offline-drain-{len(times)}")
            times.append(round((time.perf_counter() - before) * 1000, 2))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        idle = []
        for _ in range(20):
            before = time.perf_counter()
            assert await service.process_activity_facts(scope_id=scope.value, receipt_id="already-settled") == ()
            idle.append((time.perf_counter() - before) * 1000)
        assert (
            await database.fetch_one(
                "SELECT COUNT(*) FROM achievement_unlocks WHERE achievement_id='dispatch-first-return'"
            )
        )[0] == args.players
        assert (await database.fetch_one("SELECT SUM(coin_balance) FROM players"))[0] == args.players * 200
        assert await database.integrity_check() == ("ok",)
        assert await database.fetch_all("PRAGMA foreign_key_check") == []
        async with database.transaction() as session:
            assert await MaterialRepository().reconcile(session) == []
        backup = output / "verified-backup.sqlite3"
        await database.backup_to(backup)
        restore = PigCatcherDatabase(backup)
        await restore.open()
        try:
            assert await restore.integrity_check() == ("ok",)
            assert (await restore.fetch_one("SELECT COUNT(*) FROM activity_facts"))[0] == args.facts
            assert (
                await AchievementService(restore).process_activity_facts(scope_id=scope.value, receipt_id="restored")
                == ()
            )
        finally:
            await restore.close()
        projection = await database.fetch_one(
            "SELECT SUM(LENGTH(state_json)),MAX(LENGTH(state_json)) FROM achievement_activity_state"
        )
        report = {
            "status": "passed",
            "synthetic_players": args.players,
            "facts": args.facts,
            "drain_calls": len(times),
            "drain_ms": {"min": min(times), "median": statistics.median(times), "max": max(times), "all": times},
            "idle_ms": {"median": round(statistics.median(idle), 2), "max": round(max(idle), 2)},
            "python_incremental_peak_bytes": peak,
            "database_bytes": database.path.stat().st_size,
            "verified_backup_bytes": backup.stat().st_size,
            "projection_characters": projection[0],
            "largest_player_projection_characters": projection[1],
            "backup_restore": "passed",
            "integrity": "ok",
            "foreign_keys": [],
            "material_reconciliation": [],
            "notes": (
                "Synthetic stress with tracemalloc overhead; "
                "not production workload, total process RAM or QQ latency."
            ),
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        tracemalloc.stop()
        await database.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--players", type=int, default=1000)
    parser.add_argument("--facts", type=int, default=10000)
    print(json.dumps(asyncio.run(run(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
