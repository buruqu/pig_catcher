"""巡演应用事务入口：低冲突命令、原子资源变动与可重放图片回执。"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from ..commands.tour import TourRequest
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import Clock, MessageKeyFactory, SystemClock
from ..domain.tour import validate_plan
from ..domain.tour_catalog import TOUR_VERSION, TourError
from ..domain.tour_views import TourView
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories.dispatch import encode, iso_ms, timestamp_ms
from ..infrastructure.repositories.framework import FrameworkRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from ..infrastructure.repositories.tour import TourRepository
from .command_state import validate_existing_receipt
from .dispatch import DispatchService
from .receipts import request_fingerprint
from .tour_joint import TourJoint
from .tour_queries import TourQueries
from .tour_setup import TourSetup

_SETTINGS = frozenset(
    (
        "rename",
        "description",
        "color",
        "emblem",
        "costume",
        "switch",
        "captain",
        "center",
        "theme",
        "route",
        "setlist",
        "highlights",
        "ensemble",
        "tool",
    )
)
_PREVIEWS = frozenset(("roster", "practice", "branch", "retire", "guest", "upgrade", "archive"))
_QUERIES = frozenset(
    (
        "band",
        "roster_view",
        "tour_overview",
        "preview",
        "venues",
        "themes",
        "ensembles",
        "songs",
        "characters",
        "members",
        "equipment",
        "tools",
        "journal",
        "collections",
        "detail",
        "joint_status",
    )
)
_MUTATIONS = (
    _SETTINGS
    | _PREVIEWS
    | frozenset(
        (
            "create",
            "craft",
            "start",
            "continue",
            "all",
            "confirm",
            "cancel",
            "abandon",
            "joint_invite",
            "joint_accept",
            "joint_decline",
            "joint_cancel",
        )
    )
)


@dataclass(frozen=True, slots=True)
class TourResult:
    view: TourView
    receipt: CommandReceipt | None = None


class TourService:
    def __init__(
        self, database: PigCatcherDatabase, *, clock: Clock | None = None, seed_factory: Callable[[], str] | None = None
    ) -> None:
        self.database = database
        self.clock = clock or SystemClock()
        self.seed_factory = seed_factory or (lambda: secrets.token_hex(32))
        self.repository = TourRepository()
        self.framework = FrameworkRepository()
        self.receipts = ReceiptRepository()
        self.queries = TourQueries(self.repository)
        self.setup = TourSetup(self.repository, self.queries)
        self.joint = TourJoint(self.repository, self.setup, self.queries, self.seed_factory)

    async def execute(self, identity: CommandIdentity, request: TourRequest) -> TourResult:
        if request.action not in _QUERIES | _MUTATIONS or not isinstance(request.args, dict):
            raise TourError("未知巡演操作。")
        if "page" in request.args and (
            type(request.args["page"]) is not int or not 1 <= request.args["page"] <= 100000
        ):
            raise TourError("页码为1至100000。")
        now_ms = timestamp_ms(self.clock.now()) // 1000 * 1000
        now, command = iso_ms(now_ms), f"pig-catcher.tour.{request.action}"
        key = MessageKeyFactory.build(identity, command) if request.action in _MUTATIONS else ""
        async with self.database.transaction() as session:
            if key:
                receipt = await self.receipts.get_by_key(session, key)
                if receipt:
                    validate_existing_receipt(
                        receipt, identity=identity, command_name=command, request_payload=request.args
                    )
                    view = TourView.from_payload(json.loads(receipt.result_json)["view"])
                    view = await DispatchService._restrict_media(session, identity, view)
                    return TourResult(view, receipt)
            await self.framework.touch_identity(session, identity=identity, now=now)
            await self.joint.check_ban(session, identity, now_ms)
            await self.joint.expire(session, identity.player_id, now_ms)
            view = await self._perform(session, identity, request, now_ms, key)
            view = await DispatchService._restrict_media(session, identity, view)
            if not key:
                return TourResult(view)
            reservation = await self.receipts.reserve(
                session,
                idempotency_key=key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command,
                request_fingerprint=request_fingerprint(request.args),
                result_type="tour",
                result_object_id=key,
                result_json=encode({"version": TOUR_VERSION, "action": request.action, "view": view.payload()}),
                text_summary=view.text(),
                now=now,
                catch_quota_cost=0,
            )
            return TourResult(view, reservation.receipt)

    async def _perform(
        self, session: DatabaseSession, identity: CommandIdentity, request: TourRequest, now_ms: int, key: str
    ) -> TourView:
        action, args = request.action, request.args
        if action.startswith("joint_"):
            return await self.joint.execute(session, identity, action, args, now_ms)
        if action == "band":
            return await self.queries.band(session, identity, now_ms)
        if action == "roster_view":
            if type(args.get("slot")) is not int or not 1 <= args["slot"] <= 3:
                raise TourError("阵容编号为1至3。")
            return await self.queries.band(session, identity, now_ms, slot=args["slot"])
        if action in {"venues", "themes", "ensembles", "songs", "characters"}:
            return await self.queries.catalog(session, identity, action, args.get("page", 1), now_ms)
        if action == "members":
            return await self.queries.members(session, identity, args.get("page", 1), now_ms)
        if action in {"journal", "collections"}:
            return await self.queries.journal(
                session, identity, args.get("page", 1), now_ms, collections=action == "collections"
            )
        if action == "detail":
            return await self.queries.detail(session, identity, args["run_id"], now_ms)
        if action == "create":
            name = args.get("value", "")
            if not isinstance(name, str) or not name.strip() or len(name) > 32 or any(ord(c) < 32 for c in name):
                raise TourError("乐队名需要1至32个字符，不能包含控制字符。")
            await self.repository.create(session, identity.player_id, identity.scope.value, name.strip(), now_ms)
            return await self.queries.band(session, identity, now_ms)
        if action == "cancel":
            deleted = await session.execute("DELETE FROM tour_pending WHERE player_id=?", (identity.player_id,))
            return self.queries.view(
                identity,
                "已取消确认" if deleted.rowcount else "没有待确认操作",
                banner="未消耗资源，已经演出的站点不受影响。联演邀请请使用 /巡演联演 取消。",
            )
        if action == "confirm":
            return await self._confirm(session, identity, now_ms, key)
        if action == "tour_overview":
            return await self.queries.overview(session, identity, now_ms)
        if action in {"equipment", "tools"}:
            return await self.queries.equipment(session, identity, now_ms, tools=action == "tools")
        profile = await self.repository.profile(session, identity.player_id, now_ms)
        if action in _SETTINGS:
            return await self.setup.settings(session, identity, profile, action, args, now_ms)
        if action in _PREVIEWS:
            return await self.setup.preview(session, identity, profile, action, args, now_ms)
        if action == "craft":
            return await self.setup.craft(session, identity, profile, args, now_ms, key)
        if action == "preview":
            roster, members = await self.repository.roster(session, profile)
            for plan in json.loads(profile["plans_json"]):
                validate_plan(plan, members, fans=profile["fans"])
            return await self.queries.preview(session, identity, profile, roster, members)
        if action in {"start", "all"} and not await self.repository.active_run(session, identity.player_id):
            snapshot = await self.setup.ready(session, profile)
            snapshot["all"] = action == "all"
            await self.setup.pending(session, identity.player_id, "start", snapshot, now_ms)
            view = await self.queries.preview(
                session, identity, profile, snapshot["roster"], snapshot["members"], confirmation=True
            )
            if action == "all":
                from dataclasses import replace

                view = replace(view, banner=view.banner + " 本次确认将直接完成全部三站。")
            return view
        if action in {"continue", "all"}:
            return await self._play(session, identity, profile, now_ms, all_remaining=action == "all")
        if action == "start":
            raise TourError("已有进行中的巡演，请使用 /巡演继续 或 /巡演一键。")
        if action == "abandon":
            run = await self.repository.active_run(session, identity.player_id)
            if not run:
                raise TourError("没有进行中的巡演。")
            await self.setup.pending(
                session,
                identity.player_id,
                "abandon",
                {"run_id": run["run_id"], "stage_count": run["stage_count"]},
                now_ms,
            )
            return self.queries.view(
                identity,
                "提前落幕 · 请确认",
                profile,
                banner=(
                    f"结束 {run['run_id']}，已完成 {run['stage_count']}/3 站。"
                    "档期不退、没有整趟粉丝/猪币；已获得的经验与照片保留。"
                ),
                hints=("2分钟内 /猪猪巡演 确认；/猪猪巡演 取消",),
            )
        raise TourError("未知巡演操作。")

    async def _confirm(self, session: DatabaseSession, identity: CommandIdentity, now_ms: int, key: str) -> TourView:
        row = await session.fetch_one("SELECT * FROM tour_pending WHERE player_id=?", (identity.player_id,))
        if not row:
            raise TourError("没有待确认操作。请先编队、练习、出发等，再在两分钟内确认。")
        if row["expires_ms"] <= now_ms:
            await session.execute("DELETE FROM tour_pending WHERE player_id=?", (identity.player_id,))
            return self.queries.view(identity, "确认已过期", banner="已超过两分钟，没有消耗资源，请重新预览。")
        profile = await self.repository.profile(session, identity.player_id, now_ms)
        payload = json.loads(row["payload_json"])
        action = row["operation"]
        if action == "start":
            snapshot = await self.setup.ready(session, profile)
            if not self.setup.same_ready(payload, snapshot):
                raise TourError("阵容、编排或成长已改变，请重新预览后出发。")
            run = await self.repository.start(
                session, profile, snapshot["roster"], snapshot["members"], seed=self.seed_factory(), now_ms=now_ms
            )
            if payload["all"]:
                view = await self._play(session, identity, profile, now_ms, all_remaining=True)
            else:
                view = self.queries.view(
                    identity,
                    "巡演出发 · 舞台已就绪",
                    profile,
                    banner=(
                        f"巡演 {run['run_id']} 已扣1张档期，剩余{profile['tickets'] - 1}张。"
                        "尚未演出，可随时继续；站间不占用猪猪。"
                    ),
                    panels=(
                        Panel(
                            "下一步",
                            (
                                Line("逐站", "/巡演继续", "自动演奏本站三首曲目"),
                                Line("一键", "/巡演一键", "相同规则完成剩余站点"),
                            ),
                        ),
                    ),
                )
        elif action == "abandon":
            run = await self.repository.active_run(session, identity.player_id)
            if not run or run["run_id"] != payload["run_id"] or run["stage_count"] != payload["stage_count"]:
                raise TourError("巡演状态已变化，请重新确认是否结束。")
            await session.execute(
                "UPDATE tour_runs SET status='abandoned',completed_ms=? WHERE run_id=?", (now_ms, run["run_id"])
            )
            await self.repository.fact(
                session,
                identity.player_id,
                identity.scope.value,
                run["run_id"],
                "abandoned",
                now_ms,
                {"stage_count": run["stage_count"], "ticket_refund": 0},
            )
            view = self.queries.view(
                identity, "巡演已提前落幕", profile, banner="没有整趟奖励，不退档期；已完成站点和成长仍可在游记查看。"
            )
        else:
            view = await self.setup.confirm(session, identity, profile, action, payload, now_ms, key)
        await session.execute("DELETE FROM tour_pending WHERE player_id=?", (identity.player_id,))
        return view

    async def _play(
        self, session: DatabaseSession, identity: CommandIdentity, profile: dict, now_ms: int, *, all_remaining: bool
    ) -> TourView:
        run = await self.repository.active_run(session, identity.player_id)
        if not run:
            raise TourError("没有进行中的巡演。请先 /猪猪巡演 出发。")
        iterations = 3 - run["stage_count"] if all_remaining else 1
        for _ in range(iterations):
            stage = await self.repository.play_stage(session, profile, run, now_ms=now_ms)
            run = await self.repository.active_run(session, identity.player_id)
        if run["stage_count"] == 3:
            summary = await self.repository.finish(session, profile, run["run_id"], now_ms=now_ms)
            return self.queries.summary(identity, profile, summary)
        return self.queries.stage(identity, profile, stage)
