"""Review or mechanically sync only the 28 approved food-rule fields, offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pig_catcher.domain.food_effects import resolve_food_effect  # noqa: E402
from pig_catcher.domain.round9_food_rules import reviewed_food_revisions  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Update the two version-controlled JSON catalogs")
    args = parser.parse_args()
    revisions = reviewed_food_revisions()
    staged = []
    for relative in ("catalogs/formal/pig-and-food-definitions.json", "asset_library/current/assets.json"):
        path = ROOT / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        matched = set()
        for entry in data["entries"]:
            template_id = entry["template_id"]
            if template_id not in revisions:
                continue
            if template_id in matched or entry["kind"] != "food" or entry["rarity"] < 4:
                raise ValueError(f"Unexpected food revision target: {template_id}")
            effect, params = revisions[template_id]
            resolve_food_effect(effect, params)
            entry["effect_id"], entry["effect_params"] = effect, params
            matched.add(template_id)
        if matched != set(revisions):
            raise ValueError(f"Missing revision IDs in {relative}: {sorted(set(revisions) - matched)}")
        staged.append((path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"))
        print(f"{relative}: validated {len(matched)} food rules; all other fields preserved")
    if args.write:
        for path, serialized in staged:
            path.write_text(serialized, encoding="utf-8", newline="\n")
        print("Updated version-controlled catalogs only; no database or production access.")


if __name__ == "__main__":
    main()
