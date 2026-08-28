"""Focused integration of the art layer, not a production acceptance run."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pig_catcher.commands.context import extract_command_identity
from pig_catcher.domain.achievements import ACHIEVEMENT_DEFINITIONS, AchievementReward
from pig_catcher.domain.weekly_competitions import (
    WEEKLY_SPRINT_BADGE_IDS,
    WEEKLY_SPRINT_FRAME_ID,
    WEEKLY_SPRINT_TITLE_ID,
)
from pig_catcher.infrastructure.repositories import AchievementRepository
from pig_catcher.rendering import InventoryViewModel, PigCatcherRenderer
from pig_catcher.rendering.adapters import _achievement_reward_text, achievement_row_view
from pig_catcher.rendering.cosmetics import cosmetic_cache_info, cosmetic_detail
from pig_catcher.services.achievements import AchievementService

from .helpers import FakeRender, build_message, create_test_plugin
from .test_plugin import _command_kwargs
from .test_rendering import _options


def test_hidden_locked_reward_never_reads_cosmetic_images(monkeypatch) -> None:
    def forbidden(*_args):
        raise AssertionError("A hidden, locked reward must not load image bytes")

    monkeypatch.setattr("pig_catcher.rendering.cosmetics._image", forbidden)
    entry = SimpleNamespace(
        achievement_id="hidden-fixture", name="？？？", category="隐藏彩蛋",
        tier_label="隐藏", unlocked=False, hidden=True, description="等待解锁",
        progress=0, target=1, points=20, unlocked_at="",
        rewards=(AchievementReward("title", "rain-love"),),
    )
    view = achievement_row_view(entry)
    assert view.cosmetics == ()
    assert view.reward_text == "解锁后揭晓"
    assert "rain-love" not in repr(view.cosmetics)


def test_all_equipped_badges_resolve_to_their_actual_artwork() -> None:
    for definition in ACHIEVEMENT_DEFINITIONS:
        badge = next((r for r in definition.rewards if r.reward_type == "badge"), None)
        if badge is None:
            continue
        profile = dict(equipped_title_id="", equipped_frame_id="",
                       showcase_achievement_id=definition.achievement_id)
        selected = AchievementService._cosmetics_from_profile(profile)
        assert cosmetic_detail(selected.badge_name, kind="badge")["id"] == badge.reward_id


@pytest.mark.parametrize("rank", [1, 2, 3, 10])
def test_weekly_only_equipped_badge_resolves_to_earned_rank(rank: int) -> None:
    profile = dict(
        equipped_title_id=WEEKLY_SPRINT_TITLE_ID,
        equipped_frame_id=WEEKLY_SPRINT_FRAME_ID,
        showcase_achievement_id=WEEKLY_SPRINT_BADGE_IDS[rank],
    )
    selected = AchievementService._cosmetics_from_profile(profile)
    art = cosmetic_detail(selected.badge_name, kind="badge")
    assert art["id"] == WEEKLY_SPRINT_BADGE_IDS[rank]
    assert art["rank"] == rank and art["is_plate"] and art["image_data_url"]


def test_cosmetic_reward_labels_are_public_names_not_internal_ids() -> None:
    rewards = (AchievementReward("title", "rain-love"), AchievementReward("frame", "achievement-pale-pink"))
    text = _achievement_reward_text(rewards)
    assert "雨爱" in text and "淡粉" in text
    assert "rain-love" not in text and "achievement-pale-pink" not in text


def test_cosmetic_fields_are_keyword_only_and_do_not_grant_default_art() -> None:
    view = InventoryViewModel("游客", 1, 1, 0, None, "价值", ())
    assert view.achievement_title == view.achievement_frame == view.achievement_badge == ""
    assert replace(view, achievement_title="rain-love").achievement_title == "rain-love"


@pytest.mark.asyncio
async def test_bulk_cosmetics_query_is_readonly_and_group_scoped(tmp_path: Path, monkeypatch) -> None:
    plugin, _ = await create_test_plugin(tmp_path)
    repository = AchievementRepository()
    identities = [
        extract_command_identity("stream-10001", {"message": build_message(group_id=group)})
        for group in ("10001", "20002")
    ]
    try:
        for identity in identities:
            await plugin.gameplay_service.profile(identity)
        async with plugin.database.transaction() as session:
            for identity in identities:
                await repository.ensure_profile(session, player_id=identity.player_id, now="2026-08-28T00:00:00Z")
            await repository.grant_reward(
                session, player_id=identities[0].player_id, reward_type="title", reward_id="rain-love",
                quantity=1, now="2026-08-28T00:00:00Z",
            )
            assert await repository.update_equipped_cosmetics(
                session, player_id=identities[0].player_id, title_id="rain-love", frame_id=None,
                showcase_achievement_id=None, now="2026-08-28T00:00:00Z",
            )

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("A leaderboard must not run per-player profile/count queries")

        monkeypatch.setattr(plugin._achievement_service.repository, "profile_row", forbidden)
        result = await plugin._achievement_service.cosmetics_for_players([i.player_id for i in identities])
        assert cosmetic_detail(result[identities[0].player_id].title_id)["id"] == "rain-love"
        assert result[identities[1].player_id].title_id == ""
        unchanged = await plugin.database.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory WHERE player_id=? AND reward_id='rain-love'",
            (identities[0].player_id,),
        )
        assert unchanged["quantity"] == 1
        assert await plugin._achievement_service.cosmetics_for_players([]) == {}
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
async def test_weekly_only_profile_displays_equipped_plate(tmp_path: Path) -> None:
    plugin, context = await create_test_plugin(
        tmp_path, config_updates={"features": {"achievements_enabled": False, "weekly_competitions_enabled": True}},
    )
    message = build_message(message_id="weekly-art-profile")
    identity = extract_command_identity("stream-10001", {"message": message})
    try:
        await plugin.gameplay_service.profile(identity)
        repository = AchievementRepository()
        async with plugin.database.transaction() as session:
            await repository.ensure_profile(session, player_id=identity.player_id, now="2026-08-28T00:00:00Z")
            for kind, reward in (("title", WEEKLY_SPRINT_TITLE_ID), ("frame", WEEKLY_SPRINT_FRAME_ID),
                                 ("badge", WEEKLY_SPRINT_BADGE_IDS[1])):
                await repository.grant_reward(session, player_id=identity.player_id, reward_type=kind,
                                              reward_id=reward, quantity=1, now="2026-08-28T00:00:00Z")
            assert await repository.update_equipped_cosmetics(
                session, player_id=identity.player_id, title_id=WEEKLY_SPRINT_TITLE_ID,
                frame_id=WEEKLY_SPRINT_FRAME_ID, showcase_achievement_id=WEEKLY_SPRINT_BADGE_IDS[1],
                now="2026-08-28T00:00:00Z",
            )
        result = await plugin.handle_profile(**_command_kwargs(message))
        assert result[0]
        html = next(html for html, _ in reversed(context.render.calls) if "抓猪档案" in html)
        assert "抓猪冲刺者" in html
        assert cosmetic_detail(WEEKLY_SPRINT_BADGE_IDS[1])["image_data_url"] in html
        assert 'class="cosmetic-edge"' in html
        async def forbidden_backfill(*_args, **_kwargs):
            raise AssertionError("Unequipping must not backfill disabled achievements")

        plugin._achievement_service._ensure_identity_profile = forbidden_backfill
        await plugin._achievement_service.clear_equipped_cosmetics(identity)
        after = await plugin._achievement_service.cosmetics_for_player(identity.player_id)
        assert after.title_id == after.frame_id == after.badge_name == ""
    finally:
        await plugin.on_unload()


@pytest.mark.asyncio
async def test_inventory_template_reads_owned_cosmetics_and_cache_can_be_released() -> None:
    capability = FakeRender()
    renderer = PigCatcherRenderer(capability, _options())
    view = InventoryViewModel(
        "<script>游客</script>", 1, 1, 0, None, "价值", (),
        achievement_title="rain-love", achievement_frame="achievement-pale-pink",
    )
    await renderer.render_inventory(view, {})
    html = capability.calls[-1][0]
    assert "&lt;script&gt;" in html and "<script>游客</script>" not in html
    assert 'alt="雨爱"' in html and 'class="cosmetic-edge"' in html
    assert cosmetic_cache_info()["bytes"] > 0
    renderer.clear_art_cache()
    assert cosmetic_cache_info()["bytes"] == 0
