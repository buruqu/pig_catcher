"""Fourth-round cooking and economy integration tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.config.model import (
    CatchingSection,
    CookingSection,
    EconomySection,
    RankingSection,
    TradingSection,
)
from pig_catcher.domain.economy import (
    COOK_COIN_REWARDS,
    COOK_EXPERIENCE_REWARDS,
    EAT_EXPERIENCE_REWARDS,
    FOOD_BASE_VALUES,
    adjusted_cooking_weights,
    cookware_higher_rarity_multiplier,
    level_cooking_higher_rarity_multiplier,
)
from pig_catcher.domain.enums import AssetKind, Rarity
from pig_catcher.domain.errors import (
    CookCooldownError,
    CookingTemplateError,
    DailyCatchLimitError,
    FoodEffectError,
    InsufficientBalanceError,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.repositories import EconomyRepository, FrameworkRepository
from pig_catcher.rendering import food_card_view, store_view
from pig_catcher.services import (
    AssetCatalogService,
    CatchQuotaResetService,
    EconomyService,
    GameplayService,
    SocialService,
    format_store_summary,
)
from pig_catcher.services.command_state import iso_timestamp


class SequenceRandom:
    """Deterministic random source that rejects unexpected draws."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def random(self) -> float:
        if not self.values:
            raise AssertionError("deterministic random source was exhausted")
        return self.values.pop(0)


class FixedClock:
    """Stable UTC clock."""

    def __init__(self) -> None:
        self.value = datetime(2026, 7, 28, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def test_six_star_food_has_extreme_value_and_rewards() -> None:
    assert FOOD_BASE_VALUES[Rarity.SIX] == 25000
    assert COOK_COIN_REWARDS[Rarity.SIX] == 1500
    assert COOK_EXPERIENCE_REWARDS[Rarity.SIX] == 800
    assert EAT_EXPERIENCE_REWARDS[Rarity.SIX] == 1200


def _identity(
    *,
    group_id: str = "100",
    user_id: str = "200",
    message_id: str,
) -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", group_id),
        stream_id=f"stream-{group_id}",
        user_id=user_id,
        display_name=f"成员{user_id}",
        message_id=message_id,
        group_name=f"测试群{group_id}",
    )


def _pig_entry(
    rarity: int,
    *,
    group_id: str | None = None,
    template_suffix: str = "",
    paired_food_template_id: str = "",
) -> dict[str, object]:
    group_only = group_id is not None
    suffix = f"-{template_suffix}" if template_suffix else ""
    return {
        "template_id": f"pig-{rarity}-{'group' if group_only else 'common'}{suffix}",
        "kind": "pig",
        "display_name": f"{rarity}星测试猪",
        "rarity": rarity,
        "scope": "group" if group_only else "common",
        "group_scope_id": f"qq:{group_id}" if group_only else None,
        "description": "第四轮测试原料猪",
        "image": f"pig-{rarity}{suffix}.png",
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "granted" if group_only else "not-required",
        "length_min_cm": 30,
        "length_max_cm": 70,
        "weight_min_kg": 20,
        "weight_max_kg": 120,
        "fat_profile": "balanced",
        "recipe_tags": ["家常"],
        "paired_food_template_id": paired_food_template_id,
    }


def _food_entry(
    rarity: int,
    *,
    group_id: str | None = None,
    effect_id: str = "",
    effect_params: dict[str, object] | None = None,
    template_suffix: str = "",
) -> dict[str, object]:
    group_only = group_id is not None
    suffix = f"-{template_suffix}" if template_suffix else ""
    return {
        "template_id": f"food-{rarity}-{'group' if group_only else 'common'}{suffix}",
        "kind": "food",
        "display_name": f"{rarity}星测试菜",
        "rarity": rarity,
        "scope": "group" if group_only else "common",
        "group_scope_id": f"qq:{group_id}" if group_only else None,
        "description": "第四轮测试美食",
        "image": f"food-{rarity}{suffix}.png",
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "granted" if group_only else "not-required",
        "recipe_tags": ["家常"],
        "effect_id": effect_id,
        "effect_params": effect_params or {},
    }


async def _database_with_catalog(
    tmp_path: Path,
    *,
    pig_rarities: tuple[int, ...] = (1,),
    food_rarities: tuple[int, ...] = (1, 2, 3),
    group_id: str = "100",
    effect_ids: dict[int, str] | None = None,
    effect_params: dict[int, dict[str, object]] | None = None,
    extra_entries: tuple[dict[str, object], ...] = (),
    manifest_version: int = 2,
) -> PigCatcherDatabase:
    source = tmp_path / "source"
    source.mkdir()
    entries: list[dict[str, object]] = []
    for rarity in pig_rarities:
        entries.append(
            _pig_entry(
                rarity,
                group_id=group_id if rarity == 6 else None,
                paired_food_template_id=(
                    "food-6-group"
                    if rarity == 6 and manifest_version >= 4
                    else ""
                ),
            )
        )
    for rarity in food_rarities:
        entries.append(
            _food_entry(
                rarity,
                group_id=group_id if rarity == 6 else None,
                effect_id=(effect_ids or {}).get(rarity, ""),
                effect_params=(effect_params or {}).get(rarity),
            )
        )
    entries.extend(extra_entries)
    for index, entry in enumerate(entries):
        image_path = source / str(entry["image"])
        if not image_path.exists():
            Image.new(
                "RGBA",
                (64, 64),
                (255, 160 + index % 80, 190 + index % 60, 255),
            ).save(image_path, format="PNG")
    manifest = source / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": manifest_version,
                "catalog_id": "fourth-round-tests",
                "source_label": "pytest fourth-round catalog",
                "entries": entries,
            },
            ensure_ascii=False,
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


async def _catch_one_star(
    database: PigCatcherDatabase,
    *,
    clock: FixedClock,
    message_id: str = "catch-1",
    short_code: str = "A19F2C3D",
) -> tuple[GameplayService, object]:
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter((f"pig-{message_id}", f"ledger-{message_id}")).__next__,
        short_code_factory=lambda: short_code,
    )
    result = await service.catch(_identity(message_id=message_id))
    return service, result


async def _grant_coins(
    database: PigCatcherDatabase,
    identity: CommandIdentity,
    amount: int,
) -> None:
    now = iso_timestamp(datetime(2026, 7, 28, 3, 0, tzinfo=UTC))
    async with database.transaction() as session:
        await FrameworkRepository().touch_identity(
            session,
            identity=identity,
            now=now,
        )
        balance = await EconomyRepository().apply_currency_change(
            session,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            amount=amount,
            reason_code="test-grant",
            reason_text="测试入账",
            source_object_type="test",
            source_object_id="seed",
            ledger_entry_id=f"seed-{identity.message_id}",
            idempotency_key=f"seed-{identity.player_id}",
            now=now,
        )
        assert balance == amount


def test_cooking_weight_hard_boundaries() -> None:
    for source_rarity in range(1, 6):
        weights = adjusted_cooking_weights(
            source_rarity,
            size_percentile=1.0,
            weight_percentile=1.0,
            cookware_level=5,
            chef_spice=True,
        )
        assert weights[5] == 0.0
        assert sum(weights) == pytest.approx(100.0)
    assert adjusted_cooking_weights(
        6,
        size_percentile=1.0,
        weight_percentile=1.0,
        cookware_level=5,
        chef_spice=True,
    ) == (0.0, 0.0, 0.0, 0.0, 90.0, 10.0)
    assert adjusted_cooking_weights(
        6,
        size_percentile=1.0,
        weight_percentile=1.0,
        cookware_level=5,
        chef_spice=False,
        super_chef_spice=True,
    ) == (0.0, 0.0, 0.0, 0.0, 85.0, 15.0)


def test_level_and_cookware_probability_bonuses_are_exact_and_bounded() -> None:
    baseline = adjusted_cooking_weights(
        3,
        size_percentile=0.5,
        weight_percentile=0.5,
        cookware_level=0,
        player_level=1,
        chef_spice=False,
    )
    boosted = adjusted_cooking_weights(
        3,
        size_percentile=0.5,
        weight_percentile=0.5,
        cookware_level=5,
        player_level=21,
        chef_spice=False,
    )
    assert tuple(
        round((cookware_higher_rarity_multiplier(level) - 1.0) * 100)
        for level in range(6)
    ) == (0, 2, 4, 6, 8, 10)
    assert level_cooking_higher_rarity_multiplier(1) == 1.0
    assert level_cooking_higher_rarity_multiplier(21) == pytest.approx(1.05)
    assert level_cooking_higher_rarity_multiplier(999) == pytest.approx(1.05)
    assert sum(boosted[3:5]) > sum(baseline[3:5])
    assert adjusted_cooking_weights(
        6,
        size_percentile=1.0,
        weight_percentile=1.0,
        cookware_level=5,
        player_level=999,
        chef_spice=True,
    ) == (0.0, 0.0, 0.0, 0.0, 90.0, 10.0)


@pytest.mark.asyncio
async def test_cooking_commits_once_and_rehydrates_after_restart(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    identity = _identity(message_id="cook-1")
    first_service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("food-1", "cook-ledger-1")).__next__,
        short_code_factory=lambda: "B19F2C3D",
    )
    first = await first_service.cook(identity, caught.pig.selector)
    assert first.receipt_created is True
    assert first.foods[0].rarity == 1
    assert first.coin_balance == 5
    assert first.total_experience == 9
    assert "等级：Lv.1 · 被猪拱；9/50 EXP" in first.receipt.text_summary
    card = food_card_view(first.foods[0], mode_label="做菜成功", cooking=first)
    assert card.player_level == 1
    assert card.level_title == "被猪拱"
    assert card.next_level_experience == 50
    assert card.level_progress_percent == pytest.approx(18.0)

    duplicate = await first_service.cook(identity, caught.pig.selector)
    assert duplicate.receipt_created is False
    await database.close()
    await database.open()
    after_restart = await EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(),
        clock=clock,
    ).cook(identity, caught.pig.selector)
    assert after_restart.receipt_created is False

    pig = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = ?",
        (caught.pig.pig_instance_id,),
    )
    food_count = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM food_instances"
    )
    cook_receipts = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM command_receipts WHERE command_name = ?",
        ("pig-catcher.cook",),
    )
    assert pig is not None and pig["state"] == "consumed-for-cooking"
    assert food_count is not None and food_count["count"] == 1
    assert cook_receipts is not None and cook_receipts["count"] == 1
    await database.close()


@pytest.mark.asyncio
async def test_numeric_level_changes_the_committed_cooking_probability(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        food_rarities=(1, 2),
    )
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    identity = _identity(message_id="level-cook")
    async with database.transaction() as session:
        await session.execute(
            "UPDATE players SET experience = 20000 WHERE player_id = ?",
            (identity.player_id,),
        )
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.705, 0.0, 0.5),
        clock=clock,
        short_code_factory=lambda: "C19F2C3D",
    )

    result = await service.cook(identity, caught.pig.selector)
    assert result.foods[0].rarity == 2
    snapshot_row = await database.fetch_one(
        """
        SELECT random_snapshot_json
        FROM food_instances
        WHERE food_instance_id = ?
        """,
        (result.foods[0].food_instance_id,),
    )
    assert snapshot_row is not None
    snapshot = json.loads(str(snapshot_row["random_snapshot_json"]))
    assert snapshot["player_level"] == 21
    await database.close()


@pytest.mark.asyncio
async def test_missing_food_template_rolls_back_pig_and_item(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        food_rarities=(1,),
    )
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    identity = _identity(message_id="cook-missing")
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        await session.execute(
            "INSERT INTO item_inventory(player_id, item_id, quantity, updated_at) "
            "VALUES (?, 'chef-spice', 1, ?)",
            (identity.player_id, now),
        )
        await session.execute(
            "INSERT INTO armed_items(player_id, action_type, item_id, armed_at) "
            "VALUES (?, 'cooking', 'chef-spice', ?)",
            (identity.player_id, now),
        )
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.99),
        clock=clock,
    )
    with pytest.raises(CookingTemplateError, match="原料猪未消耗"):
        await service.cook(identity, caught.pig.selector)
    pig = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = ?",
        (caught.pig.pig_instance_id,),
    )
    item = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'chef-spice'",
        (identity.player_id,),
    )
    assert pig is not None and pig["state"] == "active"
    assert item is not None and item["quantity"] == 1
    await database.close()


@pytest.mark.asyncio
async def test_large_lunch_box_produces_two_foods_and_consumes_once(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    identity = _identity(message_id="cook-lunch-box")
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        await session.execute(
            "INSERT INTO item_inventory(player_id, item_id, quantity, updated_at) "
            "VALUES (?, 'large-lunch-box', 1, ?)",
            (identity.player_id, now),
        )
        await session.execute(
            "INSERT INTO armed_items(player_id, action_type, item_id, armed_at) "
            "VALUES (?, 'cooking', 'large-lunch-box', ?)",
            (identity.player_id, now),
        )
    short_codes = iter(("B19F2C3D", "B19F2C3D", "C19F2C3D"))
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.4, 0.1, 0.8),
        clock=clock,
        id_factory=iter(("food-main", "food-bonus", "cook-ledger")).__next__,
        short_code_factory=short_codes.__next__,
    )
    result = await service.cook(identity, caught.pig.selector)
    assert result.bonus_serving is True
    assert len(result.foods) == 2
    assert [food.short_code for food in result.foods] == [
        "B19F2C3D",
        "C19F2C3D",
    ]
    assert result.foods[0].rarity == result.foods[1].rarity
    assert result.coin_reward == 3
    item = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'large-lunch-box'",
        (identity.player_id,),
    )
    assert item is not None and item["quantity"] == 0
    await database.close()


@pytest.mark.asyncio
async def test_super_chef_spice_turns_six_star_cook_to_15_percent_and_consumes_once(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(6,),
        food_rarities=(5, 6),
        manifest_version=4,
    )
    clock = FixedClock()
    catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("super-source-pig", "super-source-ledger")).__next__,
        short_code_factory=lambda: "C19F2C3D",
    )
    source = await catching.catch(_identity(message_id="super-source"))
    identity = _identity(message_id="super-cook")
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        await session.execute(
            "INSERT INTO item_inventory(player_id, item_id, quantity, updated_at) "
            "VALUES (?, 'super-chef-spice', 1, ?)",
            (identity.player_id, now),
        )
        await session.execute(
            "INSERT INTO armed_items(player_id, action_type, item_id, armed_at) "
            "VALUES (?, 'cooking', 'super-chef-spice', ?)",
            (identity.player_id, now),
        )
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.86, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("super-food", "super-cook-ledger")).__next__,
        short_code_factory=lambda: "C29F2C3D",
    )

    result = await economy.cook(identity, source.pig.selector)

    assert result.foods[0].rarity == 6
    assert result.weights == (0.0, 0.0, 0.0, 0.0, 85.0, 15.0)
    assert result.item_name == "超级主厨香料"
    item = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'super-chef-spice'",
        (identity.player_id,),
    )
    assert item is not None and item["quantity"] == 0
    await database.close()


@pytest.mark.asyncio
async def test_incompatible_super_chef_spice_stays_armed_for_a_later_six_star_pig(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    identity = _identity(message_id="regular-cook-with-super-spice")
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        await session.execute(
            "INSERT INTO item_inventory(player_id, item_id, quantity, updated_at) "
            "VALUES (?, 'super-chef-spice', 1, ?)",
            (identity.player_id, now),
        )
        await session.execute(
            "INSERT INTO armed_items(player_id, action_type, item_id, armed_at) "
            "VALUES (?, 'cooking', 'super-chef-spice', ?)",
            (identity.player_id, now),
        )
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("regular-food", "regular-cook-ledger")).__next__,
        short_code_factory=lambda: "D39F2C3D",
    )

    result = await economy.cook(identity, caught.pig.selector)

    assert result.item_name == ""
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
    assert row is not None and tuple(row) == (1, "super-chef-spice")
    await database.close()


@pytest.mark.asyncio
async def test_eating_unknown_effect_does_not_consume_then_blank_effect_is_idempotent(
    tmp_path: Path,
) -> None:
    unknown_root = tmp_path / "unknown"
    unknown_root.mkdir()
    database = await _database_with_catalog(
        unknown_root,
        food_rarities=(1,),
        effect_ids={1: "unknown-effect"},
    )
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    cooking = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("food-unknown", "ledger-unknown")).__next__,
        short_code_factory=lambda: "B19F2C3D",
    )
    cooked = await cooking.cook(
        _identity(message_id="cook-unknown"),
        caught.pig.selector,
    )
    with pytest.raises(FoodEffectError, match="不会消耗"):
        await cooking.eat(
            _identity(message_id="eat-unknown"),
            cooked.foods[0].selector,
        )
    state = await database.fetch_one(
        "SELECT state FROM food_instances WHERE food_instance_id = 'food-unknown'"
    )
    assert state is not None and state["state"] == "active"
    await database.close()

    blank_root = tmp_path / "blank"
    blank_root.mkdir()
    database = await _database_with_catalog(blank_root, food_rarities=(1,))
    _, caught = await _catch_one_star(database, clock=clock)
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("food-blank", "ledger-blank")).__next__,
        short_code_factory=lambda: "D19F2C3D",
    )
    cooked = await service.cook(
        _identity(message_id="cook-blank"),
        caught.pig.selector,
    )
    eat_identity = _identity(message_id="eat-blank")
    first = await service.eat(eat_identity, cooked.foods[0].selector)
    duplicate = await service.eat(eat_identity, cooked.foods[0].selector)
    assert first.base_experience == 8
    assert duplicate.receipt_created is False
    state = await database.fetch_one(
        "SELECT state FROM food_instances WHERE food_instance_id = 'food-blank'"
    )
    assert state is not None and state["state"] == "consumed"
    await database.close()


@pytest.mark.asyncio
async def test_high_rarity_food_effect_survives_restart_and_is_consumed_once(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 2, 3, 4, 5),
        food_rarities=(4,),
        effect_ids={4: "next-catch-quality"},
        effect_params={4: {"multiplier": 1.35}},
    )
    clock = FixedClock()
    source_catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.94, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("effect-source-pig", "effect-source-ledger")).__next__,
        short_code_factory=lambda: "E19F2C3D",
    )
    source = await source_catching.catch(_identity(message_id="effect-source"))
    assert source.pig.rarity == 4

    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.5, 0.0, 0.5),
        clock=clock,
        id_factory=iter(
            ("effect-food", "effect-cook-ledger", "effect-queue-entry")
        ).__next__,
        short_code_factory=lambda: "F19F2C3D",
    )
    cooked = await economy.cook(
        _identity(message_id="effect-cook"),
        source.pig.selector,
    )
    eaten = await economy.eat(
        _identity(message_id="effect-eat"),
        cooked.foods[0].selector,
    )
    assert eaten.effect.queued_effect_id == "next-catch-quality"
    queued = await database.fetch_one(
        """
        SELECT consumed_uses
        FROM player_food_effects
        WHERE effect_entry_id = 'effect-queue-entry'
        """
    )
    assert queued is not None and queued["consumed_uses"] == 0

    restarted_catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("effect-next-pig", "effect-next-ledger")).__next__,
        short_code_factory=lambda: "A29F2C3D",
    )
    catch_identity = _identity(message_id="effect-next-catch")
    boosted = await restarted_catching.catch(catch_identity)
    duplicate = await restarted_catching.catch(catch_identity)
    assert boosted.effect_summaries
    assert duplicate.receipt_created is False
    consumed = await database.fetch_one(
        """
        SELECT consumed_uses
        FROM player_food_effects
        WHERE effect_entry_id = 'effect-queue-entry'
        """
    )
    assert consumed is not None and consumed["consumed_uses"] == 1
    await database.close()


@pytest.mark.asyncio
async def test_extra_catch_food_extends_today_limit_and_consumes_only_bonus_uses(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(5,),
        food_rarities=(5,),
        effect_ids={5: "extra-catches"},
        effect_params={5: {"count": 2}},
    )
    clock = FixedClock()
    source_catching = GameplayService(
        database,
        CatchingSection(daily_limit=1, cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("quota-source-pig", "quota-source-ledger")).__next__,
        short_code_factory=lambda: "B29F2C3D",
    )
    source = await source_catching.catch(_identity(message_id="quota-source"))
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.5, 0.0, 0.5),
        clock=clock,
        id_factory=iter(
            ("quota-food", "quota-cook-ledger", "quota-effect")
        ).__next__,
        short_code_factory=lambda: "C29F2C3D",
    )
    cooked = await economy.cook(
        _identity(message_id="quota-cook"),
        source.pig.selector,
    )
    await economy.eat(
        _identity(message_id="quota-eat"),
        cooked.foods[0].selector,
    )

    values = (
        0.0,
        0.0,
        0.5,
        0.5,
        0.5,
        0.5,
        0.5,
    ) * 2
    bonus_catching = GameplayService(
        database,
        CatchingSection(daily_limit=1, cooldown_seconds=0),
        random_source=SequenceRandom(*values),
        clock=clock,
        id_factory=iter(
            (
                "quota-bonus-pig-1",
                "quota-bonus-ledger-1",
                "quota-bonus-pig-2",
                "quota-bonus-ledger-2",
            )
        ).__next__,
        short_code_factory=iter(("D29F2C3D", "E29F2C3D")).__next__,
    )
    first = await bonus_catching.catch(_identity(message_id="quota-bonus-1"))
    second = await bonus_catching.catch(_identity(message_id="quota-bonus-2"))
    assert (first.daily_count, first.daily_limit) == (2, 3)
    assert (second.daily_count, second.daily_limit) == (3, 3)
    with pytest.raises(DailyCatchLimitError):
        await bonus_catching.catch(_identity(message_id="quota-bonus-3"))
    effect = await database.fetch_one(
        """
        SELECT granted_uses, consumed_uses
        FROM player_food_effects
        WHERE effect_entry_id = 'quota-effect'
        """
    )
    assert effect is not None
    assert (effect["granted_uses"], effect["consumed_uses"]) == (2, 2)
    clock.value += timedelta(hours=7)
    refreshed_profile = await bonus_catching.profile(
        _identity(message_id="quota-after-refresh-profile")
    )
    assert (refreshed_profile.daily_count, refreshed_profile.daily_limit) == (0, 1)
    refreshed_catching = GameplayService(
        database,
        CatchingSection(daily_limit=1, cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(
            ("quota-refreshed-pig", "quota-refreshed-ledger")
        ).__next__,
        short_code_factory=lambda: "F29F2C3D",
    )
    refreshed_catch = await refreshed_catching.catch(
        _identity(message_id="quota-after-refresh-catch")
    )
    assert (refreshed_catch.daily_count, refreshed_catch.daily_limit) == (1, 1)
    clock.value += timedelta(seconds=1)
    reset = CatchQuotaResetService(
        database,
        refresh_hours=[0, 9, 12, 19],
        timezone_name="Asia/Shanghai",
        window_limit=1,
        clock=clock,
    )
    await reset.backup_and_reset_current_window(
        data_dir=tmp_path,
        group_id="100",
        actor_user_id="test-admin",
        source="pytest",
    )
    profile = await refreshed_catching.profile(
        _identity(message_id="quota-after-reset-profile")
    )
    assert (profile.daily_count, profile.daily_limit) == (0, 1)
    await database.close()


@pytest.mark.asyncio
async def test_weekly_window_food_adds_five_to_every_window_without_stacking(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(5,),
        food_rarities=(5,),
        effect_ids={5: "weekly-window-catches"},
        effect_params={5: {"count": 5}},
    )
    clock = FixedClock()
    catching = GameplayService(
        database,
        CatchingSection(daily_limit=1, cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("weekly-source-pig", "weekly-source-ledger")).__next__,
        short_code_factory=lambda: "E19F2C3D",
    )
    source = await catching.catch(_identity(message_id="weekly-source"))
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.5, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("weekly-food", "weekly-cook-ledger")).__next__,
        short_code_factory=lambda: "E29F2C3D",
    )
    cooked = await economy.cook(_identity(message_id="weekly-cook"), source.pig.selector)
    eaten = await economy.eat(_identity(message_id="weekly-eat"), cooked.foods[0].selector)

    assert eaten.effect.queued_effect_id == "weekly-window-catches"
    profile = await catching.profile(_identity(message_id="weekly-profile"))
    assert (profile.daily_count, profile.daily_limit) == (1, 6)
    row = await database.fetch_one(
        "SELECT weekly_bonus, weekly_expires_at FROM player_catch_quota_bonuses WHERE player_id = ?",
        (source.pig.owner_player_id,),
    )
    assert row is not None and row["weekly_bonus"] == 5 and row["weekly_expires_at"]
    async with database.transaction() as session:
        granted_again = await EconomyRepository().grant_weekly_catch_bonus(
            session,
            player_id=source.pig.owner_player_id,
            source_food_instance_id="another-food",
            count=5,
            expires_at=str(row["weekly_expires_at"]),
            now=iso_timestamp(clock.now()),
        )
    assert granted_again is False

    clock.value += timedelta(days=7)
    expired = await catching.profile(_identity(message_id="weekly-expired-profile"))
    assert expired.daily_limit == 1
    await database.close()


@pytest.mark.asyncio
async def test_permanent_window_food_refuses_to_consume_at_plus_five_cap(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(5,),
        food_rarities=(5,),
        effect_ids={5: "permanent-window-catch"},
        effect_params={5: {"count": 1, "max_bonus": 5}},
    )
    clock = FixedClock()
    catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("permanent-source-pig", "permanent-source-ledger")).__next__,
        short_code_factory=lambda: "F19F2C3D",
    )
    source = await catching.catch(_identity(message_id="permanent-source"))
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.5, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("permanent-food", "permanent-cook-ledger")).__next__,
        short_code_factory=lambda: "F29F2C3D",
    )
    cooked = await economy.cook(
        _identity(message_id="permanent-cook"),
        source.pig.selector,
    )
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        for index in range(5):
            total = await EconomyRepository().increment_permanent_catch_bonus(
                session,
                player_id=source.pig.owner_player_id,
                source_food_instance_id=f"seed-permanent-{index}",
                count=1,
                max_bonus=5,
                now=now,
            )
            assert total == index + 1

    with pytest.raises(FoodEffectError, match=r"达到 \+5 上限"):
        await economy.eat(
            _identity(message_id="permanent-eat-at-cap"),
            cooked.foods[0].selector,
        )
    food = await database.fetch_one(
        "SELECT state FROM food_instances WHERE food_instance_id = ?",
        (cooked.foods[0].food_instance_id,),
    )
    assert food is not None and food["state"] == "active"
    await database.close()


@pytest.mark.asyncio
async def test_empty_selector_sells_cheapest_low_star_then_batch_sells_rest_once(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1,),
    )
    clock = FixedClock()
    rolls = (
        0.0,
        0.0,
        0.1,
        0.1,
        0.1,
        0.1,
        0.1,
        0.0,
        0.0,
        0.5,
        0.5,
        0.5,
        0.5,
        0.5,
        0.0,
        0.0,
        0.9,
        0.9,
        0.9,
        0.9,
        0.9,
    )
    catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(*rolls),
        clock=clock,
        id_factory=iter(
            (
                "cheap-pig-1",
                "cheap-ledger-1",
                "cheap-pig-2",
                "cheap-ledger-2",
                "cheap-pig-3",
                "cheap-ledger-3",
            )
        ).__next__,
        short_code_factory=iter(
            ("F29F2C3D", "A39F2C3D", "B39F2C3D")
        ).__next__,
    )
    for index in range(3):
        await catching.catch(_identity(message_id=f"cheap-catch-{index}"))
    cheapest = await database.fetch_one(
        """
        SELECT pig_instance_id
        FROM pig_instances
        WHERE owner_player_id = 'qq:100:200' AND state = 'active'
        ORDER BY official_value, acquired_at, pig_instance_id
        LIMIT 1
        """
    )
    assert cheapest is not None

    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(("cheap-sale-ledger", "batch-sale-ledger")).__next__,
    )
    sold = await economy.sell_pig(
        _identity(message_id="cheap-auto-sale"),
        "",
    )
    assert sold.pig is not None
    assert sold.pig.pig_instance_id == cheapest["pig_instance_id"]

    batch_identity = _identity(message_id="cheap-batch-sale")
    batch = await economy.batch_sell_low_rarity(
        batch_identity,
        asset_kind="pig",
    )
    duplicate = await economy.batch_sell_low_rarity(
        batch_identity,
        asset_kind="pig",
    )
    assert batch.asset_count == 2
    assert duplicate.receipt_created is False
    active = await database.fetch_one(
        """
        SELECT COUNT(*) AS asset_count
        FROM pig_instances
        WHERE owner_player_id = 'qq:100:200' AND state = 'active'
        """
    )
    assert active is not None and active["asset_count"] == 0
    await database.close()


@pytest.mark.asyncio
async def test_store_purchase_upgrade_insufficient_balance_and_ledger(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    seed_identity = _identity(message_id="seed")
    await _grant_coins(database, seed_identity, 2000)
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(
            ("purchase-ledger-1", "purchase-ledger-2", "purchase-ledger-3")
        ).__next__,
    )
    store = await service.store(seed_identity, page=1, category="全部")
    assert store.coin_balance == 2000
    assert len(store.products) == 12
    products = {product.display_name: product for product in store.products}
    assert products["超级幸运猪哨"].unit_price == 2600
    assert products["超级主厨香料"].unit_price == 2200
    store_card = store_view(store)
    assert tuple(row.value for row in store_card.feed_probability_rows) == (
        "13.00%",
        "13.27%",
        "13.54%",
        "13.80%",
        "14.05%",
        "14.30%",
    )
    assert tuple(row.value for row in store_card.cookware_probability_rows) == (
        "+0%",
        "+2%",
        "+4%",
        "+6%",
        "+8%",
        "+10%",
    )
    assert store_card.feed_probability_rows[0].current is True
    assert store_card.cookware_probability_rows[0].current is True
    assert tuple(
        (row.before, row.after)
        for row in store_card.lucky_whistle_rows
    ) == (
        ("40.00%", "36.00%"),
        ("30.00%", "28.00%"),
        ("17.00%", "16.00%"),
        ("8.00%", "11.00%"),
        ("4.00%", "6.00%"),
        ("1.00%", "3.00%"),
    )
    assert tuple(row.after for row in store_card.chef_spice_rows) == (
        "1★ 60% · 2★ 37% · 3★ 3%",
        "2★ 80% · 3★ 18% · 4★ 2%",
        "2★ 5% · 3★ 75% · 4★ 18% · 5★ 2%",
        "3★ 30% · 4★ 60% · 5★ 10%",
        "4★ 30% · 5★ 70%",
    )
    store_text = format_store_summary(store)
    assert "猪饲料 Lv.0-5 的 4-6 星合计概率" in store_text
    assert "厨具 Lv.0-5 的高档菜相对权重增幅" in store_text
    assert "幸运猪哨（基础权重，使用前→使用后）" in store_text
    assert "主厨香料（基础分布、Lv.0，使用前→使用后）" in store_text
    assert "1★猪 1★ 75%、2★ 22%、3★ 3%→1★ 60%、2★ 37%、3★ 3%" in store_text

    item_identity = _identity(message_id="buy-item")
    item = await service.purchase(item_identity, "幸运猪哨", quantity=2)
    assert item.balance_after == 640
    assert item.inventory_quantity == 2
    assert (await service.purchase(item_identity, "幸运猪哨", quantity=2)).receipt_created is False

    upgrade = await service.upgrade(
        _identity(message_id="buy-upgrade"),
        "厨具",
    )
    assert upgrade.upgrade_level == 1
    assert upgrade.balance_after == 140
    with pytest.raises(InsufficientBalanceError):
        await service.upgrade(
            _identity(message_id="buy-too-expensive"),
            "猪饲料",
        )
    inventory = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'lucky-whistle'",
        (seed_identity.player_id,),
    )
    assert inventory is not None and inventory["quantity"] == 2
    ledger = await service.ledger(seed_identity, page=1)
    assert ledger.coin_balance == ledger.ledger_total == 140
    assert ledger.total_count == 3
    await database.close()


@pytest.mark.asyncio
async def test_official_sales_credit_exact_value_once(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(("sale-ledger",)).__next__,
    )
    identity = _identity(message_id="sell-pig")
    await SocialService(
        database,
        TradingSection(),
        RankingSection(),
        clock=clock,
    ).set_showcase(
        _identity(message_id="showcase-before-sale"),
        asset_kind=AssetKind.PIG,
        selector_text=caught.pig.selector,
        clear=False,
    )
    first = await service.sell_pig(identity, caught.pig.selector)
    duplicate = await service.sell_pig(identity, caught.pig.selector)
    assert first.balance_after == 2 + caught.pig.official_value
    assert duplicate.receipt_created is False
    state = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = ?",
        (caught.pig.pig_instance_id,),
    )
    assert state is not None and state["state"] == "sold"
    showcase = await database.fetch_one(
        "SELECT pig_instance_id FROM display_preferences WHERE player_id = ?",
        (identity.player_id,),
    )
    assert showcase is not None and showcase["pig_instance_id"] is None
    ledger = await service.ledger(identity, page=1)
    assert ledger.coin_balance == ledger.ledger_total
    await database.close()


@pytest.mark.asyncio
async def test_six_star_food_templates_are_group_isolated(tmp_path: Path) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 6),
        food_rarities=(1, 5, 6),
        group_id="100",
    )
    repository = EconomyRepository()
    async with database.transaction() as session:
        allowed = await repository.list_drawable_food_templates(
            session,
            scope_id="qq:100",
            rarity=6,
        )
        denied = await repository.list_drawable_food_templates(
            session,
            scope_id="qq:999",
            rarity=6,
        )
    assert len(allowed) == 1
    assert denied == []

    service = EconomyService(database, CookingSection(cook_cooldown_seconds=0), EconomySection())
    authorized_catalog = await service.food_catalog(
        _identity(group_id="100", user_id="201", message_id="authorized-food-catalog"),
        rarity=None,
        undiscovered_only=False,
    )
    group_entry = next(
        entry
        for entry in authorized_catalog.entries
        if entry.template_id == "food-6-group"
    )
    assert group_entry.discovered is False
    assert authorized_catalog.visible_catalog_total == 3

    denied_catalog = await service.food_catalog(
        _identity(group_id="999", message_id="denied-food-catalog"),
        rarity=None,
        undiscovered_only=False,
    )
    assert all(entry.template_id != "food-6-group" for entry in denied_catalog.entries)
    assert denied_catalog.visible_catalog_total == 2
    await database.close()


@pytest.mark.asyncio
async def test_six_star_pig_can_only_produce_its_paired_six_star_food(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 6),
        food_rarities=(1, 5, 6),
        group_id="100",
        manifest_version=4,
        extra_entries=(
            _pig_entry(
                6,
                group_id="100",
                template_suffix="alt",
                paired_food_template_id="food-6-group-alt",
            ),
            _food_entry(6, group_id="100", template_suffix="alt"),
        ),
    )
    clock = FixedClock()
    caught = await GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            0.999,
            0.0,
            0.5,
            0.5,
            0.5,
            0.5,
            0.5,
        ),
        clock=clock,
        id_factory=iter(("pig-six", "pig-six-ledger")).__next__,
        short_code_factory=lambda: "ABCDEF66",
    ).catch(_identity(group_id="100", message_id="catch-six-paired"))
    assert caught.pig.template_id == "pig-6-group"
    cooked = await EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.999, 0.999, 0.5),
        clock=clock,
    ).cook(
        _identity(group_id="100", message_id="cook-six-paired"),
        caught.pig.selector,
    )
    assert cooked.foods[0].template_id == "food-6-group"
    snapshot_row = await database.fetch_one(
        "SELECT random_snapshot_json FROM food_instances WHERE food_instance_id = ?",
        (cooked.foods[0].food_instance_id,),
    )
    assert snapshot_row is not None
    snapshot = json.loads(str(snapshot_row["random_snapshot_json"]))
    assert snapshot["paired_food_template_id"] == "food-6-group"
    await database.close()


@pytest.mark.asyncio
async def test_missing_six_star_pair_rolls_back_the_source_pig(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 6),
        food_rarities=(1, 5, 6),
        group_id="100",
    )
    clock = FixedClock()
    caught = await GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            0.999,
            0.0,
            0.5,
            0.5,
            0.5,
            0.5,
            0.5,
        ),
        clock=clock,
        short_code_factory=lambda: "ABCDEF67",
    ).catch(_identity(group_id="100", message_id="catch-six-unpaired"))
    cooking = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.999),
        clock=clock,
    )
    with pytest.raises(CookingTemplateError, match="原料猪未消耗"):
        await cooking.cook(
            _identity(group_id="100", message_id="cook-six-unpaired"),
            caught.pig.selector,
        )
    row = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = ?",
        (caught.pig.pig_instance_id,),
    )
    assert row is not None and row["state"] == "active"
    await database.close()


@pytest.mark.asyncio
async def test_food_catalog_returns_every_visible_entry_without_page_limit(
    tmp_path: Path,
) -> None:
    entries = tuple(
        {
            **_food_entry((index % 5) + 1),
            "template_id": f"food-extra-{index:02d}",
            "display_name": f"完整图鉴菜{index:02d}",
            "image": f"food-extra-{index:02d}.png",
        }
        for index in range(17)
    )
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(),
        extra_entries=entries,
    )
    service = EconomyService(
        database,
        CookingSection(catalog_page_size=6),
        EconomySection(),
    )
    catalog = await service.food_catalog(
        _identity(message_id="complete-food-catalog"),
        rarity=None,
        undiscovered_only=False,
    )
    assert catalog.total_count == 17
    assert len(catalog.entries) == 17
    assert [entry.rarity for entry in catalog.entries] == sorted(
        entry.rarity for entry in catalog.entries
    )
    await database.close()


def _collab_pig_entry(rarity: int, *, suffix: str) -> dict[str, object]:
    """合成一只带收藏图鉴的联动猪。"""
    entry = _pig_entry(rarity, template_suffix=suffix)
    entry["template_id"] = f"pig-collab-{suffix}"
    entry["display_name"] = f"联动猪{suffix}"
    entry["image"] = f"pig-collab-{suffix}.png"
    entry["collection"] = {
        "collaboration_name": "测试联动",
        "collection_id": f"test-collab-{suffix}",
        "collection_name": "测试系列",
        "slot": 1,
        "total": 1,
        "character_id": "test",
        "character_name": "测试角色",
        "official_profile_url": "https://bang-dream.com/artist/test/",
    }
    return entry


@pytest.mark.asyncio
async def test_cook_cooldown_blocks_second_cook_within_window(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    identity = _identity(message_id="cook-cd-1")
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=10),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("food-cd-1", "ledger-cd-1")).__next__,
        short_code_factory=lambda: "CD000001",
    )
    first = await service.cook(identity, caught.pig.selector)
    assert first.foods
    # 同一时钟下第二次做菜应立即被冷却拦截，且不消耗原料
    second_pig = await _catch_one_star(
        database, clock=clock, message_id="cook-cd-2", short_code="CD000002"
    )
    with pytest.raises(CookCooldownError):
        await service.cook(
            _identity(message_id="cook-cd-2"),
            second_pig[1].pig.selector,
        )
    leftover = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM pig_instances WHERE state = 'active'"
    )
    assert leftover is not None and leftover["count"] == 1
    await database.close()


@pytest.mark.asyncio
async def test_cook_cooldown_elapses_after_configured_seconds(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    _, caught = await _catch_one_star(database, clock=clock)
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=10),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.0, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("food-cd-a", "ledger-cd-a", "food-cd-b", "ledger-cd-b")).__next__,
        short_code_factory=iter(("CDA00001", "CDB00002")).__next__,
    )
    await service.cook(_identity(message_id="cook-cd-a"), caught.pig.selector)
    clock.value = clock.value + timedelta(seconds=11)
    second = await _catch_one_star(
        database, clock=clock, message_id="cook-cd-b", short_code="CD00000B"
    )
    result = await service.cook(
        _identity(message_id="cook-cd-b"),
        second[1].pig.selector,
    )
    assert result.foods
    await database.close()


@pytest.mark.asyncio
async def test_selector_matches_names_without_spaces_or_case(
    tmp_path: Path,
) -> None:
    from pig_catcher.domain.selectors import parse_asset_selector
    from pig_catcher.infrastructure.repositories import GameplayRepository

    entry = _pig_entry(1, template_suffix="token")
    entry["template_id"] = "pig-token-eater"
    entry["display_name"] = "白吃 Token 的猪"
    entry["image"] = "pig-token-eater.png"
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(),
        food_rarities=(),
        extra_entries=(entry,),
    )
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=FixedClock(),
        id_factory=iter(("pig-token-1", "ledger-token-1")).__next__,
        short_code_factory=lambda: "A11E8888",
    )
    identity = _identity(message_id="token-catch")
    result = await service.catch(identity)
    assert result.pig.display_name == "白吃 Token 的猪"
    repository = GameplayRepository()
    async with database.transaction() as session:
        for variant in (
            "白吃Token的猪",
            "白吃token的猪",
            "白吃 TOKEN 的猪",
            "白吃token的猪#A11E8888",
            "白吃 Token 的猪",
        ):
            rows = await repository.find_active_pigs(
                session,
                player_id=identity.player_id,
                selector=parse_asset_selector(variant),
            )
            assert len(rows) == 1, f"variant failed: {variant}"
    await database.close()


@pytest.mark.asyncio
async def test_batch_sell_keeps_collaboration_pigs_and_supports_rarity_filter(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 2),
        food_rarities=(),
        extra_entries=(_collab_pig_entry(1, suffix="a"),),
    )
    clock = FixedClock()
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            # 1 星普通猪
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            # 2 星普通猪（仅 1/2 星模板时二星区间约 0.57~1.0）
            0.8, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            # 1 星联动猪（template_roll=0.9 选中后插入的联动模板）
            0.0, 0.9, 0.5, 0.5, 0.5, 0.5, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "pig-b1", "ledger-b1",
                "pig-b2", "ledger-b2",
                "pig-b3", "ledger-b3",
            )
        ).__next__,
        short_code_factory=iter(("B1000001", "B1000002", "B1000003")).__next__,
    )
    identity = _identity(message_id="batch-sell")
    # 抓 1 星普通、2 星普通、1 星联动
    for mid in ("batch-1", "batch-2", "batch-3"):
        await service.catch(_identity(message_id=mid, user_id="200"))

    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(("ledger-bx", "ledger-by")).__next__,
    )
    # 指定品质：二星 → 只卖 2 星普通猪
    sold = await economy.batch_sell_low_rarity(
        identity,
        asset_kind="pig",
        max_rarity=3,
        rarity=2,
    )
    assert sold.asset_count == 1
    assert sold.rarity == 2
    remaining = await database.fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        """,
        (identity.player_id,),
    )
    assert remaining is not None and remaining["count"] == 2  # 1 星普通 + 1 星联动
    # 不指定品质（1-3 星）：1 星普通被卖，联动猪仍保护
    sold_all = await economy.batch_sell_low_rarity(
        _identity(message_id="batch-sell-2", user_id="200"),
        asset_kind="pig",
        max_rarity=3,
    )
    assert sold_all.asset_count == 1
    collab_left = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM pig_instances WHERE state = 'active'"
    )
    assert collab_left is not None and collab_left["count"] == 1
    await database.close()


@pytest.mark.asyncio
async def test_batch_cook_skips_collaboration_pigs_and_sorts_by_rarity_desc(
    tmp_path: Path,
) -> None:
    from pig_catcher.rendering import batch_cook_view

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1,),
        extra_entries=(_collab_pig_entry(1, suffix="a"),),
    )
    clock = FixedClock()
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            # 1 星普通猪
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            # 1 星普通猪
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            # 1 星联动猪
            0.0, 0.9, 0.5, 0.5, 0.5, 0.5, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "pig-k1", "ledger-k1",
                "pig-k2", "ledger-k2",
                "pig-k3", "ledger-k3",
            )
        ).__next__,
        short_code_factory=iter(("A1000001", "A1000002", "A1000003")).__next__,
    )
    identity = _identity(message_id="batch-cook")
    for mid in ("cook-1", "cook-2", "cook-3"):
        await service.catch(_identity(message_id=mid, user_id="200"))

    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.0, 0.0, 0.5),
        clock=clock,
        id_factory=iter(("ck-food-1", "ck-ledger-1", "ck-food-2", "ck-ledger-2")).__next__,
        short_code_factory=iter(("ABAD0001", "ABAD0002", "ABAD0003")).__next__,
    )
    result = await economy.batch_cook(identity, rarity=None)
    assert result.pig_count == 2  # 联动猪被保护
    assert result.food_count == 2
    # 联动猪仍在背包
    collab_left = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM pig_instances WHERE state = 'active'"
    )
    assert collab_left is not None and collab_left["count"] == 1
    view = batch_cook_view(result)
    assert view.food_count == 2
    rarities = [item.rarity for item in view.items]
    assert rarities == sorted(rarities, reverse=True)
    await database.close()


@pytest.mark.asyncio
async def test_batch_cook_is_blocked_while_multi_use_cook_effect_is_held(
    tmp_path: Path,
) -> None:
    from pig_catcher.domain.errors import BatchCookRestrictedError

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1, 5),
    )
    clock = FixedClock()
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "pig-m1", "ledger-m1",
                "pig-m2", "ledger-m2",
                "pig-m3", "ledger-m3",
            )
        ).__next__,
        short_code_factory=iter(("A1000001", "A1000002", "A1000003")).__next__,
    )
    identity = _identity(message_id="batch-cook-limit")
    for mid in ("cook-a", "cook-b", "cook-c"):
        await service.catch(_identity(message_id=mid, user_id="200"))

    # 先单做一次，产生一个真实美食实例作为效果来源（外键约束）
    pig_row = await database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY acquired_at DESC LIMIT 1
        """,
        (identity.player_id,),
    )
    assert pig_row is not None
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(
            0.0, 0.0, 0.5,
            0.0, 0.0, 0.5,
            0.0, 0.0, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "ec-food-1", "ec-ledger-1",
                "ec-food-2", "ec-ledger-2",
                "ec-food-3", "ec-ledger-3",
            )
        ).__next__,
        short_code_factory=iter(("ABAD0001", "ABAD0002", "ABAD0003")).__next__,
    )
    await economy.cook(
        _identity(message_id="cook-one"),
        f"{pig_row['display_name_snapshot']}#{pig_row['short_code']}",
    )
    food_row = await database.fetch_one(
        "SELECT food_instance_id FROM food_instances WHERE owner_player_id = ? LIMIT 1",
        (identity.player_id,),
    )
    assert food_row is not None
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO player_food_effects(
                effect_entry_id, player_id, source_food_instance_id,
                effect_id, params_json, granted_uses, consumed_uses,
                expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
            """,
            (
                "multi-cook-effect",
                identity.player_id,
                str(food_row["food_instance_id"]),
                "next-five-star-cook",
                '{"uses":5}',
                5,
                "2026-07-28T00:00:00.000Z",
                "2026-07-28T00:00:00.000Z",
            ),
        )

    # 持有 5 次必出五星菜效果：批量做菜被拒绝
    with pytest.raises(BatchCookRestrictedError) as excinfo:
        await economy.batch_cook(identity, rarity=None)
    assert "只能逐个使用 /做菜" in str(excinfo.value)

    # 效果剩余 1 次：仍然全程禁止批量做菜
    async with database.transaction() as session:
        await session.execute(
            """
            UPDATE player_food_effects
            SET consumed_uses = 4, updated_at = ?
            WHERE effect_entry_id = 'multi-cook-effect'
            """,
            ("2026-07-28T00:00:00.000Z",),
        )
    with pytest.raises(BatchCookRestrictedError):
        await economy.batch_cook(identity, rarity=None)

    # 效果用尽（剩余 0）后：放行批量做菜
    async with database.transaction() as session:
        await session.execute(
            """
            UPDATE player_food_effects
            SET consumed_uses = 5, updated_at = ?
            WHERE effect_entry_id = 'multi-cook-effect'
            """,
            ("2026-07-28T00:00:00.000Z",),
        )
    result = await economy.batch_cook(identity, rarity=None)
    assert result.pig_count == 2
    await database.close()


async def _insert_pig(
    database,
    *,
    player_id: str,
    scope_id: str,
    template_id: str,
    rarity: int,
    display_name: str,
    official_value: int,
    short_code: str,
    instance_id: str,
    now: str = "2026-07-28T04:00:00.000Z",
) -> None:
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO pig_instances(
                pig_instance_id, short_code, scope_id, owner_player_id, template_id,
                template_version, rarity, display_name_snapshot,
                size_value, size_percentile, weight_value, weight_percentile,
                fat_ratio, official_value, ruleset_version, random_snapshot_json,
                state, acquired_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, 50.0, 0.5, 60.0, 0.5, 50.0, ?,
                    1, '{"test":true}', 'active', ?, ?)
            """,
            (
                instance_id, short_code, scope_id, player_id, template_id,
                rarity, display_name, official_value, now, now,
            ),
        )


async def _insert_food(
    database: PigCatcherDatabase,
    *,
    player_id: str,
    scope_id: str,
    template_id: str,
    display_name: str,
    official_value: int,
    short_code: str,
    instance_id: str,
    now: str = "2026-07-28T04:00:00.000Z",
) -> None:
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO food_instances(
                food_instance_id, short_code, scope_id, owner_player_id,
                template_id, template_version, source_pig_instance_id,
                rarity, display_name_snapshot, portion_weight, fat_category,
                official_value, effect_id, effect_params_json,
                ruleset_version, random_snapshot_json, state,
                acquired_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, NULL, 1, ?, 1.0, 'balanced', ?, '', '{}',
                    1, '{"test":true}', 'active', ?, ?)
            """,
            (
                instance_id,
                short_code,
                scope_id,
                player_id,
                template_id,
                display_name,
                official_value,
                now,
                now,
            ),
        )


@pytest.mark.asyncio
async def test_batch_keep_default_and_switch_keep_highest_value_assets(
    tmp_path: Path,
) -> None:
    """联动猪默认全保留；开启后普通猪按模板各保留最高价值实例。"""

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1,),
        extra_entries=(
            _pig_entry(1, template_suffix="other"),
            _collab_pig_entry(1, suffix="a"),
            _collab_pig_entry(1, suffix="b"),
        ),
    )
    clock = FixedClock()
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("pig-seed", "ledger-seed")).__next__,
        short_code_factory=iter(("A11E0001",)).__next__,
    )
    identity = _identity(message_id="batch-keep")
    await service.catch(identity)  # 建立玩家与群作用域
    pid = identity.player_id
    scope = identity.scope.value
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-1-common", rarity=1,
                      display_name="1星测试猪", official_value=100,
                      short_code="A11E0010", instance_id="pig-keep-1")
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-1-common", rarity=1,
                      display_name="1星测试猪", official_value=300,
                      short_code="A11E0011", instance_id="pig-keep-2")
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-collab-a", rarity=1,
                      display_name="联动猪a", official_value=150,
                      short_code="A11E0012", instance_id="pig-keep-3")
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-collab-b", rarity=1,
                      display_name="联动猪b", official_value=250,
                      short_code="A11E0013", instance_id="pig-keep-4")

    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(("ledger-1", "ledger-2", "ledger-3", "ledger-4")).__next__,
    )
    # 默认：所有联动猪受保护，普通猪（含 seed）全部批量售卖。
    sold = await economy.batch_sell_low_rarity(
        _identity(message_id="batch-keep-sell-1", user_id="200"),
        asset_kind="pig",
        max_rarity=3,
    )
    assert sold.asset_count == 3
    rows = await database.fetch_all(
        """
        SELECT template_id, official_value FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY template_id
        """,
        (pid,),
    )
    assert [(r["template_id"], r["official_value"]) for r in rows] == [
        ("pig-collab-a", 150),
        ("pig-collab-b", 250),
    ]

    # 开启后：每个普通模板各保留一只最高价值实例，联动猪仍全部保护。
    enabled, _ = await economy.set_batch_keep_highest(
        _identity(message_id="batch-keep-enable", user_id="200"),
        enabled=True,
    )
    assert enabled is True
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-1-common", rarity=1,
                      display_name="1星测试猪", official_value=100,
                      short_code="A11E0020", instance_id="pig-keep-5")
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-1-common", rarity=1,
                      display_name="1星测试猪", official_value=300,
                      short_code="A11E0021", instance_id="pig-keep-6")
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-1-common-other", rarity=1,
                      display_name="另一种1星测试猪", official_value=120,
                      short_code="A11E0022", instance_id="pig-keep-7")
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-1-common-other", rarity=1,
                      display_name="另一种1星测试猪", official_value=220,
                      short_code="A11E0023", instance_id="pig-keep-8")
    await _insert_pig(database, player_id=pid, scope_id=scope,
                      template_id="pig-collab-a", rarity=1,
                      display_name="联动猪a", official_value=150,
                      short_code="A11E0024", instance_id="pig-keep-9")
    sold = await economy.batch_sell_low_rarity(
        _identity(message_id="batch-keep-sell-2", user_id="200"),
        asset_kind="pig",
        max_rarity=3,
    )
    # 两个普通模板各卖出低价值实例；三个联动实例全部保留。
    assert sold.asset_count == 2
    rows = await database.fetch_all(
        """
        SELECT template_id, official_value FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY template_id, official_value
        """,
        (pid,),
    )
    assert [(r["template_id"], r["official_value"]) for r in rows] == [
        ("pig-1-common", 300),
        ("pig-1-common-other", 220),
        ("pig-collab-a", 150),
        ("pig-collab-a", 150),
        ("pig-collab-b", 250),
    ]
    await database.close()


@pytest.mark.asyncio
async def test_batch_sell_keeps_highest_value_food_when_enabled(
    tmp_path: Path,
) -> None:
    """开启批量保留后，批量售卖美食按模板各保留最高价值实例。"""

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1,),
        extra_entries=(_food_entry(1, template_suffix="other"),),
    )
    clock = FixedClock()
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("pig-seed", "ledger-seed")).__next__,
        short_code_factory=iter(("A11E0101",)).__next__,
    )
    identity = _identity(message_id="batch-keep-food")
    await service.catch(identity)
    pid = identity.player_id
    scope = identity.scope.value
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(("ledger-f1", "ledger-f2", "ledger-f3")).__next__,
    )
    await economy.set_batch_keep_highest(
        _identity(message_id="batch-keep-food-on", user_id="200"),
        enabled=True,
    )
    for fid, code, template_id, value in (
        ("food-keep-1", "AF000010", "food-1-common", 50),
        ("food-keep-2", "AF000011", "food-1-common", 80),
        ("food-keep-3", "AF000012", "food-1-common-other", 40),
        ("food-keep-4", "AF000013", "food-1-common-other", 70),
    ):
        await _insert_food(
            database,
            player_id=pid,
            scope_id=scope,
            template_id=template_id,
            display_name="1星测试菜",
            official_value=value,
            short_code=code,
            instance_id=fid,
        )
    sold = await economy.batch_sell_low_rarity(
        _identity(message_id="batch-keep-food-sell", user_id="200"),
        asset_kind="food",
        max_rarity=3,
    )
    assert sold.asset_count == 2
    rows = await database.fetch_all(
        """
        SELECT template_id, official_value
        FROM food_instances
        WHERE state = 'active'
        ORDER BY template_id
        """
    )
    assert [(r["template_id"], r["official_value"]) for r in rows] == [
        ("food-1-common", 80),
        ("food-1-common-other", 70),
    ]
    await database.close()


@pytest.mark.asyncio
async def test_batch_cook_defaults_to_low_rarity_and_keeps_highest(
    tmp_path: Path,
) -> None:
    """批量做菜保护全部联动猪，开启后按普通模板各保留最高价值实例。"""

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1,),
        extra_entries=(
            _pig_entry(1, template_suffix="other"),
            _collab_pig_entry(1, suffix="a"),
            _collab_pig_entry(1, suffix="b"),
        ),
    )
    clock = FixedClock()
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("pig-seed", "ledger-seed")).__next__,
        short_code_factory=iter(("A11E0201",)).__next__,
    )
    identity = _identity(message_id="batch-cook-low")
    await service.catch(identity)
    pid = identity.player_id
    scope = identity.scope.value
    for fid, code, template_id, name, value in (
        ("pig-collab-1", "A11E0210", "pig-collab-a", "联动猪a", 150),
        ("pig-collab-2", "A11E0211", "pig-collab-b", "联动猪b", 250),
    ):
        await _insert_pig(
            database, player_id=pid, scope_id=scope,
            template_id=template_id, rarity=1,
            display_name=name, official_value=value,
            short_code=code, instance_id=fid,
        )
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(
            (
                "food-c1", "ledger-c1",
                "food-c2", "ledger-c2",
                "food-c3", "ledger-c3",
            )
        ).__next__,
        short_code_factory=iter(
            ("ABAD0001", "ABAD0002", "ABAD0003")
        ).__next__,
        random_source=SequenceRandom(
            0.0, 0.0, 0.5,
            0.0, 0.0, 0.5,
            0.0, 0.0, 0.5,
        ),
    )
    # 开关默认关闭时，只处理普通 seed；两只联动猪都必须保留。
    result = await economy.batch_cook(
        _identity(message_id="batch-cook-low-1", user_id="200"),
        rarity=None,
    )
    assert result.pig_count == 1
    rows = await database.fetch_all(
        """
        SELECT template_id, official_value FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY template_id
        """,
        (pid,),
    )
    assert [(r["template_id"], r["official_value"]) for r in rows] == [
        ("pig-collab-a", 150),
        ("pig-collab-b", 250),
    ]

    await economy.set_batch_keep_highest(
        _identity(message_id="batch-cook-keep-on", user_id="200"),
        enabled=True,
    )
    for fid, code, template_id, value in (
        ("pig-common-low", "A11E0220", "pig-1-common", 100),
        ("pig-common-high", "A11E0221", "pig-1-common", 300),
        ("pig-other-low", "A11E0222", "pig-1-common-other", 120),
        ("pig-other-high", "A11E0223", "pig-1-common-other", 220),
    ):
        await _insert_pig(
            database,
            player_id=pid,
            scope_id=scope,
            template_id=template_id,
            rarity=1,
            display_name="1星测试猪",
            official_value=value,
            short_code=code,
            instance_id=fid,
        )
    result = await economy.batch_cook(
        _identity(message_id="batch-cook-low-2", user_id="200"),
        rarity=None,
    )
    assert result.pig_count == 2
    rows = await database.fetch_all(
        """
        SELECT template_id, official_value FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY template_id, official_value
        """,
        (pid,),
    )
    assert [(r["template_id"], r["official_value"]) for r in rows] == [
        ("pig-1-common", 300),
        ("pig-1-common-other", 220),
        ("pig-collab-a", 150),
        ("pig-collab-b", 250),
    ]
    await database.close()
