"""功能分商城库存授予与不可变购买账本。"""

from __future__ import annotations

from dataclasses import replace

import aiosqlite
import pytest

from pig_catcher.domain.feature_shop import feature_shop_product_by_name
from pig_catcher.infrastructure.database import DatabaseSession
from pig_catcher.infrastructure.migrations.v0048_feature_tool_store_ledger import MIGRATION_0048
from pig_catcher.infrastructure.repositories.economy import EconomyRepository

PLAYER_ID = "player-feature-shop"
SCOPE_ID = "qq:feature-shop"
NOW = "2026-08-29T16:00:00+08:00"


@pytest.fixture
async def feature_shop_database():
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.executescript(
        """
        CREATE TABLE scopes(scope_id TEXT PRIMARY KEY);
        CREATE TABLE players(
            player_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id)
        );
        CREATE TABLE dispatch_tools(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            tool_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity >= 0),
            PRIMARY KEY(player_id, tool_id)
        );
        CREATE TABLE tour_tools(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            tool_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity >= 0),
            PRIMARY KEY(player_id, tool_id)
        );
        CREATE TABLE battle_tools(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            tool_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity >= 0),
            PRIMARY KEY(player_id, tool_id)
        );
        INSERT INTO scopes VALUES('qq:feature-shop');
        INSERT INTO players VALUES('player-feature-shop', 'qq:feature-shop');
        """
    )
    for statement in MIGRATION_0048.statements:
        await connection.executescript(statement)
    await connection.commit()
    try:
        yield connection, DatabaseSession(connection)
    finally:
        await connection.close()


async def _grant(
    repository: EconomyRepository,
    session: DatabaseSession,
    *,
    name: str,
    quantity: int,
    key: str,
) -> int:
    product = feature_shop_product_by_name(name)
    return await repository.add_feature_tool_inventory(
        session,
        product=product,
        player_id=PLAYER_ID,
        scope_id=SCOPE_ID,
        quantity=quantity,
        unit_price=product.unit_price,
        total_price=product.unit_price * quantity,
        ledger_entry_key=key,
        source_kind="store-purchase",
        now=NOW,
    )


@pytest.mark.asyncio
async def test_grants_each_feature_system_accumulates_and_records_exact_ledger(
    feature_shop_database,
) -> None:
    connection, session = feature_shop_database
    repository = EconomyRepository()

    assert await _grant(repository, session, name="区域地图", quantity=2, key="buy-map-1") == 2
    assert await _grant(repository, session, name="区域地图", quantity=3, key="buy-map-2") == 5
    assert await _grant(repository, session, name="备用线缆", quantity=1, key="buy-cable") == 1
    assert await _grant(repository, session, name="练习护腕", quantity=4, key="buy-wristband") == 4

    assert (
        await (
            await connection.execute(
                "SELECT quantity FROM dispatch_tools WHERE player_id=? AND tool_id='region-map'",
                (PLAYER_ID,),
            )
        ).fetchone()
    )[0] == 5
    assert (
        await (
            await connection.execute(
                "SELECT quantity FROM tour_tools WHERE player_id=? AND tool_id='cable'",
                (PLAYER_ID,),
            )
        ).fetchone()
    )[0] == 1
    assert (
        await (
            await connection.execute(
                "SELECT quantity FROM battle_tools WHERE player_id=? AND tool_id='wristband'",
                (PLAYER_ID,),
            )
        ).fetchone()
    )[0] == 4

    rows = await (
        await connection.execute(
            """
            SELECT entry_key, player_id, scope_id, system, product_id, tool_id,
                   delta, balance_after, unit_price, quantity, total_price,
                   source_kind, occurred_at
            FROM feature_tool_store_ledger
            ORDER BY entry_key
            """
        )
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (
            "buy-cable",
            PLAYER_ID,
            SCOPE_ID,
            "tour",
            "feature-tour-cable",
            "cable",
            1,
            1,
            520,
            1,
            520,
            "store-purchase",
            NOW,
        ),
        (
            "buy-map-1",
            PLAYER_ID,
            SCOPE_ID,
            "dispatch",
            "feature-dispatch-region-map",
            "region-map",
            2,
            2,
            520,
            2,
            1040,
            "store-purchase",
            NOW,
        ),
        (
            "buy-map-2",
            PLAYER_ID,
            SCOPE_ID,
            "dispatch",
            "feature-dispatch-region-map",
            "region-map",
            3,
            5,
            520,
            3,
            1560,
            "store-purchase",
            NOW,
        ),
        (
            "buy-wristband",
            PLAYER_ID,
            SCOPE_ID,
            "battle",
            "feature-battle-wristband",
            "wristband",
            4,
            4,
            880,
            4,
            3520,
            "store-purchase",
            NOW,
        ),
    ]


@pytest.mark.asyncio
async def test_same_ledger_key_replays_once_and_conflicting_replay_is_rejected(
    feature_shop_database,
) -> None:
    connection, session = feature_shop_database
    repository = EconomyRepository()

    assert await _grant(repository, session, name="区域地图", quantity=2, key="same-key") == 2
    assert await _grant(repository, session, name="区域地图", quantity=2, key="same-key") == 2
    with pytest.raises(ValueError, match="重放参数冲突"):
        await _grant(repository, session, name="区域地图", quantity=3, key="same-key")

    inventory = await (
        await connection.execute(
            "SELECT quantity FROM dispatch_tools WHERE player_id=? AND tool_id='region-map'",
            (PLAYER_ID,),
        )
    ).fetchone()
    ledger_count = await (
        await connection.execute("SELECT COUNT(*) FROM feature_tool_store_ledger WHERE entry_key='same-key'")
    ).fetchone()
    assert inventory[0] == 2
    assert ledger_count[0] == 1


@pytest.mark.asyncio
async def test_forged_target_inventory_is_rejected_without_any_write(feature_shop_database) -> None:
    connection, session = feature_shop_database
    repository = EconomyRepository()
    product = feature_shop_product_by_name("区域地图")
    forged = replace(product, target_inventory="arbitrary_user_table")
    await connection.execute("CREATE TABLE arbitrary_user_table(value TEXT)")

    with pytest.raises(ValueError, match="未登记"):
        await repository.add_feature_tool_inventory(
            session,
            product=forged,
            player_id=PLAYER_ID,
            scope_id=SCOPE_ID,
            quantity=1,
            unit_price=product.unit_price,
            total_price=product.unit_price,
            ledger_entry_key="forged-target",
            source_kind="store-purchase",
            now=NOW,
        )

    assert (await (await connection.execute("SELECT COUNT(*) FROM arbitrary_user_table")).fetchone())[0] == 0
    assert (await (await connection.execute("SELECT COUNT(*) FROM dispatch_tools")).fetchone())[0] == 0
    assert (await (await connection.execute("SELECT COUNT(*) FROM feature_tool_store_ledger")).fetchone())[0] == 0


@pytest.mark.asyncio
async def test_outer_transaction_rollback_removes_inventory_and_ledger(feature_shop_database) -> None:
    connection, session = feature_shop_database
    repository = EconomyRepository()

    await connection.execute("BEGIN IMMEDIATE")
    await _grant(repository, session, name="应急绷带", quantity=2, key="rolled-back")
    await connection.rollback()

    assert (await (await connection.execute("SELECT COUNT(*) FROM battle_tools")).fetchone())[0] == 0
    assert (await (await connection.execute("SELECT COUNT(*) FROM feature_tool_store_ledger")).fetchone())[0] == 0
