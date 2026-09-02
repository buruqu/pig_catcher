"""低冲突巡演命令解析；名称与明确的枚举输入不交给LLM。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..domain.tour_catalog import (
    ENSEMBLES_BY_ID,
    SONGS,
    SONGS_BY_ID,
    THEMES_BY_ID,
    TOOLS_BY_ID,
    VENUES_BY_ID,
    TourError,
    resolve_definition,
)

TOUR_HELP = """【PiG Dream! 猪猪巡演】
/猪猪巡演 自动 Pastel＊Palettes — 同团最佳配队、自动路线，确认后直接完成三站
/猪猪巡演 自动配队 Pastel＊Palettes — 只保存同团最佳阵容，不消耗档期
/组建乐队 乐队名
/乐队编队 1 猪名、猪名、猪名（保存三套，3–5只；编号可精确指定）
/猪猪巡演 确认（所有巡演预览两分钟内确认；取消用 /猪猪巡演 取消）
/我的猪猪乐队；/我的猪猪乐队 阵容 1；/我的猪猪乐队 切换 1
/我的猪猪乐队 改名 新名字；/我的猪猪乐队 简介 内容
/我的猪猪乐队 主题色 粉；/我的猪猪乐队 队徽 星星
/我的猪猪乐队 队长 1；/我的猪猪乐队 中心 1（阵容位置或猪名）
/我的猪猪乐队 角色 1；/我的猪猪乐队 成员 1；/乐队练习 猪名
/我的猪猪乐队 风格 猪名 技术；/我的猪猪乐队 解除保护 猪名
/我的猪猪乐队 解散（先预览，再用巡演专用确认）
/我的猪猪乐队 客串 绿茶猪（或 取消）；/我的猪猪乐队 服装 主题名（或 默认）
/我的猪猪乐队 器材；/我的猪猪乐队 器材 升级
/我的猪猪乐队 器具；/我的猪猪乐队 制作 备用线缆 1
/猪猪巡演 场地；/猪猪巡演 曲库 1；/猪猪巡演 主题；/猪猪巡演 合奏
/猪猪巡演 主题 星星落进练习室（同时载入推荐曲目）
/猪猪巡演 路线 街头舞台、街头舞台、街头舞台
/猪猪巡演 编排 1 星屑起跑线、练习室的下午、把星光带回家
/猪猪巡演 高光 1 1、2；/猪猪巡演 合奏 1 自由合奏；/猪猪巡演 器具 1 备用线缆
/猪猪巡演 排练（免费确定性预估，不发进度）
/猪猪巡演 出发；/巡演继续；/巡演一键
/猪猪巡演 结束（放弃剩余站点，不退档期）
/巡演游记 1；/巡演游记 T开头旅程编号；/巡演游记 收藏 1
/巡演联演 @群友；/巡演联演 接受；/巡演联演 拒绝；/巡演联演 取消（五分钟有效）
每天补一张档期、最多七张；联演双方各一张。三站结束才发整趟粉丝和猪币。
乐队保护不等于占用：空闲时可派遣；销毁/转让前须解除保护并移出阵容。
主题服装展示为巡演卡面的原创舞台装扮，不改变猪猪原立绘。
简明流程：/抓猪帮助 巡演；其他玩法：/抓猪帮助
"""


@dataclass(frozen=True, slots=True)
class TourRequest:
    action: str
    args: dict[str, Any]


def _number(value: str, maximum: int, label: str) -> int:
    if not re.fullmatch(r"[0-9]{1,6}", value) or not 1 <= int(value) <= maximum:
        raise TourError(f"{label}应为1至{maximum}。")
    return int(value)


def _parts(value: str, *, maximum: int = 5) -> list[str]:
    parts = [part.strip() for part in re.split(r"[、,，;；]", value)]
    if not parts or any(not part for part in parts) or len(parts) > maximum:
        raise TourError("请用顿号分隔完整名称，且不要留空位置。")
    return parts


def _text(value: str, maximum: int, label: str) -> str:
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise TourError(f"{label}需要1至{maximum}个字符，不能含换行或控制字符。")
    return value


def parse_tour_request(
    arguments: str, *, section: str = "tour", entry: str = "", target_user_id: str = "", target_name: str = ""
) -> TourRequest:
    value = str(arguments).strip()
    aliases = {"组建乐队": "创建", "乐队编队": "编队", "乐队练习": "练习", "巡演继续": "继续", "巡演一键": "一键"}
    if entry in aliases:
        value = f"{aliases[entry]} {value}".strip()
    if value in {"帮助", "help", "?"}:
        return TourRequest("help", {})
    head, _, tail = value.partition(" ")
    tail = tail.strip()
    if section == "joint":
        if value in {"接受", "拒绝", "取消"}:
            return TourRequest({"接受": "joint_accept", "拒绝": "joint_decline", "取消": "joint_cancel"}[value], {})
        if not value:
            return TourRequest("joint_status", {})
        if not target_user_id:
            raise TourError("邀请需要明确 @ 一位当前群友，不能凭昵称猜测。")
        clean = value
        for marker in sorted(
            {
                f"@{target_name}",
                f"@{target_user_id}",
                f"<@{target_user_id}>",
                f"<@!{target_user_id}>",
                f"[CQ:at,qq={target_user_id}]",
            },
            key=len,
            reverse=True,
        ):
            clean = clean.replace(marker, "", 1)
        if clean.strip() not in {"", "邀请"}:
            raise TourError("格式：/巡演联演 @群友，不要附加其他操作。")
        return TourRequest("joint_invite", {"target_user_id": target_user_id})
    if section == "journal":
        if head == "收藏":
            return TourRequest("collections", {"page": _number(tail or "1", 100000, "页码")})
        if re.fullmatch(r"T[0-9A-Za-z]{10}", value, re.I):
            return TourRequest("detail", {"run_id": value.upper()})
        return TourRequest("journal", {"page": _number(value or "1", 100000, "页码")})
    if value in {"确认", "取消"}:
        return TourRequest("confirm" if value == "确认" else "cancel", {})
    if section == "band":
        if not value:
            return TourRequest("band", {})
        if head in {"创建", "改名", "简介", "主题色", "队徽", "服装"}:
            action = {
                "创建": "create",
                "改名": "rename",
                "简介": "description",
                "主题色": "color",
                "队徽": "emblem",
                "服装": "costume",
            }[head]
            return TourRequest(action, {"value": _text(tail, 100 if head == "简介" else 32, head)})
        if head == "编队":
            slot, _, names = tail.partition(" ")
            return TourRequest(
                "roster",
                {"slot": _number(slot, 3, "阵容编号"), "selectors": [] if names.strip() == "清空" else _parts(names)},
            )
        if head in {"阵容", "切换"}:
            return TourRequest("roster_view" if head == "阵容" else "switch", {"slot": _number(tail, 3, "阵容编号")})
        if head in {"队长", "中心", "练习", "解除保护", "客串"}:
            action = {"队长": "captain", "中心": "center", "练习": "practice", "解除保护": "retire", "客串": "guest"}[
                head
            ]
            return TourRequest(action, {"selector": _text(tail, 100, "猪名或位置")})
        if head == "风格":
            selector, _, branch = tail.rpartition(" ")
            if not selector or not branch:
                raise TourError("格式：/我的猪猪乐队 风格 猪名 技术（或亲近、叙事）。")
            return TourRequest("branch", {"selector": selector, "branch": branch})
        if head in {"角色", "成员"}:
            return TourRequest(
                "characters" if head == "角色" else "members", {"page": _number(tail or "1", 100000, "页码")}
            )
        if value in {"器材", "器材 升级", "器具", "解散"}:
            return TourRequest(
                {"器材": "equipment", "器材 升级": "upgrade", "器具": "tools", "解散": "archive"}[value], {}
            )
        if head == "制作":
            name, _, quantity = tail.partition(" ")
            return TourRequest(
                "craft",
                {
                    "tool_id": resolve_definition(name, TOOLS_BY_ID, label="器具"),
                    "quantity": _number(quantity or "1", 99, "数量"),
                },
            )
    if section == "tour":
        if head in {"自动", "自动巡演", "自动配队", "一键配队"}:
            if not tail:
                raise TourError("请填写喜欢的乐队名，例如：/猪猪巡演 自动 Pastel＊Palettes。")
            return TourRequest(
                "auto_roster" if head in {"自动配队", "一键配队"} else "auto_tour",
                {"theme": resolve_definition(tail, THEMES_BY_ID, label="乐队")},
            )
        if value in {"", "排练", "预览", "出发", "继续", "一键", "结束", "场地", "主题", "合奏"}:
            return TourRequest(
                {
                    "": "tour_overview",
                    "排练": "preview",
                    "预览": "preview",
                    "出发": "start",
                    "继续": "continue",
                    "一键": "all",
                    "结束": "abandon",
                    "场地": "venues",
                    "主题": "themes",
                    "合奏": "ensembles",
                }[value],
                {},
            )
        if head == "曲库":
            return TourRequest("songs", {"page": _number(tail or "1", 100000, "页码")})
        if head == "主题":
            return TourRequest("theme", {"theme": resolve_definition(tail, THEMES_BY_ID, label="主题")})
        if head == "路线":
            routes = _parts(tail, maximum=3)
            if len(routes) != 3:
                raise TourError("一趟巡演恰好三站，请填写三个场地。")
            return TourRequest(
                "route", {"venues": [resolve_definition(item, VENUES_BY_ID, label="场地") for item in routes]}
            )
        if head in {"编排", "高光", "合奏", "器具"}:
            stage, _, content = tail.partition(" ")
            index = _number(stage, 3, "站点") - 1
            if head == "编排":
                ids = []
                for item in _parts(content, maximum=3):
                    ids.append(
                        SONGS[_number(item, len(SONGS), "歌曲编号") - 1].song_id
                        if item.isascii() and item.isdigit()
                        else resolve_definition(item, SONGS_BY_ID, label="歌曲")
                    )
                if len(ids) != 3 or len(set(ids)) != 3:
                    raise TourError("每站需要三首不同歌曲。")
                return TourRequest("setlist", {"stage": index, "songs": ids})
            if head == "高光":
                return TourRequest(
                    "highlights", {"stage": index, "selectors": [] if content == "自动" else _parts(content, maximum=2)}
                )
            if head == "合奏":
                combo = {"自动": "auto", "无": "none"}.get(content)
                return TourRequest(
                    "ensemble",
                    {"stage": index, "ensemble": combo or resolve_definition(content, ENSEMBLES_BY_ID, label="合奏")},
                )
            return TourRequest(
                "tool",
                {
                    "stage": index,
                    "tool": "" if content == "无" else resolve_definition(content, TOOLS_BY_ID, label="器具"),
                },
            )
    raise TourError("没有识别到这个巡演操作，请用 /猪猪巡演 帮助 查看完整格式。")
