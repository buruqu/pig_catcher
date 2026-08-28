"""背包/奖励券 SDK 接线、图片回执和已持有券的成就开关解耦。"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from pig_catcher.commands.context import extract_command_identity
from pig_catcher.domain.item_bag import CODE_CHANGE_COUPON, PIG_CHOICE_COUPON
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository

from .helpers import build_message, create_plugin, create_test_plugin
from .test_plugin import _command_kwargs, _install_test_pig

NOW = "2026-08-28T00:00:00.000Z"


async def grant(plugin, identity, reward_id, quantity=1, *, kind="ticket"):
    async with plugin.database.transaction() as session:
        await FrameworkRepository().touch_identity(session, identity=identity, now=NOW)
        if reward_id in {CODE_CHANGE_COUPON, PIG_CHOICE_COUPON}:
            return await plugin._item_bag_service.grant_coupon(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                coupon_id=reward_id,
                quantity=quantity,
                source_id=f"test:{identity.player_id}:{reward_id}",
                now=NOW,
            )
        await AchievementRepository().grant_reward(
            session,
            player_id=identity.player_id,
            reward_type=kind,
            reward_id=reward_id,
            quantity=quantity,
            now=NOW,
        )


def identity_for(message):
    return extract_command_identity(message["session_id"], {"message": message})


async def no_achievement_tracking(plugin):
    for table in (
        "achievement_profiles",
        "achievement_progress",
        "achievement_events",
        "achievement_unlocks",
        "achievement_metric_counters",
        "achievement_backfill_state",
    ):
        assert (await plugin.database.fetch_one(f"SELECT COUNT(*) FROM {table}"))[0] == 0, table


@pytest.mark.parametrize(
    "name,text,arguments",
    [
        ("pig_catcher_item_bag", "/道具背包", None),
        ("pig_catcher_item_bag", "<@BOT> /道具背包 2", "2"),
        ("pig_catcher_reward_coupon", "/使用奖励券 编号修改卷 猪猪 old1 New2", "编号修改卷 猪猪 old1 New2"),
        ("pig_catcher_reward_coupon", "[CQ:at,qq=BOT] /使用奖励券 确认", "确认"),
    ],
)
def test_explicit_commands_accept_official_mentions(name, text, arguments):
    commands = {item["name"]: item for item in create_plugin().get_components()}
    pattern = commands[name]["metadata"]["command_pattern"]
    assert re.fullmatch(pattern, text).group("arguments") == arguments
    assert re.fullmatch(pattern, "道具背包") is None
    assert re.fullmatch(pattern, "/是") is None


async def test_item_bag_service_lifecycle_and_empty_image_without_achievements(tmp_path: Path):
    plugin, context = await create_test_plugin(tmp_path)
    service = plugin._item_bag_service
    assert service is not None
    result = await plugin.handle_item_bag(stream_id="stream-10001", **_command_kwargs(build_message(), arguments=""))
    assert result[0]
    assert len(context.send.images) == 1 and not context.send.texts
    html = context.render.calls[-1][0]
    assert "道具背包" in html and "背包空空" in html and "道具持有人" in html
    assert "远行社成员" not in html and "猪猪远行社" not in html
    await no_achievement_tracking(plugin)
    await plugin.on_unload()
    assert plugin._item_bag_service is None
    await plugin.on_load()
    assert plugin._item_bag_service is not None and plugin._item_bag_service is not service
    await plugin.on_unload()


@pytest.mark.parametrize("platform", ["qq", "qq-official", "qq-official-bot2"])
async def test_choice_command_images_confirmation_and_receipt_deduplication(tmp_path: Path, platform):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_test_pig(plugin, tmp_path)
        message = build_message(platform=platform, message_id="coupon-preview")
        identity = identity_for(message)
        await grant(plugin, identity, PIG_CHOICE_COUPON)
        preview = await plugin.handle_reward_coupon(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments="猪猪自选券 命令测试猪")
        )
        assert preview[0] and len(context.send.images) == 1
        assert "30秒" in context.render.calls[-1][0] and "命令测试猪" in context.render.calls[-1][0]
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 0
        confirm_message = build_message(platform=platform, message_id="coupon-confirm")
        confirmed = await plugin.handle_reward_coupon(
            stream_id=identity.stream_id, **_command_kwargs(confirm_message, arguments="确认")
        )
        assert confirmed[0] and len(context.send.images) == 2 and not context.send.texts
        assert "自选猪猪已到背包" in context.render.calls[-1][0]
        duplicate = await plugin.handle_reward_coupon(
            stream_id=identity.stream_id, **_command_kwargs(confirm_message, arguments="确认")
        )
        assert duplicate == (True, "该消息已处理，不重复公示。", 0)
        assert len(context.send.images) == 2
        asset = await plugin.database.fetch_one("SELECT * FROM pig_instances")
        assert asset["scope_id"] == identity.scope.value and asset["owner_player_id"] == identity.player_id
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM group_records"))[0] == 0
        assert (await plugin.database.fetch_one("SELECT total_catches FROM player_statistics"))[0] == 0
        assert (
            await plugin.database.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (identity.player_id,))
        )[0] == 0
        await no_achievement_tracking(plugin)
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("coupon_id", [CODE_CHANGE_COUPON, "identifier-reforge"])
async def test_legacy_reforge_entry_uses_both_coupons_and_images_without_achievements(tmp_path, coupon_id):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_test_pig(plugin, tmp_path)
        message = build_message(message_id="reforge-entry")
        identity = identity_for(message)
        await grant(plugin, identity, PIG_CHOICE_COUPON)
        await plugin._item_bag_service.execute(replace(identity, message_id="pick"), "猪猪自选券 命令测试猪")
        await plugin._item_bag_service.execute(replace(identity, message_id="confirm"), "确认")
        pig = await plugin.database.fetch_one("SELECT * FROM pig_instances")
        await grant(plugin, identity, coupon_id)
        result = await plugin.handle_achievement_reforge(
            stream_id=identity.stream_id,
            **_command_kwargs(message, kind="猪猪", old_code=pig["short_code"].lower(), new_code="MyPig2026"),
        )
        assert result[0] and len(context.send.images) == 1 and not context.send.texts
        assert "MYPIG2026" in context.render.calls[-1][0] and "剩余" in context.render.calls[-1][0]
        assert (await plugin.database.fetch_one("SELECT short_code FROM pig_instances"))[0] == "MYPIG2026"
        await no_achievement_tracking(plugin)
    finally:
        await plugin.on_unload()


async def test_reward_coupon_image_failure_keeps_single_committed_asset_and_safe_text(tmp_path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_test_pig(plugin, tmp_path)
        identity = identity_for(build_message(message_id="choose"))
        await grant(plugin, identity, PIG_CHOICE_COUPON)
        await plugin._item_bag_service.execute(identity, "猪猪自选券 命令测试猪")
        context.render.error = RuntimeError("offline-render-test")
        confirm = build_message(message_id="confirm")
        result = await plugin.handle_reward_coupon(
            stream_id=identity.stream_id, **_command_kwargs(confirm, arguments="确认")
        )
        assert result[0] and len(context.send.texts) == 1
        assert "命令测试猪" in context.send.texts[0][1]
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 1
        repeat = await plugin.handle_reward_coupon(
            stream_id=identity.stream_id, **_command_kwargs(confirm, arguments="确认")
        )
        assert repeat == (True, "该消息已处理，不重复公示。", 0)
        assert len(context.send.texts) == 1
        assert (
            await plugin.database.fetch_one(
                "SELECT quantity FROM achievement_reward_inventory WHERE reward_id=?", (PIG_CHOICE_COUPON,)
            )
        )[0] == 0
    finally:
        await plugin.on_unload()


async def test_owned_legacy_ticket_can_activate_without_backfill_or_achievements(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        message = build_message(message_id="activate-owned-ticket")
        identity = identity_for(message)
        await grant(plugin, identity, "achievement-catch", 2)
        result = await plugin.handle_achievement_ticket(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments="成就抓猪券")
        )
        assert result[0]
        repeated = await plugin.handle_achievement_ticket(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments="成就抓猪券")
        )
        assert repeated[0]
        assert (await plugin.database.fetch_one("SELECT quantity FROM achievement_reward_inventory"))[0] == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM achievement_ticket_effects"))[0] == 1
        await no_achievement_tracking(plugin)
    finally:
        await plugin.on_unload()


async def test_owned_activity_ticket_and_material_choice_work_with_achievement_feature_off(tmp_path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        message = build_message(message_id="activity-select")
        identity = identity_for(message)
        await grant(plugin, identity, "dispatch-bill", 2)
        await grant(plugin, identity, "materials-choice", 3, kind="chest")
        selected = await plugin.handle_achievement_ticket(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments="远行路费券")
        )
        assert selected[0]
        preview = await plugin.handle_activity_rewards(
            stream_id=identity.stream_id,
            **_command_kwargs(build_message(message_id="material-preview"), arguments="材料 基础材料自选份 训练矿石 2"),
        )
        assert preview[0]
        confirm = await plugin.handle_activity_rewards(
            stream_id=identity.stream_id,
            **_command_kwargs(build_message(message_id="material-confirm"), arguments="确认"),
        )
        assert confirm[0]
        assert len(context.send.images) == 3 and not context.send.texts
        assert (
            await plugin.database.fetch_one(
                "SELECT quantity FROM achievement_reward_inventory WHERE reward_id='materials-choice'"
            )
        )[0] == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM achievement_coupon_selection"))[0] == 1
        await no_achievement_tracking(plugin)
    finally:
        await plugin.on_unload()


async def test_closed_or_private_context_denies_item_bag_without_creating_player(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        result = await plugin.handle_item_bag(
            stream_id="stream-10001", **_command_kwargs(build_message(private=True), arguments="")
        )
        assert not result[0]
        config = plugin.get_plugin_config_data()
        config["plugin"]["enabled"] = False
        plugin.set_plugin_config(config)
        result = await plugin.handle_reward_coupon(
            stream_id="stream-10001", **_command_kwargs(build_message(), arguments="确认")
        )
        assert not result[0]
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM players"))[0] == 0
    finally:
        await plugin.on_unload()
