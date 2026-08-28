"""阶段 3 玩家成功出口：真 renderer 接线、已结算结果、失败文字兜底。"""

from __future__ import annotations

# The workspace import convention groups stdlib from-imports before plain imports.
# ruff: noqa: I001
from dataclasses import replace
from unittest.mock import AsyncMock

import shutil

import pytest

from pig_catcher.domain.achievements import ACHIEVEMENT_DEFINITIONS
from pig_catcher.domain.weekly_competitions import (
    WEEKLY_SPRINT_BADGE_IDS,
    WEEKLY_SPRINT_FRAME_ID,
    WEEKLY_SPRINT_TITLE_ID,
)
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository

from .helpers import build_message, create_test_plugin
from .test_item_bag_plugin import NOW, grant, identity_for
from .test_plugin import _command_kwargs, _insert_baogian, _install_baogian_template, _install_test_pig


@pytest.mark.parametrize("platform", ["qq", "qq-official", "qq-official-bot2"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_batch_keep_setting_is_rendered_without_changing_its_rules(tmp_path, platform, enabled):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        message = build_message(platform=platform, message_id="keep-status")
        identity = identity_for(message)
        handler = plugin.handle_enable_batch_keep if enabled else plugin.handle_disable_batch_keep
        result = await handler(stream_id=identity.stream_id, **_command_kwargs(message))
        assert result[0] and len(context.send.images) == 1 and not context.send.texts
        html = context.render.calls[-1][0]
        assert "保留设置已更新" in html and "每种始终保留最高价值一只" in html
        assert "批量操作始终跳过" in html
        row = await plugin.database.fetch_one(
            "SELECT batch_keep_highest FROM players WHERE player_id=?", (identity.player_id,)
        )
        assert int(row[0]) == int(enabled)
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM command_receipts"))[0] == 0
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("platform", ["qq", "qq-official", "qq-official-bot2"])
@pytest.mark.parametrize("display_name", ["保千猪", "初华猪"])
@pytest.mark.parametrize("render_fails", [False, True])
async def test_art_toggle_renders_changed_pig_but_never_toggles_again_for_preview(
    tmp_path, monkeypatch, platform, display_name, render_fails
):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_baogian_template(plugin, tmp_path)
        # The legacy helper installs in a child data root.  Copy only these tiny
        # disposable fixture media into this test plugin's actual data root.
        shutil.copytree(tmp_path / "data" / "assets", tmp_path / "assets", dirs_exist_ok=True)
        message = build_message(platform=platform, message_id="art-toggle-status")
        identity = identity_for(message)
        await plugin.gameplay_service.profile(identity)
        await _insert_baogian(
            plugin,
            scope_id=identity.scope.value,
            player_id=identity.player_id,
            short_code="ART2026",
            instance_id="art-preview-pig",
        )
        if display_name == "初华猪":
            async with plugin.database.transaction() as session:
                await session.execute("UPDATE pig_templates SET display_name='初华猪'")
                await session.execute("UPDATE pig_instances SET display_name_snapshot='初华猪'")
        method_name = "toggle_baogian" if display_name == "保千猪" else "toggle_pig_art"
        business = AsyncMock(wraps=getattr(plugin.gameplay_service, method_name))
        monkeypatch.setattr(plugin.gameplay_service, method_name, business)
        card = AsyncMock(wraps=plugin._render_pig_card)
        monkeypatch.setattr(plugin, "_render_pig_card", card)
        if render_fails:
            context.render.error = RuntimeError("offline art preview failure")
        kwargs = {"arguments": "art2026"} if display_name == "保千猪" else {"code": "art2026"}
        handler = plugin.handle_toggle_baogian if display_name == "保千猪" else plugin.handle_toggle_uika
        result = await handler(stream_id=identity.stream_id, **_command_kwargs(message, **kwargs))
        assert result[0] and "ART2026" in result[1]
        assert business.await_count == 1 and card.await_count == 1
        shown_pig = card.await_args.args[0]
        assert shown_pig.display_name == display_name and shown_pig.display_variant == "sticker"
        assert shown_pig.alternate_image_relpath
        row = await plugin.database.fetch_one(
            "SELECT display_variant, official_value, state FROM pig_instances WHERE pig_instance_id='art-preview-pig'"
        )
        assert tuple(row) == ("sticker", 100, "active")
        if render_fails:
            assert not context.send.images and len(context.send.texts) == 1
            assert "切换为" in context.send.texts[0][1]
        else:
            assert len(context.send.images) == 1 and not context.send.texts
            assert "立绘已切换" in context.render.calls[-1][0]
            command_name = "猪保千" if display_name == "保千猪" else "初华猪"
            assert f"/切换 {command_name} ART2026" in context.render.calls[-1][0]
            if display_name == "初华猪":
                assert "普通版与戴帽子版" in context.render.calls[-1][0]
                assert "/切换 猪保千" not in context.render.calls[-1][0]
    finally:
        await plugin.on_unload()


async def test_failed_toggle_stays_an_error_message_without_claiming_an_image(tmp_path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        result = await plugin.handle_toggle_uika(stream_id="stream-10001", **_command_kwargs(build_message(), code=""))
        assert not result[0] and "你还没有初华猪" in result[1]
        assert len(context.send.texts) == 1 and not context.send.images and not context.render.calls
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("kind", ["猪猪", "美食"])
@pytest.mark.parametrize("render_fails", [False, True])
async def test_favorite_card_uses_committed_receipt_and_keeps_send_deduplication(tmp_path, kind, render_fails):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        await _install_test_pig(plugin, tmp_path, include_food=True)
        message = build_message(platform="qq-official-bot2", message_id="favorite-status")
        identity = identity_for(message)
        caught = await plugin.gameplay_service.catch(replace(identity, message_id="favorite-seed"))
        asset = caught.pig
        if kind == "美食":
            cooked = await plugin.economy_service.cook(replace(identity, message_id="favorite-cook"), asset.selector)
            asset = cooked.foods[0]
        if render_fails:
            context.render.error = RuntimeError("offline favorite image failure")
        command = _command_kwargs(message, action="收藏", kind=kind, selector=asset.selector)
        result = await plugin.handle_favorite(stream_id=identity.stream_id, **command)
        assert result[0]
        repeated = await plugin.handle_favorite(stream_id=identity.stream_id, **command)
        assert repeated == (True, "该消息已处理，不重复公示。", 0)
        table = "pig_instances" if kind == "猪猪" else "food_instances"
        row = await plugin.database.fetch_one(
            f"SELECT is_favorite, state FROM {table} WHERE short_code=?", (asset.short_code,)
        )
        assert tuple(row) == (1, "active")
        assert len(context.send.texts if render_fails else context.send.images) == 1
        assert "收藏保护已开启" in context.render.calls[0][0]
        cancelled = await plugin.handle_favorite(
            stream_id=identity.stream_id,
            **_command_kwargs(
                build_message(platform="qq-official-bot2", message_id="unfavorite-status"),
                action="取消收藏",
                kind=kind,
                selector=asset.selector,
            ),
        )
        assert cancelled[0]
        assert (
            await plugin.database.fetch_one(f"SELECT is_favorite FROM {table} WHERE short_code=?", (asset.short_code,))
        )[0] == 0
        assert len(context.send.texts if render_fails else context.send.images) == 2
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize(
    "ticket_id,ticket_name",
    [
        ("achievement-catch", "成就抓猪券"),
        ("catalog-guide", "图鉴引路券"),
        ("food-inspiration", "美食灵感券"),
        ("giant-rescale", "巨物复秤券"),
        ("mini-rescale", "迷你复秤券"),
        ("recook", "回锅重做券"),
        ("achievement-firework", "成就礼花券"),
    ],
)
async def test_all_legacy_tickets_have_safe_images_and_keep_single_activation(tmp_path, ticket_id, ticket_name):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        message = build_message(message_id="legacy-ticket-status")
        identity = identity_for(message)
        await grant(plugin, identity, ticket_id, 2)
        for _ in range(2):
            result = await plugin.handle_achievement_ticket(
                stream_id=identity.stream_id, **_command_kwargs(message, arguments=ticket_name)
            )
            assert result[0] and ticket_id not in result[1]
        assert len(context.send.images) == 2 and not context.send.texts
        assert "成就券已激活" in context.render.calls[-1][0]
        assert ticket_name in context.render.calls[-1][0]
        assert ticket_id not in context.render.calls[-1][0]
        assert (
            await plugin.database.fetch_one(
                "SELECT quantity FROM achievement_reward_inventory WHERE reward_id=?", (ticket_id,)
            )
        )[0] == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM achievement_ticket_effects"))[0] == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM achievement_operations"))[0] == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM achievement_profiles"))[0] == 0
    finally:
        await plugin.on_unload()


async def test_legacy_ticket_render_failure_keeps_activation_and_hides_internal_id(tmp_path):
    plugin, context = await create_test_plugin(tmp_path)
    try:
        message = build_message(message_id="legacy-ticket-fallback")
        identity = identity_for(message)
        await grant(plugin, identity, "achievement-catch", 2)
        context.render.error = RuntimeError("offline ticket image failure")
        for _ in range(2):
            result = await plugin.handle_achievement_ticket(
                stream_id=identity.stream_id, **_command_kwargs(message, arguments="成就抓猪券")
            )
            assert result[0]
        assert len(context.send.texts) == 2 and not context.send.images
        assert all("成就抓猪券" in text and "achievement-catch" not in text for _, text in context.send.texts)
        assert (await plugin.database.fetch_one("SELECT quantity FROM achievement_reward_inventory"))[0] == 1
        assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM achievement_ticket_effects"))[0] == 1
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("choice", ["抓猪", "做菜", "图鉴", "外观"])
@pytest.mark.parametrize("render_fails", [False, True])
async def test_choice_chest_renders_actual_rewards_once_and_replays_without_consumption(tmp_path, choice, render_fails):
    plugin, context = await create_test_plugin(tmp_path, config_updates={"features": {"achievements_enabled": True}})
    try:
        message = build_message(message_id="chest-status")
        identity = identity_for(message)
        await grant(plugin, identity, "achievement-choice", 2, kind="chest")
        if render_fails:
            context.render.error = RuntimeError("offline chest image failure")
        first = await plugin.handle_achievement_chest(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments=choice)
        )
        before = [
            tuple(row)
            for row in await plugin.database.fetch_all(
                "SELECT reward_type, reward_id, quantity FROM achievement_reward_inventory "
                "ORDER BY reward_type,reward_id"
            )
        ]
        # Same message with changed arguments must display the saved choice, not
        # claim new rewards from the second argument or consume another chest.
        repeated = await plugin.handle_achievement_chest(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments="做菜" if choice != "做菜" else "抓猪")
        )
        assert first[0] and repeated[0] and first[1] == repeated[1]
        after = [
            tuple(row)
            for row in await plugin.database.fetch_all(
                "SELECT reward_type, reward_id, quantity FROM achievement_reward_inventory "
                "ORDER BY reward_type,reward_id"
            )
        ]
        assert before == after
        assert (
            await plugin.database.fetch_one(
                "SELECT quantity FROM achievement_reward_inventory WHERE reward_type='chest'"
            )
        )[0] == 1
        assert (
            await plugin.database.fetch_one(
                "SELECT COUNT(*) FROM achievement_operations WHERE operation_type='open-choice-chest'"
            )
        )[0] == 1
        assert len(context.send.texts if render_fails else context.send.images) == 2
        assert "宝箱奖励已领取" in context.render.calls[0][0]
        if choice == "外观":
            assert "自选粉金边框" in first[1] and "achievement-choice" not in first[1]
            assert "自选粉金边框" in context.render.calls[0][0]
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("render_fails", [False, True])
async def test_equip_owned_hidden_cosmetics_previews_real_profile_without_exposing_ids(
    tmp_path, monkeypatch, render_fails
):
    plugin, context = await create_test_plugin(tmp_path, config_updates={"features": {"achievements_enabled": True}})
    try:
        message = build_message(platform="qq-official", message_id="equip-hidden-cosmetic")
        identity = identity_for(message)
        achievement = next(item for item in ACHIEVEMENT_DEFINITIONS if item.achievement_id == "hidden-domain-gojo-cook")
        await plugin._achievement_service._ensure_identity_profile(identity)
        async with plugin.database.transaction() as session:
            await AchievementRepository().upsert_progress(
                session,
                player_id=identity.player_id,
                achievement_id=achievement.achievement_id,
                definition_version=achievement.definition_version,
                progress_value=1,
                state_json="{}",
                unlocked_at=NOW,
                now=NOW,
            )
        for reward in achievement.rewards:
            await grant(plugin, identity, reward.reward_id, reward.quantity, kind=reward.reward_type)
        equip = AsyncMock(wraps=plugin._achievement_service.equip_cosmetics_by_achievement)
        monkeypatch.setattr(plugin._achievement_service, "equip_cosmetics_by_achievement", equip)
        dispatch = AsyncMock(side_effect=AssertionError("Preview must never start a dispatch"))
        monkeypatch.setattr(plugin._dispatch_service, "execute", dispatch)
        if render_fails:
            context.render.error = RuntimeError("offline cosmetics preview failure")
        result = await plugin.handle_achievement_equip(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments=achievement.name)
        )
        assert result[0] and equip.await_count == 1 and dispatch.await_count == 0
        assert "rain-love" not in result[1] and "domain-gojo" not in result[1]
        profile = await plugin.database.fetch_one(
            "SELECT equipped_title_id, showcase_achievement_id FROM achievement_profiles WHERE player_id=?",
            (identity.player_id,),
        )
        assert tuple(profile) == ("rain-love", achievement.achievement_id)
        assert len(context.send.texts if render_fails else context.send.images) == 1
        html = context.render.calls[-1][0]
        assert "雨爱" in html and "外观佩戴成功" in html
        assert "cosmetic-plate" in html and "data:image/webp;base64," in html
        assert "远行社成员" not in html and "TRAVEL JOURNAL" not in html
    finally:
        await plugin.on_unload()


async def test_weekly_only_cosmetics_can_be_previewed_and_removed_without_losing_rewards(tmp_path, monkeypatch):
    plugin, context = await create_test_plugin(
        tmp_path, config_updates={"features": {"weekly_competitions_enabled": True, "achievements_enabled": False}}
    )
    try:
        message = build_message(platform="qq-official-bot2", message_id="equip-weekly-only")
        identity = identity_for(message)
        rewards = (
            ("title", WEEKLY_SPRINT_TITLE_ID),
            ("frame", WEEKLY_SPRINT_FRAME_ID),
            ("badge", WEEKLY_SPRINT_BADGE_IDS[1]),
        )
        for kind, reward_id in rewards:
            await grant(plugin, identity, reward_id, kind=kind)
        async with plugin.database.transaction() as session:
            await AchievementRepository().ensure_profile(session, player_id=identity.player_id, now=NOW)

        async def equip_existing_weekly_reward(actor, _):
            async with plugin.database.transaction() as session:
                await AchievementRepository().update_equipped_cosmetics(
                    session,
                    player_id=actor.player_id,
                    title_id=WEEKLY_SPRINT_TITLE_ID,
                    frame_id=WEEKLY_SPRINT_FRAME_ID,
                    showcase_achievement_id=WEEKLY_SPRINT_BADGE_IDS[1],
                    now=NOW,
                )
            return ("称号·抓猪冲刺者", "边框·抓猪冲刺", "牌子·第一名")

        equip = AsyncMock(side_effect=equip_existing_weekly_reward)
        monkeypatch.setattr(plugin._weekly_competition_service, "equip_competition_cosmetics", equip)
        result = await plugin.handle_achievement_equip(
            stream_id=identity.stream_id, **_command_kwargs(message, arguments="抓猪冲刺！！！")
        )
        assert result[0] and equip.await_count == 1
        html = context.render.calls[-1][0]
        assert "抓猪冲刺者" in html and "抓猪冲刺！！！·1牌" in html and "cosmetic-plate" in html
        unexpected_backfill = AsyncMock(side_effect=AssertionError("Removing cosmetics must not backfill achievements"))
        monkeypatch.setattr(plugin._achievement_service, "_ensure_identity_profile", unexpected_backfill)
        removed = await plugin.handle_achievement_unequip(
            stream_id=identity.stream_id,
            **_command_kwargs(build_message(platform="qq-official-bot2", message_id="unequip-weekly-only")),
        )
        assert removed[0] and len(context.send.images) == 2 and not context.send.texts
        assert unexpected_backfill.await_count == 0
        assert "外观已卸下" in context.render.calls[-1][0]
        profile = await plugin.database.fetch_one(
            "SELECT equipped_title_id, equipped_frame_id, showcase_achievement_id "
            "FROM achievement_profiles WHERE player_id=?",
            (identity.player_id,),
        )
        assert tuple(profile) == ("", "", "")
        assert [
            row[0] for row in await plugin.database.fetch_all("SELECT quantity FROM achievement_reward_inventory")
        ] == [1, 1, 1]
    finally:
        await plugin.on_unload()
