"""真实SDK显式命令、两种QQ提及格式、图卡优先与发送重试。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from uuid import uuid4

import pytest
from PIL import Image

from pig_catcher.commands.battle import parse_battle_request
from pig_catcher.commands.context import extract_mention_target
from pig_catcher.domain.battle import loads
from pig_catcher.domain.battle_catalog import BattleError
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.special_content import GOJO_PIG_TEMPLATE_ID, SUKUNA_PIG_TEMPLATE_ID

from .helpers import build_message, create_plugin, create_test_plugin
from .test_dispatch import NOW, seed_pigs
from .test_gameplay import MutableClock, _pig_entry
from .test_plugin import _command_kwargs

COMMANDS = {
    "pig_catcher_battle_pig": "战斗猪",
    "pig_catcher_battle_challenge": "比划比划",
    "pig_catcher_battle_count": "出招数",
    "pig_catcher_battle_move": "出招",
    "pig_catcher_battle_ready": "会赢的",
    "pig_catcher_battle_status": "对战状态",
    "pig_catcher_battle_history": "对战记录",
    "pig_catcher_battle_loot": "战利品抓猪",
}


def test_explicit_patterns_do_not_conflict_with_techniques_or_other_commands():
    components = {c["name"]: c for c in create_plugin().get_components() if c["type"] == "COMMAND"}
    for name, command in COMMANDS.items():
        pattern = components[name]["metadata"]["command_pattern"]
        for prefix in ("", "<@!BOT_ABC> ", "[CQ:at,qq=BOT_ABC] ", "@机器人 "):
            assert re.fullmatch(pattern, prefix + "/" + command)
        for other in ("/抓猪", "/是", "/购买", "/巡演联演", "/领域展开 伏魔御厨子", "/术式顺转 苍", "/虚式 茈"):
            assert re.fullmatch(pattern, other) is None
        matches = [
            c["name"] for c in components.values() if re.fullmatch(c["metadata"]["command_pattern"], "/" + command)
        ]
        assert matches == [name]


@pytest.mark.parametrize("field", ["qq", "target_user_id"])
def test_challenge_at_filters_leading_bot_for_both_adapters(field):
    kwargs = {
        "message": {
            "raw_message": [
                {"type": "at", "data": {field: "BOT", "target_user_nickname": "机器人"}},
                {"type": "at", "data": {field: "MEMBER", "target_user_nickname": "群友"}},
            ]
        }
    }
    for arguments in ("@群友", "<@!MEMBER>", "[CQ:at,qq=MEMBER]"):
        target = extract_mention_target(kwargs, arguments=arguments)
        assert parse_battle_request(
            arguments, section="challenge", target_user_id=target.user_id, target_name=target.display_name
        ).args == {"target_user_id": "MEMBER"}


@pytest.mark.parametrize(
    "text,section",
    [
        ("数量", "count"),
        ("/领域展开", "move"),
        ("提前", "ready"),
        ("不存在的猪", "challenge"),
        ("强化\n所有人", "profile"),
        ("轮盘 普通猪", "profile"),
        ("制作 练习护腕 0", "profile"),
        ("制作 练习护腕 100", "profile"),
        ("-1", "history"),
        ("B000000000001 0", "history"),
    ],
)
def test_malformed_commands_rejected(text, section):
    with pytest.raises(BattleError):
        parse_battle_request(text, section=section)


async def install_fighters(plugin, root, a, b):
    source = root / "battle-inputs"
    source.mkdir()
    entries = [_pig_entry(f"star-{i}", rarity=i, group_id="10001" if i == 6 else None) for i in range(1, 7)]
    entries += [
        _pig_entry(SUKUNA_PIG_TEMPLATE_ID, rarity=5, display_name="宿傩猪"),
        _pig_entry(GOJO_PIG_TEMPLATE_ID, rarity=5, display_name="五条猪"),
    ]
    for entry in entries:
        Image.new("RGB", (256, 256), "#f9c9de").save(source / entry["image"])
    manifest = source / "assets.json"
    manifest.write_text(
        json.dumps(
            {"manifest_version": 2, "catalog_id": "battle-tests", "source_label": "offline", "entries": entries},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    await plugin._asset_service.import_manifest(manifest)
    for actor, template in ((a, SUKUNA_PIG_TEMPLATE_ID), (b, GOJO_PIG_TEMPLATE_ID)):
        await seed_pigs(plugin.database, actor, template_id=template, count=1)


async def invoke(plugin, handler, text="", *, actor=None, mid=None, target=None):
    actor = actor or CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", "对战测试员")
    message = build_message(
        platform=actor.scope.platform,
        group_id=actor.scope.group_id,
        stream_id=actor.stream_id,
        user_id=actor.user_id,
        display_name=actor.display_name,
        message_id=mid or uuid4().hex,
    )
    if target:
        message["raw_message"] = [
            {"type": "at", "data": {"target_user_id": "BOT", "target_user_nickname": "机器人"}},
            {"type": "at", "data": {"target_user_id": target.user_id, "target_user_nickname": target.display_name}},
        ]
    return await getattr(plugin, handler)(stream_id=actor.stream_id, **_command_kwargs(message, arguments=text))


async def test_queries_help_and_errors_are_images_except_copyable_help(tmp_path):
    plugin, ctx = await create_test_plugin(tmp_path)
    try:
        for handler, text in (
            ("handle_battle_pig", ""),
            ("handle_battle_pig", "轮盘 五条猪"),
            ("handle_battle_pig", "器具"),
            ("handle_battle_status", ""),
            ("handle_battle_history", ""),
        ):
            assert (await invoke(plugin, handler, text))[0]
        assert len(ctx.send.images) == 5 and not ctx.send.texts
        assert all(
            'class="game-sheet feature-battle"' in html
            and 'class="feature-body"' in html
            and 'class="game-header feature-header feature-header--battle"' in html
            and "data-pig-catcher-root" in html
            and 'data-render-ready="true"' in html
            and "<strong>对战测试员</strong>" in html
            for html, _ in ctx.render.calls
        )
        assert (await invoke(plugin, "handle_battle_pig", "帮助"))[0]
        assert "/战利品抓猪" in ctx.send.texts[-1][1]
        assert "普通 /抓猪 会自动结算战利品" in ctx.send.texts[-1][1]
        assert not (await invoke(plugin, "handle_battle_count"))[0]
        assert "对战提示" in ctx.render.calls[-1][0]
    finally:
        await plugin.on_unload()


async def test_sdk_full_match_rendered_fallback_and_disabled_safety(tmp_path):
    plugin, ctx = await create_test_plugin(tmp_path, config_updates={"catching": {"cooldown_seconds": 0}})
    try:
        a = CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", "小小挑战者")
        b = replace(a, user_id="20002", display_name="守擂的群友")
        await install_fighters(plugin, tmp_path, a, b)
        plugin._battle_service.clock = MutableClock(NOW)
        plugin._battle_service.seed_factory = lambda: "sdk-battle"
        for actor, name in ((a, "宿傩猪"), (b, "五条猪")):
            assert (await invoke(plugin, "handle_battle_pig", "设置 " + name, actor=actor))[0]
            assert (await invoke(plugin, "handle_battle_pig", "确认", actor=actor))[0]
        assert (await invoke(plugin, "handle_inventory", actor=a))[0]
        assert "战斗保护" in ctx.render.calls[-1][0]
        assert (await invoke(plugin, "handle_battle_challenge", "@" + b.display_name, actor=a, target=b))[0]
        assert "data:image/" in ctx.render.calls[-1][0]
        ctx.render.error = RuntimeError("simulated image unavailable")
        assert (await invoke(plugin, "handle_battle_challenge", "接受", actor=b, mid="accepted"))[0]
        assert "双方入场" in ctx.send.texts[-1][1]
        before = len(ctx.send.texts)
        await invoke(plugin, "handle_battle_challenge", "接受", actor=b, mid="accepted")
        assert len(ctx.send.texts) == before
        ctx.render.error = None
        for _ in range(200):
            match = await plugin.database.fetch_one("SELECT * FROM battle_matches")
            state = loads(match["state_json"])
            if state["status"] == "completed":
                break
            for side, actor in enumerate((a, b)):
                current = loads((await plugin.database.fetch_one("SELECT state_json FROM battle_matches"))[0])
                if current["status"] != "active" or current["round"] != state["round"]:
                    break
                turn = current["sides"][side]["turn"]
                handler = (
                    "handle_battle_count"
                    if turn["raw"] is None
                    else "handle_battle_move"
                    if not turn["done"]
                    else "handle_battle_ready"
                    if all(item["turn"]["done"] for item in current["sides"])
                    and not turn.get("ready", False)
                    else ""
                )
                if handler:
                    assert (await invoke(plugin, handler, actor=actor))[0]
        else:
            pytest.fail("SDK match did not naturally finish")
        loser = a if state["winner"] == 1 else b
        assert (await invoke(plugin, "handle_catch", actor=loser, mid="loot-auto"))[0]
        assert "战利品抓猪" in ctx.render.calls[-1][0] and "本次最终概率" in ctx.render.calls[-1][0]
        assert (await plugin.database.fetch_one("SELECT used FROM battle_loot"))[0] == 1
        # The old explicit entry remains available and consumes the same queue.
        assert (await invoke(plugin, "handle_battle_loot", actor=loser, mid="loot-manual"))[0]
        assert (await plugin.database.fetch_one("SELECT used FROM battle_loot"))[0] == 2
        assert (await invoke(plugin, "handle_battle_history", match["battle_id"] + " 1 1"))[0]
        assert "逐招记录" in ctx.render.calls[-1][0]
        config = plugin.get_plugin_config_data()
        config["features"]["battle_enabled"] = False
        plugin.set_plugin_config(config)
        assert not (await invoke(plugin, "handle_battle_loot", actor=loser))[0]
        assert (await plugin.database.fetch_one("SELECT used FROM battle_loot"))[0] == 2
    finally:
        await plugin.on_unload()


async def test_configuration_blacklist_applies_to_invitee_and_html_is_escaped(tmp_path):
    plugin, ctx = await create_test_plugin(tmp_path)
    try:
        a = CommandIdentity(ScopeKey("qq", "10001"), "stream-10001", "20001", '<script>alert("x")</script>')
        b = replace(a, user_id="A" * 32, display_name="A" * 32)
        assert (await invoke(plugin, "handle_battle_pig", actor=a))[0]
        assert "<script>" not in ctx.render.calls[-1][0] and "&lt;script&gt;" in ctx.render.calls[-1][0]
        assert (await invoke(plugin, "handle_battle_pig", actor=b))[0]
        assert "A" * 32 not in ctx.render.calls[-1][0]
        config = plugin.get_plugin_config_data()
        config["access"]["user_blacklist"] = [b.user_id]
        plugin.set_plugin_config(config)
        result = await invoke(plugin, "handle_battle_challenge", "<@!" + b.user_id + ">", actor=a, target=b)
        assert not result[0] and "黑白名单" in result[1]
        assert not await plugin.database.fetch_all("SELECT * FROM battle_matches")
    finally:
        await plugin.on_unload()
