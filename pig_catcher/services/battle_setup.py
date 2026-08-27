"""战斗猪设置、逐级训练、保护解除及材料制作；预览绑定具体实例。"""

from ..domain.battle import dumps, loads
from ..domain.battle_catalog import CONFIRM_TTL_MS, MATERIAL_IDS, TOOLS_BY_ID, UPGRADE_COSTS, BattleError
from ..domain.dispatch import MATERIAL_SCALE
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..infrastructure.repositories.achievement_coupons import AchievementCouponRepository
from ..infrastructure.repositories.battle import BattleRepository, beijing_day
from ..infrastructure.repositories.dispatch import iso_ms
from .battle_views import cost_text, pig_card, view


class BattleSetup:
    def __init__(self, repo: BattleRepository):
        self.repo = repo

    async def pending(self, session, player_id: str, operation: str, payload: dict, now_ms: int):
        await session.execute(
            """INSERT INTO battle_pending VALUES(?,?,?,?) ON CONFLICT(player_id) DO UPDATE SET
            operation=excluded.operation,payload_json=excluded.payload_json,expires_ms=excluded.expires_ms""",
            (player_id, operation, dumps(payload), now_ms + CONFIRM_TTL_MS),
        )

    async def idle(self, session, identity):
        match = await self.repo.active(session, identity.scope.value)
        if (
            match
            and match["status"] == "active"
            and identity.player_id in {match["initiator_id"], match["opponent_id"]}
        ):
            raise BattleError("正在对战，不能换猪、强化、修改器具或解除保护。")

    async def profile(self, session, identity, now_ms):
        profile = await self.repo.profile(session, identity.player_id)
        quota = await session.fetch_all(
            "SELECT role FROM battle_daily_uses WHERE player_id=? AND day=?", (identity.player_id, beijing_day(now_ms))
        )
        used = {row[0] for row in quota}
        pig = (
            await self.repo.member(session, identity.player_id, profile["pig_instance_id"])
            if profile["pig_instance_id"]
            else None
        )
        protected = (
            await session.fetch_one(
                "SELECT COUNT(*) FROM battle_protections WHERE player_id=? AND protected=1", (identity.player_id,)
            )
        )[0]
        loot = (
            await session.fetch_one(
                "SELECT COALESCE(SUM(5-used),0) FROM battle_loot WHERE actor_id=?", (identity.player_id,)
            )
        )[0]
        return view(
            identity,
            "我的战斗猪",
            banner="+0即可参战。强化仅提升数值招式收益，不提升抽中概率；战斗不会销毁参战猪。",
            pigs=(pig_card(pig),) if pig else (),
            stats=(
                Line("今日主动", "0/1" if "initiator" in used else "1/1", "剩余次数"),
                Line("今日应战", "0/1" if "opponent" in used else "1/1", "剩余次数"),
                Line("待交付战利品", str(loot), "额外次数，猪归胜者"),
            ),
            panels=(
                Panel(
                    "养成与保护",
                    (
                        Line(
                            "下一级成本",
                            cost_text(UPGRADE_COSTS[pig["level"]])
                            if pig and pig["level"] < 5
                            else "已满级"
                            if pig
                            else "请先设置战斗猪",
                        ),
                        Line(
                            "战斗保护",
                            f"{protected}只",
                            "设置或强化后防做菜/售卖/赠送/交易；解除战斗保护不会取消收藏或乐队保护。",
                        ),
                        Line(
                            "当前器具",
                            TOOLS_BY_ID[profile["tool_id"]].name if profile["tool_id"] else "无",
                            "每人入场最多一个，未触发终局退回。",
                        ),
                        Line(
                            "个体微调",
                            "每回合首次数值招式额外+1" if pig and pig["trait_bonus"] else "无",
                            "体型/体重模板内平均位置≥75%时生效，不翻倍。",
                        ),
                    ),
                ),
            ),
            hints=(
                "/战斗猪 设置 名称 → /战斗猪 确认；/战斗猪 强化 → /战斗猪 确认。",
                "/战斗猪 轮盘 宿傩猪；/战斗猪 器具；/比划比划 @群友。",
            ),
        )

    async def tools(self, session, identity):
        rows = await session.fetch_all(
            "SELECT tool_id,quantity FROM battle_tools WHERE player_id=?", (identity.player_id,)
        )
        inventory = {row[0]: row[1] for row in rows}
        return view(
            identity,
            "对战器具工坊",
            panels=(
                Panel(
                    "派遣材料制作",
                    tuple(
                        Line(
                            tool.name + f" · 持有{inventory.get(tool.tool_id, 0)}",
                            tool.description,
                            cost_text(dict(tool.costs)),
                        )
                        for tool in TOOLS_BY_ID.values()
                    ),
                ),
            ),
            hints=(
                "/战斗猪 制作 练习护腕 2；/战斗猪 器具 练习护腕；/战斗猪 器具 无。",
                "制作直接扣材料；装备只作选择。接受时暂存一件，实际触发才消耗，未触发终局退回。",
            ),
        )

    async def perform(self, session, identity, action, args, now_ms, key):
        if action == "profile":
            return await self.profile(session, identity, now_ms)
        if action == "tools":
            return await self.tools(session, identity)
        if action == "cancel_setup":
            await session.execute("DELETE FROM battle_pending WHERE player_id=?", (identity.player_id,))
            return view(identity, "已取消战斗猪确认", banner="未消耗资源；挑战邀请请用 /比划比划 取消。")
        await self.idle(session, identity)
        profile = await self.repo.profile(session, identity.player_id)
        if action == "equip":
            tool = args.get("tool_id", "")
            if tool and tool not in TOOLS_BY_ID:
                raise BattleError("未知对战器具。")
            await session.execute(
                "UPDATE battle_profiles SET tool_id=?,revision=revision+1 WHERE player_id=?", (tool, identity.player_id)
            )
            return await self.profile(session, identity, now_ms)
        if action == "craft":
            tool, quantity = TOOLS_BY_ID.get(args.get("tool_id")), args.get("quantity")
            if not tool or type(quantity) is not int or not 1 <= quantity <= 99:
                raise BattleError("制作数量必须在1至99之间。")
            costs = {material: amount * quantity for material, amount in tool.costs}
            await self.repo.spend(session, identity, costs, key, now_ms)
            await self.repo.tool_change(
                session, identity.player_id, tool.tool_id, quantity, reason="craft", source=key, key=key, now_ms=now_ms
            )
            await self.repo.fact(
                session,
                identity.player_id,
                identity.scope.value,
                key,
                "tool-crafted",
                now_ms,
                {"tool_id": tool.tool_id, "quantity": quantity, "costs": costs},
            )
            return view(
                identity,
                "器具制作完成",
                banner=f"获得{tool.name}×{quantity}；已消耗{cost_text(costs)}。",
                hints=("/战斗猪 器具 " + tool.name,),
            )
        if action.endswith("_preview"):
            member = await self.repo.select(session, identity, args.get("selector", ""))
            operation = action.removesuffix("_preview")
            if operation == "upgrade" and member["level"] >= 5:
                raise BattleError("该猪已强化+5，不再消耗材料。")
            coupon_plan = (
                await AchievementCouponRepository().selected(session, identity.player_id, ("battle-training",))
                if operation == "upgrade"
                else {}
            )
            costs = dict(UPGRADE_COSTS[member["level"]]) if operation == "upgrade" else {}
            if coupon_plan:
                costs["coins"] = max(0, costs["coins"] - 300)
            payload = {"member": member, "revision": profile["revision"], "coupons": coupon_plan}
            await self.pending(session, identity.player_id, operation, payload, now_ms)
            banner = {
                "assign": "设为战斗猪并添加保护；原战斗猪的保护不会自动解除。",
                "retire": "解除这只猪的战斗保护，并移出战斗位；保留全部强化等级，收藏/乐队保护不变。",
                "upgrade": f"强化+{member['level']} → +{member['level'] + 1}；仅数值招式+1。",
            }[operation]
            return view(
                identity,
                "战斗猪操作 · 请确认",
                banner=banner,
                pigs=(pig_card(member),),
                panels=(
                    Panel(
                        "需要资源",
                        (
                            Line(
                                "本次成本",
                                cost_text(costs) if operation == "upgrade" else "无",
                                "训练热身券减免最多300猪币，材料不减；确认成功才扣券。" if coupon_plan else "",
                            ),
                        ),
                    ),
                ),
                hints=("2分钟内 /战斗猪 确认；/战斗猪 取消。确认仅影响上面这只实例。",),
            )
        if action != "confirm":
            raise BattleError("未知战斗猪操作。")
        pending = await session.fetch_one("SELECT * FROM battle_pending WHERE player_id=?", (identity.player_id,))
        if not pending or pending["operation"] not in {"assign", "upgrade", "retire"}:
            raise BattleError("没有待确认的战斗猪操作。")
        if pending["expires_ms"] <= now_ms:
            await session.execute("DELETE FROM battle_pending WHERE player_id=?", (identity.player_id,))
            return view(identity, "确认已过期", banner="没有消耗资源，请重新预览。")
        payload, operation = loads(pending["payload_json"]), pending["operation"]
        member = await self.repo.member(
            session, identity.player_id, payload["member"]["pig_instance_id"], available=True
        )
        keys = ("pig_instance_id", "template_id", "level", "favorite", "size_value", "weight_value")
        if profile["revision"] != payload["revision"] or any(member[k] != payload["member"][k] for k in keys):
            raise BattleError("战斗猪或配置已变化，请重新预览后确认。")
        pig_id = member["pig_instance_id"]
        if operation == "assign":
            await self.repo.protect(session, identity.player_id, identity.scope.value, pig_id)
            await session.execute(
                "UPDATE battle_profiles SET pig_instance_id=?,revision=revision+1 WHERE player_id=?",
                (pig_id, identity.player_id),
            )
        elif operation == "retire":
            await session.execute(
                "UPDATE battle_protections SET protected=0 WHERE pig_instance_id=? AND player_id=?",
                (pig_id, identity.player_id),
            )
            await session.execute(
                "UPDATE battle_profiles SET pig_instance_id=NULL,revision=revision+1 "
                "WHERE player_id=? AND pig_instance_id=?",
                (identity.player_id, pig_id),
            )
        else:
            costs = dict(UPGRADE_COSTS[member["level"]])
            coupons = AchievementCouponRepository()
            selected = await coupons.selected(session, identity.player_id, ("battle-training",))
            if selected != payload.get("coupons", {}):
                raise BattleError("训练成就券已变化，请重新预览。")
            usage = {}
            if selected:
                saving = min(300, costs["coins"])
                usage = await coupons.consume(
                    session,
                    identity.player_id,
                    "battle-training",
                    key,
                    iso_ms(now_ms),
                    expected=selected["battle-training"],
                    effect={"coin_saving": saving},
                )
                costs["coins"] -= saving
            natural = await session.fetch_one(
                """SELECT COALESCE(SUM(delta_units),0) FROM material_ledger
                WHERE player_id=? AND material_id='training-ore' AND delta_units>0
                AND source_kind IN('dispatch-base','dispatch-bonus','dispatch-encounter')""",
                (identity.player_id,),
            )
            await self.repo.spend(session, identity, costs, key, now_ms)
            await session.execute(
                """INSERT INTO battle_training VALUES(?,?) ON CONFLICT(pig_instance_id)
                DO UPDATE SET level=excluded.level""",
                (pig_id, member["level"] + 1),
            )
            await self.repo.protect(session, identity.player_id, identity.scope.value, pig_id)
            await session.execute(
                "INSERT INTO battle_upgrades VALUES(?,?,?,?,?,?,?)",
                (key, identity.player_id, pig_id, member["level"], member["level"] + 1, dumps(costs), now_ms),
            )
            await self.repo.fact(
                session,
                identity.player_id,
                identity.scope.value,
                key,
                "upgrade",
                now_ms,
                {
                    "payer_id": identity.player_id,
                    "pig_instance_id": pig_id,
                    "from_level": member["level"],
                    "to_level": member["level"] + 1,
                    "costs": {MATERIAL_IDS[k]: v for k, v in costs.items()},
                    "natural_ore_units_before": natural[0],
                    "material_scale": MATERIAL_SCALE,
                    "archetype": member["fighter_id"],
                    "achievement_coupon": usage,
                },
            )
            member["level"] += 1
        await session.execute("DELETE FROM battle_pending WHERE player_id=?", (identity.player_id,))
        return view(
            identity,
            {"assign": "战斗猪已设置", "retire": "已解除战斗保护", "upgrade": "战斗强化成功"}[operation],
            pigs=(pig_card(member),),
            banner="操作已完成。强化随实例保留，不会因为转让而让接收者获得本人付费训练记录。"
            + (
                f" {usage['name']}减免{usage['coin_saving']}猪币，剩余{usage['remaining']}张。"
                if operation == "upgrade" and usage
                else ""
            ),
            hints=("/战斗猪 查看当前设置；/比划比划 @群友。",),
        )
