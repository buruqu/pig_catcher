"""Run isolated fourth-round command UAT against the formal catalog."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.uat_third_round import (  # noqa: E402
    DeliveryCollector,
    FixedRandom,
    LocalContext,
    build_message,
    clone_formal_data,
    command_kwargs,
    create_plugin,
    invoke,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--authorized-group", default="1092931381")
    parser.add_argument("--ordinary-group", default="1092931382")
    return parser.parse_args()


def configure_plugin(plugin: Any) -> None:
    config = plugin.get_default_config()
    config["maintenance"]["enabled"] = False
    config["catching"]["cooldown_seconds"] = 0
    config["catching"]["daily_limit"] = 100
    plugin.set_plugin_config(config)


def message(
    *,
    group_id: str,
    user_id: str,
    message_id: str,
) -> dict[str, object]:
    return build_message(
        group_id=group_id,
        group_name=f"第四轮UAT群{group_id}",
        user_id=user_id,
        display_name=f"第四轮成员{user_id}",
        stream_id=f"uat-round4-{group_id}",
        message_id=message_id,
    )


async def active_selector(
    plugin: Any,
    *,
    table: str,
    player_id: str,
) -> tuple[str, int]:
    identifier = "pig_instance_id" if table == "pig_instances" else "food_instance_id"
    row = await plugin.database.fetch_one(
        f"""
        SELECT display_name_snapshot, short_code, rarity
        FROM {table}
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY acquired_at DESC, {identifier} DESC
        LIMIT 1
        """,
        (player_id,),
    )
    if row is None:
        raise AssertionError(f"No active asset in {table} for {player_id}")
    return f"{row['display_name_snapshot']}#{row['short_code']}", int(row["rarity"])


async def seed_coins(
    plugin: Any,
    *,
    player_id: str,
    scope_id: str,
    amount: int,
) -> None:
    row = await plugin.database.fetch_one(
        "SELECT coin_balance FROM players WHERE player_id = ?",
        (player_id,),
    )
    if row is None:
        raise AssertionError("Cannot seed missing UAT player.")
    balance_after = int(row["coin_balance"]) + amount
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            UPDATE players
            SET coin_balance = coin_balance + ?,
                updated_at = '2026-07-28T00:00:00.000Z'
            WHERE player_id = ?
            """,
            (amount, player_id),
        )
        await session.execute(
            """
            INSERT INTO currency_ledger(
                ledger_entry_id, player_id, scope_id, amount, balance_after,
                reason_code, reason_text, source_object_type, source_object_id,
                idempotency_key, created_at
            )
            VALUES (
                'round4-uat-seed', ?, ?, ?, ?,
                'uat-seed', '第四轮隔离验收入账', 'uat', 'seed',
                'round4-uat-seed', '2026-07-28T00:00:00.000Z'
            )
            """,
            (player_id, scope_id, amount, balance_after),
        )


async def run(args: argparse.Namespace) -> dict[str, object]:
    source_data = args.data_dir.resolve()
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    cloned_data = output_root / "data"
    deliveries = output_root / "deliveries"
    deliveries.mkdir()
    clone_formal_data(
        source_data_dir=source_data,
        target_data_dir=cloned_data,
        database_filename=args.database_filename,
    )

    launch_options: dict[str, object] = {"headless": True}
    if args.browser_executable:
        launch_options["executable_path"] = str(args.browser_executable.resolve())
    records: list[dict[str, object]] = []
    collector = DeliveryCollector(deliveries)
    authorized_user = "round4-authorized-user"
    ordinary_user = "round4-ordinary-user"
    authorized_player = f"qq:{args.authorized_group}:{authorized_user}"
    ordinary_player = f"qq:{args.ordinary_group}:{ordinary_user}"
    cook_message = message(
        group_id=args.authorized_group,
        user_id=authorized_user,
        message_id="round4-cook-six",
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_options)
        plugin = create_plugin()
        configure_plugin(plugin)
        context = LocalContext(
            data_dir=cloned_data,
            browser=browser,
            collector=collector,
        )
        plugin._set_context(context)
        await plugin.on_load()
        if plugin.economy_service is None or plugin.gameplay_service is None:
            raise AssertionError("Fourth-round services did not load.")

        plugin.gameplay_service.random_source = FixedRandom(
            [0.999999, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
        )
        await invoke(
            records,
            collector,
            "authorized catch six",
            plugin.handle_catch(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-catch-six",
                    )
                ),
            ),
        )
        pig_selector, pig_rarity = await active_selector(
            plugin,
            table="pig_instances",
            player_id=authorized_player,
        )
        if pig_rarity != 6:
            raise AssertionError(f"Authorized highest-boundary pig was {pig_rarity} stars.")
        plugin.economy_service.random_source = FixedRandom([0.999999, 0.0, 0.5])
        await invoke(
            records,
            collector,
            "authorized cook six",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(cook_message, selector=pig_selector),
            ),
        )
        duplicate = await invoke(
            records,
            collector,
            "duplicate cook suppression",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(cook_message, selector=pig_selector),
            ),
            expected_new_deliveries=0,
        )
        if duplicate[2] != 0:
            raise AssertionError("Duplicate cook did not return suppression priority.")
        food_selector, food_rarity = await active_selector(
            plugin,
            table="food_instances",
            player_id=authorized_player,
        )
        if food_rarity != 6:
            raise AssertionError(f"Six-star pig highest-boundary food was {food_rarity} stars.")

        query = message(
            group_id=args.authorized_group,
            user_id=authorized_user,
            message_id="round4-query",
        )
        await invoke(
            records,
            collector,
            "food detail",
            plugin.handle_food_detail(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(query, selector=food_selector),
            ),
        )
        await invoke(
            records,
            collector,
            "food inventory",
            plugin.handle_food_inventory(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(query, arguments="1 排序=价值"),
            ),
        )
        await invoke(
            records,
            collector,
            "food catalog",
            plugin.handle_food_catalog(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(query, arguments=""),
            ),
        )
        await invoke(
            records,
            collector,
            "eat food",
            plugin.handle_eat(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-eat",
                    ),
                    selector=food_selector,
                ),
            ),
        )
        plugin.gameplay_service.random_source = FixedRandom(
            [0.999999, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
        )
        await invoke(
            records,
            collector,
            "catch six for queued food effect",
            plugin.handle_catch(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-effect-catch-six",
                    )
                ),
            ),
        )
        effect_source_selector, effect_source_rarity = await active_selector(
            plugin,
            table="pig_instances",
            player_id=authorized_player,
        )
        if effect_source_rarity != 6:
            raise AssertionError("Queued six-star food effect has no compatible source.")
        plugin.economy_service.random_source = FixedRandom([0.999999, 0.0, 0.5])
        await invoke(
            records,
            collector,
            "consume queued six-star food effect",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-effect-cook-six",
                    ),
                    selector=effect_source_selector,
                ),
            ),
        )
        consumed_effect = await plugin.database.fetch_one(
            """
            SELECT consumed_uses
            FROM player_food_effects
            WHERE player_id = ? AND effect_id = 'next-six-star-cook'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (authorized_player,),
        )
        if consumed_effect is None or int(consumed_effect["consumed_uses"]) != 1:
            raise AssertionError("Queued six-star food effect was not consumed once.")

        await seed_coins(
            plugin,
            player_id=authorized_player,
            scope_id=f"qq:{args.authorized_group}",
            amount=2000,
        )
        await invoke(
            records,
            collector,
            "store",
            plugin.handle_store(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(query, arguments=""),
            ),
        )
        purchase_message = message(
            group_id=args.authorized_group,
            user_id=authorized_user,
            message_id="round4-purchase",
        )
        await invoke(
            records,
            collector,
            "purchase",
            plugin.handle_purchase(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(purchase_message, arguments="幸运猪哨 2"),
            ),
        )
        await invoke(
            records,
            collector,
            "duplicate purchase suppression",
            plugin.handle_purchase(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(purchase_message, arguments="幸运猪哨 2"),
            ),
            expected_new_deliveries=0,
        )
        await invoke(
            records,
            collector,
            "upgrade feed",
            plugin.handle_upgrade(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-upgrade-feed",
                    ),
                    arguments="猪饲料",
                ),
            ),
        )
        await invoke(
            records,
            collector,
            "ledger",
            plugin.handle_ledger(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(query, arguments="1"),
            ),
        )
        await invoke(
            records,
            collector,
            "profile",
            plugin.handle_profile(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(query),
            ),
        )

        plugin.gameplay_service.random_source = FixedRandom(
            [0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
        )
        await invoke(
            records,
            collector,
            "catch pig for sale",
            plugin.handle_catch(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-catch-sale",
                    )
                ),
            ),
        )
        sale_pig_selector, _ = await active_selector(
            plugin,
            table="pig_instances",
            player_id=authorized_player,
        )
        await invoke(
            records,
            collector,
            "sell pig",
            plugin.handle_sell_pig(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-sell-pig",
                    ),
                    selector=sale_pig_selector,
                ),
            ),
        )

        plugin.gameplay_service.random_source = FixedRandom(
            [0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
        )
        await invoke(
            records,
            collector,
            "catch pig for food sale",
            plugin.handle_catch(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-catch-food-sale",
                    )
                ),
            ),
        )
        sale_source_selector, _ = await active_selector(
            plugin,
            table="pig_instances",
            player_id=authorized_player,
        )
        plugin.economy_service.random_source = FixedRandom([0.0, 0.0, 0.5])
        await invoke(
            records,
            collector,
            "cook food for sale",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-cook-food-sale",
                    ),
                    selector=sale_source_selector,
                ),
            ),
        )
        sale_food_selector, _ = await active_selector(
            plugin,
            table="food_instances",
            player_id=authorized_player,
        )
        await invoke(
            records,
            collector,
            "sell food",
            plugin.handle_sell_food(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-sell-food",
                    ),
                    selector=sale_food_selector,
                ),
            ),
        )

        async def catch_low(label: str, message_id: str) -> None:
            plugin.gameplay_service.random_source = FixedRandom(
                [0.0, 0.0, 0.25, 0.25, 0.25, 0.25, 0.25]
            )
            await invoke(
                records,
                collector,
                label,
                plugin.handle_catch(
                    stream_id=f"uat-round4-{args.authorized_group}",
                    **command_kwargs(
                        message(
                            group_id=args.authorized_group,
                            user_id=authorized_user,
                            message_id=message_id,
                        )
                    ),
                ),
            )

        await catch_low("catch low for auto cook", "round4-auto-cook-source")
        await catch_low("catch low for auto sale", "round4-auto-sale-source")
        plugin.economy_service.random_source = FixedRandom([0.0, 0.0, 0.5])
        await invoke(
            records,
            collector,
            "auto cook cheapest low pig",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-auto-cook",
                    )
                ),
            ),
        )
        await invoke(
            records,
            collector,
            "auto sell cheapest low food",
            plugin.handle_sell_food(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-auto-sell-food",
                    )
                ),
            ),
        )
        await invoke(
            records,
            collector,
            "auto sell cheapest low pig",
            plugin.handle_sell_pig(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-auto-sell-pig",
                    )
                ),
            ),
        )

        await catch_low("catch low pig batch one", "round4-batch-pig-1")
        await catch_low("catch low pig batch two", "round4-batch-pig-2")
        await invoke(
            records,
            collector,
            "batch sell low pigs",
            plugin.handle_batch_sell(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-batch-sell-pigs",
                    ),
                    arguments="猪猪",
                ),
            ),
        )

        await catch_low("catch low food source one", "round4-batch-food-source-1")
        plugin.economy_service.random_source = FixedRandom([0.0, 0.0, 0.5])
        await invoke(
            records,
            collector,
            "auto cook low food one",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-batch-cook-food-1",
                    )
                ),
            ),
        )
        await catch_low("catch low food source two", "round4-batch-food-source-2")
        plugin.economy_service.random_source = FixedRandom([0.0, 0.0, 0.5])
        await invoke(
            records,
            collector,
            "auto cook low food two",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-batch-cook-food-2",
                    )
                ),
            ),
        )
        await invoke(
            records,
            collector,
            "auto eat cheapest low food",
            plugin.handle_eat(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-auto-eat-food",
                    )
                ),
            ),
        )
        await invoke(
            records,
            collector,
            "batch sell low foods",
            plugin.handle_batch_sell(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(
                    message(
                        group_id=args.authorized_group,
                        user_id=authorized_user,
                        message_id="round4-batch-sell-foods",
                    ),
                    arguments="美食",
                ),
            ),
        )

        plugin.gameplay_service.random_source = FixedRandom(
            [0.999999, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
        )
        await invoke(
            records,
            collector,
            "ordinary catch highest boundary",
            plugin.handle_catch(
                stream_id=f"uat-round4-{args.ordinary_group}",
                **command_kwargs(
                    message(
                        group_id=args.ordinary_group,
                        user_id=ordinary_user,
                        message_id="round4-ordinary-catch",
                    )
                ),
            ),
        )
        ordinary_pig_selector, ordinary_pig_rarity = await active_selector(
            plugin,
            table="pig_instances",
            player_id=ordinary_player,
        )
        if ordinary_pig_rarity != 5:
            raise AssertionError(
                f"Ordinary highest-boundary pig was {ordinary_pig_rarity} stars."
            )
        plugin.economy_service.random_source = FixedRandom([0.999999, 0.0, 0.5])
        await invoke(
            records,
            collector,
            "ordinary cook highest boundary",
            plugin.handle_cook(
                stream_id=f"uat-round4-{args.ordinary_group}",
                **command_kwargs(
                    message(
                        group_id=args.ordinary_group,
                        user_id=ordinary_user,
                        message_id="round4-ordinary-cook",
                    ),
                    selector=ordinary_pig_selector,
                ),
            ),
        )
        _, ordinary_food_rarity = await active_selector(
            plugin,
            table="food_instances",
            player_id=ordinary_player,
        )
        if ordinary_food_rarity != 5:
            raise AssertionError(
                f"Ordinary highest-boundary food was {ordinary_food_rarity} stars."
            )

        database = plugin.database
        if database is None:
            raise AssertionError("Plugin database disappeared during UAT.")
        reconciliation = await database.fetch_one(
            """
            SELECT
                player.coin_balance,
                COALESCE(SUM(ledger.amount), 0) AS ledger_total
            FROM players AS player
            LEFT JOIN currency_ledger AS ledger
              ON ledger.player_id = player.player_id
            WHERE player.player_id = ?
            GROUP BY player.player_id
            """,
            (authorized_player,),
        )
        if reconciliation is None or int(reconciliation["coin_balance"]) != int(
            reconciliation["ledger_total"]
        ):
            raise AssertionError("Authorized UAT player failed coin reconciliation.")
        await plugin.on_unload()

        restart_collector = DeliveryCollector(output_root / "restart-deliveries")
        restart_collector.output_dir.mkdir()
        restarted = create_plugin()
        configure_plugin(restarted)
        restarted._set_context(
            LocalContext(
                data_dir=cloned_data,
                browser=browser,
                collector=restart_collector,
            )
        )
        await restarted.on_load()
        await invoke(
            records,
            restart_collector,
            "restart duplicate cook suppression",
            restarted.handle_cook(
                stream_id=f"uat-round4-{args.authorized_group}",
                **command_kwargs(cook_message, selector=pig_selector),
            ),
            expected_new_deliveries=0,
        )
        await restarted.on_unload()
        await browser.close()

    database_path = cloned_data / args.database_filename
    connection = sqlite3.connect(database_path)
    try:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "pig_instances",
                "food_instances",
                "currency_ledger",
                "command_receipts",
            )
        }
    finally:
        connection.close()
    if collector.texts:
        raise AssertionError(f"UAT unexpectedly used text fallback: {collector.texts}")
    report = {
        "status": "passed",
        "source_data": str(source_data),
        "cloned_data": str(cloned_data),
        "schema_version": user_version,
        "uat_frequency_override": {
            "daily_limit": 100,
            "cooldown_seconds": 0,
            "reason": "连续覆盖多次抓取、做菜和售卖；正式默认值由独立测试验证",
        },
        "commands": records,
        "deliveries": collector.images,
        "text_fallbacks": collector.texts,
        "authorized_pig_rarity": pig_rarity,
        "authorized_food_rarity": food_rarity,
        "ordinary_pig_rarity": ordinary_pig_rarity,
        "ordinary_food_rarity": ordinary_food_rarity,
        "restart_deliveries": restart_collector.images,
        "counts": counts,
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> int:
    report = asyncio.run(run(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
