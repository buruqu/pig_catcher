"""第九期补充素材：梦限大、独立六星配对和未定效果的回归边界。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest
from PIL import Image

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.assets.models import AssetManifest
from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.achievements import ACHIEVEMENT_DEFINITIONS
from pig_catcher.domain.activity_achievements import FIXED_SETS, LEGACY_REGULAR_IDS
from pig_catcher.domain.dispatch import SPECIALTIES
from pig_catcher.domain.economy import EAT_EXPERIENCE_REWARDS
from pig_catcher.domain.enums import Rarity
from pig_catcher.domain.errors import AssetImportError, CookingTemplateError, PigCatcherError
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.tour import score_stage, validate_formation
from pig_catcher.domain.tour_catalog import (
    CHARACTERS,
    GUESTS,
    MAIN_FORMS,
    SCORE_CAPS,
    SONGS,
    THEME_EMBLEMS,
    THEMES_BY_ID,
    default_plan,
)
from pig_catcher.infrastructure.repositories import EconomyRepository, GameplayRepository, SocialRepository
from pig_catcher.rendering.adapters import catalog_view, daily_giants_view, records_view
from pig_catcher.services import AssetCatalogService, EconomyService, FrameworkService, GameplayService
from pig_catcher.services.achievements import _COLLECTION_ALIASES, AchievementService
from pig_catcher.services.tour import TourService

from .test_dispatch import NOW, seed_pigs
from .test_economy import _insert_food
from .test_gameplay import MutableClock, SequenceRandom, _catch_rolls, _database_with_catalog
from .test_tour import TourWorld

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "asset_library/current"
SCOPES = (
    "qq:1092931381",
    "qq-official:5E5854406D0297D6FEAE696A13E3A339",
    "qq:237716658",
    "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
)
YUMEMITA = {
    "arale": ("阿拉蕾猪", 5, ("voice",)),
    "nonoka": ("nnk猪", 4, ("guitar",)),
    "ritsu": ("律猪", 4, ("guitar",)),
    "miyako": ("都子猪", 5, ("keyboard",)),
    "yuno": ("由乃猪", 5, ("dj",)),
}
YUMEMITA_IDS = tuple(f"pig-bandori-yumemita-{key}" for key in YUMEMITA)
SIX_PIG = "熠～噜猪"
SIX_FOOD = "熠～噜猪绿芯小猪派"
QUESTION_ID = "pig-r1-pig-question"
HIGH_STAR_FOODS = {
    4: ("热猪", "猪橘子牛奶", "猪芙蕾", "香肠猪", "猪可乐", "猪咪堡", "猪条"),
    5: ("提拉米猪", "猪克力", "猪包蛋", "猪草莓牛奶", "猪堡套餐"),
    6: (SIX_FOOD,),
}
# 原字节的独立审阅基线；阿拉蕾仅按用户明确要求修正图中文字，记录已验收的新图SHA。
EXPECTED_SOURCES = {
    "一星猪猪/深海猪.png": "02dc0b8ee539f2b1c7554d08d2fceacb66bdf89ea4816c64840f3f036fc30d8b",
    "一星猪猪/猪睡觉.png": "cc9c69bae87bc3eb18b646aa7a7cbda1ab669168d21836a3b87eeed0a6e1e89e",
    "一星猪猪/猪身份证.png": "fd050e4e8e7101e5498e7b31da2c6b08b53b2d8712ab7a60b39c969c4da79c23",
    "一星猪猪/猪骰.png": "5a7e581bc830cdbe89a1e7d11e1913ba83929dfb5ad6152f078d9212174d1b97",
    "二星猪猪/你怎么跟猪一样.gif": "3abf5717027d2542b770d92b804ac9a1447248b10b31df84321278d84495e84a",
    "二星猪猪/劲爆大只猪头.png": "0c49d788c3d86567a24247ca315af3ae7d650b56caffa4c87d5488ec932252e3",
    "二星猪猪/猪上班.png": "52d44e080bc1ac6c9741403aa214da88803aa0bf6e95fd0cb258d2ba3701d4fe",
    "三星猪猪/渚交互.png": "14f4d7b0147eade53483fa806e7ef343ffbcf55afdb20011741ebc112059262b",
    "三星猪猪/猪打call.gif": "5b6d8b8b55096c915d3a304c257a1e837a72fe4322b19f7ec647fd9befcde68d",
    "四星猪猪/nnk猪.png": "4df40077024060345bbc64092b50ee35b06169d3caabafecb231dc83d55499fc",
    "四星猪猪/律猪.png": "0164a0efc7ad65c071b84fa029aef2faddcc55df6e305a22eb05a7a03a40dba4",
    "五星猪猪/阿拉蕾猪.png": "9dbb362bab1e4c08f253e79000d65faf8ee69586b8de507bb3a796822cec7fa9",
    "五星猪猪/都子猪.png": "03c3f07d2e450cf73d17228cb6548d4ca2481701df2ac0361cb2bd5ccae65ebe",
    "五星猪猪/由乃猪.png": "e210ef492278349f3290a12507cf7598e409f93cbb049b8908b19a7faf233502",
    "六星猪猪及其对应美食/熠～噜猪.png": "94e3cde855846439796ea189ce500ef1162a0c24372f3c602ec183b566f9ff5a",
    "一星美食/猪蛋.png": "c56ab5783c6e2e7249316ab5afcbb5e5d37c41e4e350eb9ec3d332d4694e390d",
    "一星美食/猪饲料.png": "1043a6abc0c949dfc65414feb0839e4c3980ecd0ee4e14a2fe647986a497c426",
    "二星美食/猪笋.png": "797089d48d3e7182dc0cb5b062797053150a2102f14fe303177cb5ef859e5749",
    "三星美食/开猪罐头.png": "48a79891d0dd621f794d5a391b57d00a64bf9e68d850225a5c4dee5d2825f35e",
    "四星美食/猪可乐.png": "8042785a36089beb28919a9dfb1d92ddca36f1f3b0023f0dbec6e204e7b720ca",
    "四星美食/猪咪堡.png": "a1e496266bd05ac787b483beeb541e39e6ad5494ae6446b1e94aa571621d6d99",
    "四星美食/猪条.png": "0e2a816e42ee8c43eecb09fb45f329bce1385922ba4127c59bad9d20fd97fa7f",
    "五星美食/猪堡套餐.png": "c1f5065abeb35e0c3b69f73ef9cee9b397955365c83e4ce6e1b44b11fde52fac",
    "六星猪猪及其对应美食/熠～噜猪绿芯小猪派.png": "76ce0235d4c48e5f35015052ad1e619ed7a82e7fe99291530865223fd0539b01",
}


@pytest.fixture(scope="module")
def definitions():
    return json.loads((ROOT / "catalogs/formal/pig-and-food-definitions.json").read_text(encoding="utf-8"))["entries"]


@pytest.fixture(scope="module")
def manifest():
    return json.loads((RELEASE / "assets.json").read_text(encoding="utf-8"))["entries"]


def _synthetic(entries):
    """业务测试用小图；真实原件的SHA和逐帧检查另有独立断言。"""
    result = deepcopy(entries)
    for entry in result:
        entry["image"] = entry["template_id"] + ".png"
        entry["alternate_image"] = ""
    return result


def _identity(scope=SCOPES[0], *, message="supplement"):
    return CommandIdentity(
        ScopeKey.parse(scope), "offline-stream", "supplement-player", "补充测试员", message, "隔离群"
    )


def _entry(entries, name, scope=None):
    candidates = [
        row for row in entries if row["display_name"] == name and (scope is None or row.get("group_scope_id") == scope)
    ]
    if scope is None:
        candidates = [row for row in candidates if not row.get("group_scope_id")]
    assert len(candidates) == 1, (name, scope)
    return candidates[0]


def test_supplement_counts_and_all_pending_food_effects(definitions):
    assert len(definitions) == 328
    assert Counter(row["kind"] for row in definitions) == {"pig": 223, "food": 105}
    new_names = {PurePosixPath(path).stem for path in EXPECTED_SOURCES}
    new_rows = [row for row in definitions if row["display_name"] in new_names]
    assert Counter(row["kind"] for row in new_rows) == {"pig": 18, "food": 12}
    for rarity, names in HIGH_STAR_FOODS.items():
        matching = [row for row in definitions if row["kind"] == "food" and row["display_name"] in names]
        assert len(matching) == (4 if rarity == 6 else len(names))
        for row in matching:
            assert row["rarity"] == rarity
            assert row.get("effect_id", "") == ""
            assert row.get("effect_params", {}) == {}


def test_new_private_pair_keeps_synchronized_content_but_four_isolated_paths(definitions, manifest):
    published = {row["template_id"]: row for row in manifest}
    semantic_keys = (
        "kind",
        "display_name",
        "rarity",
        "description",
        "display_tags",
        "recipe_tags",
        "length_min_cm",
        "length_max_cm",
        "weight_min_kg",
        "weight_max_kg",
        "fat_profile",
        "stature_profile",
        "effect_id",
        "effect_params",
    )
    for name in (SIX_PIG, SIX_FOOD):
        copies = [row for row in definitions if row["display_name"] == name]
        assert {row["group_scope_id"] for row in copies} == set(SCOPES)
        assert len({row["template_id"] for row in copies}) == 4
        assert len({row["source_path"] for row in copies}) == 4
        assert len({published[row["template_id"]]["image"] for row in copies}) == 4
        canonical = {key: copies[0].get(key) for key in semantic_keys}
        assert all({key: row.get(key) for key in semantic_keys} == canonical for row in copies)


@pytest.mark.parametrize("source_path", tuple(EXPECTED_SOURCES))
def test_every_added_source_matches_reviewed_bytes_and_kind(source_path, definitions, manifest):
    original = PurePosixPath(source_path)
    name = original.stem
    rarity = "一二三四五六".index(original.parts[0][0]) + 1
    kind = "food" if original.parent.name.endswith("美食") and rarity != 6 or name == SIX_FOOD else "pig"
    reviewed = [row for row in definitions if row["display_name"] == name]
    assert len(reviewed) == (4 if rarity == 6 else 1)
    published = {row["template_id"]: row for row in manifest}
    media_paths = set()
    for row in reviewed:
        assert (row["kind"], row["rarity"]) == (kind, rarity)
        released = published[row["template_id"]]
        assert (released["kind"], released["rarity"], released["display_name"]) == (kind, rarity, name)
        media = (RELEASE / released["image"]).resolve()
        assert media.is_relative_to(RELEASE.resolve())
        assert hashlib.sha256(media.read_bytes()).hexdigest() == EXPECTED_SOURCES[source_path]
        media_paths.add(media)
        if rarity < 6:
            assert row["source_path"] == "第九期/" + source_path
            assert not row.get("group_scope_id")
        if kind == "pig":
            assert 2 <= len(row["display_tags"]) <= 5
            assert len(set(row["display_tags"])) == len(row["display_tags"])
            assert row["template_id"] in SPECIALTIES
            for low, high, cap in (
                ("length_min_cm", "length_max_cm", 10000),
                ("weight_min_kg", "weight_max_kg", 100000),
            ):
                assert math.isfinite(row[low]) and math.isfinite(row[high])
                assert 0 < row[low] < row[high] <= cap
                assert (released[low], released[high]) == (row[low], row[high])
        else:
            assert not row.get("effect_id") and not row.get("effect_params")
    assert len(media_paths) == len(reviewed)


@pytest.mark.parametrize(
    "name,dimensions,frames,duration,timings",
    (("你怎么跟猪一样", (470, 180), 95, 2880, {30, 60}), ("猪打call", (704, 719), 6, 600, {100})),
)
def test_added_gifs_preserve_short_side_and_duplicate_hold_frames(
    name, dimensions, frames, duration, timings, manifest
):
    path = RELEASE / _entry(manifest, name)["image"]
    with Image.open(path) as image:
        assert image.format == "GIF" and image.size == dimensions
        assert image.n_frames == frames and image.info["loop"] == 0
        decoded = []
        for index in range(frames):
            image.seek(index)
            image.load()
            decoded.append(image.info["duration"])
        assert sum(decoded) == duration and set(decoded) == timings


def test_new_band_has_five_distinct_roles_without_overwriting_old_viola(definitions):
    by_id = {row["template_id"]: row for row in definitions}
    slots = set()
    for identity, (name, rarity, instruments) in YUMEMITA.items():
        key = f"pig-bandori-yumemita-{identity}"
        row, character = by_id[key], CHARACTERS[key]
        assert key in MAIN_FORMS
        assert (row["display_name"], row["rarity"]) == (name, rarity)
        assert row["collection"]["collection_id"] == "bandori-yumemita"
        assert row["collection"]["total"] == 5
        assert row["collection"]["official_profile_url"].startswith("https://")
        slots.add(row["collection"]["slot"])
        assert character.identity == identity and character.band == "yumemita"
        assert character.instruments == instruments
        assert character.signature.name and character.signature.summary
    assert slots == set(range(1, 6))
    assert "yumemita" in THEMES_BY_ID and "yumemita" in THEME_EMBLEMS
    assert {song.song_id for song in SONGS if song.theme_id == "yumemita"} == {f"yumemita-{n}" for n in (1, 2, 3)}
    assert by_id["pig-bandori-viola-green-tea"]["collection"]["collection_id"] == "bandori-yumemita-viola"
    assert by_id["pig-bandori-viola-green-tea"]["collection"]["total"] == 1
    assert "pig-bandori-viola-green-tea" in GUESTS
    assert "pig-bandori-viola-green-tea" not in MAIN_FORMS
    assert len(LEGACY_REGULAR_IDS) == 49 and len(ACHIEVEMENT_DEFINITIONS) == 130
    assert len(FIXED_SETS["tour-band-themes-v1"]) == 9
    assert "yumemita" not in FIXED_SETS["tour-band-themes-v1"]
    assert _COLLECTION_ALIASES["mugendai"] == "bandori-yumemita-viola"


@pytest.mark.parametrize("seed", ["full-band", "no-drums", "new-theme", "mixed"])
def test_new_band_can_score_without_bass_or_drums_and_keeps_original_caps(seed):
    members = [
        {
            "pig_instance_id": str(i),
            "template_id": key,
            "name": CHARACTERS[key].name,
            "training_exp": 2200,
            "rapport": 30,
        }
        for i, key in enumerate(YUMEMITA_IDS)
    ]
    if seed == "mixed":
        members[-2] = {**members[-2], "template_id": "pig-bandori-roselia-rinko", "name": "宅宅猪"}
    assert len(validate_formation(members, center="0")) == 5
    plan = default_plan("yumemita")
    result = score_stage(members, plan, equipment=5, song_plays={song: 10 for song in plan["songs"]}, seed=seed)
    assert 0 <= result["score"] <= 100
    assert all(0 <= result["components"][key] <= cap for key, cap in SCORE_CAPS.items())
    assert SCORE_CAPS == {"ability": 35, "synergy": 25, "setlist": 25, "stage": 10, "equipment": 5}


async def test_full_catalog_visibility_and_collection_denominator_in_four_scopes(tmp_path, manifest):
    db = await _database_with_catalog(tmp_path, _synthetic(manifest), manifest_version=4)
    clock = MutableClock(NOW)
    try:
        game = GameplayService(db, CatchingSection(), clock=clock)
        economy = EconomyService(db, CookingSection(), EconomySection(), clock=clock)
        for scope in SCOPES:
            identity = _identity(scope)
            pigs = await game.catalog(identity, rarity=None, undiscovered_only=False)
            foods = await economy.food_catalog(identity, rarity=None, undiscovered_only=False)
            assert pigs.total_count == 187 and foods.total_count == 69
            group = next(c for c in pigs.collections if c.collection_id == "bandori-yumemita")
            assert (group.total_count, group.available_count, group.collected_count) == (5, 5, 0)
            target = _entry(manifest, SIX_PIG, scope)
            new_pig = next(p for p in pigs.entries if p.template_id == target["template_id"])
            assert not new_pig.discovered and not new_pig.display_tags
            # Raw catalog rows retain the path for later discovery; the rendering
            # contract must suppress both the media and descriptive tags now.
            hidden = next(p for p in catalog_view(pigs).items if p.key == target["template_id"])
            assert not hidden.discovered and not hidden.media_visible and not hidden.display_tags
            visible_ids = {p.template_id for p in pigs.entries}
            assert all(
                row["template_id"] not in visible_ids
                for row in manifest
                if row["kind"] == "pig" and row.get("group_scope_id") not in (None, "", scope)
            )
            async with db.transaction() as session:
                for template in YUMEMITA_IDS:
                    await GameplayRepository().upsert_pig_catalog(
                        session,
                        player_id=identity.player_id,
                        template_id=template,
                        size_value=50,
                        weight_value=70,
                        now=NOW.isoformat(),
                    )
            complete = await game.catalog(identity, rarity=None, undiscovered_only=False)
            assert next(c for c in complete.collections if c.collection_id == "bandori-yumemita").collected_count == 5
        outsider = await game.catalog(_identity("qq:999"), rarity=None, undiscovered_only=False)
        assert outsider.total_count == 175
        assert all(p.rarity < 6 for p in outsider.entries)
    finally:
        await db.close()


@pytest.mark.parametrize("scope", SCOPES)
async def test_new_band_can_complete_real_three_stage_tour_in_each_scope(tmp_path, scope, manifest):
    entries = [row for row in manifest if row["template_id"] in YUMEMITA_IDS]
    db = await _database_with_catalog(tmp_path, _synthetic(entries), manifest_version=4)
    clock, identity = MutableClock(NOW), _identity(scope)
    try:
        world = TourWorld(db, clock, TourService(db, clock=clock, seed_factory=lambda: "round9-supplement"), identity)
        await world.form(ids=tuple(YUMEMITA))
        await world.send("主题 yumemita")
        await world.send("一键")
        result = await world.send("确认", message_id="complete-yumemita")
        assert len(result.view.scorecards) == 3
        run = await db.fetch_one("SELECT * FROM tour_runs WHERE player_id=?", (identity.player_id,))
        assert run["status"] == "completed"
        summary = json.loads(run["summary_json"])
        assert all(stage["plan"]["theme"] == "yumemita" for stage in summary["stages"])
        assert all(len(stage["members"]) == 5 for stage in summary["stages"])
        before = await db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (identity.player_id,))
        await world.send("确认", message_id="complete-yumemita")
        after = await db.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (identity.player_id,))
        assert tuple(before) == tuple(after)
        assert (await db.fetch_one("SELECT COUNT(*) FROM tour_runs"))[0] == 1
    finally:
        await db.close()


@pytest.mark.parametrize("mode,rarity", [("cook", 4), ("cook", 5), ("sell", 4), ("sell", 5)])
async def test_new_collaboration_batches_keep_exactly_the_best_eligible_copy(tmp_path, mode, rarity, manifest):
    templates = [row for row in manifest if row["template_id"] in YUMEMITA_IDS and row["rarity"] == rarity]
    ordinary_foods = [
        next(row for row in manifest if row["kind"] == "food" and row["rarity"] == star) for star in range(1, 6)
    ]
    db = await _database_with_catalog(tmp_path, _synthetic([*templates, *ordinary_foods]), manifest_version=4)
    identity = _identity()
    try:
        keep, processed, protected = [], [], []
        for template in templates:
            ids = await seed_pigs(db, identity, template_id=template["template_id"], count=4)
            processed.extend(ids[:2])
            keep.append(ids[2])
            protected.append(ids[3])
        async with db.transaction() as session:
            await session.execute("UPDATE players SET batch_keep_highest=0 WHERE player_id=?", (identity.player_id,))
            for pig_id in protected:
                await session.execute(
                    "UPDATE pig_instances SET is_favorite=1,official_value=9999 WHERE pig_instance_id=?", (pig_id,)
                )
        service = EconomyService(
            db,
            CookingSection(cook_cooldown_seconds=0),
            EconomySection(),
            random_source=SequenceRandom(*([0.0, 0.0, 0.5] * len(processed))),
            clock=MutableClock(NOW),
        )
        if mode == "cook":
            result = await service.batch_cook(identity, rarity=rarity)
            assert result.pig_count == len(processed)
        else:
            result = await service.batch_sell_low_rarity(identity, asset_kind="pig", rarity=rarity)
            assert result.asset_count == len(processed)
        active = await db.fetch_all("SELECT pig_instance_id FROM pig_instances WHERE state='active'")
        assert {row[0] for row in active} == set(keep + protected)
    finally:
        await db.close()


@pytest.mark.parametrize("scope", SCOPES)
async def test_new_six_star_pair_cooks_only_same_scope_food_and_remains_safe_to_eat(tmp_path, scope, manifest):
    pair_rows = [row for row in manifest if row["display_name"] in {SIX_PIG, SIX_FOOD}]
    db = await _database_with_catalog(tmp_path, _synthetic(pair_rows), manifest_version=4)
    identity, clock = _identity(scope, message="cook-pair"), MutableClock(NOW)
    try:
        pig = _entry(manifest, SIX_PIG, scope)
        food = _entry(manifest, SIX_FOOD, scope)
        assert pig["paired_food_template_id"] == food["template_id"]
        pig_id = (await seed_pigs(db, identity, template_id=pig["template_id"], count=1))[0]
        service = EconomyService(
            db,
            CookingSection(cook_cooldown_seconds=0),
            EconomySection(),
            random_source=SequenceRandom(0.999, 0.999, 0.5),
            clock=clock,
        )
        cooked = await service.cook(identity, SIX_PIG)
        assert len(cooked.foods) == 1 and cooked.foods[0].template_id == food["template_id"]
        assert cooked.foods[0].rarity == 6 and cooked.foods[0].effect_id == ""
        assert (await db.fetch_one("SELECT state FROM pig_instances WHERE pig_instance_id=?", (pig_id,)))[
            0
        ] == "consumed-for-cooking"
        eaten = await service.eat(replace(identity, message_id="eat-pair"), cooked.foods[0].selector)
        assert eaten.base_experience == EAT_EXPERIENCE_REWARDS[Rarity.SIX]
        assert eaten.effect.queued_effect_id == ""
        assert (await db.fetch_one("SELECT COUNT(*) FROM player_food_effects"))[0] == 0
        assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances WHERE state='active'"))[0] == 0
        replay = await service.cook(identity, SIX_PIG)
        assert not replay.receipt_created and replay.foods[0].food_instance_id == cooked.foods[0].food_instance_id
    finally:
        await db.close()


async def test_missing_new_six_star_pair_does_not_borrow_another_group_recipe(tmp_path, manifest):
    entries = [row for row in manifest if row["display_name"] in {SIX_PIG, SIX_FOOD}]
    db = await _database_with_catalog(tmp_path, _synthetic(entries), manifest_version=4)
    identity = _identity(message="missing-pair")
    try:
        pig, food = _entry(manifest, SIX_PIG, SCOPES[0]), _entry(manifest, SIX_FOOD, SCOPES[0])
        pig_id = (await seed_pigs(db, identity, template_id=pig["template_id"], count=1))[0]
        before = dict(await db.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (pig_id,)))
        async with db.transaction() as session:
            await session.execute("UPDATE food_templates SET enabled=0 WHERE template_id=?", (food["template_id"],))
        service = EconomyService(
            db,
            CookingSection(cook_cooldown_seconds=0),
            EconomySection(),
            random_source=SequenceRandom(0.999),
            clock=MutableClock(NOW),
        )
        with pytest.raises(CookingTemplateError):
            await service.cook(identity, SIX_PIG)
        assert dict(await db.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (pig_id,))) == before
        assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 0
        assert (await db.fetch_one("SELECT COUNT(*) FROM command_receipts"))[0] == 0
    finally:
        await db.close()


@pytest.mark.parametrize("scope", SCOPES)
def test_asset_validation_rejects_new_six_star_cross_scope_pair(scope, manifest):
    entries = deepcopy([row for row in manifest if row["display_name"] in {SIX_PIG, SIX_FOOD}])
    foreign = next(candidate for candidate in SCOPES if candidate != scope)
    pig = _entry(entries, SIX_PIG, scope)
    pig["paired_food_template_id"] = _entry(entries, SIX_FOOD, foreign)["template_id"]
    with pytest.raises(ValueError, match="其他群"):
        AssetManifest.model_validate(
            {
                "manifest_version": 4,
                "catalog_id": "cross-scope-rejection",
                "source_label": "offline test",
                "entries": entries,
            }
        )


@pytest.mark.parametrize("rarity,name", [(rarity, name) for rarity, names in HIGH_STAR_FOODS.items() for name in names])
async def test_each_round9_high_star_food_has_only_basic_tasting_until_effect_review(tmp_path, rarity, name, manifest):
    entry = _entry(manifest, name, SCOPES[0] if rarity == 6 else None)
    entries = [entry]
    if rarity == 6:
        entries.append(_entry(manifest, SIX_PIG, SCOPES[0]))
    db = await _database_with_catalog(tmp_path, _synthetic(entries), manifest_version=4)
    identity = _identity()
    try:
        clock = MutableClock(NOW)
        await FrameworkService(db, clock=clock).touch_identity(identity)
        await _insert_food(
            db,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id=entry["template_id"],
            display_name=name,
            official_value=100,
            short_code="TESTFOOD",
            instance_id="pending-effect-food",
            rarity=rarity,
            now=NOW.isoformat(),
        )
        service = EconomyService(db, CookingSection(), EconomySection(), clock=clock)
        detail = await service.food_detail(identity, name)
        assert detail.effect_id == "" and not detail.effect_params
        eaten = await service.eat(identity, name + "#TESTFOOD")
        assert eaten.base_experience == EAT_EXPERIENCE_REWARDS[Rarity(rarity)]
        assert eaten.effect.queued_effect_id == ""
        assert (await db.fetch_one("SELECT COUNT(*) FROM player_food_effects"))[0] == 0
    finally:
        await db.close()


async def test_existing_achievement_target_snapshots_do_not_expand_on_new_band_import(tmp_path, manifest):
    old_ids = {QUESTION_ID, "pig-bandori-viola-green-tea"}
    old_entries = [row for row in manifest if row["template_id"] in old_ids]
    db = await _database_with_catalog(tmp_path, _synthetic(old_entries), manifest_version=4)
    identity, clock = _identity(), MutableClock(NOW)
    try:
        await FrameworkService(db, clock=clock).touch_identity(identity)
        achievements = AchievementService(db, clock=clock)
        await achievements.initialize()
        async with db.transaction() as session:
            await achievements._capture_scope_targets(session, scope_id=identity.scope.value, now=NOW.isoformat())
        before = [
            tuple(row)
            for row in await db.fetch_all(
                "SELECT * FROM achievement_scope_targets WHERE scope_id=? ORDER BY achievement_id,target_key",
                (identity.scope.value,),
            )
        ]
        assert before
        manifest_path = tmp_path / "source/assets.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        additions = _synthetic([row for row in manifest if row["template_id"] in YUMEMITA_IDS])
        assert {row["template_id"] for row in additions} == set(YUMEMITA_IDS)
        for entry in additions:
            Image.new("RGB", (64, 64), (255, 200, 220)).save(tmp_path / "source" / entry["image"])
        document["entries"].extend(additions)
        manifest_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        await AssetCatalogService(
            db,
            AssetCatalogStorage(tmp_path / "data"),
            min_image_side=32,
            max_image_bytes=1024 * 1024,
        ).import_manifest(manifest_path)
        async with db.transaction() as session:
            await achievements._capture_scope_targets(session, scope_id=identity.scope.value, now=NOW.isoformat())
        after = [
            tuple(row)
            for row in await db.fetch_all(
                "SELECT * FROM achievement_scope_targets WHERE scope_id=? ORDER BY achievement_id,target_key",
                (identity.scope.value,),
            )
        ]
        assert after == before
    finally:
        await db.close()


async def test_reclassified_question_pig_keeps_id_and_every_old_instance_field(tmp_path, manifest):
    updated = next(row for row in manifest if row["template_id"] == QUESTION_ID)
    assert updated["rarity"] == 4
    old = {**deepcopy(updated), "rarity": 1}
    db = await _database_with_catalog(tmp_path, _synthetic([old]), manifest_version=4)
    identity = _identity(message="post-reclassification")
    try:
        old_id = (await seed_pigs(db, identity, template_id=QUESTION_ID, count=1, value=73))[0]
        before = dict(await db.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (old_id,)))
        manifest_path = tmp_path / "source/assets.json"
        source = json.loads(manifest_path.read_text(encoding="utf-8"))
        source["entries"][0]["rarity"] = 4
        manifest_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        await AssetCatalogService(
            db,
            AssetCatalogStorage(tmp_path / "data"),
            min_image_side=32,
            max_image_bytes=1024 * 1024,
        ).import_manifest(manifest_path)
        assert dict(await db.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (old_id,))) == before
        assert tuple(
            await db.fetch_one("SELECT rarity,template_version FROM pig_templates WHERE template_id=?", (QUESTION_ID,))
        ) == (4, 2)
        new = await GameplayService(
            db,
            CatchingSection(cooldown_seconds=0),
            random_source=SequenceRandom(*_catch_rolls()),
            clock=MutableClock(NOW),
        ).catch(identity)
        assert new.pig.template_id == QUESTION_ID and new.pig.rarity == 4
        await db.close()
        await db.open()
        assert dict(await db.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (old_id,))) == before
    finally:
        await db.close()


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize(
    ("repository_method", "page_attribute", "view_attribute"),
    (
        ("records_page", "entries", "items"),
        ("global_records", "global_entries", "global_items"),
        ("giant_sightings", "giant_sightings", "giant_sightings"),
    ),
)
async def test_reclassified_question_history_uses_instance_snapshots(
    tmp_path, manifest, scope, repository_method, page_attribute, view_attribute
):
    updated = next(row for row in manifest if row["template_id"] == QUESTION_ID)
    old_name = "猪猪？·历史名称"
    old = {
        **deepcopy(updated),
        "rarity": 1,
        "display_name": old_name,
        "length_min_cm": 2000,
        "length_max_cm": 2500,
        "weight_min_kg": 4000,
        "weight_max_kg": 5000,
    }
    db = await _database_with_catalog(tmp_path, _synthetic([old]), manifest_version=4)
    identity, clock = _identity(scope, message="historical-record"), MutableClock(NOW)
    try:
        game = GameplayService(
            db,
            CatchingSection(cooldown_seconds=0),
            random_source=SequenceRandom(*_catch_rolls(), *_catch_rolls()),
            clock=clock,
        )
        historical = await game.catch(identity)
        assert historical.pig.rarity == 1 and historical.pig.display_name == old_name
        assert historical.global_size_record and historical.global_weight_record and historical.giant_sighting
        before = dict(
            await db.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (historical.pig.pig_instance_id,))
        )
        manifest_path = tmp_path / "source/assets.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        document["entries"] = _synthetic([updated])
        manifest_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        await AssetCatalogService(
            db,
            AssetCatalogStorage(tmp_path / "data"),
            min_image_side=32,
            max_image_bytes=1024 * 1024,
        ).import_manifest(manifest_path)
        newcomer = replace(identity, user_id="new-record-player", display_name="新玩家", message_id="new-record")
        current = await game.catch(newcomer)
        assert current.pig.template_id == QUESTION_ID and current.pig.rarity == 4
        assert current.pig.display_name == updated["display_name"] != old_name
        assert current.pig.size_value < historical.pig.size_value
        assert current.pig.weight_value < historical.pig.weight_value
        assert (
            dict(
                await db.fetch_one(
                    "SELECT * FROM pig_instances WHERE pig_instance_id=?", (historical.pig.pig_instance_id,)
                )
            )
            == before
        )
        async with db.transaction() as session:
            if repository_method == "records_page":
                _, rows = await GameplayRepository().records_page(session, scope_id=scope, limit=20, offset=0)
            elif repository_method == "global_records":
                rows = await SocialRepository().global_records(session, scope_id=scope)
            else:
                rows = await SocialRepository().giant_sightings(session, scope_id=scope, limit=20)
        historical_rows = [row for row in rows if row["short_code"] == historical.pig.short_code]
        assert len(historical_rows) == (1 if repository_method == "giant_sightings" else 2)
        assert all((row["display_name"], row["rarity"]) == (old_name, 1) for row in historical_rows)
        # The service and image adapter must preserve the snapshot too, both
        # before and after reopening the database (not merely inside one query).
        for reopen in (False, True):
            if reopen:
                await db.close()
                await db.open()
            page = await game.records(identity, page=1)
            entries = [p for p in getattr(page, page_attribute) if p.short_code == historical.pig.short_code]
            rendered = [
                p for p in getattr(records_view(page), view_attribute) if p.short_code == historical.pig.short_code
            ]
            assert len(entries) == len(rendered) == len(historical_rows)
            assert all((p.display_name, p.rarity) == (old_name, 1) for p in (*entries, *rendered))
        # Today's rankings include both actual catches and must not re-grade
        # the old player's largest pig using the newly imported template.
        today = await game.daily_giants(identity)
        assert today.participant_count == 2 and today.catch_count == 2
        for entries in (today.size_entries, today.weight_entries):
            old_best = next(p for p in entries if p.player_id == identity.player_id)
            new_best = next(p for p in entries if p.player_id == newcomer.player_id)
            assert (old_best.display_name, old_best.rarity) == (old_name, 1)
            assert (new_best.display_name, new_best.rarity) == (updated["display_name"], 4)
        today_view = daily_giants_view(today)
        for entries in (today_view.size_items, today_view.weight_items):
            assert {(p.display_name, p.rarity) for p in entries} == {(old_name, 1), (updated["display_name"], 4)}
    finally:
        await db.close()


@pytest.mark.parametrize(
    ("template_id", "old_rarity", "new_rarity"),
    (("pig-unapproved-regrade", 1, 4), (QUESTION_ID, 1, 3), (QUESTION_ID, 1, 5), (QUESTION_ID, 4, 1)),
)
async def test_question_reclassification_does_not_weaken_other_identity_guards(
    tmp_path, manifest, template_id, old_rarity, new_rarity
):
    updated = next(row for row in manifest if row["template_id"] == QUESTION_ID)
    old = {**deepcopy(updated), "template_id": template_id, "rarity": old_rarity}
    db = await _database_with_catalog(tmp_path, _synthetic([old]), manifest_version=4)
    try:
        before = dict(await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (template_id,)))
        manifest_path = tmp_path / "source/assets.json"
        source = json.loads(manifest_path.read_text(encoding="utf-8"))
        source["entries"][0]["rarity"] = new_rarity
        manifest_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(AssetImportError):
            await AssetCatalogService(
                db,
                AssetCatalogStorage(tmp_path / "data"),
                min_image_side=32,
                max_image_bytes=1024 * 1024,
            ).import_manifest(manifest_path)
        assert dict(await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (template_id,))) == before
    finally:
        await db.close()


@pytest.mark.parametrize("change", ("group-scope", "kind"))
async def test_question_reclassification_still_rejects_valid_scope_or_kind_replacement(tmp_path, manifest, change):
    old = next(row for row in manifest if row["template_id"] == QUESTION_ID)
    db = await _database_with_catalog(tmp_path, _synthetic([{**old, "rarity": 1}]), manifest_version=4)
    try:
        before = dict(await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (QUESTION_ID,)))
        if change == "group-scope":
            pig = {**deepcopy(_entry(manifest, SIX_PIG, SCOPES[0])), "template_id": QUESTION_ID}
            replacements = _synthetic([pig, _entry(manifest, SIX_FOOD, SCOPES[0])])
        else:
            replacements = _synthetic([{**_entry(manifest, "猪饲料"), "template_id": QUESTION_ID}])
        manifest_path = tmp_path / "source/assets.json"
        source = json.loads(manifest_path.read_text(encoding="utf-8"))
        source["entries"] = replacements
        # These are valid catalogs in isolation: rejection must come from the
        # persisted identity guard, not from an invalid common/group rarity.
        AssetManifest.model_validate(source)
        for entry in replacements:
            Image.new("RGB", (64, 64), (255, 200, 220)).save(tmp_path / "source" / entry["image"])
        manifest_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(AssetImportError):
            await AssetCatalogService(
                db,
                AssetCatalogStorage(tmp_path / "data"),
                min_image_side=32,
                max_image_bytes=1024 * 1024,
            ).import_manifest(manifest_path)
        assert dict(await db.fetch_one("SELECT * FROM pig_templates WHERE template_id=?", (QUESTION_ID,))) == before
        assert not await db.fetch_one("SELECT 1 FROM food_templates WHERE template_id=?", (QUESTION_ID,))
    finally:
        await db.close()


async def test_feed_food_does_not_collide_with_feed_upgrade_or_equipped_items(tmp_path, manifest):
    food = _entry(manifest, "猪饲料")
    assert food["kind"] == "food" and food["rarity"] == 1
    db = await _database_with_catalog(tmp_path, _synthetic([food]), manifest_version=4)
    identity, clock = _identity(), MutableClock(NOW)
    try:
        await FrameworkService(db, clock=clock).touch_identity(identity)
        for i in (1, 2):
            await _insert_food(
                db,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                template_id=food["template_id"],
                display_name="猪饲料",
                official_value=i,
                short_code=f"FEED000{i}",
                instance_id=f"feed-food-{i}",
                now=NOW.isoformat(),
            )
        async with db.transaction() as session:
            await EconomyRepository().apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=10000,
                reason_code="test",
                reason_text="offline",
                source_object_type="test",
                source_object_id="fixture",
                ledger_entry_id=uuid4().hex,
                idempotency_key=uuid4().hex,
                now=NOW.isoformat(),
            )
        service = EconomyService(db, CookingSection(), EconomySection(), clock=clock)
        # Read-only details require a short code for duplicate names. Eating is
        # intentionally the quick selector and must choose the cheapest copy.
        detail = await service.food_detail(identity, "猪饲料#FEED0001")
        assert detail.food_instance_id == "feed-food-1"
        eaten = await service.eat_or_confirm(identity, "猪饲料")
        assert eaten.food.food_instance_id == "feed-food-1" and eaten.effect.queued_effect_id == ""
        remaining = await service.food_detail(identity, "猪饲料")
        assert remaining.food_instance_id == "feed-food-2"
        purchase = await service.upgrade(replace(identity, message_id="upgrade-feed"), "猪饲料")
        assert purchase.product_id == "upgrade-feed" and purchase.upgrade_level == 1
        assert purchase.unit_price == EconomySection().feed_upgrade_prices[0]
        assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances WHERE state='active'"))[0] == 1
        game = GameplayService(db, CatchingSection(), clock=clock)
        with pytest.raises(PigCatcherError):
            await game.arm_item(replace(identity, message_id="arm-food-not-item"), "猪饲料")
        assert (await db.fetch_one("SELECT COUNT(*) FROM item_inventory"))[0] == 0
    finally:
        await db.close()
