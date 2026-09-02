"""Help examples follow real registered regexes before reaching parsers or handlers.

All data is synthetic and lives in pytest's disposable directory.  These tests
never connect to MaiBot, a QQ gateway, or a production data directory.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pig_catcher.commands.battle import parse_battle_request
from pig_catcher.commands.dispatch import parse_dispatch_request
from pig_catcher.commands.help import format_help
from pig_catcher.commands.item_bag import parse_item_bag_request
from pig_catcher.commands.parsers import (
    FOOD_INVENTORY_SORTS,
    INVENTORY_SORTS,
    parse_batch_cook_query,
    parse_batch_sale_query,
    parse_catalog_query,
)
from pig_catcher.commands.tour import parse_tour_request
from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.domain.models import CommandIdentity, ScopeKey

from .helpers import build_message, create_plugin, create_test_plugin
from .test_plugin import _insert_baogian, _install_baogian_template


@pytest.fixture(scope="module")
def registered_patterns() -> dict[str, re.Pattern[str]]:
    return {
        component["name"]: re.compile(component["metadata"]["command_pattern"])
        for component in create_plugin().get_components()
        if component.get("metadata", {}).get("command_pattern")
    }


def _registered_match(patterns: dict[str, re.Pattern[str]], command: str, component: str) -> re.Match[str]:
    matches = [(name, match) for name, pattern in patterns.items() if (match := pattern.fullmatch(command)) is not None]
    assert [name for name, _ in matches] == [component], command
    return matches[0][1]


# The command, its registered entry, and the parsed action are the contract.
# No help module implementation detail is needed to run these examples.
_PARSER_EXAMPLES = (
    ("pig_catcher_dispatch", "/猪猪派遣 自动 回声矿洞", "dispatch", "dispatch", "auto"),
    ("pig_catcher_dispatch", "/猪猪派遣 编队 1 笨猪、草莓猪", "dispatch", "dispatch", "team"),
    ("pig_catcher_dispatch", "/猪猪派遣 出发 1 青草近郊 4小时", "dispatch", "dispatch", "start"),
    (
        "pig_catcher_dispatch",
        "/猪猪派遣 出发 1 回声矿洞 8小时 区域地图 训练矿石",
        "dispatch",
        "dispatch",
        "start",
    ),
    (
        "pig_catcher_dispatch",
        "/猪猪派遣 出发 1 回声矿洞 12小时 纪念相机",
        "dispatch",
        "dispatch",
        "start",
    ),
    (
        "pig_catcher_dispatch",
        "/猪猪派遣 出发 1 回声矿洞 24小时 奇遇罗盘 1",
        "dispatch",
        "dispatch",
        "start",
    ),
    (
        "pig_catcher_dispatch",
        "/猪猪派遣 出发 1 青草近郊 8小时 整理箱 机关零件 20 训练矿石",
        "dispatch",
        "dispatch",
        "start",
    ),
    ("pig_catcher_dispatch", "/猪猪派遣 确认", "dispatch", "dispatch", "confirm"),
    ("pig_catcher_dispatch", "/猪猪派遣 返程", "dispatch", "dispatch", "returns"),
    ("pig_catcher_dispatch_bag", "/派遣背包 配方", "dispatch", "bag", "recipes"),
    ("pig_catcher_dispatch_bag", "/派遣背包 制作 区域地图 2", "dispatch", "bag", "craft"),
    (
        "pig_catcher_dispatch_bag",
        "/派遣背包 转换 训练矿石 灵巧纤维 2",
        "dispatch",
        "bag",
        "convert",
    ),
    ("pig_catcher_dispatch_journal", "/派遣游记 纪念品 1", "dispatch", "journal", "souvenirs"),
    ("pig_catcher_dispatch_encounter", "/派遣奇遇 D123456ABCD-1 2", "dispatch", "encounters", "choose"),
    ("pig_catcher_band", "/组建乐队 我的小猪团", "tour", "band", "create"),
    ("pig_catcher_band", "/乐队编队 1 偶像猪、天才猪、武士道猪", "tour", "band", "roster"),
    ("pig_catcher_band", "/我的猪猪乐队 切换 1", "tour", "band", "switch"),
    ("pig_catcher_tour", "/猪猪巡演 自动 Pastel＊Palettes", "tour", "tour", "auto_tour"),
    ("pig_catcher_tour", "/猪猪巡演 自动配队 Pastel＊Palettes", "tour", "tour", "auto_roster"),
    ("pig_catcher_tour", "/猪猪巡演 主题 星星落进练习室", "tour", "tour", "theme"),
    ("pig_catcher_tour", "/猪猪巡演 路线 街头舞台、街头舞台、街头舞台", "tour", "tour", "route"),
    (
        "pig_catcher_tour",
        "/猪猪巡演 编排 1 星屑起跑线、练习室的下午、把星光带回家",
        "tour",
        "tour",
        "setlist",
    ),
    ("pig_catcher_tour", "/猪猪巡演 高光 1 1、2", "tour", "tour", "highlights"),
    ("pig_catcher_tour", "/猪猪巡演 合奏 1 自由合奏", "tour", "tour", "ensemble"),
    ("pig_catcher_tour", "/猪猪巡演 器具 1 备用线缆", "tour", "tour", "tool"),
    ("pig_catcher_tour", "/猪猪巡演 排练", "tour", "tour", "preview"),
    ("pig_catcher_tour", "/猪猪巡演 出发", "tour", "tour", "start"),
    ("pig_catcher_tour", "/巡演继续", "tour", "tour", "continue"),
    ("pig_catcher_tour", "/巡演一键", "tour", "tour", "all"),
    ("pig_catcher_joint_tour", "/巡演联演 @测试成员", "tour", "joint", "joint_invite"),
    ("pig_catcher_tour_journal", "/巡演游记 收藏 1", "tour", "journal", "collections"),
    ("pig_catcher_battle_pig", "/战斗猪 设置 五条猪", "battle", "profile", "assign_preview"),
    ("pig_catcher_battle_pig", "/战斗猪 确认", "battle", "profile", "confirm"),
    ("pig_catcher_battle_pig", "/战斗猪 强化", "battle", "profile", "upgrade_preview"),
    ("pig_catcher_battle_pig", "/战斗猪 轮盘 宿傩猪", "battle", "profile", "wheels"),
    ("pig_catcher_battle_pig", "/战斗猪 制作 练习护腕 2", "battle", "profile", "craft"),
    ("pig_catcher_battle_pig", "/战斗猪 器具 练习护腕", "battle", "profile", "equip"),
    ("pig_catcher_battle_challenge", "/比划比划 @测试成员", "battle", "challenge", "invite"),
    ("pig_catcher_battle_challenge", "/比划比划 确认认输", "battle", "challenge", "surrender_confirm"),
    ("pig_catcher_battle_history", "/对战记录 B123456ABCDEF 2 1", "battle", "history", "detail"),
    (
        "pig_catcher_reward_coupon",
        "/使用奖励券 编号修改券 猪猪 笨猪#A1B2C3D4 CCCC1111",
        "bag",
        "coupon",
        "rename",
    ),
    ("pig_catcher_reward_coupon", "/使用奖励券 猪猪自选券 五条猪", "bag", "coupon", "choose-pig"),
    ("pig_catcher_reward_coupon", "/使用奖励券 确认", "bag", "coupon", "confirm"),
)


@pytest.mark.parametrize("component,command,family,section,action", _PARSER_EXAMPLES)
def test_registered_help_examples_reach_the_documented_parser_action(
    registered_patterns, component, command, family, section, action
):
    match = _registered_match(registered_patterns, command, component)
    groups = match.groupdict()
    arguments = groups.get("arguments") or ""
    kwargs: dict[str, Any] = {"section": section}
    if family == "dispatch":
        result = parse_dispatch_request(arguments, **kwargs)
    elif family == "tour":
        result = parse_tour_request(
            arguments,
            entry=groups.get("entry") or "",
            target_user_id="SYNTHETIC-MEMBER",
            target_name="测试成员",
            **kwargs,
        )
    elif family == "battle":
        result = parse_battle_request(
            arguments,
            target_user_id="SYNTHETIC-MEMBER",
            target_name="测试成员",
            **kwargs,
        )
    else:
        result = parse_item_bag_request(arguments, **kwargs)
    assert result.action == action


@pytest.mark.parametrize("platform", ["qq", "qq-official", "qq-official-bot2"])
async def test_baogian_code_survives_real_regex_to_handler(tmp_path: Path, monkeypatch, registered_patterns, platform):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_baogian_template(plugin, tmp_path)
        shutil.copytree(tmp_path / "data" / "assets", tmp_path / "assets", dirs_exist_ok=True)
        message = build_message(platform=platform, message_id="real-pattern-art-switch")
        identity = CommandIdentity(
            scope=ScopeKey(platform, "10001"),
            stream_id="stream-10001",
            user_id="20001",
            display_name="测试成员",
            message_id="seed-profile",
            group_name="抓猪测试群",
        )
        await plugin.gameplay_service.profile(identity)
        for code, instance_id in (("OTHER001", "untouched-pig"), ("Pig9Fun", "chosen-pig")):
            await _insert_baogian(
                plugin,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                short_code=code,
                instance_id=instance_id,
            )
        business = AsyncMock(wraps=plugin.gameplay_service.toggle_baogian)
        monkeypatch.setattr(plugin.gameplay_service, "toggle_baogian", business)
        command = "/切换 猪保千 pig9fun"
        match = _registered_match(registered_patterns, command, "pig_catcher_toggle_baogian")
        assert match.groupdict()["arguments"] == "pig9fun"
        result = await plugin.handle_toggle_baogian(
            stream_id=identity.stream_id,
            matched_groups=match.groupdict(),
            raw_message=command,
            message=message,
        )
        assert result[0]
        assert business.await_count == 1
        assert business.await_args.kwargs["short_code"] == "pig9fun"
        rows = await plugin.database.fetch_all(
            "SELECT pig_instance_id, display_variant, official_value, state FROM pig_instances ORDER BY pig_instance_id"
        )
        assert [tuple(row) for row in rows] == [
            ("chosen-pig", "sticker", 100, "active"),
            ("untouched-pig", "pig", 100, "active"),
        ]
        assert len(context.send.images) == 1 and not context.send.texts
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("prefix", ["[CQ:at,qq=TEST-BOT] ", "<@!TEST-BOT> ", "@测试机器人 "])
@pytest.mark.parametrize("topic", ["", "对战"])
async def test_help_accepts_official_leading_mentions_without_rendering(
    tmp_path: Path, registered_patterns, prefix, topic
):
    plugin, context = await create_test_plugin(tmp_path, config_updates={"features": {"battle_enabled": True}})
    try:
        command = prefix + "/抓猪帮助" + (f" {topic}" if topic else "")
        match = _registered_match(registered_patterns, command, "pig_catcher_help")
        assert (match.groupdict().get("topic") or "") == topic
        result = await plugin.handle_help(
            stream_id="stream-10001",
            matched_groups=match.groupdict(),
            raw_message=command,
            message=build_message(platform="qq-official", message_id="help-prefix"),
        )
        assert result[0] and "/抓猪帮助" in result[1]
        if topic:
            assert "/战斗猪" in result[1]
        assert context.send.texts == [("stream-10001", result[1])]
        assert not context.send.images and not context.render.calls
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM command_receipts"))[0] == 0
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM players"))[0] == 0
    finally:
        await plugin.on_unload()


async def test_injected_unknown_long_help_topic_has_bounded_output(tmp_path: Path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        topic = "未知主题" * 3000
        result = await plugin.handle_help(
            stream_id="stream-10001",
            matched_groups={"topic": topic},
            raw_message="/抓猪帮助 " + topic,
            message=build_message(message_id="long-help-topic"),
        )
        assert result[0]
        assert len(result[1]) <= 2000
        assert topic not in result[1]
        assert "/抓猪帮助" in result[1]
        assert len(context.send.texts) == 1
        assert not context.send.images and not context.render.calls
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("component", ["pig_catcher_catalog", "pig_catcher_food_catalog"])
def test_undiscovered_catalog_example_requires_quality_prefix(registered_patterns, component):
    command = "/猪猪图鉴 品质=未收集" if component == "pig_catcher_catalog" else "/美食图鉴 品质=未收集"
    match = _registered_match(registered_patterns, command, component)
    assert parse_catalog_query(match.groupdict()["arguments"]).undiscovered_only
    with pytest.raises(DomainValidationError, match="无法识别图鉴参数"):
        parse_catalog_query("未收集")


@pytest.mark.parametrize(
    "component,command,expected_groups",
    (
        ("pig_catcher_batch_cook", "/批量做菜 五星", {"arguments": "五星"}),
        ("pig_catcher_batch_sell", "/批量售卖 美食 猪寿司拼盘", {"arguments": "美食 猪寿司拼盘"}),
        ("pig_catcher_enable_batch_keep", "/开启批量保留", {}),
        ("pig_catcher_disable_batch_keep", "/关闭批量保留", {}),
        ("pig_catcher_reset_quota_chance", "/重置额度", {}),
        ("pig_catcher_toggle_uika", "/切换 初华猪 a1B2c3D4", {"code": "a1B2c3D4"}),
        (
            "pig_catcher_achievement_reforge",
            "/重铸编号 猪猪 A1B2C3D4 CCCC1111",
            {"kind": "猪猪", "old_code": "A1B2C3D4", "new_code": "CCCC1111"},
        ),
    ),
)
def test_missing_legacy_help_entries_have_unique_working_routes(
    registered_patterns, component, command, expected_groups
):
    match = _registered_match(registered_patterns, command, component)
    assert match.groupdict() == expected_groups
    if component == "pig_catcher_batch_cook":
        assert parse_batch_cook_query(match.groupdict()["arguments"]).rarity == 5
    if component == "pig_catcher_batch_sell":
        assert parse_batch_sale_query(match.groupdict()["arguments"]).display_name == "猪寿司拼盘"


def test_batch_help_does_not_add_an_unsupported_retention_kind(registered_patterns):
    text = format_help("批量")
    for command, component in (
        ("/开启批量保留", "pig_catcher_enable_batch_keep"),
        ("/关闭批量保留", "pig_catcher_disable_batch_keep"),
    ):
        assert command in text
        assert re.search(re.escape(command) + r"\s+<", text) is None
        _registered_match(registered_patterns, command, component)
        assert registered_patterns[component].fullmatch(command + " 猪猪") is None
        assert registered_patterns[component].fullmatch(command + " 美食") is None


def test_reward_help_requires_a_ticket_name_when_stopping_its_selection():
    text = format_help("奖励")
    stop_line = next(line for line in text.splitlines() if line.startswith("/成就奖励 停用"))
    assert re.search(r"/成就奖励 停用\s+(?:<[^>]*券名>|口袋行李券)", stop_line), stop_line


def test_inventory_help_sort_labels_are_the_actual_parser_enums():
    text = format_help("背包")
    for label, accepted in (("猪猪排序", INVENTORY_SORTS), ("美食排序", FOOD_INVENTORY_SORTS)):
        match = re.search(label + r"[：:]([^；;。\n]+)", text)
        assert match is not None, label
        advertised = {part.strip() for part in match.group(1).split("、")}
        assert advertised and advertised <= accepted, advertised - accepted
