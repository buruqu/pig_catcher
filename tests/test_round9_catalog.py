"""第九期素材、全猪标签与物理范围的离线发布契约。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "asset_library" / "current"
DEFINITIONS = ROOT / "catalogs" / "formal" / "pig-and-food-definitions.json"
SCOPES = (
    "qq:1092931381",
    "qq-official:5E5854406D0297D6FEAE696A13E3A339",
    "qq:237716658",
    "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
)
RARITY_NAMES = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
# These hashes were taken from the read-only user drop during visual review.
# Keep the acceptance baseline independent of both runtime data and build reports.
EXPECTED_ASSETS: dict[str, tuple[str, str]] = {
    "pig-r1-spring-grove": (
        "一星猪猪/春猪.png",
        "353e9e0bc01e9fb3d85a09f9113856c342ec4c71d6df25811c9729e25025c354",
    ),
    "pig-r1-kitty": (
        "一星猪猪/猪咪.png",
        "f59e64aadcaf2f7f7fa5613f1bea347e62579cb738a14268787a498d390be834",
    ),
    "pig-r1-coffee-break": (
        "一星猪猪/猪喝咖啡.png",
        "c669f3b26190277070ae7360ae37d416ae716bf1751a742cae6e48124d23b739",
    ),
    "pig-r1-pig-question": (
        "四星猪猪/猪猪？.png",
        "ab779c6e306f2a04e71ef202cb20670934c73cbd8e4649e08a682db57df76176",
    ),
    "pig-r1-halo-descent": (
        "一星猪猪/猪降临.gif",
        "9db44e8c9195a79970dc69f24f04d336c0da7c66ab2ba4dba36dc87eb8ae03ba",
    ),
    "pig-r1-reindeer-cap": (
        "一星猪猪/猪麋鹿.png",
        "e87aed5f1013ac6fea0225f759a331f1a65d6e30f6a2ff0c84b0649ddfd20eb4",
    ),
    "pig-r1-line-sketch": (
        "一星猪猪/简笔猪.png",
        "4ec964abd9b0a44252c5cb9b8a6d86b569fe887e91e4358ba2dadfc8705d6e31",
    ),
    "pig-r2-parking-nap": (
        "二星猪猪/停🐷位.png",
        "8bbb8029c9a0a23c4ec80b2f93fcb829962a413961ef4139185254ef00e8d9d1",
    ),
    "pig-r2-red-note": (
        "二星猪猪/小红猪.png",
        "b64dc310e42a41451e8860ee27102f682e40f86a0c3c647e8514821a428f3c33",
    ),
    "pig-r2-bonk": (
        "二星猪猪/敲猪.png",
        "df21da3cbd578b41274513a9b7db81a4d4211e323a76e3935a54f5e611e05dd1",
    ),
    "pig-r2-swinging-harness": (
        "二星猪猪/猪上吊.gif",
        "c8ef64d0b51042eb5c4f7383bcb3940e8135b776e2058916297a8380d0f27f8e",
    ),
    "pig-r2-flying-wand": (
        "二星猪猪/猪会飞.png",
        "638b608bb9d9eb7945b190a9881473cf648b76ec4b77684845ca1832b0fb315c",
    ),
    "pig-r2-halo-boss": (
        "二星猪猪/猪大哥.png",
        "d0acf9115e409c211d3427f5b2c3d446707f14b3b6daca15afae2c655bc8c310",
    ),
    "pig-r2-courier": (
        "二星猪猪/猪递员.png",
        "28d201c8ed13e990fb3f40d86fd6cde9bbdcd8869a25779a33a07dbabbe546be",
    ),
    "pig-r3-color-spectrum": (
        "三星猪猪/大色猪.png",
        "00f664dba175dcdce615748481d3989b8593cf63246140336914c2820afbb28c",
    ),
    "pig-r3-exist-chart": (
        "三星猪猪/渚打E.png",
        "7f1950bec45aad1d20a5448e137a6d38b39c40af67c66ce51c4bd748538bcd7d",
    ),
    "pig-r3-savior-song-chart": (
        "三星猪猪/渚打S.png",
        "7c3f1e231b09d2bd31d8b41f50d9e7be80b075bc0cebb49dde54c8e835621da0",
    ),
    "pig-r3-python-eater": (
        "三星猪猪/猪吃蛇.png",
        "93d1d2e332a02c1add8295e82967c39c92c9e543f03890b168ad2b9e11c02a12",
    ),
    "pig-r3-inky-halo": (
        "三星猪猪/猪黑黑.png",
        "f855dc6c628ed0d52a2dfa7ef1d9994df9424d138e3ed5af6a84bf190139042e",
    ),
    "pig-r3-wise-glasses": (
        "三星猪猪/睿智猪.png",
        "d6d6e0f483c4e37614f7aaca59517c835bfba705afe1078d24c04ac6dd7f8f9e",
    ),
    "pig-r3-rhythm-rookie": (
        "三星猪猪/菜渚.png",
        "45c91a990dbf42535658692f406c09d736658340baa7fe6a0ef12cac6ae04d8c",
    ),
    "pig-r3-python-bitten": (
        "三星猪猪/蛇咬猪.png",
        "277d6922c047b5dee1053687d58000aa8d559fc4dd49c45a1ab2571d8875521b",
    ),
    "pig-r4-anan-sketchbook": (
        "四星猪猪/安安的猪猪.png",
        "1b3a35864bbe7910859eb3cc0f34002e791cdc98ef211215d01fa66a3400bc7f",
    ),
    "pig-r4-cheshire-maid": (
        "四星猪猪/柴郡猪.png",
        "4996b402f684ce965774359f0350aee400a72acd1173c1b50d17564dd677e412",
    ),
    "pig-r4-extra-large": (
        "四星猪猪/特大渚.png",
        "43b9b2a6439895af4bf5f826ba1df176581157dd31ef4afd7fd166863afec72b",
    ),
    "pig-r4-bandori-player": (
        "四星猪猪/猪玩邦邦.png",
        "03b21baae420821bea9f1f84234cbb977881486b3c6427589a0a5fc7d4fbeae4",
    ),
    "pig-r5-galaxy-colossus": (
        "五星猪猪/你是那么大的猪.png",
        "572b888b6514264e9e242350f9d6442a9942884d8268e4533e8fe921f7df4244",
    ),
    "pig-r5-king-crab": (
        "五星猪猪/帝王猪.png",
        "da5e77557f1cfd3acb3d8bbd0b1c57ffaf283c5a42e1085bb5c1fbbff4f38d59",
    ),
    "pig-r5-rainbow-cloud": (
        "五星猪猪/彩虹云猪.png",
        "988e5104c81592f1520a8ae30e0a29d5ead5d16cd2b52a0373fdfbcf0668c572",
    ),
    "pig-r5-dark-side-prism": (
        "五星猪猪/猪之暗面.png",
        "4682c797b31632cfd80f6e03fef549db96e2f6c5125937571fb372bea89795f2",
    ),
    "pig-r5-royal-cape": (
        "五星猪猪/猪猪王.png",
        "a467adecbf808b07ce5df585573f9a78ffa440e30073cdcb6a9d3fc62489f452",
    ),
    "pig-r5-magic-kitty": (
        "五星猪猪/魔法猪咪.png",
        "463c86756d88a6f3336db28895b9f15ecb5b4d0cce2dd5446a0474fe88c32a7e",
    ),
    "food-r1-starch-sausage": (
        "一星美食/淀粉肠.png",
        "f63c0065fb86ee311725b4fa9513302a094b3090c7e6ebe922a106c5a825573d",
    ),
    "food-r1-bone-broth": (
        "一星美食/猪骨汤.png",
        "60fd5c2ae9a7e39dbc727377cb0f8f2b37e7e9b1f0dbec9719ca4e024f459dc2",
    ),
    "food-r2-mushroom-bun": (
        "二星美食/咕咕猪包.png",
        "1efcf98163775104b915abd15a4ee091dd8b22e081ccaad439ded92c86538840",
    ),
    "food-r2-pig-belly-chicken": (
        "二星美食/渚肚鸡.png",
        "0c88233399c80489befbe8fc277dc8be68972f2634e49961b83bea073198de34",
    ),
    "food-r3-heart-bun": (
        "三星美食/爱心馅猪包.png",
        "83dadcbc8ebc4cb83b14986e1ab6605f5d064f6d066099c25a852b9d8f0e5596",
    ),
    "food-r3-grape-milk": (
        "三星美食/猪葡萄牛奶.png",
        "bcbe8579e2240137621c3249616c2ebde762c36ea2260c04147898cd64f2457f",
    ),
    "food-r4-hot-pig": (
        "四星美食/热猪.png",
        "d0db1a17ebcfb3ed0d1335b308d3909d526e66dbb580bd3ac17c0ea6a8c276a8",
    ),
    "food-r4-orange-milk": (
        "四星美食/猪橘子牛奶.png",
        "3c7097244fc9d661974a17a756aebdf6e2d9919e989402ea3cfe8a16d5cd766c",
    ),
    "food-r4-souffle": (
        "四星美食/猪芙蕾.png",
        "e5e2896edfb491b2638ba54c6f5310763b7779f281ff51d1c7da949ed52417f0",
    ),
    "food-r4-sausage-pig": (
        "四星美食/香肠猪.png",
        "e3e665829e88aa2e1ede194d37d840485006b17dae9c85dbf62da1effd4e2f2d",
    ),
    "food-r5-tiramisu": (
        "五星美食/提拉米猪.png",
        "fb3a8235dd94132b335b370deee67a3071e49c7d2af0be567f89ad721a567191",
    ),
    "food-r5-chocolate-pig": (
        "五星美食/猪克力.png",
        "db2e286092ab1832af7cf1aeb3e3e0f2da9e02dd11a6a8c709d0ff6968531b5c",
    ),
    "food-r5-yolk-pig": (
        "五星美食/猪包蛋.png",
        "7a1a543946372582a53894d087323d00fd11e2b2543cd1e2ce33203f5a0ed8b7",
    ),
    "food-r5-strawberry-milk": (
        "五星美食/猪草莓牛奶.png",
        "58dea844119cc6d3318fef1296d13304c75d1ae0fcfd64476fed6939d30c4ea3",
    ),
}


@pytest.fixture(scope="module")
def definitions() -> list[dict[str, Any]]:
    return json.loads(DEFINITIONS.read_text(encoding="utf-8"))["entries"]


@pytest.fixture(scope="module")
def manifest_by_id() -> dict[str, dict[str, Any]]:
    entries = json.loads((RELEASE / "assets.json").read_text(encoding="utf-8"))["entries"]
    assert len({entry["template_id"] for entry in entries}) == len(entries)
    return {entry["template_id"]: entry for entry in entries}


@pytest.fixture(scope="module")
def inventory_by_id() -> dict[str, dict[str, Any]]:
    entries = json.loads((RELEASE / "build-report.json").read_text(encoding="utf-8"))["inventory"]
    assert len({entry["template_id"] for entry in entries}) == len(entries)
    return {entry["template_id"]: entry for entry in entries}


def test_round9_names_rarities_and_paths_match_the_reviewed_drop(definitions: list[dict[str, Any]]) -> None:
    # 后续补充仍保留首轮46份原图的独立SHA基线；猪猪？只迁目录和品质，不换身份。
    new_entries = {
        entry["template_id"]: entry for entry in definitions if entry["template_id"] in EXPECTED_ASSETS
    }
    assert set(new_entries) == set(EXPECTED_ASSETS)
    assert Counter(entry["kind"] for entry in new_entries.values()) == {"pig": 32, "food": 14}
    assert Counter((entry["kind"], entry["rarity"]) for entry in new_entries.values()) == {
        ("pig", 1): 6,
        ("pig", 2): 7,
        ("pig", 3): 8,
        ("pig", 4): 5,
        ("pig", 5): 6,
        ("food", 1): 2,
        ("food", 2): 2,
        ("food", 3): 2,
        ("food", 4): 4,
        ("food", 5): 4,
    }
    for template_id, (relative, _) in EXPECTED_ASSETS.items():
        entry = new_entries[template_id]
        original = PurePosixPath(relative)
        assert entry["source_path"] == f"第九期/{relative}"
        assert entry["display_name"] == original.stem
        assert entry["rarity"] == RARITY_NAMES[original.parent.name[0]]
        assert entry["kind"] == ("pig" if original.parent.name.endswith("猪猪") else "food")
        assert not entry.get("group_scope_id")
        assert not entry.get("paired_food_template_id")
        assert str(entry["description"]).strip()


def test_new_high_star_foods_have_explicit_reviewed_effects(definitions: list[dict[str, Any]]) -> None:
    from pig_catcher.domain.round9_food_rules import ROUND9_FOOD_EFFECTS

    high_star_foods = [entry for entry in definitions if entry["kind"] == "food" and entry["rarity"] in (4, 5)]
    new_foods = [entry for entry in high_star_foods if str(entry["source_path"]).startswith("第九期/")]
    assert len(new_foods) == 12
    assert Counter(entry["rarity"] for entry in new_foods) == {4: 7, 5: 5}
    for entry in new_foods:
        assert (entry["effect_id"], entry["effect_params"]) == ROUND9_FOOD_EFFECTS[entry["template_id"]]
    # This exception is only for the newly supplied art, not a reset of old recipes.
    for entry in high_star_foods:
        if not str(entry["source_path"]).startswith("第九期/"):
            assert str(entry.get("effect_id") or "").strip(), entry["display_name"]


def test_every_old_and_new_pig_has_reviewed_tags_and_explicit_physical_ranges(
    definitions: list[dict[str, Any]],
    manifest_by_id: dict[str, dict[str, Any]],
) -> None:
    pigs = [entry for entry in definitions if entry["kind"] == "pig"]
    assert len(pigs) == 223
    for entry in pigs:
        name = entry["display_name"]
        tags = entry["display_tags"]
        assert isinstance(tags, list) and 2 <= len(tags) <= 5, name
        assert all(isinstance(tag, str) and tag.strip() == tag and tag for tag in tags), name
        assert len(set(tags)) == len(tags), name
        assert entry["stature_profile"] in {"mini", "standard", "giant"}, name
        published = manifest_by_id[entry["template_id"]]
        assert published["display_tags"] == tags, name
        assert published["stature_profile"] == entry["stature_profile"], name
        for minimum, maximum, cap in (
            ("length_min_cm", "length_max_cm", 10000),
            ("weight_min_kg", "weight_max_kg", 100000),
        ):
            assert minimum in entry and maximum in entry, name
            low, high = entry[minimum], entry[maximum]
            assert isinstance(low, (int, float)) and not isinstance(low, bool), name
            assert isinstance(high, (int, float)) and not isinstance(high, bool), name
            assert math.isfinite(low) and math.isfinite(high), name
            assert 0 < low < high <= cap, name
            assert published[minimum] == low and published[maximum] == high, name
    by_id = {entry["template_id"]: entry for entry in pigs}
    assert by_id["pig-r4-extra-large"]["weight_max_kg"] >= 1000
    assert by_id["pig-r5-galaxy-colossus"]["weight_max_kg"] >= 1000
    # Large clouds need not be heavy; attributes are not inferred solely from rarity.
    assert by_id["pig-r5-rainbow-cloud"]["stature_profile"] == "giant"
    assert by_id["pig-r5-rainbow-cloud"]["weight_max_kg"] < 1000


def test_four_scopes_share_content_without_merging_six_star_ownership(definitions: list[dict[str, Any]]) -> None:
    assert Counter(entry["kind"] for entry in definitions) == {"pig": 223, "food": 105}
    scoped = [entry for entry in definitions if entry.get("group_scope_id")]
    assert len(scoped) == 96
    assert {entry["group_scope_id"] for entry in scoped} == set(SCOPES)
    assert all(entry["rarity"] == 6 for entry in scoped)
    assert all(entry.get("group_scope_id") in SCOPES for entry in definitions if entry["rarity"] == 6)
    assert len({entry["template_id"] for entry in scoped}) == 96
    assert len({entry["source_path"] for entry in scoped}) == 96
    by_id = {entry["template_id"]: entry for entry in definitions}
    semantic_fields = (
        "kind",
        "display_name",
        "rarity",
        "description",
        "display_tags",
        "fat_profile",
        "stature_profile",
        "length_min_cm",
        "length_max_cm",
        "weight_min_kg",
        "weight_max_kg",
        "recipe_tags",
        "effect_id",
        "effect_params",
    )
    signatures = []
    for scope in SCOPES:
        visible = [entry for entry in definitions if entry.get("group_scope_id") in (None, "", scope)]
        assert Counter(entry["kind"] for entry in visible) == {"pig": 187, "food": 69}
        private = [entry for entry in scoped if entry["group_scope_id"] == scope]
        assert Counter(entry["kind"] for entry in private) == {"pig": 12, "food": 12}
        for entry in private:
            assert f"/{scope.split(':', 1)[1]}/" in f"/{entry['source_path']}"
        for pig in (entry for entry in private if entry["kind"] == "pig"):
            food = by_id[pig["paired_food_template_id"]]
            assert food["kind"] == "food" and food["rarity"] == 6
            assert food["group_scope_id"] == scope
        signatures.append(
            {
                (entry["kind"], entry["display_name"]): {key: entry.get(key) for key in semantic_fields}
                for entry in visible
            }
        )
    assert all(signature == signatures[0] for signature in signatures[1:])


def test_efficiency_chart_pigs_name_the_correct_songs_without_inventing_band_members(
    definitions: list[dict[str, Any]],
) -> None:
    by_id = {entry["template_id"]: entry for entry in definitions}
    for template_id, song, tag in (
        ("pig-r3-exist-chart", "EXIST", "EXIST"),
        ("pig-r3-savior-song-chart", "SAVIOR OF SONG", "SOS"),
    ):
        entry = by_id[template_id]
        assert song in entry["description"]
        assert "谱面" in entry["description"]
        assert tag in entry["display_tags"]
        assert not entry.get("collection")


@pytest.mark.parametrize("template_id", tuple(EXPECTED_ASSETS))
def test_each_new_released_binary_matches_the_original_review_hash(
    template_id: str,
    manifest_by_id: dict[str, dict[str, Any]],
    inventory_by_id: dict[str, dict[str, Any]],
) -> None:
    relative, expected_sha = EXPECTED_ASSETS[template_id]
    published = manifest_by_id[template_id]
    recorded = inventory_by_id[template_id]
    assert recorded["source_path"] == f"第九期/{relative}"
    assert published["image"] == recorded["media_path"]
    media_path = (RELEASE / published["image"]).resolve()
    assert media_path.is_relative_to(RELEASE.resolve())
    payload = media_path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == expected_sha
    assert recorded["sha256"] == expected_sha
    assert recorded["bytes"] == len(payload)
    if template_id not in {"pig-r1-halo-descent", "pig-r2-swinging-harness"}:
        with Image.open(media_path) as image:
            image.load()
            assert image.format == "PNG"
            assert image.n_frames == 1


@pytest.mark.parametrize(
    ("template_id", "media_format", "frame_count", "duration_ms", "durations", "dimensions"),
    (
        ("pig-r1-halo-descent", "WEBP", 60, 3990, {60, 70}, (240, 240)),
        ("pig-r2-swinging-harness", "GIF", 75, 3000, {40}, (404, 375)),
    ),
)
def test_round9_animations_keep_detected_format_all_frames_and_timing(
    template_id: str,
    media_format: str,
    frame_count: int,
    duration_ms: int,
    durations: set[int],
    dimensions: tuple[int, int],
    manifest_by_id: dict[str, dict[str, Any]],
) -> None:
    path = RELEASE / manifest_by_id[template_id]["image"]
    with Image.open(path) as image:
        assert image.format == media_format
        assert image.is_animated
        assert image.n_frames == frame_count
        assert image.size == dimensions
        assert image.info["loop"] == 0
        decoded_durations = []
        for frame_index in range(frame_count):
            image.seek(frame_index)
            image.load()
            decoded_durations.append(image.info["duration"])
        assert sum(decoded_durations) == duration_ms
        assert set(decoded_durations) == durations
