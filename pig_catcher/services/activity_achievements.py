"""Transactional outbox consumer. Business commits never depend on reward delivery."""

from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from ..domain.achievements import ACHIEVEMENT_DEFINITIONS, AchievementUnlock
from ..domain.activity_achievements import ACTIVITY_IDS
from ..domain.activity_progress import progress, reduce_fact
from ..domain.battle import dumps, loads
from ..domain.battle_catalog import FIGHTERS_BY_TEMPLATE


class ActivityAchievements:
    def __init__(self, service):
        self.service = service

    async def process_scope(self, scope_id: str, receipt_id: str, now: str) -> tuple[AchievementUnlock, ...]:
        """At most 4 x 64 facts; yield after the first batch exceeding 250ms.

        Queue order is commit order, not the retrospective timestamp of a late
        travel claim. One transaction includes projection, rewards and dequeue.
        The time budget is cooperative: finish the current atomic batch first.
        """
        all_unlocks = []
        started = perf_counter()
        for _ in range(4):
            async with self.service.database.transaction() as session:
                rows = await session.fetch_all(
                    """SELECT f.*,q.sequence,q.historical FROM achievement_activity_queue q
                    JOIN activity_facts f ON f.fact_key=q.fact_key
                    WHERE q.scope_id=? AND q.processed_at IS NULL ORDER BY q.sequence LIMIT 64""",
                    (scope_id,),
                )
                if not rows:
                    break
                if not self.service._definitions_ready:
                    await self.service._sync_definitions(session, now=now)
                states, historical = {}, {}
                for row in rows:
                    fact = dict(row)
                    player_id = fact["player_id"]
                    historical[player_id] = historical.get(player_id, True) and bool(fact["historical"])
                    if fact["scope_id"] != scope_id:
                        raise ValueError("Activity scope mismatch")
                    if player_id not in states:
                        old = await session.fetch_one(
                            "SELECT * FROM achievement_activity_state WHERE player_id=?", (player_id,)
                        )
                        states[player_id] = loads(old["state_json"]) if old else {}
                        await self.service.repository.ensure_profile(session, player_id=player_id, now=now)
                    data = loads(fact["payload_json"])
                    await self.enrich(session, fact, data)
                    reduce_fact(states[player_id], fact, data)
                for player_id, state in states.items():
                    # Batch identity remains stable under a retry, but a later
                    # partial drain of the same command is a distinct event.
                    source = f"activity:{rows[-1]['sequence']}:{player_id}"
                    event_id = str(uuid4())
                    all_unlocks.extend(
                        await self.settle(
                            session,
                            player_id,
                            scope_id,
                            state,
                            event_id,
                            receipt_id,
                            now,
                            notification_status="summary" if historical[player_id] else "pending",
                            event_source=source,
                        )
                    )
                    await session.execute(
                        """INSERT INTO achievement_activity_state VALUES(?,1,?,?) ON CONFLICT(player_id)
                        DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at""",
                        (player_id, dumps(state), now),
                    )
                await session.execute(
                    "UPDATE achievement_activity_queue SET processed_at=? "
                    "WHERE sequence<=? AND scope_id=? AND processed_at IS NULL",
                    (now, rows[-1]["sequence"], scope_id),
                )
            if len(rows) < 64 or perf_counter() - started >= 0.25:
                break
        return tuple(all_unlocks)

    @staticmethod
    async def enrich(session, fact: dict, data: dict) -> None:
        if fact["source_type"] == "battle" and fact["subevent_id"] == "upgrade" and "archetype" not in data:
            row = await session.fetch_one(
                "SELECT template_id FROM pig_instances WHERE pig_instance_id=?", (data["pig_instance_id"],)
            )
            definition = FIGHTERS_BY_TEMPLATE.get(row[0]) if row else None
            if definition:
                data["archetype"] = definition.fighter_id
        if fact["source_type"] == "tour" and fact["subevent_id"] == "completed" and data.get("joint_id"):
            row = await session.fetch_one(
                "SELECT * FROM tour_joint_invites WHERE joint_id=? AND status='accepted' "
                "AND (SELECT COUNT(*) FROM tour_runs r "
                "WHERE r.joint_id=tour_joint_invites.joint_id AND r.status='completed')=2",
                (data["joint_id"],),
            )
            if row and fact["player_id"] in {row["inviter_id"], row["recipient_id"]}:
                data["verified_partner"] = (
                    row["recipient_id"] if fact["player_id"] == row["inviter_id"] else row["inviter_id"]
                )

    async def settle(
        self,
        session,
        player_id: str,
        scope_id: str,
        state: dict,
        event_id: str,
        receipt_id: str,
        now: str,
        *,
        notification_status: str = "pending",
        event_source: str | None = None,
    ):
        repo, service = self.service.repository, self.service
        previous = await repo.progress_rows(session, player_id=player_id)
        unlocked = {key for key, row in previous.items() if row.get("unlocked_at")}
        results = []
        event_exists = event_source is None
        # Each pass must unlock something or stop. This bounds reward-triggered
        # earned-coin and fixed-set graduation dependencies without recursion.
        for _ in range(len(ACHIEVEMENT_DEFINITIONS) + 1):
            metrics = await repo.metric_snapshot(session, player_id=player_id)
            changed = False
            for definition in ACHIEVEMENT_DEFINITIONS:
                aid = definition.achievement_id
                if aid in unlocked:
                    continue
                if aid in ACTIVITY_IDS:
                    value, detail = progress(state, definition, unlocked)
                    target = definition.condition.target
                elif definition.condition.metric == "ordinary_coins_earned":
                    value, target = metrics.get("ordinary_coins_earned", 0), definition.condition.target
                    detail = {"target": target}
                elif definition.condition.metric == "millionaire_947947":
                    value = int(
                        metrics.get("coin_balance", 0) >= 947947 and metrics.get("ordinary_balance", 0) >= 947947
                    )
                    target, detail = 1, {"target": 1}
                else:
                    continue
                value = max(value, int(previous.get(aid, {}).get("progress_value", 0)))
                at = now if value >= target else None
                value, detail_json = min(value, target), dumps(detail)
                old = previous.get(aid)
                if at is None and (
                    (not old and value == 0)
                    or (old and old["progress_value"] == value and old["state_json"] == detail_json)
                ):
                    continue
                await repo.upsert_progress(
                    session,
                    player_id=player_id,
                    achievement_id=aid,
                    definition_version=definition.definition_version,
                    progress_value=value,
                    state_json=detail_json,
                    unlocked_at=at,
                    now=now,
                )
                previous[aid] = {"progress_value": value, "state_json": detail_json, "unlocked_at": at}
                if at is None:
                    continue
                if not event_exists:
                    # Queue/projection already provide replay protection. Only
                    # unlocks need another event row as their reward FK source.
                    await repo.insert_event(
                        session,
                        event_id=event_id,
                        receipt_id=event_source,
                        player_id=player_id,
                        scope_id=scope_id,
                        event_type="activity-settlement",
                        payload_json="{}",
                        now=now,
                    )
                    event_exists = True
                rewards = dumps(
                    [{"type": r.reward_type, "id": r.reward_id, "quantity": r.quantity} for r in definition.rewards]
                )
                inserted = await repo.insert_unlock(
                    session,
                    unlock_id=str(uuid4()),
                    player_id=player_id,
                    scope_id=scope_id,
                    achievement_id=aid,
                    definition_version=definition.definition_version,
                    source_event_id=event_id,
                    source_receipt_id=receipt_id,
                    points_awarded=definition.points,
                    rewards_json=rewards,
                    notification_status=notification_status,
                    now=now,
                )
                unlocked.add(aid)
                if inserted:
                    await service._grant_rewards(
                        session,
                        player_id=player_id,
                        scope_id=scope_id,
                        source_key=f"achievement:{aid}",
                        rewards=definition.rewards,
                        now=now,
                    )
                    results.append(
                        AchievementUnlock(
                            aid, definition.name, definition.tier, definition.points, definition.rewards, now
                        )
                    )
                    changed = True
            await service._settle_milestones(session, player_id=player_id, scope_id=scope_id, now=now)
            await service._settle_regular_completion(session, player_id=player_id, now=now)
            if not changed:
                return results
        raise RuntimeError("Achievement reward dependency did not converge")
