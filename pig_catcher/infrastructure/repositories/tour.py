"""巡演持久化与逐站结算；调用者拥有事务，站间不保留资产占用。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from ...domain.dispatch import MATERIAL_SCALE
from ...domain.tour import canonical_members, score_stage, validate_formation, validate_plan
from ...domain.tour_catalog import (
    CHARACTERS,
    GUESTS,
    REWARDS,
    THEMES_BY_ID,
    TOUR_VERSION,
    VENUES_BY_ID,
    TourError,
    default_plan,
    grade,
)
from ..database import DatabaseSession
from .dispatch import DispatchRepository, encode, iso_ms
from .economy import EconomyRepository
from .materials import MaterialRepository


def beijing_day(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, timezone(timedelta(hours=8))).date().isoformat()


class TourRepository:
    def __init__(self) -> None:
        self.materials = MaterialRepository()
        self.economy = EconomyRepository()
        self.dispatch = DispatchRepository()

    async def fact(
        self,
        session: DatabaseSession,
        player_id: str,
        scope_id: str,
        source_id: str,
        subevent: str,
        now_ms: int,
        payload: dict,
    ) -> None:
        key = hashlib.sha256(f"{player_id}|tour|{source_id}|{subevent}".encode()).hexdigest()
        existing = await session.fetch_one("SELECT * FROM activity_facts WHERE fact_key=?", (key,))
        if existing:
            if (
                existing["payload_json"] != encode(payload)
                or existing["scope_id"] != scope_id
                or existing["definition_version"] != TOUR_VERSION
            ):
                raise TourError("巡演事实与原操作不一致。")
            return
        await session.execute(
            "INSERT INTO activity_facts VALUES(?,?,?,?,?,?,?,?,?)",
            (key, player_id, scope_id, "tour", source_id, subevent, TOUR_VERSION, now_ms, encode(payload)),
        )

    async def ticket_change(
        self, session: DatabaseSession, player_id: str, delta: int, *, key: str, reason: str, source: str, now_ms: int
    ) -> None:
        old = await session.fetch_one("SELECT * FROM tour_ticket_ledger WHERE entry_key=?", (key,))
        if old:
            if (old["player_id"], old["delta"], old["reason"], old["source_id"]) != (player_id, delta, reason, source):
                raise TourError("档期账本重试参数不一致。")
            return
        changed = await session.execute(
            "UPDATE tour_profiles SET tickets=tickets+? WHERE player_id=? AND tickets+? BETWEEN 0 AND 7",
            (delta, player_id, delta),
        )
        if changed.rowcount != 1:
            raise TourError("档期不足或超过七张上限，本次没有开始巡演。")
        row = await session.fetch_one("SELECT tickets FROM tour_profiles WHERE player_id=?", (player_id,))
        await session.execute(
            "INSERT INTO tour_ticket_ledger VALUES(?,?,?,?,?,?,?)",
            (key, player_id, delta, row[0], reason, source, iso_ms(now_ms)),
        )

    async def profile(
        self, session: DatabaseSession, player_id: str, now_ms: int, *, required: bool = True
    ) -> dict | None:
        row = await session.fetch_one("SELECT * FROM tour_profiles WHERE player_id=?", (player_id,))
        if row is None:
            if required:
                raise TourError("还没有乐队，请先 /组建乐队 乐队名字。")
            return None
        day = beijing_day(now_ms)
        if day > row["last_ticket_day"]:
            days = (date.fromisoformat(day) - date.fromisoformat(row["last_ticket_day"])).days
            amount = min(7 - row["tickets"], days)
            if amount:
                await self.ticket_change(
                    session,
                    player_id,
                    amount,
                    key=f"tour-day:{player_id}:{day}",
                    reason="daily",
                    source=day,
                    now_ms=now_ms,
                )
            await session.execute("UPDATE tour_profiles SET last_ticket_day=? WHERE player_id=?", (day, player_id))
            row = await session.fetch_one("SELECT * FROM tour_profiles WHERE player_id=?", (player_id,))
        if required and row["archived"]:
            raise TourError("乐队目前已解散；/组建乐队 名字 可恢复原档案，进度与档期不会重置。")
        return dict(row)

    async def create(self, session: DatabaseSession, player_id: str, scope_id: str, name: str, now_ms: int) -> None:
        profile = await self.profile(session, player_id, now_ms, required=False)
        if profile:
            if not profile["archived"]:
                raise TourError("你已经有一支乐队；可用 /我的猪猪乐队 改名 新名字。")
            await session.execute(
                "UPDATE tour_profiles SET name=?,archived=0,revision=revision+1,updated_at=? WHERE player_id=?",
                (name, iso_ms(now_ms), player_id),
            )
            return
        await session.execute(
            """INSERT INTO tour_profiles(player_id,scope_id,name,last_ticket_day,plans_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)""",
            (
                player_id,
                scope_id,
                name,
                beijing_day(now_ms),
                encode([default_plan() for _ in range(3)]),
                iso_ms(now_ms),
                iso_ms(now_ms),
            ),
        )
        await self.ticket_change(
            session, player_id, 2, key=f"tour-initial:{player_id}", reason="initial", source="first-band", now_ms=now_ms
        )
        await self.fact(session, player_id, scope_id, player_id, "band-created", now_ms, {"name": name, "tickets": 2})

    async def member(
        self,
        session: DatabaseSession,
        player_id: str,
        pig_id: str,
        *,
        available: bool = False,
        guest: bool | None = False,
        retiring: bool = False,
    ) -> dict:
        item = await self.dispatch.member(session, player_id, pig_id)
        if guest is None:
            guest = item["template_id"] in GUESTS
        allowed = GUESTS if guest else CHARACTERS
        if item["template_id"] not in allowed:
            raise TourError(f"{item['name']}不是已登记的{'客串' if guest else '音乐'}角色。")
        if available:
            if item["busy_purpose"] or item["locked_trade_id"]:
                raise TourError(f"{item['name']}正在派遣、交易或其他活动中，暂时不能演出或练习。")
            row = await session.fetch_one(
                """SELECT t.scope_type, p.display_variant,
                CASE WHEN t.scope_type='common' THEN 1 ELSE EXISTS(SELECT 1 FROM scope_pig_templates s
                WHERE s.scope_id=p.scope_id AND s.template_id=t.template_id
                AND s.authorized=1 AND s.consent_status='granted') END AS allowed
                FROM pig_instances p JOIN pig_templates t ON t.template_id=p.template_id WHERE p.pig_instance_id=?""",
                (pig_id,),
            )
            if not row["allowed"] and not retiring:
                raise TourError("这只猪的群专属授权已撤回，不能参加新的演出。")
        row = await session.fetch_one(
            """SELECT p.display_variant, COALESCE(t.experience,0) AS experience,
            COALESCE(t.branch,'') AS branch, COALESCE(c.stages,0) AS rapport,
            COALESCE(c.natural_exp,0)+COALESCE(c.practice_exp,0) AS own_experience
            FROM pig_instances p LEFT JOIN tour_proficiency t USING(pig_instance_id)
            LEFT JOIN tour_contributions c ON c.pig_instance_id=p.pig_instance_id AND c.player_id=?
            WHERE p.pig_instance_id=?""",
            (player_id, pig_id),
        )
        item.update(
            training_exp=row["experience"],
            branch=row["branch"],
            rapport=row["rapport"],
            own_experience=row["own_experience"],
            display_variant=row["display_variant"],
        )
        if not guest:
            char = CHARACTERS[item["template_id"]]
            item.update(
                character_id=char.identity,
                character=char.character,
                band=char.band,
                instruments=list(char.instruments),
                roles=sorted(char.roles),
            )
        return item

    async def roster(
        self, session: DatabaseSession, profile: dict, *, available: bool = False
    ) -> tuple[dict, list[dict]]:
        row = await session.fetch_one(
            "SELECT * FROM tour_rosters WHERE player_id=? AND slot=?", (profile["player_id"], profile["active_slot"])
        )
        if not row or not json.loads(row["member_ids_json"]):
            raise TourError("当前阵容为空，请先 /乐队编队 1 猪名、猪名、猪名，然后确认。")
        members = [
            await self.member(session, profile["player_id"], pig_id, available=available)
            for pig_id in json.loads(row["member_ids_json"])
        ]
        validate_formation(members, row["center_id"])
        return dict(row), members

    async def protect(self, session: DatabaseSession, player_id: str, scope_id: str, pig_id: str) -> None:
        await session.execute(
            """INSERT INTO tour_protections VALUES(?,?,?,1) ON CONFLICT(pig_instance_id)
            DO UPDATE SET player_id=excluded.player_id,scope_id=excluded.scope_id,protected=1""",
            (pig_id, player_id, scope_id),
        )

    async def occupy(
        self, session: DatabaseSession, player_id: str, scope_id: str, members: list[dict], activity: str, now_ms: int
    ) -> None:
        for member in members:
            if member["busy_purpose"] or member["locked_trade_id"]:
                raise TourError("有成员正在其他活动中，本次操作没有消耗资源。")
            await session.execute(
                "INSERT INTO asset_occupancies VALUES(?,?,?,?,?,?,?)",
                (member["pig_instance_id"], player_id, scope_id, "tour", activity, now_ms, iso_ms(now_ms)),
            )

    async def release(self, session: DatabaseSession, activity: str) -> None:
        await session.execute("DELETE FROM asset_occupancies WHERE purpose='tour' AND activity_id=?", (activity,))

    async def cost(
        self,
        session: DatabaseSession,
        player_id: str,
        scope_id: str,
        costs: dict[str, int],
        *,
        key: str,
        kind: str,
        now_ms: int,
    ) -> None:
        for material, amount in costs.items():
            if amount == 0:
                continue
            if material == "coins":
                balance = await self.economy.apply_currency_change(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    amount=-amount,
                    reason_code=kind,
                    reason_text="巡演养成与器具支出",
                    source_object_type="tour",
                    source_object_id=key,
                    ledger_entry_id=uuid4().hex,
                    idempotency_key=f"{key}:{player_id}:coins",
                    now=iso_ms(now_ms),
                )
                if balance is None:
                    raise TourError("猪币不足，本次没有消耗资源。")
            else:
                await self.materials.change(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    material_id=material,
                    delta_units=-amount * MATERIAL_SCALE,
                    source_kind=kind,
                    source_id=key,
                    entry_key=f"{key}:{player_id}:{material}",
                    now=iso_ms(now_ms),
                )

    async def train(
        self,
        session: DatabaseSession,
        player_id: str,
        scope_id: str,
        member: dict,
        amount: int,
        *,
        natural: bool,
        source: str,
        subevent: str,
        now_ms: int,
    ) -> None:
        await session.execute(
            """INSERT INTO tour_proficiency(pig_instance_id,experience) VALUES(?,?)
            ON CONFLICT(pig_instance_id) DO UPDATE SET experience=experience+excluded.experience""",
            (member["pig_instance_id"], amount),
        )
        await session.execute(
            """INSERT INTO tour_contributions VALUES(?,?,?,?,?)
            ON CONFLICT(player_id,pig_instance_id) DO UPDATE SET natural_exp=natural_exp+excluded.natural_exp,
            practice_exp=practice_exp+excluded.practice_exp,stages=stages+excluded.stages""",
            (player_id, member["pig_instance_id"], amount if natural else 0, 0 if natural else amount, int(natural)),
        )
        await self.protect(session, player_id, scope_id, member["pig_instance_id"])
        await self.fact(
            session,
            player_id,
            scope_id,
            source,
            subevent,
            now_ms,
            {
                "pig_instance_id": member["pig_instance_id"],
                "template_id": member["template_id"],
                "character_id": member["character_id"],
                "amount": amount,
                "natural": natural,
                "experience_before": member["training_exp"],
                "experience_after": member["training_exp"] + amount,
                "own_experience_after": member["own_experience"] + amount,
            },
        )

    async def songs(self, session: DatabaseSession, player_id: str) -> dict[str, int]:
        return {
            row["song_id"]: row["plays"]
            for row in await session.fetch_all("SELECT * FROM tour_song_progress WHERE player_id=?", (player_id,))
        }

    async def active_run(self, session: DatabaseSession, player_id: str) -> dict | None:
        row = await session.fetch_one("SELECT * FROM tour_runs WHERE player_id=? AND status='active'", (player_id,))
        return dict(row) if row else None

    async def start(
        self,
        session: DatabaseSession,
        profile: dict,
        roster: dict,
        members: list[dict],
        *,
        seed: str,
        now_ms: int,
        joint_id: str = "",
    ) -> dict:
        player_id, scope_id = profile["player_id"], profile["scope_id"]
        if await self.active_run(session, player_id):
            raise TourError("你还有未完成的巡演，请先继续或明确结束。")
        plans = json.loads(profile["plans_json"])
        for plan in plans:
            validate_plan(plan, members, fans=profile["fans"])
        run_id = "T" + uuid4().hex[:10].upper()
        await self.ticket_change(
            session, player_id, -1, key=f"tour-start:{run_id}", reason="tour-start", source=run_id, now_ms=now_ms
        )
        await session.execute(
            """INSERT INTO tour_runs(run_id,player_id,scope_id,definition_version,plans_json,
            random_seed,initial_roster_json,started_ms,joint_id)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                player_id,
                scope_id,
                TOUR_VERSION,
                encode(plans),
                seed,
                encode({"roster": roster, "members": members, "band_name": profile["name"]}),
                now_ms,
                joint_id,
            ),
        )
        await self.fact(
            session,
            player_id,
            scope_id,
            run_id,
            "started",
            now_ms,
            {"members": members, "roster": roster, "plans": plans, "ticket_cost": 1, "joint_id": joint_id},
        )
        return await self.active_run(session, player_id)

    async def collect(
        self,
        session: DatabaseSession,
        player_id: str,
        run_id: str,
        key: str,
        kind: str,
        title: str,
        detail: dict,
        now_ms: int,
    ) -> bool:
        result = await session.execute(
            "INSERT OR IGNORE INTO tour_collections VALUES(?,?,?,?,?,?,?)",
            (player_id, key, kind, title, encode(detail), run_id, iso_ms(now_ms)),
        )
        return result.rowcount == 1

    async def play_stage(self, session: DatabaseSession, profile: dict, run: dict, *, now_ms: int) -> dict:
        if run["definition_version"] != TOUR_VERSION or run["status"] != "active" or run["stage_count"] >= 3:
            raise TourError("这趟巡演的规则版本或状态不支持继续，已完成内容不会重抽。")
        player_id, scope_id, run_id = profile["player_id"], profile["scope_id"], run["run_id"]
        roster, members = await self.roster(session, profile, available=True)
        number = run["stage_count"] + 1
        plan = json.loads(run["plans_json"])[number - 1]
        validate_plan(plan, members, fans=profile["fans"])
        previous_row = await session.fetch_one(
            "SELECT snapshot_json FROM tour_stages WHERE run_id=? AND stage_number=?", (run_id, number - 1)
        )
        previous = json.loads(previous_row[0]) if previous_row else None
        guest = (
            await self.member(session, player_id, profile["guest_id"], available=True, guest=True)
            if profile["guest_id"]
            else None
        )
        all_members = members + ([guest] if guest else [])
        await self.occupy(session, player_id, scope_id, all_members, run_id, now_ms)
        tool = plan["tool"]
        if tool:
            change = await session.execute(
                "UPDATE tour_tools SET quantity=quantity-1 WHERE player_id=? AND tool_id=? AND quantity>0",
                (player_id, tool),
            )
            if change.rowcount != 1:
                raise TourError("本站器具不足，请补做器具或调整尚未演出的站点。")
        result = score_stage(
            members,
            plan,
            equipment=profile["equipment"],
            song_plays=await self.songs(session, player_id),
            stage_number=number,
            previous=previous,
            center=roster["center_id"],
            seed=run["random_seed"],
        )
        prior_members = previous["members"] if previous else json.loads(run["initial_roster_json"])["members"]
        result.update(
            run_id=run_id,
            roster=roster,
            band_name=profile["name"],
            guest=guest,
            lineup_changed=[m["pig_instance_id"] for m in prior_members] != [m["pig_instance_id"] for m in members],
            joint_id=run["joint_id"],
            occurred_ms=now_ms,
            new_collections=[],
            training_gain=20,
        )
        for member in canonical_members(members):
            await self.train(
                session,
                player_id,
                scope_id,
                member,
                20,
                natural=True,
                source=run_id,
                subevent=f"stage-{number}-training-{member['pig_instance_id']}",
                now_ms=now_ms,
            )
        for song_id in plan["songs"]:
            await session.execute(
                """INSERT INTO tour_song_progress VALUES(?,?,1) ON CONFLICT(player_id,song_id)
                DO UPDATE SET plays=plays+1""",
                (player_id, song_id),
            )
        venue = VENUES_BY_ID[plan["venue"]]
        awards = [(f"ticket:{venue.venue_id}", "票根", f"{venue.name} · 演出票根", {})]
        representatives = {m["character_id"]: m for m in canonical_members(members)}
        for char_id in result["photo_ids"]:
            member = representatives[char_id]
            awards.append((f"photo:{char_id}", "高光照片", f"{member['character']} · 聚光留影", {"member": member}))
        for venue_id in result["scene_ids"]:
            awards.append((f"scene:{venue_id}", "城市小景", f"{VENUES_BY_ID[venue_id].name} · 意外的小舞台", {}))
        if guest:
            awards.append(("guest:viola", "客串纪念", "小小幸福的花束", {"member": guest}))
        for collection_key, kind, title, detail in awards:
            if await self.collect(session, player_id, run_id, collection_key, kind, title, detail, now_ms):
                result["new_collections"].append(title)
        tool_row = (
            await session.fetch_one(
                "SELECT quantity FROM tour_tools WHERE player_id=? AND tool_id=?", (player_id, tool)
            )
            if tool
            else None
        )
        result["tool_remaining"] = int(tool_row[0]) if tool_row else 0
        await session.execute(
            "INSERT INTO tour_stages VALUES(?,?,?,?,?,?)", (run_id, number, player_id, scope_id, encode(result), now_ms)
        )
        await session.execute(
            "UPDATE tour_runs SET stage_count=? WHERE run_id=? AND stage_count=?", (number, run_id, number - 1)
        )
        await self.fact(session, player_id, scope_id, run_id, f"stage-{number}", now_ms, result)
        await self.release(session, run_id)
        return result

    async def finish(self, session: DatabaseSession, profile: dict, run_id: str, *, now_ms: int) -> dict:
        rows = await session.fetch_all(
            "SELECT snapshot_json FROM tour_stages WHERE run_id=? ORDER BY stage_number", (run_id,)
        )
        run = await session.fetch_one(
            "SELECT * FROM tour_runs WHERE run_id=? AND player_id=?", (run_id, profile["player_id"])
        )
        if run is None or len(rows) != 3 or run["stage_count"] != 3:
            raise TourError("三站尚未完整结算，不能发放整趟奖励。")
        if run["status"] == "completed":
            return json.loads(run["summary_json"])
        if run["status"] != "active":
            raise TourError("这趟巡演已经结束。")
        stages = [json.loads(row[0]) for row in rows]
        average = round(sum(stage["score"] for stage in stages) / 3, 2)
        rating = grade(average)
        fans, coins = REWARDS[rating]
        player_id, scope_id = profile["player_id"], profile["scope_id"]
        balance = await self.economy.apply_currency_change(
            session,
            player_id=player_id,
            scope_id=scope_id,
            amount=coins,
            reason_code="tour-completed",
            reason_text="三站巡演完成奖励",
            source_object_type="tour",
            source_object_id=run_id,
            ledger_entry_id=uuid4().hex,
            idempotency_key=f"tour-reward:{run_id}",
            now=iso_ms(now_ms),
        )
        await session.execute(
            "UPDATE tour_profiles SET fans=fans+?,updated_at=? WHERE player_id=?", (fans, iso_ms(now_ms), player_id)
        )
        fresh = await session.fetch_one("SELECT fans,tickets FROM tour_profiles WHERE player_id=?", (player_id,))
        additions = []
        theme_id = stages[0]["plan"]["theme"]
        if all(stage["plan"]["theme"] == theme_id and stage["theme_qualified"] for stage in stages):
            theme = THEMES_BY_ID[theme_id]
            additions.append((f"poster:{theme_id}", "主题海报", f"{theme.name} · 三站海报"))
            if all(stage["score"] >= 65 for stage in stages):
                additions.extend(
                    (
                        (f"costume:{theme_id}", "主题服装", f"{theme.band_name}主题 · 原创舞台装扮"),
                        (f"emblem:{theme_id}", "主题队徽", f"{theme.name} · 原创主题队徽"),
                    )
                )
        newly_collected = []
        for collection_key, kind, title in additions:
            if await self.collect(session, player_id, run_id, collection_key, kind, title, {"theme": theme_id}, now_ms):
                newly_collected.append(title)
        summary = {
            "run_id": run_id,
            "band_name": profile["name"],
            "grade": rating,
            "score": average,
            "fans": fans,
            "coins": coins,
            "coin_balance": balance,
            "total_fans": fresh["fans"],
            "tickets": fresh["tickets"],
            "stages": stages,
            "new_collections": newly_collected,
            "joint_id": run["joint_id"],
            "completed_ms": now_ms,
        }
        await session.execute(
            "UPDATE tour_runs SET status='completed',completed_ms=?,summary_json=? WHERE run_id=? AND status='active'",
            (now_ms, encode(summary), run_id),
        )
        await self.fact(session, player_id, scope_id, run_id, "completed", now_ms, summary)
        return summary
