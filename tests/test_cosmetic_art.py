"""全量外观登记、实际成品、隐藏态零读图和有界缓存验证。"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from PIL import Image

from pig_catcher.domain.achievements import ACHIEVEMENT_DEFINITIONS, AchievementReward
from pig_catcher.domain.weekly_competitions import WEEKLY_COMPETITION_DEFINITIONS
from pig_catcher.rendering import cosmetics
from pig_catcher.services.achievements import _MILESTONE_REWARDS
from tools.accept_weekly_competition_views import award, leaderboard
from tools.build_cosmetic_art import emblem_svg

ROOT = Path(__file__).resolve().parents[1]
ART_ROOT = ROOT / "pig_catcher/rendering/assets/ui/cosmetics"
TEMPLATES = ROOT / "pig_catcher/rendering/templates"


def _actual_rewards() -> set[tuple[str, str]]:
    rewards = [(r.reward_id, r.reward_type) for d in ACHIEVEMENT_DEFINITIONS for r in d.rewards]
    rewards.extend((r.reward_id, r.reward_type) for bundle in _MILESTONE_REWARDS.values() for r in bundle)
    rewards.extend(
        (r.reward_id, r.reward_type)
        for definition in WEEKLY_COMPETITION_DEFINITIONS
        for tier in definition.reward_tiers
        for r in tier.rewards
    )
    # 成就自选宝箱的外观分支，奖励不是独立成就条目。
    rewards.extend((("achievement-choice-pink", "frame"), ("achievement-choice", "badge")))
    return {(key, kind) for key, kind in rewards if kind in {"title", "frame", "badge", "cosmetic"}}


def test_all_real_reward_sources_are_covered_without_placeholder_rewards():
    actual = _actual_rewards()
    registered = {(key, item["kind"]) for key, item in cosmetics.COSMETIC_DEFINITIONS.items()}
    assert registered == actual
    assert Counter(kind for _, kind in registered) == {"title": 31, "frame": 16, "badge": 35, "cosmetic": 1}


@pytest.mark.parametrize("key", tuple(cosmetics.COSMETIC_DEFINITIONS))
def test_every_registered_emblem_is_original_vector_with_no_external_content(key):
    definition = cosmetics.COSMETIC_DEFINITIONS[key]
    vector = emblem_svg(definition["emblem"], "#845578", "#bd995a")
    parsed = ElementTree.fromstring(vector)
    assert parsed.tag.endswith("svg")
    assert any(child.tag.endswith(("path", "circle", "ellipse", "polygon", "rect")) for child in parsed.iter())
    assert "<text" not in vector
    assert "href=" not in vector
    assert "<script" not in vector and "onload=" not in vector


def test_unknown_cosmetic_never_becomes_a_file_name_or_visible_identifier(monkeypatch):
    def forbidden(*args):
        raise AssertionError("未知外观不应该读取文件")

    monkeypatch.setattr(cosmetics, "_image", forbidden)
    for value in ("../../secret", "C:/Users/private.png", "<img onerror=alert(1)>", "weekly-002-catch-value-rank-1"):
        result = cosmetics.cosmetic_detail(value)
        assert not result["id"] and not result["name"] and not result["image_data_url"]
        assert value not in repr(result)


def test_locked_hidden_cosmetics_end_before_resolution_and_image_reads(monkeypatch):
    def forbidden(*args):
        raise AssertionError("隐藏外观不得预读")

    monkeypatch.setattr(cosmetics, "_image", forbidden)
    for key, item in cosmetics.COSMETIC_DEFINITIONS.items():
        result = cosmetics.cosmetic_detail(key, revealed=False)
        assert result == cosmetics.cosmetic_detail("unregistered", revealed=False)
        assert item["name"] not in repr(result)
        assert not result["id"] and not result["image_data_url"]
    assert cosmetics.cosmetic_cards([AchievementReward("title", "rain-love")], revealed=False) == ()


def test_legacy_names_and_kind_disambiguation():
    assert cosmetics.cosmetic_detail("雨爱")["id"] == "rain-love"
    assert cosmetics.cosmetic_detail("万猪之巅")["id"] == "all-giants"
    assert cosmetics.cosmetic_detail("万猪之巅", kind="frame")["id"] == "all-giants-dynamic"
    assert cosmetics.cosmetic_detail("九色巡演边框")["family"] == "nine-colors"
    assert cosmetics.cosmetic_detail("万猪之巅", kind="badge")["id"] == ""


def test_cosmetic_cards_support_base_milestone_chest_activity_and_weekly():
    rewards = [AchievementReward(kind, key) for key, kind in sorted(_actual_rewards())]
    cards = cosmetics.cosmetic_cards(rewards)
    assert len(cards) == 83
    assert {card["id"] for card in cards} == set(cosmetics.COSMETIC_DEFINITIONS)
    assert sum(card["is_plate"] for card in cards) == 35
    assert cosmetics.cosmetic_cards([AchievementReward("coin", "pig-coin", 100)]) == ()


@pytest.fixture
def art_manifest():
    path = ART_ROOT / "manifest.json"
    assert path.is_file(), "美术构建未完成：先运行 tools/build_cosmetic_art.py"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert {item["id"] for item in manifest["entries"]} == set(cosmetics.COSMETIC_DEFINITIONS)
    return manifest


def test_all_published_outputs_match_manifest_and_dimensions(art_manifest):
    for item in art_manifest["entries"]:
        definition = cosmetics.COSMETIC_DEFINITIONS[item["id"]]
        expected = (
            (1200, 360)
            if definition["kind"] == "title" or definition.get("rank")
            else ((480, 600) if definition["kind"] == "frame" else (256, 256))
        )
        for variant, file in item["files"].items():
            path = (ART_ROOT / file["path"]).resolve()
            assert path.is_relative_to(ART_ROOT.resolve())
            assert path.stat().st_size == file["bytes"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == file["sha256"]
            if path.suffix == ".svg":
                ElementTree.fromstring(path.read_text(encoding="utf-8"))
                continue
            with Image.open(path) as picture:
                assert picture.width == file["width"] and picture.height == file["height"]
                assert picture.format == file["format"]
                if variant == "png":
                    assert picture.size == expected
                if variant == "compact":
                    assert picture.width <= 600 and picture.height <= 300
                    assert picture.format == "WEBP"
                if variant == "border":
                    assert picture.size == (192, 192)
                    assert picture.convert("RGBA").getpixel((96, 96))[3] == 0
                assert file["bytes"] <= 4 * 1024 * 1024


def test_all_titles_and_weekly_plates_have_distinct_generated_art(art_manifest):
    plates = [
        item
        for item in art_manifest["entries"]
        if cosmetics.COSMETIC_DEFINITIONS[item["id"]]["kind"] == "title"
        or cosmetics.COSMETIC_DEFINITIONS[item["id"]].get("rank")
    ]
    assert len({item["files"]["png"]["sha256"] for item in plates}) == 35
    for item in plates:
        with Image.open(ART_ROOT / item["files"]["png"]["path"]) as picture:
            assert picture.mode == "RGBA"
            assert picture.getpixel((0, 0))[3] == 0


def test_registered_manifest_sources_are_unchanged(art_manifest):
    for source in art_manifest["sources"]:
        path = (ROOT / source["path"]).resolve()
        assert path.is_relative_to(ROOT)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]


def test_runtime_uses_local_bounded_derivatives_and_cache(art_manifest, monkeypatch):
    cosmetics.clear_cosmetic_cache()
    monkeypatch.setattr(cosmetics, "_MAX_CACHE_BYTES", 200_000)
    monkeypatch.setattr(cosmetics, "_MAX_CACHE_ENTRIES", 8)
    for key in cosmetics.COSMETIC_DEFINITIONS:
        card = cosmetics.cosmetic_detail(key)
        assert card["available"]
        assert card["image_data_url"].startswith("data:image/webp;base64,")
        payload = base64.b64decode(card["image_data_url"].split(",", 1)[1])
        with Image.open(BytesIO(payload)) as picture:
            assert picture.width <= 600 and picture.height <= 300
        assert cosmetics.cosmetic_cache_info()["bytes"] <= 200_000
        assert cosmetics.cosmetic_cache_info()["entries"] <= 8
    cosmetics.clear_cosmetic_cache()
    assert cosmetics.cosmetic_cache_info()["bytes"] == 0


def test_bad_hash_rejected_without_caching(art_manifest, monkeypatch):
    cosmetics.clear_cosmetic_cache()
    item = next(item for item in art_manifest["entries"] if item["id"] == "rain-love")
    item["files"]["compact"]["sha256"] = "0" * 64
    monkeypatch.setattr(cosmetics, "_MANIFEST", {"rain-love": item})
    with pytest.raises(ValueError, match="哈希"):
        cosmetics.cosmetic_detail("rain-love")
    assert cosmetics.cosmetic_cache_info()["entries"] == 0


def test_manifest_path_traversal_rejected(art_manifest, monkeypatch):
    cosmetics.clear_cosmetic_cache()
    item = next(item for item in art_manifest["entries"] if item["id"] == "rain-love")
    item["files"]["compact"]["path"] = "../../../../../private.webp"
    monkeypatch.setattr(cosmetics, "_MANIFEST", {"rain-love": item})
    with pytest.raises(ValueError, match="路径"):
        cosmetics.cosmetic_detail("rain-love")


def test_missing_known_asset_preserves_name_without_exposing_path(art_manifest, monkeypatch):
    cosmetics.clear_cosmetic_cache()
    item = next(item for item in art_manifest["entries"] if item["id"] == "rain-love")
    item["files"]["compact"]["path"] = "rain-love/missing.webp"
    monkeypatch.setattr(cosmetics, "_MANIFEST", {"rain-love": item})
    card = cosmetics.cosmetic_detail("rain-love")
    assert card["name"] == "雨爱" and not card["available"]
    assert "missing.webp" not in repr(card)


def test_jinja_components_hide_locked_and_unknown_and_render_weekly_as_plate(art_manifest):
    cosmetics.clear_cosmetic_cache()
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(), undefined=StrictUndefined)
    env.globals["cosmetic_detail"] = cosmetics.cosmetic_detail
    template = env.from_string(
        "{% from 'cosmetic_components.html' import cosmetic_preview,cosmetic_frame %}"
        "{{ cosmetic_preview(value) }}{{ cosmetic_frame(frame) }}"
    )
    locked = template.render(value=cosmetics.cosmetic_detail("rain-love", revealed=False), frame="not-a-frame")
    assert "rain-love" not in locked and "雨爱" not in locked
    assert "data:image" not in locked and "file:" not in locked
    html = template.render(value="weekly-001-catch-value-rank-1", frame="frame-nine-colors")
    assert 'class="cosmetic-plate"' in html
    assert "cosmetic-edge" in html
    assert "抓猪冲刺！！！·1牌" in html
    assert "file:" not in html and "https:" not in html


def test_frame_css_is_decorative_and_does_not_shift_media_slot():
    css = (TEMPLATES / "cosmetic.css").read_text(encoding="utf-8")
    declaration = css.split(".cosmetic-edge{", 1)[1].split("}", 1)[0]
    assert "position:absolute" in declaration and "pointer-events:none" in declaration
    assert "border:20px" in declaration and "border-image-slice:64" in declaration
    assert "padding:" not in declaration and "margin:" not in declaration


def _weekly_environment() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(), undefined=StrictUndefined)
    env.globals["cosmetic_detail"] = cosmetics.cosmetic_detail
    return env


@pytest.mark.parametrize("rank,badge_rank", ((1, 1), (2, 2), (3, 3), (4, 10), (7, 10), (10, 10)))
def test_first_season_award_uses_real_rank_art_without_changing_actual_position(rank, badge_rank):
    html = (
        _weekly_environment()
        .get_template("weekly_competition_award.html")
        .render(view=replace(award(), final_rank=rank), font_family="sans-serif", theme_css="")
    )
    assert f'alt="抓猪冲刺！！！·{badge_rank}牌"' in html
    assert f"最终第 {rank} 名" in html
    assert 'alt="抓猪冲刺者"' in html
    assert 'alt="抓猪冲刺！！！·赛道边框"' in html
    assert "cosmetic-frame-preview" in html


def test_first_season_board_displays_all_four_art_previews_without_claiming_ownership():
    html = (
        _weekly_environment()
        .get_template("weekly_competition.html")
        .render(view=leaderboard(status="进行中"), font_family="sans-serif", theme_css="")
    )
    assert "预览不代表已经获奖" in html
    for rank in (1, 2, 3, 10):
        assert f'alt="抓猪冲刺！！！·{rank}牌"' in html


@pytest.mark.parametrize("season", (0, 2, 99))
def test_unregistered_season_never_reads_or_reuses_first_season_art(season):
    env = _weekly_environment()

    def forbid_asset_lookup(*args, **kwargs):
        pytest.fail("其他期不得读取或复用首期专属外观")

    env.globals["cosmetic_detail"] = forbid_asset_lookup
    for filename, view in (
        ("weekly_competition.html", replace(leaderboard(status="进行中"), season_number=season)),
        ("weekly_competition_award.html", replace(award(), season_number=season)),
    ):
        html = env.get_template(filename).render(view=view, font_family="sans-serif", theme_css="")
        assert "data:image" not in html
        assert "weekly-001" not in html
        assert "其他期" in html
