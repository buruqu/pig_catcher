"""旧收藏页面状态图形、真实身份称号批查和隐私投影。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from xml.etree import ElementTree

import pytest
from PIL import Image

from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.rendering import (
    CatalogItemViewModel,
    CatalogViewModel,
    FoodCatalogItemViewModel,
    FoodCatalogViewModel,
    InventoryItemViewModel,
    InventoryViewModel,
    PigCatcherRenderer,
    daily_giants_view,
    inventory_view,
    records_view,
)
from pig_catcher.rendering.asset_icons import ASSET_ICON_KEYS, asset_icon
from pig_catcher.services.gameplay import InventoryPage, PigView

from .helpers import FakeRender, build_message, create_test_plugin
from .test_gameplay import SequenceRandom
from .test_plugin import _command_kwargs, _install_test_pig
from .test_rendering import _options


@pytest.mark.parametrize("key", sorted(ASSET_ICON_KEYS))
def test_status_graphics_are_real_vectors_from_a_finite_whitelist(key):
    value = str(asset_icon(key))
    parsed = ElementTree.fromstring(value)
    assert any(node.tag.endswith("svg") for node in parsed.iter())
    assert any(node.tag.endswith("path") for node in parsed.iter())
    assert "<text" not in value
    assert "href=" not in value and "<script" not in value
    assert "http:" not in value.replace("http://www.w3.org/2000/svg", "")


@pytest.mark.parametrize(
    "value", ("../../private.png", "<script>steal()</script>", "https://example.invalid/x", {}, None)
)
def test_unknown_status_is_neutral_and_never_echoes_input(value):
    rendered = str(asset_icon(value))
    assert rendered == str(asset_icon("tag"))
    assert "private.png" not in rendered and "steal" not in rendered and "example.invalid" not in rendered


def test_physical_and_protection_icons_are_not_conflated():
    keys = (
        "giant",
        "mini",
        "dual-giant",
        "double-top",
        "double-mini",
        "favorite",
        "busy",
        "protected",
        "private",
        "hidden",
        "missing",
    )
    assert len({str(asset_icon(key)) for key in keys}) == len(keys)
    assert asset_icon("双项巨物") != asset_icon("双顶壮硕")
    assert asset_icon("派遣中") != asset_icon("乐队保护")


def _pig(**updates) -> PigView:
    pig = PigView(
        pig_instance_id="pig-one",
        short_code="ASSET001",
        scope_id="qq:10001",
        owner_player_id="qq:10001:20001",
        owner_display_name="玩家",
        template_id="pig",
        template_version=1,
        rarity=5,
        display_name="巨物猪",
        description="测试",
        size_value=1000,
        size_percentile=0.93,
        weight_value=20000,
        weight_percentile=0.9,
        fat_ratio=50,
        official_value=100,
        acquired_at="2026-08-28T00:00:00Z",
        image_relpath="safe/pig.png",
        image_fit="contain",
        media_format="PNG",
        is_animated=False,
        frame_count=1,
        media_visible=True,
        collection_name="",
        collection_total=0,
        character_name="",
        is_size_record=False,
        is_weight_record=False,
        body_label="双项巨物",
        display_tags=("巨物",),
    )
    return replace(pig, **updates)


@pytest.mark.parametrize(
    "size,weight,visible,label",
    (
        (0.92, 0.88, True, "双顶壮硕"),
        (0.91999, 0.99, True, ""),
        (0.99, 0.87999, True, ""),
        (0.08, 0.15, True, "双顶迷你"),
        (0.08001, 0.1, True, ""),
        (0.01, 0.15001, True, ""),
        (0.99, 0.99, False, ""),
        (0.01, 0.01, False, ""),
    ),
)
def test_snapshot_extreme_label_uses_existing_thresholds_without_recomputing_values(size, weight, visible, label):
    pig = _pig(size_percentile=size, weight_percentile=weight, media_visible=visible)
    page = InventoryPage("玩家", 1, 1, 1, 12, None, "价值", (pig,))
    view = inventory_view(page).items[0]
    assert view.extreme_label == label
    assert view.size_value == pig.size_value and view.weight_value == pig.weight_value
    assert view.official_value == pig.official_value and view.rarity == pig.rarity
    assert view.body_label == pig.body_label
    if not visible:
        assert not view.display_tags


@pytest.mark.asyncio
async def test_inventory_preserves_star_count_favorites_busy_status_and_private_media(tmp_path):
    source = tmp_path / "pig.png"
    Image.new("RGB", (64, 64), "#c7a5cb").save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    base = InventoryItemViewModel(
        "public",
        "测试猪",
        "ABCD1234",
        5,
        120,
        350,
        "均衡",
        500,
        True,
        False,
        "contain",
        body_label="双项巨物",
        extreme_label="双顶壮硕",
        is_favorite=True,
        activity_label="巡演中 · 第三站",
        display_tags=("巨物",),
    )
    private = replace(
        base,
        key="private",
        display_name="已撤权历史猪",
        media_visible=False,
        is_animated=True,
        display_tags=("绝不能展示的标签",),
        extreme_label="绝不能展示的体格",
    )
    renderer = PigCatcherRenderer(capability := FakeRender(), _options())
    view = InventoryViewModel("玩家", 1, 1, 2, None, "价值", (base, private))
    await renderer.render_inventory(view, {"public": source, "private": source})
    html = capability.calls[-1][0]
    assert "★★★★★ · #ABCD1234" in html
    for key in ("favorite", "busy", "dual-giant", "double-top", "private"):
        assert f'class="asset-icon asset-icon--{key}"' in html
    assert "绝不能展示" not in html
    assert html.count("data:image/webp;base64,") == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.asyncio
async def test_catalog_unseen_private_and_missing_are_distinct_without_reading_secret_media(monkeypatch):
    renderer = PigCatcherRenderer(capability := FakeRender(), _options())

    async def no_read(*args, **kwargs):
        pytest.fail("无授权图鉴不应读取任何原图")

    monkeypatch.setattr(renderer, "_cached_preview_data_url", no_read)
    base = CatalogItemViewModel("a", "普通猪", 4, False, 0, None, None, "", "", False, False, "contain")
    items = (
        base,
        replace(base, key="secret", display_name="隐藏六星真名", rarity=6, display_tags=("隐藏标签",)),
        replace(base, key="revoked", display_name="历史已见猪", discovered=True, is_animated=True),
        replace(base, key="missing", display_name="缺图猪", discovered=True, media_visible=True),
    )
    view = CatalogViewModel("玩家", 4, None, False, 2, 4, items)
    await renderer.render_catalog(view, {})
    html = capability.calls[-1][0]
    assert "隐藏六星真名" not in html and "隐藏标签" not in html
    assert "★★★★★★" in html
    for key in ("unseen", "hidden", "private", "missing"):
        assert f'class="asset-icon asset-icon--{key}"' in html
    assert "授权已撤回" in html and "图片暂缺" in html and "尚未发现" in html


@pytest.mark.asyncio
async def test_food_catalog_never_confuses_missing_picture_with_undiscovered_food():
    renderer = PigCatcherRenderer(capability := FakeRender(), _options())
    base = FoodCatalogItemViewModel("hidden", "隐藏食谱", 6, False, 0, None, False, False, "contain", "隐藏效果")
    items = (
        base,
        replace(
            base,
            key="missing",
            display_name="已发现美食",
            discovered=True,
            media_visible=True,
            effect_summary="已公开效果",
        ),
        replace(base, key="revoked", display_name="撤权美食", discovered=True),
    )
    await renderer.render_food_catalog(FoodCatalogViewModel("玩家", 3, None, False, 2, 3, items), {})
    html = capability.calls[-1][0]
    assert "隐藏食谱" not in html and "隐藏效果" not in html
    assert "已公开效果" in html
    for key in ("hidden", "missing", "private"):
        assert f'class="asset-icon asset-icon--{key}"' in html


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", ("achievements_enabled", "weekly_competitions_enabled"))
async def test_records_and_daily_boards_batch_equipped_titles_by_real_scoped_holder(tmp_path, monkeypatch, feature):
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={
            "features": {feature: True},
            "catching": {"cooldown_seconds": 0},
            "ranking": {"giant_size_threshold_cm": 1, "giant_weight_threshold_kg": 1},
        },
    )
    try:
        await _install_test_pig(plugin, tmp_path)
        actors = (
            CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", "同名玩家", "first", "测试群"),
            CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20002", "同名玩家", "second", "测试群"),
            CommandIdentity(ScopeKey("qq", "10002"), "stream-10002", "20001", "同名玩家", "other-scope", "另一个群"),
        )
        caught = []
        for actor, fraction in zip(actors, (0.95, 0.1, 0.95), strict=True):
            plugin.gameplay_service.random_source = SequenceRandom(0.1, 0.1, *([fraction] * 5))
            caught.append(await plugin.gameplay_service.catch(actor))
        repository = AchievementRepository()
        async with plugin.database.transaction() as session:
            for actor, title in zip(actors, ("rain-love", "title-traveler", "title-three-world-master"), strict=True):
                await repository.ensure_profile(session, player_id=actor.player_id, now="2026-08-28T00:00:00Z")
                await repository.grant_reward(
                    session,
                    player_id=actor.player_id,
                    reward_type="title",
                    reward_id=title,
                    quantity=1,
                    now="2026-08-28T00:00:00Z",
                )
                await session.execute(
                    "UPDATE achievement_profiles SET equipped_title_id=? WHERE player_id=?", (title, actor.player_id)
                )
            # 资产转到另一个同名玩家，不得让今日原抓取人的称号跟着资产改变。
            await session.execute(
                "UPDATE pig_instances SET owner_player_id=? WHERE pig_instance_id=?",
                (actors[1].player_id, caught[0].pig.pig_instance_id),
            )

        records = await plugin.gameplay_service.records(actors[0], page=1)
        assert {item.player_id for item in records.entries} == {actors[0].player_id}
        assert {item.player_id for item in records.global_entries} == {actors[0].player_id}
        assert {item.player_id for item in records.giant_sightings} == {actors[0].player_id, actors[1].player_id}
        assert records_view(records).items[0].player_id == actors[0].player_id
        daily = await plugin.gameplay_service.daily_giants(actors[0])
        assert daily.size_entries[0].player_id == actors[0].player_id
        assert daily_giants_view(daily).size_items[0].player_id == actors[0].player_id

        original = plugin._achievement_service.cosmetics_for_players
        batches = []

        async def batch_lookup(player_ids):
            batches.append(tuple(player_ids))
            return await original(player_ids)

        async def forbid_single(*args, **kwargs):
            pytest.fail("群纪录不得逐行查询外观")

        monkeypatch.setattr(plugin._achievement_service, "cosmetics_for_players", batch_lookup)
        monkeypatch.setattr(plugin._achievement_service, "cosmetics_for_player", forbid_single)
        for handler in (plugin.handle_records, plugin.handle_daily_giants):
            batches.clear()
            result = await handler(
                stream_id="stream-10001", **_command_kwargs(build_message(display_name="同名玩家"), arguments="1")
            )
            assert result[0]
            assert len(batches) == 1
            assert set(batches[0]) == {actors[0].player_id, actors[1].player_id}
            assert len(batches[0]) == 2
            html = context.render.calls[-1][0]
            assert 'alt="雨爱"' in html and 'alt="远行家"' in html
            assert 'alt="PiG Dream! 三栖达人"' not in html
            assert "qq:10001:20001" not in html and "qq:10002" not in html
    finally:
        await plugin.on_unload()
