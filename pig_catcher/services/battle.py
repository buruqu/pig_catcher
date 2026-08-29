"""双人对战应用服务：一个事务提交状态、随机事实、额度和图片收据。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from uuid import uuid4

from ..commands.battle import BattleRequest
from ..config.model import CatchingSection
from ..domain.battle import dumps, loads, mark_ready, new_state, play_chunk, resolve_round, roll_count
from ..domain.battle_catalog import ACTION_TTL_MS, BATTLE_VERSION, INVITE_COOLDOWN_MS, INVITE_TTL_MS, BattleError
from ..domain.battle_views import BattleView
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.models import CommandIdentity, CommandReceipt
from ..domain.ports import MessageKeyFactory, SystemClock
from ..infrastructure.repositories.battle import BattleRepository, beijing_day
from ..infrastructure.repositories.dispatch import iso_ms, timestamp_ms
from ..infrastructure.repositories.framework import FrameworkRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from ..infrastructure.repositories.restrictions import (
    GIFT_TRANSFER_BAN,
    PLUGIN_ACCESS_BAN,
    TRADE_BAN,
    RestrictionRepository,
)
from .battle_setup import BattleSetup
from .battle_views import STATUS_NAMES, matchup, move_line, view, wheels
from .command_state import validate_existing_receipt
from .dispatch import DispatchService
from .receipts import request_fingerprint

QUERIES = frozenset(("profile", "tools", "wheels", "status", "history", "detail"))
AUTO_LOOT_COMMAND = "pig-catcher.catch"
AUTO_LOOT_REQUEST = {"command_version": 1}
SETUP = frozenset(
    (
        "profile",
        "tools",
        "equip",
        "craft",
        "assign_preview",
        "retire_preview",
        "upgrade_preview",
        "confirm",
        "cancel_setup",
    )
)
MUTATIONS = SETUP - QUERIES | frozenset(
    (
        "invite",
        "accept",
        "decline",
        "cancel_invite",
        "surrender_preview",
        "surrender_confirm",
        "count",
        "move",
        "ready",
        "loot",
    )
)


@dataclass(frozen=True, slots=True)
class BattleResult:
    view: BattleView
    receipt: CommandReceipt | None = None


class BattleService:
    def __init__(
        self,
        database,
        *,
        clock=None,
        seed_factory=None,
        catching=None,
        regulation_service=None,
        access_policy_factory=None,
    ):
        self.database = database
        self.clock = clock or SystemClock()
        self.seed_factory = seed_factory or (lambda: secrets.token_hex(32))
        self.catching = catching or CatchingSection()
        self.regulation = regulation_service
        self.access_policy_factory = access_policy_factory
        self.repo, self.framework, self.receipts = BattleRepository(), FrameworkRepository(), ReceiptRepository()
        self.setup = BattleSetup(self.repo)

    async def execute(self, identity: CommandIdentity, request: BattleRequest) -> BattleResult:
        if request.action not in QUERIES | MUTATIONS or not isinstance(request.args, dict):
            raise BattleError("未知对战操作。")
        for name in ("page", "round"):
            if name in request.args and (type(request.args[name]) is not int or not 1 <= request.args[name] <= 100000):
                raise BattleError("页码和回合应为1至100000。")
        now_ms = timestamp_ms(self.clock.now()) // 1000 * 1000
        now, command = iso_ms(now_ms), f"pig-catcher.battle.{request.action}"
        key = MessageKeyFactory.build(identity, command) if request.action in MUTATIONS else ""
        async with self.database.transaction() as session:
            if key:
                old = await self.receipts.get_by_key(session, key)
                if old:
                    validate_existing_receipt(
                        old, identity=identity, command_name=command, request_payload=request.args
                    )
                    result_view = await DispatchService._restrict_media(
                        session, identity, BattleView.from_payload(loads(old.result_json)["view"])
                    )
                    return BattleResult(result_view, old)
            await self.framework.touch_identity(session, identity=identity, now=now)
            scope = await session.fetch_one("SELECT enabled FROM scopes WHERE scope_id=?", (identity.scope.value,))
            if not scope or not scope[0]:
                raise BattleError("本群玩法已关闭。")
            await self.check_participants(session, identity.scope.value, [identity.player_id], now_ms, social=False)
            result_view = await self._perform(session, identity, request.action, request.args, now_ms, key)
            result_view = await DispatchService._restrict_media(session, identity, result_view)
            if not key:
                return BattleResult(result_view)
            receipt = await self.receipts.reserve(
                session,
                idempotency_key=key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command,
                request_fingerprint=request_fingerprint(request.args),
                result_type="battle",
                result_object_id=result_view.battle_id or key,
                result_json=dumps({"version": BATTLE_VERSION, "action": request.action, "view": result_view.payload()}),
                text_summary=result_view.text(),
                now=now,
                catch_quota_cost=0,
            )
            return BattleResult(result_view, receipt.receipt)

    async def execute_pending_loot(self, identity: CommandIdentity) -> BattleResult | None:
        """Route a normal catch to the oldest pending battle reward, if one exists.

        The command name and payload deliberately match ``GameplayService.catch`` so one
        source message can commit either a normal pig or a battle reward, never both.
        ``/战利品抓猪`` remains a compatible explicit alias with its own command receipt.
        """

        now_ms = timestamp_ms(self.clock.now()) // 1000 * 1000
        now = iso_ms(now_ms)
        key = MessageKeyFactory.build(identity, AUTO_LOOT_COMMAND)
        async with self.database.transaction() as session:
            old = await self.receipts.get_by_key(session, key)
            if old:
                validate_existing_receipt(
                    old,
                    identity=identity,
                    command_name=AUTO_LOOT_COMMAND,
                    request_payload=AUTO_LOOT_REQUEST,
                )
                # A previously committed ordinary catch must still be replayed by the
                # ordinary service even if a battle finishes before the duplicate arrives.
                if old.result_type != "battle":
                    return None
                payload = loads(old.result_json)
                if payload.get("action") != "loot":
                    raise BattleError("抓猪回执中的战利品结果类型无效。")
                result_view = await DispatchService._restrict_media(
                    session,
                    identity,
                    BattleView.from_payload(payload["view"]),
                )
                return BattleResult(result_view, old)
            if not await self.repo.has_pending_loot(session, identity.player_id, identity.scope.value):
                return None
            await self.framework.touch_identity(session, identity=identity, now=now)
            scope = await session.fetch_one("SELECT enabled FROM scopes WHERE scope_id=?", (identity.scope.value,))
            if not scope or not scope[0]:
                raise BattleError("本群玩法已关闭。")
            from .battle_loot import claim_loot

            result_view = await claim_loot(self, session, identity, now_ms, key)
            result_view = await DispatchService._restrict_media(session, identity, result_view)
            receipt = await self.receipts.reserve(
                session,
                idempotency_key=key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=AUTO_LOOT_COMMAND,
                request_fingerprint=request_fingerprint(AUTO_LOOT_REQUEST),
                result_type="battle",
                result_object_id=result_view.battle_id or key,
                result_json=dumps({"version": BATTLE_VERSION, "action": "loot", "view": result_view.payload()}),
                text_summary=result_view.text(),
                now=now,
                catch_quota_cost=0,
            )
            return BattleResult(result_view, receipt.receipt)

    async def check_participants(self, session, scope_id, ids, now_ms, *, social=True):
        # 接收者也必须通过WebUI配置的黑白名单，不能只检查当前命令发送人。
        policy = self.access_policy_factory() if self.access_policy_factory else None
        if policy:
            for pid in ids:
                row = await session.fetch_one(
                    "SELECT platform_user_id FROM players WHERE player_id=? AND scope_id=?", (pid, scope_id)
                )
                if not row or not policy.evaluate(group_id=scope_id.split(":", 1)[1], user_id=row[0]).allowed:
                    raise BattleError("参与方未通过插件配置的黑白名单，已有战利品次数保留。")
        restriction_types = (PLUGIN_ACCESS_BAN, GIFT_TRANSFER_BAN, TRADE_BAN) if social else (PLUGIN_ACCESS_BAN,)
        for restriction in restriction_types:
            if await RestrictionRepository().active_restrictions_for_players(
                session, player_ids=ids, restriction_type=restriction, now=iso_ms(now_ms)
            ):
                raise BattleError("参与方存在插件或赠送/收赠/交易限制，不能发起、应战或交付战利品；已有剩余次数保留。")
        if self.regulation:
            # 延续插件管理员不受自动监管的规则；手工设置的黑名单仍在上面正常检查。
            admin_ids = self.regulation.admin_user_ids
            platform = scope_id.split(":", 1)[0]
            regulated_ids = [
                pid
                for pid in ids
                if pid.rsplit(":", 1)[1] not in admin_ids and f"{platform}:{pid.rsplit(':', 1)[1]}" not in admin_ids
            ]
            if not regulated_ids:
                return
            hold = await self.regulation.current_hold(
                session,
                scope_id=scope_id,
                player_ids=regulated_ids,
                hold_types=("plugin", "social") if social else ("plugin",),
                now=iso_ms(now_ms),
            )
            if hold:
                raise BattleError(hold.public_message)

    async def check_quota(self, session, ids, now_ms):
        day = beijing_day(now_ms)
        for pid, role in zip(ids, ("initiator", "opponent"), strict=True):
            if await self.repo.quota_used(session, pid, day, role):
                raise BattleError("发起方今日主动次数或应战方今日被挑战次数已用完；北京时间00:00刷新。")

    async def _perform(self, session, identity, action, args, now_ms, key):
        if action in SETUP:
            return await self.setup.perform(session, identity, action, args, now_ms, key)
        if action == "wheels":
            fighter_id = args.get("fighter_id", "sukuna")
            from ..domain.battle_catalog import FIGHTERS_BY_ID

            if fighter_id not in FIGHTERS_BY_ID:
                raise BattleError("未知战斗盘。")
            profile = await self.repo.profile(session, identity.player_id)
            member = (
                await self.repo.member(session, identity.player_id, profile["pig_instance_id"])
                if profile["pig_instance_id"]
                else None
            )
            return wheels(identity, fighter_id, member["level"] if member and member["fighter_id"] == fighter_id else 0)
        if action in {"history", "detail"}:
            return await self.history(session, identity, action, args, now_ms)
        if action == "loot":
            from .battle_loot import claim_loot

            return await claim_loot(self, session, identity, now_ms, key)
        match = await self.repo.active(session, identity.scope.value)
        if action == "status":
            if not match:
                row = await session.fetch_one(
                    "SELECT * FROM battle_matches WHERE scope_id=? ORDER BY sequence DESC LIMIT 1",
                    (identity.scope.value,),
                )
                match = dict(row) if row else None
            return (
                matchup(identity, match, loads(match["state_json"]), now_ms)
                if match
                else view(identity, "本群尚无对战", banner="先 /战斗猪 设置 宿傩猪 或五条猪，再 /比划比划 @群友。")
            )
        if action == "invite":
            if match:
                raise BattleError("本群已有等待接受或进行中的对战，请先结束当前一场。")
            target = args.get("target_user_id")
            row = await session.fetch_one(
                "SELECT player_id FROM players WHERE scope_id=? AND platform_user_id=?", (identity.scope.value, target)
            )
            if not row or row[0] == identity.player_id:
                raise BattleError("请 @ 本群另一位已设置战斗猪的玩家，不能挑战自己。")
            ids = [identity.player_id, row[0]]
            await self.check_participants(session, identity.scope.value, ids, now_ms)
            await self.check_quota(session, ids, now_ms)
            profile = await self.repo.profile(session, identity.player_id)
            if profile["last_invite_ms"] and now_ms - profile["last_invite_ms"] < INVITE_COOLDOWN_MS:
                raise BattleError("两次成功发出邀请至少间隔60秒，避免反复打扰。")
            snapshots = [await self.repo.snapshot(session, pid, identity.scope.value, now_ms) for pid in ids]
            state = new_state(snapshots)
            state["status"] = "pending"
            for side_state in state["sides"]:
                side_state["tool_used"] = False
            battle_id = "B" + uuid4().hex[:12].upper()
            await session.execute(
                """INSERT INTO battle_matches(battle_id,scope_id,initiator_id,opponent_id,status,
                definition_version,random_seed,state_json,invitation_json,expires_ms,created_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    battle_id,
                    identity.scope.value,
                    *ids,
                    "pending",
                    BATTLE_VERSION,
                    self.seed_factory(),
                    dumps(state),
                    dumps([self.repo.fingerprint(snap) for snap in snapshots]),
                    now_ms + INVITE_TTL_MS,
                    now_ms,
                ),
            )
            await session.execute(
                "UPDATE battle_profiles SET last_invite_ms=? WHERE player_id=?", (now_ms, identity.player_id)
            )
            match = await self.repo.active(session, identity.scope.value)
            for pid in ids:
                await self.repo.fact(
                    session,
                    pid,
                    identity.scope.value,
                    battle_id,
                    "invited",
                    now_ms,
                    {"initiator_id": ids[0], "opponent_id": ids[1], "snapshots": snapshots, "quota_cost": 0},
                )
            return matchup(identity, match, state, now_ms)
        if not match:
            raise BattleError("没有等待接受或进行中的对战；可能已经结束或超时。")
        ids = [match["initiator_id"], match["opponent_id"]]
        if identity.player_id not in ids:
            raise BattleError("只有本场双方可以执行此操作；其他群友可用 /对战状态 观战。")
        side = ids.index(identity.player_id)
        state = loads(match["state_json"])
        if match["definition_version"] != BATTLE_VERSION:
            raise BattleError("当前对战规则版本不可恢复，保留现场等待维护。")
        if action in {"accept", "decline", "cancel_invite"}:
            if match["status"] != "pending":
                raise BattleError("邀请已处理，不能重复接受、拒绝或取消。")
            if side != (0 if action == "cancel_invite" else 1):
                raise BattleError("接受/拒绝只能由受邀者操作，取消只能由发起者操作。")
            if action != "accept":
                status = "declined" if action == "decline" else "cancelled"
                await self.repo.finish(session, match, state, status, now_ms)
                match["status"] = status
                return matchup(identity, match, state, now_ms, banner="未消耗双方每日额度，也没有发放战利品。")
            await self.check_participants(session, identity.scope.value, ids, now_ms)
            await self.check_quota(session, ids, now_ms)
            snapshots = [await self.repo.snapshot(session, pid, identity.scope.value, now_ms) for pid in ids]
            if [self.repo.fingerprint(snap) for snap in snapshots] != loads(match["invitation_json"]):
                raise BattleError("邀请后战斗猪、强化或器具设置发生变化，请取消后重新邀请。")
            from ..infrastructure.repositories.achievement_coupons import AchievementCouponRepository

            for snap in snapshots:
                snap["achievement_entry"] = await AchievementCouponRepository().consume(
                    session, snap["player_id"], "battle-visual", match["battle_id"], iso_ms(now_ms)
                )
            state = new_state(snapshots)
            day = beijing_day(now_ms)
            await session.execute(
                "UPDATE battle_matches SET status='active',state_json=?,accepted_day=?,expires_ms=? WHERE battle_id=?",
                (dumps(state), day, now_ms + ACTION_TTL_MS, match["battle_id"]),
            )
            for index, (pid, snap, role) in enumerate(zip(ids, snapshots, ("initiator", "opponent"), strict=True)):
                await self.repo.record_quota_use(
                    session,
                    player_id=pid,
                    scope_id=identity.scope.value,
                    day=day,
                    role=role,
                    battle_id=match["battle_id"],
                    now_ms=now_ms,
                )
                await session.execute(
                    "INSERT INTO asset_occupancies VALUES(?,?,?,'battle',?,?,?)",
                    (
                        snap["pig_instance_id"],
                        pid,
                        identity.scope.value,
                        match["battle_id"],
                        now_ms + ACTION_TTL_MS,
                        iso_ms(now_ms),
                    ),
                )
                if snap["tool_id"]:
                    await self.repo.tool_change(
                        session,
                        pid,
                        snap["tool_id"],
                        -1,
                        reason="entry-reserve",
                        source=match["battle_id"],
                        key=f"battle-entry:{match['battle_id']}:{pid}",
                        now_ms=now_ms,
                    )
                await self.repo.fact(
                    session,
                    pid,
                    identity.scope.value,
                    match["battle_id"],
                    "accepted",
                    now_ms,
                    {"role": role, "side": index, "day": day, "snapshot": snap, "quota_cost": 1, "initial_weight": 5},
                )
            match.update(status="active", expires_ms=now_ms + ACTION_TTL_MS)
            entry_banner = "双方累计胜利权重各为5；已扣今日对应角色次数，战斗猪已锁定。"
            confetti_names = [snap["player_name"] for snap in snapshots if snap["tool_id"] == "confetti"]
            if confetti_names:
                entry_banner += " " + "、".join(confetti_names) + "洒下了入场彩纸！"
            return matchup(
                identity,
                match,
                state,
                now_ms,
                title="双方入场 · 比划开始",
                banner=entry_banner,
            )
        if match["status"] != "active":
            raise BattleError("请等待受邀者接受挑战。")
        if action == "surrender_preview":
            await self.setup.pending(
                session, identity.player_id, "surrender", {"battle_id": match["battle_id"]}, now_ms
            )
            return matchup(
                identity,
                match,
                state,
                now_ms,
                title="认输 · 请确认",
                banner="确认认输将结束本场；不退每日次数、不发五次战利品。两分钟内 /比划比划 确认认输。",
            )
        if action == "surrender_confirm":
            pending = await session.fetch_one("SELECT * FROM battle_pending WHERE player_id=?", (identity.player_id,))
            if (
                not pending
                or pending["operation"] != "surrender"
                or pending["expires_ms"] <= now_ms
                or loads(pending["payload_json"])["battle_id"] != match["battle_id"]
            ):
                raise BattleError("认输确认不存在、过期或不属于本场，请先 /比划比划 认输。")
            state["winner"] = 1 - side
            await self.repo.finish(session, match, state, "surrendered", now_ms, winner_id=ids[1 - side])
            await session.execute("DELETE FROM battle_pending WHERE player_id=?", (identity.player_id,))
            match["status"] = "surrendered"
            return matchup(
                identity, match, state, now_ms, banner="已认输。本场不记自然力竭胜场，不发战利品；未触发器具退回。"
            )
        await self.check_participants(session, identity.scope.value, ids, now_ms, social=False)
        events = []
        round_number = state["round"]
        summary = None
        if action == "count":
            result = roll_count(state, side, match["random_seed"])
            changed = result.pop("changed")
            if changed:
                await self.repo.fact(
                    session,
                    identity.player_id,
                    identity.scope.value,
                    match["battle_id"],
                    f"count:{round_number}",
                    now_ms,
                    result,
                )
        elif action == "move":
            events = play_chunk(state, side, match["random_seed"])
            changed = bool(events)
            for event in events:
                await session.execute(
                    "INSERT INTO battle_moves VALUES(?,?,?,?,?,?)",
                    (match["battle_id"], round_number, side, event["ordinal"], dumps(event), now_ms),
                )
                await self.repo.fact(
                    session,
                    identity.player_id,
                    identity.scope.value,
                    match["battle_id"],
                    f"move:{round_number}:{event['ordinal']}",
                    now_ms,
                    event,
                )
        elif action == "ready":
            result = mark_ready(state, side)
            changed = bool(result.pop("changed"))
            if changed:
                await self.repo.fact(
                    session,
                    identity.player_id,
                    identity.scope.value,
                    match["battle_id"],
                    f"ready:{round_number}",
                    now_ms,
                    result,
                )
                summary = resolve_round(state, match["random_seed"])
        else:
            raise BattleError("未知对战操作。")
        if summary:
            await session.execute(
                "INSERT INTO battle_rounds VALUES(?,?,?,?)", (match["battle_id"], round_number, dumps(summary), now_ms)
            )
            for index, pid in enumerate(ids):
                await self.repo.fact(
                    session,
                    pid,
                    identity.scope.value,
                    match["battle_id"],
                    f"round:{round_number}",
                    now_ms,
                    {"side": index, "result": summary},
                )
        if state["status"] == "completed":
            await self.repo.finish(session, match, state, "completed", now_ms, winner_id=ids[state["winner"]])
            match["status"] = "completed"
        elif changed:
            match["expires_ms"] = now_ms + ACTION_TTL_MS
            await session.execute(
                "UPDATE battle_matches SET state_json=?,expires_ms=? WHERE battle_id=?",
                (dumps(state), match["expires_ms"], match["battle_id"]),
            )
            await session.execute(
                "UPDATE asset_occupancies SET busy_until_ms=? WHERE purpose='battle' AND activity_id=?",
                (match["expires_ms"], match["battle_id"]),
            )
        if action == "count":
            title = "出招数已确定"
            banner = (
                "出招数已显示在你的战斗猪下方；0招也不是直接判负。"
                if changed
                else "本回合出招数已经确定，不会重新抽取。"
            )
        elif action == "move":
            title = "招式已展示"
            banner = (
                "本次招式与逐招数值已显示在你的战斗猪下方；双方出完后再各自输入 /会赢的。"
                if changed
                else "本回合已经出完招；请看完双方招式后输入 /会赢的。"
            )
        else:
            title = "会赢的 · 回合结算" if summary else "会赢的 · 等待对方"
            banner = (
                "双方都已确认，以下为本回合唯一结算结果。"
                if summary
                else "你的胜负宣言已锁定，等待对方输入 /会赢的。"
                if changed
                else "你已经输入过 /会赢的；等待对方确认，不会重复结算。"
            )
        return matchup(
            identity,
            match,
            state,
            now_ms,
            title=title,
            banner=banner,
            events=events,
            round_result=summary,
        )

    async def history(self, session, identity, action, args, now_ms):
        page = args.get("page", 1)
        if action == "history":
            count = (
                await session.fetch_one(
                    "SELECT COUNT(*) FROM battle_matches WHERE scope_id=? AND ? IN(initiator_id,opponent_id)",
                    (identity.scope.value, identity.player_id),
                )
            )[0]
            pages = max(1, (count + 7) // 8)
            if page > pages:
                raise BattleError(f"对战记录只有{pages}页。")
            rows = await session.fetch_all(
                "SELECT * FROM battle_matches WHERE scope_id=? AND ? IN(initiator_id,opponent_id) "
                "ORDER BY sequence DESC LIMIT 8 OFFSET ?",
                (identity.scope.value, identity.player_id, (page - 1) * 8),
            )
            lines = []
            for row in rows:
                state = loads(row["state_json"])
                names = " vs ".join(s["snapshot"]["player_name"] for s in state["sides"])
                lines.append(Line(row["battle_id"], names, STATUS_NAMES[row["status"]] + f" · 第{state['round']}回合"))
            return view(
                identity,
                "我的对战记录",
                panels=(Panel("本群记录", tuple(lines), "没有对战也可以先查看 /战斗猪 帮助。"),),
                page=page,
                page_count=pages,
                hints=("/对战记录 B对战号 回合 页码，查看逐招事实和完整伤势结果。",),
            )
        row = await session.fetch_one(
            "SELECT * FROM battle_matches WHERE scope_id=? AND battle_id=?",
            (identity.scope.value, args.get("battle_id")),
        )
        if not row:
            raise BattleError("本群没有这个对战号。")
        match, round_number = dict(row), args.get("round", 1)
        state = loads(match["state_json"])
        if round_number > state["round"]:
            raise BattleError("这个回合尚未发生。")
        record = await session.fetch_one(
            "SELECT result_json FROM battle_rounds WHERE battle_id=? AND round_number=?",
            (match["battle_id"], round_number),
        )
        summary = loads(record[0]) if record else None
        count = (
            await session.fetch_one(
                "SELECT COUNT(*) FROM battle_moves WHERE battle_id=? AND round_number=?",
                (match["battle_id"], round_number),
            )
        )[0]
        pages = max(1, (count + 11) // 12)
        if page > pages:
            raise BattleError(f"本回合逐招记录只有{pages}页。")
        rows = await session.fetch_all(
            "SELECT event_json FROM battle_moves WHERE battle_id=? AND round_number=? "
            "ORDER BY side,ordinal LIMIT 12 OFFSET ?",
            (match["battle_id"], round_number, (page - 1) * 12),
        )
        lines = []
        for item in rows:
            event = loads(item[0])
            line = move_line(event)
            lines.append(
                replace(line, label=state["sides"][event["side"]]["snapshot"]["player_name"] + " · " + line.label)
            )
        result = matchup(
            identity,
            match,
            state,
            now_ms,
            title=f"第{round_number}回合战报",
            round_result=summary,
            extra_panels=(
                Panel(
                    "逐招记录", tuple(lines), f"共{count}次真实招式，包含所有黑闪/贷款追加，按双方各自出招顺序展示。"
                ),
            ),
        )
        return replace(result, page=page, page_count=pages)
