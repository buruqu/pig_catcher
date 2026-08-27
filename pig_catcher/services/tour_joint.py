"""同群双向联演；邀请不花档期，接受时双方完整三站在一个事务内提交。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from ..domain.dispatch import safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.errors import PigCatcherError
from ..domain.models import CommandIdentity
from ..domain.tour_catalog import THEMES_BY_ID, VENUES_BY_ID, TourError
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.dispatch import encode, iso_ms
from ..infrastructure.repositories.restrictions import RestrictionRepository
from ..infrastructure.repositories.tour import TourRepository
from .tour_queries import TourQueries
from .tour_setup import TourSetup


class TourJoint:
    def __init__(self, repo: TourRepository, setup: TourSetup, queries: TourQueries, seed_factory: Callable[[], str]):
        self.repo, self.setup, self.queries, self.seed_factory = repo, setup, queries, seed_factory
        self.restrictions = RestrictionRepository()

    async def expire(self, session: DatabaseSession, player_id: str, now_ms: int) -> None:
        row = await session.fetch_one(
            """SELECT i.* FROM tour_joint_invites i JOIN tour_joint_reservations r
            USING(joint_id) WHERE r.player_id=?""",
            (player_id,),
        )
        if row and row["status"] == "pending" and row["expires_ms"] <= now_ms:
            await self.close(session, row["joint_id"], "expired")

    async def close(self, session: DatabaseSession, joint_id: str, status: str) -> None:
        await session.execute(
            "UPDATE tour_joint_invites SET status=? WHERE joint_id=? AND status='pending'", (status, joint_id)
        )
        await session.execute("DELETE FROM tour_joint_reservations WHERE joint_id=?", (joint_id,))

    async def check_ban(self, session: DatabaseSession, identity: CommandIdentity, now_ms: int) -> None:
        if await self.restrictions.active_plugin_access_ban(
            session, scope_id=identity.scope.value, platform_user_id=identity.user_id, now=iso_ms(now_ms)
        ):
            raise TourError("参与玩家处于插件使用黑名单，不能进行巡演或联演。")

    async def identities(
        self, session: DatabaseSession, identity: CommandIdentity, player_ids: list[str]
    ) -> list[CommandIdentity]:
        result = []
        for pid in player_ids:
            row = await session.fetch_one(
                "SELECT * FROM players WHERE player_id=? AND scope_id=?", (pid, identity.scope.value)
            )
            if not row:
                raise TourError("只能与本群已经建立乐队的玩家联演。")
            result.append(replace(identity, user_id=row["platform_user_id"], display_name=row["display_name"]))
        return result

    async def invitation_view(self, session: DatabaseSession, identity: CommandIdentity, invitation: dict, now_ms: int):
        participants = await self.identities(session, identity, [invitation["inviter_id"], invitation["recipient_id"]])
        payload = json.loads(invitation["payload_json"])
        panels = []
        for member, entry in zip(participants, payload["participants"], strict=True):
            snap, profile = entry["snapshot"], entry["profile"]
            panels.append(
                Panel(
                    profile["name"],
                    (
                        Line("团长", safe_display_name(member.display_name, member.user_id)),
                        Line("阵容", "、".join(m["name"] for m in snap["members"])),
                        Line("路线", " → ".join(VENUES_BY_ID[p["venue"]].name for p in snap["plans"])),
                        Line("主题", " → ".join(THEMES_BY_ID[p["theme"]].name for p in snap["plans"])),
                        Line("各自成本", "1张档期", "按各自编排消耗三站器具"),
                    ),
                )
            )
        remaining = max(0, (invitation["expires_ms"] - now_ms + 999) // 1000)
        return self.queries.view(
            identity,
            "联演邀请 · 等你上台",
            payload["participants"][0]["profile"],
            banner=(
                f"受邀者 {safe_display_name(participants[1].display_name, participants[1].user_id)} "
                f"须在 {remaining} 秒内接受。此时未消耗双方资源。接受即自动完成双方全部三站。"
            ),
            panels=tuple(panels),
            hints=(
                "受邀者：/巡演联演 接受 或 /巡演联演 拒绝",
                "邀请者：/巡演联演 取消",
                "修改阵容或发生占用、授权变化后须重新邀请；不借用对方猪猪。",
            ),
        )

    async def execute(self, session: DatabaseSession, identity: CommandIdentity, action: str, args: dict, now_ms: int):
        if action == "joint_invite":
            target = args.get("target_user_id", "")
            target_row = await session.fetch_one(
                "SELECT player_id FROM players WHERE scope_id=? AND platform_user_id=?", (identity.scope.value, target)
            )
            if not target_row or target_row[0] == identity.player_id:
                raise TourError("请 @ 本群另一位已建立乐队的玩家，不能邀请自己。")
            ids = [identity.player_id, target_row[0]]
            identities = await self.identities(session, identity, ids)
            entries = []
            for member in identities:
                await self.expire(session, member.player_id, now_ms)
                if await session.fetch_one(
                    "SELECT 1 FROM tour_joint_reservations WHERE player_id=?", (member.player_id,)
                ):
                    raise TourError("有一方已有待处理联演邀请，请接受、拒绝、取消或等待五分钟到期。")
                await self.check_ban(session, member, now_ms)
                profile = await self.repo.profile(session, member.player_id, now_ms)
                entries.append({"profile": profile, "snapshot": await self.setup.ready(session, profile)})
            joint_id = "J" + uuid4().hex[:12].upper()
            await session.execute(
                """INSERT INTO tour_joint_invites(joint_id,scope_id,inviter_id,recipient_id,
                payload_json,expires_ms,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    joint_id,
                    identity.scope.value,
                    ids[0],
                    ids[1],
                    encode({"participants": entries}),
                    now_ms + 300_000,
                    iso_ms(now_ms),
                ),
            )
            for pid in ids:
                await session.execute("INSERT INTO tour_joint_reservations VALUES(?,?)", (pid, joint_id))
            row = await session.fetch_one("SELECT * FROM tour_joint_invites WHERE joint_id=?", (joint_id,))
            return await self.invitation_view(session, identity, dict(row), now_ms)
        await self.expire(session, identity.player_id, now_ms)
        row = await session.fetch_one(
            """SELECT i.* FROM tour_joint_invites i JOIN tour_joint_reservations r USING(joint_id)
            WHERE r.player_id=? AND i.scope_id=?""",
            (identity.player_id, identity.scope.value),
        )
        if row is None:
            # 不同消息的重复接受也只返回旧结算，不重新演出。只查自己的最近一次。
            last = await session.fetch_one(
                """SELECT * FROM tour_joint_invites WHERE scope_id=? AND (inviter_id=? OR recipient_id=?)
                ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (identity.scope.value, identity.player_id, identity.player_id),
            )
            if last and last["status"] == "accepted" and action in {"joint_status", "joint_accept"}:
                data = json.loads(last["summary_json"])
                return self.queries.joint_summary(identity, data["summaries"], data["profiles"])
            return self.queries.view(
                identity,
                "没有待接受的联演",
                banner="邀请已结束或已过期；没有新扣除任何档期或器具。",
                hints=("/巡演联演 @群友 · 发出新的邀请",),
            )
        invitation = dict(row)
        if action == "joint_status":
            return await self.invitation_view(session, identity, invitation, now_ms)
        if action == "joint_decline":
            if identity.player_id != row["recipient_id"]:
                raise TourError("只有受邀者可以拒绝；邀请者请用取消。")
            await self.close(session, row["joint_id"], "declined")
            return self.queries.view(identity, "已婉拒联演", banner="双方都没有消耗档期或器具。")
        if action == "joint_cancel":
            if identity.player_id != row["inviter_id"]:
                raise TourError("只有邀请者可以取消；受邀者请用拒绝。")
            await self.close(session, row["joint_id"], "cancelled")
            return self.queries.view(identity, "已取消联演邀请", banner="双方都没有消耗档期或器具。")
        if action != "joint_accept" or identity.player_id != row["recipient_id"]:
            raise TourError("需要受邀者本人使用 /巡演联演 接受。")
        identities = await self.identities(session, identity, [row["inviter_id"], row["recipient_id"]])
        before = json.loads(row["payload_json"])["participants"]
        current = []
        try:
            for member, old in zip(identities, before, strict=True):
                await self.check_ban(session, member, now_ms)
                profile = await self.repo.profile(session, member.player_id, now_ms)
                snapshot = await self.setup.ready(session, profile)
                if not self.setup.same_ready(old["snapshot"], snapshot):
                    raise TourError("有一方阵容、成长或编排发生变化，请重新邀请。")
                current.append((profile, snapshot))
        except PigCatcherError as exc:
            await self.close(session, row["joint_id"], "cancelled")
            return self.queries.view(identity, "联演条件已改变", banner=f"{exc} 本次邀请已取消，双方均未消耗资源。")
        summaries = []
        for profile, snapshot in current:
            run = await self.repo.start(
                session,
                profile,
                snapshot["roster"],
                snapshot["members"],
                seed=self.seed_factory(),
                now_ms=now_ms,
                joint_id=row["joint_id"],
            )
            for _ in range(3):
                run = await self.repo.active_run(session, profile["player_id"])
                await self.repo.play_stage(session, profile, run, now_ms=now_ms)
            summaries.append(await self.repo.finish(session, profile, run["run_id"], now_ms=now_ms))
        for index, (profile, _) in enumerate(current):
            partner = identities[1 - index]
            await self.repo.collect(
                session,
                profile["player_id"],
                summaries[index]["run_id"],
                f"joint:{row['joint_id']}",
                "联演海报",
                f"{profile['name']} × {current[1 - index][0]['name']}",
                {"partner": safe_display_name(partner.display_name, partner.user_id)},
                now_ms,
            )
            await self.repo.fact(
                session,
                profile["player_id"],
                profile["scope_id"],
                row["joint_id"],
                "joint-completed",
                now_ms,
                {
                    "partner_player_id": partner.player_id,
                    "own_ticket_cost": 1,
                    "partner_ticket_cost": 1,
                    "own_run": summaries[index]["run_id"],
                    "partner_run": summaries[1 - index]["run_id"],
                    "both_three_stages": True,
                },
            )
        profiles = [
            {**entry[0], "owner_display_name": safe_display_name(member.display_name, member.user_id)}
            for entry, member in zip(current, identities, strict=True)
        ]
        await session.execute(
            "UPDATE tour_joint_invites SET summary_json=? WHERE joint_id=?",
            (encode({"summaries": summaries, "profiles": profiles}), row["joint_id"]),
        )
        await self.close(session, row["joint_id"], "accepted")
        return self.queries.joint_summary(identity, summaries, profiles)
