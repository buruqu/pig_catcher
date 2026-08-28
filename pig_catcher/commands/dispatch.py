"""严格限定在派遣命令空间，确认不抢占吃菜的 /是 与 /否。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..domain.dispatch import DURATIONS, DispatchError, material_id, region_definition, tool_definition

DISPATCH_HELP = """【猪猪远行社】
/猪猪派遣 — 队伍、到期结算和未读返程
/猪猪派遣 路线 — 五条路线、产物、费用、特长
/猪猪派遣 编队 1 猪名、猪名、猪名 — 1至3只，至少一只1～3星，最多一只高星
/猪猪派遣 编队 1 清空 — 清空空闲队伍
/猪猪派遣 出发 1 回声矿洞 8小时 — 时长可选4/8/12/24小时
/猪猪派遣 出发 1 青草近郊 4小时 区域地图 训练矿石
/猪猪派遣 出发 1 回声矿洞 12小时 纪念相机
/猪猪派遣 出发 1 回声矿洞 24小时 奇遇罗盘 1 — 最后数字为自动选择偏好1/2
/猪猪派遣 出发 1 青草近郊 8小时 整理箱 机关零件 20 训练矿石
/猪猪派遣 确认 或 /猪猪派遣 取消 — 预览后2分钟内确认，仅确认最后一项
/猪猪派遣 召回 1 — 需确认，保留完整4小时块，未满4小时不结算，费用与器具不退
/猪猪派遣 返程 — 查看未读返程（每次最多3趟）；奖励已自动入账，不会过期
/派遣背包 — 材料、零头、器具与纪念品总览
/派遣背包 配方 — 四种器具配方（每趟最多携带一件、出发时消耗）
/派遣背包 制作 区域地图 2 — 一次制作1～99件
/派遣背包 转换 训练矿石 灵巧纤维 2 — 消耗6矿石得到2纤维；四种基础材料3:1转换
/派遣游记 1 — 分页旅行记录；/派遣游记 旅程编号 — 单趟详情
/派遣游记 纪念品 1 — 20枚自然纪念品收藏册
/派遣奇遇 — 待选择奇遇；/派遣奇遇 奇遇编号 1 — 选择候选1/2
同名自动选低价值、未收藏、空闲猪；要带收藏猪，请输入完整“猪名#编号”并确认。
材料和熟练度独立于抓猪概率、次数、道具和菜品；猪猪安全归来。
简明流程：/抓猪帮助 派遣；其他玩法：/抓猪帮助"""


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    action: str
    args: dict[str, Any] = field(default_factory=dict)


def positive_number(text: str, *, maximum: int, label: str) -> int:
    if not re.fullmatch(r"[0-9]{1,7}", text.strip()):
        raise DispatchError(f"{label}必须是正整数。")
    value = int(text)
    if not 1 <= value <= maximum:
        raise DispatchError(f"{label}必须在1至{maximum}之间。")
    return value


def parse_dispatch_request(arguments: str, *, section: str = "dispatch") -> DispatchRequest:
    text = str(arguments or "").strip()
    if len(text) > 500:
        raise DispatchError("派遣指令过长，请查看 /猪猪派遣 帮助。")
    words = text.split()
    if section == "bag":
        if not words:
            return DispatchRequest("bag")
        if words == ["配方"]:
            return DispatchRequest("recipes")
        if words[0] == "制作" and len(words) in (2, 3):
            return DispatchRequest(
                "craft",
                {
                    "tool_id": tool_definition(words[1]).tool_id,
                    "quantity": positive_number(words[2] if len(words) == 3 else "1", maximum=99, label="制作数量"),
                },
            )
        if words[0] == "转换" and len(words) == 4:
            source, target = material_id(words[1], basic_only=True), material_id(words[2], basic_only=True)
            if source == target:
                raise DispatchError("转换前后的材料不能相同。")
            return DispatchRequest(
                "convert",
                {
                    "source": source,
                    "target": target,
                    "quantity": positive_number(words[3], maximum=100_000, label="得到数量"),
                },
            )
    elif section == "journal":
        if not words or (len(words) == 1 and words[0].isdigit()):
            return DispatchRequest(
                "journal", {"page": positive_number(words[0] if words else "1", maximum=1_000_000, label="页码")}
            )
        if words[0] == "纪念品" and len(words) in (1, 2):
            return DispatchRequest(
                "souvenirs", {"page": positive_number(words[1] if len(words) == 2 else "1", maximum=3, label="页码")}
            )
        if len(words) == 1 and re.fullmatch(r"D[A-Z0-9]{10}", words[0], re.IGNORECASE):
            return DispatchRequest("detail", {"trip_id": words[0].upper()})
    elif section == "encounters":
        if not words:
            return DispatchRequest("choices")
        if len(words) == 2 and re.fullmatch(r"D[A-Z0-9]{10}-[1-6]", words[0], re.IGNORECASE):
            return DispatchRequest(
                "choose",
                {"choice_id": words[0].upper(), "selected": positive_number(words[1], maximum=2, label="候选")},
            )
    elif section == "dispatch":
        simple = {
            "": "overview",
            "总览": "overview",
            "帮助": "help",
            "路线": "routes",
            "确认": "confirm",
            "取消": "cancel",
            "返程": "returns",
        }
        if text in simple:
            return DispatchRequest(simple[text])
        if len(words) >= 3 and words[0] == "编队":
            slot = positive_number(words[1], maximum=3, label="队伍编号")
            selectors = re.split(r"[、，,]", text.split(None, 2)[2])
            selectors = [item.strip() for item in selectors]
            if selectors == ["清空"]:
                selectors = []
            elif not 1 <= len(selectors) <= 3 or not all(selectors):
                raise DispatchError("请用顿号分隔1至3只猪的名称，例如：/猪猪派遣 编队 1 苯猪、野猪。")
            return DispatchRequest("team", {"slot": slot, "selectors": selectors})
        if len(words) == 2 and words[0] == "召回":
            return DispatchRequest("recall", {"slot": positive_number(words[1], maximum=3, label="队伍编号")})
        if len(words) >= 4 and words[0] == "出发":
            slot = positive_number(words[1], maximum=3, label="队伍编号")
            hours_text = re.sub(r"(?:小时|h)$", "", words[3], flags=re.IGNORECASE)
            hours = positive_number(hours_text, maximum=24, label="旅行小时")
            if hours not in DURATIONS:
                raise DispatchError("旅行时长只能为4、8、12或24小时。")
            region = region_definition(words[2])
            options: dict[str, Any] = {}
            tool_id = ""
            extra = words[4:]
            if extra:
                tool_id = tool_definition(extra[0]).tool_id
                if tool_id == "region-map" and len(extra) == 2:
                    options = {"target": material_id(extra[1], basic_only=True)}
                elif tool_id == "souvenir-camera" and len(extra) == 1:
                    pass
                elif tool_id == "encounter-compass" and len(extra) in (1, 2):
                    options = {
                        "preference": positive_number(
                            extra[1] if len(extra) == 2 else "1", maximum=2, label="自动选择偏好"
                        )
                    }
                elif tool_id == "sorting-box" and len(extra) == 4 and re.fullmatch(r"[0-9]{1,7}", extra[2]):
                    source, target = material_id(extra[1], basic_only=True), material_id(extra[3], basic_only=True)
                    if source == target:
                        raise DispatchError("整理箱的来源和目标材料不能相同。")
                    options = {"source": source, "keep": int(extra[2]), "target": target}
                else:
                    raise DispatchError("器具参数不正确，请查看 /猪猪派遣 帮助 中的出发示例。")
            return DispatchRequest(
                "start",
                {
                    "slot": slot,
                    "region_id": region.region_id,
                    "hours": hours,
                    "tool_id": tool_id,
                    "tool_options": options,
                },
            )
    raise DispatchError("未识别此派遣指令，请输入 /猪猪派遣 帮助 查看可复制示例。")
