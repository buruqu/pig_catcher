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
from pig_catcher.services import (
    AssetCatalogService,
    FrameworkService,
    GameplayService,
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
async def test_default_frequency_is_twenty_per_day_with_twenty_second_cooldown(
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
                for _ in range(20)
                for roll in _catch_rolls()
            )
        ),
        clock=clock,
    )
    await service.catch(_identity(message_id="default-1"))
    with pytest.raises(CatchCooldownError, match="20"):
        await service.catch(_identity(message_id="default-2"))
    for index in range(2, 21):
        clock.value += timedelta(seconds=20)
        await service.catch(_identity(message_id=f"default-{index}"))
    clock.value += timedelta(seconds=20)
    with pytest.raises(DailyCatchLimitError, match="20/20"):
        await service.catch(_identity(message_id="default-21"))
    profile = await service.profile(_identity(message_id="default-profile"))
    assert profile.daily_count == 20
    assert profile.daily_limit == 20
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
async def test_cooldown_does_not_reset_at_beijing_midnight(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        [_pig_entry("one-pig", rarity=1)],
    )
    clock = MutableClock(datetime(2026, 7, 28, 15, 59, 50, tzinfo=UTC))
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=60, daily_limit=2),
        random_source=SequenceRandom(*_catch_rolls()),
        clock=clock,
    )
    await service.catch(_identity(message_id="before-midnight"))
    clock.value += timedelta(seconds=20)
    with pytest.raises(CatchCooldownError, match="40"):
        await service.catch(_identity(message_id="after-midnight"))
    profile = await service.profile(_identity(message_id="profile"))
    assert profile.daily_count == 0
    assert profile.cooldown_remaining_seconds == 40
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
    assert caught.pig.size_percentile == pytest.approx(0.62)
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
