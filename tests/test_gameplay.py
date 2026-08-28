"""Third-round catching, collection, records, and item integration tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.errors import (
    AmbiguousPigSelectorError,
    CatchCooldownError,
    DailyCatchLimitError,
    ItemInventoryError,
    TechniqueError,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.special_content import (
    GOJO_BLUE_FOOD_TEMPLATE_ID,
    GOJO_PIG_TEMPLATE_ID,
    GOJO_RED_FOOD_TEMPLATE_ID,
    KFC_PIG_TEMPLATE_ID,
    TECHNIQUE_HOLLOW_PURPLE,
    TECHNIQUE_LAPSE_BLUE,
    TECHNIQUE_MALEVOLENT_KITCHEN,
    TECHNIQUE_REVERSAL_RED,
)
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.repositories import (
    EconomyRepository,
    FrameworkRepository,
    TechniqueRepository,
)
from pig_catcher.rendering import (
    pig_card_view,
    profile_view,
    technique_activation_view,
    technique_catch_event_view,
)
from pig_catcher.services import (
    AssetCatalogService,
    FrameworkService,
    GameplayService,
    format_catch_summary,
    format_profile_summary,
)


class SequenceRandom:
    """Deterministic random source that fails when a code path draws too much."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def random(self) -> float:
        if not self.values:
            raise AssertionError("deterministic random source was exhausted")
        return self.values.pop(0)


class MutableClock:
    """UTC clock that tests may advance between commands."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _identity(
    *,
    group_id: str = "100",
    user_id: str = "200",
    message_id: str = "message-1",
    display_name: str = "测试成员",
) -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", group_id),
        stream_id=f"stream-{group_id}",
        user_id=user_id,
        display_name=display_name,
        message_id=message_id,
        group_name=f"测试群{group_id}",
    )


def _pig_entry(
    template_id: str,
    *,
    rarity: int,
    display_name: str | None = None,
    group_id: str | None = None,
    stature_profile: str = "standard",
    collection: dict[str, object] | None = None,
    paired_food_template_id: str = "",
) -> dict[str, object]:
    group_only = group_id is not None
    entry: dict[str, object] = {
        "template_id": template_id,
        "kind": "pig",
        "display_name": display_name or f"{rarity}星测试猪",
        "rarity": rarity,
        "scope": "group" if group_only else "common",
        "group_scope_id": f"qq:{group_id}" if group_only else None,
        "description": f"{template_id} 的测试描述",
        "image": f"{template_id}.png",
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "granted" if group_only else "not-required",
        "length_min_cm": 30.0,
        "length_max_cm": 70.0,
        "weight_min_kg": 20.0,
        "weight_max_kg": 120.0,
        "fat_profile": "balanced",
        "stature_profile": stature_profile,
        "recipe_tags": ["测试"],
        "paired_food_template_id": paired_food_template_id,
    }
    if collection is not None:
        entry["collection"] = collection
    return entry


def _food_entry(
    template_id: str,
    *,
    effect_id: str,
    effect_params: dict[str, object],
    display_name: str = "专属次数测试菜",
    rarity: int = 6,
    group_id: str | None = "100",
) -> dict[str, object]:
    group_only = group_id is not None
    return {
        "template_id": template_id,
        "kind": "food",
        "display_name": display_name,
        "rarity": rarity,
        "scope": "group" if group_only else "common",
        "group_scope_id": f"qq:{group_id}" if group_only else None,
        "description": "用于验证六星菜专属抓猪次数。",
        "image": f"{template_id}.png",
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "granted" if group_only else "not-required",
        "recipe_tags": ["测试"],
        "effect_id": effect_id,
        "effect_params": effect_params,
    }


async def _database_with_catalog(
    tmp_path: Path,
    entries: list[dict[str, object]],
    *,
    manifest_version: int = 2,
) -> PigCatcherDatabase:
    source = tmp_path / "source"
    source.mkdir()
    for index, entry in enumerate(entries):
        Image.new(
            "RGBA",
            (64, 64),
            (255, 150 + index % 80, 180 + index % 70, 255),
        ).save(source / str(entry["image"]), format="PNG")
    manifest = source / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": manifest_version,
                "catalog_id": "third-round-tests",
                "source_label": "pytest third-round catalog",
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    database = PigCatcherDatabase(data_dir / "pig.sqlite3")
    await database.open()
    await AssetCatalogService(
        database,
        AssetCatalogStorage(data_dir),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    ).import_manifest(manifest)
    return database


def _catch_rolls(
    *,
    rarity_roll: float = 0.0,
    template_roll: float = 0.0,
    attributes: tuple[float, float, float, float, float] = (
        0.5,
        0.5,
        0.5,
        0.5,
        0.5,
    ),
) -> tuple[float, ...]:
    return (rarity_roll, template_roll, *attributes)


def test_giant_food_template_weight_prefers_giant_five_star_candidates() -> None:
    candidates = [
        {"template_id": "giant", "stature_profile": "giant"},
        {"template_id": "normal", "stature_profile": "standard"},
    ]
    uniform = GameplayService._select_template(candidates, 0.6)
    weighted = GameplayService._select_template(
        candidates,
        0.6,
        giant_template_multiplier=4.0,
    )
    assert uniform["template_id"] == "normal"
    assert weighted["template_id"] == "giant"


@pytest.mark.asyncio
async def test_collaboration_food_guarantees_collab_and_preserves_probability_item(
    tmp_path: Path,
) -> None:
    collections = [
        {
            "collaboration_name": "测试企划",
            "collection_id": "collab-test",
            "collection_name": "测试联动",
            "slot": index,
            "total": 3,
            "character_id": f"member-{index}",
            "character_name": f"成员{index}",
            "official_profile_url": f"https://example.com/member-{index}",
        }
        for index in range(1, 4)
    ]
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("ordinary-three", rarity=3),
            _pig_entry("ordinary-four", rarity=4),
            _pig_entry("ordinary-five", rarity=5),
            _pig_entry("collab-three", rarity=3, collection=collections[0]),
            _pig_entry("collab-four", rarity=4, collection=collections[1]),
            _pig_entry("collab-five", rarity=5, collection=collections[2]),
            _food_entry(
                "collaboration-stew",
                effect_id="next-collaboration-catch",
                effect_params={
                    "three_star_percent": 15,
                    "four_star_percent": 55,
                    "five_star_percent": 30,
                },
                display_name="猪猪白菜炖粉条",
                rarity=5,
                group_id=None,
            ),
        ],
    )
    identity = _identity(message_id="collaboration-catch")
    await FrameworkService(database).touch_identity(
        _identity(message_id="collaboration-initialize")
    )
    now = "2026-08-11T00:00:00.000Z"
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                portion_weight, fat_category, official_value, effect_id,
                effect_params_json, ruleset_version, random_snapshot_json,
                state, acquired_at, disposed_at, updated_at
            ) VALUES (
                'collaboration-source', 'COLLAB01', ?, ?, 'collaboration-stew',
                1, 5, '猪猪白菜炖粉条', 1.0, 'balanced', 1100,
                'next-collaboration-catch',
                '{"five_star_percent":30,"four_star_percent":55,"three_star_percent":15}',
                17, '{}', 'consumed', ?, ?, ?
            )
            """,
            (identity.scope.value, identity.player_id, now, now, now),
        )
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                created_at, updated_at
            ) VALUES (
                'collaboration-effect', ?, 'collaboration-source',
                'next-collaboration-catch',
                '{"five_star_percent":30,"four_star_percent":55,"three_star_percent":15}',
                1, 0, ?, ?
            )
            """,
            (identity.player_id, now, now),
        )
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'super-lucky-whistle', 1, ?)
            """,
            (identity.player_id, now),
        )
        await session.execute(
            """
            INSERT INTO armed_items(player_id, action_type, item_id, armed_at)
            VALUES (?, 'catching', 'super-lucky-whistle', ?)
            """,
            (identity.player_id, now),
        )

    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls(rarity_roll=0.8)),
        id_factory=iter(("collaboration-pig", "collaboration-ledger")).__next__,
        short_code_factory=lambda: "COLLPIG1",
    )
    result = await service.catch(identity)
    assert result.pig.rarity == 5
    assert result.pig.collection_name == "测试联动"
    assert result.weights == pytest.approx((0, 0, 15, 55, 30, 0))
    assert any("保留未消耗" in summary for summary in result.excluded_summaries)
    item = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'super-lucky-whistle'",
        (identity.player_id,),
    )
    armed = await database.fetch_one(
        "SELECT item_id FROM armed_items WHERE player_id = ? AND action_type = 'catching'",
        (identity.player_id,),
    )
    effect = await database.fetch_one(
        "SELECT consumed_uses FROM player_food_effects WHERE effect_entry_id = 'collaboration-effect'"
    )
    assert item is not None and item["quantity"] == 1
    assert armed is not None and armed["item_id"] == "super-lucky-whistle"
    assert effect is not None and effect["consumed_uses"] == 1
    await database.close()


@pytest.mark.asyncio
async def test_catch_commits_all_effects_once_and_survives_restart(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    identity = _identity()
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    first_service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls()),
        clock=clock,
        id_factory=iter(("pig-1", "ledger-1")).__next__,
        short_code_factory=lambda: "A19F2C3D",
    )
    first = await first_service.catch(identity)
    assert first.receipt_created is True
    assert first.pig.selector == "1星测试猪#A19F2C3D"
    assert first.coin_reward == 2
    assert first.experience_reward == 5
    assert first.catalog_new is True
    assert first.size_record is True
    assert first.weight_record is True
    assert "等级：Lv.1 · 被猪拱；5/50 EXP" in first.receipt.text_summary
    card = pig_card_view(first.pig, mode_label="抓猪成功", catch=first)
    assert card.player_level == 1
    assert card.level_title == "被猪拱"
    assert card.next_level_experience == 50
    assert card.level_progress_percent == pytest.approx(10.0)
    assert "1★100.0%" in card.probability_line
    assert "等级 Lv.1" in card.probability_sources

    duplicate = await first_service.catch(identity)
    assert duplicate.receipt_created is False
    assert duplicate.receipt.receipt_id == first.receipt.receipt_id
    await database.close()

    await database.open()
    after_restart = await GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(),
        clock=clock,
    ).catch(identity)
    assert after_restart.receipt_created is False
    for table, expected in {
        "pig_instances": 1,
        "currency_ledger": 1,
        "pig_catalog_entries": 1,
        "group_records": 2,
        "command_receipts": 1,
    }.items():
        row = await database.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
        assert row is not None
        assert row["count"] == expected
    player = await database.fetch_one(
        "SELECT coin_balance, experience FROM players WHERE player_id = ?",
        (identity.player_id,),
    )
    assert player is not None
    assert tuple(player) == (2, 5)
    await database.close()


@pytest.mark.asyncio
async def test_numeric_level_changes_the_committed_catch_probability(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            *[
                _pig_entry(f"pig-{rarity}", rarity=rarity)
                for rarity in range(1, 6)
            ],
            _pig_entry("pig-6", rarity=6, group_id="100"),
        ],
    )
    identity = _identity(message_id="level-probability")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            "UPDATE players SET experience = 20000 WHERE player_id = ?",
            (identity.player_id,),
        )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls(rarity_roll=0.395)),
        short_code_factory=lambda: "ABCDEF21",
    )

    result = await service.catch(identity)
    assert result.pig.rarity == 2
    assert result.coin_reward == 5
    assert result.experience_reward == 10
    assert result.veteran_coin_reward == 1_000
    assert result.veteran_reward_levels == (21,)
    snapshot_row = await database.fetch_one(
        """
        SELECT random_snapshot_json
        FROM pig_instances
        WHERE pig_instance_id = ?
        """,
        (result.pig.pig_instance_id,),
    )
    assert snapshot_row is not None
    snapshot = json.loads(str(snapshot_row["random_snapshot_json"]))
    assert snapshot["player_level"] == 21

    profile = await service.profile(_identity(message_id="level-profile"))
    assert profile.level.level == 21
    assert profile.veteran_tier == 1
    assert profile.veteran_catch_coin_bonus == 0
    assert profile.veteran_cook_coin_bonus == 0
    assert profile.veteran_experience_bonus_percent == 0
    assert profile.veteran_milestone_coin_reward == 1_000
    assert profile.veteran_cumulative_coin_reward == 1_000
    assert profile.veteran_claimed_tier == 1
    assert profile.veteran_next_tier_level == 31
    assert profile.veteran_next_tier_coin_reward == 2_000
    assert (
        profile.level_catch_adjusted_high_percent
        > profile.level_catch_base_high_percent
    )
    assert profile.level_cooking_bonus_percent == pytest.approx(10.0)
    profile_card = profile_view(profile)
    assert profile_card.level_bonus_cap_level == 21
    assert "等级概率加成：抓猪 4-6 星" in format_profile_summary(profile)
    assert "普通做菜高档权重 +10.00%" in format_profile_summary(profile)
    assert "资深里程碑：1/5 档" in format_profile_summary(profile)
    assert "本档一次性奖励 1,000 猪币" in format_profile_summary(profile)
    ledger_rows = await database.fetch_all(
        """
        SELECT amount, reason_code, source_object_id
        FROM currency_ledger
        WHERE player_id = ? AND reason_code = 'veteran-level-reward'
        """,
        (identity.player_id,),
    )
    assert [tuple(row) for row in ledger_rows] == [(1_000, "veteran-level-reward", "1")]
    await database.close()


@pytest.mark.asyncio
async def test_existing_high_level_player_receives_all_unclaimed_milestones_once(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    identity = _identity(message_id="veteran-backfill")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            "UPDATE players SET experience = 180000 WHERE player_id = ?",
            (identity.player_id,),
        )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls(rarity_roll=0.0)),
        short_code_factory=lambda: "VETERAN1",
    )

    result = await service.catch(identity)
    replay = await service.catch(identity)

    assert result.veteran_coin_reward == 15_000
    assert result.veteran_reward_levels == (21, 31, 41, 51, 61)
    assert replay.receipt_created is False
    assert replay.veteran_coin_reward == 15_000
    ledger_rows = await database.fetch_all(
        """
        SELECT amount, source_object_id
        FROM currency_ledger
        WHERE player_id = ? AND reason_code = 'veteran-level-reward'
        ORDER BY CAST(source_object_id AS INTEGER)
        """,
        (identity.player_id,),
    )
    assert [tuple(row) for row in ledger_rows] == [
        (1_000, "1"),
        (2_000, "2"),
        (3_000, "3"),
        (4_000, "4"),
        (5_000, "5"),
    ]
    profile = await service.profile(_identity(message_id="veteran-profile"))
    assert profile.veteran_tier == 5
    assert profile.veteran_claimed_tier == 5
    assert profile.veteran_next_tier_level is None
    await database.close()


@pytest.mark.asyncio
async def test_cooldown_daily_limit_and_duplicate_precedence(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    settings = CatchingSection(cooldown_seconds=60, daily_limit=2)
    service = GameplayService(
        database,
        settings,
        random_source=SequenceRandom(
            *_catch_rolls(),
            *_catch_rolls(),
        ),
        clock=clock,
    )
    first_identity = _identity(message_id="first")
    await service.catch(first_identity)
    assert (await service.catch(first_identity)).receipt_created is False
    with pytest.raises(CatchCooldownError, match="60"):
        await service.catch(_identity(message_id="second"))

    clock.value += timedelta(seconds=61)
    await service.catch(_identity(message_id="second"))
    with pytest.raises(DailyCatchLimitError, match="2/2"):
        await service.catch(_identity(message_id="third"))
    await database.close()


@pytest.mark.asyncio
async def test_default_frequency_is_five_per_window_with_twenty_second_cooldown(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    service = GameplayService(
        database,
        CatchingSection(),
        random_source=SequenceRandom(
            *(
                roll
                for _ in range(6)
                for roll in _catch_rolls()
            )
        ),
        clock=clock,
    )
    await service.catch(_identity(message_id="default-1"))
    with pytest.raises(CatchCooldownError, match="20"):
        await service.catch(_identity(message_id="default-2"))
    for index in range(2, 6):
        clock.value += timedelta(seconds=20)
        await service.catch(_identity(message_id=f"default-{index}"))
    clock.value += timedelta(seconds=20)
    with pytest.raises(DailyCatchLimitError, match="5/5"):
        await service.catch(_identity(message_id="default-6"))
    profile = await service.profile(_identity(message_id="default-profile"))
    assert profile.daily_count == 5
    assert profile.daily_limit == 5

    clock.value = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    refreshed = await service.catch(_identity(message_id="after-19-refresh"))
    assert refreshed.daily_count == 1
    await database.close()


@pytest.mark.asyncio
async def test_six_ways_cooking_bonus_waits_without_blocking_catching(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("one-pig", rarity=1),
            _food_entry(
                "six-ways-food",
                effect_id="next-six-star-cook-bonus",
                effect_params={"bonus_percent": 15},
                display_name="一猪六吃",
                rarity=5,
                group_id=None,
            ),
        ],
    )
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls()),
        clock=clock,
    )
    identity = _identity(message_id="catch-with-six-ways")
    await service.profile(_identity(message_id="initialize-six-ways-player"))
    now = "2026-07-28T04:00:00.000Z"
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                portion_weight, fat_category, official_value, effect_id,
                effect_params_json, ruleset_version, random_snapshot_json,
                state, acquired_at, disposed_at, updated_at
            )
            VALUES (
                'six-ways-source', 'WAYS0001', ?, ?, 'six-ways-food', 1, 5,
                '一猪六吃', 1.0, 'balanced', 1000,
                'next-six-star-cook-bonus', '{"bonus_percent":15}', 16, '{}',
                'consumed', ?, ?, ?
            )
            """,
            (identity.scope.value, identity.player_id, now, now, now),
        )
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                expires_at, created_at, updated_at
            ) VALUES (
                'six-ways-effect', ?, 'six-ways-source',
                'next-six-star-cook-bonus', '{"bonus_percent":15}',
                1, 0, NULL, ?, ?
            )
            """,
            (identity.player_id, now, now),
        )

    result = await service.catch(identity)
    assert result.pig.rarity == 1
    effect = await database.fetch_one(
        "SELECT consumed_uses FROM player_food_effects WHERE effect_entry_id = 'six-ways-effect'"
    )
    assert effect is not None and effect["consumed_uses"] == 0
    await database.close()


@pytest.mark.asyncio
async def test_six_star_dish_dedicated_catches_do_not_consume_normal_quota(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("six-star-pig", rarity=6, group_id="100"),
            _food_entry(
                "dedicated-catch-food",
                effect_id="next-six-star-catch",
                effect_params={"six_star_percent": 60, "uses": 5},
            ),
        ],
    )
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    instance_ids = iter(
        value
        for index in range(1, 7)
        for value in (f"dedicated-pig-{index}", f"dedicated-ledger-{index}")
    )
    short_codes = iter(f"D{index:07d}" for index in range(1, 7))
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0, daily_limit=1),
        random_source=SequenceRandom(
            *(roll for _ in range(6) for roll in _catch_rolls())
        ),
        clock=clock,
        id_factory=instance_ids.__next__,
        short_code_factory=short_codes.__next__,
    )
    identity = _identity(message_id="normal-quota-catch")
    normal = await service.catch(identity)
    assert (normal.daily_count, normal.daily_limit) == (1, 1)

    now = "2026-07-28T04:00:00.000Z"
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                portion_weight, fat_category, official_value, effect_id,
                effect_params_json, ruleset_version, random_snapshot_json,
                state, acquired_at, disposed_at, updated_at
            )
            VALUES (
                'dedicated-source-food', 'Food0001', ?, ?,
                'dedicated-catch-food', 1, 6, '专属次数测试菜',
                1.0, 'balanced', 25000, 'next-six-star-catch',
                '{"six_star_percent":60,"uses":5}', 16, '{}',
                'consumed', ?, ?, ?
            )
            """,
            (identity.scope.value, identity.player_id, now, now, now),
        )
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                expires_at, created_at, updated_at
            ) VALUES (
                'dedicated-effect', ?, 'dedicated-source-food',
                'next-six-star-catch', '{"six_star_percent":60,"uses":5}',
                5, 0, NULL, ?, ?
            )
            """,
            (identity.player_id, now, now),
        )

    dedicated_results = []
    for index in range(1, 6):
        result = await service.catch(
            _identity(message_id=f"dedicated-catch-{index}")
        )
        dedicated_results.append(result)
        assert result.quota_exempt_catch is True
        assert (result.daily_count, result.daily_limit) == (1, 1)
        assert pig_card_view(
            result.pig,
            mode_label="抓猪成功",
            catch=result,
        ).quota_exempt_catch is True

    with pytest.raises(DailyCatchLimitError, match="1/1"):
        await service.catch(_identity(message_id="dedicated-exhausted"))

    quota = await database.fetch_one(
        """
        SELECT COUNT(*) AS total, SUM(catch_quota_cost) AS quota_cost
        FROM command_receipts
        WHERE player_id = ? AND command_name = 'pig-catcher.catch'
        """,
        (identity.player_id,),
    )
    effect = await database.fetch_one(
        "SELECT granted_uses, consumed_uses FROM player_food_effects WHERE effect_entry_id = 'dedicated-effect'"
    )
    assert quota is not None and tuple(quota) == (6, 1)
    assert effect is not None and tuple(effect) == (5, 5)
    profile = await service.profile(_identity(message_id="dedicated-profile"))
    assert (profile.daily_count, profile.daily_limit) == (1, 1)
    await database.close()


@pytest.mark.asyncio
async def test_pearl_pig_milk_tea_duplicates_without_duplicate_rewards(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("duplication-pig", rarity=1),
            _food_entry(
                "duplication-food",
                effect_id="catch-duplication-chance",
                effect_params={"chance_percent": 55, "uses": 2},
                display_name="珍猪奶茶",
                rarity=4,
                group_id=None,
            ),
        ],
    )
    identity = _identity(message_id="duplication-owner")
    await FrameworkService(database).touch_identity(identity)
    now = "2026-07-28T04:00:00.000Z"
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                portion_weight, fat_category, official_value, effect_id,
                effect_params_json, ruleset_version, random_snapshot_json,
                state, acquired_at, disposed_at, updated_at
            ) VALUES (
                'duplication-source-food', 'MILK0001', ?, ?,
                'duplication-food', 1, 4, '珍猪奶茶',
                1.0, 'balanced', 1000, 'catch-duplication-chance',
                '{"chance_percent":55,"uses":2}', 27, '{}',
                'consumed', ?, ?, ?
            )
            """,
            (identity.scope.value, identity.player_id, now, now, now),
        )
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                expires_at, created_at, updated_at
            ) VALUES (
                'duplication-effect', ?, 'duplication-source-food',
                'catch-duplication-chance',
                '{"chance_percent":55,"uses":2}', 2, 0, NULL, ?, ?
            )
            """,
            (identity.player_id, now, now),
        )

    ids = iter(
        (
            "caught-one",
            "duplicated-one",
            "ledger-one",
            "caught-two",
            "ledger-two",
        )
    )
    codes = iter(("CATCH001", "DUPL0001", "CATCH002"))
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        clock=MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC)),
        random_source=SequenceRandom(
            *_catch_rolls(),
            0.54,
            *_catch_rolls(),
            0.55,
        ),
        id_factory=ids.__next__,
        short_code_factory=codes.__next__,
    )
    first = await service.catch(
        _identity(message_id="duplication-catch-one")
    )
    second = await service.catch(
        _identity(message_id="duplication-catch-two")
    )
    assert any("复制成功" in text for text in first.effect_summaries)
    assert any("未触发复制" in text for text in second.effect_summaries)
    pigs = await database.fetch_all(
        """
        SELECT pig_instance_id, random_snapshot_json
        FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY pig_instance_id
        """,
        (identity.player_id,),
    )
    assert {str(row["pig_instance_id"]) for row in pigs} == {
        "caught-one",
        "caught-two",
        "duplicated-one",
    }
    duplicate = next(
        row for row in pigs if row["pig_instance_id"] == "duplicated-one"
    )
    snapshot = json.loads(str(duplicate["random_snapshot_json"]))
    assert snapshot["duplicated_from_pig_instance_id"] == "caught-one"
    assert snapshot["duplicate_reward_granted"] is False
    effect = await database.fetch_one(
        """
        SELECT granted_uses, consumed_uses
        FROM player_food_effects
        WHERE effect_entry_id = 'duplication-effect'
        """
    )
    assert effect is not None and tuple(effect) == (2, 2)
    statistics = await database.fetch_one(
        "SELECT total_catches FROM player_statistics WHERE player_id = ?",
        (identity.player_id,),
    )
    assert statistics is not None and int(statistics["total_catches"]) == 2
    await database.close()


@pytest.mark.asyncio
async def test_daily_quota_reset_keeps_receipts_and_lifetime_statistics(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0, daily_limit=2),
        random_source=SequenceRandom(
            *_catch_rolls(),
            *_catch_rolls(),
            *_catch_rolls(),
        ),
        clock=clock,
    )
    await service.catch(_identity(message_id="before-reset-1"))
    await service.catch(_identity(message_id="before-reset-2"))
    with pytest.raises(DailyCatchLimitError, match="2/2"):
        await service.catch(_identity(message_id="before-reset-limit"))

    clock.value += timedelta(seconds=1)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO audit_events(
                audit_event_id, scope_id, actor_user_id, action,
                object_type, object_id, detail_json, created_at
            )
            VALUES (
                'quota-reset-1', NULL, 'local-operator',
                'daily-catch-quota-reset', 'daily-quota',
                '2026-07-28', '{"scope":"all"}',
                '2026-07-28T04:00:01.000Z'
            )
            """
        )

    await service.catch(_identity(message_id="after-reset-1"))
    profile = await service.profile(_identity(message_id="after-reset-profile"))
    assert profile.daily_count == 1

    rows = await database.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM command_receipts
             WHERE command_name = 'pig-catcher.catch') AS receipts,
            (SELECT total_catches FROM player_statistics
             WHERE player_id = ?) AS lifetime_catches
        """,
        (_identity().player_id,),
    )
    assert rows is not None
    assert tuple(rows) == (3, 3)
    await database.close()


@pytest.mark.asyncio
async def test_group_six_star_catch_displays_activator_and_consumes_per_player(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("one-pig", rarity=1),
            _pig_entry("five-pig", rarity=5),
            _pig_entry("six-pig", rarity=6, group_id="100"),
            _food_entry(
                "cloud-pot-food",
                effect_id="group-next-exclusive-high-star-catch",
                effect_params={
                    "five_star_multiplier": 8,
                    "six_star_multiplier": 8,
                    "uses_per_player": 1,
                    "self_coin": 18888,
                    "other_coin": 1680,
                    "source_label": "神龙化猪七星云海锅",
                },
            ),
        ],
    )
    clock = MutableClock(datetime(2026, 8, 11, 4, 0, tzinfo=UTC))
    activator = _identity(
        user_id="1455722694",
        message_id="activator",
        display_name="千早の花火",
    )
    catcher = _identity(user_id="OFFICIAL_OPEN_ID", message_id="group-catch-1")
    now = "2026-08-11T04:00:00.000Z"
    async with database.transaction() as session:
        framework = FrameworkRepository()
        await framework.touch_identity(session, identity=activator, now=now)
        await framework.touch_identity(session, identity=catcher, now=now)
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                portion_weight, fat_category, official_value, effect_id,
                effect_params_json, ruleset_version, random_snapshot_json,
                state, acquired_at, disposed_at, updated_at
            ) VALUES (
                'cloud-pot-source', 'CLOUD001', ?, ?, 'cloud-pot-food', 1, 6,
                '神龙化猪七星云海锅', 1.0, 'balanced', 25000,
                'group-next-exclusive-high-star-catch',
                '{"five_star_multiplier":8,"other_coin":1680,"self_coin":18888,"six_star_multiplier":8,"source_label":"神龙化猪七星云海锅","uses_per_player":1}',
                18, '{}', 'consumed', ?, ?, ?
            )
            """,
            (
                activator.scope.value,
                activator.player_id,
                now,
                now,
                now,
            ),
        )
        await EconomyRepository().insert_group_food_effect(
            session,
            group_effect_entry_id="cloud-pot-group-effect",
            scope_id=activator.scope.value,
            source_player_id=activator.player_id,
            source_food_instance_id="cloud-pot-source",
            effect_id="group-next-exclusive-high-star-catch",
            params_json=(
                '{"five_star_multiplier":8,"other_coin":1680,"self_coin":18888,'
                '"six_star_multiplier":8,"source_label":"神龙化猪七星云海锅",'
                '"uses_per_player":1}'
            ),
            granted_uses_per_player=1,
            starts_at=now,
            expires_at="2026-08-12T04:00:00.000Z",
            now=now,
        )
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                created_at, updated_at
            ) VALUES (
                'ordinary-player-effect', ?, 'cloud-pot-source',
                'next-catch-quality', '{"multiplier":2.2}', 1, 0, ?, ?
            )
            """,
            (catcher.player_id, now, now),
        )
        await session.execute(
            "INSERT INTO item_inventory(player_id, item_id, quantity, updated_at) "
            "VALUES (?, 'super-lucky-whistle', 1, ?)",
            (catcher.player_id, now),
        )
        await session.execute(
            "INSERT INTO armed_items(player_id, action_type, item_id, armed_at) "
            "VALUES (?, 'catching', 'super-lucky-whistle', ?)",
            (catcher.player_id, now),
        )

    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0, daily_limit=5),
        random_source=SequenceRandom(*_catch_rolls(), *_catch_rolls()),
        clock=clock,
        id_factory=iter(
            (
                "group-effect-pig-1",
                "group-effect-ledger-1",
                "group-effect-pig-2",
                "group-effect-ledger-2",
            )
        ).__next__,
        short_code_factory=iter(("CLOUDP01", "CLOUDP02")).__next__,
    )
    first = await service.catch(catcher)
    duplicate = await service.catch(catcher)
    assert first.exclusive_effect_active is True
    assert duplicate.receipt_created is False
    assert any(
        "发动群友：千早の花火" in summary
        for summary in first.effect_summaries
    )
    assert all("1455722694" not in summary for summary in first.effect_summaries)
    assert any("未生效且未消耗" in text for text in first.excluded_summaries)
    assert "发动群友：千早の花火" in format_catch_summary(first)
    assert "发动群友：千早の花火" in pig_card_view(
        first.pig,
        mode_label="抓猪成功",
        catch=first,
    ).effect_summaries[0]
    snapshot_row = await database.fetch_one(
        "SELECT random_snapshot_json FROM pig_instances WHERE pig_instance_id = ?",
        (first.pig.pig_instance_id,),
    )
    assert snapshot_row is not None
    snapshot = json.loads(str(snapshot_row["random_snapshot_json"]))
    assert snapshot["group_effect_source_user_id"] == "1455722694"
    assert snapshot["group_effect_source_display_name"] == "千早の花火"
    usage = await database.fetch_one(
        "SELECT consumed_uses FROM group_food_effect_usage "
        "WHERE group_effect_entry_id = 'cloud-pot-group-effect' AND player_id = ?",
        (catcher.player_id,),
    )
    ordinary = await database.fetch_one(
        "SELECT consumed_uses FROM player_food_effects "
        "WHERE effect_entry_id = 'ordinary-player-effect'"
    )
    item = await database.fetch_one(
        "SELECT quantity FROM item_inventory "
        "WHERE player_id = ? AND item_id = 'super-lucky-whistle'",
        (catcher.player_id,),
    )
    assert usage is not None and usage["consumed_uses"] == 1
    assert ordinary is not None and ordinary["consumed_uses"] == 0
    assert item is not None and item["quantity"] == 1

    second = await service.catch(
        _identity(user_id="OFFICIAL_OPEN_ID", message_id="group-catch-2")
    )
    assert second.exclusive_effect_active is False
    assert all("发动群友" not in text for text in second.effect_summaries)
    ordinary = await database.fetch_one(
        "SELECT consumed_uses FROM player_food_effects "
        "WHERE effect_entry_id = 'ordinary-player-effect'"
    )
    item = await database.fetch_one(
        "SELECT quantity FROM item_inventory "
        "WHERE player_id = ? AND item_id = 'super-lucky-whistle'",
        (catcher.player_id,),
    )
    assert ordinary is not None and ordinary["consumed_uses"] == 1
    assert item is not None and item["quantity"] == 0
    await database.close()


@pytest.mark.asyncio
async def test_cooldown_resets_at_beijing_midnight(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    clock = MutableClock(datetime(2026, 7, 28, 15, 59, 50, tzinfo=UTC))
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=60, daily_limit=2),
        random_source=SequenceRandom(*_catch_rolls(), *_catch_rolls()),
        clock=clock,
    )
    await service.catch(_identity(message_id="before-midnight"))
    clock.value += timedelta(seconds=20)
    after_midnight = await service.catch(_identity(message_id="after-midnight"))
    assert after_midnight.daily_count == 1
    profile = await service.profile(_identity(message_id="profile"))
    assert profile.daily_count == 1
    assert profile.cooldown_remaining_seconds == 60
    await database.close()


@pytest.mark.asyncio
async def test_six_star_is_drawn_only_in_its_authorized_group(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("five-pig", rarity=5),
            _pig_entry("group-six", rarity=6, group_id="100"),
        ],
    )
    settings = CatchingSection(cooldown_seconds=0)
    group_service = GameplayService(
        database,
        settings,
        random_source=SequenceRandom(*_catch_rolls(rarity_roll=0.999)),
    )
    authorized_catalog = await group_service.catalog(
        _identity(group_id="100", user_id="201", message_id="authorized-catalog"),
        rarity=None,
        undiscovered_only=False,
    )
    authorized_group_entry = next(
        entry
        for entry in authorized_catalog.entries
        if entry.template_id == "group-six"
    )
    assert authorized_group_entry.discovered is False
    assert authorized_catalog.visible_catalog_total == 2

    group_result = await group_service.catch(_identity(group_id="100"))
    assert group_result.pig.rarity == 6
    assert group_result.pig.template_id == "group-six"

    other_service = GameplayService(
        database,
        settings,
        random_source=SequenceRandom(*_catch_rolls(rarity_roll=0.999)),
    )
    other_result = await other_service.catch(
        _identity(group_id="999", message_id="other-catch")
    )
    assert other_result.pig.rarity == 5
    assert other_result.pig.template_id == "five-pig"
    other_catalog = await other_service.catalog(
        _identity(group_id="999", message_id="catalog"),
        rarity=None,
        undiscovered_only=False,
    )
    assert all(entry.template_id != "group-six" for entry in other_catalog.entries)
    assert other_catalog.visible_catalog_total == 1
    await database.close()


@pytest.mark.asyncio
async def test_catalog_returns_every_visible_entry_without_page_limit(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry(f"common-{index:02d}", rarity=(index % 5) + 1)
            for index in range(17)
        ],
    )
    service = GameplayService(database, CatchingSection(catalog_page_size=6))
    catalog = await service.catalog(
        _identity(message_id="complete-catalog"),
        rarity=None,
        undiscovered_only=False,
    )
    assert catalog.total_count == 17
    assert len(catalog.entries) == 17
    assert [entry.rarity for entry in catalog.entries] == sorted(
        entry.rarity for entry in catalog.entries
    )
    await database.close()


@pytest.mark.asyncio
async def test_records_and_profile_aggregates_do_not_multiply_rows(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("one-a", rarity=1, display_name="同名猪"),
            _pig_entry("one-b", rarity=1, display_name="同名猪"),
        ],
    )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            *_catch_rolls(
                template_roll=0.0,
                attributes=(0.9, 0.9, 0.9, 0.9, 0.5),
            ),
            *_catch_rolls(
                template_roll=0.99,
                attributes=(0.1, 0.1, 0.1, 0.1, 0.5),
            ),
        ),
        short_code_factory=iter(("AAAA0001", "BBBB0002")).__next__,
    )
    identity = _identity()
    first = await service.catch(identity)
    second = await service.catch(_identity(message_id="message-2"))
    assert first.size_record and first.weight_record
    assert second.size_record and second.weight_record
    profile = await service.profile(_identity(message_id="profile"))
    assert profile.total_catches == 2
    assert profile.active_pigs == 2
    assert profile.catalog_count == 2
    records = await service.records(_identity(message_id="records"), page=1)
    assert records.total_count == 4

    with pytest.raises(AmbiguousPigSelectorError, match="AAAA0001"):
        await service.pig_detail(_identity(message_id="detail"), "同名猪")
    selected = await service.pig_detail(
        _identity(message_id="detail-exact"),
        f"同名猪#{first.pig.short_code}",
    )
    assert selected.pig_instance_id == first.pig.pig_instance_id
    await database.close()


@pytest.mark.asyncio
async def test_daily_giants_uses_beijing_day_original_catcher_and_exact_scope(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("daily-giant-pig", rarity=1, display_name="今日测试猪")],
    )
    clock = MutableClock(datetime(2026, 8, 11, 15, 59, 59, tzinfo=UTC))
    id_values = iter(
        (
            "old-pig",
            "old-ledger",
            "alice-size-pig",
            "alice-size-ledger",
            "alice-weight-pig",
            "alice-weight-ledger",
            "bob-pig",
            "bob-ledger",
            "other-group-pig",
            "other-group-ledger",
        )
    )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*(_catch_rolls() * 5)),
        clock=clock,
        id_factory=id_values.__next__,
        short_code_factory=iter(
            ("OLDPIG01", "ALICESIZ", "ALICEWGT", "BOBPIG01", "OTHERGRP")
        ).__next__,
    )

    old = await service.catch(
        _identity(user_id="alice", display_name="爱丽丝", message_id="old-catch")
    )
    clock.value = datetime(2026, 8, 11, 16, 0, 0, tzinfo=UTC)
    alice_size = await service.catch(
        _identity(user_id="alice", display_name="爱丽丝", message_id="alice-size")
    )
    clock.value = datetime(2026, 8, 11, 18, 0, 0, tzinfo=UTC)
    alice_weight = await service.catch(
        _identity(user_id="alice", display_name="爱丽丝", message_id="alice-weight")
    )
    clock.value = datetime(2026, 8, 11, 20, 0, 0, tzinfo=UTC)
    bob = await service.catch(
        _identity(user_id="bob", display_name="鲍勃", message_id="bob-catch")
    )
    clock.value = datetime(2026, 8, 11, 21, 0, 0, tzinfo=UTC)
    other_group = await service.catch(
        _identity(
            group_id="999",
            user_id="outside",
            display_name="隔壁群",
            message_id="other-group-catch",
        )
    )

    async with database.transaction() as session:
        for pig_instance_id, size_value, weight_value in (
            (old.pig.pig_instance_id, 999.0, 9999.0),
            (alice_size.pig.pig_instance_id, 200.0, 400.0),
            (alice_weight.pig.pig_instance_id, 150.0, 800.0),
            (bob.pig.pig_instance_id, 190.0, 900.0),
            (other_group.pig.pig_instance_id, 1000.0, 10000.0),
        ):
            await session.execute(
                """
                UPDATE pig_instances
                SET size_value = ?, weight_value = ?
                WHERE pig_instance_id = ?
                """,
                (size_value, weight_value, pig_instance_id),
            )
        await session.execute(
            """
            UPDATE pig_instances
            SET owner_player_id = ?, state = 'sold', disposed_at = updated_at
            WHERE pig_instance_id = ?
            """,
            (_identity(user_id="bob").player_id, alice_size.pig.pig_instance_id),
        )
        await session.execute(
            """
            INSERT INTO pig_instances(
                pig_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, rarity, display_name_snapshot,
                size_value, size_percentile, weight_value, weight_percentile,
                fat_ratio, official_value, ruleset_version, random_snapshot_json,
                state, acquired_at, updated_at
            )
            SELECT 'admin-grant-pig', 'ADMINMAX', scope_id, owner_player_id,
                   template_id, template_version, rarity, display_name_snapshot,
                   2000.0, size_percentile, 20000.0, weight_percentile,
                   fat_ratio, official_value, ruleset_version, random_snapshot_json,
                   'active', updated_at, updated_at
            FROM pig_instances
            WHERE pig_instance_id = ?
            """,
            (bob.pig.pig_instance_id,),
        )

    clock.value = datetime(2026, 8, 12, 4, 0, 0, tzinfo=UTC)
    result = await service.daily_giants(
        _identity(user_id="viewer", display_name="查询者", message_id="daily-query")
    )

    assert result.date_label == "北京时间 2026-08-12 12:00 截止"
    assert result.participant_count == 2
    assert result.catch_count == 3
    assert [entry.holder_display_name for entry in result.size_entries] == [
        "爱丽丝",
        "鲍勃",
    ]
    assert [entry.short_code for entry in result.size_entries] == [
        "ALICESIZ",
        "BOBPIG01",
    ]
    assert [entry.holder_display_name for entry in result.weight_entries] == [
        "鲍勃",
        "爱丽丝",
    ]
    assert [entry.short_code for entry in result.weight_entries] == [
        "BOBPIG01",
        "ALICEWGT",
    ]
    assert {entry.short_code for entry in (*result.size_entries, *result.weight_entries)}.isdisjoint(
        {"OLDPIG01", "OTHERGRP", "ADMINMAX"}
    )
    await database.close()


@pytest.mark.asyncio
async def test_armed_item_is_idempotent_and_consumed_only_by_successful_catch(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    identity = _identity(message_id="arm")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'giant-corn', 1, '2026-07-28T00:00:00.000Z')
            """,
            (identity.player_id,),
        )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            *_catch_rolls(attributes=(0.5, 0.5, 0.5, 0.5, 0.5))
        ),
    )
    armed = await service.arm_item(identity, "巨物玉米")
    duplicate = await service.arm_item(identity, "巨物玉米")
    assert armed.receipt_created is True
    assert duplicate.receipt_created is False
    assert duplicate.receipt.receipt_id == armed.receipt.receipt_id

    caught = await service.catch(_identity(message_id="catch"))
    assert caught.item_name == "巨物玉米"
    assert caught.pig.size_percentile == pytest.approx(0.72)
    card = pig_card_view(caught.pig, mode_label="抓猪成功", catch=caught)
    assert "本次" not in card.probability_line
    assert "1★" in card.probability_line
    assert "道具·巨物玉米" in card.probability_sources
    summary = format_catch_summary(caught)
    assert summary.count("本次最终概率：") == 1
    assert "概率来源：等级 Lv.1、饲料 Lv.0、道具·巨物玉米" in summary
    inventory = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'giant-corn'",
        (identity.player_id,),
    )
    assert inventory is not None and inventory["quantity"] == 0
    assert (
        await database.fetch_one(
            "SELECT item_id FROM armed_items WHERE player_id = ?",
            (identity.player_id,),
        )
        is None
    )
    await database.close()


@pytest.mark.asyncio
async def test_coin_bounty_tag_triples_coins_and_increases_experience_once(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    identity = _identity(message_id="bounty-arm")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'coin-bounty-tag', 1, '2026-07-28T00:00:00.000Z')
            """,
            (identity.player_id,),
        )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls()),
    )
    await service.arm_item(identity, "猪币悬赏牌")
    result = await service.catch(_identity(message_id="bounty-catch"))
    assert (result.coin_reward, result.experience_reward) == (6, 8)
    assert result.item_name == "猪币悬赏牌"
    row = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'coin-bounty-tag'",
        (identity.player_id,),
    )
    assert row is not None and row["quantity"] == 0
    await database.close()


@pytest.mark.asyncio
async def test_same_item_can_queue_multiple_compatible_catches(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    identity = _identity(message_id="queue-arm")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'giant-corn', 3, '2026-07-28T00:00:00.000Z')
            """,
            (identity.player_id,),
        )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            *_catch_rolls(),
            *_catch_rolls(),
        ),
    )
    armed = await service.arm_item(identity, "巨物玉米", quantity=2)
    assert armed.armed_uses == 2
    first = await service.catch(_identity(message_id="queue-catch-1"))
    second = await service.catch(_identity(message_id="queue-catch-2"))
    assert (first.item_remaining_uses, second.item_remaining_uses) == (1, 0)
    assert "剩余 1 次" in format_catch_summary(first)
    inventory = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = ?",
        (identity.player_id, "giant-corn"),
    )
    assert inventory is not None and inventory["quantity"] == 1
    assert await database.fetch_one(
        "SELECT 1 FROM armed_items WHERE player_id = ? AND action_type = 'catching'",
        (identity.player_id,),
    ) is None
    await database.close()


@pytest.mark.asyncio
async def test_last_item_use_rolls_back_when_inventory_is_missing(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    identity = _identity(message_id="last-use-arm")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'giant-corn', 1, '2026-07-28T00:00:00.000Z')
            """,
            (identity.player_id,),
        )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls()),
    )
    await service.arm_item(identity, "巨物玉米")
    async with database.transaction() as session:
        await session.execute(
            """
            UPDATE item_inventory
            SET quantity = 0
            WHERE player_id = ? AND item_id = 'giant-corn'
            """,
            (identity.player_id,),
        )

    with pytest.raises(ItemInventoryError, match="库存不足"):
        await service.catch(_identity(message_id="last-use-catch"))

    armed = await database.fetch_one(
        """
        SELECT remaining_uses
        FROM armed_items
        WHERE player_id = ? AND action_type = 'catching'
        """,
        (identity.player_id,),
    )
    pigs = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM pig_instances WHERE owner_player_id = ?",
        (identity.player_id,),
    )
    assert armed is not None and int(armed["remaining_uses"]) == 1
    assert pigs is not None and int(pigs["count"]) == 0
    await database.close()


@pytest.mark.asyncio
async def test_item_is_not_consumed_when_catch_fails_cooldown(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    clock = MutableClock(datetime(2026, 7, 28, 4, 0, tzinfo=UTC))
    identity = _identity(message_id="first")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'lucky-whistle', 1, '2026-07-28T00:00:00.000Z')
            """,
            (identity.player_id,),
        )
    first_service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=60),
        random_source=SequenceRandom(*_catch_rolls()),
        clock=clock,
    )
    await first_service.catch(identity)
    await first_service.arm_item(_identity(message_id="arm"), "幸运猪哨")
    with pytest.raises(CatchCooldownError):
        await first_service.catch(_identity(message_id="blocked"))
    row = await database.fetch_one(
        """
        SELECT inventory.quantity, armed.item_id
        FROM item_inventory AS inventory
        JOIN armed_items AS armed
          ON armed.player_id = inventory.player_id
         AND armed.item_id = inventory.item_id
        WHERE inventory.player_id = ?
        """,
        (identity.player_id,),
    )
    assert row is not None
    assert tuple(row) == (1, "lucky-whistle")
    await database.close()


@pytest.mark.asyncio
async def test_cancel_item_is_idempotent_and_keeps_inventory(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    identity = _identity(message_id="arm")
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'giant-corn', 2, '2026-07-28T00:00:00.000Z')
            """,
            (identity.player_id,),
        )
    service = GameplayService(database, CatchingSection())
    await service.arm_item(identity, "巨物玉米")
    cancel_identity = _identity(message_id="cancel")
    cancelled = await service.cancel_item(cancel_identity, "catching")
    duplicate = await service.cancel_item(cancel_identity, "catching")
    assert cancelled.receipt_created is True
    assert duplicate.receipt_created is False
    assert cancelled.quantity == duplicate.quantity == 2
    with pytest.raises(ItemInventoryError, match="没有为“抓猪”装备"):
        await service.cancel_item(_identity(message_id="cancel-again"), "catching")
    await database.close()


@pytest.mark.asyncio
async def test_concurrent_duplicate_catch_across_database_managers_commits_once(
    tmp_path: Path,
) -> None:
    first_database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    second_database = PigCatcherDatabase(first_database.path)
    await second_database.open()
    identity = _identity(message_id="concurrent")
    first = GameplayService(
        first_database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls()),
    )
    second = GameplayService(
        second_database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*_catch_rolls()),
    )
    results = await asyncio.gather(first.catch(identity), second.catch(identity))
    assert sorted(result.receipt_created for result in results) == [False, True]
    assert results[0].receipt.receipt_id == results[1].receipt.receipt_id
    row = await first_database.fetch_one(
        "SELECT COUNT(*) AS count FROM pig_instances"
    )
    assert row is not None and row["count"] == 1
    await second_database.close()
    await first_database.close()


@pytest.mark.asyncio
async def test_kfc_pig_is_only_drawable_on_beijing_thursday(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry(KFC_PIG_TEMPLATE_ID, rarity=4, display_name="KFC猪"),
            _pig_entry("pig-z-normal-four", rarity=4, display_name="普通四星猪"),
        ],
    )
    clock = MutableClock(datetime(2026, 8, 26, 4, 0, tzinfo=UTC))
    wednesday = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            *_catch_rolls(rarity_roll=0.90, template_roll=0.0)
        ),
        clock=clock,
        id_factory=iter(("wed-pig", "wed-ledger")).__next__,
        short_code_factory=lambda: "WEDKFC01",
    )
    first = await wednesday.catch(_identity(message_id="kfc-wednesday"))
    assert first.pig.template_id == "pig-z-normal-four"

    clock.value = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
    thursday = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            *_catch_rolls(rarity_roll=0.90, template_roll=0.0)
        ),
        clock=clock,
        id_factory=iter(("thu-pig", "thu-ledger")).__next__,
        short_code_factory=lambda: "THUKFC01",
    )
    second = await thursday.catch(_identity(message_id="kfc-thursday"))
    assert second.pig.template_id == KFC_PIG_TEMPLATE_ID
    await database.close()


@pytest.mark.asyncio
async def test_blue_red_pair_unlocks_purple_and_grants_five_six_star_pigs(
    tmp_path: Path,
) -> None:
    six_pig = _pig_entry(
        "pig-phase8-six",
        rarity=6,
        group_id="100",
        paired_food_template_id="food-phase8-six",
    )
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("pig-phase8-one", rarity=1),
            six_pig,
            _food_entry(
                "food-phase8-six",
                effect_id="",
                effect_params={},
                rarity=6,
                group_id="100",
            ),
        ],
        manifest_version=4,
    )
    activator = _identity(
        user_id="100",
        message_id="technique-activator",
        display_name="苍赫发动者",
    )
    await FrameworkService(database).touch_identity(activator)
    now = "2026-08-27T04:00:00.000Z"
    techniques = TechniqueRepository()
    async with database.transaction() as session:
        await techniques.grant_permit(
            session,
            player_id=activator.player_id,
            technique_id=TECHNIQUE_LAPSE_BLUE,
            uses=1,
            now=now,
        )
        await techniques.grant_permit(
            session,
            player_id=activator.player_id,
            technique_id=TECHNIQUE_REVERSAL_RED,
            uses=1,
            now=now,
        )

    counters = {"id": 0, "code": 0}

    def next_id() -> str:
        counters["id"] += 1
        return f"phase8-object-{counters['id']}"

    def next_code() -> str:
        counters["code"] += 1
        return f"P8{counters['code']:06d}"

    clock = MutableClock(datetime(2026, 8, 27, 4, 0, tzinfo=UTC))
    activation = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(*([0.0] * 30)),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    blue = await activation.activate_group_technique(
        _identity(
            user_id="100",
            message_id="activate-blue",
            display_name="苍赫发动者",
        ),
        technique_id=TECHNIQUE_LAPSE_BLUE,
    )
    assert blue.total_uses == 5
    with pytest.raises(TechniqueError, match="不能发动另一种群体术式"):
        await activation.activate_group_technique(
            _identity(
                user_id="100",
                message_id="red-blocked-by-blue",
                display_name="苍赫发动者",
            ),
            technique_id=TECHNIQUE_REVERSAL_RED,
        )

    catches = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(
            *(value for _ in range(5) for value in _catch_rolls())
        ),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    for index in range(5):
        result = await catches.catch(
            _identity(
                user_id=str(201 + index),
                message_id=f"blue-catch-{index}",
                display_name=f"抓猪者{index + 1}",
            )
        )
        assert result.pig.owner_player_id == activator.player_id
        assert result.technique_resolution is not None
        assert result.technique_resolution.technique_id == TECHNIQUE_LAPSE_BLUE
        assert result.technique_resolution.target_display_name == "苍赫发动者"
        assert result.technique_resolution.remaining_uses == 4 - index
        assert f"本群术式剩余 {4 - index} 次" in result.receipt.text_summary

    red = await activation.activate_group_technique(
        _identity(
            user_id="100",
            message_id="activate-red",
            display_name="苍赫发动者",
        ),
        technique_id=TECHNIQUE_REVERSAL_RED,
    )
    assert red.purple_unlocked == 1
    async with database.transaction() as session:
        assert await techniques.available_permits(
            session,
            player_id=activator.player_id,
            technique_id=TECHNIQUE_HOLLOW_PURPLE,
        ) == 1

    purple = await activation.activate_hollow_purple(
        _identity(
            user_id="100",
            message_id="activate-purple",
            display_name="苍赫发动者",
        )
    )
    assert len(purple.granted_pigs) == 5
    assert all(pig.rarity == 6 for pig in purple.granted_pigs)
    assert purple.remaining_permits == 0
    purple_view = technique_activation_view(
        purple,
        actor_name="术式使用者",
        actor_player_id=activator.player_id,
        group_name="测试群100",
    )
    assert purple_view.title == "虚式·茈发动"
    assert purple_view.hero_value == "5 只六星猪"
    assert len(purple_view.rows) == 3
    assert all(pig.selector in purple_view.note for pig in purple.granted_pigs)
    assert purple_view.media_visible is True
    await database.close()


@pytest.mark.asyncio
async def test_malevolent_kitchen_auto_cooks_and_duplicates_six_star_food(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry(
                "pig-domain-six",
                rarity=6,
                group_id="100",
                paired_food_template_id="food-domain-six",
            ),
            _food_entry(
                "food-domain-six",
                effect_id="",
                effect_params={},
                display_name="领域六星菜",
                rarity=6,
                group_id="100",
            ),
        ],
        manifest_version=4,
    )
    activator = _identity(
        user_id="100",
        message_id="domain-activator",
        display_name="领域发动者",
    )
    await FrameworkService(database).touch_identity(activator)
    techniques = TechniqueRepository()
    async with database.transaction() as session:
        await techniques.grant_permit(
            session,
            player_id=activator.player_id,
            technique_id=TECHNIQUE_MALEVOLENT_KITCHEN,
            uses=1,
            now="2026-08-27T04:00:00.000Z",
        )

    counters = {"id": 0, "code": 0}

    def next_id() -> str:
        counters["id"] += 1
        return f"domain-object-{counters['id']}"

    def next_code() -> str:
        counters["code"] += 1
        return f"DM{counters['code']:06d}"

    clock = MutableClock(datetime(2026, 8, 27, 4, 0, tzinfo=UTC))
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(
            0.999,
            0.0,
            0.5,
            0.5,
            0.5,
            0.5,
            0.5,
            0.99,
            0.0,
            0.5,
            0.5,
        ),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    activated = await service.activate_group_technique(
        _identity(
            user_id="100",
            message_id="activate-domain",
            display_name="领域发动者",
        ),
        technique_id=TECHNIQUE_MALEVOLENT_KITCHEN,
    )
    assert activated.total_uses == 10
    catch_identity = _identity(
        user_id="200",
        message_id="domain-catch",
        display_name="领域抓猪者",
    )
    caught = await service.catch(catch_identity)
    assert "六星菜概率固定为 25%" in activated.summary
    assert "发动者与抓猪者各获得一份" in caught.receipt.text_summary
    assert caught.technique_resolution is not None
    assert caught.technique_resolution.technique_id == TECHNIQUE_MALEVOLENT_KITCHEN
    assert caught.technique_resolution.remaining_uses == 9
    assert len(caught.technique_resolution.generated_foods) == 2
    assert {
        food.owner_display_name
        for food in caught.technique_resolution.generated_foods
    } == {"领域发动者", "领域抓猪者"}
    replayed = await service.catch(catch_identity)
    assert replayed.receipt_created is False
    assert replayed.technique_resolution == caught.technique_resolution
    source = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = ?",
        (caught.pig.pig_instance_id,),
    )
    assert source is not None and source["state"] == "consumed-for-cooking"
    foods = await database.fetch_all(
        """
        SELECT owner_player_id, template_id, rarity
        FROM food_instances
        WHERE source_pig_instance_id = ?
        ORDER BY owner_player_id
        """,
        (caught.pig.pig_instance_id,),
    )
    assert len(foods) == 2
    assert {str(row["owner_player_id"]) for row in foods} == {
        activator.player_id,
        "qq:100:200",
    }
    assert all(row["template_id"] == "food-domain-six" for row in foods)
    assert all(row["rarity"] == 6 for row in foods)
    effect = await database.fetch_one(
        """
        SELECT remaining_uses
        FROM group_technique_effects
        WHERE scope_id = 'qq:100' AND status = 'active'
        """
    )
    assert effect is not None and effect["remaining_uses"] == 9
    await database.close()


@pytest.mark.asyncio
async def test_malevolent_kitchen_gojo_creates_both_exclusive_foods_for_two_random_players(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry(
                GOJO_PIG_TEMPLATE_ID,
                rarity=5,
                display_name="五条猪",
            ),
            _food_entry(
                GOJO_BLUE_FOOD_TEMPLATE_ID,
                effect_id="technique-permit",
                effect_params={"technique_id": "lapse-blue"},
                display_name="五条猪无量苍蓝雪山",
                rarity=5,
                group_id=None,
            ),
            _food_entry(
                GOJO_RED_FOOD_TEMPLATE_ID,
                effect_id="technique-permit",
                effect_params={"technique_id": "reversal-red"},
                display_name="五条猪无量赫焰雪山",
                rarity=5,
                group_id=None,
            ),
        ],
    )
    identities = (
        _identity(user_id="100", message_id="gojo-domain-owner", display_name="领域发动者"),
        _identity(user_id="200", message_id="gojo-domain-catcher", display_name="抓猪者"),
        _identity(user_id="300", message_id="gojo-domain-blue", display_name="苍蓝获得者"),
        _identity(user_id="400", message_id="gojo-domain-red", display_name="赫焰获得者"),
    )
    framework = FrameworkService(database)
    for identity in identities:
        await framework.touch_identity(identity)
    techniques = TechniqueRepository()
    async with database.transaction() as session:
        await techniques.grant_permit(
            session,
            player_id=identities[0].player_id,
            technique_id=TECHNIQUE_MALEVOLENT_KITCHEN,
            uses=1,
            now="2026-08-27T04:00:00.000Z",
        )

    counters = {"id": 0, "code": 0}

    def next_id() -> str:
        counters["id"] += 1
        return f"gojo-domain-object-{counters['id']}"

    def next_code() -> str:
        counters["code"] += 1
        return f"GJ{counters['code']:06d}"

    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        clock=MutableClock(datetime(2026, 8, 27, 4, 0, tzinfo=UTC)),
        random_source=SequenceRandom(
            *_catch_rolls(),
            0.5,
            0.55,
            0.99,
            0.5,
            0.5,
        ),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    await service.activate_group_technique(
        _identity(
            user_id="100",
            message_id="activate-gojo-domain",
            display_name="领域发动者",
        ),
        technique_id=TECHNIQUE_MALEVOLENT_KITCHEN,
    )
    catch_identity = _identity(
        user_id="200",
        message_id="gojo-domain-catch",
        display_name="抓猪者",
    )
    caught = await service.catch(catch_identity)
    resolution = caught.technique_resolution
    assert resolution is not None
    assert resolution.technique_id == TECHNIQUE_MALEVOLENT_KITCHEN
    assert resolution.remaining_uses == 9
    assert [food.display_name for food in resolution.generated_foods] == [
        "五条猪无量苍蓝雪山",
        "五条猪无量赫焰雪山",
    ]
    assert [food.owner_player_id for food in resolution.generated_foods] == [
        identities[2].player_id,
        identities[3].player_id,
    ]
    assert len({food.owner_player_id for food in resolution.generated_foods}) == 2
    assert "随机分给两名不同群友，一人一道" in caught.receipt.text_summary

    view = technique_catch_event_view(
        caught,
        catcher_name=catch_identity.display_name,
        catcher_player_id=catch_identity.player_id,
        group_name=catch_identity.group_name,
    )
    assert view.title == "五条猪化为苍蓝与赫焰"
    assert view.hero_value == "5 星 · 专属双菜"
    assert "苍蓝获得者" in view.rows[1].detail
    assert "赫焰获得者" in view.rows[1].detail

    food_rows = await database.fetch_all(
        """
        SELECT owner_player_id, template_id, random_snapshot_json
        FROM food_instances
        WHERE source_pig_instance_id = ?
        ORDER BY template_id
        """,
        (caught.pig.pig_instance_id,),
    )
    assert {
        (str(row["template_id"]), str(row["owner_player_id"]))
        for row in food_rows
    } == {
        (GOJO_BLUE_FOOD_TEMPLATE_ID, identities[2].player_id),
        (GOJO_RED_FOOD_TEMPLATE_ID, identities[3].player_id),
    }
    for row in food_rows:
        snapshot = json.loads(str(row["random_snapshot_json"]))
        assert snapshot["domain_gojo_dual_recipe"] is True
        assert snapshot["domain_gojo_self_caught"] is False
        assert snapshot["special_template_id"] == str(row["template_id"])
        assert snapshot["recipient_player_ids"] == [
            identities[2].player_id,
            identities[3].player_id,
        ]

    replayed = await service.catch(catch_identity)
    assert replayed.receipt_created is False
    assert replayed.technique_resolution == resolution
    await database.close()


@pytest.mark.asyncio
async def test_malevolent_kitchen_gojo_self_catch_gives_both_foods_to_activator(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry(
                GOJO_PIG_TEMPLATE_ID,
                rarity=5,
                display_name="五条猪",
            ),
            _food_entry(
                GOJO_BLUE_FOOD_TEMPLATE_ID,
                effect_id="technique-permit",
                effect_params={"technique_id": "lapse-blue"},
                display_name="五条猪无量苍蓝雪山",
                rarity=5,
                group_id=None,
            ),
            _food_entry(
                GOJO_RED_FOOD_TEMPLATE_ID,
                effect_id="technique-permit",
                effect_params={"technique_id": "reversal-red"},
                display_name="五条猪无量赫焰雪山",
                rarity=5,
                group_id=None,
            ),
        ],
    )
    identity = _identity(
        user_id="100",
        message_id="single-gojo-domain-owner",
        display_name="唯一玩家",
    )
    await FrameworkService(database).touch_identity(identity)
    techniques = TechniqueRepository()
    async with database.transaction() as session:
        await techniques.grant_permit(
            session,
            player_id=identity.player_id,
            technique_id=TECHNIQUE_MALEVOLENT_KITCHEN,
            uses=1,
            now="2026-08-27T04:00:00.000Z",
        )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        clock=MutableClock(datetime(2026, 8, 27, 4, 0, tzinfo=UTC)),
        random_source=SequenceRandom(*_catch_rolls(), 0.5, 0.5, 0.5),
    )
    await service.activate_group_technique(
        _identity(
            user_id="100",
            message_id="activate-single-gojo-domain",
            display_name="唯一玩家",
        ),
        technique_id=TECHNIQUE_MALEVOLENT_KITCHEN,
    )
    caught = await service.catch(
        _identity(
            user_id="100",
            message_id="single-gojo-domain-catch",
            display_name="唯一玩家",
        )
    )
    resolution = caught.technique_resolution
    assert resolution is not None
    assert [food.owner_player_id for food in resolution.generated_foods] == [
        identity.player_id,
        identity.player_id,
    ]
    assert [food.display_name for food in resolution.generated_foods] == [
        "五条猪无量苍蓝雪山",
        "五条猪无量赫焰雪山",
    ]
    assert "由发动者本人全部获得" in caught.receipt.text_summary
    view = technique_catch_event_view(
        caught,
        catcher_name=identity.display_name,
        catcher_player_id=identity.player_id,
        group_name=identity.group_name,
    )
    assert view.subtitle == "发动者亲自抓获五条猪，两道专属雪山全部归发动者"
    food_rows = await database.fetch_all(
        """
        SELECT owner_player_id, template_id, random_snapshot_json
        FROM food_instances
        WHERE source_pig_instance_id = ?
        ORDER BY template_id
        """,
        (caught.pig.pig_instance_id,),
    )
    assert len(food_rows) == 2
    assert {str(row["owner_player_id"]) for row in food_rows} == {
        identity.player_id
    }
    assert {str(row["template_id"]) for row in food_rows} == {
        GOJO_BLUE_FOOD_TEMPLATE_ID,
        GOJO_RED_FOOD_TEMPLATE_ID,
    }
    for row in food_rows:
        snapshot = json.loads(str(row["random_snapshot_json"]))
        assert snapshot["domain_gojo_self_caught"] is True
        assert snapshot["recipient_rolls"] == []
        assert snapshot["recipient_player_ids"] == [
            identity.player_id,
            identity.player_id,
        ]
    effect = await database.fetch_one(
        """
        SELECT remaining_uses
        FROM group_technique_effects
        WHERE scope_id = 'qq:100' AND status = 'active'
        """
    )
    assert effect is not None and effect["remaining_uses"] == 9
    await database.close()
