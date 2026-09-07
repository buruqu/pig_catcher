"""周榜结活、到账公告、两分钟间隔及下一期启用。仅处理明确配置的交接计划。"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchView
from ..domain.errors import DomainValidationError
from ..domain.models import ScopeKey
from ..infrastructure.repositories.receipts import ReceiptRepository
from ..version import RULESET_VERSION
from .dispatch import DispatchResult
from .weekly_competitions import (
    _BEIJING_TIMEZONE,
    WeeklyCompetitionService,
    _iso_utc,
    _json,
    _parse_utc,
    _score_text,
    weekly_reward_label,
)

NOTICE_COMMAND = "pig-catcher.weekly-event"


def notice_key(transition_id: str, stage: str, scope_id: str) -> str:
    return f"weekly-transition:{transition_id}:{stage}:{scope_id}"


class WeeklyTransitionService:
    def __init__(self, weekly: WeeklyCompetitionService) -> None:
        self.weekly = weekly
        self.database = weekly.database
        self.clock = weekly.clock
        self.receipts = ReceiptRepository()

    async def schedule(self, *, previous_season: int, next_season: int, scope_ids: list[str]) -> dict:
        """只允许尚未开始且零成绩的下一期；幂等重放不改时间、更不发奖。"""
        scopes = sorted({ScopeKey.parse(scope).value for scope in scope_ids})
        if not scopes or previous_season >= next_season:
            raise DomainValidationError("请指定前后期数以及明确的公告群。")
        now = _iso_utc(self.clock.now())
        async with self.database.transaction() as session:
            previous = await session.fetch_one(
                "SELECT * FROM weekly_competitions WHERE season_number=?", (previous_season,)
            )
            following = await session.fetch_one(
                "SELECT * FROM weekly_competitions WHERE season_number=?", (next_season,)
            )
            if not previous or not following:
                raise DomainValidationError("两期活动必须先注册。")
            key = f"weekly-{previous_season:03d}-to-{next_season:03d}"
            old = await session.fetch_one("SELECT * FROM weekly_transitions WHERE transition_id=?", (key,))
            if old:
                if (old["previous_id"], old["next_id"], old["scope_ids_json"]) != (
                    previous["competition_id"],
                    following["competition_id"],
                    _json(scopes),
                ):
                    raise DomainValidationError("交接计划已有不同参数，禁止覆盖。")
                return {"transition_id": key, "created": False, "not_before": old["not_before"]}
            if previous["status"] not in ("active", "scheduled") or previous["ends_at"] <= now:
                raise DomainValidationError("本工具只预定未结束的前一期，逾期操作需要另行确认。")
            if following["status"] != "scheduled" or following["starts_at"] <= now:
                raise DomainValidationError("下一期已经开始或已结束，禁止变更。")
            if await session.fetch_one(
                "SELECT 1 FROM weekly_competition_entries WHERE competition_id=? LIMIT 1",
                (following["competition_id"],),
            ):
                raise DomainValidationError("下一期已有成绩，禁止改写计分边界。")
            streams = set()
            for scope_id in scopes:
                scope = await session.fetch_one("SELECT * FROM scopes WHERE scope_id=?", (scope_id,))
                if not scope or not scope["enabled"] or not scope["stream_id"]:
                    raise DomainValidationError(f"公告群未启用或缺少真实会话：{scope_id}")
                if scope["stream_id"] in streams:
                    raise DomainValidationError("多个公告范围指向同一聊天流，禁止重复发送。")
                streams.add(scope["stream_id"])
            not_before = max(following["starts_at"], _iso_utc(_parse_utc(previous["ends_at"]) + timedelta(minutes=2)))
            if not_before >= following["ends_at"]:
                raise DomainValidationError("下一期没有可用活动窗口。")
            snapshot = json.loads(following["definition_json"])
            snapshot.update(fixed_starts_at=not_before, transition_id=key, start_policy="after-close-notices-plus-120s")
            await session.execute(
                "UPDATE weekly_competitions SET starts_at=?,definition_json=?,ruleset_version=?,updated_at=? "
                "WHERE competition_id=?",
                (not_before, _json(snapshot), RULESET_VERSION, now, following["competition_id"]),
            )
            await session.execute(
                "INSERT INTO weekly_transitions(transition_id,previous_id,next_id,scope_ids_json,delay_seconds,"
                "not_before,created_at) VALUES(?,?,?,?,120,?,?)",
                (key, previous["competition_id"], following["competition_id"], _json(scopes), not_before, now),
            )
            await self._audit(
                session, key, "weekly-transition-scheduled", {"scopes": scopes, "not_before": not_before}, now
            )
            return {"transition_id": key, "created": True, "not_before": not_before}

    async def process_due(self) -> None:
        # 结算沿用原有原子账本和排名奖励，公告失败不回滚或重复入账。
        await self.weekly.advance()
        now_value = self.clock.now()
        now = _iso_utc(now_value)
        async with self.database.transaction() as session:
            plans = await session.fetch_all("SELECT * FROM weekly_transitions WHERE activated_at IS NULL")
            for plan in plans:
                previous = await session.fetch_one(
                    "SELECT * FROM weekly_competitions WHERE competition_id=?", (plan["previous_id"],)
                )
                if previous["status"] != "settled":
                    continue
                scopes = json.loads(plan["scope_ids_json"])
                sent_at = []
                for scope_id in scopes:
                    await self._queue_notice(session, plan, previous, scope_id, "close", now)
                    row = await session.fetch_one(
                        "SELECT send_status,sent_at FROM command_receipts WHERE idempotency_key=?",
                        (notice_key(plan["transition_id"], "close", scope_id),),
                    )
                    if row["send_status"] == "sent" and row["sent_at"]:
                        sent_at.append(row["sent_at"])
                if len(sent_at) != len(scopes):
                    continue
                due_at = max(
                    _parse_utc(plan["not_before"]),
                    _parse_utc(max(sent_at)) + timedelta(seconds=plan["delay_seconds"]),
                )
                if now_value < due_at:
                    continue
                following = await session.fetch_one(
                    "SELECT * FROM weekly_competitions WHERE competition_id=?", (plan["next_id"],)
                )
                if following["status"] != "scheduled" or now >= following["ends_at"]:
                    raise DomainValidationError("周榜交接窗口过期或状态被改动，需要运营确认；未擅自开启。")
                snapshot = json.loads(following["definition_json"])
                snapshot["actual_starts_at"] = now
                await session.execute(
                    "UPDATE weekly_competitions SET status='active',starts_at=?,definition_json=?,updated_at=? "
                    "WHERE competition_id=? AND status='scheduled'",
                    (now, _json(snapshot), now, plan["next_id"]),
                )
                await session.execute(
                    "UPDATE weekly_transitions SET activated_at=? WHERE transition_id=? AND activated_at IS NULL",
                    (now, plan["transition_id"]),
                )
                following = dict(following)
                following.update(starts_at=now, status="active")
                for scope_id in scopes:
                    await self._queue_notice(session, plan, following, scope_id, "open", now)
                await self._audit(
                    session, plan["transition_id"], "weekly-transition-activated", {"starts_at": now}, now
                )

    async def _queue_notice(self, session, plan, competition, scope_id: str, stage: str, now: str) -> None:
        key = notice_key(plan["transition_id"], stage, scope_id)
        if await self.receipts.get_by_key(session, key):
            return
        scope = await session.fetch_one("SELECT * FROM scopes WHERE scope_id=?", (scope_id,))
        definition = self.weekly._definition_for_row(dict(competition))
        awards = (
            await session.fetch_all(
                "SELECT a.*,p.display_name FROM weekly_competition_awards a JOIN players p ON p.player_id=a.player_id "
                "WHERE a.competition_id=? AND a.scope_id=? ORDER BY a.final_rank",
                (competition["competition_id"], scope_id),
            )
            if stage == "close"
            else []
        )
        if stage == "close":
            winner_rows = tuple(
                Line(
                    f"第{award['final_rank']}名 · {award['display_name'] or '未命名群友'}",
                    _score_text(award["score_value"], competition["metric_unit"]),
                    "、".join(
                        weekly_reward_label(item) for item in self.weekly._decode_rewards(award["reward_snapshot_json"])
                    ),
                )
                for award in awards
            )
            panels = (
                Panel(
                    "本群最终获奖名单",
                    winner_rows,
                    "以上奖励已自动进入各位玩家账户，无需领取。"
                    if awards
                    else "本群本期没有有效参榜成绩，未产生获奖名次。",
                ),
            )
            banner = "第一段冲刺圆满落幕！感谢每一位猪友的参与，恭喜本期获奖选手！"
            hints = (
                f"/佩戴成就 {competition['name']} · 佩戴本期专属活动牌与边框。",
                "两群结活公告送达后，间隔两分钟开启第二期「寿司拼盘大王」。",
            )
            title = "结活啦！奖励已到账" if awards else "本期活动圆满结束"
        else:
            panels = (
                Panel(
                    "本期怎么玩",
                    (
                        Line("比拼目标", "亲手做出的猪寿司拼盘份数", "单次做菜、批量做菜均可；同分先达成者优先。"),
                        Line(
                            "只比厨艺",
                            "赠送、交易、自选券、猪管发放不计分",
                            "售出、吃掉或送出已做好的寿司，不会扣除已有成绩。",
                        ),
                        Line("查看排名", "/抓猪线 或 /zzx", "后面加页码翻页。各群独立排名，前十名有奖！"),
                    ),
                ),
                Panel(
                    "冲榜奖励",
                    tuple(
                        Line(
                            "第1名"
                            if tier.ranks == (1,)
                            else "第2名"
                            if tier.ranks == (2,)
                            else "第3名"
                            if tier.ranks == (3,)
                            else "第4—10名",
                            "、".join(
                                weekly_reward_label(item)
                                for item in tier.rewards
                                if item.reward_type in ("coin", "ticket")
                            ),
                        )
                        for tier in definition.reward_tiers
                    ),
                    "前十另获专属称号、活动牌、匠心寿司徽章和寿司宴台边框；前三名各有独立色段。",
                ),
            )
            banner = "灶台就位，寿司上桌！谁才是本群真正的寿司拼盘大王？现在开始，用一道道亲手做出的寿司冲击榜首！"
            hints = ("只统计本期开幕后成功做出的猪寿司拼盘，开幕前库存不计分。", "活动结束后，奖励自动发放。")
            title = "第二期正式开幕！"
        view = DispatchView(
            title=title,
            player_name=scope["group_name"] or "猪友们",
            subtitle=f"PiG Dream! · 第{competition['season_number']}期「{competition['name']}」",
            banner=banner,
            panels=panels,
            hints=hints,
            stats=(
                Line(
                    "活动时间",
                    f"{_parse_utc(competition['starts_at']).astimezone(_BEIJING_TIMEZONE):%m月%d日 %H:%M:%S} — "
                    f"{_parse_utc(competition['ends_at']).astimezone(_BEIJING_TIMEZONE):%m月%d日 %H:%M}（北京时间）",
                ),
            ),
            presentation="weekly-event",
            scene_key=str(competition["season_number"]),
        )
        await self.receipts.reserve(
            session,
            idempotency_key=key,
            scope_id=scope_id,
            player_id=None,
            command_name=NOTICE_COMMAND,
            request_fingerprint=hashlib.sha256(key.encode()).hexdigest(),
            result_type="weekly-event",
            result_object_id=plan["transition_id"],
            result_json=_json(
                {"view": view.payload(), "stage": stage, "competition_id": competition["competition_id"]}
            ),
            text_summary=view.text(),
            now=now,
            catch_quota_cost=0,
        )

    async def pending_notices(self) -> list[tuple[str, DispatchResult]]:
        results = []
        async with self.database.transaction(immediate=False) as session:
            rows = await session.fetch_all(
                "SELECT r.idempotency_key,s.stream_id FROM command_receipts r JOIN scopes s ON s.scope_id=r.scope_id "
                "WHERE r.command_name=? AND r.send_status='pending' AND s.enabled=1 AND s.stream_id<>'' "
                "ORDER BY r.created_at,r.scope_id",
                (NOTICE_COMMAND,),
            )
            for row in rows:
                receipt = await self.receipts.get_by_key(session, row["idempotency_key"])
                results.append(
                    (
                        row["stream_id"],
                        DispatchResult(DispatchView.from_payload(json.loads(receipt.result_json)["view"]), receipt),
                    )
                )
        return results

    @staticmethod
    async def _audit(session, key: str, action: str, detail: dict, now: str) -> None:
        await session.execute(
            "INSERT INTO audit_events(audit_event_id,scope_id,actor_user_id,action,"
            "object_type,object_id,detail_json,created_at) "
            "VALUES(?,NULL,'system-weekly',?,'weekly-transition',?,?,?)",
            (uuid4().hex, action, key, _json(detail), now),
        )
