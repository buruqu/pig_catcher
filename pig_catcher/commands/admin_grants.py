"""管理员统一发放解析；保留旧发猪/发菜的裸编号语法。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.admin_grants import validate_grant_quantity
from ..domain.errors import DomainValidationError
from ..domain.short_codes import normalize_short_code
from .parsers import parse_admin_asset_grant, parse_admin_target_arguments


@dataclass(frozen=True, slots=True)
class GrantTarget:
    user_id: str
    all_players: bool
    remaining: str


@dataclass(frozen=True, slots=True)
class GrantQuery:
    selector: str
    quantity: int = 1
    kind: str = ""
    short_code: str | None = None


def parse_grant_target(arguments: str, *, all_players: bool = False, mention=None) -> GrantTarget:
    words = str(arguments or "").strip().split(None, 1)
    explicit_all = bool(words and words[0] in {"全员", "全体", "所有人"})
    if all_players or explicit_all:
        if mention is not None:
            raise DomainValidationError("全员发放不能同时指定个人@，请只保留一种目标。")
        remaining = (words[1] if len(words) == 2 else "") if explicit_all else str(arguments).strip()
        return GrantTarget("", True, remaining)
    if mention is not None:
        # 官方平台可能保留原始<@openid>；只删除已由结构化消息验证过的目标标记。
        target_id = re.escape(mention.user_id)
        arguments = re.sub(rf"<@!?{target_id}>|\[CQ:at,qq={target_id}\]", " ", arguments)
    target = parse_admin_target_arguments(
        arguments,
        mentioned_user_id=mention.user_id if mention else "",
        mentioned_display_name=mention.display_name if mention else "",
    )
    return GrantTarget(target.user_id, False, target.remaining)


def parse_grant_query(arguments: str, *, kind: str = "", legacy_asset: bool = False) -> GrantQuery:
    text = str(arguments or "").strip()
    kinds = {
        "猪": "猪猪",
        "猪猪": "猪猪",
        "菜": "美食",
        "美食": "美食",
        "道具": "道具",
        "券": "券",
        "商城道具": "商城道具",
        "材料": "材料",
        "升级": "升级",
    }
    kind = kinds.get(kind, kind)
    words = text.split(None, 1)
    if not kind and not legacy_asset and len(words) == 2 and words[0] in kinds:
        kind, text = kinds[words[0]], words[1]
    quantity = 1
    # 老指令末尾的四位以上数字可能是手动编号，只有 x数量 明确表示批量。
    suffix = re.search(r"\s+[xX×]([0-9]+)$", text)
    if suffix is None and not legacy_asset:
        suffix = re.search(r"\s+([+-]?[0-9]+)$", text)
    if suffix is not None:
        quantity = validate_grant_quantity(int(suffix[1]))
        text = text[: suffix.start()].strip()
    if legacy_asset:
        old = parse_admin_asset_grant(text)
        return GrantQuery(old.template_selector, quantity, kind, old.short_code)
    code = None
    if "#" in text:
        text, code = text.rsplit("#", 1)
        code = normalize_short_code(code)
    if not text.strip():
        raise DomainValidationError("请填写发放名称，例如：/猪管发放 全员 编号修改券 3。")
    return GrantQuery(text.strip(), quantity, kind, code)
