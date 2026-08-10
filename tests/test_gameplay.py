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
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.rendering import pig_card_view, profile_view
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
) -> dict[str, object]:
    group_only = group_id is not None
    return {
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
        "recipe_tags": ["测试"],
    }


def _food_entry(
    template_id: str,
    *,
    effect_id: str,
    effect_params: dict[str, object],
) -> dict[str, object]:
    return {
        "template_id": template_id,
        "kind": "food",
        "display_name": "专属次数测试菜",
        "rarity": 6,
        "scope": "group",
        "group_scope_id": "qq:100",
        "description": "用于验证六星菜专属抓猪次数。",
        "image": f"{template_id}.png",
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "granted",
        "recipe_tags": ["测试"],
        "effect_id": effect_id,
        "effect_params": effect_params,
    }


async def _database_with_catalog(
    tmp_path: Path,
    entries: list[dict[str, object]],
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
                "manifest_version": 2,
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
    assert (
        profile.level_catch_adjusted_high_percent
        > profile.level_catch_base_high_percent
    )
    assert profile.level_cooking_bonus_percent == pytest.approx(10.0)
    profile_card = profile_view(profile)
    assert profile_card.level_bonus_cap_level == 21
    assert "等级概率加成：抓猪 4-6 星" in format_profile_summary(profile)
    assert "普通做菜高档权重 +10.00%" in format_profile_summary(profile)
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
async def test_coin_bounty_tag_doubles_coins_and_increases_experience_once(
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
    assert (result.coin_reward, result.experience_reward) == (4, 8)
    assert result.item_name == "猪币悬赏牌"
    row = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'coin-bounty-tag'",
        (identity.player_id,),
    )
    assert row is not None and row["quantity"] == 0
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
