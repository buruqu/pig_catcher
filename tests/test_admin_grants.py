"""全资源发放：真实库存可用、跨群隔离、批次原子性和图片交付。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace

import pytest

from pig_catcher.commands.admin_grants import parse_grant_query
from pig_catcher.domain.admin_grants import GRANT_RESOURCES
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.infrastructure.repositories.item_bag import ItemBagRepository
from pig_catcher.infrastructure.repositories.materials import MaterialRepository
from pig_catcher.services.administration import AdministrationService

from .helpers import build_message, create_plugin, create_test_plugin
from .test_admin_commands import _admin_message, _identity, _seed_player
from .test_gameplay import _database_with_catalog, _food_entry, _pig_entry
from .test_plugin import _command_kwargs, _install_test_pig


async def invoke(plugin, text, message_id="grant", *, message=None, **groups):
    return await plugin.handle_admin_grant_resource(
        stream_id="stream-10001",
        **_command_kwargs(message or _admin_message(message_id=message_id), arguments=text, **groups),
    )


@pytest.fixture
async def world(tmp_path):
    plugin, context = await create_test_plugin(tmp_path, config_updates={"access": {"admin_user_ids": ["admin"]}})
    await _install_test_pig(plugin, tmp_path, include_food=True)
    await _seed_player(plugin, user_id="target", display_name="目标玩家")
    await _seed_player(plugin, user_id="target", display_name="别群同一人", group_id="10002")
    try:
        yield plugin, context
    finally:
        await plugin.on_unload()


async def test_every_registered_resource_reaches_live_inventory_for_all_players(world):
    plugin, context = world
    for index, resource in enumerate(GRANT_RESOURCES):
        result = await invoke(plugin, f"全员 {resource.name} 3", f"all-{index}")
        assert result[0], (resource, result)
    async with plugin.database.transaction(immediate=False) as session:
        target_bag = await ItemBagRepository().entries(session, player_id="qq:10001:target")
        admin_bag = await ItemBagRepository().entries(session, player_id="qq:10001:admin")
        other_bag = await ItemBagRepository().entries(session, player_id="qq:10002:target")
        materials = await MaterialRepository().balances(session, "qq:10001:target")
        assert not await MaterialRepository().reconcile(session)
    expected = {r.name for r in GRANT_RESOURCES if r.storage in {"item", "feature", "reward"}}
    assert expected <= {entry.name for entry in target_bag}
    assert expected <= {entry.name for entry in admin_bag}
    assert all(entry.available == 3 for entry in target_bag)
    assert not other_bag
    assert materials and set(materials.values()) == {3}
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM upgrades WHERE level=3"))[0] == 4
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM currency_ledger"))[0] == 0
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM achievement_events"))[0] == 0
    assert len(context.send.images) == len(GRANT_RESOURCES)
    assert not context.send.texts


async def test_pigs_foods_batch_codes_and_coupon_can_actually_be_used(world):
    plugin, context = world
    assert (await invoke(plugin, "全员 命令测试猪 2", "pigs"))[0]
    assert (await invoke(plugin, "全员 命令测试菜1 3", "foods"))[0]
    assert (await invoke(plugin, "target 命令测试猪#MiXeD123", "manual"))[0]
    assert (await invoke(plugin, "target 编号修改卷 1", "coupon"))[0]
    renamed = await plugin.handle_reward_coupon(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(user_id="target", message_id="rename"),
            arguments="编号修改券 猪猪 命令测试猪#MIXED123 NewCode77",
        ),
    )
    assert renamed[0], renamed
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM pig_instances WHERE short_code='NEWCODE77'"))[0] == 1
    row = await plugin.database.fetch_one(
        "SELECT quantity FROM achievement_reward_inventory "
        "WHERE player_id='qq:10001:target' AND reward_id='asset-code-change'"
    )
    assert row[0] == 0
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 5
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 6
    assert (
        await plugin.database.fetch_one(
            "SELECT COUNT(DISTINCT short_code) FROM "
            "(SELECT short_code FROM pig_instances UNION ALL SELECT short_code FROM food_instances)"
        )
    )[0] == 11
    assert (await plugin.database.fetch_one("SELECT SUM(total_catches+total_cooks) FROM player_statistics"))[0] == 0
    for invalid in ("全员 命令测试猪#SameCode 2", "全员 命令测试猪 1000", "target 厨具#FakeCode 1"):
        assert not (await invoke(plugin, invalid, invalid))[0]
    assert "本群 2 人" in context.render.calls[0][0]
    assert "qq:10001:target" not in context.render.calls[0][0]


async def test_full_batch_rollback_and_retry_once_after_restart(world, monkeypatch):
    plugin, context = world
    repo = plugin._administration_service.grant_repository
    real = repo.grant
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        result = await real(*args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("注入第二人库存故障")
        return result

    monkeypatch.setattr(repo, "grant", fail_second)
    assert not (await invoke(plugin, "全员 超级幸运猪哨 4"))[0]
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM item_inventory"))[0] == 0
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM audit_events WHERE action='admin-resource-granted'"))[
        0
    ] == 0
    assert (
        await plugin.database.fetch_one(
            "SELECT COUNT(*) FROM command_receipts WHERE command_name='pig-catcher.admin-grant-resource'"
        )
    )[0] == 0
    monkeypatch.setattr(repo, "grant", real)
    results = await asyncio.gather(invoke(plugin, "全员 超级幸运猪哨 4"), invoke(plugin, "全员 超级幸运猪哨 4"))
    assert all(result[0] for result in results)
    assert len(context.send.images) == 1
    await plugin.on_unload()
    await plugin.on_load()
    assert (await invoke(plugin, "全员 超级幸运猪哨 4"))[0]
    assert len(context.send.images) == 1
    assert not (await invoke(plugin, "全员 超级幸运猪哨 5"))[0]
    assert (await plugin.database.fetch_one("SELECT SUM(quantity) FROM item_inventory"))[0] == 8


async def test_access_unknown_scope_upgrade_cap_and_image_fallback(world):
    plugin, context = world
    denied = await invoke(plugin, "全员 幸运猪哨 5", message=build_message(user_id="outsider"))
    assert not denied[0] and "管理员" in denied[1]
    for target in ("unknown", "qq:10002:target", "qq-official:target"):
        assert not (await invoke(plugin, f"{target} 幸运猪哨 5", target))[0]
    assert (await invoke(plugin, "target 猪饲料 9", "level9"))[0]
    capped = await invoke(plugin, "全员 猪饲料 3", "capped")
    assert capped[0]
    assert (await plugin.database.fetch_one("SELECT level FROM upgrades WHERE player_id='qq:10001:target'"))[0] == 10
    assert "实际发放合计：4级" in capped[1]
    context.render.error = RuntimeError("离线注入渲染失败")
    result = await invoke(plugin, "target 美食自选券 2", "fallback")
    assert result[0] and "美食自选券" in result[1]
    assert context.send.texts[-1][1] == result[1]
    assert (await invoke(plugin, "target 美食自选券 2", "fallback"))[0]
    assert (
        await plugin.database.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory "
            "WHERE player_id='qq:10001:target' AND reward_id='food-choice'"
        )
    )[0] == 2


@pytest.mark.parametrize("platform", ["qq", "qq-official", "qq-official-bot2"])
async def test_structured_target_works_for_both_official_bots_and_does_not_select_all(world, platform):
    plugin, _ = world
    config = plugin.get_default_config()
    config["access"]["admin_user_ids"] = [f"{platform}:admin"]
    plugin.set_plugin_config(config)
    target = replace(
        _identity(user_id="MEMBER_OPENID", display_name="群友"),
        scope=type(_identity(user_id="x", display_name="x").scope)(platform, "10001"),
    )
    await plugin.gameplay_service.profile(target)
    message = _admin_message(message_id="official", target_user_id="MEMBER_OPENID", target_name="群友")
    message["platform"] = platform
    result = await invoke(plugin, "<@MEMBER_OPENID> 战斗猪自选券 2", message=message)
    assert result[0], result
    row = await plugin.database.fetch_one(
        "SELECT quantity FROM achievement_reward_inventory WHERE player_id=? AND reward_id='battle-pig-choice'",
        (target.player_id,),
    )
    assert row[0] == 2
    assert not (await invoke(plugin, "全员 战斗猪自选券 2", message=message))[0]


@pytest.mark.parametrize(
    "text",
    [
        "/猪管发放 全员 幸运猪哨 3",
        "/猪管全员发放 编号修改券 3",
        "/猪管发券 target 编号修改券 3",
        "/猪管全员发道具 超级主厨香料 2",
        "/猪管发猪 target 命令测试猪 A123",
        "/猪管全员发菜 命令测试菜1 x3",
        "<@BOT> /猪管发放 target 美食自选券 1",
    ],
)
def test_each_grant_alias_has_exactly_one_registered_command(text):
    commands = [c for c in create_plugin().get_components() if c["type"] == "COMMAND"]
    matches = [c for c in commands if re.fullmatch(c["metadata"]["command_pattern"], text)]
    assert len(matches) == 1


def test_legacy_number_codes_are_not_reinterpreted_as_quantities():
    assert parse_grant_query("猪 1007", legacy_asset=True).short_code == "1007"
    assert parse_grant_query("测试猪 1007").quantity == 1007
    assert parse_grant_query("猪#AbCd x2", legacy_asset=True).quantity == 2


async def test_six_star_grants_use_only_enabled_authorized_templates(tmp_path):
    db = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("six-pig", rarity=6, group_id="10001", paired_food_template_id="six-food"),
            _food_entry("six-food", effect_id="", effect_params={}, group_id="10001"),
        ],
    )
    admin = _identity(user_id="admin", display_name="管理员")
    service = AdministrationService(db, refresh_hours=(0, 9, 12, 19), timezone_name="Asia/Shanghai")
    try:
        async with db.transaction() as session:
            await FrameworkRepository().touch_identity(
                session, identity=replace(admin, user_id="member"), now="2026-09-06T00:00:00Z"
            )
        for selector in ("six-pig", "six-food"):
            result = await service.grant_resource(
                replace(admin, message_id=selector),
                command_name="pig-catcher.admin-grant-resource",
                selector=selector,
                all_players=True,
                quantity=2,
            )
            assert result.affected_players == 2
        other = replace(admin, scope=type(admin.scope)("qq", "10002"), message_id="wrong-scope")
        with pytest.raises(Exception, match="找不到当前群"):
            await service.grant_resource(other, command_name="grant", selector="six-pig", all_players=True)
        async with db.transaction() as session:
            await session.execute("UPDATE scope_pig_templates SET authorized=0 WHERE template_id='six-pig'")
        with pytest.raises(Exception, match="找不到当前群"):
            await service.grant_resource(
                replace(admin, message_id="revoked"), command_name="grant", selector="six-pig", all_players=True
            )
        assert (await db.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 4
        assert (await db.fetch_one("SELECT COUNT(*) FROM food_instances"))[0] == 4
    finally:
        await db.close()


async def test_asset_generation_failure_rolls_back_the_whole_batch(world, monkeypatch):
    plugin, _ = world
    service = plugin._administration_service
    original = service._insert_granted_asset
    calls = 0

    async def fail_after_write(*args, **kwargs):
        nonlocal calls
        result = await original(*args, **kwargs)
        calls += 1
        if calls == 3:
            raise RuntimeError("第二名玩家生成失败")
        return result

    monkeypatch.setattr(service, "_insert_granted_asset", fail_after_write)
    assert not (await invoke(plugin, "全员 命令测试猪 2"))[0]
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 0
    assert (await plugin.database.fetch_one("SELECT COUNT(*) FROM pig_catalog_entries"))[0] == 0
