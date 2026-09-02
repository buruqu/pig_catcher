"""乐队编排与养成操作；所有预览绑定实例和版本，确认时重新检查。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from itertools import combinations, product

from ..domain.dispatch import MATERIAL_SCALE
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.models import CommandIdentity
from ..domain.selectors import parse_asset_selector
from ..domain.tour import canonical_members, ensemble_available, forecast_route, validate_formation, validate_plan
from ..domain.tour_catalog import (
    BRANCH_COST,
    BRANCHES,
    CHARACTERS,
    COLORS,
    EMBLEMS,
    ENSEMBLES_BY_ID,
    EQUIPMENT_COSTS,
    PRACTICE_COST,
    THEMES_BY_ID,
    TOOLS_BY_ID,
    VENUES_BY_ID,
    TourError,
    default_plan,
    training_level,
)
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.achievement_coupons import AchievementCouponRepository
from ..infrastructure.repositories.dispatch import encode, iso_ms
from ..infrastructure.repositories.tour import TourRepository, beijing_day
from .tour_queries import TourQueries, cost_text, tour_pig


def member_fingerprint(member: dict) -> dict:
    """只比较会影响确认决策的状态；不因时钟或派遣累计时长改变而误失效。"""
    return {
        key: member.get(key)
        for key in (
            "pig_instance_id",
            "template_id",
            "training_exp",
            "branch",
            "rapport",
            "favorite",
            "display_variant",
        )
    }


class TourSetup:
    def __init__(self, repository: TourRepository, queries: TourQueries) -> None:
        self.repo, self.queries = repository, queries

    async def pending(
        self, session: DatabaseSession, player_id: str, operation: str, payload: dict, now_ms: int
    ) -> None:
        await session.execute(
            """INSERT INTO tour_pending VALUES(?,?,?,?) ON CONFLICT(player_id)
            DO UPDATE SET operation=excluded.operation,payload_json=excluded.payload_json,
            expires_ms=excluded.expires_ms""",
            (player_id, operation, encode(payload), now_ms + 120_000),
        )

    async def revision(self, session: DatabaseSession, player_id: str, now_ms: int) -> None:
        await session.execute(
            "UPDATE tour_profiles SET revision=revision+1,updated_at=? WHERE player_id=?", (iso_ms(now_ms), player_id)
        )

    async def select(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        text: str,
        *,
        selected: list[str] | None = None,
        position: bool = True,
        guest: bool | None = False,
        retiring: bool = False,
    ) -> dict:
        selected = selected or []
        if position and text.isascii() and text.isdigit() and 1 <= int(text) <= 5:
            row = await session.fetch_one(
                """SELECT r.member_ids_json FROM tour_profiles p JOIN tour_rosters r
                ON r.player_id=p.player_id AND r.slot=p.active_slot WHERE p.player_id=?""",
                (identity.player_id,),
            )
            ids = json.loads(row[0]) if row else []
            if int(text) > len(ids) or ids[int(text) - 1] in selected:
                raise TourError("没有这个阵容位置，或同一实例被重复选择。")
            return await self.repo.member(
                session, identity.player_id, ids[int(text) - 1], available=True, guest=guest, retiring=retiring
            )
        selector = parse_asset_selector(text)
        clause = "AND p.short_code=? COLLATE NOCASE" if selector.short_code else "AND p.is_favorite=0"
        parameters: list = [identity.player_id, identity.scope.value, selector.name, "".join(selector.name.split())]
        if selector.short_code:
            parameters.append(selector.short_code)
        exclusions = ""
        if selected:
            exclusions = "AND p.pig_instance_id NOT IN (" + ",".join("?" for _ in selected) + ")"
            parameters.extend(selected)
        row = await session.fetch_one(
            f"""SELECT p.pig_instance_id FROM pig_instances p
            WHERE p.owner_player_id=? AND p.scope_id=? AND p.state='active' AND p.locked_trade_id IS NULL
            AND (p.display_name_snapshot=? OR REPLACE(REPLACE(p.display_name_snapshot,' ',''),'　','')=? COLLATE NOCASE)
            AND NOT EXISTS(SELECT 1 FROM asset_occupancies o WHERE o.pig_instance_id=p.pig_instance_id)
            {clause} {exclusions} ORDER BY p.official_value,p.acquired_at,p.pig_instance_id LIMIT 1""",
            parameters,
        )
        if row is None:
            raise TourError(f"找不到可选的“{text}”。名称自动选最低价值、非收藏空闲猪；收藏猪请使用全名#编号。")
        return await self.repo.member(
            session, identity.player_id, row[0], available=True, guest=guest, retiring=retiring
        )

    async def check_cost(self, session: DatabaseSession, player_id: str, costs: dict) -> None:
        balance = (await session.fetch_one("SELECT coin_balance FROM players WHERE player_id=?", (player_id,)))[0]
        if balance < costs.get("coins", 0):
            raise TourError("猪币不足，本次没有消耗任何资源。")
        for material, amount in costs.items():
            if material == "coins":
                continue
            row = await session.fetch_one(
                "SELECT quantity,remainder_units FROM material_balances WHERE player_id=? AND material_id=?",
                (player_id, material),
            )
            if not row or row[0] * MATERIAL_SCALE + row[1] < amount * MATERIAL_SCALE:
                raise TourError("材料不足：需要 " + cost_text(costs) + "。可去派遣获得材料。")

    async def ready(self, session: DatabaseSession, profile: dict) -> dict:
        if await self.repo.active_run(session, profile["player_id"]):
            raise TourError("还有进行中的巡演，请先继续或结束。")
        roster, members = await self.repo.roster(session, profile, available=True)
        guest = (
            await self.repo.member(session, profile["player_id"], profile["guest_id"], available=True, guest=True)
            if profile["guest_id"]
            else None
        )
        plans = json.loads(profile["plans_json"])
        if profile["tickets"] < 1:
            raise TourError("没有可用档期。每天补一张、最多七张；排练仍然免费。")
        for plan in plans:
            validate_plan(plan, members, fans=profile["fans"])
        for tool, amount in Counter(p["tool"] for p in plans if p["tool"]).items():
            row = await session.fetch_one(
                "SELECT quantity FROM tour_tools WHERE player_id=? AND tool_id=?", (profile["player_id"], tool)
            )
            if not row or row[0] < amount:
                raise TourError(f"三站安排需要{TOOLS_BY_ID[tool].name}×{amount}，请先制作或移除器具安排。")
        return {
            "revision": profile["revision"],
            "roster": roster,
            "members": members,
            "guest": guest,
            "plans": plans,
            "equipment": profile["equipment"],
            "songs": await self.repo.songs(session, profile["player_id"]),
            "coupons": await AchievementCouponRepository().selected(
                session, profile["player_id"], ("tour-stage", "tour-visual")
            ),
        }

    @staticmethod
    def same_ready(before: dict, after: dict) -> bool:
        return (
            before.get("coupons", {}) == after.get("coupons", {})
            and all(before[key] == after[key] for key in ("revision", "roster", "plans", "equipment", "songs"))
            and (
                [member_fingerprint(m) for m in before["members"]] == [member_fingerprint(m) for m in after["members"]]
                and (member_fingerprint(before["guest"]) if before["guest"] else None)
                == (member_fingerprint(after["guest"]) if after["guest"] else None)
            )
        )

    async def auto_selection(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        profile: dict,
        theme_id: str,
    ) -> dict:
        """从空闲同团猪中选择现有评分器下的最佳阵容与三站路线。"""
        if theme_id not in THEMES_BY_ID:
            raise TourError("未知乐队；可先用 /猪猪巡演 主题 查看可选乐队。")
        if await self.repo.active_run(session, identity.player_id):
            raise TourError("还有进行中的巡演，请先 /巡演一键 完成，或明确结束。")
        rows = await session.fetch_all(
            """SELECT p.pig_instance_id,p.template_id FROM pig_instances p JOIN pig_templates t
            ON t.template_id=p.template_id
            WHERE p.owner_player_id=? AND p.scope_id=? AND p.state='active'
            AND p.locked_trade_id IS NULL
            AND NOT EXISTS(SELECT 1 FROM asset_occupancies o WHERE o.pig_instance_id=p.pig_instance_id)
            AND (t.scope_type='common' OR EXISTS(SELECT 1 FROM scope_pig_templates s
                WHERE s.scope_id=p.scope_id AND s.template_id=p.template_id
                AND s.authorized=1 AND s.consent_status='granted'))
            ORDER BY p.official_value,p.acquired_at,p.pig_instance_id""",
            (identity.player_id, identity.scope.value),
        )
        candidates = []
        for row in rows:
            character = CHARACTERS.get(row["template_id"])
            if character and character.band == theme_id:
                candidates.append(
                    await self.repo.member(session, identity.player_id, row["pig_instance_id"], available=True)
                )
        if not candidates:
            raise TourError("没有可自动编队的该乐队空闲猪；请检查持有角色、交易、派遣或对战状态。")

        # 同一角色的不同立绘只保留培养最好的一只；培养相同则优先低价值实例。
        representatives: dict[str, dict] = {}
        for member in candidates:
            identity_id = CHARACTERS[member["template_id"]].identity
            key = (
                training_level(int(member["training_exp"])),
                int(member["rapport"]),
                int(member["own_experience"]),
                bool(member["branch"]),
                bool(member["favorite"]),
                -int(member["official_value"]),
            )
            current = representatives.get(identity_id)
            if current is None:
                representatives[identity_id] = member
                continue
            current_key = (
                training_level(int(current["training_exp"])),
                int(current["rapport"]),
                int(current["own_experience"]),
                bool(current["branch"]),
                bool(current["favorite"]),
                -int(current["official_value"]),
            )
            if key > current_key:
                representatives[identity_id] = member
        available = list(representatives.values())
        if len(available) < 3:
            raise TourError("该乐队至少需要三位不同角色的空闲猪才能自动编队。")

        unlocked_venues = [venue for venue in VENUES_BY_ID.values() if int(profile["fans"]) >= venue.fans]
        song_plays = await self.repo.songs(session, identity.player_id)
        order = {
            character.identity: index
            for index, character in enumerate(CHARACTERS.values())
            if character.band == theme_id
        }
        best: dict | None = None
        best_key: tuple[float, float, int, int, int] | None = None
        for size in range(3, min(5, len(available)) + 1):
            for chosen_tuple in combinations(available, size):
                chosen = sorted(chosen_tuple, key=lambda member: order[CHARACTERS[member["template_id"]].identity])
                try:
                    unique = validate_formation(chosen)
                except TourError:
                    continue
                melody = [
                    member for member in unique if "主旋律" in CHARACTERS[member["template_id"]].roles
                ]
                center = max(
                    melody,
                    key=lambda member: (
                        training_level(int(member["training_exp"])),
                        int(member["rapport"]),
                        -int(member["official_value"]),
                    ),
                )["pig_instance_id"]
                for venues in product(unlocked_venues, repeat=3):
                    plans = []
                    for venue in venues:
                        plan = default_plan(theme_id)
                        plan["venue"] = venue.venue_id
                        plans.append(plan)
                    forecast = forecast_route(
                        chosen,
                        plans,
                        equipment=int(profile["equipment"]),
                        song_plays=song_plays,
                        center=center,
                    )
                    key = (
                        round(sum(float(stage["score"]) for stage in forecast), 3),
                        min(float(stage["score"]) for stage in forecast),
                        len({venue.venue_id for venue in venues}),
                        size,
                        sum(venue.fans for venue in venues),
                    )
                    if best_key is None or key > best_key:
                        best_key = key
                        best = {
                            "theme": theme_id,
                            "members": chosen,
                            "plans": plans,
                            "forecast": forecast,
                            "center_id": center,
                            "captain_id": chosen[0]["pig_instance_id"],
                        }
        if best is None:
            raise TourError("现有同团猪无法同时覆盖主旋律、节奏与伴奏；可继续使用手动混编。")
        return best

    async def auto_preview(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        profile: dict,
        theme_id: str,
        now_ms: int,
        *,
        full_tour: bool,
    ):
        if full_tour and int(profile["tickets"]) < 1:
            raise TourError("没有可用档期。每天补一张、最多七张；自动配队本身仍可使用。")
        selected = await self.auto_selection(session, identity, profile, theme_id)
        slot = int(profile["active_slot"])
        current_roster = await session.fetch_one(
            "SELECT revision FROM tour_rosters WHERE player_id=? AND slot=?", (identity.player_id, slot)
        )
        roster_revision = int(current_roster[0]) if current_roster else 0
        if not full_tour:
            payload = {
                "revision": profile["revision"],
                "args": {
                    "slot": slot,
                    "selectors": [f"{member['name']}#{member['short_code']}" for member in selected["members"]],
                },
                "members": selected["members"],
                "costs": {},
            }
            await self.pending(session, identity.player_id, "roster", payload, now_ms)
            return self.queries.view(
                identity,
                "同乐队一键配队 · 请确认",
                profile,
                banner=(
                    f"已从{THEMES_BY_ID[theme_id].band_name}选择当前最佳的{len(selected['members'])}位空闲成员，"
                    f"保存到阵容{slot}；不消耗档期。"
                ),
                pigs=tuple(tour_pig(member, position=index) for index, member in enumerate(selected["members"], 1)),
                panels=(
                    Panel(
                        "自动站位",
                        (
                            Line(
                                "中心",
                                next(
                                    member["name"]
                                    for member in selected["members"]
                                    if member["pig_instance_id"] == selected["center_id"]
                                ),
                            ),
                            Line("队长", selected["members"][0]["name"]),
                        ),
                    ),
                ),
                hints=("2分钟内 /猪猪巡演 确认；/猪猪巡演 取消", "/猪猪巡演 自动 乐队名 · 自动完成路线与三站"),
            )

        guest = (
            await self.repo.member(
                session, identity.player_id, profile["guest_id"], available=True, guest=True
            )
            if profile["guest_id"]
            else None
        )
        roster = {
            "player_id": identity.player_id,
            "slot": slot,
            "member_ids_json": encode([member["pig_instance_id"] for member in selected["members"]]),
            "captain_id": selected["captain_id"],
            "center_id": selected["center_id"],
            "revision": roster_revision + 1,
        }
        ready = {
            "revision": int(profile["revision"]) + 1,
            "roster": roster,
            "members": selected["members"],
            "guest": guest,
            "plans": selected["plans"],
            "equipment": profile["equipment"],
            "songs": await self.repo.songs(session, identity.player_id),
            "coupons": await AchievementCouponRepository().selected(
                session, identity.player_id, ("tour-stage", "tour-visual")
            ),
        }
        payload = {
            "base_revision": profile["revision"],
            "base_roster_revision": roster_revision,
            "slot": slot,
            "theme": theme_id,
            "ready": ready,
        }
        await self.pending(session, identity.player_id, "auto_start", payload, now_ms)
        shadow = {**profile, "plans_json": encode(selected["plans"])}
        view = await self.queries.preview(
            session, identity, shadow, roster, selected["members"], confirmation=True
        )
        route = " → ".join(VENUES_BY_ID[plan["venue"]].name for plan in selected["plans"])
        return replace(
            view,
            title="同乐队自动巡演 · 请确认",
            banner=(
                f"{THEMES_BY_ID[theme_id].band_name}已自动完成最佳配队与路线：{route}。"
                "确认后扣1张档期，并在同一条指令中完成全部三站。"
            ),
            hints=("2分钟内 /猪猪巡演 确认；只需确认这一次。", "/猪猪巡演 取消 · 不改阵容、不扣档期"),
        )

    async def preview(
        self, session: DatabaseSession, identity: CommandIdentity, profile: dict, action: str, args: dict, now_ms: int
    ):
        payload = {"revision": profile["revision"], "args": args, "members": []}
        banner, lines, costs = "", [], {}
        if action == "roster":
            if type(args.get("slot")) is not int or not 1 <= args["slot"] <= 3:
                raise TourError("阵容编号为1至3。")
            selectors = args.get("selectors")
            if not isinstance(selectors, list) or (selectors and not 3 <= len(selectors) <= 5):
                raise TourError("阵容需要三至五只猪；清空请明确使用“清空”。")
            for text in selectors:
                payload["members"].append(
                    await self.select(
                        session,
                        identity,
                        text,
                        selected=[m["pig_instance_id"] for m in payload["members"]],
                        position=False,
                    )
                )
            if payload["members"]:
                validate_formation(payload["members"])
            banner = f"保存为阵容{args['slot']}，同时切换为当前阵容。成员获得独立的乐队保护。"
            lines.append(Line("替换原阵容", "不会自动解除原成员的保护；可单独解除保护。"))
        elif action in {"practice", "branch", "retire", "guest"}:
            if action == "guest" and args["selector"] in {"取消", "无"}:
                banner = "移除舞台客串，不清除原猪的保护或收藏。"
            else:
                member = await self.select(
                    session,
                    identity,
                    args["selector"],
                    guest=None if action == "retire" else action == "guest",
                    retiring=action == "retire",
                )
                payload["members"] = [member]
                if action == "practice":
                    if training_level(member["training_exp"]) >= 10:
                        raise TourError("已经巡演满级，不再接受付费训练；正常演出仍记录本人培养贡献。")
                    if await session.fetch_one(
                        "SELECT 1 FROM tour_practice_days WHERE pig_instance_id=? AND practice_day=?",
                        (member["pig_instance_id"], beijing_day(now_ms)),
                    ):
                        raise TourError("这只猪今天已经付费练习过；转让也不会重置每日次数。")
                    costs, banner = PRACTICE_COST, "付费练习 +50 巡演经验；每只每天一次，不增加曲目熟练度或默契。"
                elif action == "branch":
                    if args["branch"] not in BRANCHES or training_level(member["training_exp"]) < 3:
                        raise TourError("巡演Lv.3起可选择亲近、技术、叙事之一。")
                    if member["branch"] == args["branch"]:
                        raise TourError("已经是这个风格，无需重复支付。")
                    costs = BRANCH_COST if member["branch"] else {}
                    banner = f"改为{args['branch']}风格。首次免费，之后切换收费；风格只补演出评分，不改变身份。"
                elif action == "retire":
                    banner = "从本人全部三套阵容及客串中移除，并关闭乐队保护。原收藏标记、经验、贡献和历史均保留。"
                    lines.append(
                        Line("操作风险", "确认后这只猪可能被售卖、做菜或转让；日后重新编队/训练会恢复乐队保护。")
                    )
                else:
                    banner = "绿茶猪作客串，保存特别纪念；不承担音乐职能，也不增加分数或收益。"
        elif action == "upgrade":
            if profile["equipment"] >= 5:
                raise TourError("舞台器材已满级。")
            costs = EQUIPMENT_COSTS[profile["equipment"]]
            banner = f"舞台器材升至Lv.{profile['equipment'] + 1}。仅影响巡演器材分项，不影响抓猪或做菜。"
        elif action == "archive":
            if await self.repo.active_run(session, identity.player_id):
                raise TourError("请先完成或结束当前巡演再解散。")
            banner = "解散当前乐队并清空三套阵容。档期、粉丝、器材、训练和收藏保留；重建不会重复送初始档期。"
            lines.append(Line("保护保留", "原成员仍受保护；请逐只明确解除，不会因为解散而自动卖掉。"))
        else:
            raise TourError("未知乐队确认操作。")
        if costs:
            await self.check_cost(session, identity.player_id, costs)
        payload["costs"] = costs
        lines.append(Line("费用", cost_text(costs)))
        await self.pending(session, identity.player_id, action, payload, now_ms)
        return self.queries.view(
            identity,
            "乐队操作 · 请确认",
            profile,
            banner=banner,
            pigs=tuple(tour_pig(m, position=i) for i, m in enumerate(payload["members"], 1)),
            panels=(Panel("确认内容", tuple(lines)),),
            hints=("2分钟内 /猪猪巡演 确认；/猪猪巡演 取消",),
        )

    async def confirm(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        profile: dict,
        action: str,
        payload: dict,
        now_ms: int,
        key: str,
    ):
        if payload["revision"] != profile["revision"]:
            raise TourError("乐队配置已改变，请重新预览后确认；没有消耗资源。")
        members = []
        for before in payload["members"]:
            member = await self.repo.member(
                session,
                identity.player_id,
                before["pig_instance_id"],
                available=True,
                guest=None if action == "retire" else action == "guest",
                retiring=action == "retire",
            )
            if member_fingerprint(member) != member_fingerprint(before):
                raise TourError("猪猪状态已改变，请重新预览；没有消耗资源。")
            members.append(member)
        args = payload["args"]
        if action == "roster":
            if members:
                unique = validate_formation(members)
                center = next(m["pig_instance_id"] for m in unique if "主旋律" in CHARACTERS[m["template_id"]].roles)
                captain = members[0]["pig_instance_id"]
            else:
                center = captain = ""
            await session.execute(
                """INSERT INTO tour_rosters VALUES(?,?,?,?,?,1) ON CONFLICT(player_id,slot)
                DO UPDATE SET member_ids_json=excluded.member_ids_json,captain_id=excluded.captain_id,
                center_id=excluded.center_id,revision=tour_rosters.revision+1""",
                (identity.player_id, args["slot"], encode([m["pig_instance_id"] for m in members]), captain, center),
            )
            await session.execute(
                "UPDATE tour_profiles SET active_slot=? WHERE player_id=?", (args["slot"], identity.player_id)
            )
            for m in members:
                await self.repo.protect(session, identity.player_id, identity.scope.value, m["pig_instance_id"])
            await self.adapt_plans(session, identity.player_id, members)
            title, banner = "阵容已保存", "已切换当前阵容；不适配新阵容的高光和合奏已恢复自动，完成过的站点不变。"
        elif action == "practice":
            member = members[0]
            if training_level(member["training_exp"]) >= 10 or await session.fetch_one(
                "SELECT 1 FROM tour_practice_days WHERE pig_instance_id=? AND practice_day=?",
                (member["pig_instance_id"], beijing_day(now_ms)),
            ):
                raise TourError("今天已练习或已经满级，未扣除资源。")
            await self.repo.occupy(session, identity.player_id, identity.scope.value, members, key, now_ms)
            await self.repo.cost(
                session,
                identity.player_id,
                identity.scope.value,
                PRACTICE_COST,
                key=key,
                kind="tour-practice",
                now_ms=now_ms,
            )
            await self.repo.train(
                session,
                identity.player_id,
                identity.scope.value,
                member,
                50,
                natural=False,
                source=key,
                subevent="practice-training",
                now_ms=now_ms,
            )
            await session.execute(
                "INSERT INTO tour_practice_days VALUES(?,?,?,?)",
                (member["pig_instance_id"], beijing_day(now_ms), identity.player_id, key),
            )
            await self.repo.release(session, key)
            title, banner = (
                "今天的练习完成",
                f"+50 巡演经验 · Lv.{training_level(member['training_exp'] + 50)} / 10"
                f" · 消耗 {cost_text(PRACTICE_COST)}",
            )
        elif action == "branch":
            member = members[0]
            await self.repo.cost(
                session,
                identity.player_id,
                identity.scope.value,
                payload["costs"],
                key=key,
                kind="tour-style",
                now_ms=now_ms,
            )
            await session.execute(
                "INSERT INTO tour_proficiency VALUES(?,0,?) ON CONFLICT(pig_instance_id) "
                "DO UPDATE SET branch=excluded.branch",
                (member["pig_instance_id"], args["branch"]),
            )
            await self.repo.protect(session, identity.player_id, identity.scope.value, member["pig_instance_id"])
            title, banner = "风格已确定", f"{member['name']} · {args['branch']}风格；身份与乐器不变。"
        elif action == "upgrade":
            costs = EQUIPMENT_COSTS[profile["equipment"]]
            natural = await session.fetch_one(
                """SELECT COALESCE(SUM(delta_units),0) FROM material_ledger
                WHERE player_id=? AND material_id='stage-components' AND delta_units>0
                AND source_kind IN ('dispatch-base','dispatch-bonus','dispatch-encounter')""",
                (identity.player_id,),
            )
            await self.repo.cost(
                session, identity.player_id, identity.scope.value, costs, key=key, kind="tour-equipment", now_ms=now_ms
            )
            await session.execute(
                "UPDATE tour_profiles SET equipment=equipment+1 WHERE player_id=? AND equipment=?",
                (identity.player_id, profile["equipment"]),
            )
            await self.repo.fact(
                session,
                identity.player_id,
                identity.scope.value,
                key,
                "equipment-upgraded",
                now_ms,
                {
                    "level": profile["equipment"] + 1,
                    "costs": costs,
                    "natural_stage_components_before_units": natural[0],
                    "paid": True,
                },
            )
            title, banner = "舞台器材升级完成", f"Lv.{profile['equipment'] + 1} / 5 · 消耗 {cost_text(costs)}"
        elif action == "guest":
            await session.execute(
                "UPDATE tour_profiles SET guest_id=? WHERE player_id=?",
                (members[0]["pig_instance_id"] if members else None, identity.player_id),
            )
            if members:
                await self.repo.protect(
                    session, identity.player_id, identity.scope.value, members[0]["pig_instance_id"]
                )
            title, banner = "客串已安排" if members else "客串已移除", "客串不会额外增加音乐角色人数或评分。"
        elif action == "retire":
            member = members[0]
            pid = member["pig_instance_id"]
            rows = await session.fetch_all("SELECT * FROM tour_rosters WHERE player_id=?", (identity.player_id,))
            for row in rows:
                ids = [item for item in json.loads(row["member_ids_json"]) if item != pid]
                captain = row["captain_id"] if row["captain_id"] in ids else (ids[0] if ids else "")
                center = row["center_id"] if row["center_id"] in ids else ""
                await session.execute(
                    "UPDATE tour_rosters SET member_ids_json=?,captain_id=?,center_id=?,revision=revision+1 "
                    "WHERE player_id=? AND slot=?",
                    (encode(ids), captain, center, identity.player_id, row["slot"]),
                )
            await session.execute(
                "UPDATE tour_profiles SET guest_id=NULL WHERE player_id=? AND guest_id=?", (identity.player_id, pid)
            )
            await session.execute(
                "UPDATE tour_protections SET protected=0 WHERE pig_instance_id=? AND player_id=?",
                (pid, identity.player_id),
            )
            # 不尝试补上另一只同名猪；不足三人的阵容必须由用户重新编排。
            await self.adapt_plans(session, identity.player_id, [])
            title, banner = (
                "已解除乐队保护",
                "已移出全部阵容和客串。经验、本人培养贡献、收藏标记与历史均保留；现在可按原规则处置。",
            )
        elif action == "archive":
            if await self.repo.active_run(session, identity.player_id):
                raise TourError("巡演尚未结束，不能解散。")
            await session.execute("DELETE FROM tour_rosters WHERE player_id=?", (identity.player_id,))
            await session.execute(
                "UPDATE tour_profiles SET archived=1,guest_id=NULL WHERE player_id=?", (identity.player_id,)
            )
            title, banner = "乐队暂时落幕", "档期、粉丝、器材、个人成长与收藏保留。成员保护不会自动解除。"
        else:
            raise TourError("未知待确认操作。")
        await self.revision(session, identity.player_id, now_ms)
        await self.repo.fact(
            session,
            identity.player_id,
            identity.scope.value,
            key,
            action,
            now_ms,
            {"args": args, "member_ids": [m["pig_instance_id"] for m in members], "costs": payload["costs"]},
        )
        profile = await self.repo.profile(session, identity.player_id, now_ms, required=False)
        return self.queries.view(
            identity,
            title,
            profile,
            banner=banner,
            hints=(
                "/我的猪猪乐队 · 查看当前乐队",
                "/猪猪巡演 · 准备演出",
            ),
        )

    async def adapt_plans(self, session: DatabaseSession, player_id: str, members: list[dict]) -> None:
        profile = await session.fetch_one("SELECT plans_json FROM tour_profiles WHERE player_id=?", (player_id,))
        run = await self.repo.active_run(session, player_id)
        identities = {CHARACTERS[m["template_id"]].identity for m in members}

        def adapt(plans: list, start: int = 0):
            for plan in plans[start:]:
                if not set(plan["highlights"]) <= identities:
                    plan["highlights"] = []
                if plan["ensemble"] not in {"auto", "none"} and (
                    not members or not ensemble_available(ENSEMBLES_BY_ID[plan["ensemble"]], members)
                ):
                    plan["ensemble"] = "auto"
            return plans

        await session.execute(
            "UPDATE tour_profiles SET plans_json=? WHERE player_id=?",
            (encode(adapt(json.loads(profile[0]))), player_id),
        )
        if run:
            await session.execute(
                "UPDATE tour_runs SET plans_json=? WHERE run_id=?",
                (encode(adapt(json.loads(run["plans_json"]), run["stage_count"])), run["run_id"]),
            )

    async def settings(
        self, session: DatabaseSession, identity: CommandIdentity, profile: dict, action: str, args: dict, now_ms: int
    ):
        if action in {"rename", "description", "color", "emblem", "costume"}:
            value = args["value"]
            if (
                not isinstance(value, str)
                or not value
                or len(value) > (100 if action == "description" else 32)
                or any(ord(c) < 32 for c in value)
            ):
                raise TourError("设置内容为空、太长或包含控制字符。")
            if action == "color" and value not in COLORS:
                raise TourError("可选主题色：" + "、".join(COLORS))
            if action in {"emblem", "costume"} and not (action == "emblem" and value in EMBLEMS):
                if action == "costume" and value == "默认":
                    value = ""
                else:
                    from ..domain.tour_catalog import resolve_definition

                    theme_id = resolve_definition(value, THEMES_BY_ID, label="主题")
                    row = await session.fetch_one(
                        "SELECT 1 FROM tour_collections WHERE player_id=? AND collection_key=?",
                        (identity.player_id, f"{action}:{theme_id}"),
                    )
                    if not row:
                        raise TourError("尚未解锁该主题装扮；三站该主题均合格且至少A可获得。")
                    value = f"theme:{theme_id}" if action == "emblem" else theme_id
            column = "name" if action == "rename" else action
            await session.execute(f"UPDATE tour_profiles SET {column}=? WHERE player_id=?", (value, identity.player_id))
        elif action == "switch":
            if type(args.get("slot")) is not int or not 1 <= args["slot"] <= 3:
                raise TourError("阵容编号为1至3。")
            shadow = {**profile, "active_slot": args["slot"]}
            _, members = await self.repo.roster(session, shadow, available=True)
            await session.execute(
                "UPDATE tour_profiles SET active_slot=? WHERE player_id=?", (args["slot"], identity.player_id)
            )
            await self.adapt_plans(session, identity.player_id, members)
        elif action in {"captain", "center"}:
            roster, members = await self.repo.roster(session, profile, available=True)
            member = await self.select(session, identity, args["selector"])
            if member["pig_instance_id"] not in json.loads(roster["member_ids_json"]):
                raise TourError("队长和中心需要从当前阵容选择。")
            if action == "center":
                validate_formation(members, member["pig_instance_id"])
            column = "captain_id" if action == "captain" else "center_id"
            await session.execute(
                f"UPDATE tour_rosters SET {column}=?,revision=revision+1 WHERE player_id=? AND slot=?",
                (member["pig_instance_id"], identity.player_id, profile["active_slot"]),
            )
        else:
            await self.set_plan(session, identity, profile, action, args)
        await self.revision(session, identity.player_id, now_ms)
        view = (
            await self.queries.overview(session, identity, now_ms)
            if action in {"theme", "route", "setlist", "highlights", "ensemble", "tool"}
            else await self.queries.band(session, identity, now_ms)
        )
        return replace(
            view,
            banner="设置已保存。已结算站点不改写；若有未演出站点，新设置对它们生效。"
            + ("不适配阵容的高光或合奏已恢复自动。" if action == "switch" else ""),
        )

    async def set_plan(
        self, session: DatabaseSession, identity: CommandIdentity, profile: dict, action: str, args: dict
    ) -> None:
        plans = json.loads(profile["plans_json"])
        run = await self.repo.active_run(session, identity.player_id)
        start = run["stage_count"] if run else 0
        if "stage" in args and (type(args["stage"]) is not int or not start <= args["stage"] <= 2):
            raise TourError("只能修改尚未演出的第1至3站。")
        members = None
        if action in {"highlights", "ensemble"}:
            _, members = await self.repo.roster(session, profile)
        if action == "highlights":
            selected = [await self.select(session, identity, text) for text in args["selectors"]]
            if any(
                m["pig_instance_id"] not in {x["pig_instance_id"] for x in canonical_members(members)} for m in selected
            ):
                raise TourError("高光必须选择当前阵容中不同角色的代表实例。")
            highlights = [CHARACTERS[m["template_id"]].identity for m in selected]
            if len(highlights) > 2 or len(set(highlights)) != len(highlights):
                raise TourError("最多两位不同角色高光。")
        else:
            highlights = []

        def edit(items: list, first: int):
            if action == "theme":
                if args["theme"] not in THEMES_BY_ID:
                    raise TourError("未知主题。")
                for plan in items[first:]:
                    plan["theme"] = args["theme"]
                    plan["songs"] = [f"{args['theme']}-{i}" for i in (1, 2, 3)]
            elif action == "route":
                if len(args["venues"]) != 3:
                    raise TourError("路线必须三站。")
                for i in range(first, 3):
                    venue = VENUES_BY_ID.get(args["venues"][i])
                    if venue is None or profile["fans"] < venue.fans:
                        raise TourError("选择的场地未知或尚未解锁。")
                    items[i]["venue"] = venue.venue_id
            elif action in {"setlist", "highlights", "ensemble", "tool"}:
                field = {"setlist": "songs", "highlights": "highlights", "ensemble": "ensemble", "tool": "tool"}[action]
                items[args["stage"]][field] = highlights if action == "highlights" else args[field]
            else:
                raise TourError("未知编排设置。")
            # 即使没有阵容也要校验曲目、器具；有阵容再检查高光与合奏条件。
            from ..domain.tour_catalog import SONGS_BY_ID

            for plan in items[first:]:
                if (
                    len(plan["songs"]) != 3
                    or len(set(plan["songs"])) != 3
                    or any(s not in SONGS_BY_ID for s in plan["songs"])
                ):
                    raise TourError("每站恰好三首不同原创曲目。")
                if plan["tool"] and plan["tool"] not in TOOLS_BY_ID:
                    raise TourError("未知巡演器具。")
                if members:
                    validate_plan(plan, members, fans=profile["fans"])
            return items

        await session.execute(
            "UPDATE tour_profiles SET plans_json=? WHERE player_id=?", (encode(edit(plans, 0)), identity.player_id)
        )
        if run:
            await session.execute(
                "UPDATE tour_runs SET plans_json=? WHERE run_id=?",
                (encode(edit(json.loads(run["plans_json"]), start)), run["run_id"]),
            )

    async def craft(
        self, session: DatabaseSession, identity: CommandIdentity, profile: dict, args: dict, now_ms: int, key: str
    ):
        tool = TOOLS_BY_ID.get(args.get("tool_id"))
        count = args.get("quantity")
        if tool is None or type(count) is not int or not 1 <= count <= 99:
            raise TourError("请选择有效器具，数量为1至99。")
        costs = {material: amount * count for material, amount in tool.costs}
        await self.repo.cost(
            session, identity.player_id, identity.scope.value, costs, key=key, kind="tour-craft", now_ms=now_ms
        )
        await session.execute(
            "INSERT INTO tour_tools VALUES(?,?,?) ON CONFLICT(player_id,tool_id) "
            "DO UPDATE SET quantity=quantity+excluded.quantity",
            (identity.player_id, tool.tool_id, count),
        )
        await self.repo.fact(
            session,
            identity.player_id,
            identity.scope.value,
            key,
            "crafted",
            now_ms,
            {"tool_id": tool.tool_id, "quantity": count, "costs": costs},
        )
        return self.queries.view(
            identity,
            "舞台器具制作完成",
            profile,
            banner=f"{tool.name} ×{count} · {cost_text(costs)}",
            panels=(Panel("使用说明", (Line("效果", tool.summary),)),),
            hints=(
                f"/猪猪巡演 器具 1 {tool.name}",
                "/我的猪猪乐队 器具 查看库存",
            ),
        )
