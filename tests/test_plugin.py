"""MaiBot 生命周期、组件边界和命令级帮助验证。"""

from __future__ import annotations

import asyncio
import base64
import json
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


def test_plugin_registers_only_explicit_third_round_commands() -> None:
    plugin = create_plugin()
    components = plugin.get_components()
    assert len(components) == 9
    assert {component["type"] for component in components} == {"COMMAND"}
    assert {component["name"] for component in components} == {
        "pig_catcher_help",
        "pig_catcher_catch",
        "pig_catcher_profile",
        "pig_catcher_pig_detail",
        "pig_catcher_inventory",
        "pig_catcher_catalog",
        "pig_catcher_records",
        "pig_catcher_use_item",
        "pig_catcher_cancel_item",
    }
    serialized = str(components)
    assert "EVENT_HANDLER" not in serialized
    assert "LLM" not in serialized
    assert "TOOL" not in serialized


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
    manifest = source / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "catalog_id": "command-tests",
                "source_label": "pytest command catalog",
                "entries": [
                    {
                        "template_id": "command-pig",
                        "kind": "pig",
                        "display_name": "命令测试猪",
                        "rarity": 1,
                        "scope": "common",
                        "group_scope_id": None,
                        "description": "用于命令级完整流程验收。",
                        "image": image_name,
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
        config_updates={"catching": {"cooldown_seconds": 0}},
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
        **_command_kwargs(query_message, arguments="1"),
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
async def test_catch_render_failure_falls_back_once_without_rollback(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"catching": {"cooldown_seconds": 0}},
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
        config_updates={"catching": {"cooldown_seconds": 0}},
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
        config_updates={"catching": {"cooldown_seconds": 0}},
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
        config_updates={"catching": {"cooldown_seconds": 0}},
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
