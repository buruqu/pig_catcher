"""MaiBot 生命周期、组件边界和命令级帮助验证。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF

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


def test_plugin_registers_only_the_help_command() -> None:
    plugin = create_plugin()
    components = plugin.get_components()
    assert len(components) == 1
    assert components[0]["type"] == "COMMAND"
    assert components[0]["name"] == "pig_catcher_help"
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
    assert "/抓猪帮助 [抓猪|背包|做菜|商城|交易|排行]" in text
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
