"""Privileged group-command authorization, ledger, asset, blacklist, and quota tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pig_catcher.domain.models import CommandIdentity, ScopeKey

from .helpers import build_message, create_test_plugin
from .test_plugin import _command_kwargs, _install_test_pig


def _identity(
    *,
    user_id: str,
    display_name: str,
    group_id: str = "10001",
    message_id: str = "seed",
) -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", group_id),
        stream_id=f"stream-{group_id}",
        user_id=user_id,
        display_name=display_name,
        message_id=message_id,
        group_name=f"测试群-{group_id}",
    )


def _admin_message(
    *,
    message_id: str,
    target_user_id: str | None = None,
    target_name: str = "目标玩家",
) -> dict[str, Any]:
    message = build_message(
        user_id="admin",
        display_name="插件管理员",
        message_id=message_id,
    )
    if target_user_id:
        message["raw_message"] = [
            {"type": "text", "data": "/猪管命令 "},
            {
                "type": "at",
                "data": {
                    "target_user_id": target_user_id,
                    "target_user_cardname": target_name,
                },
            },
        ]
    return message


async def _seed_player(plugin: Any, *, user_id: str, display_name: str, group_id: str = "10001") -> None:
    await plugin.gameplay_service.profile(
        _identity(
            user_id=user_id,
            display_name=display_name,
            group_id=group_id,
            message_id=f"seed-{group_id}-{user_id}",
        )
    )


@pytest.mark.asyncio
async def test_admin_coin_commands_allow_negative_are_idempotent_and_group_scoped(
    tmp_path: Path,
) -> None:
    plugin, context = await create_test_plugin(
        tmp_path,
        config_updates={"access": {"admin_user_ids": ["qq:admin"]}},
    )
    await _seed_player(plugin, user_id="target", display_name="目标玩家")
    await _seed_player(plugin, user_id="target", display_name="另群同ID", group_id="20002")

    grant_message = _admin_message(message_id="admin-grant-1", target_user_id="target")
    granted = await plugin.handle_admin_grant_coins(
        stream_id="stream-10001",
        **_command_kwargs(grant_message, arguments="@目标玩家 100"),
    )
    assert granted[0] is True
    duplicate = await plugin.handle_admin_grant_coins(
        stream_id="stream-10001",
        **_command_kwargs(grant_message, arguments="@目标玩家 100"),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)

    deducted = await plugin.handle_admin_deduct_coins(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-deduct-1", target_user_id="target"),
            arguments="@目标玩家 250",
        ),
    )
    assert deducted[0] is True
    row = await plugin.database.fetch_one(
        "SELECT coin_balance FROM players WHERE player_id = 'qq:10001:target'"
    )
    assert row is not None and int(row["coin_balance"]) == -150
    other_scope = await plugin.database.fetch_one(
        "SELECT coin_balance FROM players WHERE player_id = 'qq:20002:target'"
    )
    assert other_scope is not None and int(other_scope["coin_balance"]) == 0
    ledger = await plugin.database.fetch_one(
        """
        SELECT COUNT(*) AS count, SUM(amount) AS total,
               MIN(balance_after) AS minimum_balance
        FROM currency_ledger
        WHERE player_id = 'qq:10001:target'
          AND reason_code = 'admin-coin-adjustment'
        """
    )
    assert ledger is not None
    assert tuple(ledger) == (2, -150, -150)

    all_result = await plugin.handle_admin_grant_coins_all(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-all-grant"),
            amount="20",
        ),
    )
    assert all_result[0] is True
    balances = await plugin.database.fetch_all(
        "SELECT platform_user_id, coin_balance FROM players WHERE scope_id = 'qq:10001' ORDER BY platform_user_id"
    )
    assert [(row["platform_user_id"], row["coin_balance"]) for row in balances] == [
        ("admin", 20),
        ("target", -130),
    ]
    all_deducted = await plugin.handle_admin_deduct_coins_all(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-all-deduct"),
            amount="50",
        ),
    )
    assert all_deducted[0] is True
    balances = await plugin.database.fetch_all(
        "SELECT platform_user_id, coin_balance FROM players WHERE scope_id = 'qq:10001' ORDER BY platform_user_id"
    )
    assert [(row["platform_user_id"], row["coin_balance"]) for row in balances] == [
        ("admin", -30),
        ("target", -180),
    ]
    assert len(context.send.texts) == 4
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_admin_all_player_coin_adjustment_rolls_back_as_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"access": {"admin_user_ids": ["admin"]}},
    )
    await _seed_player(plugin, user_id="target-a", display_name="玩家甲")
    await _seed_player(plugin, user_id="target-b", display_name="玩家乙")
    repository = plugin._administration_service.economy_repository
    original = repository.apply_currency_change
    calls = 0

    async def fail_on_second_player(*args: Any, **kwargs: Any) -> int | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected batch failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(repository, "apply_currency_change", fail_on_second_player)
    result = await plugin.handle_admin_grant_coins_all(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-all-rollback"),
            amount="88",
        ),
    )
    assert result[0] is False
    balances = await plugin.database.fetch_all(
        "SELECT platform_user_id, coin_balance FROM players WHERE scope_id = 'qq:10001' ORDER BY platform_user_id"
    )
    assert [(row["platform_user_id"], row["coin_balance"]) for row in balances] == [
        ("target-a", 0),
        ("target-b", 0),
    ]
    assert await plugin.database.fetch_one(
        "SELECT 1 FROM currency_ledger WHERE reason_code = 'admin-coin-adjustment'"
    ) is None
    assert await plugin.database.fetch_one(
        "SELECT 1 FROM audit_events WHERE action = 'admin-coins-adjusted'"
    ) is None
    assert await plugin.database.fetch_one(
        "SELECT 1 FROM command_receipts WHERE command_name = 'pig-catcher.admin-coins'"
    ) is None
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_admin_asset_grant_manual_or_generated_code_and_history_preserving_removal(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"access": {"admin_user_ids": ["admin"]}},
    )
    await _install_test_pig(plugin, tmp_path, include_food=True)
    await _seed_player(plugin, user_id="target", display_name="目标玩家")

    pig_grant = await plugin.handle_admin_grant_asset(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-pig-grant", target_user_id="target"),
            kind="猪",
            arguments="@目标玩家 命令测试猪 A1B2C3D4",
        ),
    )
    assert pig_grant[0] is True
    food_grant = await plugin.handle_admin_grant_asset(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-food-grant", target_user_id="target"),
            kind="菜",
            arguments="@目标玩家 命令测试菜1",
        ),
    )
    assert food_grant[0] is True

    pig = await plugin.database.fetch_one(
        """
        SELECT pig_instance_id, short_code, state, random_snapshot_json
        FROM pig_instances
        WHERE owner_player_id = 'qq:10001:target'
        """
    )
    food = await plugin.database.fetch_one(
        """
        SELECT food_instance_id, short_code, source_pig_instance_id, state
        FROM food_instances
        WHERE owner_player_id = 'qq:10001:target'
        """
    )
    assert pig is not None and tuple(pig)[1:3] == ("A1B2C3D4", "active")
    assert '"source":"admin-grant"' in str(pig["random_snapshot_json"])
    assert food is not None
    assert len(str(food["short_code"])) == 8
    assert food["source_pig_instance_id"] is None and food["state"] == "active"
    stats = await plugin.database.fetch_one(
        "SELECT total_catches, total_cooks FROM player_statistics WHERE player_id = 'qq:10001:target'"
    )
    assert stats is not None and tuple(stats) == (0, 0)

    removed = await plugin.handle_admin_remove_asset(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-pig-remove", target_user_id="target"),
            kind="猪",
            arguments="@目标玩家 命令测试猪#A1B2C3D4",
        ),
    )
    assert removed[0] is True
    state = await plugin.database.fetch_one(
        "SELECT state, disposed_at FROM pig_instances WHERE pig_instance_id = ?",
        (pig["pig_instance_id"],),
    )
    assert state is not None and state["state"] == "admin-removed"
    assert state["disposed_at"] is not None
    catalog = await plugin.database.fetch_one(
        "SELECT acquired_count FROM pig_catalog_entries WHERE player_id = 'qq:10001:target'"
    )
    assert catalog is not None and catalog["acquired_count"] == 1

    food_removed = await plugin.handle_admin_remove_asset(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="admin-food-remove", target_user_id="target"),
            kind="菜",
            arguments=f"@目标玩家 命令测试菜1#{food['short_code']}",
        ),
    )
    assert food_removed[0] is True
    food_state = await plugin.database.fetch_one(
        "SELECT state, disposed_at FROM food_instances WHERE food_instance_id = ?",
        (food["food_instance_id"],),
    )
    assert food_state is not None and food_state["state"] == "admin-removed"
    assert food_state["disposed_at"] is not None
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_admin_blacklists_are_independent_visible_and_plugin_ban_blocks_commands(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={"access": {"admin_user_ids": ["admin"]}},
    )
    await _seed_player(plugin, user_id="target", display_name="目标玩家")

    for category, message_id in (("插件", "ban-plugin"), ("赠送", "ban-gift"), ("交易", "ban-trade")):
        result = await plugin.handle_admin_blacklist(
            stream_id="stream-10001",
            **_command_kwargs(
                _admin_message(message_id=message_id, target_user_id="target"),
                arguments=f"加入 {category} @目标玩家 pytest",
            ),
        )
        assert result[0] is True
    rows = await plugin.database.fetch_all(
        """
        SELECT restriction_type
        FROM player_restrictions
        WHERE player_id = 'qq:10001:target'
        ORDER BY restriction_type
        """
    )
    assert [row["restriction_type"] for row in rows] == [
        "gift-transfer-ban",
        "plugin-access-ban",
        "trade-ban",
    ]
    listed = await plugin.handle_admin_blacklist(
        stream_id="stream-10001",
        **_command_kwargs(_admin_message(message_id="list-bans"), arguments=""),
    )
    assert listed[0] is True
    assert "插件黑名单（1 人）" in listed[1]
    assert "赠送/收赠黑名单（1 人）" in listed[1]
    assert "交易黑名单（1 人）" in listed[1]

    denied = await plugin.handle_profile(
        stream_id="stream-10001",
        **_command_kwargs(build_message(user_id="target", message_id="blocked-profile")),
    )
    assert denied[0] is False
    denied_help = await plugin.handle_help(
        stream_id="stream-10001",
        **_command_kwargs(build_message(user_id="target", message_id="blocked-help")),
    )
    assert denied_help[0] is False
    removed = await plugin.handle_admin_blacklist(
        stream_id="stream-10001",
        **_command_kwargs(
            _admin_message(message_id="unban-plugin", target_user_id="target"),
            arguments="移除 插件 @目标玩家",
        ),
    )
    assert removed[0] is True
    allowed = await plugin.handle_profile(
        stream_id="stream-10001",
        **_command_kwargs(build_message(user_id="target", message_id="allowed-profile")),
    )
    assert allowed[0] is True
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_admin_resets_only_one_players_current_window_and_rejects_non_admin(
    tmp_path: Path,
) -> None:
    plugin, _ = await create_test_plugin(
        tmp_path,
        config_updates={
            "access": {"admin_user_ids": ["admin"]},
            "catching": {"cooldown_seconds": 0},
        },
    )
    await _install_test_pig(plugin, tmp_path)
    for user_id, display_name in (("target", "目标玩家"), ("other", "其他玩家")):
        for index in range(2):
            caught = await plugin.handle_catch(
                stream_id="stream-10001",
                **_command_kwargs(
                    build_message(
                        user_id=user_id,
                        display_name=display_name,
                        message_id=f"catch-{user_id}-{index}",
                    )
                ),
            )
            assert caught[0] is True

    reset_message = _admin_message(message_id="reset-target", target_user_id="target")
    reset = await plugin.handle_admin_reset_player_quota(
        stream_id="stream-10001",
        **_command_kwargs(reset_message, arguments="@目标玩家"),
    )
    assert reset[0] is True and "已归零：2 次" in reset[1]
    duplicate = await plugin.handle_admin_reset_player_quota(
        stream_id="stream-10001",
        **_command_kwargs(reset_message, arguments="@目标玩家"),
    )
    assert duplicate == (True, "该消息已处理，不重复公示。", 0)
    target_after = await plugin.gameplay_service.catch(
        _identity(
            user_id="target",
            display_name="目标玩家",
            message_id="target-after-reset",
        )
    )
    assert target_after.daily_count == 1
    other_profile = await plugin.gameplay_service.profile(
        _identity(
            user_id="other",
            display_name="其他玩家",
            message_id="other-profile",
        )
    )
    assert other_profile.daily_count == 2

    denied = await plugin.handle_admin_deduct_coins(
        stream_id="stream-10001",
        **_command_kwargs(
            build_message(user_id="ordinary", message_id="unauthorized-admin-command"),
            arguments="target 10",
        ),
    )
    assert denied[0] is False
    assert "只有插件配置中的管理员" in denied[1]
    assert await plugin.database.fetch_one(
        """
        SELECT 1 FROM audit_events
        WHERE actor_user_id = 'ordinary' AND action = 'admin-coins-adjusted'
        """
    ) is None
    await plugin.on_unload()
