"""Formal 2B catalog metadata remains complete, stable and group-safe."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = PROJECT_ROOT / "catalogs" / "formal" / "pig-and-food-definitions.json"


def _entries() -> list[dict[str, object]]:
    payload = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    return list(payload["entries"])


def test_public_catalog_has_all_110_named_assets_and_stable_ids() -> None:
    entries = _entries()
    assert len(entries) == 110
    assert len({entry["template_id"] for entry in entries}) == 110
    assert len({entry["source_path"] for entry in entries}) == 110
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
    assert pig_counts == {1: 20, 2: 20, 3: 20, 4: 17, 5: 12}
    assert food_counts == {1: 2, 2: 5, 3: 5, 4: 5, 5: 4}


def test_high_rarity_food_effects_cover_new_gameplay_families() -> None:
    foods = {
        entry["display_name"]: entry
        for entry in _entries()
        if entry["kind"] == "food"
    }
    assert foods["猪咪虾寿司"]["effect_id"] == "next-catch-quality"
    assert foods["猪猪玉子烧"]["effect_id"] == "next-cook-quality"
    assert foods["猪寿司拼盘"]["effect_params"] == {"count": 2}
    assert foods["一猪六吃"]["effect_params"] == {"six_star_percent": 20}
    assert foods["一盒油炸猪"]["effect_params"] == {"count": 1}
    assert foods["猪猪白菜炖粉条"]["effect_params"] == {"shift_percent": 12}


def test_public_catalog_contains_no_private_group_entries() -> None:
    entries = _entries()
    assert all(not entry.get("group_scope_id") for entry in entries)
    assert all(int(entry["rarity"]) <= 5 for entry in entries)


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
        "兔吉猪": ("花园多惠", "Poppin'Party"),
        "巧克力猪": ("牛込里美", "Poppin'Party"),
        "面包鼓猪": ("山吹沙绫", "Poppin'Party"),
        "傲娇猪": ("市谷有咲", "Poppin'Party"),
        "红挑染猪": ("美竹兰", "Afterglow"),
        "摩卡猪": ("青叶摩卡", "Afterglow"),
        "大绯猪": ("上原绯玛丽", "Afterglow"),
        "巴巴猪": ("宇田川巴", "Afterglow"),
        "鸫鸫猪": ("羽泽鸫", "Afterglow"),
        "粉音猪": ("千早爱音", "MyGO!!!!!"),
        "红茶猪": ("长崎素世", "MyGO!!!!!"),
    }
    assert all(value["total"] == 5 for value in collabs.values())
    assert all(
        str(value["official_profile_url"]).startswith("https://bang-dream.com/")
        for value in collabs.values()
    )
    afterglow_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Afterglow"
    }
    assert afterglow_slots == {1, 2, 3, 4, 5}
    poppin_party_slots = {
        int(value["slot"])
        for value in collabs.values()
        if value["collection_name"] == "Poppin'Party"
    }
    assert poppin_party_slots == {1, 2, 3, 4, 5}


def test_new_pigs_keep_reviewed_descriptions_and_rarities() -> None:
    pigs = {
        entry["display_name"]: entry
        for entry in _entries()
        if entry["kind"] == "pig"
    }
    assert pigs["猪纵连"]["rarity"] == 3
    assert pigs["猪纵连"]["description"] == (
        "三只小猪首尾相接排成一列，队伍一旦启动就越连越长，谁先掉队谁负责请全队加餐。"
    )
    assert pigs["面包鼓猪"]["rarity"] == 4
    assert pigs["面包鼓猪"]["description"] == (
        "扎着山吹沙绫的侧马尾，一边守着面包一边敲响小鼓；"
        "总把大家照顾得稳稳当当，散场后还会记得给全队留一份加餐。"
    )
    assert pigs["兔吉猪"]["rarity"] == 4
    assert pigs["兔吉猪"]["description"] == (
        "学着花园多惠抱起蓝色吉他，头上的小花和身后的兔子一起听它即兴；"
        "想法总是自由跳脱，弹起琴来却比谁都认真。"
    )
    assert pigs["傲娇猪"]["rarity"] == 5
    assert pigs["傲娇猪"]["description"] == (
        "借来市谷有咲的双马尾，在键盘、乐谱和盆栽之间忙得团团转；"
        "嘴上嫌麻烦，伙伴一开口却总是第一个把演出撑起来。"
    )
