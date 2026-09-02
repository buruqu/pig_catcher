from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--schema", type=int, required=True)
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    connection = sqlite3.connect(database)
    try:
        schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "players",
                "pig_instances",
                "food_instances",
                "currency_ledger",
                "launch_campaign_grants",
            )
        }
    finally:
        connection.close()
    result = {"schema": schema, "quick_check": quick_check, "counts": counts}
    print(json.dumps(result, ensure_ascii=False))
    if schema != args.schema or quick_check != "ok" or any(counts.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
