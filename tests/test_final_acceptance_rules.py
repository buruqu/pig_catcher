"""Independent final-acceptance checks against committed gameplay facts."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pig_catcher.config.model import CatchingSection, RankingSection
from pig_catcher.domain.enums import AssetKind
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.infrastructure.repositories.receipts import ReceiptRepository
from pig_catcher.infrastructure.repositories.social import SocialRepository
from pig_catcher.services.achievements import AchievementService
from pig_catcher.services.framework import FrameworkService
from pig_catcher.services.gameplay import GameplayService

from .test_economy import _insert_food
from .test_gameplay import (
    MutableClock,
    SequenceRandom,
    _catch_rolls,
    _database_with_catalog,
    _food_entry,
    _identity,
    _pig_entry,
)

_LEGACY_SUSHI_QUERY = """
SELECT DISTINCT food.food_instance_id
FROM food_instances food
WHERE food.display_name_snapshot='猪寿司拼盘'
  AND COALESCE(json_extract(food.random_snapshot_json, '$.source'), '') <> 'admin-grant'
  AND (
      food.owner_player_id=?
      OR EXISTS(
          SELECT 1
          FROM command_receipts receipt,
               json_each(receipt.result_json, '$.food_instance_ids') generated
          WHERE receipt.player_id=?
            AND receipt.result_type IN ('cooking', 'batch-cooking')
            AND generated.value=food.food_instance_id
      )
      OR EXISTS(
          SELECT 1 FROM asset_transfer_events transfer
          WHERE transfer.asset_kind='food'
            AND transfer.asset_instance_id=food.food_instance_id
            AND transfer.to_player_id=?
      )
  )
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size_qualified", "weight_qualified"),
    [(False, False), (True, False), (False, True), (True, True)],
)
async def test_giant_achievements_match_each_committed_axis(
    tmp_path: Path, size_qualified: bool, weight_qualified: bool
) -> None:
    """An OR-qualified sighting is not proof that this same pig reached both boards."""

    template = _pig_entry("final-acceptance-axis-pig", rarity=1)
    template.update(
        length_min_cm=150.0 if size_qualified else 30.0,
        length_max_cm=180.0 if size_qualified else 50.0,
        weight_min_kg=400.0 if weight_qualified else 20.0,
        weight_max_kg=450.0 if weight_qualified else 60.0,
    )
    database = await _database_with_catalog(tmp_path, [template])
    try:
        clock = MutableClock(datetime(2026, 8, 29, 2, 0, tzinfo=UTC))
        achievements = AchievementService(database, clock=clock)
        await achievements.initialize()
        identity = _identity(message_id="axis-proof")
        gameplay = GameplayService(
            database,
            CatchingSection(cooldown_seconds=0),
            random_source=SequenceRandom(*_catch_rolls()),
            clock=clock,
        )
        caught = await gameplay.catch(identity)
        sighting = await database.fetch_one(
            "SELECT size_qualified,weight_qualified FROM giant_sightings WHERE pig_instance_id=?",
            (caught.pig.pig_instance_id,),
        )
        if size_qualified or weight_qualified:
            assert sighting is not None
            assert bool(sighting["size_qualified"]) is size_qualified
            assert bool(sighting["weight_qualified"]) is weight_qualified
        else:
            assert sighting is None

        await achievements.process_receipt(caught.receipt)
        rows = await database.fetch_all(
            "SELECT achievement_id FROM achievement_unlocks WHERE player_id=? "
            "AND achievement_id IN ('giant-size-board','giant-weight-board','giant-dual-board')",
            (identity.player_id,),
        )
        actual = {row["achievement_id"] for row in rows}
        expected = set()
        if size_qualified:
            expected.add("giant-size-board")
        if weight_qualified:
            expected.add("giant-weight-board")
        if size_qualified and weight_qualified:
            expected.add("giant-dual-board")
        assert actual == expected
        assert await achievements.process_receipt(caught.receipt) == ()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_delayed_giant_achievement_uses_original_threshold_facts(tmp_path: Path) -> None:
    """Delayed/restarted consumption must not reinterpret old catches using today's thresholds."""

    database = await _database_with_catalog(tmp_path, [_pig_entry("threshold-pig", rarity=1)])
    try:
        clock = MutableClock(datetime(2026, 8, 29, 2, 0, tzinfo=UTC))
        identity = _identity(message_id="old-threshold-catch")
        gameplay = GameplayService(
            database,
            CatchingSection(cooldown_seconds=0),
            ranking=RankingSection(giant_size_threshold_cm=20, giant_weight_threshold_kg=10000),
            random_source=SequenceRandom(*_catch_rolls()),
            clock=clock,
        )
        caught = await gameplay.catch(identity)
        assert caught.giant_sighting
        # New configuration classifies the same physical instance in the opposite axis.
        gameplay.ranking = RankingSection(giant_size_threshold_cm=10000, giant_weight_threshold_kg=1)
        await gameplay.pig_detail(identity, caught.pig.selector)
        restarted_consumer = AchievementService(database, clock=clock)
        await restarted_consumer.initialize()
        await restarted_consumer.process_receipt(caught.receipt)
        rows = await database.fetch_all(
            "SELECT achievement_id FROM achievement_unlocks WHERE player_id=? "
            "AND achievement_id IN ('giant-size-board','giant-weight-board','giant-dual-board')",
            (identity.player_id,),
        )
        assert {row["achievement_id"] for row in rows} == {"giant-size-board"}
        assert await restarted_consumer.process_receipt(caught.receipt) == ()
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["scope", "player", "asset"])
async def test_giant_fact_lookup_rejects_unrelated_receipt_coordinates(tmp_path: Path, mismatch: str) -> None:
    """The event projection cannot borrow a real sighting via a mismatched scope/player/pig pointer."""

    template = _pig_entry("scope-proof-pig", rarity=1)
    template.update(length_min_cm=200.0, length_max_cm=250.0, weight_min_kg=500.0, weight_max_kg=600.0)
    database = await _database_with_catalog(tmp_path, [template])
    try:
        identity = _identity(message_id="scope-proof-catch")
        gameplay = GameplayService(
            database,
            CatchingSection(cooldown_seconds=0),
            random_source=SequenceRandom(*_catch_rolls()),
        )
        caught = await gameplay.catch(identity)
        forged = replace(
            caught.receipt,
            **{
                "scope": {"scope_id": "qq:another-group"},
                "player": {"player_id": "qq:100:another-player"},
                "asset": {"result_object_id": "another-pig"},
            }[mismatch],
        )
        consumer = AchievementService(database)
        payload = json.loads(forged.result_json)
        async with database.transaction() as session:
            context = await consumer._event_context(session, receipt=forged, payload=payload)
        _, _, deltas = consumer._event_signals(forged, payload, context)
        assert not (
            {"giant_sightings", "size_board_entries", "weight_board_entries", "dual_board_entries"} & deltas.keys()
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_sushi_history_query_keeps_acquisition_semantics_without_correlated_scans(tmp_path: Path) -> None:
    """Owned/disposed/cooked/transferred history is distinct, scoped and admin-filtered."""

    database = await _database_with_catalog(
        tmp_path,
        [
            _food_entry(
                "sushi-history",
                effect_id="today-window-catches",
                effect_params={"count": 2},
                display_name="猪寿司拼盘",
                rarity=5,
                group_id=None,
            ),
            _food_entry(
                "other-food-history",
                effect_id="",
                effect_params={},
                display_name="其他测试菜",
                rarity=1,
                group_id=None,
            ),
        ],
    )
    try:
        player = _identity(user_id="history-owner")
        peer = _identity(user_id="history-peer")
        foreign = _identity(group_id="other-group", user_id="history-owner")
        empty = _identity(user_id="history-empty")
        framework = FrameworkService(database)
        for identity in (player, peer, foreign, empty):
            await framework.touch_identity(identity)
        # Historical disposed rows retain their last owner; actual transfers move ownership.
        cases = [
            ("owned-active", player, "active", False, False),
            ("owned-consumed", player, "consumed", False, False),
            ("owned-sold", player, "sold", False, False),
            ("cooked-single", peer, "active", False, False),
            ("cooked-batch", peer, "sold", False, False),
            ("received-then-sent", peer, "active", False, False),
            ("received-trade", peer, "consumed", False, False),
            ("received-system", peer, "sold", False, False),
            ("deduplicated", player, "active", False, False),
            ("admin-owned", player, "active", True, False),
            ("admin-cooked", peer, "active", True, False),
            ("admin-received", peer, "active", True, False),
            ("ordinary-owned", player, "active", False, True),
            ("ordinary-cooked", peer, "sold", False, True),
            ("unrelated", peer, "active", False, False),
            ("wrong-receipt-type", peer, "active", False, False),
            ("foreign-player", foreign, "active", False, False),
        ]
        for number, (instance_id, owner, state, admin, other_food) in enumerate(cases):
            await _insert_food(
                database,
                player_id=owner.player_id,
                scope_id=owner.scope.value,
                template_id="other-food-history" if other_food else "sushi-history",
                display_name="其他测试菜" if other_food else "猪寿司拼盘",
                official_value=100,
                short_code=f"HIST{number:04}",
                instance_id=instance_id,
                rarity=1 if other_food else 5,
            )
            async with database.transaction() as session:
                await session.execute(
                    "UPDATE food_instances SET state=?, random_snapshot_json=? WHERE food_instance_id=?",
                    (state, '{"source":"admin-grant"}' if admin else "{}", instance_id),
                )

        now = "2026-08-29T02:00:00.000Z"
        receipts = ReceiptRepository()
        social = SocialRepository()
        async with database.transaction() as session:
            for number, (owner, result_type, generated) in enumerate(
                [
                    (player, "cooking", ["cooked-single", "deduplicated", "admin-cooked"]),
                    (player, "batch-cooking", ["cooked-batch", "deduplicated", "ordinary-cooked", "missing-id"]),
                    (peer, "cooking", ["unrelated"]),
                    (foreign, "cooking", ["foreign-player"]),
                    (player, "food-eating", ["wrong-receipt-type"]),
                ]
            ):
                await receipts.reserve(
                    session,
                    idempotency_key=f"sushi-history-{number}",
                    scope_id=owner.scope.value,
                    player_id=owner.player_id,
                    command_name="synthetic-history",
                    request_fingerprint=f"history-{number}",
                    result_type=result_type,
                    result_object_id=generated[0],
                    result_json=json.dumps({"food_instance_ids": generated}),
                    text_summary="synthetic historical receipt",
                    now=now,
                )
            for number, (asset_id, sender, recipient, transfer_type) in enumerate(
                [
                    ("received-then-sent", peer, player, "gift"),
                    ("received-then-sent", player, peer, "gift"),
                    ("received-trade", peer, player, "trade"),
                    ("received-system", peer, player, "system-group-effect"),
                    ("deduplicated", peer, player, "gift"),
                    ("deduplicated", peer, player, "trade"),
                    ("admin-received", peer, player, "gift"),
                ]
            ):
                await social.insert_transfer_event(
                    session,
                    transfer_event_id=f"sushi-transfer-{number}",
                    scope_id=sender.scope.value,
                    asset_kind=AssetKind.FOOD,
                    asset_instance_id=asset_id,
                    from_player_id=sender.player_id,
                    to_player_id=recipient.player_id,
                    transfer_type=transfer_type,
                    trade_id=None,
                    now=now,
                )

            class RecordingSession:
                query = ""

                async def fetch_all(self, query, parameters):
                    self.query = query
                    return await session.fetch_all(query, parameters)

            capture = RecordingSession()
            repository = AchievementRepository()
            actual = await repository.sushi_instance_ids(capture, player_id=player.player_id)
            legacy = await session.fetch_all(_LEGACY_SUSHI_QUERY, (player.player_id,) * 3)
            assert actual == {str(row["food_instance_id"]) for row in legacy} == {
                "owned-active", "owned-consumed", "owned-sold", "cooked-single", "cooked-batch",
                "received-then-sent", "received-trade", "received-system", "deduplicated",
            }
            plan = await session.fetch_all("EXPLAIN QUERY PLAN " + capture.query, (player.player_id,) * 3)
            assert not any("CORRELATED" in str(row["detail"]) for row in plan)
            assert await repository.sushi_instance_ids(session, player_id=foreign.player_id) == {"foreign-player"}
            assert await repository.sushi_instance_ids(session, player_id=empty.player_id) == set()
    finally:
        await database.close()
