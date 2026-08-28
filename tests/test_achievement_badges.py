"""三格徽章展示架：历史迁移、授权、幂等与现有结果图接线。"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from dataclasses import replace

import pytest

from pig_catcher.domain.achievements import ACHIEVEMENT_DEFINITIONS
from pig_catcher.domain.battle_views import BattleView
from pig_catcher.domain.dispatch_views import DispatchView
from pig_catcher.domain.errors import DomainValidationError, ReceiptConflictError
from pig_catcher.domain.tour_views import TourView
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.repositories import AchievementRepository
from pig_catcher.rendering.cosmetics import COSMETIC_DEFINITIONS, cosmetic_detail
from pig_catcher.services.achievement_badges import AchievementBadgeService
from pig_catcher.services.achievements import AchievementService
from pig_catcher.version import SCHEMA_VERSION

from .helpers import build_message, create_plugin, create_test_plugin
from .test_asset_code_lifecycle import NOW, OWNER, _create_v41
from .test_item_bag_plugin import grant, identity_for
from .test_plugin import _command_kwargs, _install_test_pig

LABELS = {key: value["name"] for key, value in COSMETIC_DEFINITIONS.items() if value["kind"] == "badge"}
BADGES = ("kfc-thursday", "achievement-choice", "weekly-001-catch-value-rank-1")


async def seed(plugin, identity, *, three=True):
    await plugin.gameplay_service.profile(identity)
    async with plugin.database.transaction() as session:
        await AchievementRepository().ensure_profile(session, player_id=identity.player_id, now=NOW)
    for badge in BADGES:
        await grant(plugin, identity, badge, kind="badge")
    if three:
        await grant(plugin, identity, "badge-showcase-3", kind="cosmetic")
    return AchievementBadgeService(plugin._achievement_service, labels=LABELS)


def command(message, **kwargs):
    return {"stream_id": message["session_id"], **_command_kwargs(message, **kwargs)}


@pytest.mark.parametrize("stored_slots", [False, True])
@pytest.mark.parametrize(
    ("badge_id", "badge_name"),
    [
        ("badge-ten-moves", "十式研习"),
        ("weekly-001-catch-value-rank-1", "抓猪冲刺！！！·1牌"),
        ("", ""),
    ],
)
def test_badge_legacy_name_is_public_while_showcase_ids_stay_canonical(stored_slots, badge_id, badge_name):
    profile = {
        "equipped_title_id": "",
        "equipped_frame_id": "",
        "showcase_achievement_id": badge_id,
    }
    if stored_slots:
        profile.update(
            badge_slots_json=json.dumps({"1": badge_id, "2": BADGES[0], "3": BADGES[1]}),
            badge_capacity=3,
        )
    cosmetics = AchievementService._cosmetics_from_profile(profile)
    assert cosmetics.badge_name == badge_name
    assert cosmetics.badge_ids == ((badge_id, BADGES[0], BADGES[1]) if stored_slots else (badge_id,))
    assert cosmetics.badge_capacity == (3 if stored_slots else 1)


@pytest.mark.parametrize("prefix", ["", "<@BOT> ", "[CQ:at,qq=BOT] "])
@pytest.mark.parametrize("arguments", ["", "查看 2", "1 成就自选徽章", "2 weekly-001-catch-value-rank-1", "卸下 3"])
def test_badge_command_has_unique_official_safe_route(prefix, arguments):
    components = create_plugin().get_components()
    text = prefix + "/成就徽章" + (" " + arguments if arguments else "")
    matched = [
        item["name"]
        for item in components
        if item["metadata"].get("command_pattern") and re.fullmatch(item["metadata"]["command_pattern"], text)
    ]
    assert matched == ["pig_catcher_achievement_badges"]


@pytest.mark.parametrize("source", ["achievement", "weekly", "unowned"])
async def test_schema_45_migration_preserves_only_owned_first_badge_and_reopens(tmp_path, source):
    path = tmp_path / "old.sqlite3"
    connection = _create_v41(path)
    for migration in MIGRATIONS:
        if 42 <= migration.version <= 45:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, NOW))
    definition = next(item for item in ACHIEVEMENT_DEFINITIONS if item.achievement_id == "hidden-kfc-thursday-catch")
    connection.execute(
        "INSERT INTO achievement_definition_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            definition.achievement_id,
            1,
            definition.name,
            definition.category,
            "rare",
            1,
            20,
            "private",
            "private",
            "{}",
            '[{"type":"badge","id":"kfc-thursday","quantity":1}]',
            NOW,
        ),
    )
    legacy = definition.achievement_id if source != "weekly" else BADGES[2]
    badge = BADGES[0] if source != "weekly" else BADGES[2]
    connection.execute(
        "INSERT INTO achievement_profiles(player_id,equipped_title_id,equipped_frame_id,"
        "showcase_achievement_id,created_at,updated_at) VALUES(?,'rain-love','hollow-purple',?,?,?)",
        (OWNER, legacy, NOW, NOW),
    )
    if source != "unowned":
        connection.execute("INSERT INTO achievement_reward_inventory VALUES(?,'badge',?,1,?)", (OWNER, badge, NOW))
    connection.execute("PRAGMA user_version=45")
    connection.commit()
    before = connection.execute("SELECT * FROM players").fetchall()
    connection.close()
    db = PigCatcherDatabase(path)
    await db.open()
    assert await db.schema_version() == SCHEMA_VERSION
    assert [tuple(row) for row in await db.fetch_all("SELECT * FROM players")] == before
    slots = await db.fetch_all("SELECT slot,badge_id FROM achievement_badge_slots")
    assert [tuple(row) for row in slots] == ([] if source == "unowned" else [(1, badge)])
    assert tuple(
        await db.fetch_one(
            "SELECT equipped_title_id,equipped_frame_id,showcase_achievement_id FROM achievement_profiles"
        )
    ) == ("rain-love", "hollow-purple", legacy)
    assert await db.integrity_check() == ("ok",)
    assert not await db.fetch_all("PRAGMA foreign_key_check")
    await db.close()
    await db.open()
    assert len(await db.fetch_all("SELECT * FROM achievement_badge_slots")) == len(slots)
    assert (await db.fetch_one("SELECT COUNT(*) FROM schema_migrations WHERE version=46"))[0] == 1
    await db.close()


async def test_points_alone_do_not_bypass_permanent_showcase_entitlement(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity, three=False)
        async with plugin.database.transaction() as session:
            await session.execute(
                "UPDATE achievement_profiles SET achievement_points=500 WHERE player_id=?", (identity.player_id,)
            )
        await service.execute(replace(identity, message_id="single"), "1 成就自选徽章")
        for slot in (2, 3):
            with pytest.raises(DomainValidationError, match="500"):
                await service.execute(replace(identity, message_id=f"locked-{slot}"), f"{slot} {BADGES[0]}")
        assert (await service.execute(identity)).view.achievement_badge_capacity == 1
        await grant(plugin, identity, "badge-showcase-3", kind="cosmetic")
        await service.execute(replace(identity, message_id="unlocked"), "2 疯狂星期四的邀约")
        assert (await service.execute(identity)).view.achievement_badges == (BADGES[1], BADGES[0], "")
        assert (
            await plugin.database.fetch_one(
                "SELECT quantity FROM achievement_reward_inventory WHERE reward_id='badge-showcase-3'"
            )
        )[0] == 1
        assert not await plugin.database.fetch_all("SELECT * FROM currency_ledger")
        assert not await plugin.database.fetch_all("SELECT * FROM achievement_unlocks")
    finally:
        await plugin.on_unload()


async def test_actual_500_point_milestone_grants_showcase_once_and_survives_clear(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity, three=False)
        for points in (499, 500, 500):
            async with plugin.database.transaction() as session:
                await session.execute(
                    "UPDATE achievement_profiles SET achievement_points=? WHERE player_id=?",
                    (points, identity.player_id),
                )
                await plugin._achievement_service._settle_milestones(
                    session, player_id=identity.player_id, scope_id=identity.scope.value, now=NOW
                )
            assert (await service.execute(identity)).view.achievement_badge_capacity == (1 if points == 499 else 3)
        assert (
            await plugin.database.fetch_one(
                "SELECT quantity FROM achievement_reward_inventory WHERE player_id=? AND reward_id='badge-showcase-3'",
                (identity.player_id,),
            )
        )[0] == 1
        assert (
            await plugin.database.fetch_one(
                "SELECT COUNT(*) FROM achievement_milestone_claims WHERE player_id=? AND milestone_points=500",
                (identity.player_id,),
            )
        )[0] == 1
        await plugin._achievement_service.clear_equipped_cosmetics(identity)
        assert (await service.execute(identity)).view.achievement_badge_capacity == 3
    finally:
        await plugin.on_unload()


async def test_badge_commands_do_not_track_achievements_even_when_enabled(tmp_path, monkeypatch):
    plugin, context = await create_test_plugin(tmp_path, config_updates={"features": {"achievements_enabled": True}})
    try:
        message = build_message(message_id="only-cosmetics")
        await seed(plugin, identity_for(message))

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("换徽章不得启动成就统计或补发")

        monkeypatch.setattr(plugin, "_process_achievement_receipt", forbidden)
        monkeypatch.setattr(plugin, "_process_weekly_competition_receipt", forbidden)
        monkeypatch.setattr(plugin, "_deliver_achievement_backfill_summary", forbidden)
        for args in ("", "1 疯狂星期四的邀约", "1 疯狂星期四的邀约"):
            assert (await plugin.handle_achievement_badges(**command(message, arguments=args)))[0]
        assert len(context.send.images) == 2
        assert not await plugin.database.fetch_all("SELECT * FROM achievement_events")
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("broken", ["table", "guard"])
async def test_current_version_refuses_missing_badge_schema(tmp_path, broken):
    from pig_catcher.domain.errors import MigrationError

    path = tmp_path / "badges.sqlite3"
    db = PigCatcherDatabase(path)
    await db.open()
    await db.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DROP TABLE achievement_badge_slots"
            if broken == "table"
            else "DROP TRIGGER achievement_badge_slot_insert_guard"
        )
    with pytest.raises(MigrationError):
        await db.open()


@pytest.mark.parametrize(
    "other",
    [
        dict(group_id="20002"),
        dict(user_id="second-player"),
        dict(platform="qq-official"),
        dict(platform="qq-official", group_id="20002"),
    ],
)
async def test_badges_and_entitlement_never_cross_scope_or_owner(tmp_path, other):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        owner = identity_for(build_message())
        await seed(plugin, owner)
        other_identity = identity_for(build_message(**other))
        service = AchievementBadgeService(plugin._achievement_service, labels=LABELS)
        view = (await service.execute(other_identity)).view
        assert view.achievement_badges == ("",) and view.achievement_badge_capacity == 1
        for badge in BADGES:
            with pytest.raises(DomainValidationError, match="本群尚未获得"):
                await service.execute(replace(other_identity, message_id=badge), f"1 {badge}")
            assert LABELS[badge] not in view.text()
        assert not await plugin.database.fetch_all("SELECT * FROM achievement_badge_slots")
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("slot", [0, 4, -1])
async def test_invalid_slots_and_unowned_badges_cannot_mutate(tmp_path, slot):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity)
        with pytest.raises(DomainValidationError):
            await service.execute(identity, f"{slot} {BADGES[0]}")
        with pytest.raises(DomainValidationError, match="尚未获得"):
            await service.execute(identity, "1 domain-gojo")
        assert not await plugin.database.fetch_all("SELECT * FROM achievement_badge_slots")
        assert not await plugin.database.fetch_all("SELECT * FROM command_receipts")
    finally:
        await plugin.on_unload()


async def test_three_owned_badges_duplicate_guard_single_clear_and_legacy_clear(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity)
        for slot, badge in enumerate(BADGES, 1):
            await service.execute(replace(identity, message_id=str(slot)), f"{slot} {badge.upper()}")
        assert (await plugin._achievement_service.cosmetics_for_player(identity.player_id)).badge_ids == BADGES
        with pytest.raises(DomainValidationError, match="不能重复"):
            await service.execute(replace(identity, message_id="duplicate"), f"2 {BADGES[0]}")
        assert (await plugin._achievement_service.cosmetics_for_player(identity.player_id)).badge_ids == BADGES
        await service.execute(replace(identity, message_id="clear-one"), "卸下 2")
        assert (await service.execute(identity)).view.achievement_badges == (BADGES[0], "", BADGES[2])
        await plugin._achievement_service.clear_equipped_cosmetics(identity)
        assert (await service.execute(identity)).view.achievement_badges == ("", "", "")
        assert all(
            row[0] == 1 for row in await plugin.database.fetch_all("SELECT quantity FROM achievement_reward_inventory")
        )
    finally:
        await plugin.on_unload()


async def test_duplicate_badge_move_rolls_back_old_bundle_title_frame_changes(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity)
        await service.execute(replace(identity, message_id="slot-2"), f"2 {BADGES[0]}")
        await grant(plugin, identity, "rain-love", kind="title")
        async with plugin.database.transaction() as session:
            await AchievementRepository().update_equipped_title(
                session, player_id=identity.player_id, title_id="rain-love", now=NOW
            )
        with pytest.raises(DomainValidationError, match="不能重复"):
            async with plugin.database.transaction() as session:
                await AchievementRepository().update_equipped_cosmetics(
                    session,
                    player_id=identity.player_id,
                    title_id="",
                    frame_id="",
                    showcase_achievement_id="hidden-kfc-thursday-catch",
                    now=NOW,
                )
        outfit = await plugin._achievement_service.cosmetics_for_player(identity.player_id)
        assert outfit.title_id == "雨爱" and outfit.badge_ids == ("", BADGES[0], "")
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("render_fails", [False, True])
async def test_image_receipt_retry_restart_is_idempotent_and_no_reward_tracking(tmp_path, render_fails):
    config = {"features": {"achievements_enabled": False, "weekly_competitions_enabled": True}}
    plugin, context = await create_test_plugin(tmp_path, config_updates=config)
    message = build_message(platform="qq-official", message_id="badge-render")
    identity = identity_for(message)
    try:
        await seed(plugin, identity)
        if render_fails:
            context.render.error = RuntimeError("offline badge renderer fault")
        result = await plugin.handle_achievement_badges(**command(message, arguments="2 成就自选徽章"))
        assert result[0]
        assert len(context.send.texts if render_fails else context.send.images) == 1
        assert "第2位" in result[1]
        retry = await plugin.handle_achievement_badges(**command(message, arguments="2 成就自选徽章"))
        assert retry == (True, "该消息已处理，不重复公示。", 0)
        assert not await plugin.database.fetch_all("SELECT * FROM achievement_events")
        assert not await plugin.database.fetch_all("SELECT * FROM achievement_unlocks")
        assert not await plugin.database.fetch_all("SELECT * FROM currency_ledger")
        await plugin.on_unload()
        plugin, context = await create_test_plugin(tmp_path, config_updates=config)
        assert (await plugin._achievement_service.cosmetics_for_player(identity.player_id)).badge_ids == (
            "",
            BADGES[1],
            "",
        )
        retry = await plugin.handle_achievement_badges(**command(message, arguments="2 成就自选徽章"))
        assert retry == (True, "该消息已处理，不重复公示。", 0)
        assert not context.send.images and not context.send.texts
    finally:
        await plugin.on_unload()


async def test_replayed_receipt_never_reapplies_stale_equipment_and_conflicting_payload_is_rejected(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity)
        original = await service.execute(identity, f"1 {BADGES[0]}")
        await service.execute(replace(identity, message_id="next-command"), f"1 {BADGES[1]}")
        replay = await service.execute(identity, f"1 {BADGES[0]}")
        assert replay.receipt.receipt_id == original.receipt.receipt_id
        assert replay.view.achievement_badges[0] == BADGES[0]
        assert (await service.execute(replace(identity, message_id="query"))).view.achievement_badges[0] == BADGES[1]
        with pytest.raises(ReceiptConflictError):
            await service.execute(identity, f"1 {BADGES[2]}")
    finally:
        await plugin.on_unload()


async def test_concurrent_equip_keeps_one_badge_per_slot_and_one_receipt_per_message(tmp_path):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity)
        results = await asyncio.gather(*(service.execute(identity, f"1 {BADGES[0]}") for _ in range(8)))
        assert len({result.receipt.receipt_id for result in results}) == 1
        assert len(await plugin.database.fetch_all("SELECT * FROM achievement_badge_slots")) == 1
        assert len(await plugin.database.fetch_all("SELECT * FROM command_receipts")) == 1
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("kind", ["owned", "capacity", "duplicate"])
async def test_database_guards_protect_owned_capacity_and_duplicate_invariants(tmp_path, kind):
    plugin, _ = await create_test_plugin(tmp_path)
    try:
        identity = identity_for(build_message())
        service = await seed(plugin, identity, three=False)
        if kind == "duplicate":
            await grant(plugin, identity, "badge-showcase-3", kind="cosmetic")
            await service.execute(identity, f"1 {BADGES[0]}")
        with pytest.raises(sqlite3.IntegrityError):
            async with plugin.database.transaction() as session:
                await session.execute(
                    "INSERT INTO achievement_badge_slots VALUES(?,?,?,?)",
                    (
                        identity.player_id,
                        2 if kind != "owned" else 1,
                        "not-owned" if kind == "owned" else BADGES[0],
                        NOW,
                    ),
                )
    finally:
        await plugin.on_unload()


@pytest.mark.parametrize("view_type", [DispatchView, TourView, BattleView])
def test_feature_receipts_preserve_slots_and_read_legacy_payloads(view_type):
    view = view_type("徽章测试", "玩家", achievement_badges=BADGES, achievement_badge_capacity=3)
    assert view_type.from_payload(view.payload()).achievement_badges == BADGES
    legacy = view.payload()
    legacy.pop("achievement_badges")
    legacy.pop("achievement_badge_capacity")
    assert view_type.from_payload(legacy).achievement_badges == ()


async def test_all_three_badges_reach_real_profile_inventory_catch_cook_and_feature_templates(tmp_path):
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"features": {"weekly_competitions_enabled": True}, "catching": {"cooldown_seconds": 0}},
    )
    try:
        await _install_test_pig(plugin, tmp_path, include_food=True)
        identity = identity_for(build_message())
        service = await seed(plugin, identity)
        for slot, badge in enumerate(BADGES, 1):
            await service.execute(replace(identity, message_id=f"wear-{slot}"), f"{slot} {badge}")
        pig = (await plugin.gameplay_service.catch(replace(identity, message_id="badge-catch"))).pig
        food = (await plugin.economy_service.cook(replace(identity, message_id="badge-cook"), pig.selector)).foods[0]
        await plugin._render_pig_card(pig, mode_label="抓猪成功")
        await plugin._render_food_card(food, mode_label="做菜成功")
        await plugin.handle_profile(**command(build_message(message_id="badge-profile")))
        await plugin.handle_inventory(**command(build_message(message_id="badge-inventory")))
        feature = await plugin._activity_view_cosmetics(identity, DispatchView("测试结果", "玩家"))
        await plugin._renderer.render_dispatch(feature, {})
        for html, _ in context.render.calls[-5:]:
            for badge in BADGES:
                assert cosmetic_detail(badge)["image_data_url"] in html
            assert all(f'data-badge-slot="{slot}"' in html for slot in (1, 2, 3))
    finally:
        await plugin.on_unload()
