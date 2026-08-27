"""从 MaiBot 命令参数中提取稳定群身份。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.errors import CommandContextError, MentionTargetError
from ..domain.models import CommandIdentity, ScopeKey


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def matched_group(kwargs: Mapping[str, Any], name: str) -> str:
    """读取命令正则命名组并清理空白。"""

    groups = _mapping(kwargs.get("matched_groups"))
    return str(groups.get(name) or "").strip()


@dataclass(frozen=True, slots=True)
class MentionTarget:
    """One explicit group-member mention carried by the command message."""

    user_id: str
    display_name: str


def _mention_segments(value: object) -> list[Mapping[str, Any]]:
    segments: list[Mapping[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            segments.extend(_mention_segments(item))
        return segments
    if not isinstance(value, Mapping):
        return segments
    if str(value.get("type") or "").strip().lower() == "at":
        segments.append(value)
    for key in ("components", "segments", "items"):
        child = value.get(key)
        if isinstance(child, list):
            segments.extend(_mention_segments(child))
    return segments


def extract_mention_target(kwargs: Mapping[str, Any], *, arguments: str | None = None) -> MentionTarget:
    """Require exactly one structured @ target instead of trusting nickname text."""

    message = _mapping(kwargs.get("message"))
    raw_message = (
        message.get("raw_message")
        or kwargs.get("raw_message")
        or message
    )
    mentions: dict[str, MentionTarget] = {}
    for segment in _mention_segments(raw_message):
        data = segment.get("data")
        if isinstance(data, Mapping):
            user_id = str(data.get("target_user_id") or data.get("qq") or "").strip()
            display_name = str(
                data.get("target_user_cardname")
                or data.get("target_user_nickname")
                or user_id
            ).strip()
        else:
            user_id = str(
                segment.get("target_user_id")
                or segment.get("qq")
                or data
                or ""
            ).strip()
            display_name = str(
                segment.get("target_user_cardname")
                or segment.get("target_user_nickname")
                or user_id
            ).strip()
        if user_id:
            mentions[user_id] = MentionTarget(
                user_id=user_id,
                display_name=(display_name or user_id)[:128],
            )
    if arguments is not None:
        # 官方群消息常同时 @机器人 和 @群友。只保留明确出现在命令参数内的结构化目标，
        # 不把触发机器人的前导 @ 算作邀请对象，也绝不只凭自由文本昵称定位玩家。
        def in_arguments(target: MentionTarget) -> bool:
            encoded = (f"<@{target.user_id}>", f"<@!{target.user_id}>", f"[CQ:at,qq={target.user_id}]")
            if any(marker in arguments for marker in encoded):
                return True
            return any(re.search(r"(?<!\S)@" + re.escape(name) + r"(?=$|\s)", arguments)
                       for name in {target.user_id, target.display_name} if name)

        mentions = {key: target for key, target in mentions.items() if in_arguments(target)}
    if not mentions:
        raise MentionTargetError("请在命令中明确 @ 一位当前群成员。")
    if len(mentions) != 1:
        raise MentionTargetError("一次只能选择一位接收群友。")
    return next(iter(mentions.values()))


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
