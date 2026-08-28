"""派遣只读投影与图片视图；结算由服务先在同一事务完成。"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.dispatch import (
    MATERIAL_SCALE,
    MATERIALS,
    REGIONS,
    REGIONS_BY_ID,
    TOOLS,
    TOOLS_BY_ID,
    DispatchError,
    block_yield,
    proficiency,
    safe_display_name,
    souvenir_id,
    team_slots,
)
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchPigCard, DispatchView
from ..domain.models import CommandIdentity
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.dispatch import DispatchRepository

_CST = timezone(timedelta(hours=8))
_STATUS = {"traveling": "旅行中", "completed": "平安归来", "recalled": "已召回"}
_BONUS_NAMES = {
    "low_star_ppm": "全低星队",
    "tags_ppm": "区域特长",
    "attribute_ppm": "相对体型/重量",
    "proficiency_ppm": "旅行熟练度",
}


def local_time(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, _CST).strftime("%m-%d %H:%M")


def units_text(units: int) -> str:
    whole, remainder = divmod(abs(units), MATERIAL_SCALE)
    tail = f".{remainder:07d}".rstrip("0") if remainder else ""
    return ("-" if units < 0 else "") + f"{whole}{tail}"


def pig_card(member: dict[str, Any], *, team_slot: int | None = None) -> DispatchPigCard:
    prefix = f"第{team_slot}队 · " if team_slot is not None else ""
    return DispatchPigCard(
        name=member["name"],
        short_code=member["short_code"],
        rarity=member["rarity"],
        image_relpath=member["image_relpath"],
        tags=tuple(dict.fromkeys((*member["tags"], *member.get("display_tags", ())[:2]))),
        summary=f"{prefix}熟练度 Lv.{member['proficiency']} · 累计远行 {member['hours']}h",
        favorite=member["favorite"],
        template_id=member["template_id"],
    )


def reward_lines(rewards: list[dict[str, Any]]) -> tuple[Line, ...]:
    materials: dict[str, int] = {}
    extras = []
    for reward in rewards:
        if "material_id" in reward:
            key = reward["material_id"]
            materials[key] = materials.get(key, 0) + reward["delta_units"]
        else:
            extras.append(Line(reward["name"], "+1", "纪念品" if "souvenir_id" in reward else "器具成品"))
    return tuple(
        Line(MATERIALS[key], ("+" if value >= 0 else "") + units_text(value), "含累计零头")
        for key, value in materials.items()
        if value
    ) + tuple(extras)


def option_text(option: dict[str, Any]) -> str:
    kind = option["kind"]
    if kind in ("materials", "notes"):
        return f"{MATERIALS[option['material_id']]} ×{option['quantity']}"
    if kind == "souvenir":
        return f"纪念品·{option['name']}（重复时转2补给）"
    if kind == "recipe":
        return f"配方附赠成品·{option['name']} ×1"
    return "一段属于这支队伍的旅行见闻"


class DispatchQueries:
    def __init__(self, repository: DispatchRepository) -> None:
        self.repo = repository

    @staticmethod
    def view(identity: CommandIdentity, title: str, **kwargs: Any) -> DispatchView:
        return DispatchView(
            title=title, player_name=safe_display_name(identity.display_name, identity.user_id), **kwargs
        )

    async def overview(self, session: DatabaseSession, identity: CommandIdentity, now_ms: int) -> DispatchView:
        profile = await self.repo.profile(session, identity.player_id)
        slots = team_slots(profile["effective_seconds"])
        active = {trip["slot"]: trip for trip in await self.repo.trips(session, identity.player_id, active=True)}
        teams = {
            row["slot"]: json.loads(row["member_ids_json"])
            for row in await session.fetch_all(
                "SELECT * FROM dispatch_teams WHERE player_id=? ORDER BY slot",
                (identity.player_id,),
            )
        }
        panels, cards = [], []
        for slot in range(1, slots + 1):
            trip = active.get(slot)
            if trip:
                snapshot = json.loads(trip["snapshot_json"])
                cards.extend(pig_card(member, team_slot=slot) for member in snapshot["members"])
                remaining = max(1, math.ceil((trip["ends_ms"] - now_ms) / 60_000))
                panels.append(
                    Panel(
                        f"第{slot}队 · {REGIONS_BY_ID[snapshot['region_id']].name}",
                        (
                            Line("队员", "、".join(member["name"] for member in snapshot["members"])),
                            Line(
                                "预计归来",
                                local_time(trip["ends_ms"]),
                                f"还需{remaining}分钟 · 已完成{trip['processed_blocks']}个4小时块",
                            ),
                        ),
                        f"旅行中 · 旅程 {trip['trip_id']}",
                    )
                )
            else:
                names = []
                for pig_id in teams.get(slot, []):
                    try:
                        member = await self.repo.member(session, identity.player_id, pig_id)
                        names.append(member["name"] + ("（其他队占用）" if member["busy_purpose"] else ""))
                    except DispatchError:
                        names.append("原队员已不在背包，请重新编队")
                panels.append(
                    Panel(
                        f"第{slot}队 · 待命",
                        (Line("队员", "、".join(names) or "尚未编队"),),
                        f"/猪猪派遣 {'出发' if names else '编队'} {slot} …",
                    )
                )
        unread = await session.fetch_one(
            "SELECT COUNT(*) FROM dispatch_trips WHERE player_id=? AND status!='traveling' AND viewed=0",
            (identity.player_id,),
        )
        pending = await session.fetch_one(
            "SELECT COUNT(*) FROM dispatch_choices WHERE player_id=? AND selected IS NULL",
            (identity.player_id,),
        )
        hours = profile["effective_seconds"] / 3600
        next_unlock = "队伍已全部解锁" if slots == 3 else f"累计{'12' if slots == 1 else '72'}小时解锁下一队"
        return self.view(
            identity,
            "猪猪远行社",
            stats=(
                Line("可用队伍", f"{slots}/3", next_unlock),
                Line("有效远行", f"{hours:.2f}h", "并行队伍不重复累计时间"),
                Line("返程 / 待选奇遇", f"{unread[0]} / {pending[0]}", "奖励已入账，未读记录不失效"),
            ),
            pigs=tuple(cards),
            panels=tuple(panels),
            hints=(
                "/猪猪派遣 路线　/猪猪派遣 返程　/派遣背包　/派遣奇遇",
                "/猪猪派遣 帮助 查看完整可复制命令；所有时间为北京时间。",
            ),
        )

    def routes(self, identity: CommandIdentity) -> DispatchView:
        panels = tuple(
            Panel(
                region.name,
                (
                    Line("每4小时满编基础产出", f"6 {MATERIALS[region.material]} + 2 旅行补给"),
                    Line("出发费用", f"{region.fee} 猪币 / 4h", "确认时一次扣除；召回不退费"),
                    Line("区域特长", " / ".join(region.tags)),
                    Line(
                        "属性偏好",
                        ("重量较高" if region.attribute == "weight" else "体型较小") + "的本品种个体",
                        "比较各自品种区间，不比绝对体重或售价",
                    ),
                ),
            )
            for region in REGIONS
        )
        return self.view(
            identity,
            "五条路线 · 总有一处值得出发",
            panels=panels,
            banner="4 / 8 / 12 / 24小时｜1 / 2 / 3只队员对应40% / 70% / 100%产出",
            hints=(
                "全低星队+10%；命中特长最多+10%；属性最多+5%；熟练度最多+5%，加法合计上限30%。",
                "同玩家同区域共享奇遇积累：每完整探索单位10%概率，连续9次未遇则第10次必遇。",
                "/猪猪派遣 出发 1 青草近郊 4小时",
            ),
        )

    def team_preview(self, identity: CommandIdentity, slot: int, members: list[dict[str, Any]]) -> DispatchView:
        return self.view(
            identity,
            f"编队预览 · 第{slot}队",
            pigs=tuple(pig_card(m) for m in members),
            banner="将清空这支空闲队伍" if not members else "仅保存队伍，不扣猪币、不占用猪猪。",
            panels=(
                Panel(
                    "安全检查",
                    (
                        Line(
                            "队伍构成",
                            f"{len(members)}只",
                            "至少一只低星，最多一只高星" if members else "清空后可重新编队",
                        ),
                        Line("收藏保护", "保留收藏标记", "明确编号点名的收藏猪允许旅行；不会消耗猪猪"),
                    ),
                ),
            ),
            hints=("2分钟内 /猪猪派遣 确认；/猪猪派遣 取消 放弃。",),
        )

    def start_preview(
        self,
        identity: CommandIdentity,
        snapshot: dict[str, Any],
        now_ms: int,
        *,
        started: bool = False,
        trip_id: str = "",
        auto_choices: int = 0,
    ) -> DispatchView:
        region = REGIONS_BY_ID[snapshot["region_id"]]
        bonus = snapshot["bonus"]
        primary, supply = block_yield(len(snapshot["members"]), sum(bonus.values()))
        blocks = snapshot["hours"] // 4
        rows = [
            Line("主产物", f"{units_text(primary * blocks)} {MATERIALS[region.material]}"),
            Line("通用补给", f"{units_text(supply * blocks)} 旅行补给", "地图只替换此部分，最多4份"),
            Line("猪币费用", str(snapshot["fee"]), "已扣除" if started else "确认后扣除"),
            Line(
                "器具",
                TOOLS_BY_ID[snapshot["tool_id"]].name if snapshot["tool_id"] else "无",
                "出发时消耗1件，召回不退" if snapshot["tool_id"] else "每次最多携带1件",
            ),
        ]
        if snapshot["tool_id"]:
            options = snapshot["tool_options"]
            text = TOOLS_BY_ID[snapshot["tool_id"]].summary
            if "target" in options:
                text += f" 目标：{MATERIALS[options['target']]}。"
            if "source" in options:
                text += f" 来源：{MATERIALS[options['source']]}，保留{options['keep']}份。"
            if "preference" in options:
                text += f" 默认选择{options['preference']}。"
            rows.append(Line("器具设置", text))
        from ..infrastructure.repositories.achievement_coupons import AchievementCouponRepository

        rows.append(
            Line(
                "成就券",
                AchievementCouponRepository.description(snapshot.get("coupons", {})),
                "路费/行李占同一数值槽；惊喜券独立。免费路线不消耗路费券，提前召回不返已用券。",
            )
        )
        for usage in snapshot.get("coupon_uses", []):
            rows.append(
                Line(usage["name"], f"剩余{usage['remaining']}张", f"本次旅费减免{usage.get('coin_saving', 0)}猪币")
            )
        panels = (
            Panel("出发快照 · 基础预期，不含奇遇与器具转换", tuple(rows), "零头会保存到材料账户，不会逐块向上取整。"),
            Panel("产出加成", tuple(Line(_BONUS_NAMES[key], f"+{value / 10_000:g}%") for key, value in bonus.items())),
        )
        hints = (
            (f"/派遣游记 {trip_id} 查看旅行；/猪猪派遣 召回 {snapshot['slot']} 可提前返回。",)
            if started
            else (
                "2分钟内 /猪猪派遣 确认 后出发；/猪猪派遣 取消 放弃。",
                "确认时再次校验所有权、收藏、交易锁、占用、余额和器具库存。",
            )
        )
        if auto_choices:
            hints += (f"已按预设处理{auto_choices}条上次遗留奇遇，可在 /派遣游记 查看。",)
        return self.view(
            identity,
            f"{'出发啦' if started else '出发预览'} · 第{snapshot['slot']}队",
            panels=panels,
            pigs=tuple(pig_card(m) for m in snapshot["members"]),
            stats=(
                Line("目的地", region.name),
                Line("旅行时长", f"{snapshot['hours']}h"),
                Line("预计归来", local_time(now_ms + snapshot["hours"] * 3600_000)),
            ),
            banner="猪猪已进入旅行保护，不占抓猪额度，不触发或消耗抓猪/做菜效果。"
            if started
            else "不会消耗猪猪；确认后旅行期间无法做菜、售卖、赠送、交易或参加其他活动。",
            hints=hints,
        )

    async def bag(self, session: DatabaseSession, identity: CommandIdentity) -> DispatchView:
        balances = {
            row["material_id"]: dict(row)
            for row in await session.fetch_all(
                "SELECT * FROM material_balances WHERE player_id=?",
                (identity.player_id,),
            )
        }
        tools = {
            row["tool_id"]: row["quantity"]
            for row in await session.fetch_all(
                "SELECT * FROM dispatch_tools WHERE player_id=?",
                (identity.player_id,),
            )
        }
        count = await session.fetch_one(
            "SELECT COUNT(*) FROM dispatch_souvenirs WHERE player_id=?", (identity.player_id,)
        )
        rows = tuple(
            Line(
                name,
                str(balances.get(key, {}).get("quantity", 0)),
                "累计零头 " + units_text(balances.get(key, {}).get("remainder_units", 0)),
            )
            for key, name in MATERIALS.items()
        )
        return self.view(
            identity,
            "派遣背包",
            panels=(
                Panel("材料库存 · 整数部分可使用", rows),
                Panel(
                    "旅行器具",
                    tuple(Line(tool.name, f"{tools.get(tool.tool_id, 0)}件", tool.summary) for tool in TOOLS),
                ),
            ),
            stats=(Line("自然纪念品", f"{count[0]}/20", "重复纪念品自动变成2补给"),),
            hints=(
                "/派遣背包 配方　/派遣游记 纪念品",
                "四种基础材料可以3:1转换；补给、手记不可作为转换原料或目标。材料不可售卖、赠送或交易。",
            ),
        )

    def recipes(self, identity: CommandIdentity) -> DispatchView:
        return self.view(
            identity,
            "工匠小铺 · 旅行器具配方",
            panels=tuple(
                Panel(
                    tool.name,
                    (
                        Line("制作材料", " + ".join(f"{MATERIALS[key]} ×{quantity}" for key, quantity in tool.costs)),
                        Line("效果", tool.summary),
                        Line("制作指令", f"/派遣背包 制作 {tool.name} 1"),
                    ),
                    "一次性器具，每趟只能带1件",
                )
                for tool in TOOLS
            ),
            hints=(
                "所有四张旅行配方现已可用；奇遇中发现的配方附送一件成品。",
                "派遣材料可用于战斗猪强化与巡演养成；分别在 /战斗猪 帮助 和 /猪猪巡演 帮助 查看。",
            ),
        )

    async def journal(self, session: DatabaseSession, identity: CommandIdentity, page: int) -> DispatchView:
        count = await session.fetch_one("SELECT COUNT(*) FROM dispatch_trips WHERE player_id=?", (identity.player_id,))
        pages = max(1, math.ceil(count[0] / 6))
        if not 1 <= page <= pages:
            raise DispatchError(f"游记只有{pages}页。")
        rows = await session.fetch_all(
            "SELECT * FROM dispatch_trips WHERE player_id=? ORDER BY sequence DESC LIMIT 6 OFFSET ?",
            (identity.player_id, (page - 1) * 6),
        )
        panels = []
        for row in rows:
            snapshot, state = json.loads(row["snapshot_json"]), json.loads(row["progress_json"])
            panels.append(
                Panel(
                    f"{REGIONS_BY_ID[snapshot['region_id']].name} · {_STATUS[row['status']]}",
                    (
                        Line(
                            "旅程",
                            row["trip_id"],
                            f"第{row['slot']}队 · {local_time(row['starts_ms'])} 出发 · 计划{snapshot['hours']}h",
                        ),
                        Line("队员", "、".join(m["name"] for m in snapshot["members"])),
                        Line(
                            "旅行收获",
                            f"已完成{row['processed_blocks']}块 · 奇遇{len(state['events'])}次",
                            "正常完成" if row["status"] == "completed" else "仅完整4小时块有效",
                        ),
                    ),
                )
            )
        return self.view(
            identity,
            "猪猪游记",
            panels=tuple(panels),
            page=page,
            page_count=pages,
            banner="每趟旅行都有独立记录；重复查看不会重复发奖。" if rows else "尚未出发，第一本游记正在等你。",
            hints=("/派遣游记 页码　/派遣游记 旅程编号　/派遣游记 纪念品",),
        )

    async def trip_detail(
        self, session: DatabaseSession, identity: CommandIdentity, trip: dict[str, Any]
    ) -> DispatchView:
        snapshot, state = json.loads(trip["snapshot_json"]), json.loads(trip["progress_json"])
        choices = {
            row["choice_id"]: row["selected"]
            for row in await session.fetch_all(
                "SELECT choice_id,selected FROM dispatch_choices WHERE trip_id=? AND player_id=?",
                (trip["trip_id"], identity.player_id),
            )
        }
        panels = [
            Panel(
                "本次收获",
                reward_lines(state.get("rewards", [])),
                "材料已入账，未选奇遇另行结算；展示的是本趟变化，非当前背包余额。"
                if trip["status"] != "traveling"
                else "返程或召回时统一入账。已完成块的随机结果固定，不因查看而改变。",
            )
        ]
        if state.get("achievement_story"):
            story = state["achievement_story"]
            panels.append(
                Panel(story["title"], (Line(story["region"], story["text"], "原创旅行纪念 · 不增加奇遇或自然纪念品"),))
            )
        if snapshot.get("coupon_uses"):
            panels.append(
                Panel(
                    "本趟成就券",
                    tuple(Line(u["name"], f"使用后剩余{u['remaining']}张") for u in snapshot["coupon_uses"]),
                )
            )
        for event in state["events"]:
            rows = []
            for index, option in enumerate(event["options"], 1):
                selected = choices.get(event.get("choice_id", ""))
                suffix = "（已选）" if selected == index else ("（未选）" if selected is not None else "")
                rows.append(
                    Line(
                        f"候选{index}{suffix}" if len(event["options"]) == 2 else "奇遇收获",
                        option_text(option),
                        option["story"],
                    )
                )
            hint = "第10次探索保底命中" if event["forced"] else "探索偶遇"
            if event.get("choice_id") and choices.get(event["choice_id"]) is None:
                hint += f" · /派遣奇遇 {event['choice_id']} 1 或 2"
            panels.append(Panel(f"第{event['block']}块 · {local_time(event['at_ms'])}", tuple(rows), hint))
        displayed_members = []
        for member in snapshot["members"]:
            if trip["status"] == "traveling":
                displayed_members.append(member)
            else:
                hours = member["hours"] + state["settled_hours"]
                displayed_members.append({**member, "hours": hours, "proficiency": proficiency(hours)})
        return self.view(
            identity,
            f"{REGIONS_BY_ID[snapshot['region_id']].name} · {_STATUS[trip['status']]}",
            subtitle=f"猪猪游记 · {trip['trip_id']}",
            panels=tuple(panels),
            pigs=tuple(pig_card(m) for m in displayed_members),
            stats=(
                Line("出发", local_time(trip["starts_ms"])),
                Line("返程", local_time(trip["settled_ms"] or trip["ends_ms"])),
                Line("有效旅行", f"{trip['processed_blocks'] * 4}h", f"计划{snapshot['hours']}h"),
            ),
            hints=("/派遣背包 查看当前余额；/派遣游记 返回列表。",),
        )

    async def souvenirs(self, session: DatabaseSession, identity: CommandIdentity, page: int) -> DispatchView:
        if not 1 <= page <= 3:
            raise DispatchError("纪念品册共有3页。")
        owned = {
            row[0]
            for row in await session.fetch_all(
                "SELECT souvenir_id FROM dispatch_souvenirs WHERE player_id=?",
                (identity.player_id,),
            )
        }
        entries = [(region, i, name) for region in REGIONS for i, name in enumerate(region.souvenirs)]
        rows = tuple(
            Line(name, "已收藏" if souvenir_id(region.region_id, i) in owned else "未发现", region.name)
            for region, i, name in entries[(page - 1) * 8 : page * 8]
        )
        return self.view(
            identity,
            "把风景带回家",
            panels=(Panel("20枚自然纪念品", rows),),
            page=page,
            page_count=3,
            banner=f"已收藏{len(owned)}/20 · 照相机只在纪念品分支优先未拥有，不增加奇遇概率。",
            hints=("/派遣游记 纪念品 页码；重复自然纪念品会自动兑换2份旅行补给。",),
        )

    async def choices(self, session: DatabaseSession, identity: CommandIdentity) -> DispatchView:
        rows = await session.fetch_all(
            "SELECT * FROM dispatch_choices WHERE player_id=? AND selected IS NULL ORDER BY rowid LIMIT 3",
            (identity.player_id,),
        )
        panels = tuple(
            Panel(
                f"奇遇 {row['choice_id']}",
                tuple(
                    Line(f"候选{index}", option_text(option), option["story"])
                    for index, option in enumerate(json.loads(row["options_json"]), 1)
                ),
                f"/派遣奇遇 {row['choice_id']} 1 或 2",
            )
            for row in rows
        )
        return self.view(
            identity,
            "旅行的岔路口",
            panels=panels,
            banner="选择不影响猪猪返程；下次出发时未选择项会按原出发偏好自动领取。" if rows else "没有待选择奇遇。",
            hints=("候选在探索时已固定，重启、重发或换序查看不会重抽。",),
        )
