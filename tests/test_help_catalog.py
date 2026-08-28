"""2.0 分专题帮助的公开契约；只读纯函数，不连接正式机器人或数据库。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pig_catcher.commands.battle import parse_battle_request
from pig_catcher.commands.dispatch import parse_dispatch_request
from pig_catcher.commands.help import format_help
from pig_catcher.commands.item_bag import parse_item_bag_request
from pig_catcher.commands.parsers import (
    parse_batch_cook_query,
    parse_batch_sale_query,
    parse_catalog_query,
    parse_food_inventory_query,
    parse_inventory_query,
    parse_item_use_query,
    parse_purchase_query,
    parse_store_query,
    parse_upgrade_name,
)
from pig_catcher.commands.tour import parse_tour_request
from pig_catcher.config import PigCatcherConfig
from pig_catcher.domain.enums import AssetKind
from pig_catcher.domain.gameplay import ITEM_DEFINITIONS

PUBLIC_TOPICS = (
    "抓猪",
    "背包",
    "做菜",
    "批量",
    "商城",
    "道具",
    "交易",
    "排行",
    "成就",
    "奖励",
    "周榜",
    "派遣",
    "巡演",
    "对战",
    "术式",
    "叠加",
)


@pytest.mark.parametrize("topic,prefix", [("派遣", "/猪猪派遣 编队 1 "), ("巡演", "/乐队编队 1 ")])
def test_ready_to_copy_team_examples_use_real_current_pigs(topic: str, prefix: str) -> None:
    """解析器接受任意名称不等于玩家照抄后能找到猪，示例另核正式目录。"""
    catalog = Path(__file__).resolve().parents[1] / "catalogs/formal/pig-and-food-definitions.json"
    definitions = json.loads(catalog.read_text(encoding="utf-8"))["entries"]
    pigs = {entry["display_name"]: entry for entry in definitions if entry["kind"] == "pig"}
    line = next(line for line in format_help(topic).splitlines() if line.startswith(prefix))
    selectors = line.removeprefix(prefix).split("、")
    assert all(name in pigs for name in selectors)
    if topic == "派遣":
        assert 1 <= len(selectors) <= 3
        assert any(pigs[name]["rarity"] <= 3 for name in selectors)
        assert sum(pigs[name]["rarity"] >= 4 for name in selectors) <= 1
    else:
        assert 3 <= len(selectors) <= 5


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _contains_integer(text: str, value: int) -> bool:
    return re.search(rf"(?<![0-9]){value}(?![0-9])", text.replace(",", "")) is not None


def _has_range(text: str, start: int, stop: int) -> bool:
    return re.search(rf"{start}\s*[至到~～–—-]\s*{stop}", text) is not None


def _settings(**features: bool) -> PigCatcherConfig:
    return PigCatcherConfig.model_validate({"features": features})


def test_main_help_is_a_short_directory_not_all_advanced_commands() -> None:
    text = format_help()
    assert isinstance(text, str)
    assert 100 <= len(text) <= 600
    for command in ("/抓猪", "/猪猪背包", "/做菜", "/猪猪商城", "/抓猪帮助"):
        assert command in text
    for topic in PUBLIC_TOPICS:
        assert topic in text
    for command in ("/比划比划", "/战利品抓猪", "/领域展开", "/使用成就券"):
        assert command not in text


@pytest.mark.parametrize("topic", ["", "全部", " ", "  全部  "])
def test_legacy_all_and_blank_return_the_compact_directory(topic: str) -> None:
    assert format_help(topic) == format_help()


@pytest.mark.parametrize("alias,topic", [("仓库", "背包"), ("图鉴", "背包"), ("经济", "商城")])
def test_existing_topic_aliases_are_preserved(alias: str, topic: str) -> None:
    assert format_help(alias) == format_help(topic)
    assert format_help(f"  {alias}  ") == format_help(topic)


@pytest.mark.parametrize("topic", PUBLIC_TOPICS)
def test_each_public_topic_is_bounded_copyable_text_with_a_return_route(topic: str) -> None:
    text = format_help(topic)
    assert 20 <= len(text) <= 2000
    assert topic in text
    assert "/抓猪帮助" in text
    assert "```" not in text
    assert "data:image/" not in text
    assert "<img" not in text.casefold()
    assert "<svg" not in text.casefold()
    assert "![" not in text


@pytest.mark.parametrize("topic", ["", "全部", *PUBLIC_TOPICS])
def test_public_help_excludes_admin_commands_hidden_aliases_and_spoilers(topic: str) -> None:
    text = format_help(topic)
    for forbidden in ("/猪管", "/抓猪档案", "/抓猪详情"):
        assert forbidden not in text
    # 玩家糖醋排骨的 /重置额度 是公开玩法；仅禁止独立的管理员 /重置。
    assert re.search(r"/重置(?=$|[\s；;，,。|｜）)\]])", text) is None
    for spoiler in (
        "五条猪不能直接",
        "解除无下限",
        "不需要天逆鉾",
        "必得专属",
        "连续50次",
        "连续 50 次",
        "947947",
        "100次猪寿司拼盘",
        "100 次猪寿司拼盘",
    ):
        assert spoiler not in text
    # 可以诚实说明旧缺口，但不能把未实现的三槽展示架写成可用能力。
    assert re.search(r"(?:可用|支持|同时佩戴|同时展示).{0,8}(?:三|3)(?:格|槽|枚)", text) is None
    assert re.search(r"(?:三|3)(?:格|槽).{0,8}(?:已开放|已启用|可用)", text) is None


def test_unknown_topic_points_to_the_directory_without_dumping_all_commands() -> None:
    text = format_help("不存在")
    assert "不存在" in text and "未知" in text
    assert "/抓猪帮助" in text
    assert len(text) <= 600
    assert "/领域展开" not in text
    assert "/战利品抓猪" not in text


def test_inventory_help_explains_exact_selectors_and_protection() -> None:
    text = format_help("背包")
    assert "/猪猪详情" in text
    assert "/收藏" in text and "/取消收藏" in text
    assert "#" in text and "编号" in text
    assert "大小写" in text
    assert "收藏" in text and ("不会" in text or "不参与" in text or "跳过" in text)
    assert "品质=未收集" in text
    assert "/切换 初华猪" in text and "/切换 猪保千" in text


def test_quick_eating_help_describes_the_last_edible_copy_confirmation() -> None:
    text = format_help("做菜")
    compact = _compact(text)
    assert "同名" in text
    assert "最低价值" in text or "最低价" in text
    assert "最后" in text
    assert "30秒" in compact
    assert "/是" in text and "/否" in text
    assert "超时" in text or "自动退出" in text or "自动取消" in text
    assert "收藏" in text
    assert "/使用美食" in text


def test_batch_help_distinguishes_default_low_stars_explicit_quality_and_retention() -> None:
    text = format_help("批量")
    assert "/批量做菜" in text and "/批量售卖" in text
    assert "/开启批量保留" in text and "/关闭批量保留" in text
    assert _has_range(text, 1, 3) or "一至三星" in text
    assert _has_range(text, 1, 5) or "一至五星" in text
    assert "联动" in text and "最高" in text
    assert re.search(r"(?:1|一)\s*只", text)
    assert "收藏" in text
    assert "六星" in text or "6星" in _compact(text)
    assert "美食 <菜名>" in text or "美食 猪" in text


@pytest.mark.parametrize("item", ITEM_DEFINITIONS, ids=lambda item: item.item_id)
def test_store_help_uses_real_current_item_prices(item: Any) -> None:
    text = format_help("商城")
    matching_lines = [line for line in text.splitlines() if item.display_name in line]
    assert matching_lines, item.display_name
    assert any(_contains_integer(line, item.price) for line in matching_lines), (item.display_name, item.price)


def test_spear_public_effect_stays_the_approved_non_spoiler_summary() -> None:
    spear = next(item for item in ITEM_DEFINITIONS if item.item_id == "inverted-spear-of-heaven")
    assert spear.effect_summary == "解除术式"
    text = format_help("商城")
    line = next(line for line in text.splitlines() if spear.display_name in line)
    assert "解除术式" in line
    assert "五条" not in line and "无下限" not in line


def test_help_uses_runtime_upgrade_prices_instead_of_a_hardcoded_table() -> None:
    feed = [311, 733, 1777, 3889, 8111]
    cookware = [299, 611, 1559, 3449, 6997]
    settings = PigCatcherConfig.model_validate(
        {"economy": {"feed_upgrade_prices": feed, "cookware_upgrade_prices": cookware}}
    )
    text = format_help("商城", settings=settings)
    for price in (*feed, *cookware):
        assert _contains_integer(text, price), price
    assert "猪饲料" in text and "厨具" in text
    assert not _contains_integer(text, 8000)
    assert not _contains_integer(text, 7000)
    # 另一份配置不能错误复用上一个群或旧配置的帮助缓存。
    fresh = format_help("商城", settings=PigCatcherConfig())
    assert _contains_integer(fresh, 8000)
    assert not _contains_integer(fresh, 8111)


def test_help_uses_runtime_catch_quota_refresh_hours_and_cooldown() -> None:
    settings = PigCatcherConfig.model_validate(
        {"catching": {"daily_limit": 11, "cooldown_seconds": 17, "quota_refresh_hours": [0, 6, 18]}}
    )
    text = format_help("抓猪", settings=settings)
    for hour in (0, 6, 18):
        assert f"{hour:02d}:00" in text
    assert "09:00" not in text and "12:00" not in text and "19:00" not in text
    assert re.search(r"11\s*次", text)
    assert re.search(r"17\s*秒", text)
    assert "北京时间" in text


def test_item_queue_help_shows_remaining_uses_and_separate_action_slots() -> None:
    text = format_help("道具")
    assert "/道具背包" in text
    assert "/使用道具" in text and "/取消道具" in text
    assert "抓猪" in text and "做菜" in text
    assert "同名" in text and "数量" in text
    assert "剩余" in text
    assert "成功" in text and "消耗" in text


def test_stacking_help_keeps_latest_mist_rule_and_exclusive_resources() -> None:
    text = format_help("叠加")
    assert "雾蓝" in text
    mist = next(line for line in text.splitlines() if "雾蓝" in line)
    assert re.search(r"10\s*次", mist)
    assert "随机" in mist or "洗牌" in mist or "反转" in mist
    assert "不叠加" in mist or "独占" in mist
    assert "61.538" not in text
    assert "保留" in text and "不消耗" in text
    assert "顺序" in text or "先" in text
    assert "批量" in text


def test_reward_help_separates_coupons_and_namespaced_confirmation() -> None:
    text = format_help("奖励")
    for command in ("/成就奖励", "/使用成就券", "/使用奖励券", "/重铸编号"):
        assert command in text
    assert "编号修改券" in text and "猪猪自选券" in text
    assert "/使用奖励券 确认" in text and "/使用奖励券 取消" in text
    assert "30秒" in _compact(text)
    assert "不可交易" in text or "不可赠送" in text or "不能交易" in text


@pytest.mark.parametrize(
    "topic,feature,hidden_commands",
    [
        ("派遣", "dispatch_enabled", ("/猪猪派遣", "/派遣背包", "/派遣奇遇")),
        ("巡演", "tour_enabled", ("/猪猪巡演", "/组建乐队", "/巡演联演")),
        ("对战", "battle_enabled", ("/战斗猪", "/比划比划", "/战利品抓猪")),
        ("周榜", "weekly_competitions_enabled", ("/抓猪线", "/zzx")),
    ],
)
def test_disabled_feature_topics_show_status_not_an_executable_menu(
    topic: str, feature: str, hidden_commands: tuple[str, ...]
) -> None:
    text = format_help(topic, settings=_settings(**{feature: False}))
    assert "未启用" in text or "已关闭" in text
    for command in hidden_commands:
        assert command not in text
    assert "/抓猪帮助" in text


def test_coupon_and_bag_help_remain_available_with_achievements_and_items_disabled() -> None:
    settings = _settings(achievements_enabled=False, items_enabled=False)
    bag = format_help("道具", settings=settings)
    rewards = format_help("奖励", settings=settings)
    assert "/道具背包" in bag
    assert "/使用道具" not in bag and "/取消道具" not in bag
    assert "/成就奖励" in rewards
    assert "/使用奖励券" in rewards
    assert "/使用成就券" in rewards
    assert "/重铸编号" in rewards


def test_weekly_only_cosmetics_keep_the_wear_menu_without_achievement_statistics() -> None:
    text = format_help("成就", settings=_settings(achievements_enabled=False, weekly_competitions_enabled=True))
    assert "/佩戴成就" in text and "/取消佩戴成就" in text
    assert "/猪猪成就" not in text and "/成就排行" not in text and "/成就详情" not in text
    assert "周榜" in text or "周冲榜" in text


def test_all_disabled_cosmetics_do_not_advertise_a_wear_command() -> None:
    text = format_help("成就", settings=_settings(achievements_enabled=False, weekly_competitions_enabled=False))
    assert "未启用" in text or "已关闭" in text
    assert "/佩戴成就" not in text and "/取消佩戴成就" not in text


@pytest.mark.parametrize("gift_enabled,trade_enabled", [(False, True), (True, False), (False, False)])
def test_social_help_obeys_independent_gift_and_trade_switches(gift_enabled: bool, trade_enabled: bool) -> None:
    settings = PigCatcherConfig.model_validate(
        {"trading": {"gift_enabled": gift_enabled, "trade_enabled": trade_enabled}}
    )
    text = format_help("交易", settings=settings)
    assert ("/猪猪赠送" in text) is gift_enabled
    assert ("/美食赠送" in text) is gift_enabled
    assert ("/猪猪交易" in text) is trade_enabled
    assert ("/接受交易" in text) is trade_enabled


def test_format_help_is_deterministic_does_not_mutate_settings_or_write_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = PigCatcherConfig()
    before = settings.model_dump(mode="json")

    def unexpected_database(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("文字帮助不应连接数据库")

    monkeypatch.setattr(sqlite3, "connect", unexpected_database)
    monkeypatch.chdir(tmp_path)
    for topic in ("", *PUBLIC_TOPICS):
        assert format_help(topic, settings=settings) == format_help(topic, settings=settings)
    assert settings.model_dump(mode="json") == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "inventory_enabled,catalog_enabled", [(False, False), (False, True), (True, False), (True, True)]
)
def test_main_menu_treats_inventory_and_catalog_as_independent_switches(
    inventory_enabled: bool, catalog_enabled: bool
) -> None:
    text = format_help(settings=_settings(inventory_enabled=inventory_enabled, catalog_enabled=catalog_enabled))
    assert ("/猪猪背包" in text) is inventory_enabled
    assert ("/猪猪图鉴" in text) is catalog_enabled
    assert len(text) <= 600


def test_disabling_eating_removes_confirmation_instructions_from_notes_too() -> None:
    text = format_help("做菜", settings=_settings(eating_enabled=False))
    assert "/做菜" in text and "/美食背包" in text
    for command in ("/吃菜", "/使用美食", "/是", "/否"):
        assert command not in text


def test_live_cooking_and_trade_timing_are_not_copied_from_default_help() -> None:
    settings = PigCatcherConfig.model_validate(
        {
            "cooking": {"cook_cooldown_seconds": 37},
            "trading": {"offer_expiry_minutes": 19, "max_trade_price": 876543},
        }
    )
    cook_text = format_help("做菜", settings=settings)
    trade_text = format_help("交易", settings=settings)
    assert re.search(r"37\s*秒", cook_text)
    assert re.search(r"19\s*分钟", trade_text)
    assert _contains_integer(trade_text, 876543)
    settings.features.cooking_enabled = False
    settings.trading.trade_enabled = False
    assert not re.search(r"37\s*秒", format_help("做菜", settings=settings))
    assert not re.search(r"19\s*分钟", format_help("交易", settings=settings))
    assert not _contains_integer(format_help("交易", settings=settings), 876543)


def test_boundary_runtime_values_still_produce_bounded_help() -> None:
    settings = PigCatcherConfig.model_validate(
        {
            "catching": {"daily_limit": 1000, "cooldown_seconds": 86400, "quota_refresh_hours": list(range(24))},
            "economy": {
                "feed_upgrade_prices": [2147483647] * 5,
                "cookware_upgrade_prices": [2147483647] * 5,
            },
            "trading": {"offer_expiry_minutes": 1440, "max_trade_price": 2147483647},
        }
    )
    assert len(format_help(settings=settings)) <= 600
    for topic in PUBLIC_TOPICS:
        assert len(format_help(topic, settings=settings)) <= 2000
    catching = format_help("抓猪", settings=settings)
    for hour in range(24):
        assert f"{hour:02d}:00" in catching


def test_disabling_gameplay_does_not_hide_existing_unconditionally_available_rewards() -> None:
    defaults = PigCatcherConfig()
    settings = PigCatcherConfig.model_validate(
        {
            "features": {name: False for name in type(defaults.features).model_fields},
            "trading": {"gift_enabled": False, "trade_enabled": False},
        }
    )
    directory = format_help(settings=settings)
    assert "道具" in directory and "奖励" in directory
    assert "/猪猪背包" not in directory and "/做菜" not in directory and "/猪猪商城" not in directory
    assert re.search(r"/抓猪(?=$|\s)", directory) is None
    assert "/道具背包" in format_help("道具", settings=settings)
    assert "/使用奖励券" in format_help("奖励", settings=settings)
    assert "/打开成就宝箱" not in format_help("奖励", settings=settings)
    assert "/领取成就纪念猪" not in format_help("奖励", settings=settings)


def test_unknown_topic_is_bounded_and_cannot_echo_notification_markup() -> None:
    unknown = "[CQ:at,qq=all]<@everyone>\n" + "长输入" * 10000
    text = format_help(unknown)
    assert len(text) <= 600
    assert "[CQ:" not in text
    assert "<@" not in text
    assert "@everyone" not in text
    assert "长输入" * 100 not in text
    assert "/抓猪帮助" in text


@pytest.mark.parametrize(
    "topic,command,parser,arguments,expected",
    [
        ("背包", "/猪猪背包", parse_inventory_query, "2 品质=4 排序=重量", {"page": 2, "rarity": 4, "sort": "重量"}),
        (
            "做菜",
            "/美食背包",
            parse_food_inventory_query,
            "2 品质=5 排序=份量",
            {"page": 2, "rarity": 5, "sort": "份量"},
        ),
        ("背包", "/猪猪图鉴", parse_catalog_query, "品质=未收集", {"undiscovered_only": True}),
        ("商城", "/猪猪商城", parse_store_query, "分类=做菜", {"category": "做菜"}),
        ("商城", "/购买", parse_purchase_query, "超级幸运猪哨 2", {"product_name": "超级幸运猪哨", "quantity": 2}),
        ("道具", "/使用道具", parse_item_use_query, "幸运猪哨 3", {"item_name": "幸运猪哨", "quantity": 3}),
        ("批量", "/批量做菜", parse_batch_cook_query, "五星", {"rarity": 5}),
        (
            "批量",
            "/批量售卖",
            parse_batch_sale_query,
            "美食 猪寿司拼盘",
            {"asset_kind": AssetKind.FOOD, "display_name": "猪寿司拼盘"},
        ),
    ],
)
def test_advertised_selector_forms_work_in_real_parsers(
    topic: str,
    command: str,
    parser: Callable[[str], Any],
    arguments: str,
    expected: dict[str, Any],
) -> None:
    assert command in format_help(topic)
    parsed = parser(arguments)
    for key, value in expected.items():
        assert getattr(parsed, key) == value


@pytest.mark.parametrize("product", ["猪饲料", "厨具"])
def test_advertised_upgrades_have_real_parser_support(product: str) -> None:
    assert "/升级" in format_help("商城") and product in format_help("商城")
    assert parse_upgrade_name(product) == product


@pytest.mark.parametrize(
    "topic,command,parser,arguments,kwargs,action",
    [
        ("派遣", "/猪猪派遣", parse_dispatch_request, "出发 1 回声矿洞 8小时", {}, "start"),
        ("派遣", "/猪猪派遣", parse_dispatch_request, "确认", {}, "confirm"),
        ("派遣", "/派遣背包", parse_dispatch_request, "制作 区域地图 2", {"section": "bag"}, "craft"),
        ("巡演", "/组建乐队", parse_tour_request, "测试乐队", {"section": "band", "entry": "组建乐队"}, "create"),
        (
            "巡演",
            "/乐队编队",
            parse_tour_request,
            "1 偶像猪、天才猪、武士道猪",
            {"section": "band", "entry": "乐队编队"},
            "roster",
        ),
        ("巡演", "/猪猪巡演", parse_tour_request, "确认", {}, "confirm"),
        ("对战", "/战斗猪", parse_battle_request, "设置 宿傩猪", {}, "assign_preview"),
        ("对战", "/战斗猪", parse_battle_request, "确认", {}, "confirm"),
        ("对战", "/比划比划", parse_battle_request, "接受", {"section": "challenge"}, "accept"),
        (
            "奖励",
            "/使用奖励券",
            parse_item_bag_request,
            "编号修改券 猪猪 笨猪#Abc1 MyPig8",
            {"section": "coupon"},
            "rename",
        ),
        ("奖励", "/使用奖励券", parse_item_bag_request, "猪猪自选券 地球猪", {"section": "coupon"}, "choose-pig"),
        ("奖励", "/使用奖励券", parse_item_bag_request, "确认", {"section": "coupon"}, "confirm"),
    ],
)
def test_advertised_feature_flows_use_existing_parsers_and_confirmation_namespaces(
    topic: str,
    command: str,
    parser: Callable[..., Any],
    arguments: str,
    kwargs: dict[str, Any],
    action: str,
) -> None:
    assert command in format_help(topic)
    assert parser(arguments, **kwargs).action == action
