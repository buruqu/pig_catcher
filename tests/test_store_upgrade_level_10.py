from __future__ import annotations

import sqlite3

import pytest

from pig_catcher.domain.economy import (
    build_store_products,
    cookware_higher_rarity_multiplier,
)
from pig_catcher.domain.errors import DomainValidationError
from pig_catcher.domain.rules import (
    feed_rarity_multipliers,
    level_catch_rarity_multipliers,
)
from pig_catcher.infrastructure.migrations.v0047_upgrade_level_10 import MIGRATION_0047

FEED_PRICES = [300, 600, 1000, 1600, 2600, 4200, 6800, 10500, 16000, 25000]
COOKWARE_PRICES = [300, 550, 900, 1450, 2400, 3800, 6200, 9800, 15000, 24000]


def test_ten_upgrade_levels_preserve_the_previous_maximum_effect() -> None:
    assert feed_rarity_multipliers(10) == pytest.approx((1.0, 1.0, 1.0, 1.10, 1.20, 1.30))
    assert level_catch_rarity_multipliers(21) == pytest.approx((1.0, 1.0, 1.0, 1.10, 1.20, 1.30))
    assert cookware_higher_rarity_multiplier(10) == pytest.approx(1.20)
    assert cookware_higher_rarity_multiplier(5) == pytest.approx(1.10)
    with pytest.raises(DomainValidationError, match="0 至 10"):
        feed_rarity_multipliers(11)
    with pytest.raises(DomainValidationError, match="0 至 10"):
        cookware_higher_rarity_multiplier(11)


def test_store_upgrade_products_use_all_ten_price_levels() -> None:
    products = build_store_products(
        feed_level=9,
        cookware_level=10,
        feed_prices=FEED_PRICES,
        cookware_prices=COOKWARE_PRICES,
    )
    by_id = {product.product_id: product for product in products}
    feed = by_id["upgrade-feed"]
    cookware = by_id["upgrade-cookware"]
    assert (feed.current_level, feed.target_level, feed.unit_price) == (9, 10, 25000)
    assert feed.effect_summary == "购买后提升至 Lv.10"
    assert (cookware.current_level, cookware.target_level, cookware.unit_price) == (10, 10, 0)
    assert cookware.effect_summary == "已满级"


def test_schema47_doubles_existing_upgrade_levels_and_enforces_new_limit() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE players(player_id TEXT PRIMARY KEY)")
    connection.execute(
        """
        CREATE TABLE upgrades(
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            upgrade_type TEXT NOT NULL CHECK(upgrade_type IN ('feed', 'cookware')),
            level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 5),
            updated_at TEXT NOT NULL,
            PRIMARY KEY(player_id, upgrade_type)
        )
        """
    )
    connection.executemany("INSERT INTO players VALUES(?)", (("player-a",), ("player-b",)))
    connection.executemany(
        "INSERT INTO upgrades VALUES(?,?,?,?)",
        (
            ("player-a", "feed", 5, "2026-08-29T10:00:00+08:00"),
            ("player-a", "cookware", 3, "2026-08-29T10:01:00+08:00"),
            ("player-b", "feed", 1, "2026-08-29T10:02:00+08:00"),
        ),
    )

    assert MIGRATION_0047.version == 47
    assert MIGRATION_0047.name == "upgrade-level-10"
    for statement in MIGRATION_0047.statements:
        connection.execute(statement)

    assert connection.execute(
        "SELECT player_id, upgrade_type, level, updated_at FROM upgrades ORDER BY player_id, upgrade_type"
    ).fetchall() == [
        ("player-a", "cookware", 6, "2026-08-29T10:01:00+08:00"),
        ("player-a", "feed", 10, "2026-08-29T10:00:00+08:00"),
        ("player-b", "feed", 2, "2026-08-29T10:02:00+08:00"),
    ]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO upgrades VALUES('player-b','cookware',11,'2026-08-29T10:03:00+08:00')"
        )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
