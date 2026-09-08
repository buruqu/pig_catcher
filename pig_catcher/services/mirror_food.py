"""Water mirror copies settle inside the original catch transaction, before techniques.

Copies are new assets, not new catches: no catch rewards, quota, activity score,
or another mirror trigger. The original stays available to the existing pipeline.
"""

from ..domain.mirror_food import GROUP_WATER_MIRROR


async def grant_water_mirror_copies(service, session, *, identity, pig_instance_id: str, now: str) -> list[str]:
    rows = await session.fetch_all(
        """SELECT t.* FROM water_mirror_targets t
           WHERE t.catcher_id=? AND t.scope_id=? AND t.remaining>0
           ORDER BY t.created_at,t.source_food_id""",
        (identity.player_id, identity.scope.value),
    )
    if not rows:
        return []
    original = dict(await session.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (pig_instance_id,)))
    summaries = []
    for row in rows:
        claimed = await session.fetch_one(
            "SELECT 1 FROM water_mirror_claims WHERE source_food_id=? AND original_pig_id=?",
            (row["source_food_id"], pig_instance_id),
        )
        if claimed:
            continue
        clone_id = service._new_identifier()
        code = await service._new_unique_short_code(session)
        clone = {
            **original,
            "pig_instance_id": clone_id,
            "short_code": code,
            "owner_player_id": row["receiver_id"],
            "acquired_at": now,
            "updated_at": now,
            "random_snapshot_json": service.repository.random_snapshot_json(
                {
                    "source": GROUP_WATER_MIRROR,
                    "original_pig_id": pig_instance_id,
                    "source_food_id": row["source_food_id"],
                    "catcher_id": identity.player_id,
                    "duplicate_reward_granted": False,
                }
            ),
        }
        await service.repository.insert_pig_instance(session, values=clone)
        await service.repository.upsert_pig_catalog(
            session,
            player_id=str(row["receiver_id"]),
            template_id=original["template_id"],
            size_value=original["size_value"],
            weight_value=original["weight_value"],
            now=now,
        )
        await session.execute(
            "INSERT INTO water_mirror_claims VALUES(?,?,?)", (row["source_food_id"], pig_instance_id, clone_id)
        )
        await session.execute(
            "UPDATE water_mirror_targets SET remaining=remaining-1 WHERE source_food_id=? AND catcher_id=?",
            (row["source_food_id"], identity.player_id),
        )
        # Use the same player nickname projection as the normal group rewards.
        receiver = await session.fetch_one("SELECT display_name FROM players WHERE player_id=?", (row["receiver_id"],))
        name = str(receiver["display_name"] or "群友") if receiver else "群友"
        summaries.append(
            f"流形水镜冻：为{name}复制了{original['display_name_snapshot']}#{code}；"
            f"此道水镜冻对你的复制剩余{int(row['remaining']) - 1}/2次。"
        )
    return summaries
