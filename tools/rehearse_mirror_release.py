"""Read-only source backup and isolated migration; never opens a QQ connection."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pig_catcher.infrastructure.database import PigCatcherDatabase
from tools.rehearse_social_rewards_release import inspect, snapshot


async def main():
    source = Path("C:/Users/Administrator/MaiBot/data/plugins/local.pig-catcher/pig_catcher.sqlite3")
    output = Path("D:/MaiBotArchives/pig_catcher/preview-prep/mirror-20260908/migration")
    output.mkdir(parents=True, exist_ok=False)
    before = snapshot(source, output / "before-schema67.sqlite3")
    target = output / "isolated-schema68.sqlite3"
    shutil.copyfile(output / "before-schema67.sqlite3", target)
    database = PigCatcherDatabase(target)
    await database.open()
    await database.close()
    after = inspect(target)
    changes = {
        name: (count, after["counts"].get(name))
        for name, count in before["counts"].items()
        if name != "schema_migrations" and count != after["counts"].get(name)
    }
    assert not changes and after["schema"] == 68
    assert after["quick_check"] == "ok" and not after["foreign_key_errors"] and not after["ledger_mismatches"]
    report = {"before": before, "after": after, "old_table_changes": changes, "source_read_only": True}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "schema": [before["schema"], after["schema"]],
                "old_table_changes": changes,
                "foreign_key_errors": 0,
                "ledger_mismatches": 0,
                "output": str(output),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
