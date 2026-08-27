"""四个派遣入口的真实插件分发、图片优先、失败兜底与旧界面占用标记。"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.services.gameplay import GameplayService

from .helpers import build_message, create_plugin, create_test_plugin
from .test_dispatch import seed_pigs
from .test_gameplay import MutableClock
from .test_plugin import _command_kwargs, _install_test_pig


def test_official_qq_mentions_route_to_dispatch_without_capturing_other_commands():
    entries = {item["name"]: item for item in create_plugin().get_components() if item["type"] == "COMMAND"}
    names = {
        "pig_catcher_dispatch": "猪猪派遣",
        "pig_catcher_dispatch_bag": "派遣背包",
        "pig_catcher_dispatch_journal": "派遣游记",
        "pig_catcher_dispatch_encounter": "派遣奇遇",
    }
    for component, command in names.items():
        pattern = entries[component]["metadata"]["command_pattern"]
        for prefix in ("", "<@!BOT_ABC> ", "[CQ:at,qq=BOT_ABC] ", "@小马初号机 "):
            assert re.fullmatch(pattern, f"{prefix}/{command}")
        for other in ("/抓猪", "/是", "/否", "/购买", "/出招", "/领域展开 伏魔御厨子"):
            assert re.fullmatch(pattern, other) is None


@pytest.mark.asyncio
async def test_all_dispatch_queries_render_and_help_is_copyable(tmp_path: Path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        cases = (
            (plugin.handle_dispatch, ""),
            (plugin.handle_dispatch, "路线"),
            (plugin.handle_dispatch_bag, ""),
            (plugin.handle_dispatch_bag, "配方"),
            (plugin.handle_dispatch_journal, ""),
            (plugin.handle_dispatch_journal, "纪念品"),
            (plugin.handle_dispatch_encounters, ""),
        )
        for index, (handler, args) in enumerate(cases):
            result = await handler(
                stream_id="stream-10001", **_command_kwargs(build_message(message_id=f"query-{index}"), arguments=args)
            )
            assert result[0], result
        assert len(context.send.images) == 7 and not context.send.texts
        assert all("data-pig-catcher-root" in html and "猪猪远行社" in html for html, _ in context.render.calls)
        result = await plugin.handle_dispatch(
            stream_id="stream-10001", **_command_kwargs(build_message(), arguments="帮助")
        )
        assert result[0] and "/猪猪派遣 确认" in context.send.texts[-1][1]
        assert len(context.send.images) == 7
        bad = await plugin.handle_dispatch(
            stream_id="stream-10001", **_command_kwargs(build_message(), arguments="出发 1 任意 7")
        )
        assert not bad[0] and len(context.send.images) == 8
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
async def test_dispatch_mutations_render_and_failure_falls_back_without_rededucting(tmp_path: Path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_test_pig(plugin, tmp_path)
        identity = CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", "测试成员", "seed", "测试群")
        await seed_pigs(plugin.database, identity, template_id="command-pig", count=3)
        clock = MutableClock(datetime(2026, 8, 27, tzinfo=UTC))
        plugin._dispatch_service.clock = clock
        for index, args in enumerate(("编队 1 命令测试猪、命令测试猪、命令测试猪", "确认", "出发 1 青草近郊 4小时")):
            result = await plugin.handle_dispatch(
                stream_id="stream-10001",
                **_command_kwargs(build_message(message_id=f"prepare-{index}"), arguments=args),
            )
            assert result[0], result
        assert len(context.send.images) == 3
        assert "data:image/" in context.render.calls[0][0]
        context.render.error = RuntimeError("offline renderer intentionally unavailable")
        message = build_message(message_id="once-depart")
        first = await plugin.handle_dispatch(stream_id="stream-10001", **_command_kwargs(message, arguments="确认"))
        assert first[0] and "出发啦" in context.send.texts[-1][1]
        again = await plugin.handle_dispatch(stream_id="stream-10001", **_command_kwargs(message, arguments="确认"))
        assert again[0] and len(context.send.texts) == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM dispatch_trips"))[0] == 1
        context.render.error = None
        clock.value += timedelta(hours=4)
        result = await plugin.handle_dispatch(
            stream_id="stream-10001", **_command_kwargs(build_message(message_id="returned"), arguments="返程")
        )
        assert result[0] and "欢迎回家" in result[1]
        assert len(context.send.images) == 4
        assert "旅行补给" in context.render.calls[-1][0]
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
async def test_disabling_dispatch_still_allows_due_pigs_to_return_via_inventory(tmp_path: Path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_test_pig(plugin, tmp_path)
        identity = CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", "测试成员", "seed", "测试群")
        await seed_pigs(plugin.database, identity, template_id="command-pig", count=1)
        clock = MutableClock(datetime(2026, 8, 27, tzinfo=UTC))
        plugin._dispatch_service.clock = clock
        plugin._gameplay_service = GameplayService(plugin.database, CatchingSection(), clock=clock)
        for index, args in enumerate(("编队 1 命令测试猪", "确认", "出发 1 青草近郊 4小时", "确认")):
            assert (
                await plugin.handle_dispatch(
                    stream_id="stream-10001", **_command_kwargs(build_message(message_id=str(index)), arguments=args)
                )
            )[0]
        await plugin.handle_inventory(
            stream_id="stream-10001", **_command_kwargs(build_message(message_id="busy-inventory"), arguments="")
        )
        assert "派遣中" in context.render.calls[-1][0]
        config = plugin.get_plugin_config_data()
        config["features"]["dispatch_enabled"] = False
        plugin.set_plugin_config(config)
        result = await plugin.handle_dispatch(
            stream_id="stream-10001", **_command_kwargs(build_message(), arguments="")
        )
        assert not result[0]
        clock.value += timedelta(hours=4)
        await plugin.handle_inventory(
            stream_id="stream-10001", **_command_kwargs(build_message(message_id="free-inventory"), arguments="")
        )
        assert "派遣中" not in context.render.calls[-1][0]
        assert (await plugin.database.fetch_one("SELECT status FROM dispatch_trips"))[0] == "completed"
        assert await plugin.database.fetch_all("SELECT * FROM asset_occupancies") == []
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
async def test_receipt_public_projection_escapes_user_html_and_never_contains_random_seed(tmp_path: Path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        result = await plugin.handle_dispatch(
            stream_id="stream-10001",
            **_command_kwargs(build_message(display_name='<script>alert("xx")</script>'), arguments=""),
        )
        assert result[0]
        html = context.render.calls[-1][0]
        assert "<script>" not in html and "&lt;script&gt;" in html
        await plugin.handle_dispatch(
            stream_id="stream-10001",
            **_command_kwargs(build_message(display_name="B" * 32, user_id="B" * 32), arguments=""),
        )
        assert "B" * 32 not in context.render.calls[-1][0]
    finally:
        await plugin.on_unload()
