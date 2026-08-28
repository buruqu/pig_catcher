"""巡演公开投影；图片和降级文字来自同一份结构化数据。"""

from __future__ import annotations

import json
import math
from dataclasses import replace

from ..domain.dispatch import MATERIALS, safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchPigCard
from ..domain.models import CommandIdentity
from ..domain.tour import forecast_route
from ..domain.tour_catalog import (
    CHARACTERS,
    COLORS,
    EMBLEMS,
    ENSEMBLES,
    EQUIPMENT_COSTS,
    INSTRUMENTS,
    MAIN_FORMS,
    SCORE_CAPS,
    SCORE_NAMES,
    SONGS,
    SONGS_BY_ID,
    THEME_EMBLEMS,
    THEMES,
    THEMES_BY_ID,
    TOOLS,
    TOOLS_BY_ID,
    VENUES,
    VENUES_BY_ID,
    TourError,
    training_level,
)
from ..domain.tour_views import TourScoreCard, TourView
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.achievement_coupons import AchievementCouponRepository
from ..infrastructure.repositories.tour import TourRepository
from .dispatch_queries import local_time


def cost_text(costs: dict[str, int]) -> str:
    return (
        "、".join(f"{MATERIALS.get(key, '猪币' if key == 'coins' else key)}×{value}" for key, value in costs.items())
        or "免费"
    )


def tour_pig(member: dict, *, position: int | None = None) -> DispatchPigCard:
    char = CHARACTERS.get(member["template_id"])
    level = training_level(member.get("training_exp", 0))
    tags = tuple(INSTRUMENTS[item] for item in char.instruments) if char else ("特邀客串",)
    prefix = f"{position}. " if position else ""
    status = " · 活动占用" if member.get("busy_purpose") else ""
    return DispatchPigCard(
        name=prefix + member["name"],
        short_code=member["short_code"],
        rarity=member["rarity"],
        image_relpath=member["image_relpath"],
        template_id=member["template_id"],
        tags=tuple(dict.fromkeys((*tags, *member.get("display_tags", ())[:2]))),
        summary=(
            f"{char.character if char else '薇欧拉'} · 巡演 Lv.{level} · "
            f"默契 {min(30, member.get('rapport', 0))}/30{status}"
        ),
        favorite=member.get("favorite", False),
    )


def stage_score(stage: dict, *, prefix: str = "") -> TourScoreCard:
    venue = VENUES_BY_ID[stage["plan"]["venue"]]
    preview = stage.get("preview", False)
    variation = stage["variation"]
    note = (
        "确定性排练；正式现场波动最多±3分"
        if preview
        else f"现场波动 {variation:+d} 分 · {stage.get('ensemble_story', '')}"
    )
    if stage.get("lineup_changed"):
        note += " · 本站阵容已变更，按本站成员结算"
    if stage.get("achievement_coupon"):
        coupon = stage["achievement_coupon"]
        note += (
            f" · {coupon['name']}挽回{stage.get('coupon_recovery', 0)}分，剩余{coupon['remaining']}张；"
            f"原始波动{stage['variation_raw']:+d}"
        )
    return TourScoreCard(
        f"{prefix}第{stage['stage_number']}站 · {venue.name}",
        f"{stage['score']:.2f}",
        f"预计 {stage['grade']}" if preview else stage["grade"],
        tuple(Line(SCORE_NAMES[key], f"{stage['components'][key]:.2f}", f"/ {cap}") for key, cap in SCORE_CAPS.items()),
        note,
    )


def grown_members(stage: dict) -> list[dict]:
    """卡面展示本站完成后的成长；评分使用的演出前快照原样保留。"""
    seen, result = set(), []
    for member in stage["members"]:
        char_id = CHARACTERS[member["template_id"]].identity
        if char_id not in seen:
            result.append(
                {
                    **member,
                    "training_exp": member["training_exp"] + stage.get("training_gain", 20),
                    "rapport": member["rapport"] + 1,
                }
            )
            seen.add(char_id)
        else:
            result.append(member)
    return result


class TourQueries:
    def __init__(self, repository: TourRepository) -> None:
        self.repo = repository

    @staticmethod
    def view(identity: CommandIdentity, title: str, profile: dict | None = None, **kwargs) -> TourView:
        profile = profile or {}
        emblem = profile.get("emblem", "星星")
        if emblem.startswith("theme:"):
            emblem_symbol = THEME_EMBLEMS.get(emblem.removeprefix("theme:"), "★")
        else:
            emblem_symbol = EMBLEMS.get(emblem, "★")
        costume = THEMES_BY_ID[profile["costume"]].name if profile.get("costume") in THEMES_BY_ID else ""
        return TourView(
            title=title,
            player_name=safe_display_name(identity.display_name, identity.user_id),
            band_name=profile.get("name", "PiG Dream!"),
            color=COLORS.get(profile.get("color", "粉"), COLORS["粉"]),
            emblem=emblem_symbol,
            costume=costume,
            **kwargs,
        )

    async def band(
        self, session: DatabaseSession, identity: CommandIdentity, now_ms: int, *, slot: int | None = None
    ) -> TourView:
        profile = await self.repo.profile(session, identity.player_id, now_ms, required=False)
        if not profile or profile["archived"]:
            return self.view(
                identity,
                "为自己的乐队起个名字",
                profile,
                banner="三至五只音乐角色猪，跨乐队自由混团。覆盖主旋律、节奏、伴奏即可，不要求五个固定位置。",
                hints=(
                    "/组建乐队 乐队名",
                    "/我的猪猪乐队 角色 1 查看角色与招牌",
                    "首次组建赠两张档期；解散重建不重复赠送。",
                ),
            )
        chosen = profile["active_slot"] if slot is None else slot
        row = await session.fetch_one(
            "SELECT * FROM tour_rosters WHERE player_id=? AND slot=?", (identity.player_id, chosen)
        )
        members = []
        if row:
            members = [
                await self.repo.member(session, identity.player_id, pid) for pid in json.loads(row["member_ids_json"])
            ]
        names = {m["pig_instance_id"]: m["name"] for m in members}
        guest = (
            await self.repo.member(session, identity.player_id, profile["guest_id"], guest=True)
            if profile["guest_id"]
            else None
        )
        roster_lines = (
            Line("队长", names.get(row["captain_id"], "未编队") if row else "未编队"),
            Line("中心", names.get(row["center_id"], "未编队") if row else "未编队"),
            Line("客串", guest["name"] if guest else "无", "客串不占音乐席位、不额外加分"),
        )
        return self.view(
            identity,
            f"我的猪猪乐队 · 阵容{chosen}",
            profile,
            banner=profile["description"] or "各有自己的声部，也能组成我们的乐队。",
            stats=(
                Line("粉丝", str(profile["fans"]), "累积解锁场地"),
                Line("档期", f"{profile['tickets']}/7", "每日+1"),
                Line("舞台器材", f"Lv.{profile['equipment']}/5", "仅巡演评分"),
                Line("当前阵容", str(profile["active_slot"]), "最多保存3套"),
            ),
            pigs=tuple(tour_pig(m, position=i) for i, m in enumerate(members, 1))
            + ((tour_pig(guest),) if guest else ()),
            panels=(Panel("舞台站位", roster_lines),),
            hints=("/乐队编队 1 猪名、猪名、猪名", "/猪猪巡演 排练 · 免费预估", "/猪猪巡演 帮助 · 全部指令"),
        )

    async def overview(self, session: DatabaseSession, identity: CommandIdentity, now_ms: int) -> TourView:
        profile = await self.repo.profile(session, identity.player_id, now_ms)
        run = await self.repo.active_run(session, identity.player_id)
        plans = json.loads(run["plans_json"] if run else profile["plans_json"])
        panels = tuple(
            Panel(
                f"第{i}站 · {VENUES_BY_ID[plan['venue']].name}",
                (
                    Line("主题", THEMES_BY_ID[plan["theme"]].name),
                    Line("曲序", " → ".join(SONGS_BY_ID[song].name for song in plan["songs"])),
                    Line("器具", TOOLS_BY_ID[plan["tool"]].name if plan["tool"] else "无"),
                    Line("状态", "已结算" if run and i <= run["stage_count"] else "等待演出"),
                ),
                "开场 → 中段 → 终曲",
            )
            for i, plan in enumerate(plans, 1)
        )
        return self.view(
            identity,
            "巡演准备室",
            profile,
            banner=f"巡演 {run['run_id']} 已完成 {run['stage_count']}/3 站；站间暂停无超时。"
            if run
            else "每趟三站，消耗一张档期；三站全部完成才发放整趟奖励。",
            stats=(
                Line("档期", f"{profile['tickets']}/7"),
                Line("累计粉丝", str(profile["fans"])),
                Line("舞台器材", f"Lv.{profile['equipment']}"),
            ),
            panels=panels,
            hints=(
                "/巡演继续 · 继续下一站" if run else "/猪猪巡演 出发 · 先查看确认卡",
                "/巡演一键 · 相同规则完成剩余三站",
                "/猪猪巡演 排练 · 不消耗、不发进度",
            ),
        )

    async def preview(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        profile: dict,
        roster: dict,
        members: list[dict],
        *,
        confirmation: bool = False,
    ) -> TourView:
        plans = json.loads(profile["plans_json"])
        results = forecast_route(
            members,
            plans,
            equipment=profile["equipment"],
            song_plays=await self.repo.songs(session, identity.player_id),
            center=roster["center_id"],
        )
        coupons = await AchievementCouponRepository().preview(
            session, identity.player_id, ("tour-stage", "tour-visual")
        )
        return self.view(
            identity,
            "出发前确认" if confirmation else "三站免费排练",
            profile,
            banner="确认后扣一张档期。训练、曲目熟练度随站点增长；正式评分按当时阵容和现场波动结算。"
            if confirmation
            else "仅计算预估，不抽随机数，不增加经验、照片、成就或收益。站间阵容变化会影响正式结果。",
            scorecards=tuple(stage_score(stage) for stage in results),
            pigs=tuple(tour_pig(m, position=i) for i, m in enumerate(members, 1)),
            panels=(
                Panel(
                    "已选成就券 · 预览不消耗",
                    tuple(Line(c["name"], f"库存{c['quantity']}张", c["effect"]) for c in coupons),
                    "正式开始才绑定视觉券；稳场券在下一次正式站点使用，不自动连吃库存。",
                ),
                Panel(
                    "三站安排",
                    tuple(
                        Line(
                            f"第{i}站",
                            " → ".join(SONGS_BY_ID[s].name for s in p["songs"]),
                            THEMES_BY_ID[p["theme"]].name,
                        )
                        for i, p in enumerate(plans, 1)
                    ),
                ),
            ),
            hints=(
                "2分钟内 /猪猪巡演 确认；/猪猪巡演 取消"
                if confirmation
                else "/猪猪巡演 出发；也可先调整路线、编排和高光。",
            ),
        )

    def stage(self, identity: CommandIdentity, profile: dict, stage: dict) -> TourView:
        rows = [
            Line(
                item["character"] + " · " + item["name"],
                "招牌触发" if item["triggered"] else "高光亮相",
                item["summary"],
            )
            for item in stage["highlights"]
        ]
        rows.append(Line("合奏", stage["ensemble_story"]))
        rows.append(Line("成员成长", "每位不同角色代表实例 +20巡演经验、+1默契", "同身份趣味形态不重复增长"))
        if stage["plan"]["tool"]:
            rows.append(
                Line("本站器具", TOOLS_BY_ID[stage["plan"]["tool"]].name, f"使用后剩余 {stage['tool_remaining']} 个")
            )
        if stage["new_collections"]:
            rows.append(Line("新收藏", "、".join(stage["new_collections"])))
        return self.view(
            identity,
            f"第{stage['stage_number']}站 · 演出完成",
            profile,
            banner=THEMES_BY_ID[stage["plan"]["theme"]].story,
            scorecards=(stage_score(stage),),
            pigs=tuple(tour_pig(m) for m in grown_members(stage)),
            panels=(Panel("这一站的记忆", tuple(rows)),),
            celebration=stage["confetti"],
            hints=(
                f"巡演 {stage['run_id']} · 已完成 {stage['stage_number']}/3 站",
                "/巡演继续 · 下一站；/巡演一键 · 完成剩余站点",
                "本次已入账；站间不占用猪猪，可暂停、派遣，返回后重新检查阵容。",
            ),
        )

    def summary(self, identity: CommandIdentity, profile: dict, summary: dict) -> TourView:
        highlights = tuple(
            Line(
                f"第{stage['stage_number']}站",
                "、".join(item["character"] + "·" + item["name"] for item in stage["highlights"]),
                stage["ensemble_story"],
            )
            for stage in summary["stages"]
        )
        additions = summary["new_collections"] + [c for stage in summary["stages"] for c in stage["new_collections"]]
        story_panels = ()
        if summary.get("achievement_story"):
            story = summary["achievement_story"]
            coupon = story["coupon"]
            story_panels = (
                Panel(
                    story["title"],
                    (
                        Line(
                            "原创返场留影",
                            story["text"],
                            f"{coupon['name']} · 剩余{coupon['remaining']}张 · 不额外增加分数或经济奖励",
                        ),
                    ),
                ),
            )
        return self.view(
            identity,
            "三站落幕 · 谢谢每一次合奏",
            profile,
            banner=(
                f"巡演 {summary['run_id']} · 整趟 {summary['score']:.2f} 分 / {summary['grade']} · 奖励已一次性入账。"
            ),
            stats=(
                Line("猪币", f"+{summary['coins']}", f"余额 {summary['coin_balance']}"),
                Line("粉丝", f"+{summary['fans']}", f"累计 {summary['total_fans']}"),
                Line("剩余档期", f"{summary['tickets']}/7"),
            ),
            scorecards=tuple(stage_score(s) for s in summary["stages"]),
            pigs=tuple(tour_pig(m) for m in grown_members(summary["stages"][-1])),
            panels=(
                Panel("三站高光", highlights),
                *story_panels,
                Panel(
                    "巡演纪念", (Line("新获得", "、".join(additions) or "本次没有新的收藏，成长和整趟奖励照常入账。"),)
                ),
            ),
            celebration=True,
            hints=("/巡演游记 收藏 查看票根、照片与原创主题装扮", "/猪猪巡演 排练 · 为下一趟作准备"),
        )

    async def catalog(
        self, session: DatabaseSession, identity: CommandIdentity, action: str, page: int, now_ms: int
    ) -> TourView:
        profile = await self.repo.profile(session, identity.player_id, now_ms, required=False)
        if action == "venues":
            fans = profile["fans"] if profile else 0
            return self.view(
                identity,
                "五个舞台，五种听众",
                profile,
                panels=(
                    Panel(
                        "场地目录",
                        tuple(
                            Line(
                                v.name,
                                f"{'已解锁' if fans >= v.fans else '需' + str(v.fans) + '粉丝'} · {' / '.join(v.tags)}",
                                v.audience,
                            )
                            for v in VENUES
                        ),
                    ),
                ),
                hints=(
                    "/猪猪巡演 路线 街头舞台、校园祭、Livehouse",
                    "场地不额外收门票；用曲目和听众相匹配，不按高星猪加分。",
                ),
            )
        if action == "themes":
            return self.view(
                identity,
                "九种巡演主题",
                profile,
                panels=tuple(Panel(t.name, (Line(t.band_name, " / ".join(t.tags), t.story),)) for t in THEMES),
                hints=(
                    "/猪猪巡演 主题 主题名 · 同时载入推荐曲目",
                    "主题允许自由混团；三站主题合格解锁海报，三站至少A再解锁原创主题服装、队徽。",
                ),
            )
        if action == "ensembles":
            character_names = {char.identity: char.character for char in CHARACTERS.values()}
            return self.view(
                identity,
                "合奏图鉴",
                profile,
                panels=(
                    Panel(
                        "每站最多一个合奏；不要求整支原乐队",
                        tuple(
                            Line(
                                e.name,
                                " + ".join(character_names[i] for i in e.identities)
                                + (f" + 任意{THEMES_BY_ID[e.band].band_name}成员" if e.band else "")
                                or "覆盖三种职能即可",
                                f"{e.kind} · {e.story}",
                            )
                            for e in ENSEMBLES
                        ),
                    ),
                ),
                hints=(
                    "/猪猪巡演 合奏 1 合奏名（或自动、无）",
                    "关系主题和原创职能合奏均为本插件玩法，不是角色官方技能。",
                ),
            )
        if action == "songs":
            size, entries = 9, SONGS
            count = math.ceil(len(entries) / size)
            page = min(page, count)
            plays = await self.repo.songs(session, identity.player_id)
            rows = tuple(
                Line(
                    f"{i}. {song.name}",
                    f"能量{song.energy} · {' / '.join(song.tags)} · 熟练{min(10, plays.get(song.song_id, 0))}/10",
                    f"{THEMES_BY_ID[song.theme_id].name} · {song.summary}",
                )
                for i, song in enumerate(entries[(page - 1) * size : page * size], (page - 1) * size + 1)
            )
            return self.view(
                identity,
                "巡演原创曲库",
                profile,
                panels=(Panel("曲卡不是原作歌曲音频", rows),),
                page=page,
                page_count=count,
                hints=(
                    "/猪猪巡演 编排 1 1、2、3 · 用歌曲编号或全名",
                    "开场—中段—终曲能量递进、动机呼应、观众偏好分别计分。",
                ),
            )
        if action == "characters":
            entries, size = [CHARACTERS[key] for key in MAIN_FORMS], 6
            count = math.ceil(len(entries) / size)
            page = min(page, count)
            return self.view(
                identity,
                "角色与招牌",
                profile,
                panels=tuple(
                    Panel(
                        f"{char.name} · {char.character}",
                        (
                            Line(
                                THEMES_BY_ID[char.band].band_name, " / ".join(INSTRUMENTS[i] for i in char.instruments)
                            ),
                            Line(char.signature.name, char.signature.summary),
                        ),
                    )
                    for char in entries[(page - 1) * size : page * size]
                ),
                page=page,
                page_count=count,
                hints=("/我的猪猪乐队 角色 页码", "每站最多两位不同角色高光；美咲/米歇尔、黄瓜/墨提斯按同一角色算。"),
            )
        raise TourError("未知巡演目录。")

    async def members(self, session: DatabaseSession, identity: CommandIdentity, page: int, now_ms: int) -> TourView:
        profile = await self.repo.profile(session, identity.player_id, now_ms, required=False)
        placeholders = ",".join("?" for _ in CHARACTERS)
        where = f"owner_player_id=? AND scope_id=? AND state='active' AND template_id IN ({placeholders})"
        params = (identity.player_id, identity.scope.value, *CHARACTERS)
        total = (await session.fetch_one(f"SELECT COUNT(*) FROM pig_instances WHERE {where}", params))[0]
        pages = max(1, math.ceil(total / 6))
        page = min(page, pages)
        rows = await session.fetch_all(
            f"SELECT pig_instance_id FROM pig_instances WHERE {where} "
            "ORDER BY display_name_snapshot,official_value,pig_instance_id LIMIT 6 OFFSET ?",
            (*params, (page - 1) * 6),
        )
        members = [await self.repo.member(session, identity.player_id, row[0]) for row in rows]
        return self.view(
            identity,
            "我的音乐角色猪",
            profile,
            pigs=tuple(tour_pig(m) for m in members),
            page=page,
            page_count=pages,
            panels=(
                Panel(
                    "成长明细",
                    tuple(
                        Line(
                            m["name"] + "#" + m["short_code"],
                            f"总经验 {m['training_exp']} · 本人培养 {m['own_experience']}",
                            f"风格 {m['branch'] or 'Lv.3后可选择'}",
                        )
                        for m in members
                    ),
                ),
            ),
            banner="星级、重量、售价不乘巡演评分；同身份形态只取阵容中首个实例。",
            hints=(
                "/乐队练习 猪名 · 每天每只一次付费训练",
                "/我的猪猪乐队 成员 页码",
                "/我的猪猪乐队 解除保护 猪名#编号 · 销毁或转让前需确认",
            ),
        )

    async def equipment(
        self, session: DatabaseSession, identity: CommandIdentity, now_ms: int, *, tools: bool = False
    ) -> TourView:
        profile = await self.repo.profile(session, identity.player_id, now_ms)
        if tools:
            stocks = {
                r["tool_id"]: r["quantity"]
                for r in await session.fetch_all("SELECT * FROM tour_tools WHERE player_id=?", (identity.player_id,))
            }
            return self.view(
                identity,
                "舞台小器具",
                profile,
                panels=tuple(
                    Panel(
                        f"{tool.name} · 持有{stocks.get(tool.tool_id, 0)}",
                        (Line("效果", tool.summary), Line("制作材料", cost_text(dict(tool.costs)))),
                    )
                    for tool in TOOLS
                ),
                hints=(
                    "/我的猪猪乐队 制作 备用线缆 2",
                    "/猪猪巡演 器具 1 备用线缆 · 每站最多消耗一个",
                    "器具、装备不影响抓猪或做菜的概率。",
                ),
            )
        return self.view(
            identity,
            "舞台器材养成",
            profile,
            stats=(
                Line("当前等级", f"Lv.{profile['equipment']}/5"),
                Line("评分加成", f"{profile['equipment']}/5分", "分项总上限不变"),
            ),
            panels=(
                Panel(
                    "逐级升级费用",
                    tuple(
                        Line(f"Lv.{i}", cost_text(cost), "已完成" if i <= profile["equipment"] else "待升级")
                        for i, cost in enumerate(EQUIPMENT_COSTS, 1)
                    ),
                ),
            ),
            hints=("/我的猪猪乐队 器材 升级 · 先预览再确认", "材料来自派遣；不会消耗猪猪、抓猪额度或现有道具。"),
        )

    async def journal(
        self, session: DatabaseSession, identity: CommandIdentity, page: int, now_ms: int, *, collections: bool = False
    ) -> TourView:
        profile = await self.repo.profile(session, identity.player_id, now_ms, required=False)
        table = "tour_collections" if collections else "tour_runs"
        total = (await session.fetch_one(f"SELECT COUNT(*) FROM {table} WHERE player_id=?", (identity.player_id,)))[0]
        count = max(1, math.ceil(total / 6))
        page = min(page, count)
        ordering = "acquired_at DESC,collection_key" if collections else "sequence DESC"
        rows = await session.fetch_all(
            f"SELECT * FROM {table} WHERE player_id=? ORDER BY {ordering} LIMIT 6 OFFSET ?",
            (identity.player_id, (page - 1) * 6),
        )
        pigs = []
        if collections:
            lines = tuple(Line(row["title"], row["kind"], f"来自 {row['run_id']}") for row in rows)
            for row in rows:
                member = json.loads(row["detail_json"]).get("member")
                if member:
                    pigs.append(tour_pig(member))
        else:
            labels = {"active": "续演中", "completed": "已完成", "abandoned": "提前落幕"}
            lines = tuple(
                Line(
                    row["run_id"], f"{labels[row['status']]} · {row['stage_count']}/3站", local_time(row["started_ms"])
                )
                for row in rows
            )
        return self.view(
            identity,
            "巡演收藏册" if collections else "巡演游记",
            profile,
            pigs=tuple(pigs),
            panels=(Panel("只记录自己的巡演", lines or (Line("尚无记录", "去为乐队创造第一张票根吧。"),)),),
            page=page,
            page_count=count,
            hints=("/巡演游记 T开头编号 · 查看这趟完整结算", "/巡演游记 收藏 页码 · 照片、票根、服装和主题队徽"),
        )

    async def detail(self, session: DatabaseSession, identity: CommandIdentity, run_id: str, now_ms: int) -> TourView:
        row = await session.fetch_one(
            "SELECT * FROM tour_runs WHERE run_id=? AND player_id=? AND scope_id=?",
            (run_id, identity.player_id, identity.scope.value),
        )
        if row is None:
            raise TourError("没有找到本群属于你的这趟巡演。")
        profile = await self.repo.profile(session, identity.player_id, now_ms, required=False)
        if row["status"] == "completed":
            summary = json.loads(row["summary_json"])
            return self.summary(identity, {**profile, "name": summary["band_name"]}, summary)
        snapshots = [
            json.loads(r[0])
            for r in await session.fetch_all(
                "SELECT snapshot_json FROM tour_stages WHERE run_id=? ORDER BY stage_number", (run_id,)
            )
        ]
        return self.view(
            identity,
            "巡演游记 · " + run_id,
            profile,
            banner="已提前落幕，不会发放三站整趟奖励。"
            if row["status"] == "abandoned"
            else "站间暂停中，已完成的站点不会重抽。",
            scorecards=tuple(stage_score(s) for s in snapshots),
            hints=("/巡演继续 · 继续当前巡演", "照片和已培养经验保留；取消未演出的部分不退档期。"),
        )

    def joint_summary(self, identity: CommandIdentity, summaries: list[dict], profiles: list[dict]) -> TourView:
        views = [self.summary(identity, profile, summary) for profile, summary in zip(profiles, summaries, strict=True)]
        base = views[0]
        return replace(
            base,
            title="双乐队联演 · 共同谢幕",
            player_name=safe_display_name(identity.display_name, identity.user_id),
            banner="双方各完成三站、各消耗一张档期，独立结算常规奖励；联演不转移猪猪、不额外倍增猪币。",
            band_name=" × ".join(s["band_name"] for s in summaries),
            stats=tuple(
                Line(
                    profile.get("owner_display_name", "团长") + " · " + s["band_name"],
                    f"+{s['coins']}币 / +{s['fans']}粉丝",
                    f"{s['score']:.2f}分 · {s['grade']}",
                )
                for s, profile in zip(summaries, profiles, strict=True)
            ),
            scorecards=tuple(
                stage_score(stage, prefix=s["band_name"] + " · ") for s in summaries for stage in s["stages"]
            ),
            pigs=tuple(
                replace(pig, summary=profile.get("owner_display_name", "团长") + " · " + pig.summary)
                for view, profile in zip(views, profiles, strict=True)
                for pig in view.pigs
            ),
            panels=(Panel("共同纪念", (Line("联演海报", "已分别保存到双方收藏册"),)),)
            + tuple(
                replace(panel, title=profile["name"] + " · " + panel.title)
                for view, profile, summary in zip(views, profiles, summaries, strict=True)
                for panel in view.panels
                if panel.title == summary.get("achievement_story", {}).get("title")
            ),
            hints=("/巡演游记 查看自己三站的详细高光与结算",),
        )
