"""全量制作阶段功能美术的确定性、输入边界及实际公开view投影。"""

from __future__ import annotations

import hashlib
import json
from base64 import b64decode
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image

from pig_catcher.domain.battle import new_state
from pig_catcher.domain.battle_catalog import FIGHTERS, INJURY_WHEELS
from pig_catcher.domain.battle_views import BattleView
from pig_catcher.domain.dispatch import MATERIALS, REGIONS, TOOLS
from pig_catcher.domain.dispatch_views import DispatchView
from pig_catcher.domain.gameplay import ITEM_DEFINITIONS
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.tour_catalog import INSTRUMENTS, THEME_EMBLEMS, VENUES
from pig_catcher.rendering import feature_art
from pig_catcher.rendering.adapters import _group_event_asset
from pig_catcher.rendering.feature_art import feature_backdrop, feature_icon, feature_scene, feature_wheel
from pig_catcher.services.battle_views import matchup, wheels
from pig_catcher.services.gameplay import TechniqueFoodView


def identity() -> CommandIdentity:
    return CommandIdentity(ScopeKey("qq-official", "art-fixture"), "stream", "private-openid", "渲染验收员", "msg")


@pytest.mark.parametrize("product", ITEM_DEFINITIONS, ids=lambda item: item.item_id)
def test_every_store_consumable_has_a_named_original_icon(product):
    by_name, by_id = feature_icon(product.display_name), feature_icon(product.item_id)
    assert by_name == by_id
    assert "回执" not in by_name
    root = ElementTree.fromstring(str(by_name))
    assert root.attrib["viewBox"] == "0 0 96 96"
    assert root.attrib["aria-label"]


def test_every_store_product_uses_a_distinct_icon():
    icons = {str(feature_icon(item.display_name)) for item in ITEM_DEFINITIONS}
    assert len(icons) == 16
    assert feature_icon("猪饲料升级") != feature_icon("厨具升级")


def test_all_theme_emblems_are_distinct_native_vectors_not_glyphs():
    icons = {str(feature_icon(symbol)) for symbol in THEME_EMBLEMS.values()}
    assert len(icons) == 10
    assert all("<path" in icon and "回执" not in icon for icon in icons)


def test_all_souvenirs_have_local_visual_identity():
    for region in REGIONS:
        for name in region.souvenirs:
            assert "回执" not in feature_icon(name)


def test_feature_caches_can_be_released():
    feature_icon("pig")
    feature_scene("grassland")
    feature_art.clear_feature_art_cache()
    assert feature_art._icon_svg.cache_info().currsize == 0
    assert feature_art._scene_svg.cache_info().currsize == 0
    assert feature_art._backdrop.cache_info().currsize == 0


def test_invalid_decorative_master_does_not_block_business_card(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_art, "_ASSETS", tmp_path)
    (tmp_path / "battle.png").write_bytes(b"not an image")
    feature_art.clear_feature_art_cache()
    assert feature_backdrop("battle") == ""


def test_domain_asset_projection_never_requires_price_missing_from_receipt():
    food = TechniqueFoodView(
        food_instance_id="food",
        short_code="BLUE1",
        owner_player_id="qq:fixture:member",
        owner_display_name="名为蓝的群友",
        rarity=5,
        display_name="五条猪无量苍蓝雪山",
        image_relpath="local.png",
        image_fit="contain",
        media_format="PNG",
        is_animated=False,
        media_visible=True,
    )
    asset = _group_event_asset(food, kind_label="美食")
    assert asset.name == food.display_name and asset.owner_name == "名为蓝的群友"
    assert asset.detail == "已自动出餐并写入背包"


def test_domain_asset_projection_hides_revoked_art_and_raw_identity():
    food = TechniqueFoodView(
        food_instance_id="food",
        short_code="HIDDEN1",
        owner_player_id="qq:fixture:private-openid",
        owner_display_name="private-openid",
        rarity=6,
        display_name="不该出现在公共卡的原名",
        image_relpath="secret.png",
        image_fit="contain",
        media_format="PNG",
        is_animated=False,
        media_visible=False,
    )
    asset = _group_event_asset(food, kind_label="美食")
    assert asset.name == "已隐藏的专属资产" and asset.owner_name == "未命名群友"
    assert "private-openid" not in str(asset) and food.display_name not in str(asset)


@pytest.mark.parametrize("name", (*MATERIALS.values(), *(item.name for item in TOOLS), *INSTRUMENTS.values()))
def test_material_tools_and_instruments_have_valid_icons(name):
    value = str(feature_icon(name))
    ElementTree.fromstring(value)
    assert "回执" not in value


@pytest.mark.parametrize("entry", (*REGIONS, *VENUES), ids=lambda item: item.name)
def test_each_route_or_stage_has_its_own_scene(entry):
    svg = str(feature_scene(entry.name))
    assert entry.name in svg
    assert ElementTree.fromstring(svg).attrib["viewBox"] == "0 0 320 140"


def test_scene_and_icon_do_not_interpret_arbitrary_markup_or_paths():
    unsafe = '<script>alert(1)</script><image href="https://private/secret.png"/>'
    assert str(feature_scene(unsafe)) == ""
    assert unsafe not in feature_icon(unsafe)
    assert feature_backdrop("../../config") == ""
    assert feature_backdrop("https://private/secret") == ""


@pytest.mark.parametrize("weight", [0, -1, float("nan"), float("inf"), 1_000_001])
def test_wheel_rejects_invalid_draw_weights(weight):
    with pytest.raises(ValueError):
        feature_wheel([{"label": "一", "weight": weight}])


@pytest.mark.parametrize("selection", [-1, 2, 0.0, True])
def test_wheel_requires_a_valid_actual_selection(selection):
    with pytest.raises(ValueError):
        feature_wheel([{"label": "一", "weight": 1}, {"label": "二", "weight": 2}], selection)


def test_wheel_bounds_generator_and_escapes_labels():
    consumed = []

    def items():
        while True:
            consumed.append(1)
            yield {"label": "一", "weight": 1}

    with pytest.raises(ValueError):
        feature_wheel(items())
    assert len(consumed) == 17
    values = [{"label": "<script>not trusted</script>", "weight": 1}, {"label": "二", "weight": 5}]
    svg = str(feature_wheel(values, 0))
    ElementTree.fromstring(svg)
    assert "<script>" not in svg
    assert "已抽中：&lt;script&gt;" in svg
    assert svg == str(feature_wheel(values, 0))
    assert "已抽中" not in str(feature_wheel(values))


def test_backdrop_is_bounded_local_derivative_and_preserves_master(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(feature_art, "_ASSETS", tmp_path)
    master = tmp_path / "travel.png"
    Image.new("RGB", (1448, 1086), "#a8d8bd").save(master)
    before = hashlib.sha256(master.read_bytes()).hexdigest()
    feature_art._backdrop.cache_clear()
    output = feature_backdrop("travel")
    data = b64decode(output.split(",", 1)[1])
    assert len(data) < 256 * 1024
    with Image.open(BytesIO(data)) as derivative:
        assert derivative.format == "WEBP"
        assert derivative.width <= 960 and derivative.height <= 360
    assert before == hashlib.sha256(master.read_bytes()).hexdigest()
    assert output == feature_backdrop("travel")
    assert feature_art._backdrop.cache_info().maxsize == 6
    feature_art._backdrop.cache_clear()


def test_old_dispatch_payload_and_new_battle_art_payload_roundtrip():
    payload = DispatchView("旧回执", "群友").payload()
    payload.pop("presentation")
    payload.pop("scene_key")
    assert DispatchView.from_payload(payload).presentation == "dispatch"
    rule_view = wheels(identity(), "sukuna", 5)
    restored = BattleView.from_payload(json.loads(json.dumps(rule_view.payload(), ensure_ascii=False)))
    assert restored == rule_view
    assert len(rule_view.wheels) == 6
    assert all(card.selected_index is None for card in rule_view.wheels)
    assert "private-openid" not in str(rule_view.payload())


def test_count_wheel_uses_pre_injury_state_after_core_heals():
    snapshots = [
        {
            "fighter_id": f.fighter_id,
            "level": 0,
            "name": f.name,
            "player_name": f"玩家{i}",
            "short_code": f"TEST{i}",
            "rarity": 5,
            "size_value": 55.0,
            "weight_value": 60.0,
            "template_id": f.template_id,
        }
        for i, f in enumerate(FIGHTERS)
    ]
    state = new_state(snapshots)
    state["sides"][0].update(heavy=True, risk=2)
    state["sides"][0]["turn"].update(raw=4, debt=2, effective=2, done=True)
    state["sides"][1]["turn"].update(raw=5, debt=0, effective=5, done=True)
    before = deepcopy(state["sides"])
    state["sides"][0].update(heavy=False, core=1)
    result = {
        "before": before,
        "after": state["sides"],
        "winner": 1,
        "loser": 0,
        "injury": "core",
        "injury_wheel": INJURY_WHEELS[2],
        "natural_end": False,
        "round": 1,
    }
    match = {"status": "active", "expires_ms": 999999, "battle_id": "battle-art"}
    snapshot = deepcopy(state)
    rendered = matchup(identity(), match, state, 0, round_result=result)
    first = rendered.wheels[0]
    assert [s.weight for s in first.segments] == [5, 4, 3, 2]
    assert first.selected_index == 3
    injury = rendered.wheels[-1]
    assert [s.weight for s in injury.segments] == [1, 2, 6, 1]
    assert injury.selected_index == 3
    assert state == snapshot
