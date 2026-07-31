"""Fourth-round cooking and economy integration tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
        CookingSection(),
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
    await _grant_coins(database, seed_identity, 1000)
    service = EconomyService(
        database,
        CookingSection(),
        EconomySection(),
        clock=clock,
        id_factory=iter(
            ("purchase-ledger-1", "purchase-ledger-2", "purchase-ledger-3")
        ).__next__,
    )
    store = await service.store(seed_identity, page=1, category="全部")
    assert store.coin_balance == 1000
    assert len(store.products) == 10
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
        ("40.00%", "38.65%"),
        ("30.00%", "28.99%"),
        ("17.00%", "18.40%"),
        ("8.00%", "8.66%"),
        ("4.00%", "4.33%"),
        ("1.00%", "0.99%"),
    )
    assert tuple(row.after for row in store_card.chef_spice_rows) == (
        "1★ 69% · 2★ 28% · 3★ 3%",
        "1★ 9% · 2★ 71% · 3★ 18% · 4★ 2%",
        "2★ 14% · 3★ 66% · 4★ 18% · 5★ 2%",
        "3★ 30% · 4★ 60% · 5★ 10%",
        "4★ 30% · 5★ 70%",
    )
    store_text = format_store_summary(store)
    assert "猪饲料 Lv.0-5 的 4-6 星合计概率" in store_text
    assert "厨具 Lv.0-5 的高档菜相对权重增幅" in store_text
    assert "幸运猪哨（基础权重，使用前→使用后）" in store_text
    assert "主厨香料（基础分布、Lv.0，使用前→使用后）" in store_text
    assert "1★猪 1★ 75%、2★ 22%、3★ 3%→1★ 69%、2★ 28%、3★ 3%" in store_text

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
        CookingSection(),
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

    service = EconomyService(database, CookingSection(), EconomySection())
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
        CookingSection(),
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
        CookingSection(),
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
