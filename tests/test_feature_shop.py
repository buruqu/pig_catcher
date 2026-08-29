"""三个独立功能商城的目录合同。"""

import sqlite3
from pathlib import Path

import pytest

from pig_catcher.domain.battle_catalog import TOOLS as BATTLE_TOOLS
from pig_catcher.domain.dispatch import TOOLS as DISPATCH_TOOLS
from pig_catcher.domain.errors import MigrationError, StoreProductError
from pig_catcher.domain.feature_shop import (
    FEATURE_SHOP_PRODUCTS,
    FEATURE_SHOP_PRODUCTS_BY_ID,
    FEATURE_SHOP_PRODUCTS_BY_NAME,
    FEATURE_SHOP_TARGET_INVENTORIES,
    FeatureShopSystem,
    build_feature_shop_products,
    feature_shop_product_by_id,
    feature_shop_product_by_name,
    feature_shop_system,
)
from pig_catcher.domain.tour_catalog import TOOLS as TOUR_TOOLS
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.migrations.v0048_feature_tool_store_ledger import MIGRATION_0048

EXPECTED_PRICES = {
    "feature-dispatch-region-map": 520,
    "feature-dispatch-souvenir-camera": 720,
    "feature-dispatch-encounter-compass": 1480,
    "feature-dispatch-sorting-box": 420,
    "feature-tour-cable": 520,
    "feature-tour-cue": 760,
    "feature-tour-recorder": 880,
    "feature-tour-confetti": 220,
    "feature-battle-wristband": 880,
    "feature-battle-bandage": 820,
    "feature-battle-confetti": 220,
}


def test_feature_shop_has_exactly_eleven_unique_products_and_prices() -> None:
    assert len(FEATURE_SHOP_PRODUCTS) == 11
    assert len(FEATURE_SHOP_PRODUCTS_BY_ID) == 11
    assert len(FEATURE_SHOP_PRODUCTS_BY_NAME) == 11
    assert len({(product.system, product.tool_id) for product in FEATURE_SHOP_PRODUCTS}) == 11
    assert {product.product_id: product.unit_price for product in FEATURE_SHOP_PRODUCTS} == EXPECTED_PRICES


@pytest.mark.parametrize(
    ("system", "label", "definitions", "inventory"),
    (
        (FeatureShopSystem.DISPATCH, "派遣", DISPATCH_TOOLS, "dispatch_tools"),
        (FeatureShopSystem.TOUR, "巡演", TOUR_TOOLS, "tour_tools"),
        (FeatureShopSystem.BATTLE, "对战", BATTLE_TOOLS, "battle_tools"),
    ),
)
def test_each_feature_shop_reuses_the_complete_verified_tool_catalog(
    system: FeatureShopSystem,
    label: str,
    definitions: tuple,
    inventory: str,
) -> None:
    products = build_feature_shop_products(label)
    assert products == build_feature_shop_products(system.value)
    assert [product.tool_id for product in products] == [tool.tool_id for tool in definitions]
    assert all(product.system is system for product in products)
    assert all(product.category == label for product in products)
    assert all(product.target_inventory == inventory for product in products)
    assert FEATURE_SHOP_TARGET_INVENTORIES[system] == inventory

    expected_summaries = {
        tool.tool_id: getattr(tool, "summary", getattr(tool, "description", "")) for tool in definitions
    }
    assert {product.tool_id: product.effect_summary for product in products} == expected_summaries


def test_feature_shop_lookup_is_exact_and_rejects_unknown_products() -> None:
    product = feature_shop_product_by_name("奇遇罗盘")
    assert product is feature_shop_product_by_id("feature-dispatch-encounter-compass")
    assert feature_shop_system("BATTLE") is FeatureShopSystem.BATTLE

    with pytest.raises(StoreProductError, match="只能选择"):
        feature_shop_system("全部")
    with pytest.raises(StoreProductError, match="没有商品 ID"):
        feature_shop_product_by_id("feature-unknown")
    with pytest.raises(StoreProductError, match="没有"):
        feature_shop_product_by_name("不存在的器具")


def test_feature_tool_store_ledger_is_scoped_and_immutable() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE scopes(scope_id TEXT PRIMARY KEY);
        CREATE TABLE players(
            player_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id)
        );
        INSERT INTO scopes VALUES('qq:group-a'),('qq:group-b');
        INSERT INTO players VALUES('player-a','qq:group-a');
        """
    )
    for statement in MIGRATION_0048.statements:
        connection.executescript(statement)

    row = (
        "purchase-a",
        "player-a",
        "qq:group-a",
        "dispatch",
        "feature-dispatch-region-map",
        "region-map",
        2,
        3,
        520,
        2,
        1040,
        "store-purchase",
        "2026-08-29T12:00:00+08:00",
    )
    connection.execute("INSERT INTO feature_tool_store_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", row)

    with pytest.raises(sqlite3.IntegrityError, match="群范围"):
        connection.execute(
            "INSERT INTO feature_tool_store_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("wrong-scope", row[1], "qq:group-b", *row[3:]),
        )
    with pytest.raises(sqlite3.IntegrityError, match="不可改写"):
        connection.execute(
            "UPDATE feature_tool_store_ledger SET balance_after=4 WHERE entry_key='purchase-a'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="不可删除"):
        connection.execute("DELETE FROM feature_tool_store_ledger WHERE entry_key='purchase-a'")

    stored = connection.execute(
        "SELECT system,tool_id,delta,balance_after,total_price FROM feature_tool_store_ledger"
    ).fetchone()
    assert stored == ("dispatch", "region-map", 2, 3, 1040)
    connection.close()


@pytest.mark.asyncio
async def test_stamped_schema_48_rejects_missing_feature_store_ledger(tmp_path: Path) -> None:
    path = tmp_path / "missing-feature-store-ledger.sqlite3"
    database = PigCatcherDatabase(path)
    await database.open()
    await database.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE feature_tool_store_ledger")
    connection.commit()
    connection.close()

    malformed = PigCatcherDatabase(path)
    with pytest.raises(MigrationError, match="feature_tool_store_ledger"):
        await malformed.open()


@pytest.mark.asyncio
async def test_stamped_schema_48_rejects_legacy_five_level_upgrade_constraint(tmp_path: Path) -> None:
    path = tmp_path / "legacy-upgrade-constraint.sqlite3"
    database = PigCatcherDatabase(path)
    await database.open()
    await database.close()

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys=OFF;
        ALTER TABLE upgrades RENAME TO upgrades_v48;
        CREATE TABLE upgrades (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            upgrade_type TEXT NOT NULL CHECK (upgrade_type IN ('feed', 'cookware')),
            level INTEGER NOT NULL CHECK (level BETWEEN 0 AND 5),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (player_id, upgrade_type)
        );
        INSERT INTO upgrades SELECT * FROM upgrades_v48;
        DROP TABLE upgrades_v48;
        PRAGMA foreign_keys=ON;
        """
    )
    connection.commit()
    connection.close()

    malformed = PigCatcherDatabase(path)
    with pytest.raises(MigrationError, match="0 至 10"):
        await malformed.open()
