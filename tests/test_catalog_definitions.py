"""Formal 2B catalog metadata remains complete, stable and group-safe."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = PROJECT_ROOT / "catalogs" / "2b" / "catalog-definitions.json"


def _entries() -> list[dict[str, object]]:
    payload = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    return list(payload["entries"])


def test_formal_catalog_has_all_98_named_assets_and_stable_ids() -> None:
    entries = _entries()
    assert len(entries) == 98
    assert len({entry["template_id"] for entry in entries}) == 98
    assert len({entry["source_path"] for entry in entries}) == 98
    assert all(str(entry["description"]).strip() for entry in entries)
    pig_counts = Counter(
        int(entry["rarity"])
        for entry in entries
        if entry["kind"] == "pig"
    )
    food_counts = Counter(
        int(entry["rarity"])
        for entry in entries
        if entry["kind"] == "food"
    )
    assert pig_counts == {1: 20, 2: 20, 3: 19, 4: 13, 5: 9, 6: 2}
    assert food_counts == {1: 1, 2: 4, 3: 5, 4: 2, 5: 1, 6: 2}


def test_group_custom_assets_are_confined_and_keep_user_text() -> None:
    entries = _entries()
    group_entries = [
        entry
        for entry in entries
        if entry.get("group_scope_id")
    ]
    assert len(group_entries) == 4
    assert {entry["group_scope_id"] for entry in group_entries} == {"qq:1092931381"}
    descriptions = {
        entry["display_name"]: entry["description"]
        for entry in group_entries
    }
    assert descriptions["撅撅猪"] == "撅撅。"
    assert descriptions["1004猪鼻哥"] == "救我！！！！！晚上救来不及咯！"


def test_bandori_collaboration_mappings_use_official_profiles_and_five_slots() -> None:
    collabs = {
        entry["display_name"]: entry["collection"]
        for entry in _entries()
        if entry.get("collection")
    }
    assert {
        name: (value["character_name"], value["collection_name"])
        for name, value in collabs.items()
    } == {
        "星星猪": ("户山香澄", "Poppin'Party"),
        "巧克力猪": ("牛込里美", "Poppin'Party"),
        "红挑染猪": ("美竹兰", "Afterglow"),
        "粉音猪": ("千早爱音", "MyGO!!!!!"),
        "红茶猪": ("长崎素世", "MyGO!!!!!"),
    }
    assert all(value["total"] == 5 for value in collabs.values())
    assert all(
        str(value["official_profile_url"]).startswith("https://bang-dream.com/")
        for value in collabs.values()
    )
