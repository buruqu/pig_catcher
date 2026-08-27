"""真实SDK入口与图片优先的巡演验收（全部使用假发送器与独立数据库）。"""

from __future__ import annotations

import json
import re
from uuid import uuid4

import pytest
from PIL import Image

from pig_catcher.commands.context import extract_mention_target
from pig_catcher.commands.tour import parse_tour_request
from pig_catcher.domain.errors import MentionTargetError
from pig_catcher.domain.models import CommandIdentity, ScopeKey

from .helpers import build_message, create_plugin, create_test_plugin
from .test_dispatch import NOW, seed_pigs
from .test_gameplay import MutableClock, _pig_entry
from .test_plugin import _command_kwargs
from .test_tour import character


def test_tour_aliases_do_not_capture_existing_or_future_battle_commands():
    entries = {item["name"]: item for item in create_plugin().get_components() if item["type"] == "COMMAND"}
    groups = {
        "pig_catcher_band": ("我的猪猪乐队", "组建乐队", "乐队编队", "乐队练习"),
        "pig_catcher_tour": ("猪猪巡演", "巡演继续", "巡演一键"),
        "pig_catcher_tour_journal": ("巡演游记",),
        "pig_catcher_joint_tour": ("巡演联演",),
    }
    for component, aliases in groups.items():
        pattern = entries[component]["metadata"]["command_pattern"]
        for command in aliases:
            for prefix in ("", "<@!BOT_ABC> ", "[CQ:at,qq=BOT_ABC] ", "@机器人 "):
                assert re.fullmatch(pattern, f"{prefix}/{command}")
        for other in ("/抓猪", "/是", "/购买", "/猪猪派遣", "/出招", "/比划比划", "/领域展开 伏魔御厨子"):
            assert re.fullmatch(pattern, other) is None


def test_joint_strict_at_target_excludes_leading_bot_on_both_adapters():
    for data in (
        {"target_user_id": "MEMBER", "target_user_nickname": "群友"},
        {"qq": "MEMBER", "target_user_nickname": "群友"},
    ):
        kwargs = {
            "message": {
                "raw_message": [
                    {"type": "at", "data": {"target_user_id": "BOT", "target_user_nickname": "机器人"}},
                    {"type": "at", "data": data},
                ]
            }
        }
        for arguments in ("@群友", "<@!MEMBER>", "[CQ:at,qq=MEMBER]"):
            target = extract_mention_target(kwargs, arguments=arguments)
            assert target.user_id == "MEMBER"
            assert parse_tour_request(
                arguments, section="joint", target_user_id=target.user_id, target_name=target.display_name
            ).args == {"target_user_id": "MEMBER"}
        with pytest.raises(MentionTargetError):
            extract_mention_target(kwargs, arguments="@不在结构化消息里")


async def install_band(plugin, tmp_path, identity):
    source = tmp_path / "tour-inputs"
    source.mkdir(exist_ok=True)
    entries = []
    for c in ("kasumi", "tomoe", "layer"):
        char = character(c)
        entry = _pig_entry(char.template_id, rarity=5, display_name=char.name)
        entries.append(entry)
        Image.new("RGB", (256, 256), "#f9c9de").save(source / entry["image"])
    manifest = source / "assets.json"
    manifest.write_text(
        json.dumps(
            {"manifest_version": 2, "catalog_id": "tour-tests", "source_label": "offline tests", "entries": entries},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    await plugin._asset_service.import_manifest(manifest)
    for c in ("kasumi", "tomoe", "layer"):
        await seed_pigs(plugin.database, identity, template_id=character(c).template_id, count=1)


async def invoke(plugin, handler, arguments="", *, entry="", user_id="20001", name="巡演测试员", message_id=None):
    return await getattr(plugin, handler)(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(user_id=user_id, display_name=name, message_id=message_id or uuid4().hex),
            arguments=arguments,
            entry=entry,
        ),
    )


async def test_tour_queries_render_and_copyable_help(tmp_path):
    plugin, ctx = await create_test_plugin(tmp_path)
    try:
        cases = (
            ("handle_band", ""),
            ("handle_band", "角色 8"),
            ("handle_band", "成员"),
            ("handle_tour", "场地"),
            ("handle_tour", "主题"),
            ("handle_tour", "曲库 2"),
            ("handle_tour", "合奏"),
            ("handle_tour_journal", ""),
            ("handle_tour_journal", "收藏 1"),
            ("handle_joint_tour", ""),
        )
        for handler, args in cases:
            assert (await invoke(plugin, handler, args))[0]
        assert len(ctx.send.images) == len(cases) and not ctx.send.texts
        assert all("tour-body" in html for html, _ in ctx.render.calls)
        assert (await invoke(plugin, "handle_tour", "帮助"))[0]
        assert "/猪猪巡演 确认" in ctx.send.texts[-1][1]
        assert len(ctx.send.images) == len(cases)
        assert not (await invoke(plugin, "handle_tour", "不存在的子命令"))[0]
        assert "巡演提示" in ctx.render.calls[-1][0]
    finally:
        await plugin.on_unload()


async def test_mutations_render_receipt_fallback_does_not_replay_rewards(tmp_path):
    plugin, ctx = await create_test_plugin(tmp_path)
    try:
        identity = CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", "巡演测试员", "seed", "测试群")
        await install_band(plugin, tmp_path, identity)
        plugin._tour_service.clock = MutableClock(NOW)
        assert (await invoke(plugin, "handle_band", "新团", entry="组建乐队"))[0]
        assert (await invoke(plugin, "handle_band", "1 星星猪、巴巴猪、LAYER猪", entry="乐队编队"))[0]
        assert "data:image/" in ctx.render.calls[-1][0]
        assert (await invoke(plugin, "handle_tour", "确认"))[0]
        await invoke(plugin, "handle_inventory")
        assert "乐队保护" in ctx.render.calls[-1][0]
        assert (await invoke(plugin, "handle_tour", entry="巡演一键"))[0]
        ctx.render.error = RuntimeError("offline render unavailable")
        result = await invoke(plugin, "handle_tour", "确认", message_id="finish-once")
        assert result[0] and "三站落幕" in ctx.send.texts[-1][1]
        before = len(ctx.send.texts)
        await invoke(plugin, "handle_tour", "确认", message_id="finish-once")
        assert len(ctx.send.texts) == before
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM tour_runs WHERE status='completed'"))[0] == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM tour_stages"))[0] == 3
        ctx.render.error = None
        assert (await invoke(plugin, "handle_tour_journal", "收藏 1"))[0]
        assert "tour-body" in ctx.render.calls[-1][0]
        config = plugin.get_plugin_config_data()
        config["features"]["tour_enabled"] = False
        plugin.set_plugin_config(config)
        assert not (await invoke(plugin, "handle_tour"))[0]
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM tour_protections WHERE protected=1"))[0] == 3
    finally:
        await plugin.on_unload()


async def test_tour_html_safe_and_no_raw_openid(tmp_path):
    plugin, ctx = await create_test_plugin(tmp_path)
    try:
        assert (await invoke(plugin, "handle_band", name='<script>alert("x")</script>'))[0]
        assert "<script>" not in ctx.render.calls[-1][0] and "&lt;script&gt;" in ctx.render.calls[-1][0]
        assert (await invoke(plugin, "handle_band", name="B" * 32, user_id="B" * 32))[0]
        assert "B" * 32 not in ctx.render.calls[-1][0]
    finally:
        await plugin.on_unload()
