"""MaiBot 生命周期、组件边界和命令级帮助验证。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sqlite3
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF
from PIL import Image

from pig_catcher.domain.models import CommandIdentity, ScopeKey

from .helpers import build_message, create_plugin, create_test_plugin, invoke_help


@pytest.mark.asyncio
async def test_plugin_loads_initializes_and_unloads_cleanly(tmp_path: Path) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    assert plugin.database is not None
    assert plugin.database.is_open
    assert plugin.renderer is not None
    assert plugin.animation_composer is not None
    assert (tmp_path / "pig_catcher.sqlite3").is_file()
    assert (tmp_path / "assets" / "catalogs").is_dir()
    await plugin.on_unload()
    assert plugin.database is None
    assert plugin.renderer is None
    assert plugin.animation_composer is None


@pytest.mark.asyncio
async def test_default_maintenance_task_starts_and_stops_cleanly(tmp_path: Path) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"maintenance": {"enabled": True}},
    )
    await asyncio.sleep(0)
    assert plugin._maintenance is not None
    assert plugin._maintenance.task is not None
    assert not plugin._maintenance.task.done()
    await plugin.on_unload()
    assert plugin.database is None


@pytest.mark.asyncio
async def test_config_reload_closes_and_reopens_runtime(tmp_path: Path) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    config = plugin.get_plugin_config_data()
    config["plugin"]["enabled"] = False
    plugin.set_plugin_config(config)
    await plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, config, "disabled")
    assert plugin.database is None

    config["plugin"]["enabled"] = True
    plugin.set_plugin_config(config)
    await plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, config, "enabled")
    assert plugin.database is not None
    assert plugin.database.is_open
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_admin_panel_one_shot_reset_is_scoped_and_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    assert plugin.gameplay_service is not None
    await plugin.gameplay_service.profile(
        CommandIdentity(
            scope=ScopeKey("qq", "10001"),
            stream_id="stream-10001",
            user_id="20001",
            display_name="测试管理员",
            message_id="profile-before-reset",
            group_name="抓猪测试群",
        )
    )
    cleared: list[bool] = []
    monkeypatch.setattr(
        plugin,
        "_clear_administration_triggers",
        lambda: cleared.append(True),
    )
    config = plugin.get_plugin_config_data()
    config["quota_administration"] = {
        "group_id": "10001",
        "execute_current_window_reset": True,
    }
    plugin.set_plugin_config(config)
    await plugin.on_config_update(
        CONFIG_RELOAD_SCOPE_SELF,
        config,
        "admin-reset",
    )
    assert cleared == [True]
    assert plugin.database is not None
    row = await plugin.database.fetch_one(
        """
        SELECT scope_id, actor_user_id, action
        FROM audit_events
        WHERE action = 'catch-quota-window-reset'
        """
    )
    assert row is not None
    assert tuple(row) == (
        "qq:10001",
        "maibot-admin-panel",
        "catch-quota-window-reset",
    )
    assert tuple((tmp_path / "backups").glob("*.sqlite3"))
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_group_reset_command_requires_configured_admin_and_is_idempotent(
    tmp_path: Path,
) -> None:
    admin_openid = "official-admin-openid"
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={
            "access": {"admin_user_ids": [f"qq-official:{admin_openid}"]},
            "catching": {"cooldown_seconds": 0},
        },
    )
    await _install_test_pig(plugin, tmp_path)
    catch_message = build_message(
        platform="qq-official",
        group_id="official-group",
        user_id=admin_openid,
        display_name="官方管理员",
        message_id="official-catch-before-reset",
    )
    caught = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(catch_message),
    )
    assert caught[0] is True

    reset_message = build_message(
        platform="qq-official",
        group_id="official-group",
        group_name="官方测试群",
        user_id=admin_openid,
        display_name="官方管理员",
        message_id="official-reset-once",
    )
    reset = await plugin.handle_reset_quota(
        stream_id="stream-10001",
        **_command_kwargs(reset_message),
    )
    assert reset[0] is True
    assert "已归零：1 次" in reset[1]
    assert len(context.send.texts) == 1
    assert len(tuple((tmp_path / "backups").glob("*.sqlite3"))) == 1

    duplicate = await plugin.handle_reset_quota(
        stream_id="stream-10001",
        **_command_kwargs(reset_message),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    assert len(context.send.texts) == 1
    assert len(tuple((tmp_path / "backups").glob("*.sqlite3"))) == 1
    rows = await plugin.database.fetch_all(
        """
        SELECT action, scope_id, actor_user_id
        FROM audit_events
        WHERE action = 'catch-quota-window-reset'
        """
    )
    assert [tuple(row) for row in rows] == [
        (
            "catch-quota-window-reset",
            "qq-official:official-group",
            admin_openid,
        )
    ]
    receipt = await plugin.database.fetch_one(
        """
        SELECT command_name, send_status
        FROM command_receipts
        WHERE command_name = 'pig-catcher.reset-quota'
        """
    )
    assert receipt is not None
    assert tuple(receipt) == ("pig-catcher.reset-quota", "sent")
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_group_reset_command_rejects_unconfigured_user_before_backup(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"access": {"admin_user_ids": ["configured-admin"]}},
    )
    await plugin.gameplay_service.profile(
        CommandIdentity(
            scope=ScopeKey("qq", "10001"),
            stream_id="stream-10001",
            user_id="configured-admin",
            display_name="已配置管理员",
            message_id="seed-admin-scope",
            group_name="抓猪测试群",
        )
    )
    denied = await plugin.handle_reset_quota(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(
                user_id="not-admin",
                display_name="普通成员",
                message_id="unauthorized-reset",
            )
        ),
    )
    assert denied[0] is False
    assert "只有插件配置中的管理员" in denied[1]
    assert len(context.send.texts) == 1
    assert not (tmp_path / "backups").exists()
    assert await plugin.database.fetch_one(
        "SELECT 1 FROM audit_events WHERE action = 'catch-quota-window-reset'"
    ) is None
    await plugin.on_unload()


def test_plugin_registers_only_explicit_production_commands() -> None:
    plugin = create_plugin()
    components = plugin.get_components()
    assert len(components) == 45
    commands = {
        component["name"]
        for component in components
        if component["type"] == "COMMAND"
    }
    assert commands == {
        "pig_catcher_help",
        "pig_catcher_reset_quota",
        "pig_catcher_reset_quota_chance",
        "pig_catcher_admin_help",
        "pig_catcher_admin_grant_coins",
        "pig_catcher_admin_deduct_coins",
        "pig_catcher_admin_grant_coins_all",
        "pig_catcher_admin_deduct_coins_all",
        "pig_catcher_admin_grant_asset",
        "pig_catcher_admin_remove_asset",
        "pig_catcher_admin_blacklist",
        "pig_catcher_admin_reset_player_quota",
        "pig_catcher_catch",
        "pig_catcher_profile",
        "pig_catcher_pig_detail",
        "pig_catcher_inventory",
        "pig_catcher_catalog",
        "pig_catcher_records",
        "pig_catcher_use_item",
        "pig_catcher_cancel_item",
        "pig_catcher_cook",
        "pig_catcher_batch_cook",
        "pig_catcher_food_detail",
        "pig_catcher_food_inventory",
        "pig_catcher_food_catalog",
        "pig_catcher_eat",
        "pig_catcher_store",
        "pig_catcher_purchase",
        "pig_catcher_upgrade",
        "pig_catcher_sell_pig",
        "pig_catcher_sell_food",
        "pig_catcher_batch_sell",
        "pig_catcher_ledger",
        "pig_catcher_gift",
        "pig_catcher_trade_offer",
        "pig_catcher_trade_accept",
        "pig_catcher_trade_reject",
        "pig_catcher_trade_cancel",
        "pig_catcher_trade_list",
        "pig_catcher_showcase",
        "pig_catcher_ranking",
        "pig_catcher_toggle_baogian",
        "pig_catcher_enable_batch_keep",
        "pig_catcher_disable_batch_keep",
    }
    home_card = next(
        component
        for component in components
        if component["type"] == "HOME_CARD"
    )
    assert home_card["name"] == "pig_catcher_quota_control"
    assert "打开运营控制" in str(home_card)
    assert "社交黑名单" in str(home_card)
    assert "群公告" in str(home_card)
    assert "/plugin-config?plugin=local.pig-catcher" in str(home_card)
    serialized = str(components)
    assert "EVENT_HANDLER" not in serialized
    assert "LLM" not in serialized
    assert "TOOL" not in serialized


def test_store_command_patterns_do_not_claim_livehouse_commands() -> None:
    components = {
        component["name"]: component
        for component in create_plugin().get_components()
    }
    purchase_pattern = components["pig_catcher_purchase"]["metadata"][
        "command_pattern"
    ]
    upgrade_pattern = components["pig_catcher_upgrade"]["metadata"][
        "command_pattern"
    ]

    for text in (
        "/购买 幸运猪哨 2",
        "/购买 超级幸运猪哨",
        "/购买 超级主厨香料 2",
        "/购买 超级幸运猪哨 数量错误",
        "@小马哥bot测试机 /购买 超级幸运猪哨",
        "<@!bot-openid> /购买 超级幸运猪哨",
        "[CQ:at,qq=1353436150] /购买 超级主厨香料",
    ):
        assert re.search(purchase_pattern, text), text
    for text in (
        "/升级 猪饲料",
        "/升级 厨具",
        "@小马哥bot测试机 /升级 厨具",
        "<@bot-openid> /升级 猪饲料",
    ):
        assert re.search(upgrade_pattern, text), text
    assert re.search(purchase_pattern, "/购买")  # 保留缺参时的格式提示。
    assert re.search(upgrade_pattern, "/升级")

    for text in (
        "/购买 练习券 2",
        "@小马哥bot测试机 /购买 招募券 3",
        "<@!bot-openid> /购买 蓝魔方",
    ):
        assert re.search(purchase_pattern, text) is None, text
    assert re.search(upgrade_pattern, "/升级 #3 满级") is None
    assert re.search(upgrade_pattern, "/升级 户山香澄") is None


def test_admin_command_patterns_claim_only_the_documented_syntax() -> None:
    components = {
        component["name"]: component
        for component in create_plugin().get_components()
    }
    examples = {
        "pig_catcher_admin_help": ("/猪管帮助",),
        "pig_catcher_admin_grant_coins": ("/猪管发币 @玩家 100",),
        "pig_catcher_admin_deduct_coins": ("/猪管扣币 official-openid 100",),
        "pig_catcher_admin_grant_coins_all": ("/猪管全员发币 100",),
        "pig_catcher_admin_deduct_coins_all": ("/猪管全员扣币 100",),
        "pig_catcher_admin_grant_asset": (
            "/猪管发猪 @玩家 地球猪",
            "/猪管发菜 official-openid 彩彩修车饭 A1B2C3D4",
        ),
        "pig_catcher_admin_remove_asset": (
            "/猪管删猪 @玩家 地球猪#A1B2C3D4",
            "/猪管删菜 official-openid 彩彩修车饭#1234ABCD",
        ),
        "pig_catcher_admin_blacklist": (
            "/猪管黑名单",
            "/猪管黑名单 加入 交易 @玩家 复核原因",
        ),
        "pig_catcher_admin_reset_player_quota": ("/猪管重置玩家 @玩家",),
    }
    for component_name, commands in examples.items():
        pattern = components[component_name]["metadata"]["command_pattern"]
        for command in commands:
            assert re.search(pattern, command), (component_name, command)

    asset_pattern = components["pig_catcher_admin_grant_asset"]["metadata"][
        "command_pattern"
    ]
    assert re.search(asset_pattern, "/猪管发币 @玩家 100") is None


def test_plugin_exposes_fully_chinese_webui_schema() -> None:
    plugin = create_plugin()
    schema = plugin.get_webui_config_schema(
        plugin_id="local.pig-catcher",
        plugin_name="抓猪插件",
        plugin_version="0.2.0",
    )
    serialized = str(schema)
    assert "插件设置" in serialized
    assert "访问控制" in serialized
    assert "群黑名单" in serialized
    assert "用户白名单" in serialized
    assert "图片展示" in serialized


@pytest.mark.asyncio
async def test_help_command_sends_copyable_text_without_rendering(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(tmp_path)
    success, text, level = await invoke_help(plugin)
    assert success is True
    assert level == 2
    assert "/抓猪帮助 [抓猪|背包|道具|做菜|商城|交易|排行]" in text
    assert "当前版本：" not in text
    assert "已开放抓猪" not in text
    assert "/抓猪档案" not in text
    assert "/抓猪详情" not in text
    assert context.send.texts == [("stream-10001", text)]
    assert context.send.images == []
    assert context.render.calls == []
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_help_topic_and_unknown_topic_are_explicit(tmp_path: Path) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    success, text, _ = await invoke_help(plugin, topic="交易")
    assert success
    assert "/接受交易 <交易号>" in text
    _, unknown, _ = await invoke_help(plugin, topic="不存在")
    assert "未知帮助主题：不存在" in unknown
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_help_obeys_group_and_user_access_controls(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={
            "access": {
                "group_whitelist": ["10001"],
                "user_blacklist": ["20001"],
            }
        },
    )
    success, text, level = await invoke_help(plugin)
    assert not success
    assert level == 1
    assert text == "当前群或账号未启用抓猪插件。"
    assert context.send.texts[-1][1] == text
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_silent_access_denial_sends_nothing(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={
            "access": {
                "group_blacklist": ["10001"],
                "notify_denied": False,
            }
        },
    )
    assert await invoke_help(plugin) == (False, "", 0)
    assert context.send.texts == []
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_private_message_is_rejected(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(tmp_path)
    success, text, level = await invoke_help(
        plugin,
        message=build_message(private=True),
    )
    assert not success
    assert level == 1
    assert text == "抓猪插件只能在群聊中使用。"
    assert context.send.texts[-1][1] == text
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_disabled_help_feature_returns_clear_error(tmp_path: Path) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"features": {"help_enabled": False}},
    )
    success, text, _ = await invoke_help(plugin)
    assert not success
    assert "已关闭“抓猪帮助”" in text
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_disabled_plugin_does_not_open_database(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"plugin": {"enabled": False}},
    )
    assert plugin.database is None
    success, text, _ = await invoke_help(plugin)
    assert not success
    assert "当前已在管理面板中停用" in text
    assert context.render.calls == []
    await plugin.on_unload()


async def _install_test_pig(
    plugin: Any,
    tmp_path: Path,
    *,
    animated: bool = False,
    include_food: bool = False,
    alternate: bool = False,
) -> None:
    source = tmp_path / "command-assets"
    source.mkdir()
    image_name = "command-pig.gif" if animated else "command-pig.png"
    if animated:
        frames = [
            Image.new("RGBA", (256, 256), color)
            for color in ("#F58CAD", "#66BFA3", "#5B8FD1")
        ]
        frames[0].save(
            source / image_name,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 120, 160],
            loop=2,
            disposal=2,
        )
    else:
        Image.new("RGBA", (256, 256), "#F58CAD").save(
            source / image_name,
            format="PNG",
        )
    alternate_name = "command-pig-sticker.png"
    if alternate:
        Image.new("RGBA", (256, 256), "#66BFA3").save(
            source / alternate_name,
            format="PNG",
        )
    entries = [
        {
            "template_id": "command-pig",
            "kind": "pig",
            "display_name": "命令测试猪",
            "rarity": 1,
            "scope": "common",
            "group_scope_id": None,
            "description": "用于命令级完整流程验收。",
            "image": image_name,
            "alternate_image": alternate_name if alternate else "",
            "fit": "contain",
            "source": "pytest",
            "license": "test-only",
            "consent_status": "not-required",
            "length_min_cm": 30,
            "length_max_cm": 60,
            "weight_min_kg": 20,
            "weight_max_kg": 90,
            "fat_profile": "balanced",
            "recipe_tags": ["测试"],
        }
    ]
    if include_food:
        for rarity in (1, 2, 3):
            food_image = f"command-food-{rarity}.png"
            Image.new("RGBA", (256, 256), "#F7A7C4").save(
                source / food_image,
                format="PNG",
            )
            entries.append(
                {
                    "template_id": f"command-food-{rarity}",
                    "kind": "food",
                    "display_name": f"命令测试菜{rarity}",
                    "rarity": rarity,
                    "scope": "common",
                    "group_scope_id": None,
                    "description": "用于第四轮命令级完整流程验收。",
                    "image": food_image,
                    "fit": "contain",
                    "source": "pytest",
                    "license": "test-only",
                    "consent_status": "not-required",
                    "recipe_tags": ["家常"],
                    "effect_id": "",
                }
            )
    manifest = source / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "catalog_id": "command-tests",
                "source_label": "pytest command catalog",
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    await plugin._asset_service.import_manifest(manifest)


def _command_kwargs(
    message: dict[str, object],
    **groups: str | None,
) -> dict[str, object]:
    return {
        "matched_groups": groups,
        "raw_message": "",
        "message": message,
    }


@pytest.mark.asyncio
async def test_complete_third_round_command_flow_and_duplicate_publication(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path)
    message = build_message(message_id="catch-once")
    first = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert first[0] is True
    assert len(context.send.images) == 1
    assert context.send.texts == []
    duplicate = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    assert len(context.send.images) == 1

    row = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:20001'
        """
    )
    assert row is not None
    selector = f"{row['display_name_snapshot']}#{row['short_code']}"
    query_message = build_message(message_id="query")
    await plugin.handle_profile(
        stream_id="stream-10001",
        **_command_kwargs(query_message),
    )
    await plugin.handle_pig_detail(
        stream_id="stream-10001",
        **_command_kwargs(query_message, selector=selector),
    )
    await plugin.handle_inventory(
        stream_id="stream-10001",
        **_command_kwargs(query_message, arguments="1 排序=价值"),
    )
    await plugin.handle_catalog(
        stream_id="stream-10001",
        **_command_kwargs(query_message, arguments=""),
    )
    await plugin.handle_records(
        stream_id="stream-10001",
        **_command_kwargs(query_message, arguments="1"),
    )
    assert len(context.send.images) == 6
    assert len(context.render.calls) == 6
    assert all(call[1]["allow_network"] is False for call in context.render.calls)
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_alternate_catch_preface_is_not_resent_for_duplicate_delivery(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path, alternate=True)
    message = build_message(message_id="alternate-catch-once")
    first = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert first[0] is True
    assert len(context.send.images) == 2

    duplicate = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    assert len(context.send.images) == 2
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_complete_fourth_round_command_flow_and_duplicate_publication(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path, include_food=True)

    await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="round4-catch-1")),
    )
    pig_row = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:20001' AND state = 'active'
        ORDER BY acquired_at DESC
        LIMIT 1
        """
    )
    assert pig_row is not None
    pig_selector = f"{pig_row['display_name_snapshot']}#{pig_row['short_code']}"
    cook_message = build_message(message_id="round4-cook-1")
    cooked = await plugin.handle_cook(
        stream_id="stream-10001",
        **_command_kwargs(cook_message, selector=pig_selector),
    )
    assert cooked[0] is True
    assert len(context.send.images) == 2
    duplicate = await plugin.handle_cook(
        stream_id="stream-10001",
        **_command_kwargs(cook_message, selector=pig_selector),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    assert len(context.send.images) == 2

    food_row = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM food_instances
        WHERE owner_player_id = 'qq:10001:20001' AND state = 'active'
        ORDER BY acquired_at DESC
        LIMIT 1
        """
    )
    assert food_row is not None
    food_selector = f"{food_row['display_name_snapshot']}#{food_row['short_code']}"
    query_message = build_message(message_id="round4-query")
    await plugin.handle_food_detail(
        stream_id="stream-10001",
        **_command_kwargs(query_message, selector=food_selector),
    )
    await plugin.handle_food_inventory(
        stream_id="stream-10001",
        **_command_kwargs(query_message, arguments="1 排序=价值"),
    )
    await plugin.handle_food_catalog(
        stream_id="stream-10001",
        **_command_kwargs(query_message, arguments=""),
    )
    assert len(context.send.images) == 5

    eaten = await plugin.handle_eat(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(message_id="round4-eat"),
            selector=food_selector,
        ),
    )
    assert eaten[0] is True
    assert len(context.send.images) == 6

    player = await plugin.database.fetch_one(
        "SELECT coin_balance FROM players WHERE player_id = 'qq:10001:20001'"
    )
    assert player is not None
    balance_after_seed = int(player["coin_balance"]) + 2000
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            UPDATE players
            SET coin_balance = coin_balance + 2000
            WHERE player_id = 'qq:10001:20001'
            """
        )
        await session.execute(
            """
            INSERT INTO currency_ledger(
                ledger_entry_id, player_id, scope_id, amount, balance_after,
                reason_code, reason_text, source_object_type, source_object_id,
                idempotency_key, created_at
            )
            VALUES (
                'round4-seed', 'qq:10001:20001', 'qq:10001', 2000, ?,
                'test-grant', '命令测试入账', 'test', 'seed',
                'round4-seed', '2026-07-28T00:00:00.000Z'
            )
            """,
            (balance_after_seed,),
        )

    await plugin.handle_store(
        stream_id="stream-10001",
        **_command_kwargs(query_message, arguments="分类=全部"),
    )
    purchase_message = build_message(message_id="round4-purchase")
    purchased = await plugin.handle_purchase(
        stream_id="stream-10001",
        **_command_kwargs(purchase_message, arguments="幸运猪哨 2"),
    )
    assert purchased[0] is True
    purchase_duplicate = await plugin.handle_purchase(
        stream_id="stream-10001",
        **_command_kwargs(purchase_message, arguments="幸运猪哨 2"),
    )
    assert purchase_duplicate[2] == 0
    assert len(context.send.images) == 8

    await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="round4-catch-2")),
    )
    second_pig = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:20001' AND state = 'active'
        ORDER BY acquired_at DESC, pig_instance_id DESC
        LIMIT 1
        """
    )
    assert second_pig is not None
    second_pig_selector = (
        f"{second_pig['display_name_snapshot']}#{second_pig['short_code']}"
    )
    await plugin.handle_cook(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(message_id="round4-cook-2"),
            selector=second_pig_selector,
        ),
    )
    second_food = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM food_instances
        WHERE owner_player_id = 'qq:10001:20001' AND state = 'active'
        ORDER BY acquired_at DESC, food_instance_id DESC
        LIMIT 1
        """
    )
    assert second_food is not None
    second_food_selector = (
        f"{second_food['display_name_snapshot']}#{second_food['short_code']}"
    )
    await plugin.handle_sell_food(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(message_id="round4-sell-food"),
            selector=second_food_selector,
        ),
    )

    await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="round4-catch-3")),
    )
    third_pig = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:20001' AND state = 'active'
        ORDER BY acquired_at DESC, pig_instance_id DESC
        LIMIT 1
        """
    )
    assert third_pig is not None
    third_pig_selector = (
        f"{third_pig['display_name_snapshot']}#{third_pig['short_code']}"
    )
    await plugin.handle_sell_pig(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(message_id="round4-sell-pig"),
            selector=third_pig_selector,
        ),
    )
    await plugin.handle_ledger(
        stream_id="stream-10001",
        **_command_kwargs(query_message, arguments="1"),
    )
    await plugin.handle_profile(
        stream_id="stream-10001",
        **_command_kwargs(query_message),
    )
    assert len(context.send.images) == 15
    assert all(call[1]["allow_network"] is False for call in context.render.calls)
    item = await plugin.database.fetch_one(
        """
        SELECT quantity
        FROM item_inventory
        WHERE player_id = 'qq:10001:20001' AND item_id = 'lucky-whistle'
        """
    )
    assert item is not None and item["quantity"] == 2
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_complete_fifth_round_command_flow_with_structured_mention(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path)
    for index in range(2):
        caught = await plugin.handle_catch(
            stream_id="stream-10001",
            **_command_kwargs(build_message(message_id=f"round5-catch-{index}")),
        )
        assert caught[0] is True
    rows = await plugin.database.fetch_all(
        """
        SELECT pig_instance_id, display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:20001' AND state = 'active'
        ORDER BY acquired_at, pig_instance_id
        """
    )
    assert len(rows) == 2
    selectors = [
        f"{row['display_name_snapshot']}#{row['short_code']}" for row in rows
    ]

    gift_message = build_message(message_id="round5-gift")
    gift_message["raw_message"] = [
        {"type": "text", "data": f"/猪猪赠送 {selectors[0]} "},
        {
            "type": "at",
            "data": {
                "target_user_id": "20002",
                "target_user_cardname": "接收成员",
            },
        },
    ]
    gifted = await plugin.handle_gift(
        stream_id="stream-10001",
        **_command_kwargs(
            gift_message,
            kind="猪猪",
            arguments=f"{selectors[0]} @接收成员",
        ),
    )
    assert gifted[0] is True
    duplicate = await plugin.handle_gift(
        stream_id="stream-10001",
        **_command_kwargs(
            gift_message,
            kind="猪猪",
            arguments=f"{selectors[0]} @接收成员",
        ),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)

    async with plugin.database.transaction() as session:
        await session.execute(
            """
            UPDATE players
            SET coin_balance = 500, updated_at = '2026-07-28T04:00:00.000Z'
            WHERE player_id = 'qq:10001:20002'
            """
        )
        await session.execute(
            """
            INSERT INTO currency_ledger(
                ledger_entry_id, player_id, scope_id, amount, balance_after,
                reason_code, reason_text, source_object_type, source_object_id,
                idempotency_key, created_at
            )
            VALUES(
                'round5-seed', 'qq:10001:20002', 'qq:10001', 500, 500,
                'test-grant', '第五轮命令测试入账', 'test', 'seed',
                'round5-seed', '2026-07-28T04:00:00.000Z'
            )
            """
        )

    offer_message = build_message(message_id="round5-offer")
    offer_message["raw_message"] = gift_message["raw_message"]
    offered = await plugin.handle_trade_offer(
        stream_id="stream-10001",
        **_command_kwargs(
            offer_message,
            kind="猪猪",
            arguments=f"{selectors[1]} @接收成员 120",
        ),
    )
    assert offered[0] is True
    offer = await plugin.database.fetch_one(
        "SELECT trade_id FROM trade_offers WHERE status = 'pending'"
    )
    assert offer is not None
    trade_id = str(offer["trade_id"])

    buyer_message = build_message(
        user_id="20002",
        display_name="接收成员",
        message_id="round5-accept",
    )
    accepted = await plugin.handle_trade_accept(
        stream_id="stream-10001",
        **_command_kwargs(buyer_message, arguments=trade_id),
    )
    assert accepted[0] is True
    await plugin.handle_trade_list(
        stream_id="stream-10001",
        **_command_kwargs(buyer_message, arguments="全部 1"),
    )
    await plugin.handle_showcase(
        stream_id="stream-10001",
        **_command_kwargs(
            {**buyer_message, "message_id": "round5-showcase"},
            arguments=f"猪猪 {selectors[0]}",
        ),
    )
    ranked = await plugin.handle_ranking(
        stream_id="stream-10001",
        **_command_kwargs(
            {**buyer_message, "message_id": "round5-ranking"},
            arguments="综合 1",
        ),
    )
    assert ranked[0] is True
    assert len(context.send.images) == 8
    assert all(call[1]["allow_network"] is False for call in context.render.calls)

    ownership = await plugin.database.fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:20002' AND state = 'active'
        """
    )
    assert ownership is not None and ownership["count"] == 2
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_cooking_render_failure_falls_back_once_without_rollback(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path, include_food=True)
    await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="cook-fallback-catch")),
    )
    pig = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE state = 'active'
        LIMIT 1
        """
    )
    assert pig is not None
    selector = f"{pig['display_name_snapshot']}#{pig['short_code']}"
    context.render.error = RuntimeError("chromium unavailable")
    message = build_message(message_id="cook-fallback")
    result = await plugin.handle_cook(
        stream_id="stream-10001",
        **_command_kwargs(message, selector=selector),
    )
    assert result[0] is True
    assert len(context.send.texts) == 1
    assert "【做菜成功】" in context.send.texts[0][1]
    duplicate = await plugin.handle_cook(
        stream_id="stream-10001",
        **_command_kwargs(message, selector=selector),
    )
    assert duplicate[2] == 0
    assert len(context.send.texts) == 1
    counts = await plugin.database.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM food_instances) AS foods,
            (SELECT COUNT(*) FROM command_receipts WHERE command_name = 'pig-catcher.cook') AS receipts
        """
    )
    assert counts is not None
    assert tuple(counts) == (1, 1)
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_catch_render_failure_falls_back_once_without_rollback(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path)
    context.render.error = RuntimeError("chromium unavailable")
    message = build_message(message_id="fallback-catch")
    result = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert result[0] is True
    assert context.send.images == []
    assert len(context.send.texts) == 1
    assert "【抓猪成功】" in context.send.texts[0][1]
    duplicate = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert duplicate[2] == 0
    assert len(context.send.texts) == 1
    row = await plugin.database.fetch_one(
        "SELECT COUNT(*) AS count FROM pig_instances"
    )
    assert row is not None and row["count"] == 1
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_catch_image_send_failure_falls_back_once_across_restart(
    tmp_path: Path,
) -> None:
    first_plugin, first_context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(first_plugin, tmp_path)
    first_context.send.image_error = RuntimeError("qq image transport unavailable")
    message = build_message(message_id="image-send-fallback")
    result = await first_plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert result[0] is True
    assert len(first_context.send.images) == 1
    assert len(first_context.send.texts) == 1
    assert "【抓猪成功】" in first_context.send.texts[0][1]
    receipt = await first_plugin.database.fetch_one(
        """
        SELECT send_status
        FROM command_receipts
        WHERE command_name = 'pig-catcher.catch'
        """
    )
    assert receipt is not None and receipt["send_status"] == "sent"
    await first_plugin.on_unload()

    second_plugin, second_context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    duplicate = await second_plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    assert second_context.send.images == []
    assert second_context.send.texts == []
    await second_plugin.on_unload()


@pytest.mark.asyncio
@pytest.mark.parametrize("animated", [False, True])
async def test_missing_pig_asset_uses_image_placeholder(
    tmp_path: Path,
    *,
    animated: bool,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path, animated=animated)
    row = await plugin.database.fetch_one(
        "SELECT image_relpath FROM pig_templates WHERE template_id = 'command-pig'"
    )
    assert row is not None
    source_path = (tmp_path / str(row["image_relpath"])).resolve()
    assert source_path.is_relative_to(tmp_path.resolve())
    source_path.unlink()

    result = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id=f"missing-asset-{animated}")),
    )
    assert result[0] is True
    assert len(context.send.images) == 1
    assert context.send.texts == []
    assert "素材文件暂时不可用" in context.render.calls[-1][0]
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_database_busy_rejects_catch_without_partial_state(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={
            "storage": {"sqlite_busy_timeout_ms": 100},
            "catching": {"cooldown_seconds": 0},
        },
    )
    await _install_test_pig(plugin, tmp_path)
    external = sqlite3.connect(tmp_path / "pig_catcher.sqlite3", isolation_level=None)
    external.execute("PRAGMA busy_timeout = 100")
    external.execute("BEGIN IMMEDIATE")
    try:
        result = await plugin.handle_catch(
            stream_id="stream-10001",
            **_command_kwargs(build_message(message_id="database-busy")),
        )
    finally:
        external.rollback()
        external.close()

    assert result[0] is False
    assert "抓猪暂时不可用" in result[1]
    assert len(context.send.texts) == 1
    counts = await plugin.database.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM pig_instances) AS pigs,
            (SELECT COUNT(*) FROM command_receipts) AS receipts
        """
    )
    assert counts is not None and tuple(counts) == (0, 0)
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_maintenance_reports_ledger_and_asset_faults_without_repair(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    await _install_test_pig(plugin, tmp_path)
    await plugin._framework_service.touch_identity(
        CommandIdentity(
            scope=ScopeKey("qq", "10001"),
            stream_id="stream-10001",
            user_id="20001",
            display_name="巡检测试成员",
            message_id="maintenance-seed",
            group_name="巡检测试群",
        )
    )
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            UPDATE players
            SET coin_balance = 9
            WHERE player_id = 'qq:10001:20001'
            """
        )
    row = await plugin.database.fetch_one(
        "SELECT image_relpath FROM pig_templates WHERE template_id = 'command-pig'"
    )
    assert row is not None
    source_path = (tmp_path / str(row["image_relpath"])).resolve()
    source_path.unlink()

    report = await plugin._maintenance.run_once()
    assert report.integrity_results == ("ok",)
    assert report.ledger_mismatch_count == 1
    assert report.active_asset_file_count == 1
    assert report.missing_asset_file_count == 1
    player = await plugin.database.fetch_one(
        """
        SELECT coin_balance
        FROM players
        WHERE player_id = 'qq:10001:20001'
        """
    )
    assert player is not None and player["coin_balance"] == 9
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_item_commands_use_inventory_and_are_idempotent(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(tmp_path)
    identity_message = build_message(message_id="arm-item")
    await plugin._framework_service.touch_identity(
        CommandIdentity(
            scope=ScopeKey("qq", "10001"),
            stream_id="stream-10001",
            user_id="20001",
            display_name="测试成员",
            message_id="seed",
            group_name="抓猪测试群",
        )
    )
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES ('qq:10001:20001', 'giant-corn', 2, '2026-07-28T00:00:00.000Z')
            """
        )
    armed = await plugin.handle_use_item(
        stream_id="stream-10001",
        **_command_kwargs(identity_message, item_name="巨物玉米"),
    )
    assert armed[0] is True
    assert len(context.send.images) == 1
    duplicate = await plugin.handle_use_item(
        stream_id="stream-10001",
        **_command_kwargs(identity_message, item_name="巨物玉米"),
    )
    assert duplicate[2] == 0
    cancel_message = build_message(message_id="cancel-item")
    cancelled = await plugin.handle_cancel_item(
        stream_id="stream-10001",
        **_command_kwargs(cancel_message, action="抓猪"),
    )
    assert cancelled[0] is True
    assert len(context.send.images) == 2
    row = await plugin.database.fetch_one(
        """
        SELECT quantity
        FROM item_inventory
        WHERE player_id = 'qq:10001:20001' AND item_id = 'giant-corn'
        """
    )
    assert row is not None and row["quantity"] == 2
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_sent_receipt_remains_suppressed_after_plugin_restart(
    tmp_path: Path,
) -> None:
    first_plugin, first_context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(first_plugin, tmp_path)
    message = build_message(message_id="restart-duplicate")
    await first_plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert len(first_context.send.images) == 1
    await first_plugin.on_unload()

    second_plugin, second_context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    duplicate = await second_plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(message),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    assert second_context.send.images == []
    assert second_context.send.texts == []
    assert second_context.render.calls == []
    await second_plugin.on_unload()


@pytest.mark.asyncio
async def test_animated_catch_command_preserves_all_frames_and_loop(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path, animated=True)
    result = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="animated-catch")),
    )
    assert result[0] is True
    assert len(context.send.images) == 1
    raw = base64.b64decode(context.send.images[0][1])
    with Image.open(BytesIO(raw)) as image:
        assert image.format == "GIF"
        assert image.n_frames == 3
        assert image.info["loop"] == 2
        durations = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            durations.append(image.info["duration"])
    assert durations == [80, 120, 160]
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_quota_reset_chance_requires_held_effect_and_consumes_once(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}, "cooking": {"cook_cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path, include_food=True)
    # 抓猪 + 做菜，产生一个真实美食实例作为效果来源（外键约束）
    await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="chance-seed-catch")),
    )
    pig_row = await plugin.database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:20001' AND state = 'active'
        ORDER BY acquired_at DESC LIMIT 1
        """
    )
    assert pig_row is not None
    await plugin.handle_cook(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(message_id="chance-seed-cook"),
            selector=f"{pig_row['display_name_snapshot']}#{pig_row['short_code']}",
        ),
    )
    food_row = await plugin.database.fetch_one(
        """
        SELECT food_instance_id
        FROM food_instances
        WHERE owner_player_id = 'qq:10001:20001'
        ORDER BY acquired_at DESC LIMIT 1
        """
    )
    assert food_row is not None
    food_id = str(food_row["food_instance_id"])
    chance_message = build_message(
        group_id="10001",
        user_id="20001",
        display_name="普通成员",
        message_id="chance-reset-1",
    )
    # 未持有重置机会：拒绝
    denied = await plugin.handle_reset_quota_chance(
        stream_id="stream-10001",
        **_command_kwargs(chance_message),
    )
    assert denied[0] is False
    assert "糖醋排骨" in denied[1]

    # 插入一次 quota-reset 效果后再次调用：成功且只消耗一次
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
            """,
            (
                "chance-effect-1",
                "qq:10001:20001",
                food_id,
                "quota-reset",
                '{"count":1}',
                1,
                "2026-07-28T00:00:00.000Z",
                "2026-07-28T00:00:00.000Z",
            ),
        )
    granted = await plugin.handle_reset_quota_chance(
        stream_id="stream-10001",
        **_command_kwargs(chance_message),
    )
    assert granted[0] is True
    assert "已归零：1 次" in granted[1]
    assert len(context.send.texts) == 2  # 拒绝提示 + 成功回执
    leftover = await plugin.database.fetch_one(
        """
        SELECT consumed_uses, granted_uses
        FROM player_food_effects
        WHERE effect_entry_id = 'chance-effect-1'
        """
    )
    assert leftover is not None
    assert leftover["consumed_uses"] == 1
    assert leftover["granted_uses"] == 1
    # 效果已用尽：再次使用被拒绝
    exhausted = await plugin.handle_reset_quota_chance(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(
                group_id="10001",
                user_id="20001",
                display_name="普通成员",
                message_id="chance-reset-2",
            )
        ),
    )
    assert exhausted[0] is False
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_window_quota_boost_overrides_limit_and_bypasses_restriction(
    tmp_path: Path,
) -> None:
    """提额窗口内额度按提升值计算并暂时无视违规限制，窗口切换后自动恢复。"""

    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={
            "catching": {"cooldown_seconds": 0},
            "features": {
                "selling_enabled": False,
                "cooking_enabled": True,
            },
        },
    )
    await _install_test_pig(plugin, tmp_path)
    scope_id = "qq:10001"
    player_id = f"{scope_id}:20001"

    # 建立群作用域与玩家（profile 不产生抓猪回执，不占窗口额度）
    await plugin.gameplay_service.profile(
        CommandIdentity(
            scope=ScopeKey("qq", "10001"),
            stream_id="stream-10001",
            user_id="20001",
            display_name="测试成员",
            message_id="boost-seed",
            group_name="抓猪测试群",
        )
    )

    # 给玩家加 catch-window-limit 限制（额度 1）模拟违规者
    now_text = "2026-08-07T06:00:00.000Z"
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            INSERT INTO player_restrictions(
                restriction_id, player_id, restriction_type, limit_value,
                starts_at, expires_at, reason, source, created_by,
                created_at, updated_at
            )
            VALUES (?, ?, 'catch-window-limit', 1, ?, NULL,
                    '测试违规限制', 'test', 'tester', ?, ?)
            """,
            (
                "boost-restriction-1",
                player_id,
                now_text,
                now_text,
                now_text,
            ),
        )

    service = plugin._quota_reset_service
    assert service is not None
    boosted = await service.apply_window_boost(
        data_dir=tmp_path,
        scope_ids=[scope_id],
        limit_value=15,
        created_by="test-operator",
        reason="测试提额",
        source="test",
    )
    assert boosted.scope_ids == (scope_id,)
    assert boosted.limit_value == 15
    assert len(boosted.audit_event_ids) == 1
    assert tuple((tmp_path / "backups").glob("*.sqlite3"))

    # 提额记录以 (scope_id, window_start) 主键落库
    async with plugin.database.transaction(immediate=False) as session:
        stored = await service.repository.active_window_boost(
            session,
            scope_id=scope_id,
            window_start=boosted.window_start,
        )
        assert stored is not None
        assert stored["limit_value"] == 15

    # 违规者（原限制 1）在提额窗口内可抓满 15 次
    for index in range(1, 16):
        ok, _, _ = await plugin.handle_catch(
            stream_id="stream-10001",
            **_command_kwargs(
                build_message(message_id=f"boost-catch-{index}")
            ),
        )
        assert ok is True, f"第 {index} 次抓猪应成功"

    # 第 16 次被拒绝（15/15）
    denied = await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="boost-catch-16")),
    )
    assert denied[0] is False
    assert "15/15" in denied[1]

    # 另一个额度窗口无提额记录：自动恢复每时段 5 次
    async with plugin.database.transaction(immediate=False) as session:
        next_window = await service.repository.active_window_boost(
            session,
            scope_id=scope_id,
            window_start="2099-01-01T04:00:00.000Z",
        )
        assert next_window is None
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_admin_panel_boost_one_shot_is_scoped_and_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    assert plugin.gameplay_service is not None
    await plugin.gameplay_service.profile(
        CommandIdentity(
            scope=ScopeKey("qq", "10001"),
            stream_id="stream-10001",
            user_id="20001",
            display_name="测试管理员",
            message_id="profile-before-boost",
            group_name="抓猪测试群",
        )
    )
    cleared: list[bool] = []
    monkeypatch.setattr(
        plugin,
        "_clear_administration_triggers",
        lambda: cleared.append(True),
    )
    config = plugin.get_plugin_config_data()
    config["quota_administration"] = {
        "group_id": "10001",
        "platform": "qq",
        "execute_current_window_reset": True,
        "boost_window_limit": 15,
    }
    plugin.set_plugin_config(config)
    await plugin.on_config_update(
        CONFIG_RELOAD_SCOPE_SELF,
        config,
        "admin-boost",
    )
    assert cleared == [True]
    assert plugin.database is not None
    async with plugin.database.transaction(immediate=False) as session:
        row = await session.fetch_one(
            """
            SELECT scope_id, actor_user_id, action, detail_json
            FROM audit_events
            WHERE action = 'catch-quota-window-boost'
            """
        )
        assert row is not None
        assert tuple(row[:3]) == (
            "qq:10001",
            "maibot-admin-panel",
            "catch-quota-window-boost",
        )
        assert '"limit_value":15' in str(row["detail_json"])
        boost_row = await session.fetch_one(
            """
            SELECT limit_value FROM quota_window_boosts
            WHERE scope_id = 'qq:10001'
            """
        )
        assert boost_row is not None
        assert boost_row["limit_value"] == 15
    assert tuple((tmp_path / "backups").glob("*.sqlite3"))
    await plugin.on_unload()



@pytest.mark.asyncio
async def test_batch_keep_commands_toggle_player_preference(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}},
    )
    await _install_test_pig(plugin, tmp_path)
    await plugin.handle_catch(
        stream_id="stream-10001",
        **_command_kwargs(build_message(message_id="batch-keep-seed")),
    )
    player_id = "qq:10001:20001"

    enabled, message, _ = await plugin.handle_enable_batch_keep(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(
                display_name="测试成员",
                message_id="batch-keep-on",
            )
        ),
    )
    assert enabled is True
    assert "已开启批量保留" in message
    assert "每个普通猪猪品种" in message
    assert "所有联动猪始终全部保护" in message
    row = await plugin.database.fetch_one(
        "SELECT batch_keep_highest FROM players WHERE player_id = ?",
        (player_id,),
    )
    assert row is not None and row["batch_keep_highest"] == 1

    disabled, message, _ = await plugin.handle_disable_batch_keep(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(
                display_name="测试成员",
                message_id="batch-keep-off",
            )
        ),
    )
    assert disabled is True
    assert "已关闭批量保留" in message
    assert "所有联动猪仍始终全部保护" in message
    row = await plugin.database.fetch_one(
        "SELECT batch_keep_highest FROM players WHERE player_id = ?",
        (player_id,),
    )
    assert row is not None and row["batch_keep_highest"] == 0
    await plugin.on_unload()



async def _install_baogian_template(plugin: Any, tmp_path: Path) -> None:
    """安装一只带备用表情包图的保千猪模板（公共素材，四个群通用）。"""

    source = tmp_path / "baogian-assets"
    source.mkdir()
    Image.new("RGBA", (64, 64), "#F58CAD").save(source / "baogian.png", format="PNG")
    Image.new("RGBA", (64, 64), "#66BFA3").save(
        source / "baogian-sticker.png", format="PNG"
    )
    manifest = source / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "catalog_id": "baogian-tests",
                "source_label": "pytest baogian catalog",
                "entries": [
                    {
                        "template_id": "pig-test-baogian",
                        "kind": "pig",
                        "display_name": "保千猪",
                        "rarity": 1,
                        "scope": "common",
                        "group_scope_id": None,
                        "description": "测试保千猪",
                        "image": "baogian.png",
                        "alternate_image": "baogian-sticker.png",
                        "fit": "contain",
                        "source": "pytest",
                        "license": "test-only",
                        "consent_status": "not-required",
                        "length_min_cm": 30,
                        "length_max_cm": 60,
                        "weight_min_kg": 20,
                        "weight_max_kg": 90,
                        "fat_profile": "balanced",
                        "recipe_tags": ["测试"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from pig_catcher.assets import AssetCatalogStorage
    from pig_catcher.services import AssetCatalogService

    await AssetCatalogService(
        plugin.database,
        AssetCatalogStorage(tmp_path / "data"),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    ).import_manifest(manifest)


async def _insert_baogian(
    plugin: Any,
    *,
    scope_id: str,
    player_id: str,
    short_code: str,
    instance_id: str,
    variant: str = "pig",
) -> None:
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            INSERT INTO pig_instances(
                pig_instance_id, short_code, scope_id, owner_player_id, template_id,
                template_version, rarity, display_name_snapshot,
                size_value, size_percentile, weight_value, weight_percentile,
                fat_ratio, official_value, ruleset_version, random_snapshot_json,
                display_variant, state, acquired_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'pig-test-baogian', 1, 1, '保千猪',
                    50.0, 0.5, 60.0, 0.5, 50.0, 100, 1, '{"test":true}',
                    ?, 'active', ?, ?)
            """,
            (
                instance_id, short_code, scope_id, player_id,
                variant, "2026-07-28T04:00:00.000Z", "2026-07-28T04:00:00.000Z",
            ),
        )


@pytest.mark.asyncio
async def test_toggle_baogian_works_across_groups_and_requires_code_when_multiple(
    tmp_path: Path,
) -> None:
    """修复官方群保千猪切换 bug；背包里有多只保千猪时必须指定编号。"""

    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}},
    )
    await _install_baogian_template(plugin, tmp_path)
    # 官方群作用域建玩家（QQ 官方群的保千猪此前无法切换）
    official_scope = "qq-official:OFFICIAL001"
    await plugin.gameplay_service.profile(
        CommandIdentity(
            scope=ScopeKey.parse(official_scope),
            stream_id="stream-official",
            user_id="U001",
            display_name="官方成员",
            message_id="baogian-profile",
            group_name="官方群",
        )
    )
    player_id = f"{official_scope}:U001"
    await _insert_baogian(
        plugin, scope_id=official_scope, player_id=player_id,
        short_code="BA0A0001", instance_id="baogian-1",
    )
    await _insert_baogian(
        plugin, scope_id=official_scope, player_id=player_id,
        short_code="BA0A0002", instance_id="baogian-2",
    )

    # 多只且未给编号：提示需要编号
    ok, message, _ = await plugin.handle_toggle_baogian(
        stream_id="stream-official",
        **_command_kwargs(
            build_message(
                platform="qq-official",
                group_id="OFFICIAL001",
                user_id="U001",
                display_name="官方成员",
                message_id="baogian-toggle-1",
            ),
            arguments="",
        ),
    )
    assert ok is False
    assert "请指定编号" in message
    assert "BA0A0001" in message and "BA0A0002" in message

    # 指定编号：只切换那一只
    ok, message, _ = await plugin.handle_toggle_baogian(
        stream_id="stream-official",
        **_command_kwargs(
            build_message(
                platform="qq-official",
                group_id="OFFICIAL001",
                user_id="U001",
                display_name="官方成员",
                message_id="baogian-toggle-2",
            ),
            arguments="BA0A0002",
        ),
    )
    assert ok is True
    assert "BA0A0002" in message
    assert "表情包" in message
    row = await plugin.database.fetch_one(
        "SELECT display_variant FROM pig_instances WHERE pig_instance_id = 'baogian-2'"
    )
    assert row is not None and row["display_variant"] == "sticker"
    row = await plugin.database.fetch_one(
        "SELECT display_variant FROM pig_instances WHERE pig_instance_id = 'baogian-1'"
    )
    assert row is not None and row["display_variant"] == "pig"

    # 不存在的编号：明确提示
    ok, message, _ = await plugin.handle_toggle_baogian(
        stream_id="stream-official",
        **_command_kwargs(
            build_message(
                platform="qq-official",
                group_id="OFFICIAL001",
                user_id="U001",
                display_name="官方成员",
                message_id="baogian-toggle-3",
            ),
            arguments="BA0A9999",
        ),
    )
    assert ok is False
    assert "没有编号 BA0A9999" in message
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_toggle_baogian_single_instance_needs_no_code(
    tmp_path: Path,
) -> None:
    """背包里只有一只保千猪时，不带编号直接切换。"""

    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}},
    )
    await _install_baogian_template(plugin, tmp_path)
    scope_id = "qq:10001"
    await plugin.gameplay_service.profile(
        CommandIdentity(
            scope=ScopeKey("qq", "10001"),
            stream_id="stream-10001",
            user_id="20001",
            display_name="测试成员",
            message_id="baogian-single-profile",
            group_name="抓猪测试群",
        )
    )
    await _insert_baogian(
        plugin, scope_id=scope_id, player_id=f"{scope_id}:20001",
        short_code="BA0B0001", instance_id="baogian-single",
    )
    ok, message, _ = await plugin.handle_toggle_baogian(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(
                group_id="10001",
                user_id="20001",
                display_name="测试成员",
                message_id="baogian-single-toggle",
            ),
            arguments="",
        ),
    )
    assert ok is True
    assert "BA0B0001" in message
    row = await plugin.database.fetch_one(
        "SELECT display_variant FROM pig_instances WHERE pig_instance_id = 'baogian-single'"
    )
    assert row is not None and row["display_variant"] == "sticker"
    await plugin.on_unload()
