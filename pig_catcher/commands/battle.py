"""战斗命令只解析明确格式，不匹配既有领域展开、术式、普通赠送。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.battle_catalog import BattleError, tool_id

BATTLE_HELP = """【PiG Dream! 猪猪对战】
/战斗猪 设置 宿傩猪（或五条猪；同名自动选低价值，收藏猪用全名#编号）
/战斗猪；/战斗猪 强化；/战斗猪 解除保护 名称#编号
/战斗猪 确认（设置、强化、解除保护需2分钟内确认）；/战斗猪 取消
/战斗猪 轮盘 宿傩猪；/战斗猪 轮盘 五条猪
/战斗猪 器具；/战斗猪 制作 练习护腕 2；/战斗猪 器具 练习护腕（或 无）
/比划比划 @群友；/比划比划 接受；/比划比划 拒绝；/比划比划 取消
/出招数 → /出招 → 双方 /会赢的（看完招式后各自确认，两人都确认才结算）
/对战状态；/比划比划 认输（两分钟内 /比划比划 确认认输）
/对战记录 [页码]；/对战记录 B对战号 [回合] [页码]
自然力竭败者接下来5次普通 /抓猪 会自动结算战利品，猪直接归胜者且不占普通额度
/战利品抓猪（保留的手动兼容入口，与普通 /抓猪 使用同一战利品队列）
每人北京时间每天可主动1场、应战1场。接受才扣额度，五分钟不接受取消。
每群同时一场；开战后十分钟无有效行动自动无奖励结束。认输不发战利品。
权重从5起跨回合累计，招式等概率；核心无上限，重伤不清历史风险。
器具一人一个，实际触发才消耗；未触发的终局退回。战利品仅受败者永久加成。
战斗招式与日常群术式互不触发；未使用战利品跨日保留。
简明流程：/抓猪帮助 对战；其他玩法：/抓猪帮助"""


@dataclass(frozen=True, slots=True)
class BattleRequest:
    action: str
    args: dict


def _number(value: str, maximum: int = 100000) -> int:
    if not value.isascii() or not value.isdigit() or len(value) > 6 or not 1 <= int(value) <= maximum:
        raise BattleError(f"请输入1至{maximum}的整数。")
    return int(value)


def parse_battle_request(
    arguments: str, *, section: str = "profile", target_user_id: str = "", target_name: str = ""
) -> BattleRequest:
    value = arguments.strip()
    if len(value) > 300 or any(ord(c) < 32 for c in value):
        raise BattleError("对战参数过长或含控制字符。")
    if value in {"帮助", "help", "?"}:
        return BattleRequest("help", {})
    if section in {"count", "move", "ready", "status", "loot"}:
        if value:
            raise BattleError("该指令不需要额外参数。")
        return BattleRequest(section, {})
    if section == "challenge":
        actions = {
            "": "status",
            "接受": "accept",
            "拒绝": "decline",
            "取消": "cancel_invite",
            "认输": "surrender_preview",
            "确认认输": "surrender_confirm",
        }
        if value in actions:
            return BattleRequest(actions[value], {})
        if not target_user_id:
            raise BattleError("请明确 @ 本群另一位已设置战斗猪的玩家，不能按昵称猜测身份。")
        clean = value
        for marker in sorted(
            {
                f"@{target_user_id}",
                f"@{target_name}",
                f"<@{target_user_id}>",
                f"<@!{target_user_id}>",
                f"[CQ:at,qq={target_user_id}]",
            },
            key=len,
            reverse=True,
        ):
            clean = clean.replace(marker, "", 1)
        if clean.strip() not in {"", "邀请"}:
            raise BattleError("格式：/比划比划 @群友。")
        return BattleRequest("invite", {"target_user_id": target_user_id})
    if section == "history":
        parts = value.split()
        if parts and re.fullmatch(r"B[0-9A-Za-z]{12}", parts[0]):
            if len(parts) > 3:
                raise BattleError("格式：/对战记录 B对战号 [回合] [页码]。")
            return BattleRequest(
                "detail",
                {
                    "battle_id": parts[0].upper(),
                    "round": _number(parts[1]) if len(parts) > 1 else 1,
                    "page": _number(parts[2]) if len(parts) > 2 else 1,
                },
            )
        return BattleRequest("history", {"page": _number(value or "1")})
    head, _, tail = value.partition(" ")
    tail = tail.strip()
    if value in {"", "确认", "取消"}:
        return BattleRequest({"": "profile", "确认": "confirm", "取消": "cancel_setup"}[value], {})
    if head in {"设置", "强化", "解除保护"}:
        if head == "设置" and not tail:
            raise BattleError("格式：/战斗猪 设置 宿傩猪（或五条猪）。")
        return BattleRequest(
            {"设置": "assign_preview", "强化": "upgrade_preview", "解除保护": "retire_preview"}[head],
            {"selector": tail},
        )
    if head == "轮盘":
        if tail not in {"宿傩猪", "五条猪", "sukuna", "gojo", ""}:
            raise BattleError("目前支持宿傩猪和五条猪的战斗盘。")
        return BattleRequest(
            "wheels", {"fighter_id": {"宿傩猪": "sukuna", "五条猪": "gojo"}.get(tail, tail or "sukuna")}
        )
    if head == "器具":
        return (
            BattleRequest("equip", {"tool_id": "" if tail == "无" else tool_id(tail)})
            if tail
            else BattleRequest("tools", {})
        )
    if head == "制作":
        name, _, quantity = tail.partition(" ")
        return BattleRequest("craft", {"tool_id": tool_id(name), "quantity": _number(quantity or "1", 99)})
    raise BattleError("未知对战操作，请输入 /战斗猪 帮助。")
