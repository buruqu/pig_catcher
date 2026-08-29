"""Fourth-round cooking and economy integration tests."""

from __future__ import annotations

import json
from dataclasses import replace
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
    AssetStateConflictError,
    CookCooldownError,
    CookingTemplateError,
    DailyCatchLimitError,
    DomainValidationError,
    FoodEffectError,
    InsufficientBalanceError,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.special_content import (
    GOJO_BLUE_FOOD_TEMPLATE_ID,
    GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS,
    GOJO_PIG_TEMPLATE_ID,
    GOJO_RED_FOOD_TEMPLATE_ID,
    KFC_FOOD_TEMPLATE_ID,
    KFC_PIG_TEMPLATE_ID,
    SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS,
    SUKUNA_FOOD_TEMPLATE_ID,
    SUKUNA_PIG_TEMPLATE_ID,
    TECHNIQUE_DOMAIN_GOJO_BYPASS,
    TECHNIQUE_LAPSE_BLUE,
)
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.migrations.v0029_asamu_auto_gift_rebalance import (
    MIGRATION_0029,
)
from pig_catcher.infrastructure.repositories import (
    EconomyRepository,
    FrameworkRepository,
    TechniqueRepository,
)
from pig_catcher.rendering import food_card_view, group_event_eat_view, store_view
from pig_catcher.services import (
    AssetCatalogService,
    CatchQuotaResetService,
    EatConfirmationRequest,
    EconomyService,
    FrameworkService,
    GameplayService,
    SocialService,
    format_cooking_summary,
    format_group_event_eat_summary,
    format_store_summary,
    is_group_event_food,
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
    ) == (0.0, 0.0, 0.0, 0.0, 80.0, 20.0)


def test_new_cooking_items_have_distinct_non_stackable_probability_profiles() -> None:
    baseline = adjusted_cooking_weights(
        4,
        size_percentile=0.0,
        weight_percentile=0.0,
        cookware_level=0,
        chef_spice=False,
    )
    protected = adjusted_cooking_weights(
        4,
        size_percentile=0.0,
        weight_percentile=0.0,
        cookware_level=0,
        chef_spice=False,
        item_id="no-downgrade-lid",
    )
    ascended = adjusted_cooking_weights(
        4,
        size_percentile=0.0,
        weight_percentile=0.0,
        cookware_level=0,
        chef_spice=False,
        item_id="ascension-stove-core",
    )
    assert protected[:3] == (0.0, 0.0, 0.0)
    assert protected[3] > baseline[3]
    assert ascended[4] > baseline[4]
    with pytest.raises(DomainValidationError, match="一个"):
        adjusted_cooking_weights(
            4,
            size_percentile=0.0,
            weight_percentile=0.0,
            cookware_level=0,
            chef_spice=True,
            item_id="no-downgrade-lid",
        )


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
        cookware_level=10,
        player_level=21,
        chef_spice=False,
    )
    assert tuple(
        round((cookware_higher_rarity_multiplier(level) - 1.0) * 100)
        for level in range(11)
    ) == (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20)
    assert level_cooking_higher_rarity_multiplier(1) == 1.0
    assert level_cooking_higher_rarity_multiplier(21) == pytest.approx(1.10)
    assert level_cooking_higher_rarity_multiplier(999) == pytest.approx(1.10)
    assert sum(boosted[3:5]) > sum(baseline[3:5])
    assert adjusted_cooking_weights(
        6,
        size_percentile=1.0,
        weight_percentile=1.0,
        cookware_level=10,
        player_level=999,
        chef_spice=True,
    ) == (0.0, 0.0, 0.0, 0.0, 90.0, 10.0)


@pytest.mark.parametrize(
    ("item_id", "chef_spice"),
    (
        ("", False),
        ("chef-spice", False),
        ("no-downgrade-lid", False),
        ("ascension-stove-core", False),
        ("", True),
    ),
)
def test_cookware_and_level_never_reduce_reachable_higher_results(
    item_id: str,
    chef_spice: bool,
) -> None:
    for source_rarity in range(1, 6):
        baseline = adjusted_cooking_weights(
            source_rarity,
            size_percentile=0.5,
            weight_percentile=0.5,
            cookware_level=0,
            player_level=1,
            chef_spice=chef_spice,
            item_id=item_id,
        )
        boosted = adjusted_cooking_weights(
            source_rarity,
            size_percentile=0.5,
            weight_percentile=0.5,
            cookware_level=5,
            player_level=21,
            chef_spice=chef_spice,
            item_id=item_id,
        )
        assert sum(boosted) == pytest.approx(100.0)
        assert all(
            boosted[index] >= baseline[index] - 1e-10
            for index in range(source_rarity, 5)
        )


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
    assert "1★" in card.probability_line
    assert "厨具 Lv.0" in card.probability_sources

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
    assert result.veteran_coin_reward == 1_000
    assert result.veteran_reward_levels == (21,)
    assert result.coin_reward == COOK_COIN_REWARDS[Rarity.TWO]
    assert result.experience_reward == COOK_EXPERIENCE_REWARDS[Rarity.TWO]
    assert "资深里程碑：Lv.21" in result.receipt.text_summary
    card = food_card_view(result.foods[0], mode_label="做菜成功", cooking=result)
    assert card.veteran_coin_reward == 1_000
    assert card.veteran_reward_levels == (21,)
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
    summary = format_cooking_summary(result)
    assert summary.count("本次最终概率：") == 1
    assert "最终品质概率" not in summary
    assert "概率来源：等级 Lv.1、厨具 Lv.0、道具·大份餐盒" in summary
    item = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'large-lunch-box'",
        (identity.player_id,),
    )
    assert item is not None and item["quantity"] == 0
    await database.close()


@pytest.mark.asyncio
async def test_super_chef_spice_turns_six_star_cook_to_20_percent_and_consumes_once(
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
    assert result.weights == (0.0, 0.0, 0.0, 0.0, 80.0, 20.0)
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
        id_factory=iter(
            ("food-blank", "ledger-blank", "veteran-ledger-blank")
        ).__next__,
        short_code_factory=lambda: "D19F2C3D",
    )
    cooked = await service.cook(
        _identity(message_id="cook-blank"),
        caught.pig.selector,
    )
    async with database.transaction() as session:
        await session.execute(
            "UPDATE players SET experience = 20000 WHERE player_id = ?",
            (_identity(message_id="eat-blank").player_id,),
        )
    eat_identity = _identity(message_id="eat-blank")
    first = await service.eat(eat_identity, cooked.foods[0].selector)
    duplicate = await service.eat(eat_identity, cooked.foods[0].selector)
    assert first.base_experience == 8
    assert first.veteran_coin_reward == 1_000
    assert first.veteran_reward_levels == (21,)
    assert "资深里程碑：Lv.21" in first.receipt.text_summary
    assert duplicate.receipt_created is False
    state = await database.fetch_one(
        "SELECT state FROM food_instances WHERE food_instance_id = 'food-blank'"
    )
    assert state is not None and state["state"] == "consumed"
    await database.close()


@pytest.mark.asyncio
async def test_quick_eat_uses_cheapest_same_name_and_confirms_last_copy(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path, food_rarities=(1,))
    identity = _identity(message_id="seed-quick-eat")
    await FrameworkService(database).touch_identity(identity)
    await _insert_food(
        database,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id="food-1-common",
        display_name="同名测试菜",
        official_value=50,
        short_code="QUICK050",
        instance_id="quick-food-high",
    )
    await _insert_food(
        database,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id="food-1-common",
        display_name="同名测试菜",
        official_value=20,
        short_code="QUICK020",
        instance_id="quick-food-low",
    )
    clock = FixedClock()
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
    )

    eaten = await economy.eat_or_confirm(
        _identity(message_id="quick-eat-low"),
        "同名测试菜",
    )
    assert not isinstance(eaten, EatConfirmationRequest)
    assert eaten.food.food_instance_id == "quick-food-low"
    low = await database.fetch_one(
        "SELECT state FROM food_instances WHERE food_instance_id = ?",
        ("quick-food-low",),
    )
    assert low is not None and low["state"] == "consumed"

    pending = await economy.eat_or_confirm(
        _identity(message_id="quick-eat-last-prompt"),
        "同名测试菜",
    )
    assert isinstance(pending, EatConfirmationRequest)
    cancelled = await economy.confirm_eat(
        _identity(message_id="quick-eat-no"),
        accepted=False,
    )
    assert isinstance(cancelled, str) and "已取消" in cancelled

    pending_again = await economy.eat_or_confirm(
        _identity(message_id="quick-eat-last-prompt-2"),
        "同名测试菜",
    )
    assert isinstance(pending_again, EatConfirmationRequest)
    confirmed = await economy.confirm_eat(
        _identity(message_id="quick-eat-yes"),
        accepted=True,
    )
    assert not isinstance(confirmed, str)
    assert confirmed.food.food_instance_id == "quick-food-high"

    await _insert_food(
        database,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id="food-1-common",
        display_name="超时测试菜",
        official_value=30,
        short_code="TIMEOUT1",
        instance_id="quick-food-timeout",
    )
    timeout_pending = await economy.eat_or_confirm(
        _identity(message_id="quick-eat-timeout-prompt"),
        "超时测试菜",
    )
    assert isinstance(timeout_pending, EatConfirmationRequest)
    clock.value += timedelta(seconds=31)
    expired = await economy.confirm_eat(
        _identity(message_id="quick-eat-timeout-yes"),
        accepted=True,
    )
    assert isinstance(expired, str) and "超过 30 秒" in expired
    timeout_food = await database.fetch_one(
        "SELECT state FROM food_instances WHERE food_instance_id = ?",
        ("quick-food-timeout",),
    )
    assert timeout_food is not None and timeout_food["state"] == "active"
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
    eaten = await economy.eat(
        _identity(message_id="quota-eat"),
        cooked.foods[0].selector,
    )
    assert "效果可用次数：2 次" in eaten.receipt.text_summary

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
async def test_rolling_seven_day_food_adds_five_to_every_window_without_stacking(
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
    anniversary = await catching.profile(_identity(message_id="weekly-anniversary-profile"))
    assert anniversary.daily_limit == 6
    clock.value += timedelta(hours=7)
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
    await _grant_coins(database, seed_identity, 3000)
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
    assert store.coin_balance == 3000
    assert len(store.products) == 18
    products = {product.display_name: product for product in store.products}
    assert products["超级幸运猪哨"].unit_price == 1680
    assert products["超级主厨香料"].unit_price == 3600
    store_card = store_view(store)
    assert tuple(row.value for row in store_card.feed_probability_rows) == (
        "13.00%",
        "13.16%",
        "13.33%",
        "13.49%",
        "13.66%",
        "13.82%",
        "13.98%",
        "14.14%",
        "14.30%",
        "14.46%",
        "14.62%",
    )
    assert tuple(row.value for row in store_card.cookware_probability_rows) == (
        "+0%",
        "+2%",
        "+4%",
        "+6%",
        "+8%",
        "+10%",
        "+12%",
        "+14%",
        "+16%",
        "+18%",
        "+20%",
    )
    assert store_card.feed_probability_rows[0].current is True
    assert store_card.cookware_probability_rows[0].current is True
    assert tuple(
        (row.before, row.after)
        for row in store_card.lucky_whistle_rows
    ) == (
        ("40.00%", "35.00%"),
        ("30.00%", "27.00%"),
        ("17.00%", "16.00%"),
        ("8.00%", "12.00%"),
        ("4.00%", "7.00%"),
        ("1.00%", "3.00%"),
    )
    assert tuple(row.after for row in store_card.chef_spice_rows) == (
        "1★ 57% · 2★ 40% · 3★ 3%",
        "2★ 77% · 3★ 21% · 4★ 2%",
        "2★ 2% · 3★ 78% · 4★ 18% · 5★ 2%",
        "3★ 17% · 4★ 73% · 5★ 10%",
        "4★ 17% · 5★ 83%",
    )
    assert store_card.super_chef_spice_rows[0].after == "5★ 80% · 6★ 20%"
    store_text = format_store_summary(store)
    assert "猪饲料 Lv.0-10 的 4-6 星合计概率" in store_text
    assert "厨具 Lv.0-10 的高档菜相对权重增幅" in store_text
    assert "幸运猪哨（基础权重，使用前→使用后）" in store_text
    assert "超级幸运猪哨（基础权重，使用前→使用后）" in store_text
    assert "星辉探猪镜（基础权重，使用前→使用后）" in store_text
    assert "主厨香料（基础分布、Lv.0，使用前→使用后）" in store_text
    assert "1★猪 1★ 75%、2★ 22%、3★ 3%→1★ 57%、2★ 40%、3★ 3%" in store_text

    item_identity = _identity(message_id="buy-item")
    item = await service.purchase(item_identity, "幸运猪哨", quantity=3)
    assert item.balance_after == 480
    assert item.inventory_quantity == 3
    assert (await service.purchase(item_identity, "幸运猪哨", quantity=3)).receipt_created is False

    upgrade = await service.upgrade(
        _identity(message_id="buy-upgrade"),
        "厨具",
    )
    assert upgrade.upgrade_level == 1
    assert upgrade.balance_after == 180
    with pytest.raises(InsufficientBalanceError):
        await service.upgrade(
            _identity(message_id="buy-too-expensive"),
            "猪饲料",
        )
    inventory = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = 'lucky-whistle'",
        (seed_identity.player_id,),
    )
    assert inventory is not None and inventory["quantity"] == 3
    ledger = await service.ledger(seed_identity, page=1)
    assert ledger.coin_balance == ledger.ledger_total == 180
    assert ledger.total_count == 3
    await database.close()


@pytest.mark.asyncio
async def test_feature_stores_are_separate_and_purchase_into_existing_tool_inventories(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(tmp_path)
    clock = FixedClock()
    identity = _identity(message_id="feature-store-seed")
    await _grant_coins(database, identity, 2000)
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(("feature-coin-1", "feature-coin-2", "feature-coin-3")).__next__,
    )

    main = await service.store(identity, page=1, category="全部")
    dispatch = await service.store(identity, page=1, category="派遣")
    tour = await service.store(identity, page=1, category="巡演")
    battle = await service.store(identity, page=1, category="对战")
    assert main.shop_section == "主商城" and len(main.products) == 18
    assert {product.display_name for product in main.products}.isdisjoint(
        {product.display_name for product in (*dispatch.products, *tour.products, *battle.products)}
    )
    assert (dispatch.shop_section, len(dispatch.products)) == ("派遣", 4)
    assert (tour.shop_section, len(tour.products)) == ("巡演", 4)
    assert (battle.shop_section, len(battle.products)) == ("对战", 3)
    dispatch_view = store_view(dispatch)
    assert dispatch_view.shop_section == "派遣"
    assert dispatch_view.feed_probability_rows == ()
    assert "猪饲料 Lv." not in format_store_summary(dispatch)

    buy_map_identity = _identity(message_id="feature-buy-map")
    bought_map = await service.purchase(buy_map_identity, "区域地图", quantity=2)
    duplicate = await service.purchase(buy_map_identity, "区域地图", quantity=2)
    bought_wristband = await service.purchase(
        _identity(message_id="feature-buy-wristband"),
        "练习护腕",
        quantity=1,
    )
    assert bought_map.product_type == "feature-tool"
    assert bought_map.inventory_quantity == 2 and bought_map.balance_after == 960
    assert duplicate.receipt_created is False and duplicate.balance_after == 960
    assert bought_wristband.inventory_quantity == 1 and bought_wristband.balance_after == 80
    with pytest.raises(InsufficientBalanceError):
        await service.purchase(
            _identity(message_id="feature-buy-insufficient"),
            "奇遇罗盘",
            quantity=1,
        )

    dispatch_row = await database.fetch_one(
        "SELECT quantity FROM dispatch_tools WHERE player_id = ? AND tool_id = 'region-map'",
        (identity.player_id,),
    )
    battle_row = await database.fetch_one(
        "SELECT quantity FROM battle_tools WHERE player_id = ? AND tool_id = 'wristband'",
        (identity.player_id,),
    )
    ledger_rows = await database.fetch_all(
        """
        SELECT system, product_id, tool_id, quantity, total_price, balance_after
        FROM feature_tool_store_ledger
        WHERE player_id = ?
        ORDER BY occurred_at, entry_key
        """,
        (identity.player_id,),
    )
    assert dispatch_row is not None and dispatch_row["quantity"] == 2
    assert battle_row is not None and battle_row["quantity"] == 1
    assert [tuple(row) for row in ledger_rows] == [
        ("dispatch", "feature-dispatch-region-map", "region-map", 2, 1040, 2),
        ("battle", "feature-battle-wristband", "wristband", 1, 880, 1),
    ]
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
    # 不指定品质（1-3 星）：1 星普通被卖，每种联动猪仍保留最高价值实例。
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
async def test_batch_cook_keeps_best_collaboration_duplicate_and_sorts_by_rarity_desc(
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
            # 同模板的另一只 1 星联动猪
            0.0, 0.9, 0.5, 0.5, 0.5, 0.5, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "pig-k1", "ledger-k1",
                "pig-k2", "ledger-k2",
                "pig-k3", "ledger-k3",
                "pig-k4", "ledger-k4",
            )
        ).__next__,
        short_code_factory=iter(
            ("A1000001", "A1000002", "A1000003", "A1000004")
        ).__next__,
    )
    for mid in ("cook-1", "cook-2", "cook-3", "cook-4"):
        await service.catch(_identity(message_id=mid, user_id="200"))
    async with database.transaction() as session:
        await session.execute(
            "UPDATE pig_instances SET official_value = 999 WHERE pig_instance_id = 'pig-k4'"
        )

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
                "ck-food-1", "ck-ledger-1",
                "ck-food-2", "ck-ledger-2",
                "ck-food-3", "ck-ledger-3",
            )
        ).__next__,
        short_code_factory=iter(("ABAD0001", "ABAD0002", "ABAD0003")).__next__,
    )
    result = await economy.batch_cook(
        _identity(message_id="batch-cook-ordinary"),
        rarity=None,
    )
    assert result.pig_count == 3  # 两只普通猪 + 一只低价值联动重复猪
    assert result.food_count == 3
    # 同一种联动猪只保留最高价值的一只
    collab_left = await database.fetch_one(
        "SELECT COUNT(*) AS count, MAX(official_value) AS value "
        "FROM pig_instances WHERE state = 'active'"
    )
    assert collab_left is not None and collab_left["count"] == 1
    assert collab_left["value"] == 999
    view = batch_cook_view(result)
    assert view.food_count == 3
    rarities = [item.rarity for item in view.items]
    assert rarities == sorted(rarities, reverse=True)
    await database.close()


@pytest.mark.asyncio
async def test_batch_cook_only_blocks_six_star_origin_cook_effect(
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
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "pig-m1", "ledger-m1",
                "pig-m2", "ledger-m2",
                "pig-m3", "ledger-m3",
                "pig-m4", "ledger-m4",
            )
        ).__next__,
        short_code_factory=iter(("A1000001", "A1000002", "A1000003", "A1000004")).__next__,
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
            0.0, 0.0, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "ec-food-1", "ec-ledger-1",
                "ec-food-2", "ec-ledger-2",
                "ec-food-3", "ec-ledger-3",
                "ec-food-4", "ec-ledger-4",
            )
        ).__next__,
        short_code_factory=iter(("ABAD0001", "ABAD0002", "ABAD0003", "ABAD0004")).__next__,
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

    # 普通菜效果不再拦截批量做菜；本批次按顺序逐只消耗。
    result = await economy.batch_cook(identity, rarity=None)
    assert result.pig_count == 2
    ordinary_effect = await database.fetch_one(
        "SELECT consumed_uses FROM player_food_effects WHERE effect_entry_id = ?",
        ("multi-cook-effect",),
    )
    assert ordinary_effect is not None and ordinary_effect["consumed_uses"] == 2

    # 新增原料并把效果来源品质改成六星；剩余一次也必须逐个做菜。
    await service.catch(_identity(message_id="cook-d", user_id="200"))
    async with database.transaction() as session:
        await session.execute(
            """
            UPDATE food_instances SET rarity = 6
            WHERE food_instance_id = ?
            """,
            (str(food_row["food_instance_id"]),),
        )
        await session.execute(
            """
            UPDATE player_food_effects SET consumed_uses = 4, updated_at = ?
            WHERE effect_entry_id = 'multi-cook-effect'
            """,
            ("2026-07-28T00:00:00.000Z",),
        )
    with pytest.raises(BatchCookRestrictedError) as excinfo:
        await economy.batch_cook(
            _identity(message_id="batch-cook-six-star-blocked"),
            rarity=None,
        )
    assert "六星菜做菜效果" in str(excinfo.value)
    assert "只能逐个使用 /做菜" in str(excinfo.value)

    # 六星菜效果用尽后放行。
    async with database.transaction() as session:
        await session.execute(
            """
            UPDATE player_food_effects
            SET consumed_uses = 5, updated_at = ?
            WHERE effect_entry_id = 'multi-cook-effect'
            """,
            ("2026-07-28T00:00:00.000Z",),
        )
    result = await economy.batch_cook(
        _identity(message_id="batch-cook-six-star-finished"),
        rarity=None,
    )
    assert result.pig_count == 1
    await database.close()


@pytest.mark.asyncio
async def test_batch_cook_consumes_queued_same_item_in_source_order(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1, 2),
    )
    clock = FixedClock()
    catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "batch-item-pig-1",
                "batch-item-ledger-1",
                "batch-item-pig-2",
                "batch-item-ledger-2",
            )
        ).__next__,
        short_code_factory=iter(("BITEM001", "BITEM002")).__next__,
    )
    identity = _identity(message_id="batch-item-seed")
    await catching.catch(identity)
    await catching.catch(_identity(message_id="batch-item-seed-2"))
    same_acquired_at = iso_timestamp(clock.now())
    async with database.transaction() as session:
        await session.execute(
            "UPDATE pig_instances SET acquired_at = ? WHERE owner_player_id = ?",
            (same_acquired_at, identity.player_id),
        )
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'chef-spice', 3, ?)
            """,
            (identity.player_id, iso_timestamp(clock.now())),
        )
    await catching.arm_item(
        _identity(message_id="batch-item-arm"),
        "主厨香料",
        quantity=2,
    )
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(
            0.0, 0.0, 0.5,
            0.0, 0.0, 0.5,
        ),
        clock=clock,
        id_factory=iter(
            (
                "batch-item-food-1",
                "batch-item-cook-ledger-1",
                "batch-item-food-2",
                "batch-item-cook-ledger-2",
            )
        ).__next__,
        short_code_factory=iter(("BIFood01", "BIFood02")).__next__,
    )
    result = await economy.batch_cook(
        _identity(message_id="batch-item-cook"),
        rarity=None,
    )
    duplicate = await economy.batch_cook(
        _identity(message_id="batch-item-cook"),
        rarity=None,
    )
    assert result.pig_count == 2
    assert result.receipt_created is True
    assert duplicate.receipt_created is False
    assert duplicate.receipt is not None and result.receipt is not None
    assert duplicate.receipt.receipt_id == result.receipt.receipt_id
    assert [pig.pig_instance_id for pig in result.source_pigs] == [
        "batch-item-pig-1",
        "batch-item-pig-2",
    ]
    assert result.item_use_summaries == ("主厨香料 ×2（队列剩余 0 次）",)
    inventory = await database.fetch_one(
        "SELECT quantity FROM item_inventory WHERE player_id = ? AND item_id = ?",
        (identity.player_id, "chef-spice"),
    )
    assert inventory is not None and inventory["quantity"] == 1
    assert await database.fetch_one(
        "SELECT 1 FROM armed_items WHERE player_id = ? AND action_type = 'cooking'",
        (identity.player_id,),
    ) is None
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
    rarity: int = 1,
    effect_id: str = "",
    effect_params: dict[str, object] | None = None,
    now: str = "2026-07-28T04:00:00.000Z",
) -> None:
    params_json = json.dumps(
        effect_params or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
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
            VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?, 1.0, 'balanced', ?, ?, ?,
                    1, '{"test":true}', 'active', ?, ?)
            """,
            (
                instance_id,
                short_code,
                scope_id,
                player_id,
                template_id,
                rarity,
                display_name,
                official_value,
                effect_id,
                params_json,
                now,
                now,
            ),
        )


@pytest.mark.asyncio
async def test_batch_keep_default_and_switch_keep_highest_value_assets(
    tmp_path: Path,
) -> None:
    """联动猪默认每模板留最高一只；开关再保护普通猪最高实例。"""

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
    # 默认：每种联动猪保留一只，普通猪（含 seed）全部批量售卖。
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

    # 开启后：普通模板也各保留最高实例；联动重复实例只留最高的一只。
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
                      display_name="联动猪a", official_value=350,
                      short_code="A11E0024", instance_id="pig-keep-9")
    sold = await economy.batch_sell_low_rarity(
        _identity(message_id="batch-keep-sell-2", user_id="200"),
        asset_kind="pig",
        max_rarity=3,
    )
    # 两个普通模板低价值实例和联动 a 的低价值重复实例被处理。
    assert sold.asset_count == 3
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
        ("pig-collab-a", 350),
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
    """批量做菜按联动模板留最高实例，开启后再按普通模板留最高实例。"""

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


@pytest.mark.asyncio
async def test_cloud_sea_pot_eat_rewards_scope_and_creates_one_day_group_effect(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(6,),
        food_rarities=(6,),
        effect_ids={6: "group-next-exclusive-high-star-catch"},
        effect_params={
            6: {
                "five_star_multiplier": 8,
                "six_star_multiplier": 8,
                "uses_per_player": 1,
                "self_coin": 18888,
                "other_coin": 1680,
                "source_label": "神龙化猪七星云海锅",
            }
        },
        manifest_version=4,
    )
    clock = FixedClock()
    eater = _identity(user_id="200", message_id="cloud-source")
    other = _identity(user_id="300", message_id="cloud-other")
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        await session.execute(
            "UPDATE food_templates SET display_name = ? WHERE template_id = ?",
            ("神龙化猪七星云海锅", "food-6-group"),
        )
        await FrameworkRepository().touch_identity(
            session,
            identity=other,
            now=now,
        )
    catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("cloud-source-pig", "cloud-source-ledger")).__next__,
        short_code_factory=lambda: "CLOUDPIG",
    )
    source = await catching.catch(eater)
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.999, 0.0, 0.5),
        clock=clock,
        id_factory=iter(
            (
                "cloud-food",
                "cloud-cook-ledger",
                "cloud-group-effect",
                "cloud-eater-ledger",
                "cloud-other-ledger",
            )
        ).__next__,
        short_code_factory=lambda: "CLOUDFOD",
    )
    cooked = await economy.cook(
        _identity(user_id="200", message_id="cloud-cook"),
        source.pig.selector,
    )
    assert cooked.foods[0].rarity == 6
    eat_identity = _identity(user_id="200", message_id="cloud-eat")
    eaten = await economy.eat(eat_identity, cooked.foods[0].selector)
    duplicate = await economy.eat(eat_identity, cooked.foods[0].selector)
    assert eaten.effect.queued_effect_id == "group-next-exclusive-high-star-catch"
    assert eaten.effect.coin_bonus == 18888
    assert eaten.group_rewarded_players == 2
    assert duplicate.group_rewarded_players == 2
    assert is_group_event_food(eaten)
    assert "【全群大事件 · 神龙临世】" in eaten.receipt.text_summary
    assert "成员200" in eaten.receipt.text_summary
    assert "18,888" in eaten.receipt.text_summary
    assert "1,680" in eaten.receipt.text_summary
    assert "五星、六星相对权重 ×8" in eaten.receipt.text_summary
    assert format_group_event_eat_summary(duplicate) == eaten.receipt.text_summary
    renamed_result = replace(
        eaten,
        food=replace(eaten.food, display_name="改名后的云海盛宴"),
    )
    assert is_group_event_food(renamed_result)
    assert "【全群大事件 · 神龙临世】" in format_group_event_eat_summary(
        renamed_result
    )
    assert group_event_eat_view(
        renamed_result,
        group_name="重命名测试群",
    ).tone == "cloud"
    assert duplicate.receipt_created is False
    balances = await database.fetch_all(
        "SELECT platform_user_id, coin_balance FROM players ORDER BY platform_user_id"
    )
    balance_by_user = {
        str(row["platform_user_id"]): int(row["coin_balance"])
        for row in balances
    }
    assert balance_by_user["300"] == 1680
    assert balance_by_user["200"] == eaten.coin_balance
    effect = await database.fetch_one(
        """
        SELECT effect.effect_id, effect.granted_uses_per_player,
               effect.starts_at, effect.expires_at,
               source.platform_user_id AS source_user_id
        FROM group_food_effects AS effect
        JOIN players AS source ON source.player_id = effect.source_player_id
        WHERE effect.group_effect_entry_id = 'cloud-group-effect'
        """
    )
    assert effect is not None
    assert tuple(effect) == (
        "group-next-exclusive-high-star-catch",
        1,
        "2026-07-28T04:00:00.000Z",
        "2026-07-29T04:00:00.000Z",
        "200",
    )
    ledgers = await database.fetch_one(
        "SELECT COUNT(*) AS count FROM currency_ledger "
        "WHERE reason_code = 'group-food-effect'"
    )
    assert ledgers is not None and ledgers["count"] == 2
    await database.close()


@pytest.mark.asyncio
async def test_pig_nose_omelette_keeps_group_rewards_and_restores_two_cooks(
    tmp_path: Path,
) -> None:
    params = {
        "coin_per_player": 1004,
        "dedicated_catches": 1,
        "dedicated_only": True,
        "five_star_multiplier": 1.004,
        "personal_six_star_cook_percent": 60,
        "personal_six_star_cook_uses": 2,
        "six_star_multiplier": 1.004,
        "source_label": "猪鼻蛋包饭",
    }
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(6,),
        food_rarities=(6,),
        effect_ids={6: "group-window-high-star-boost"},
        effect_params={6: params},
        manifest_version=4,
    )
    clock = FixedClock()
    eater = _identity(user_id="200", message_id="omelette-source")
    other = _identity(user_id="300", message_id="omelette-other")
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        await FrameworkRepository().touch_identity(session, identity=other, now=now)
    catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("omelette-source-pig", "omelette-source-ledger")).__next__,
        short_code_factory=lambda: "OMELEPIG",
    )
    source = await catching.catch(eater)
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.999, 0.0, 0.5),
        clock=clock,
        id_factory=iter(
            (
                "omelette-food",
                "omelette-cook-ledger",
                "omelette-group-effect",
                "omelette-personal-effect",
                "omelette-eater-ledger",
                "omelette-other-ledger",
            )
        ).__next__,
        short_code_factory=lambda: "OMELEFOD",
    )
    cooked = await economy.cook(
        _identity(user_id="200", message_id="omelette-cook"),
        source.pig.selector,
    )
    eaten = await economy.eat(
        _identity(user_id="200", message_id="omelette-eat"),
        cooked.foods[0].selector,
    )
    assert eaten.effect.queued_effect_id == "group-window-high-star-boost"
    assert eaten.effect.coin_bonus == 1004
    assert "1 次额外抓猪机会" in eaten.effect.summary
    assert "连续 2 次" in eaten.effect.summary
    personal = await database.fetch_one(
        """
        SELECT effect_id, params_json, granted_uses, consumed_uses
        FROM player_food_effects
        WHERE effect_entry_id = 'omelette-personal-effect'
        """
    )
    assert personal is not None
    assert tuple(personal) == (
        "next-six-star-cook",
        '{"six_star_percent":60.0,"uses":2}',
        2,
        0,
    )
    group_effect = await database.fetch_one(
        """
        SELECT effect_id, params_json, granted_uses_per_player
        FROM group_food_effects
        WHERE group_effect_entry_id = 'omelette-group-effect'
        """
    )
    assert group_effect is not None
    assert group_effect["effect_id"] == "group-window-high-star-boost"
    assert group_effect["granted_uses_per_player"] == 1
    assert json.loads(str(group_effect["params_json"])) == params
    balances = await database.fetch_all(
        "SELECT platform_user_id, coin_balance FROM players ORDER BY platform_user_id"
    )
    assert {str(row["platform_user_id"]): int(row["coin_balance"]) for row in balances}[
        "300"
    ] == 1004
    receiver_catching = GameplayService(
        database,
        CatchingSection(daily_limit=1, cooldown_seconds=0),
        random_source=SequenceRandom(
            *(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5) * 2
        ),
        clock=clock,
        id_factory=iter(
            (
                "omelette-extra-pig",
                "omelette-extra-ledger",
                "omelette-normal-pig",
                "omelette-normal-ledger",
            )
        ).__next__,
        short_code_factory=iter(("OMELEXTR", "OMELNORM")).__next__,
    )
    extra = await receiver_catching.catch(
        _identity(user_id="300", message_id="omelette-extra-catch")
    )
    assert extra.quota_exempt_catch is True
    assert (extra.daily_count, extra.daily_limit) == (0, 1)
    assert any("猪鼻蛋包饭全群加成" in text for text in extra.effect_summaries)
    assert any("×1.004" in text for text in extra.effect_summaries)

    normal = await receiver_catching.catch(
        _identity(user_id="300", message_id="omelette-normal-catch")
    )
    assert normal.quota_exempt_catch is False
    assert (normal.daily_count, normal.daily_limit) == (1, 1)
    assert all("猪鼻蛋包饭" not in text for text in normal.effect_summaries)
    with pytest.raises(DailyCatchLimitError):
        await receiver_catching.catch(
            _identity(user_id="300", message_id="omelette-limit-catch")
        )
    usage = await database.fetch_one(
        """
        SELECT consumed_uses
        FROM group_food_effect_usage
        WHERE group_effect_entry_id = 'omelette-group-effect'
          AND player_id = ?
        """,
        (other.player_id,),
    )
    assert usage is not None and int(usage["consumed_uses"]) == 1
    await database.close()


@pytest.mark.asyncio
async def test_favorites_block_destructive_selection_and_named_food_batch_sale(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1,),
        food_rarities=(1,),
    )
    clock = FixedClock()
    _, seed = await _catch_one_star(database, clock=clock, message_id="favorite-seed")
    player_id = seed.pig.owner_player_id
    scope_id = seed.pig.scope_id
    await _insert_pig(
        database,
        player_id=player_id,
        scope_id=scope_id,
        template_id="pig-1-common",
        rarity=1,
        display_name="名称直选猪",
        official_value=30,
        short_code="PICKLOW1",
        instance_id="pig-pick-low",
    )
    await _insert_pig(
        database,
        player_id=player_id,
        scope_id=scope_id,
        template_id="pig-1-common",
        rarity=1,
        display_name="名称直选猪",
        official_value=300,
        short_code="PICKHIGH",
        instance_id="pig-pick-high",
    )
    for instance_id, short_code, value in (
        ("food-named-low", "FOODLOW1", 20),
        ("food-named-mid", "FOODMID1", 40),
        ("food-named-favorite", "FOODFAV1", 80),
    ):
        await _insert_food(
            database,
            player_id=player_id,
            scope_id=scope_id,
            template_id="food-1-common",
            display_name="批售测试菜",
            official_value=value,
            short_code=short_code,
            instance_id=instance_id,
        )
    await _insert_food(
        database,
        player_id=player_id,
        scope_id=scope_id,
        template_id="food-1-common",
        display_name="不应批售的菜",
        official_value=60,
        short_code="OTHER001",
        instance_id="food-other",
    )

    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        random_source=SequenceRandom(0.0, 0.0, 0.5),
    )
    favorite_pig = await economy.set_favorite(
        _identity(message_id="favorite-pig"),
        asset_kind="pig",
        selector_text="名称直选猪#PICKHIGH",
        favorite=True,
    )
    assert favorite_pig.changed_count == 1
    cooked = await economy.cook(
        _identity(message_id="favorite-cook"),
        "名称直选猪",
    )
    assert cooked.source_pig.pig_instance_id == "pig-pick-low"
    with pytest.raises(AssetStateConflictError, match="收藏保护"):
        await economy.sell_pig(
            _identity(message_id="favorite-exact-sale"),
            "名称直选猪#PICKHIGH",
        )

    favorite_food = await economy.set_favorite(
        _identity(message_id="favorite-food"),
        asset_kind="food",
        selector_text="批售测试菜#FOODFAV1",
        favorite=True,
    )
    assert favorite_food.target_count == 1
    sold = await economy.batch_sell_low_rarity(
        _identity(message_id="favorite-named-batch"),
        asset_kind="food",
        max_rarity=5,
        display_name="批售测试菜",
    )
    assert sold.asset_count == 2
    assert sold.display_name == "批售测试菜"
    remaining = await database.fetch_all(
        """
        SELECT food_instance_id, is_favorite
        FROM food_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY food_instance_id
        """,
        (player_id,),
    )
    assert ("food-named-favorite", 1) in [
        (str(row["food_instance_id"]), int(row["is_favorite"]))
        for row in remaining
    ]
    assert any(str(row["food_instance_id"]) == "food-other" for row in remaining)

    await _insert_food(
        database,
        player_id=player_id,
        scope_id=scope_id,
        template_id="food-1-common",
        display_name="收藏不算余量菜",
        official_value=20,
        short_code="EATABLE1",
        instance_id="food-confirm-eligible",
    )
    await _insert_food(
        database,
        player_id=player_id,
        scope_id=scope_id,
        template_id="food-1-common",
        display_name="收藏不算余量菜",
        official_value=80,
        short_code="EATFAV01",
        instance_id="food-confirm-favorite",
    )
    await economy.set_favorite(
        _identity(message_id="favorite-confirm-food"),
        asset_kind="food",
        selector_text="收藏不算余量菜#EATFAV01",
        favorite=True,
    )
    confirmation = await economy.eat_or_confirm(
        _identity(message_id="favorite-confirm-eat"),
        "收藏不算余量菜",
    )
    assert isinstance(confirmation, EatConfirmationRequest)
    assert confirmation.food.food_instance_id == "food-confirm-eligible"

    for instance_id, short_code, value in (
        ("food-keep-low", "KEEPLOW1", 10),
        ("food-keep-high", "KEEPHI01", 40),
        ("food-keep-favorite", "KEEPFAV1", 100),
    ):
        await _insert_food(
            database,
            player_id=player_id,
            scope_id=scope_id,
            template_id="food-1-common",
            display_name="保留排除收藏菜",
            official_value=value,
            short_code=short_code,
            instance_id=instance_id,
        )
    await economy.set_favorite(
        _identity(message_id="favorite-keep-food"),
        asset_kind="food",
        selector_text="保留排除收藏菜#KEEPFAV1",
        favorite=True,
    )
    await economy.set_batch_keep_highest(
        _identity(message_id="favorite-enable-keep"),
        enabled=True,
    )
    kept_batch = await economy.batch_sell_low_rarity(
        _identity(message_id="favorite-keep-batch"),
        asset_kind="food",
        max_rarity=5,
        display_name="保留排除收藏菜",
    )
    assert kept_batch.asset_count == 1
    protected_rows = await database.fetch_all(
        """
        SELECT food_instance_id, is_favorite
        FROM food_instances
        WHERE display_name_snapshot = ? AND state = 'active'
        ORDER BY food_instance_id
        """,
        ("保留排除收藏菜",),
    )
    assert [(row["food_instance_id"], row["is_favorite"]) for row in protected_rows] == [
        ("food-keep-favorite", 1),
        ("food-keep-high", 0),
    ]

    unprotected = await economy.set_favorite(
        _identity(message_id="unfavorite-food"),
        asset_kind="food",
        selector_text="批售测试菜",
        favorite=False,
    )
    assert unprotected.target_count == 1
    assert unprotected.changed_count == 1
    await database.close()


@pytest.mark.asyncio
async def test_pig_dumpling_stacks_five_layers_and_consumes_together(
    tmp_path: Path,
) -> None:
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(6,),
        food_rarities=(4, 5, 6),
        effect_ids={4: "next-stackable-six-star-cook-bonus"},
        effect_params={4: {"bonus_percent": 1, "max_stacks": 5}},
        manifest_version=4,
    )
    clock = FixedClock()
    catching = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("dumpling-source-pig", "dumpling-catch-ledger")).__next__,
        short_code_factory=lambda: "DUMPPIG1",
    )
    caught = await catching.catch(_identity(message_id="dumpling-catch"))
    now = iso_timestamp(clock.now())
    async with database.transaction() as session:
        for index in range(1, 7):
            await session.execute(
                """
                INSERT INTO food_instances(
                    food_instance_id, short_code, scope_id, owner_player_id,
                    template_id, template_version, rarity, display_name_snapshot,
                    portion_weight, fat_category, official_value, effect_id,
                    effect_params_json, ruleset_version, random_snapshot_json,
                    state, acquired_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 'food-4-common', 1, 4, '4星测试菜',
                    1.0, 'balanced', 320,
                    'next-stackable-six-star-cook-bonus',
                    '{"bonus_percent":1,"max_stacks":5}', 17, '{}',
                    'active', ?, ?
                )
                """,
                (
                    f"dumpling-food-{index}",
                    f"DUMP{index:04d}",
                    caught.pig.scope_id,
                    caught.pig.owner_player_id,
                    now,
                    now,
                ),
            )

    cooking = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.0, 0.0, 0.5),
        clock=clock,
    )
    for index in range(1, 6):
        eaten = await cooking.eat(
            _identity(message_id=f"dumpling-eat-{index}"),
            f"4星测试菜#DUMP{index:04d}",
        )
        assert eaten.effect.queued_effect_id == "next-stackable-six-star-cook-bonus"

    with pytest.raises(FoodEffectError, match="已经叠加 5 层"):
        await cooking.eat(
            _identity(message_id="dumpling-eat-6"),
            "4星测试菜#DUMP0006",
        )
    sixth = await database.fetch_one(
        "SELECT state FROM food_instances WHERE food_instance_id = 'dumpling-food-6'"
    )
    assert sixth is not None and sixth["state"] == "active"

    cooked = await cooking.cook(
        _identity(message_id="dumpling-six-star-cook"),
        caught.pig.selector,
    )
    assert cooked.weights == pytest.approx((0, 0, 0, 0, 85, 15))
    assert any("猪饺叠加 5 层" in summary for summary in cooked.effect_summaries)
    queue = await database.fetch_one(
        """
        SELECT COUNT(*) AS entries, SUM(consumed_uses) AS consumed
        FROM player_food_effects
        WHERE player_id = ?
          AND effect_id = 'next-stackable-six-star-cook-bonus'
        """,
        (caught.pig.owner_player_id,),
    )
    assert queue is not None
    assert (queue["entries"], queue["consumed"]) == (5, 5)
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("effect_id", "advance", "expected_after_advance"),
    [
        ("current-window-catches", timedelta(hours=7), 1),
        ("today-window-catches", timedelta(hours=7), 3),
    ],
)
async def test_balanced_quota_foods_separate_current_window_from_all_today_windows(
    tmp_path: Path,
    effect_id: str,
    advance: timedelta,
    expected_after_advance: int,
) -> None:
    case_root = tmp_path / effect_id
    case_root.mkdir()
    database = await _database_with_catalog(
        case_root,
        pig_rarities=(4,),
        food_rarities=(4,),
        effect_ids={4: effect_id},
        effect_params={4: {"count": 2}},
    )
    clock = FixedClock()
    catching = GameplayService(
        database,
        CatchingSection(daily_limit=1, cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter((f"{effect_id}-pig", f"{effect_id}-catch-ledger")).__next__,
        short_code_factory=lambda: "A19F2C3D",
    )
    source = await catching.catch(_identity(message_id=f"{effect_id}-source"))
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        random_source=SequenceRandom(0.5, 0.0, 0.5),
        clock=clock,
        id_factory=iter(
            (f"{effect_id}-food", f"{effect_id}-cook-ledger", f"{effect_id}-effect")
        ).__next__,
        short_code_factory=lambda: "A29F2C3D",
        quota_refresh_hours=(0, 9, 12, 19),
    )
    cooked = await economy.cook(
        _identity(message_id=f"{effect_id}-cook"),
        source.pig.selector,
    )
    eaten = await economy.eat(
        _identity(message_id=f"{effect_id}-eat"),
        cooked.foods[0].selector,
    )
    assert eaten.effect.queued_effect_id == effect_id
    active = await catching.profile(_identity(message_id=f"{effect_id}-active"))
    assert (active.daily_count, active.daily_limit) == (1, 3)

    clock.value += advance
    next_window = await catching.profile(_identity(message_id=f"{effect_id}-next"))
    assert (next_window.daily_count, next_window.daily_limit) == (
        0,
        expected_after_advance,
    )
    await database.close()



@pytest.mark.asyncio
async def test_daniya_progress_accumulates_boosts_and_rejects_at_cap(
    tmp_path: Path,
) -> None:
    """达妮娅泡泡云冻：永久累计最多 5 层，逐层提升抓猪/做菜六星概率，满层拒绝。"""

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 6),
        food_rarities=(1, 6),
        effect_ids={6: "permanent-six-star-progress"},
        effect_params={
            6: {
                "catch_bonus_per_stack": 0.2,
                "cook_bonus_per_stack": 2.0,
                "max_stacks": 5,
            }
        },
    )
    clock = FixedClock()
    service = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5),
        clock=clock,
        id_factory=iter(("pig-seed", "ledger-seed")).__next__,
        short_code_factory=iter(("D0000001",)).__next__,
    )
    identity = _identity(message_id="daniya-seed")
    await service.catch(identity)
    pid = identity.player_id
    scope = identity.scope.value
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(("food-1", "ledger-1", "food-2", "ledger-2")).__next__,
        short_code_factory=iter(("DF000001", "DF000002")).__next__,
    )
    # 直接 INSERT 六盒达妮娅泡泡云冻（模板 food-6-group 带达妮娅效果）
    boxes = (
        ("daniya-1", "DF000001"),
        ("daniya-2", "DF000002"),
        ("daniya-3", "DF000003"),
        ("daniya-4", "DF000004"),
        ("daniya-5", "DF000005"),
        ("daniya-6", "DF000006"),
    )
    for fid, code in boxes:
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
                VALUES (?, ?, ?, ?, 'food-6-group', 1, NULL, 6, '达妮娅泡泡云冻',
                        1.0, 'balanced', 25000, 'permanent-six-star-progress',
                        '{"catch_bonus_per_stack":0.2,"cook_bonus_per_stack":2.0,"max_stacks":5}',
                        1, '{"test":true}', 'active', ?, ?)
                """,
                (fid, code, scope, pid, "2026-07-28T04:00:00.000Z", "2026-07-28T04:00:00.000Z"),
            )
    # 依次吃 1-5 盒 → stacks 到 5
    for index, (_, code) in enumerate(boxes[:5]):
        eaten = await economy.eat(
            _identity(user_id="200", message_id=f"daniya-eat-{index + 1}"),
            f"达妮娅泡泡云冻#{code}",
        )
        assert eaten.effect.queued_effect_id == "permanent-six-star-progress"
    row = await database.fetch_one(
        "SELECT stacks FROM player_six_star_progress WHERE player_id = ?",
        (pid,),
    )
    assert row is not None and row["stacks"] == 5
    # 满层后吃第 6 盒被拒绝并保留
    with pytest.raises(FoodEffectError) as excinfo:
        await economy.eat(
            _identity(user_id="200", message_id="daniya-eat-6"),
            "达妮娅泡泡云冻#DF000006",
        )
    assert "累计上限" in str(excinfo.value)
    remaining = await database.fetch_one(
        "SELECT COUNT(*) AS c FROM food_instances WHERE food_instance_id = 'daniya-6' AND state = 'active'"
    )
    assert remaining is not None and remaining["c"] == 1
    await database.close()


@pytest.mark.asyncio
async def test_asamu_group_effect_auto_gifts_six_star_pig(
    tmp_path: Path,
) -> None:
    """阿萨姆红茶奶雾锅：固定品质分布；抓到 5/6 星时以 40% 概率自动赠送发起人。"""

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 6),
        food_rarities=(1, 6),
        effect_ids={6: "group-next-exclusive-high-star-catch"},
        effect_params={
            6: {
                "fixed_weights": [0, 0, 0, 50, 30, 20],
                "uses_per_player": 1,
                "self_coin": 0,
                "other_coin": 0,
                "auto_gift_chance_percent": 40.0,
                "auto_gift_rarities": [5, 6],
                "quota_exempt": True,
                "source_label": "阿萨姆红茶奶雾锅",
            }
        },
    )
    clock = FixedClock()
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(
            ("asamu-food", "asamu-ledger", "asamu-group-effect", "asamu-eat-ledger")
        ).__next__,
        short_code_factory=iter(("AS000001",)).__next__,
    )
    # 发起人（U100）持有阿萨姆菜并食用创建群效果
    activator_identity = _identity(user_id="100", message_id="asamu-seed")
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO players(
                player_id, scope_id, platform_user_id, display_name,
                coin_balance, experience, created_at, updated_at
            )
            VALUES (?, 'qq:100', '100', '阿萨姆发动者', 0, 0, ?, ?)
            """,
            (
                activator_identity.player_id,
                "2026-07-28T04:00:00.000Z",
                "2026-07-28T04:00:00.000Z",
            ),
        )
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
            VALUES ('asamu-1', 'AS000001', 'qq:100', ?, 'food-6-group', 1, NULL, 6,
                    '阿萨姆红茶奶雾锅', 1.0, 'balanced', 25000,
                    'group-next-exclusive-high-star-catch',
                    '{"auto_gift_chance_percent":40.0,"auto_gift_rarities":[5,6],"fixed_weights":[0,0,0,50,30,20],"other_coin":0,"quota_exempt":true,"self_coin":0,"source_label":"阿萨姆红茶奶雾锅","uses_per_player":1}',
                    1, '{"test":true}', 'active', ?, ?)
            """,
            (
                activator_identity.player_id,
                "2026-07-28T04:00:00.000Z",
                "2026-07-28T04:00:00.000Z",
            ),
        )
    eaten = await economy.eat(
        _identity(user_id="100", message_id="asamu-eat"),
        "阿萨姆红茶奶雾锅#AS000001",
    )
    assert eaten.effect.queued_effect_id == "group-next-exclusive-high-star-catch"

    # 另一玩家（U200）抓六星，gift roll = 0.39 (< 0.40) → 自动赠送给发起人
    catcher = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            # rarity_roll=0.99 命中六星、template_roll=0.0、属性 0.5×5
            0.99, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            # 阿萨姆自动赠送判定 roll = 0.39 → 触发（< 0.40）
            0.39,
        ),
        clock=clock,
        id_factory=iter(
            ("asamu-pig", "asamu-pig-ledger", "asamu-transfer", "asamu-snapshot")
        ).__next__,
        short_code_factory=iter(("AS00PIG1",)).__next__,
    )
    caught = await catcher.catch(
        _identity(user_id="200", message_id="asamu-catch")
    )
    assert caught.pig.rarity == 6
    # 六星猪转移给发起人
    owner = await database.fetch_one(
        "SELECT owner_player_id FROM pig_instances WHERE pig_instance_id = 'asamu-pig'"
    )
    assert owner is not None and owner["owner_player_id"] == activator_identity.player_id
    # transfer_event 以 system 来源写入（不进入自动监管图）
    transfer = await database.fetch_one(
        """
        SELECT transfer_type, from_player_id, to_player_id
        FROM asset_transfer_events
        WHERE transfer_type = 'system-group-effect'
        ORDER BY created_at DESC LIMIT 1
        """
    )
    assert transfer is not None
    assert transfer["from_player_id"] == "qq:100:200"
    assert transfer["to_player_id"] == activator_identity.player_id
    # 快照记录自动赠送目标
    snapshot = await database.fetch_one(
        "SELECT random_snapshot_json FROM pig_instances WHERE pig_instance_id = 'asamu-pig'"
    )
    assert '"auto_gift_target_player_id"' in str(snapshot["random_snapshot_json"])
    await database.close()



@pytest.mark.asyncio
async def test_asamu_fixed_distribution_covers_four_five_six_and_auto_gifts(
    tmp_path: Path,
) -> None:
    """阿萨姆固定概率池：1-3 星不出、4/5/6 星按 50/30/20 分配；5/6 星以 40% 概率赠送。"""

    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(1, 4, 5, 6),
        food_rarities=(1, 6),
        effect_ids={6: "group-next-exclusive-high-star-catch"},
        effect_params={
            6: {
                "fixed_weights": [0, 0, 0, 50, 30, 20],
                "uses_per_player": 1,
                "self_coin": 0,
                "other_coin": 0,
                "auto_gift_chance_percent": 40.0,
                "auto_gift_rarities": [5, 6],
                "quota_exempt": True,
                "source_label": "阿萨姆红茶奶雾锅",
            }
        },
    )
    clock = FixedClock()
    economy = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        id_factory=iter(
            ("asamu-food", "asamu-ledger", "asamu-group-effect", "asamu-eat-ledger")
        ).__next__,
        short_code_factory=iter(("AS000001",)).__next__,
    )
    activator_identity = _identity(user_id="100", message_id="asamu-seed-fix")
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO players(
                player_id, scope_id, platform_user_id, display_name,
                coin_balance, experience, created_at, updated_at
            )
            VALUES (?, 'qq:100', '100', '阿萨姆发动者', 0, 0, ?, ?)
            """,
            (
                activator_identity.player_id,
                "2026-07-28T04:00:00.000Z",
                "2026-07-28T04:00:00.000Z",
            ),
        )
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
            VALUES ('asamu-f1', 'AS000001', 'qq:100', ?, 'food-6-group', 1, NULL, 6,
                    '阿萨姆红茶奶雾锅', 1.0, 'balanced', 25000,
                    'group-next-exclusive-high-star-catch',
                    '{"auto_gift_chance_percent":40.0,"auto_gift_rarities":[5,6],"fixed_weights":[0,0,0,50,30,20],"other_coin":0,"quota_exempt":true,"self_coin":0,"source_label":"阿萨姆红茶奶雾锅","uses_per_player":1}',
                    1, '{"test":true}', 'active', ?, ?)
            """,
            (
                activator_identity.player_id,
                "2026-07-28T04:00:00.000Z",
                "2026-07-28T04:00:00.000Z",
            ),
        )
    eaten = await economy.eat(
        _identity(user_id="100", message_id="asamu-eat-fix"),
        "阿萨姆红茶奶雾锅#AS000001",
    )
    assert eaten.effect.queued_effect_id == "group-next-exclusive-high-star-catch"

    catcher = GameplayService(
        database,
        CatchingSection(cooldown_seconds=0),
        random_source=SequenceRandom(
            # U300：roll 0.05 -> 4 星（<0.5 区间），4 星不在赠送列表
            0.05, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5,
            # U400：roll 0.60 -> 5 星，gift 0.39 -> 触发赠送
            0.60, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.39,
            # U500：roll 0.90 -> 6 星，gift 0.40 位于右开边界 -> 不赠送
            0.90, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.40,
        ),
        clock=clock,
        id_factory=iter(
            [f"asamu-fix-pig-{i}" for i in range(40)]
        ).__next__,
        short_code_factory=iter(
            [f"AS0FIX{i:02d}" for i in range(40)]
        ).__next__,
    )
    # 1) 4 星：不出低星，也不赠送
    first = await catcher.catch(
        _identity(user_id="300", message_id="asamu-fix-catch-1")
    )
    assert int(first.pig.rarity) == 4
    owner = await database.fetch_one(
        "SELECT owner_player_id FROM pig_instances WHERE short_code = ?",
        (first.pig.short_code,),
    )
    assert owner is not None and owner["owner_player_id"] == "qq:100:300"
    # 2) 5 星：命中赠送 -> 归发起人
    second = await catcher.catch(
        _identity(user_id="400", message_id="asamu-fix-catch-2")
    )
    assert int(second.pig.rarity) == 5
    owner = await database.fetch_one(
        "SELECT owner_player_id FROM pig_instances WHERE short_code = ?",
        (second.pig.short_code,),
    )
    assert owner is not None and owner["owner_player_id"] == activator_identity.player_id
    # 3) 6 星：gift roll 0.40 不小于 40% -> 不赠送，留在自己背包
    third = await catcher.catch(
        _identity(user_id="500", message_id="asamu-fix-catch-3")
    )
    assert int(third.pig.rarity) == 6
    owner = await database.fetch_one(
        "SELECT owner_player_id FROM pig_instances WHERE short_code = ?",
        (third.pig.short_code,),
    )
    assert owner is not None and owner["owner_player_id"] == "qq:100:500"
    # 群效果独占结算的本次抓猪为额外次数：不消耗正常时段额度
    assert first.quota_exempt_catch is True
    assert second.quota_exempt_catch is True
    assert third.quota_exempt_catch is True
    assert first.daily_count == 0
    assert second.daily_count == 0
    assert third.daily_count == 0
    # PK 展示：回执完整呈现原主（抓猪人）、猪名编号与赠送对象（发动群友）
    assert second.effect_summaries
    gift_summary = next(
        item for item in second.effect_summaries if "抓到了" in item
    )
    assert "成员400" in gift_summary
    assert "5 星 5星测试猪" in gift_summary
    assert "成员100" in gift_summary
    assert "自动赠送" in gift_summary
    # 3 名玩家的群效果使用各消耗 1 次（usage 表每玩家一行）
    uses = await database.fetch_one(
        """
        SELECT COUNT(*) AS c
        FROM group_food_effect_usage AS usage
        JOIN group_food_effects AS effect
          ON effect.group_effect_entry_id = usage.group_effect_entry_id
        WHERE effect.effect_id = 'group-next-exclusive-high-star-catch'
        """
    )
    assert uses is not None and uses["c"] == 3
    await database.close()


@pytest.mark.asyncio
async def test_asamu_rebalance_migrates_playable_inventory_and_active_group_effect(
    tmp_path: Path,
) -> None:
    """Schema 29 收敛可用菜与未过期群效果，同时保留已消费菜品的历史快照。"""

    old_params = {
        "fixed_weights": [0, 0, 0, 50, 30, 20],
        "uses_per_player": 1,
        "self_coin": 0,
        "other_coin": 0,
        "auto_gift_chance_percent": 50.0,
        "auto_gift_rarities": [5, 6],
        "quota_exempt": True,
        "source_label": "阿萨姆红茶奶雾锅",
    }
    asamu_entry = _food_entry(
        6,
        group_id="100",
        effect_id="group-next-exclusive-high-star-catch",
        effect_params=old_params,
        template_suffix="asamu",
    )
    asamu_entry["display_name"] = "阿萨姆红茶奶雾锅"
    asamu_pig_entry = _pig_entry(
        6,
        group_id="100",
        template_suffix="asamu",
        paired_food_template_id="food-6-group-asamu",
    )
    asamu_pig_entry["display_name"] = "阿萨姆猪"
    database = await _database_with_catalog(
        tmp_path,
        food_rarities=(1,),
        extra_entries=(asamu_pig_entry, asamu_entry),
        manifest_version=4,
    )
    params_json = json.dumps(
        old_params,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    now = "2026-08-21T04:00:00.000Z"
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO players(
                player_id, scope_id, platform_user_id, display_name,
                coin_balance, experience, created_at, updated_at
            ) VALUES ('qq:100:100', 'qq:100', '100', '阿萨姆发动者', 0, 0, ?, ?)
            """,
            (now, now),
        )
        for instance_id, short_code, state in (
            ("asamu-active", "ASACTIVE", "active"),
            ("asamu-source", "ASSOURCE", "consumed"),
        ):
            await session.execute(
                """
                INSERT INTO food_instances(
                    food_instance_id, short_code, scope_id, owner_player_id,
                    template_id, template_version, source_pig_instance_id,
                    rarity, display_name_snapshot, portion_weight, fat_category,
                    official_value, effect_id, effect_params_json,
                    ruleset_version, random_snapshot_json, state,
                    acquired_at, updated_at
                ) VALUES (?, ?, 'qq:100', 'qq:100:100', 'food-6-group-asamu', 1,
                          NULL, 6, '阿萨姆红茶奶雾锅', 1.0, 'balanced', 25000,
                          'group-next-exclusive-high-star-catch', ?, 23,
                          '{"test":true}', ?, ?, ?)
                """,
                (instance_id, short_code, params_json, state, now, now),
            )
        await session.execute(
            """
            INSERT INTO group_food_effects(
                group_effect_entry_id, scope_id, source_player_id,
                source_food_instance_id, effect_id, params_json,
                granted_uses_per_player, starts_at, expires_at,
                created_at, updated_at
            ) VALUES (
                'asamu-active-effect', 'qq:100', 'qq:100:100', 'asamu-source',
                'group-next-exclusive-high-star-catch', ?, 1, ?,
                '2099-08-22T04:00:00.000Z', ?, ?
            )
            """,
            (params_json, now, now, now),
        )
        for statement in MIGRATION_0029.statements:
            await session.execute(statement)

    template = await database.fetch_one(
        "SELECT effect_params_json FROM food_templates WHERE template_id = 'food-6-group-asamu'"
    )
    active = await database.fetch_one(
        "SELECT effect_params_json FROM food_instances WHERE food_instance_id = 'asamu-active'"
    )
    historical = await database.fetch_one(
        "SELECT effect_params_json FROM food_instances WHERE food_instance_id = 'asamu-source'"
    )
    group_effect = await database.fetch_one(
        "SELECT params_json FROM group_food_effects WHERE group_effect_entry_id = 'asamu-active-effect'"
    )
    assert json.loads(str(template["effect_params_json"]))["auto_gift_chance_percent"] == 40.0
    assert json.loads(str(active["effect_params_json"]))["auto_gift_chance_percent"] == 40.0
    assert json.loads(str(historical["effect_params_json"]))["auto_gift_chance_percent"] == 50.0
    assert json.loads(str(group_effect["params_json"]))["auto_gift_chance_percent"] == 40.0
    await database.close()


@pytest.mark.asyncio
async def test_mist_daifuku_expires_at_current_quota_window_end(
    tmp_path: Path,
) -> None:
    mist_food = _food_entry(
        6,
        group_id="100",
        effect_id="next-high-star-catch",
        effect_params={
            "uses": 5,
            "four_star_percent": 61.5385,
            "five_star_percent": 30.7692,
            "six_star_percent": 7.6923,
            "current_window_only": True,
        },
        template_suffix="mist-current-window",
    )
    mist_food["display_name"] = "雾蓝键盘大福"
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(),
        food_rarities=(),
        extra_entries=(mist_food,),
    )
    owner = _identity(message_id="mist-owner")
    await FrameworkService(database).touch_identity(owner)
    await _insert_food(
        database,
        player_id=owner.player_id,
        scope_id=owner.scope.value,
        template_id=str(mist_food["template_id"]),
        display_name="雾蓝键盘大福",
        official_value=25_000,
        short_code="MIST0001",
        instance_id="mist-food",
        rarity=6,
        effect_id="next-high-star-catch",
        effect_params=dict(mist_food["effect_params"]),
    )
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
    )
    eaten = await service.eat(
        _identity(message_id="eat-mist"),
        "雾蓝键盘大福#MIST0001",
    )
    assert eaten.effect.granted_uses == 5
    effect = await database.fetch_one(
        """
        SELECT granted_uses, consumed_uses, expires_at
        FROM player_food_effects
        WHERE source_food_instance_id = 'mist-food'
        """
    )
    assert effect is not None
    assert tuple(effect) == (5, 0, "2026-07-28T11:00:00.000Z")
    await database.close()


@pytest.mark.asyncio
async def test_pork_cutlet_roulette_settles_all_six_outcomes_idempotently(
    tmp_path: Path,
) -> None:
    roulette_food = _food_entry(
        6,
        group_id="100",
        effect_id="roulette-chances",
        effect_params={"count": 3},
        template_suffix="roulette",
    )
    roulette_food["display_name"] = "猪保千猪排轮盘"
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(),
        food_rarities=(),
        extra_entries=(roulette_food,),
    )
    owner = _identity(message_id="roulette-owner")
    await FrameworkService(database).touch_identity(owner)
    for index in (1, 2):
        await _insert_food(
            database,
            player_id=owner.player_id,
            scope_id=owner.scope.value,
            template_id=str(roulette_food["template_id"]),
            display_name="猪保千猪排轮盘",
            official_value=25_000,
            short_code=f"ROULET0{index}",
            instance_id=f"roulette-food-{index}",
            rarity=6,
            effect_id="roulette-chances",
            effect_params={"count": 3},
        )
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.0, 0.20, 0.34, 0.51, 0.68, 0.99),
    )
    for index in (1, 2):
        eaten = await service.eat(
            _identity(message_id=f"eat-roulette-{index}"),
            f"猪保千猪排轮盘#ROULET0{index}",
        )
        assert "未使用机会" in eaten.effect.summary

    results = []
    first_identity = _identity(message_id="roulette-spin-1")
    first = await service.spin_roulette(first_identity)
    duplicate = await service.spin_roulette(first_identity)
    assert duplicate.receipt_created is False
    assert duplicate.receipt.receipt_id == first.receipt.receipt_id
    results.append(first)
    for index in range(2, 7):
        results.append(
            await service.spin_roulette(
                _identity(message_id=f"roulette-spin-{index}")
            )
        )
    assert [result.outcome for result in results] == [1, 2, 3, 4, 5, 6]
    assert results[0].coin_balance == 10_000
    assert results[-1].remaining_spins == 2

    state = await database.fetch_one(
        "SELECT available_spins FROM player_roulette_state WHERE player_id = ?",
        (owner.player_id,),
    )
    assert state is not None and int(state["available_spins"]) == 2
    effects = await database.fetch_all(
        """
        SELECT effect_id, params_json, granted_uses, expires_at
        FROM player_food_effects
        WHERE player_id = ? AND consumed_uses < granted_uses
        ORDER BY effect_id
        """,
        (owner.player_id,),
    )
    by_effect = {str(row["effect_id"]): row for row in effects}
    assert json.loads(by_effect["next-six-star-cook-bonus"]["params_json"]) == {
        "bonus_percent": 30.0
    }
    assert json.loads(by_effect["rolling-day-window-catches"]["params_json"]) == {
        "count": 4
    }
    assert by_effect["rolling-day-window-catches"]["expires_at"] == (
        "2026-07-29T04:00:00.000Z"
    )
    assert int(by_effect["even-catch-distribution"]["granted_uses"]) == 5
    assert int(by_effect["next-guaranteed-six-star-catch"]["granted_uses"]) == 1
    await database.close()


@pytest.mark.asyncio
async def test_aya_mousse_returns_failed_six_star_pig_without_consuming_success(
    tmp_path: Path,
) -> None:
    paired_food_id = "food-six-paired"
    source_template = _pig_entry(
        6,
        group_id="100",
        template_suffix="aya-source",
        paired_food_template_id=paired_food_id,
    )
    ordinary_five = _food_entry(5, template_suffix="ordinary-five-output")
    paired_six = _food_entry(
        6,
        group_id="100",
        template_suffix="paired-six-output",
    )
    paired_six["template_id"] = paired_food_id
    repair_food = _food_entry(
        6,
        group_id="100",
        effect_id="six-star-cook-failure-return",
        effect_params={"uses": 3, "return_chance_percent": 75},
        template_suffix="aya-repair-effect",
    )
    repair_food["display_name"] = "彩彩修车猪慕斯"
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(),
        food_rarities=(),
        extra_entries=(source_template, ordinary_five, paired_six, repair_food),
    )
    owner = _identity(message_id="aya-owner")
    await FrameworkService(database).touch_identity(owner)
    await _insert_pig(
        database,
        player_id=owner.player_id,
        scope_id=owner.scope.value,
        template_id=str(source_template["template_id"]),
        rarity=6,
        display_name=str(source_template["display_name"]),
        official_value=25_000,
        short_code="AYAPIG01",
        instance_id="aya-source-pig",
    )
    await _insert_food(
        database,
        player_id=owner.player_id,
        scope_id=owner.scope.value,
        template_id=str(repair_food["template_id"]),
        display_name="彩彩修车猪慕斯",
        official_value=25_000,
        short_code="AYAMOUS1",
        instance_id="aya-repair-food",
        rarity=6,
        effect_id="six-star-cook-failure-return",
        effect_params={"uses": 3, "return_chance_percent": 75},
    )
    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(
            0.0,
            0.50,
            0.0,
            0.5,
            0.95,
            0.0,
            0.5,
        ),
    )
    eaten = await service.eat(
        _identity(message_id="eat-aya-mousse"),
        "彩彩修车猪慕斯#AYAMOUS1",
    )
    assert eaten.effect.granted_uses == 3

    failed = await service.cook(
        _identity(message_id="aya-failed-cook"),
        "6星测试猪#AYAPIG01",
    )
    assert failed.foods[0].rarity == 5
    assert any("返还原料猪成功" in text for text in failed.effect_summaries)
    source_after_failure = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = 'aya-source-pig'"
    )
    assert source_after_failure is not None
    assert source_after_failure["state"] == "active"
    effect_after_failure = await database.fetch_one(
        """
        SELECT granted_uses, consumed_uses
        FROM player_food_effects
        WHERE source_food_instance_id = 'aya-repair-food'
        """
    )
    assert effect_after_failure is not None
    assert tuple(effect_after_failure) == (3, 1)

    succeeded = await service.cook(
        _identity(message_id="aya-successful-cook"),
        "6星测试猪#AYAPIG01",
    )
    assert succeeded.foods[0].rarity == 6
    assert any("保护次数不消耗" in text for text in succeeded.effect_summaries)
    source_after_success = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = 'aya-source-pig'"
    )
    assert source_after_success is not None
    assert source_after_success["state"] == "consumed-for-cooking"
    effect_after_success = await database.fetch_one(
        """
        SELECT granted_uses, consumed_uses
        FROM player_food_effects
        WHERE source_food_instance_id = 'aya-repair-food'
        """
    )
    assert effect_after_success is not None
    assert tuple(effect_after_success) == (3, 1)
    await database.close()


@pytest.mark.asyncio
async def test_kfc_five_star_special_food_collects_group_tribute(
    tmp_path: Path,
) -> None:
    kfc_pig = _pig_entry(4, template_suffix="kfc")
    kfc_pig.update(template_id=KFC_PIG_TEMPLATE_ID, display_name="KFC猪")
    kfc_food = _food_entry(
        5,
        effect_id="group-coin-tribute",
        effect_params={"coin_per_player": 50},
        template_suffix="kfc-bucket",
    )
    kfc_food.update(
        template_id=KFC_FOOD_TEMPLATE_ID,
        display_name="炸猪全家桶",
    )
    sukuna_pig = _pig_entry(5, template_suffix="sukuna")
    sukuna_pig.update(template_id=SUKUNA_PIG_TEMPLATE_ID, display_name="宿傩猪")
    sukuna_food = _food_entry(
        5,
        effect_id="technique-permit",
        effect_params={"technique_id": "malevolent-kitchen"},
        template_suffix="sukuna-domain",
    )
    sukuna_food.update(
        template_id=SUKUNA_FOOD_TEMPLATE_ID,
        display_name="伏魔朱焰咒纹猪蹄饭",
    )
    gojo_blue_food = _food_entry(
        5,
        effect_id="technique-permit",
        effect_params={"technique_id": "lapse-blue"},
        template_suffix="gojo-blue-exclusive",
    )
    gojo_blue_food.update(
        template_id=GOJO_BLUE_FOOD_TEMPLATE_ID,
        display_name="五条猪无量苍蓝雪山",
    )
    gojo_red_food = _food_entry(
        5,
        effect_id="technique-permit",
        effect_params={"technique_id": "reversal-red"},
        template_suffix="gojo-red-exclusive",
    )
    gojo_red_food.update(
        template_id=GOJO_RED_FOOD_TEMPLATE_ID,
        display_name="五条猪无量赫焰雪山",
    )
    ordinary_pig = _pig_entry(5, template_suffix="ordinary-five")
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(),
        food_rarities=(5,),
        extra_entries=(
            kfc_pig,
            kfc_food,
            sukuna_pig,
            sukuna_food,
            gojo_blue_food,
            gojo_red_food,
            ordinary_pig,
        ),
    )
    eater = _identity(user_id="100", message_id="kfc-eater")
    await FrameworkService(database).touch_identity(eater)
    await _insert_pig(
        database,
        player_id=eater.player_id,
        scope_id=eater.scope.value,
        template_id=KFC_PIG_TEMPLATE_ID,
        rarity=4,
        display_name="KFC猪",
        official_value=400,
        short_code="KFCPIG01",
        instance_id="kfc-pig-instance",
    )
    counters = {"id": 0, "code": 0}

    def next_id() -> str:
        counters["id"] += 1
        return f"kfc-object-{counters['id']}"

    def next_code() -> str:
        counters["code"] += 1
        return f"KFC{counters['code']:05d}"

    service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.95, 0.49, 0.0, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    cooked = await service.cook(
        _identity(user_id="100", message_id="cook-kfc"),
        "KFC猪#KFCPIG01",
    )
    assert cooked.foods[0].template_id == KFC_FOOD_TEMPLATE_ID
    assert cooked.foods[0].display_name == "炸猪全家桶"

    payer_full = _identity(user_id="201", message_id="payer-full")
    payer_partial = _identity(user_id="202", message_id="payer-partial")
    await _grant_coins(database, payer_full, 100)
    await _grant_coins(database, payer_partial, 30)
    eater_before = await database.fetch_one(
        "SELECT coin_balance FROM players WHERE player_id = ?",
        (eater.player_id,),
    )
    assert eater_before is not None
    eaten = await service.eat(
        _identity(user_id="100", message_id="eat-kfc"),
        cooked.foods[0].selector,
    )
    assert eaten.group_rewarded_players == 2
    assert eaten.group_coin_total == 80
    assert eaten.coin_balance == int(eater_before["coin_balance"]) + 80
    balances = await database.fetch_all(
        """
        SELECT platform_user_id, coin_balance
        FROM players
        WHERE player_id IN (?, ?)
        ORDER BY platform_user_id
        """,
        (payer_full.player_id, payer_partial.player_id),
    )
    assert [(row["platform_user_id"], row["coin_balance"]) for row in balances] == [
        ("201", 50),
        ("202", 0),
    ]

    await _insert_pig(
        database,
        player_id=eater.player_id,
        scope_id=eater.scope.value,
        template_id=SUKUNA_PIG_TEMPLATE_ID,
        rarity=5,
        display_name="宿傩猪",
        official_value=1000,
        short_code="SUKUNA01",
        instance_id="sukuna-pig-instance",
    )
    sukuna_service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.50, 0.19, 0.0, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    sukuna_result = await sukuna_service.cook(
        _identity(user_id="100", message_id="cook-sukuna"),
        "宿傩猪#SUKUNA01",
    )
    assert sukuna_result.foods[0].template_id == SUKUNA_FOOD_TEMPLATE_ID

    await _insert_pig(
        database,
        player_id=eater.player_id,
        scope_id=eater.scope.value,
        template_id=SUKUNA_PIG_TEMPLATE_ID,
        rarity=5,
        display_name="宿傩猪",
        official_value=1000,
        short_code="SUKUNA02",
        instance_id="sukuna-boundary-instance",
    )
    sukuna_boundary = await EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.50, 0.20, 0.0, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    ).cook(
        _identity(user_id="100", message_id="cook-sukuna-boundary"),
        "宿傩猪#SUKUNA02",
    )
    assert sukuna_boundary.foods[0].template_id != SUKUNA_FOOD_TEMPLATE_ID

    await _insert_pig(
        database,
        player_id=eater.player_id,
        scope_id=eater.scope.value,
        template_id=str(ordinary_pig["template_id"]),
        rarity=5,
        display_name=str(ordinary_pig["display_name"]),
        official_value=1000,
        short_code="NORMAL01",
        instance_id="ordinary-five-pig-instance",
    )
    ordinary_result = await EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.50, 0.999, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    ).cook(
        _identity(user_id="100", message_id="cook-ordinary-five"),
        f"{ordinary_pig['display_name']}#NORMAL01",
    )
    assert (
        ordinary_result.foods[0].template_id
        not in SOURCE_EXCLUSIVE_FOOD_TEMPLATE_IDS
    )
    await database.close()


@pytest.mark.asyncio
async def test_gojo_requires_spear_or_domain_bypass_and_yields_exclusive_food(
    tmp_path: Path,
) -> None:
    gojo = _pig_entry(5, template_suffix="gojo")
    gojo.update(template_id=GOJO_PIG_TEMPLATE_ID, display_name="五条猪")
    blue = _food_entry(
        5,
        effect_id="technique-permit",
        effect_params={"technique_id": "lapse-blue"},
        template_suffix="gojo-blue",
    )
    blue.update(
        template_id=GOJO_BLUE_FOOD_TEMPLATE_ID,
        display_name="五条猪无量苍蓝雪山",
    )
    red = _food_entry(
        5,
        effect_id="technique-permit",
        effect_params={"technique_id": "reversal-red"},
        template_suffix="gojo-red",
    )
    red.update(
        template_id=GOJO_RED_FOOD_TEMPLATE_ID,
        display_name="五条猪无量赫焰雪山",
    )
    database = await _database_with_catalog(
        tmp_path,
        pig_rarities=(),
        food_rarities=(5,),
        extra_entries=(gojo, blue, red),
    )
    identity = _identity(user_id="100", message_id="gojo-owner")
    await FrameworkService(database).touch_identity(identity)
    await _insert_pig(
        database,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id=GOJO_PIG_TEMPLATE_ID,
        rarity=5,
        display_name="五条猪",
        official_value=1000,
        short_code="GOJO0001",
        instance_id="gojo-one",
    )
    blocked = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(),
    )
    with pytest.raises(CookingTemplateError, match="无下限术式"):
        await blocked.cook(
            _identity(user_id="100", message_id="gojo-blocked"),
            "五条猪#GOJO0001",
        )
    state = await database.fetch_one(
        "SELECT state FROM pig_instances WHERE pig_instance_id = 'gojo-one'"
    )
    assert state is not None and state["state"] == "active"

    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'inverted-spear-of-heaven', 1, '2026-07-28T04:00:00.000Z')
            """,
            (identity.player_id,),
        )
        await session.execute(
            """
            INSERT INTO armed_items(player_id, action_type, item_id, armed_at)
            VALUES (?, 'cooking', 'inverted-spear-of-heaven',
                    '2026-07-28T04:00:00.000Z')
            """,
            (identity.player_id,),
        )
    counters = {"id": 0, "code": 0}

    def next_id() -> str:
        counters["id"] += 1
        return f"gojo-object-{counters['id']}"

    def next_code() -> str:
        counters["code"] += 1
        return f"GJ{counters['code']:06d}"

    spear_service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.50, 0.0, 0.0, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    spear_result = await spear_service.cook(
        _identity(user_id="100", message_id="gojo-spear"),
        "五条猪#GOJO0001",
    )
    assert spear_result.item_id == "inverted-spear-of-heaven"
    assert spear_result.foods[0].template_id == GOJO_BLUE_FOOD_TEMPLATE_ID
    assert await database.fetch_one(
        """
        SELECT 1 FROM armed_items
        WHERE player_id = ? AND action_type = 'cooking'
        """,
        (identity.player_id,),
    ) is None

    eaten_blue = await spear_service.eat(
        _identity(user_id="100", message_id="eat-gojo-blue"),
        spear_result.foods[0].selector,
    )
    assert eaten_blue.effect.queued_effect_id == "technique-permit"
    techniques = TechniqueRepository()
    async with database.transaction() as session:
        assert await techniques.available_permits(
            session,
            player_id=identity.player_id,
            technique_id=TECHNIQUE_LAPSE_BLUE,
        ) == 1

    await _insert_pig(
        database,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id=GOJO_PIG_TEMPLATE_ID,
        rarity=5,
        display_name="五条猪",
        official_value=1000,
        short_code="GOJO0003",
        instance_id="gojo-red-boundary",
    )
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'inverted-spear-of-heaven', 1,
                    '2026-07-28T04:00:00.000Z')
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = item_inventory.quantity + 1,
                updated_at = excluded.updated_at
            """,
            (identity.player_id,),
        )
        await session.execute(
            """
            INSERT INTO armed_items(player_id, action_type, item_id, armed_at)
            VALUES (?, 'cooking', 'inverted-spear-of-heaven',
                    '2026-07-28T04:00:00.000Z')
            """,
            (identity.player_id,),
        )
    red_boundary = await EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.50, 0.10, 0.0, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    ).cook(
        _identity(user_id="100", message_id="gojo-red-boundary"),
        "五条猪#GOJO0003",
    )
    assert red_boundary.foods[0].template_id == GOJO_RED_FOOD_TEMPLATE_ID

    await _insert_pig(
        database,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id=GOJO_PIG_TEMPLATE_ID,
        rarity=5,
        display_name="五条猪",
        official_value=1000,
        short_code="GOJO0004",
        instance_id="gojo-miss-boundary",
    )
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'inverted-spear-of-heaven', 1,
                    '2026-07-28T04:00:00.000Z')
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = item_inventory.quantity + 1,
                updated_at = excluded.updated_at
            """,
            (identity.player_id,),
        )
        await session.execute(
            """
            INSERT INTO armed_items(player_id, action_type, item_id, armed_at)
            VALUES (?, 'cooking', 'inverted-spear-of-heaven',
                    '2026-07-28T04:00:00.000Z')
            """,
            (identity.player_id,),
        )
    miss_boundary = await EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.50, 0.20, 0.0, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    ).cook(
        _identity(user_id="100", message_id="gojo-miss-boundary"),
        "五条猪#GOJO0004",
    )
    assert (
        miss_boundary.foods[0].template_id
        not in GOJO_EXCLUSIVE_FOOD_TEMPLATE_IDS
    )

    await _insert_pig(
        database,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id=GOJO_PIG_TEMPLATE_ID,
        rarity=5,
        display_name="五条猪",
        official_value=1000,
        short_code="GOJO0002",
        instance_id="gojo-two",
    )
    async with database.transaction() as session:
        await techniques.grant_permit(
            session,
            player_id=identity.player_id,
            technique_id=TECHNIQUE_DOMAIN_GOJO_BYPASS,
            uses=1,
            now="2026-07-28T04:00:00.000Z",
        )
    bypass_service = EconomyService(
        database,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=FixedClock(),
        random_source=SequenceRandom(0.20, 0.90, 0.0, 0.5),
        id_factory=next_id,
        short_code_factory=next_code,
    )
    bypass_result = await bypass_service.cook(
        _identity(user_id="100", message_id="gojo-domain-bypass"),
        "五条猪#GOJO0002",
    )
    assert bypass_result.foods[0].template_id == GOJO_RED_FOOD_TEMPLATE_ID
    async with database.transaction() as session:
        assert await techniques.available_permits(
            session,
            player_id=identity.player_id,
            technique_id=TECHNIQUE_DOMAIN_GOJO_BYPASS,
        ) == 0
    await database.close()
