"""Explicit image-first reward bag, coupon selection and material choice confirmation."""

from __future__ import annotations

import json
from dataclasses import replace

from ..domain.activity_achievements import ACTIVITY_REWARDS
from ..domain.dispatch import MATERIAL_SCALE, MATERIALS, material_id, safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchView
from ..domain.errors import DomainValidationError
from ..domain.ports import MessageKeyFactory
from ..infrastructure.repositories.achievement_coupons import AchievementCouponRepository
from ..infrastructure.repositories.achievements import AchievementRepository
from ..infrastructure.repositories.dispatch import encode, iso_ms, timestamp_ms
from ..infrastructure.repositories.framework import FrameworkRepository
from ..infrastructure.repositories.materials import MaterialRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from ..infrastructure.repositories.tour import TourRepository
from .command_state import validate_existing_receipt
from .dispatch import DispatchResult
from .receipts import request_fingerprint

HELP = (
    "/成就奖励 [页码]：查看奖励和已选券。",
    "/成就奖励 材料 基础材料自选份 训练矿石 10 → /成就奖励 确认（30秒内）。",
    "训练材料自选份只能选矿石、零件、纤维；基础材料还可选舞台组件。每份只换1份材料。",
    "/使用成就券 口袋行李券；/成就奖励 停用 口袋行李券；/成就奖励 取消（仅取消材料确认）。",
    "玩法券每次只选一张；路费/行李互斥，视觉券独立。预览不扣券，实际出发/演出/强化/应战才扣。",
    "例外：巡演档期券使用即补1张档期，需已有乐队，满7张不扣券；旧成就券以激活回执为准。",
    "完整券与材料规则：/抓猪帮助 奖励；道具连续使用：/抓猪帮助 道具。",
)


class AchievementRewardService:
    def __init__(self, achievement_service):
        self.achievements = achievement_service
        self.database = achievement_service.database
        self.clock = achievement_service.clock
        self.coupons = AchievementCouponRepository()
        self.repo = AchievementRepository()

    async def execute(self, identity, text: str) -> DispatchResult:
        words = text.strip().split()
        action = words[0] if words else "查看"
        query = action == "查看" or action.isdecimal()
        now_ms = timestamp_ms(self.clock.now())
        now = iso_ms(now_ms)
        command = "pig-catcher.achievement-rewards"
        key = MessageKeyFactory.build(identity, command) if not query else ""
        receipts = ReceiptRepository()
        async with self.database.transaction() as session:
            if key:
                old = await receipts.get_by_key(session, key)
                if old:
                    validate_existing_receipt(
                        old, identity=identity, command_name=command, request_payload={"text": text}
                    )
                    return DispatchResult(DispatchView.from_payload(json.loads(old.result_json)["view"]), old)
            await FrameworkRepository().touch_identity(session, identity=identity, now=now)
            banner = "不可赠送或交易；库存和使用记录按群独立。"
            if action == "材料":
                if len(words) != 4:
                    raise DomainValidationError(HELP[1])
                chest = next(
                    (
                        key
                        for key in ("materials-choice", "training-choice")
                        if words[1] in {key, ACTIVITY_REWARDS[key]["name"]}
                    ),
                    None,
                )
                if chest is None:
                    raise DomainValidationError("请选择基础材料自选份或训练材料自选份。")
                material = material_id(words[2])
                if not words[3].isascii() or not words[3].isdecimal() or len(words[3]) > 5:
                    raise DomainValidationError("兑换数量须为1至10000的整数。")
                quantity = int(words[3])
                if not 1 <= quantity <= 10000 or material not in ACTIVITY_REWARDS[chest]["choices"]:
                    raise DomainValidationError("数量或材料不属于这个自选奖励的范围。")
                await self.check_chest(session, identity.player_id, chest, quantity)
                await session.execute(
                    "INSERT INTO achievement_material_choices VALUES(?,?,?,?,?) ON CONFLICT(player_id) "
                    "DO UPDATE SET chest_id=excluded.chest_id,material_id=excluded.material_id,"
                    "quantity=excluded.quantity,expires_ms=excluded.expires_ms",
                    (identity.player_id, chest, material, quantity, now_ms + 30_000),
                )
                banner = (
                    f"消耗{ACTIVITY_REWARDS[chest]['name']}×{quantity}，仅兑换{MATERIALS[material]}×{quantity}。"
                    "30秒内 /成就奖励 确认；未确认不扣。"
                )
            elif action == "确认":
                row = await session.fetch_one(
                    "SELECT * FROM achievement_material_choices WHERE player_id=?", (identity.player_id,)
                )
                if row is None or row["expires_ms"] <= now_ms:
                    raise DomainValidationError("没有有效的材料确认；30秒后自动失效，请重新选择。")
                if not await self.repo.consume_reward(
                    session,
                    player_id=identity.player_id,
                    reward_type="chest",
                    reward_id=row["chest_id"],
                    quantity=row["quantity"],
                    now=now,
                ):
                    raise DomainValidationError("自选份库存不足，未兑换任何材料。")
                await MaterialRepository().change(
                    session,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                    material_id=row["material_id"],
                    delta_units=row["quantity"] * MATERIAL_SCALE,
                    source_kind="achievement-choice",
                    source_id=key,
                    entry_key=key,
                    now=now,
                )
                await session.execute(
                    "DELETE FROM achievement_material_choices WHERE player_id=?", (identity.player_id,)
                )
                banner = (
                    f"兑换完成：{MATERIALS[row['material_id']]}×{row['quantity']}。"
                    "不是派遣自然产出，不推进自然材料成就。"
                )
            elif action == "取消":
                await session.execute(
                    "DELETE FROM achievement_material_choices WHERE player_id=?", (identity.player_id,)
                )
                banner = "材料确认已取消，没有消耗库存。"
            elif action in {"使用", "停用"}:
                if len(words) != 2:
                    raise DomainValidationError("请在使用/停用后填写完整券名。")
                ticket = next(
                    (
                        key
                        for key, item in ACTIVITY_REWARDS.items()
                        if item["kind"] == "ticket" and words[1] in {key, item["name"]}
                    ),
                    None,
                )
                if ticket is None:
                    raise DomainValidationError("这不是三系统成就券，请 /成就奖励 查看库存。")
                if action == "停用":
                    await session.execute(
                        "DELETE FROM achievement_coupon_selection WHERE player_id=? AND ticket_id=?",
                        (identity.player_id, ticket),
                    )
                    banner = "已取消待命选择，没有消耗成就券。"
                elif ticket == "tour-date":
                    tour = TourRepository()
                    profile = await session.fetch_one(
                        "SELECT * FROM tour_profiles WHERE player_id=?", (identity.player_id,)
                    )
                    if not profile:
                        raise DomainValidationError("请先创建乐队，再使用巡演档期券。")
                    await tour.profile(session, identity.player_id, now_ms)
                    current = await session.fetch_one(
                        "SELECT tickets FROM tour_profiles WHERE player_id=?", (identity.player_id,)
                    )
                    if current[0] >= 7:
                        raise DomainValidationError("档期已达7张，成就券未消耗。")
                    if not await self.repo.consume_reward(
                        session,
                        player_id=identity.player_id,
                        reward_type="ticket",
                        reward_id=ticket,
                        quantity=1,
                        now=now,
                    ):
                        raise DomainValidationError("你没有巡演档期券。")
                    await tour.ticket_change(
                        session, identity.player_id, 1, key=key, reason="achievement-coupon", source=key, now_ms=now_ms
                    )
                    remaining = await session.fetch_one(
                        "SELECT quantity FROM achievement_reward_inventory "
                        "WHERE player_id=? AND reward_type='ticket' AND reward_id=?",
                        (identity.player_id, ticket),
                    )
                    effect = {"name": ACTIVITY_REWARDS[ticket]["name"], "remaining": remaining[0], "tickets_added": 1}
                    await session.execute(
                        "INSERT INTO achievement_coupon_uses VALUES(?,?,?,?,?,?,?)",
                        (key, identity.player_id, ticket, "tour-date", key, encode(effect), now),
                    )
                    banner = f"已增加1张档期，当前{current[0] + 1}/7。巡演档期券剩余{remaining[0]}张。"
                else:
                    await self.coupons.select(session, identity.player_id, ticket, now)
                    banner = (
                        f"已选择{ACTIVITY_REWARDS[ticket]['name']}，尚未扣券。" + ACTIVITY_REWARDS[ticket]["effect"]
                    )
            elif not query:
                raise DomainValidationError("未知奖励操作。" + "\n".join(HELP))
            view = await self.bag(session, identity, int(action) if action.isdecimal() and len(action) < 6 else 1)
            view = replace(view, banner=banner)
            if not key:
                return DispatchResult(view)
            reserved = await receipts.reserve(
                session,
                idempotency_key=key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command,
                request_fingerprint=request_fingerprint({"text": text}),
                result_type="achievement-reward",
                result_object_id=key,
                result_json=encode({"view": view.payload()}),
                text_summary=view.text(),
                now=now,
                catch_quota_cost=0,
            )
            return DispatchResult(view, reserved.receipt)

    async def check_chest(self, session, player_id, chest, quantity):
        row = await session.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory "
            "WHERE player_id=? AND reward_type='chest' AND reward_id=?",
            (player_id, chest),
        )
        if not row or row[0] < quantity:
            raise DomainValidationError("自选材料份数不足。")

    async def bag(self, session, identity, page: int):
        rows = await self.repo.reward_rows(session, player_id=identity.player_id)
        rows = [r for r in rows if r["reward_id"] in ACTIVITY_REWARDS and r["quantity"] > 0]
        count = max(1, (len(rows) + 7) // 8)
        page = max(1, min(page, count))
        selected = await self.coupons.selected(session, identity.player_id)
        panels = (
            Panel(
                "已选活动券",
                tuple(
                    Line(
                        ACTIVITY_REWARDS[v["ticket_id"]]["name"],
                        "待下一次适用操作",
                        ACTIVITY_REWARDS[v["ticket_id"]]["effect"],
                    )
                    for v in selected.values()
                ),
            ),
            Panel(
                "奖励库存",
                tuple(
                    Line(
                        ACTIVITY_REWARDS[r["reward_id"]]["name"],
                        f"×{r['quantity']}",
                        ACTIVITY_REWARDS[r["reward_id"]].get(
                            "effect", "纯外观" if r["reward_type"] in {"title", "frame", "badge"} else "1份兑换1份材料"
                        ),
                    )
                    for r in rows[(page - 1) * 8 : page * 8]
                ),
            ),
        )
        return DispatchView(
            "成就奖励行李箱",
            safe_display_name(identity.display_name, identity.user_id),
            subtitle="PiG Dream! · 三章同行",
            presentation="item-bag",
            panels=panels,
            hints=HELP,
            page=page,
            page_count=count,
        )
