"""派遣持久化结算器：在调用者事务内推进当前玩家最多三趟、十八个时间块。

随机种子与出发规则已冻结；按到点时间推进所有队伍，不依赖玩家先领取哪一队。
没有计时线程、隐式提交或全库逐玩家扫描。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from ...domain.dispatch import (
    BLOCK_MS,
    DISPATCH_VERSION,
    MATERIAL_SCALE,
    REGIONS_BY_ID,
    DispatchError,
    block_yield,
    encounter_options,
    exploration_step,
    normalized_attribute,
    proficiency,
    random_at,
    specialties,
)
from ..database import DatabaseSession
from .materials import MaterialRepository


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def timestamp_ms(value: str | datetime) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise DispatchError("派遣时钟必须带时区。")
    return int(parsed.timestamp() * 1000)


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DispatchRepository:
    def __init__(self) -> None:
        self.materials = MaterialRepository()

    async def profile(self, session: DatabaseSession, player_id: str) -> dict[str, Any]:
        await session.execute("INSERT OR IGNORE INTO dispatch_profiles(player_id) VALUES(?)", (player_id,))
        row = await session.fetch_one("SELECT * FROM dispatch_profiles WHERE player_id=?", (player_id,))
        assert row is not None
        return dict(row)

    async def fact(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        source_id: str,
        subevent: str,
        at_ms: int,
        payload: dict[str, Any],
        source_type: str = "dispatch",
    ) -> None:
        key = hashlib.sha256(f"{player_id}|{source_type}|{source_id}|{subevent}".encode()).hexdigest()
        existing = await session.fetch_one("SELECT scope_id,payload_json FROM activity_facts WHERE fact_key=?", (key,))
        if existing is not None:
            if existing["scope_id"] != scope_id or existing["payload_json"] != encode(payload):
                raise DispatchError("活动事实与原幂等操作不一致。")
            return
        await session.execute(
            """INSERT INTO activity_facts VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(fact_key) DO NOTHING""",
            (key, player_id, scope_id, source_type, source_id, subevent, DISPATCH_VERSION, at_ms, encode(payload)),
        )

    async def member(self, session: DatabaseSession, player_id: str, pig_id: str) -> dict[str, Any]:
        row = await session.fetch_one(
            """SELECT p.*,t.length_min,t.length_max,t.weight_min,t.weight_max,t.image_relpath,
            t.alternate_image_relpath,t.scope_type,
            COALESCE(d.hours,0) AS dispatch_hours,o.purpose AS busy_purpose,o.activity_id,
            CASE WHEN t.scope_type='common' THEN 1 ELSE EXISTS(
              SELECT 1 FROM scope_pig_templates s WHERE s.scope_id=p.scope_id AND s.template_id=p.template_id
              AND s.authorized=1 AND s.consent_status='granted') END AS media_visible
            FROM pig_instances p JOIN pig_templates t ON t.template_id=p.template_id
            LEFT JOIN dispatch_proficiency d ON d.pig_instance_id=p.pig_instance_id
            LEFT JOIN asset_occupancies o ON o.pig_instance_id=p.pig_instance_id
            WHERE p.pig_instance_id=? AND p.owner_player_id=?""",
            (pig_id, player_id),
        )
        if row is None or row["state"] != "active":
            raise DispatchError("队伍中的猪已转让、售出或不可用，请重新编队。")
        image = str(row["image_relpath"]) if row["media_visible"] else ""
        if image and row["display_variant"] == "sticker" and row["alternate_image_relpath"]:
            image = str(row["alternate_image_relpath"])
        return {
            "pig_instance_id": pig_id,
            "template_id": row["template_id"],
            "name": row["display_name_snapshot"],
            "short_code": row["short_code"],
            "rarity": row["rarity"],
            "official_value": row["official_value"],
            "size_value": row["size_value"],
            "weight_value": row["weight_value"],
            "size_percentile": row["size_percentile"],
            "weight_percentile": row["weight_percentile"],
            "size_q": normalized_attribute(row["size_value"], row["length_min"], row["length_max"]),
            "weight_q": normalized_attribute(row["weight_value"], row["weight_min"], row["weight_max"]),
            "tags": list(specialties(row["template_id"])),
            "proficiency": proficiency(row["dispatch_hours"]),
            "hours": row["dispatch_hours"],
            "favorite": bool(row["is_favorite"]),
            "image_relpath": image,
            "busy_purpose": row["busy_purpose"] or "",
            "locked_trade_id": row["locked_trade_id"] or "",
        }

    @staticmethod
    def require_available(member: dict[str, Any]) -> None:
        if member["locked_trade_id"]:
            raise DispatchError(f"{member['name']}正在交易中，不能派遣。")
        if member["busy_purpose"]:
            label = {"dispatch": "派遣", "tour": "巡演", "battle": "对战"}.get(member["busy_purpose"], "活动")
            raise DispatchError(f"{member['name']}正在{label}中，请等待归来或先召回。")

    async def trips(self, session: DatabaseSession, player_id: str, *, active: bool = False) -> list[dict[str, Any]]:
        clause = "AND status='traveling'" if active else ""
        rows = await session.fetch_all(
            f"SELECT * FROM dispatch_trips WHERE player_id=? {clause} ORDER BY sequence",
            (player_id,),
        )
        return [dict(row) for row in rows]

    async def settle_elapsed(self, session: DatabaseSession, player_id: str, now: str) -> None:
        trips = await self.trips(session, player_id, active=True)
        if not trips:
            return
        now_ms = timestamp_ms(now)
        profile = await self.profile(session, player_id)
        known = {
            str(r[0])
            for r in await session.fetch_all(
                "SELECT souvenir_id FROM dispatch_souvenirs WHERE player_id=?",
                (player_id,),
            )
        }
        states: dict[str, dict[str, Any]] = {}
        due = []
        for trip in trips:
            snapshot = json.loads(trip["snapshot_json"])
            if snapshot["definition_version"] != DISPATCH_VERSION:
                raise DispatchError("旅行规则版本无法识别，已保留原旅程等待维护。")
            state = json.loads(trip["progress_json"])
            states[trip["trip_id"]] = state
            for event in state["events"]:
                if len(event["options"]) == 1 and event["options"][0]["kind"] == "souvenir":
                    known.add(event["options"][0]["souvenir_id"])
            blocks = min((min(now_ms, trip["ends_ms"]) - trip["starts_ms"]) // BLOCK_MS, snapshot["hours"] // 4)
            for block in range(trip["processed_blocks"] + 1, max(0, blocks) + 1):
                due.append((trip["starts_ms"] + block * BLOCK_MS, trip["sequence"], block, trip, snapshot))
        routes: dict[str, dict[str, int]] = {}
        for end_ms, _, block, trip, snapshot in sorted(due, key=lambda item: item[:3]):
            state = states[trip["trip_id"]]
            region = REGIONS_BY_ID[snapshot["region_id"]]
            if region.region_id not in routes:
                row = await session.fetch_one(
                    "SELECT exploration_tenths,misses FROM dispatch_route_progress WHERE player_id=? AND region_id=?",
                    (player_id, region.region_id),
                )
                routes[region.region_id] = dict(row) if row else {"exploration_tenths": 0, "misses": 0}
            route = routes[region.region_id]
            main_units, supply_units = block_yield(len(snapshot["members"]), sum(snapshot["bonus"].values()))
            base_main, base_supply = block_yield(len(snapshot["members"]), 0)
            state["primary_units"] += main_units
            state["supply_units"] += supply_units
            roll = random_at(trip["random_seed"], block, "encounter")
            before = dict(route)
            fraction, misses, hit, forced = exploration_step(
                route["exploration_tenths"],
                len(snapshot["members"]),
                route["misses"],
                roll,
            )
            route.update(exploration_tenths=fraction, misses=misses)
            if hit:
                use_compass = snapshot["tool_id"] == "encounter-compass" and not state["compass_used"]
                tags = set().union(*(set(m["tags"]) for m in snapshot["members"]))
                options = encounter_options(
                    region,
                    trip["random_seed"],
                    block,
                    tags,
                    camera=snapshot["tool_id"] == "souvenir-camera",
                    known=known,
                    count=2 if use_compass else 1,
                )
                state["events"].append({"block": block, "at_ms": end_ms, "forced": forced, "options": options})
                if use_compass:
                    state["compass_used"] = True
                elif options[0]["kind"] == "souvenir":
                    known.add(options[0]["souvenir_id"])
            extra_ms = max(0, end_ms - max(end_ms - BLOCK_MS, int(profile["covered_until_ms"])))
            profile["effective_seconds"] += extra_ms // 1000
            profile["covered_until_ms"] = max(end_ms, int(profile["covered_until_ms"]))
            trip["processed_blocks"] = block
            trip["progress_json"] = encode(state)
            await self.fact(
                session,
                player_id=player_id,
                scope_id=trip["scope_id"],
                source_id=trip["trip_id"],
                subevent=f"block:{block}",
                at_ms=end_ms,
                payload={
                    "block": block,
                    "start_ms": end_ms - BLOCK_MS,
                    "end_ms": end_ms,
                    "region_id": region.region_id,
                    "primary_units": main_units,
                    "supply_units": supply_units,
                    "base_primary_units": base_main,
                    "base_supply_units": base_supply,
                    "bonus_primary_units": main_units - base_main,
                    "bonus_supply_units": supply_units - base_supply,
                    "material_scale": MATERIAL_SCALE,
                    "exploration_before": before,
                    "exploration_after": dict(route),
                    "roll": roll,
                    "hit": hit,
                    "forced": forced,
                    "effective_seconds_added": extra_ms // 1000,
                },
            )
            if end_ms == trip["ends_ms"]:
                await self.finish(session, trip, now_ms=end_ms, recalled=False, recorded_ms=now_ms)
        for trip in trips:
            if trip["status"] == "traveling":
                await session.execute(
                    "UPDATE dispatch_trips SET processed_blocks=?,progress_json=? WHERE trip_id=?",
                    (trip["processed_blocks"], encode(states[trip["trip_id"]]), trip["trip_id"]),
                )
        for region_id, route in routes.items():
            await session.execute(
                """INSERT INTO dispatch_route_progress VALUES(?,?,?,?) ON CONFLICT(player_id,region_id)
                DO UPDATE SET exploration_tenths=excluded.exploration_tenths,misses=excluded.misses""",
                (player_id, region_id, route["exploration_tenths"], route["misses"]),
            )
        await session.execute(
            "UPDATE dispatch_profiles SET effective_seconds=?,covered_until_ms=? WHERE player_id=?",
            (profile["effective_seconds"], profile["covered_until_ms"], player_id),
        )

    async def credit(
        self,
        session: DatabaseSession,
        trip: dict[str, Any],
        material: str,
        units: int,
        kind: str,
        suffix: str,
        now: str,
    ) -> dict[str, Any]:
        key = f"dispatch:{trip['trip_id']}:{suffix}:{material}"
        await self.materials.change(
            session,
            player_id=trip["player_id"],
            scope_id=trip["scope_id"],
            material_id=material,
            delta_units=units,
            source_kind=kind,
            source_id=trip["trip_id"],
            entry_key=key,
            now=now,
        )
        ledger = await session.fetch_one("SELECT balance_units FROM material_ledger WHERE entry_key=?", (key,))
        assert ledger is not None
        after = int(ledger[0])
        return {
            "material_id": material,
            "delta_units": units,
            "whole_delta": after // MATERIAL_SCALE - (after - units) // MATERIAL_SCALE,
            "source_kind": kind,
        }

    async def award_event(
        self,
        session: DatabaseSession,
        trip: dict[str, Any],
        option: dict[str, Any],
        *,
        suffix: str,
        at_ms: int,
        credited_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = []
        # 奇遇发生于途中，材料到返程/召回才可用；不要把实际入库倒记到途中。
        credited_ms = at_ms if credited_ms is None else credited_ms
        now = iso_ms(credited_ms)
        if option["kind"] in ("materials", "notes"):
            rows.append(
                await self.credit(
                    session,
                    trip,
                    option["material_id"],
                    option["quantity"] * MATERIAL_SCALE,
                    "dispatch-encounter",
                    suffix,
                    now,
                )
            )
        elif option["kind"] == "souvenir":
            inserted = await session.execute(
                "INSERT OR IGNORE INTO dispatch_souvenirs VALUES(?,?,?,?)",
                (trip["player_id"], option["souvenir_id"], trip["trip_id"], now),
            )
            if inserted.rowcount == 0:
                rows.append(
                    await self.credit(
                        session,
                        trip,
                        "travel-supplies",
                        2 * MATERIAL_SCALE,
                        "dispatch-souvenir-duplicate",
                        suffix,
                        now,
                    )
                )
            else:
                rows.append({"souvenir_id": option["souvenir_id"], "name": option["name"], "whole_delta": 1})
        elif option["kind"] == "recipe":
            await session.execute(
                """INSERT INTO dispatch_tools VALUES(?,?,1) ON CONFLICT(player_id,tool_id)
                DO UPDATE SET quantity=quantity+1""",
                (trip["player_id"], option["tool_id"]),
            )
            rows.append({"tool_id": option["tool_id"], "name": option["name"], "whole_delta": 1})
        await self.fact(
            session,
            player_id=trip["player_id"],
            scope_id=trip["scope_id"],
            source_id=trip["trip_id"],
            subevent=suffix,
            at_ms=at_ms,
            payload={"option": option, "rewards": rows, "credited_ms": credited_ms},
        )
        return rows

    async def finish(
        self,
        session: DatabaseSession,
        trip: dict[str, Any],
        *,
        now_ms: int,
        recalled: bool,
        recorded_ms: int | None = None,
    ) -> dict[str, Any]:
        if trip["status"] != "traveling":
            raise DispatchError("这趟旅行已经结算，不能再次领取。")
        snapshot, state = json.loads(trip["snapshot_json"]), json.loads(trip["progress_json"])
        region = REGIONS_BY_ID[snapshot["region_id"]]
        now = iso_ms(now_ms)
        rewards: list[dict[str, Any]] = []
        base_main, base_supply = block_yield(len(snapshot["members"]), 0)
        base_main *= trip["processed_blocks"]
        base_supply *= trip["processed_blocks"]
        for kind, main, general in (
            ("base", base_main, base_supply),
            ("bonus", state["primary_units"] - base_main, state["supply_units"] - base_supply),
        ):
            amounts = {region.material: main}
            amounts["travel-supplies"] = amounts.get("travel-supplies", 0) + general
            for material, units in amounts.items():
                if units:
                    rewards.append(await self.credit(session, trip, material, units, f"dispatch-{kind}", kind, now))
        if snapshot["tool_id"] == "region-map" and state["supply_units"]:
            amount = min(4 * MATERIAL_SCALE, state["supply_units"])
            rewards.append(
                await self.credit(
                    session, trip, "travel-supplies", -amount, "dispatch-tool-conversion", "map-debit", now
                )
            )
            rewards.append(
                await self.credit(
                    session,
                    trip,
                    snapshot["tool_options"]["target"],
                    amount,
                    "dispatch-tool-conversion",
                    "map-credit",
                    now,
                )
            )
        for event in state["events"]:
            suffix = f"event:{event['block']}"
            if len(event["options"]) == 2:
                choice_id = f"{trip['trip_id']}-{event['block']}"
                await session.execute(
                    "INSERT INTO dispatch_choices(choice_id,player_id,scope_id,trip_id,options_json) VALUES(?,?,?,?,?)",
                    (choice_id, trip["player_id"], trip["scope_id"], trip["trip_id"], encode(event["options"])),
                )
                event["choice_id"] = choice_id
            else:
                event["rewards"] = await self.award_event(
                    session,
                    trip,
                    event["options"][0],
                    suffix=suffix,
                    at_ms=event["at_ms"],
                    credited_ms=now_ms,
                )
                rewards.extend(event["rewards"])
        if snapshot["tool_id"] == "sorting-box":
            options = snapshot["tool_options"]
            balance = await self.materials.balances(session, trip["player_id"])
            quantity = max(0, balance.get(options["source"], 0) - options["keep"]) // 3
            if quantity:
                rewards.append(
                    await self.credit(
                        session,
                        trip,
                        options["source"],
                        -3 * quantity * MATERIAL_SCALE,
                        "dispatch-tool-conversion",
                        "sort-debit",
                        now,
                    )
                )
                rewards.append(
                    await self.credit(
                        session,
                        trip,
                        options["target"],
                        quantity * MATERIAL_SCALE,
                        "dispatch-tool-conversion",
                        "sort-credit",
                        now,
                    )
                )
        hours = trip["processed_blocks"] * 4
        if not recalled and hours >= 4:
            for usage in snapshot.get("coupon_uses", []):
                if usage["ticket_id"] == "dispatch-luggage":
                    rewards.append(
                        await self.credit(
                            session, trip, region.material, 3 * MATERIAL_SCALE, "achievement-coupon", "luggage", now
                        )
                    )
                elif usage["ticket_id"] == "dispatch-story":
                    state["achievement_story"] = {
                        "title": "口袋里的第六张明信片",
                        "text": f"{'、'.join(m['name'] for m in snapshot['members'])}在归途把一路的脚印画成了地图。"
                        "这一张寄给出发前的自己：远方并不总是更大的奖品，也可以是值得一起记住的一天。",
                        "region": region.name,
                        "visual_only": True,
                    }
        for member in snapshot["members"]:
            await session.execute(
                """INSERT INTO dispatch_proficiency VALUES(?,?) ON CONFLICT(pig_instance_id)
                DO UPDATE SET hours=hours+excluded.hours""",
                (member["pig_instance_id"], hours),
            )
            await session.execute(
                """INSERT INTO dispatch_contributions VALUES(?,?,?,?) ON CONFLICT(player_id,pig_instance_id)
                DO UPDATE SET hours=hours+excluded.hours,normal_hours=normal_hours+excluded.normal_hours""",
                (trip["player_id"], member["pig_instance_id"], hours, 0 if recalled else hours),
            )
        state["rewards"] = rewards
        state["balances"] = await self.materials.balances(session, trip["player_id"])
        state["settled_hours"] = hours
        state["recorded_ms"] = recorded_ms if recorded_ms is not None else now_ms
        trip.update(status="recalled" if recalled else "completed", progress_json=encode(state), settled_ms=now_ms)
        await session.execute(
            """UPDATE dispatch_trips SET status=?,progress_json=?,settled_ms=?,processed_blocks=? WHERE trip_id=?""",
            (trip["status"], trip["progress_json"], now_ms, trip["processed_blocks"], trip["trip_id"]),
        )
        await session.execute(
            "DELETE FROM asset_occupancies WHERE purpose='dispatch' AND activity_id=?",
            (trip["trip_id"],),
        )
        await self.fact(
            session,
            player_id=trip["player_id"],
            scope_id=trip["scope_id"],
            source_id=trip["trip_id"],
            subevent="completed" if not recalled else "recalled",
            at_ms=now_ms,
            payload={
                "snapshot": snapshot,
                "progress": state,
                "starts_ms": trip["starts_ms"],
                "planned_ends_ms": trip["ends_ms"],
                "settled_ms": now_ms,
                "recorded_ms": state["recorded_ms"],
                "status": trip["status"],
            },
        )
        return trip

    async def claim_choice(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        choice_id: str,
        selected: int,
        now_ms: int,
    ) -> dict[str, Any]:
        if selected not in (1, 2):
            raise DispatchError("奇遇只可选择1或2。")
        row = await session.fetch_one(
            "SELECT * FROM dispatch_choices WHERE choice_id=? AND player_id=?",
            (choice_id, player_id),
        )
        if row is None:
            raise DispatchError("找不到本群属于你的这条奇遇记录。")
        if row["selected"] is not None:
            return {"choice_id": choice_id, "selected": row["selected"], "already_claimed": True, "rewards": []}
        trip_row = await session.fetch_one("SELECT * FROM dispatch_trips WHERE trip_id=?", (row["trip_id"],))
        assert trip_row is not None
        trip = dict(trip_row)
        options = json.loads(row["options_json"])
        rewards = await self.award_event(
            session, trip, options[selected - 1], suffix=f"choice:{choice_id}", at_ms=now_ms
        )
        await session.execute(
            "UPDATE dispatch_choices SET selected=?,claimed_at=? WHERE choice_id=? AND selected IS NULL",
            (selected, iso_ms(now_ms), choice_id),
        )
        return {
            "choice_id": choice_id,
            "selected": selected,
            "option": options[selected - 1],
            "already_claimed": False,
            "rewards": rewards,
        }

    async def claim_old_choices(self, session: DatabaseSession, player_id: str, now_ms: int) -> list[dict[str, Any]]:
        rows = await session.fetch_all(
            """SELECT c.choice_id,t.snapshot_json FROM dispatch_choices c
            JOIN dispatch_trips t ON t.trip_id=c.trip_id WHERE c.player_id=? AND c.selected IS NULL
            ORDER BY t.sequence,c.choice_id""",
            (player_id,),
        )
        claimed = []
        for row in rows:
            preference = json.loads(row["snapshot_json"])["tool_options"].get("preference", 1)
            claimed.append(
                await self.claim_choice(
                    session,
                    player_id=player_id,
                    choice_id=row["choice_id"],
                    selected=preference,
                    now_ms=now_ms,
                )
            )
        return claimed
