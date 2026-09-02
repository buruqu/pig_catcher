"""WebUI moderation and announcement operations."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF

from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.services import (
    AnnouncementAdminService,
    FrameworkService,
    RestrictionAdminService,
)

from .helpers import create_test_plugin


def _identity(user_id: str, display_name: str) -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", "10001"),
        stream_id="stream-10001",
        user_id=user_id,
        display_name=display_name,
        message_id=f"seed-{user_id}",
        group_name="抓猪测试群",
    )


async def _seed_members(database: PigCatcherDatabase) -> None:
    framework = FrameworkService(database)
    await framework.touch_identity(_identity("member-a", "成员甲"))
    await framework.touch_identity(_identity("member-b", "成员乙"))


@pytest.mark.asyncio
async def test_blacklist_admin_adds_and_removes_independent_lists_with_backup(
    tmp_path: Path,
) -> None:
    database = PigCatcherDatabase(tmp_path / "pig_catcher.sqlite3")
    await database.open()
    try:
        await _seed_members(database)
        service = RestrictionAdminService(database)
        added = await service.backup_and_update_social_blacklists(
            data_dir=tmp_path,
            group_id="10001",
            platform="qq",
            user_ids=["qq:member-a", "qq:10001:member-b"],
            gift_action="add",
            trade_action="add",
            reason="pytest 人工复核",
            source="admin-panel-test",
            created_by="maibot-admin-panel",
        )
        assert added.platform_user_ids == ("member-a", "member-b")
        assert added.display_names == ("成员甲", "成员乙")
        assert added.gift_rows_changed == 2
        assert added.trade_rows_changed == 2
        assert added.backup_path.is_file()
        rows = await database.fetch_all(
            """
            SELECT restriction_type, COUNT(*) AS count
            FROM player_restrictions
            GROUP BY restriction_type
            ORDER BY restriction_type
            """
        )
        assert [tuple(row) for row in rows] == [
            ("gift-transfer-ban", 2),
            ("trade-ban", 2),
        ]

        removed = await service.backup_and_update_social_blacklists(
            data_dir=tmp_path,
            group_id="10001",
            platform="qq",
            user_ids=["member-a", "member-b"],
            gift_action="remove",
            trade_action="none",
            reason="pytest 解除赠送限制",
            source="admin-panel-test",
            created_by="maibot-admin-panel",
        )
        assert removed.gift_rows_changed == 2
        assert removed.trade_rows_changed == 0
        remaining = await database.fetch_all(
            "SELECT restriction_type FROM player_restrictions ORDER BY restriction_type"
        )
        assert [str(row["restriction_type"]) for row in remaining] == [
            "trade-ban",
            "trade-ban",
        ]
        audits = await database.fetch_all(
            """
            SELECT action, detail_json
            FROM audit_events
            WHERE action = 'social-blacklists-updated'
            ORDER BY created_at, audit_event_id
            """
        )
        assert len(audits) == 2
        assert json.loads(str(audits[0]["detail_json"]))["gift_action"] == "add"
        assert json.loads(str(audits[1]["detail_json"]))["gift_action"] == "remove"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_blacklist_admin_rejects_unknown_member_before_backup(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig_catcher.sqlite3")
    await database.open()
    try:
        await _seed_members(database)
        with pytest.raises(DomainValidationError, match="尚未在指定群留下插件身份"):
            await RestrictionAdminService(database).backup_and_update_social_blacklists(
                data_dir=tmp_path,
                group_id="10001",
                platform="qq",
                user_ids=["unknown-member"],
                gift_action="add",
                trade_action="none",
                reason="pytest",
                source="admin-panel-test",
                created_by="maibot-admin-panel",
            )
        assert not (tmp_path / "backups").exists()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_announcement_claim_and_result_are_audited(tmp_path: Path) -> None:
    database = PigCatcherDatabase(tmp_path / "pig_catcher.sqlite3")
    await database.open()
    try:
        await _seed_members(database)
        service = AnnouncementAdminService(database)
        claim = await service.claim(
            group_id="10001",
            platform="qq",
            content="测试公告正文",
            source="admin-panel-test",
            created_by="maibot-admin-panel",
        )
        assert claim.stream_id == "stream-10001"
        await service.record_result(claim, success=True)
        rows = await database.fetch_all(
            """
            SELECT action, object_id
            FROM audit_events
            WHERE object_id = ?
            ORDER BY created_at, audit_event_id
            """,
            (claim.announcement_id,),
        )
        assert {str(row["action"]) for row in rows} == {
            "announcement-send-claimed",
            "announcement-send-succeeded",
        }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_plugin_panel_executes_blacklist_and_announcement_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, context = await create_test_plugin(tmp_path)
    assert plugin.gameplay_service is not None
    await plugin.gameplay_service.profile(_identity("member-a", "成员甲"))
    monkeypatch.setattr(plugin, "_clear_administration_triggers", lambda: None)

    config = plugin.get_plugin_config_data()
    config["blacklist_administration"] = {
        "group_id": "10001",
        "platform": "qq",
        "user_ids": ["member-a"],
        "gift_action": "加入黑名单",
        "trade_action": "加入黑名单",
        "reason": "面板测试",
        "execute_blacklist_update": True,
    }
    config["announcement_administration"] = {
        "group_id": "10001",
        "platform": "qq",
        "content": "面板公告测试",
        "execute_send": True,
    }
    plugin.set_plugin_config(config)
    await plugin.on_config_update(
        CONFIG_RELOAD_SCOPE_SELF,
        config,
        "admin-operations",
    )
    assert context.send.texts == [("stream-10001", "面板公告测试")]
    assert plugin.database is not None
    restrictions = await plugin.database.fetch_all(
        "SELECT restriction_type FROM player_restrictions ORDER BY restriction_type"
    )
    assert [str(row["restriction_type"]) for row in restrictions] == [
        "gift-transfer-ban",
        "trade-ban",
    ]
    actions = await plugin.database.fetch_all(
        "SELECT action FROM audit_events ORDER BY created_at, audit_event_id"
    )
    assert {str(row["action"]) for row in actions} >= {
        "social-blacklists-updated",
        "announcement-send-claimed",
        "announcement-send-succeeded",
    }
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_panel_announcement_with_image_is_sent_as_one_hybrid_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, context = await create_test_plugin(tmp_path)
    assert plugin.gameplay_service is not None
    await plugin.gameplay_service.profile(_identity("member-a", "成员甲"))
    monkeypatch.setattr(plugin, "_clear_administration_triggers", lambda: None)
    image_path = tmp_path / "release-banner.jpg"
    image_payload = b"\xff\xd8\xff\xe0pig-catcher-v2\xff\xd9"
    image_path.write_bytes(image_payload)

    config = plugin.get_plugin_config_data()
    config["announcement_administration"] = {
        "group_id": "10001",
        "platform": "qq",
        "content": "2.0 图文公告",
        "image_path": str(image_path),
        "execute_send": True,
    }
    plugin.set_plugin_config(config)
    await plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, config, "hybrid-announcement")

    assert context.send.texts == []
    assert context.send.images == []
    assert len(context.send.hybrids) == 1
    stream_id, segments = context.send.hybrids[0]
    assert stream_id == "stream-10001"
    assert segments[0] == {"type": "text", "content": "2.0 图文公告"}
    assert segments[1]["type"] == "image"
    assert base64.b64decode(segments[1]["binary_data_base64"]) == image_payload
    await plugin.on_unload()


@pytest.mark.asyncio
async def test_failed_panel_announcement_is_audited_and_not_retried_on_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, context = await create_test_plugin(tmp_path)
    assert plugin.gameplay_service is not None
    await plugin.gameplay_service.profile(_identity("member-a", "成员甲"))
    context.send.text_success = False
    monkeypatch.setattr(plugin, "_clear_administration_triggers", lambda: None)

    config = plugin.get_plugin_config_data()
    config["announcement_administration"] = {
        "group_id": "10001",
        "platform": "qq",
        "content": "不会自动重试的公告",
        "execute_send": True,
    }
    plugin.set_plugin_config(config)
    await plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, config, "announcement-failed")
    assert context.send.texts == [("stream-10001", "不会自动重试的公告")]
    assert plugin.database is not None
    failed = await plugin.database.fetch_one(
        "SELECT 1 FROM audit_events WHERE action = 'announcement-send-failed'"
    )
    assert failed is not None

    config["announcement_administration"]["execute_send"] = False
    plugin.set_plugin_config(config)
    await plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, config, "trigger-cleared")
    assert context.send.texts == [("stream-10001", "不会自动重试的公告")]
    await plugin.on_unload()
