"""2.0 全量接入数值：逐档香料、配置一致性及不重估历史资产。"""

# 宿主 AGENTS.md 规定标准库 from 导入在直接 import 之前。
# ruff: noqa: I001

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import json
import sqlite3
import tomllib

import pytest

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.config.model import CookingSection, EconomySection
from pig_catcher.domain.economy import (
    FOOD_BASE_VALUES,
    adjusted_cooking_weights,
    generate_food_attributes,
    stable_recipe_factor,
)
from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.domain.food_effects import (
    ActiveFoodEffect,
    apply_cooking_effects,
    resolve_food_effect,
)
from pig_catcher.domain.gameplay import PIG_BASE_VALUES
from pig_catcher.domain.rules import shift_original_probability_up_one_tier
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.migrations.v0045_economy_template_balance import MIGRATION_0045
from pig_catcher.services import AssetCatalogService, EconomyService
from pig_catcher.version import RULESET_VERSION, SCHEMA_VERSION

from .test_asset_code_lifecycle import (
    NOW,
    OWNER,
    _asset_values,
    _create_v41,
    _insert_sql,
    _snapshot,
)
from .test_economy import (
    FixedClock,
    SequenceRandom,
    _database_with_catalog,
    _food_entry,
    _grant_coins,
    _identity,
    _insert_food,
    _insert_pig,
)

ROOT = Path(__file__).resolve().parents[1]
LEMON_TEMPLATE_ID = "food-r4-pig-paw-lemon-tea"


@pytest.mark.parametrize(
    ("source_rarity", "expected"),
    (
        (1, (57, 40, 3, 0, 0, 0)),
        (2, (0, 77, 21, 2, 0, 0)),
        (3, (0, 2, 78, 18, 2, 0)),
        (4, (0, 0, 17, 73, 10, 0)),
        (5, (0, 0, 0, 17, 83, 0)),
        (6, (0, 0, 0, 0, 90, 10)),
    ),
)
def test_chef_spice_uses_full_progressive_budget_without_new_rarity(source_rarity, expected):
    actual = adjusted_cooking_weights(
        source_rarity,
        size_percentile=0.0,
        weight_percentile=0.0,
        cookware_level=0,
        chef_spice=True,
    )
    assert actual == pytest.approx(expected)


def test_probability_mass_is_moved_at_most_once_and_gaps_are_not_created():
    # 1星搬来的5点不能混入原2星的5点再次升到3星。
    assert shift_original_probability_up_one_tier((5, 5, 90, 0, 0, 0), shift_percent=18) == pytest.approx(
        (0, 5, 95, 0, 0, 0)
    )
    assert shift_original_probability_up_one_tier((5, 0, 90, 5, 0, 0), shift_percent=18) == pytest.approx(
        (5, 0, 72, 23, 0, 0)
    )
    assert shift_original_probability_up_one_tier((0, 0, 0, 0, 100, 0), shift_percent=18) == pytest.approx(
        (0, 0, 0, 0, 100, 0)
    )


@pytest.mark.parametrize("budget", (-1.0, 100.1, float("nan"), float("inf")))
def test_progressive_budget_rejects_invalid_values(budget):
    with pytest.raises(DomainValidationError, match="预算"):
        shift_original_probability_up_one_tier((40, 30, 17, 8, 4, 1), shift_percent=budget)


def test_chef_spice_runs_after_attributes_but_does_not_change_same_family_food():
    before = adjusted_cooking_weights(
        4,
        size_percentile=0.5,
        weight_percentile=0.5,
        cookware_level=0,
        chef_spice=False,
    )
    after = adjusted_cooking_weights(
        4,
        size_percentile=0.5,
        weight_percentile=0.5,
        cookware_level=0,
        chef_spice=True,
    )
    assert before == pytest.approx((0, 1, 29, 60, 10, 0))
    assert after == pytest.approx((0, 0, 13, 77, 10, 0))
    # 旧同族菜仍只搬最低档剩下的1点，不受香料的算法重做影响。
    effect = ActiveFoodEffect(
        effect_entry_id="old-quality-food",
        effect_id="next-cook-quality",
        params={"shift_percent": 15},
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at=NOW,
        source_food_rarity=4,
        source_food_name="猪猪玉子烧",
    )
    result = apply_cooking_effects(before, (effect,), source_rarity=4)
    assert result.weights == pytest.approx((0, 0, 30, 60, 10, 0))


def test_packaged_upgrade_defaults_match_model_and_do_not_override_custom_prices():
    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    model = EconomySection()
    assert model.feed_upgrade_prices == config["economy"]["feed_upgrade_prices"] == [300, 800, 1800, 4000, 8000]
    assert model.cookware_upgrade_prices == config["economy"]["cookware_upgrade_prices"] == [300, 700, 1600, 3500, 7000]
    assert sum(model.feed_upgrade_prices) + sum(model.cookware_upgrade_prices) == 28000
    custom = EconomySection(feed_upgrade_prices=[100, 200, 300, 400, 500])
    assert custom.feed_upgrade_prices == [100, 200, 300, 400, 500]


def test_new_food_base_values_preserve_pig_prices_and_absolute_weight_independence():
    assert tuple(FOOD_BASE_VALUES.values()) == (14, 42, 120, 420, 1800, 25000)
    assert tuple(PIG_BASE_VALUES.values()) == (20, 55, 150, 450, 1500, 5000)
    for rarity, base in FOOD_BASE_VALUES.items():
        ordinary = generate_food_attributes(
            rarity=rarity,
            template_id="same-reviewed-recipe",
            source_weight=100,
            source_weight_percentile=0.5,
            portion_roll=0.5,
        )
        giant = generate_food_attributes(
            rarity=rarity,
            template_id="same-reviewed-recipe",
            source_weight=100000,
            source_weight_percentile=0.5,
            portion_roll=0.5,
        )
        expected = round(base * stable_recipe_factor("same-reviewed-recipe"))
        assert ordinary.official_value == giant.official_value == expected
        assert giant.portion_weight == pytest.approx(ordinary.portion_weight * 1000)


def test_catalog_lemon_effect_matches_both_packages_without_changing_souffle():
    for relative in ("catalogs/formal/pig-and-food-definitions.json", "asset_library/current/assets.json"):
        entries = json.loads((ROOT / relative).read_text(encoding="utf-8"))["entries"]
        by_id = {entry["template_id"]: entry for entry in entries}
        lemon = by_id[LEMON_TEMPLATE_ID]
        assert lemon["effect_id"] == "next-pig-stature"
        assert lemon["effect_params"] == {"mode": "mini", "strength": 0.5}
        assert by_id["food-r4-souffle"]["effect_params"] == {"mode": "mini", "strength": 0.35, "uses": 2}
        assert "-0.5" in resolve_food_effect(lemon["effect_id"], lemon["effect_params"]).summary


def _create_previous_economy_database(path: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = _create_v41(path)
    for migration in MIGRATIONS:
        if 42 <= migration.version <= 44:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, NOW))
    connection.execute("PRAGMA user_version=44")
    connection.row_factory = sqlite3.Row
    template = dict(connection.execute("SELECT * FROM food_templates WHERE template_id='code-food'").fetchone())
    template.update(
        template_id=LEMON_TEMPLATE_ID,
        display_name="猪趴柠檬茶",
        template_version=7,
        rarity=4,
        effect_id="next-pig-stature",
        effect_params_json='{"mode":"mini","strength":0.22}',
    )
    connection.execute(
        f"INSERT INTO food_templates({','.join(template)}) VALUES({','.join('?' for _ in template)})",
        tuple(template.values()),
    )
    for index, state in enumerate(("active", "locked-for-trade", "sold", "consumed", "admin-removed")):
        food = _asset_values("food", f"old-lemon-{index}", f"Lemon{index}", state=state)
        food.update(
            template_id=LEMON_TEMPLATE_ID,
            rarity=4,
            template_version=7,
            effect_id="next-pig-stature",
            effect_params_json='{"mode":"mini","strength":0.22}',
            official_value=313,
            ruleset_version=38,
        )
        connection.execute(_insert_sql("food", food), food)
    pig = _asset_values("pig", "old-pig", "OldPig")
    connection.execute(_insert_sql("pig", pig), pig)
    connection.execute(
        "INSERT INTO player_food_effects(effect_entry_id,player_id,source_food_instance_id,effect_id,params_json,"
        "granted_uses,consumed_uses,created_at,updated_at) "
        "VALUES('old-lemon-queue',?,'old-lemon-3','next-pig-stature',?,1,0,?,?)",
        (OWNER, '{"mode":"mini","strength":0.22}', NOW, NOW),
    )
    connection.execute("INSERT INTO upgrades VALUES(?,'feed',4,?)", (OWNER, NOW))
    connection.execute("INSERT INTO item_inventory VALUES(?,'coin-bounty-tag',2,?)", (OWNER, NOW))
    connection.execute(
        "INSERT INTO armed_items(player_id,action_type,item_id,armed_at,remaining_uses) "
        "VALUES(?,'catching','coin-bounty-tag',?,2)",
        (OWNER, NOW),
    )
    connection.commit()
    connection.row_factory = None
    before = _snapshot(connection)
    connection.close()
    return before


@pytest.mark.asyncio
async def test_schema45_changes_only_new_lemon_template_and_preserves_all_other_rows(tmp_path):
    path = tmp_path / "before-45.sqlite3"
    before = _create_previous_economy_database(path)
    database = PigCatcherDatabase(path)
    await database.open()
    try:
        assert await database.schema_version() == SCHEMA_VERSION
        template = dict(
            await database.fetch_one("SELECT * FROM food_templates WHERE template_id=?", (LEMON_TEMPLATE_ID,))
        )
        assert json.loads(template["effect_params_json"]) == {"mode": "mini", "strength": 0.5}
        assert template["template_version"] == 8
        columns = [str(row["name"]) for row in await database.fetch_all("PRAGMA table_info(food_templates)")]
        old_templates = {str(row[0]): dict(zip(columns, row, strict=True)) for row in before["food_templates"]}
        for current in await database.fetch_all("SELECT * FROM food_templates ORDER BY rowid"):
            current = dict(current)
            previous = old_templates[str(current["template_id"])]
            if current["template_id"] == LEMON_TEMPLATE_ID:
                for field in ("effect_params_json", "template_version", "updated_at"):
                    current.pop(field)
                    previous.pop(field)
            assert current == previous
        for name, rows in before.items():
            if name == "food_templates":
                continue
            assert [tuple(row) for row in await database.fetch_all(f'SELECT * FROM "{name}" ORDER BY rowid')] == rows
        assert await database.integrity_check() == ("ok",)
        assert await database.fetch_all("PRAGMA foreign_key_check") == []
        async with database.transaction() as session:
            for statement in MIGRATION_0045.statements:
                await session.execute(statement)
        repeated = dict(
            await database.fetch_one("SELECT * FROM food_templates WHERE template_id=?", (LEMON_TEMPLATE_ID,))
        )
        assert repeated == template
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_catalog_update_preserves_old_food_but_new_cooking_gets_new_price_and_effect(tmp_path):
    lemon = _food_entry(4, effect_id="next-pig-stature", effect_params={"mode": "mini", "strength": 0.22})
    lemon.update(template_id=LEMON_TEMPLATE_ID, display_name="猪趴柠檬茶")
    database = await _database_with_catalog(tmp_path, pig_rarities=(4,), food_rarities=(), extra_entries=(lemon,))
    clock, identity = FixedClock(), _identity(message_id="economy-before-update")
    try:
        await _grant_coins(database, identity, 100)
        for name in ("sale", "eat"):
            await _insert_food(
                database,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                template_id=LEMON_TEMPLATE_ID,
                display_name="猪趴柠檬茶",
                official_value=313,
                short_code=f"Old{name}",
                instance_id=f"old-{name}",
                rarity=4,
                effect_id="next-pig-stature",
                effect_params={"mode": "mini", "strength": 0.22},
            )
        old_rows = [
            dict(row) for row in await database.fetch_all("SELECT * FROM food_instances ORDER BY food_instance_id")
        ]
        manifest_path = tmp_path / "source" / "assets.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["entries"]:
            if entry["template_id"] == LEMON_TEMPLATE_ID:
                entry["effect_params"]["strength"] = 0.5
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        catalog = AssetCatalogService(
            database, AssetCatalogStorage(tmp_path / "data"), min_image_side=32, max_image_bytes=1024 * 1024
        )
        await catalog.import_manifest(manifest_path)
        after_import = [
            dict(row) for row in await database.fetch_all("SELECT * FROM food_instances ORDER BY food_instance_id")
        ]
        assert after_import == old_rows
        await _insert_pig(
            database,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id="pig-4-common",
            rarity=4,
            display_name="旧原料猪",
            official_value=411,
            short_code="OldSource",
            instance_id="old-source",
        )
        economy = EconomyService(
            database,
            CookingSection(cook_cooldown_seconds=0),
            EconomySection(),
            clock=clock,
            random_source=SequenceRandom(0.5, 0.0, 0.5),
        )
        cook_identity = replace(identity, message_id="new-rule-cook")
        cooked = await economy.cook(cook_identity, "旧原料猪#OldSource")
        new_food = cooked.foods[0]
        assert new_food.rarity == 4
        assert new_food.official_value == round(420 * stable_recipe_factor(LEMON_TEMPLATE_ID))
        assert new_food.effect_params == {"mode": "mini", "strength": 0.5}
        saved = await database.fetch_one(
            "SELECT ruleset_version FROM food_instances WHERE food_instance_id=?", (new_food.food_instance_id,)
        )
        assert saved[0] == RULESET_VERSION
        assert (await economy.cook(cook_identity, "旧原料猪#OldSource")).receipt_created is False
        old_eaten = await economy.eat(replace(identity, message_id="eat-old-rule"), "猪趴柠檬茶#Oldeat")
        new_eaten = await economy.eat(replace(identity, message_id="eat-new-rule"), new_food.selector)
        assert old_eaten.effect.queued_effect_params["strength"] == 0.22
        assert new_eaten.effect.queued_effect_params["strength"] == 0.5
        assert "-0.22" in old_eaten.effect.summary and "-0.5" in new_eaten.effect.summary
        sold = await economy.sell_food(replace(identity, message_id="sell-old-snapshot"), "猪趴柠檬茶#Oldsale")
        assert sold.food.official_value == 313
        assert sold.balance_after == 100 + 45 + 313
        ledger = await economy.ledger(identity, page=1)
        assert ledger.coin_balance == ledger.ledger_total == sold.balance_after
    finally:
        await database.close()
