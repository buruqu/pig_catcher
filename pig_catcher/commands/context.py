"""从 MaiBot 命令参数中提取稳定群身份。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.errors import CommandContextError
from ..domain.models import CommandIdentity, ScopeKey


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def matched_group(kwargs: Mapping[str, Any], name: str) -> str:
    """读取命令正则命名组并清理空白。"""

    groups = _mapping(kwargs.get("matched_groups"))
    return str(groups.get(name) or "").strip()


def extract_command_identity(stream_id: str, kwargs: Mapping[str, Any]) -> CommandIdentity:
    """只接受可识别平台、群、成员与聊天流的群聊命令。"""

    message = _mapping(kwargs.get("message"))
    if not message:
        raise CommandContextError("无法读取当前消息上下文。")
    message_info = _mapping(message.get("message_info"))
    group_info = _mapping(message_info.get("group_info"))
    user_info = _mapping(message_info.get("user_info"))
    additional = _mapping(message_info.get("additional_config"))

    platform = str(message.get("platform") or additional.get("platform") or "").strip()
    group_id = str(group_info.get("group_id") or additional.get("platform_io_target_group_id") or "").strip()
    user_id = str(user_info.get("user_id") or "").strip()
    group_name = str(group_info.get("group_name") or "").strip()
    display_name = str(user_info.get("user_cardname") or user_info.get("user_nickname") or user_id).strip()
    resolved_stream_id = str(stream_id or message.get("session_id") or message.get("stream_id") or "").strip()
    message_id = str(message.get("message_id") or additional.get("message_id") or "").strip()

    if not group_id:
        raise CommandContextError("抓猪插件只能在群聊中使用。")
    if not platform or not user_id or not resolved_stream_id:
        raise CommandContextError("无法识别当前平台、成员或聊天流。")
    return CommandIdentity(
        scope=ScopeKey(platform=platform, group_id=group_id),
        stream_id=resolved_stream_id,
        user_id=user_id,
        display_name=display_name or user_id,
        message_id=message_id,
        group_name=group_name,
    )
