"""群与用户黑白名单判定。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


def normalized_id_set(values: Iterable[str]) -> frozenset[str]:
    """清理配置中的空白 ID。"""

    return frozenset(str(value).strip() for value in values if str(value).strip())


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """访问控制结果。"""

    allowed: bool
    reason: str = ""


class AccessPolicy:
    """黑名单优先的群和用户访问策略。"""

    def __init__(
        self,
        *,
        group_whitelist: Iterable[str] = (),
        group_blacklist: Iterable[str] = (),
        user_whitelist: Iterable[str] = (),
        user_blacklist: Iterable[str] = (),
        denied_message: str,
    ) -> None:
        self.group_whitelist = normalized_id_set(group_whitelist)
        self.group_blacklist = normalized_id_set(group_blacklist)
        self.user_whitelist = normalized_id_set(user_whitelist)
        self.user_blacklist = normalized_id_set(user_blacklist)
        self.denied_message = str(denied_message or "").strip() or "当前群或账号未启用抓猪插件。"

    def evaluate(self, *, group_id: str, user_id: str) -> AccessDecision:
        """判断一个群成员能否使用插件。"""

        normalized_group_id = str(group_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        if normalized_group_id in self.group_blacklist or normalized_user_id in self.user_blacklist:
            return AccessDecision(False, self.denied_message)
        if self.group_whitelist and normalized_group_id not in self.group_whitelist:
            return AccessDecision(False, self.denied_message)
        if self.user_whitelist and normalized_user_id not in self.user_whitelist:
            return AccessDecision(False, self.denied_message)
        return AccessDecision(True)

    def is_admin(
        self,
        *,
        platform: str,
        user_id: str,
        admin_user_ids: Iterable[str],
    ) -> bool:
        """按当前平台身份判断用户是否位于插件管理员列表。"""

        normalized_user_id = str(user_id or "").strip()
        normalized_platform = str(platform or "").strip().lower()
        configured = normalized_id_set(admin_user_ids)
        return normalized_user_id in configured or (
            bool(normalized_platform)
            and f"{normalized_platform}:{normalized_user_id}" in configured
        )
