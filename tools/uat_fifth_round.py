"""Run isolated fifth-round command UAT against the formal catalog."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.domain.social import RANKING_TYPES  # noqa: E402
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
    parser.add_argument("--primary-group", default="1092931381")
    parser.add_argument("--isolated-group", default="1092931382")
    return parser.parse_args()


def configure_plugin(plugin: Any) -> None:
    config = plugin.get_default_config()
    config["maintenance"]["enabled"] = False
    config["catching"]["cooldown_seconds"] = 0
    config["catching"]["daily_limit"] = 100
    config["features"]["showcase_enabled"] = True
    config["features"]["ranking_enabled"] = True
    config["trading"]["gift_enabled"] = True
    config["trading"]["trade_enabled"] = True
    plugin.set_plugin_config(config)


def message(
    *,
    group_id: str,
    user_id: str,
    display_name: str,
    message_id: str,
    target_user_id: str = "",
    target_display_name: str = "",
) -> dict[str, object]:
    payload = build_message(
        group_id=group_id,
        group_name=f"第五轮UAT群{group_id}",
        user_id=user_id,
        display_name=display_name,
        stream_id=f"uat-round5-{group_id}",
        message_id=message_id,
    )
    if target_user_id:
        payload["raw_message"] = [
            {
                "type": "at",
                "data": {
                    "target_user_id": target_user_id,
                    "target_user_cardname": target_display_name,
                },
            }
        ]
    return payload


async def active_selectors(
    plugin: Any,
    *,
    player_id: str,
) -> list[str]:
    rows = await plugin.database.fetch_all(
        """
        SELECT display_name_snapshot, short_code
        FROM pig_instances
        WHERE owner_player_id = ? AND state = 'active'
        ORDER BY acquired_at, pig_instance_id
        """,
        (player_id,),
    )
    return [
        f"{row['display_name_snapshot']}#{row['short_code']}"
        for row in rows
    ]


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
        raise AssertionError("无法给尚未建立的第五轮 UAT 玩家入账。")
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
                'round5-uat-seed', ?, ?, ?, ?,
                'uat-seed', '第五轮隔离验收入账', 'uat', 'seed',
                'round5-uat-seed', '2026-07-28T00:00:00.000Z'
            )
            """,
            (player_id, scope_id, amount, balance_after),
        )


async def invoke_expected_error(
    records: list[dict[str, object]],
    collector: DeliveryCollector,
    label: str,
    awaitable: Any,
) -> tuple[bool, str, int]:
    before = len(collector.images) + len(collector.texts)
    result = await awaitable
    delivered = len(collector.images) + len(collector.texts) - before
    if result[0] is not False or delivered != 1:
        raise AssertionError(
            f"{label} should fail once with one text reply: {result}, "
            f"deliveries={delivered}"
        )
    records.append(
        {
            "command": label,
            "result": list(result),
            "new_deliveries": delivered,
            "expected_error": True,
        }
    )
    return result


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
    seller_id = "round5-seller"
    buyer_id = "round5-buyer"
    outsider_id = "round5-outsider"
    seller_name = "第五轮卖家"
    buyer_name = "第五轮买家"
    seller_player = f"qq:{args.primary_group}:{seller_id}"
    buyer_player = f"qq:{args.primary_group}:{buyer_id}"
    outsider_player = f"qq:{args.isolated_group}:{outsider_id}"
    primary_stream = f"uat-round5-{args.primary_group}"
    isolated_stream = f"uat-round5-{args.isolated_group}"

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
        if plugin.social_service is None or plugin.gameplay_service is None:
            raise AssertionError("第五轮服务未加载。")
        components = plugin.get_components()
        if len(components) != 27:
            raise AssertionError(f"第五轮组件数应为 27，实际为 {len(components)}。")
        if {component["type"] for component in components} != {"COMMAND"}:
            raise AssertionError("第五轮插件出现了非显式命令组件。")

        plugin.gameplay_service.random_source = FixedRandom(
            [
                value
                for _ in range(8)
                for value in (0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5)
            ]
        )
        for index in range(3):
            await invoke(
                records,
                collector,
                f"卖家抓猪 {index + 1}",
                plugin.handle_catch(
                    stream_id=primary_stream,
                    **command_kwargs(
                        message(
                            group_id=args.primary_group,
                            user_id=seller_id,
                            display_name=seller_name,
                            message_id=f"round5-seller-catch-{index + 1}",
                        )
                    ),
                ),
            )
        await invoke(
            records,
            collector,
            "买家抓猪建立身份",
            plugin.handle_catch(
                stream_id=primary_stream,
                **command_kwargs(
                    message(
                        group_id=args.primary_group,
                        user_id=buyer_id,
                        display_name=buyer_name,
                        message_id="round5-buyer-catch",
                    )
                ),
            ),
        )
        await invoke(
            records,
            collector,
            "隔离群抓猪",
            plugin.handle_catch(
                stream_id=isolated_stream,
                **command_kwargs(
                    message(
                        group_id=args.isolated_group,
                        user_id=outsider_id,
                        display_name="隔离群成员",
                        message_id="round5-outsider-catch",
                    )
                ),
            ),
        )
        selectors = await active_selectors(plugin, player_id=seller_player)
        if len(selectors) != 3:
            raise AssertionError(f"卖家应有 3 只可用猪猪，实际为 {selectors}。")
        await seed_coins(
            plugin,
            player_id=buyer_player,
            scope_id=f"qq:{args.primary_group}",
            amount=5000,
        )

        gift_message = message(
            group_id=args.primary_group,
            user_id=seller_id,
            display_name=seller_name,
            message_id="round5-gift",
            target_user_id=buyer_id,
            target_display_name=buyer_name,
        )
        gift_kwargs = command_kwargs(
            gift_message,
            kind="猪猪",
            arguments=f"{selectors[0]} @{buyer_name}",
        )
        await invoke(
            records,
            collector,
            "结构化@赠送",
            plugin.handle_gift(stream_id=primary_stream, **gift_kwargs),
        )
        await invoke(
            records,
            collector,
            "赠送重复投递抑制",
            plugin.handle_gift(stream_id=primary_stream, **gift_kwargs),
            expected_new_deliveries=0,
        )

        offer_message = message(
            group_id=args.primary_group,
            user_id=seller_id,
            display_name=seller_name,
            message_id="round5-trade-offer",
            target_user_id=buyer_id,
            target_display_name=buyer_name,
        )
        await invoke(
            records,
            collector,
            "创建双方确认交易",
            plugin.handle_trade_offer(
                stream_id=primary_stream,
                **command_kwargs(
                    offer_message,
                    kind="猪猪",
                    arguments=f"{selectors[1]} @{buyer_name} 300",
                ),
            ),
        )
        trade_row = await plugin.database.fetch_one(
            """
            SELECT trade_id
            FROM trade_offers
            WHERE sender_player_id = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (seller_player,),
        )
        if trade_row is None:
            raise AssertionError("创建交易后未找到待处理报价。")
        accepted_trade_id = str(trade_row["trade_id"])
        accept_message = message(
            group_id=args.primary_group,
            user_id=buyer_id,
            display_name=buyer_name,
            message_id="round5-trade-accept",
        )
        accept_kwargs = command_kwargs(
            accept_message,
            arguments=accepted_trade_id,
        )
        await invoke(
            records,
            collector,
            "接收方确认交易",
            plugin.handle_trade_accept(
                stream_id=primary_stream,
                **accept_kwargs,
            ),
        )
        await invoke(
            records,
            collector,
            "接受交易重复投递抑制",
            plugin.handle_trade_accept(
                stream_id=primary_stream,
                **accept_kwargs,
            ),
            expected_new_deliveries=0,
        )

        await plugin.on_unload()
        plugin = create_plugin()
        configure_plugin(plugin)
        plugin._set_context(context)
        await plugin.on_load()
        await invoke(
            records,
            collector,
            "重启后赠送重复公示保护",
            plugin.handle_gift(stream_id=primary_stream, **gift_kwargs),
            expected_new_deliveries=0,
        )
        await invoke(
            records,
            collector,
            "重启后交易重复公示保护",
            plugin.handle_trade_accept(
                stream_id=primary_stream,
                **accept_kwargs,
            ),
            expected_new_deliveries=0,
        )

        expensive_message = message(
            group_id=args.primary_group,
            user_id=seller_id,
            display_name=seller_name,
            message_id="round5-expensive-offer",
            target_user_id=buyer_id,
            target_display_name=buyer_name,
        )
        await invoke(
            records,
            collector,
            "创建余额不足报价",
            plugin.handle_trade_offer(
                stream_id=primary_stream,
                **command_kwargs(
                    expensive_message,
                    kind="猪猪",
                    arguments=f"{selectors[2]} @{buyer_name} 999999",
                ),
            ),
        )
        expensive_row = await plugin.database.fetch_one(
            """
            SELECT trade_id
            FROM trade_offers
            WHERE sender_player_id = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (seller_player,),
        )
        if expensive_row is None:
            raise AssertionError("余额不足场景未建立待处理报价。")
        expensive_trade_id = str(expensive_row["trade_id"])
        await invoke_expected_error(
            records,
            collector,
            "余额不足保持待处理",
            plugin.handle_trade_accept(
                stream_id=primary_stream,
                **command_kwargs(
                    message(
                        group_id=args.primary_group,
                        user_id=buyer_id,
                        display_name=buyer_name,
                        message_id="round5-expensive-accept",
                    ),
                    arguments=expensive_trade_id,
                ),
            ),
        )
        pending_row = await plugin.database.fetch_one(
            "SELECT status FROM trade_offers WHERE trade_id = ?",
            (expensive_trade_id,),
        )
        if pending_row is None or pending_row["status"] != "pending":
            raise AssertionError("余额不足后报价没有保持待处理。")
        async with plugin.database.transaction() as session:
            await session.execute(
                """
                UPDATE trade_offers
                SET expires_at = '2020-01-01T00:00:00.000Z'
                WHERE trade_id = ?
                """,
                (expensive_trade_id,),
            )
        expired_count = await plugin.social_service.expire_stale_offers()
        if expired_count != 1:
            raise AssertionError(f"应过期 1 笔报价，实际为 {expired_count}。")

        await invoke(
            records,
            collector,
            "设置猪猪展示位",
            plugin.handle_showcase(
                stream_id=primary_stream,
                **command_kwargs(
                    message(
                        group_id=args.primary_group,
                        user_id=seller_id,
                        display_name=seller_name,
                        message_id="round5-showcase",
                    ),
                    arguments=f"猪猪 {selectors[2]}",
                ),
            ),
        )
        await invoke(
            records,
            collector,
            "查看全部交易",
            plugin.handle_trade_list(
                stream_id=primary_stream,
                **command_kwargs(
                    message(
                        group_id=args.primary_group,
                        user_id=seller_id,
                        display_name=seller_name,
                        message_id="round5-trade-list",
                    ),
                    arguments="全部 1",
                ),
            ),
        )
        for ranking_type in RANKING_TYPES:
            await invoke(
                records,
                collector,
                f"{ranking_type}排行榜",
                plugin.handle_ranking(
                    stream_id=primary_stream,
                    **command_kwargs(
                        message(
                            group_id=args.primary_group,
                            user_id=seller_id,
                            display_name=seller_name,
                            message_id=f"round5-rank-{ranking_type}",
                        ),
                        arguments=f"{ranking_type} 1",
                    ),
                ),
            )
        await invoke(
            records,
            collector,
            "隔离群综合排行",
            plugin.handle_ranking(
                stream_id=isolated_stream,
                **command_kwargs(
                    message(
                        group_id=args.isolated_group,
                        user_id=outsider_id,
                        display_name="隔离群成员",
                        message_id="round5-isolated-rank",
                    ),
                    arguments="综合 1",
                ),
            ),
        )

        trade_ledger = await plugin.database.fetch_one(
            """
            SELECT COUNT(*) AS entry_count, COALESCE(SUM(amount), 0) AS total
            FROM currency_ledger
            WHERE source_object_type = 'trade'
              AND source_object_id = ?
            """,
            (accepted_trade_id,),
        )
        accepted = await plugin.database.fetch_one(
            """
            SELECT status, price, sender_player_id, recipient_player_id
            FROM trade_offers
            WHERE trade_id = ?
            """,
            (accepted_trade_id,),
        )
        expired = await plugin.database.fetch_one(
            "SELECT status FROM trade_offers WHERE trade_id = ?",
            (expensive_trade_id,),
        )
        transfer_counts = await plugin.database.fetch_one(
            """
            SELECT
                SUM(CASE WHEN transfer_type = 'gift' THEN 1 ELSE 0 END) AS gifts,
                SUM(CASE WHEN transfer_type = 'trade' THEN 1 ELSE 0 END) AS trades
            FROM asset_transfer_events
            """
        )
        scope_counts = await plugin.database.fetch_all(
            """
            SELECT scope_id, COUNT(*) AS player_count
            FROM players
            GROUP BY scope_id
            ORDER BY scope_id
            """
        )
        seller_stats = await plugin.database.fetch_one(
            """
            SELECT total_catches, gifts_sent, trades_completed
            FROM player_statistics
            WHERE player_id = ?
            """,
            (seller_player,),
        )
        buyer_stats = await plugin.database.fetch_one(
            """
            SELECT total_catches, gifts_received, trades_completed
            FROM player_statistics
            WHERE player_id = ?
            """,
            (buyer_player,),
        )
        isolated_stats = await plugin.database.fetch_one(
            """
            SELECT total_catches
            FROM player_statistics
            WHERE player_id = ?
            """,
            (outsider_player,),
        )
        integrity = await plugin.database.fetch_one("PRAGMA quick_check")
        await plugin.on_unload()
        await browser.close()

    if trade_ledger is None or tuple(trade_ledger) != (2, 0):
        raise AssertionError(f"成交账本不是两条零和分录：{trade_ledger}")
    if accepted is None or accepted["status"] != "accepted":
        raise AssertionError(f"确认交易未完成：{accepted}")
    if expired is None or expired["status"] != "expired":
        raise AssertionError(f"过期报价未落为 expired：{expired}")
    if transfer_counts is None or tuple(transfer_counts) != (1, 1):
        raise AssertionError(f"赠送或交易转移事件数异常：{transfer_counts}")
    if integrity is None or str(integrity[0]).lower() != "ok":
        raise AssertionError(f"SQLite quick_check 失败：{integrity}")
    if not collector.images:
        raise AssertionError("命令 UAT 未产生图片。")

    report = {
        "status": "passed",
        "plugin_version": "0.5.0",
        "schema_version": 5,
        "component_count": 27,
        "commands": records,
        "command_count": len(records),
        "deliveries": {
            "images": len(collector.images),
            "texts": len(collector.texts),
            "image_files": collector.images,
            "text_replies": collector.texts,
        },
        "trade": {
            "accepted_trade_id": accepted_trade_id,
            "accepted_status": accepted["status"],
            "accepted_price": int(accepted["price"]),
            "ledger_entries": int(trade_ledger["entry_count"]),
            "ledger_zero_sum": int(trade_ledger["total"]) == 0,
            "expired_trade_id": expensive_trade_id,
            "expired_status": expired["status"],
        },
        "transfers": {
            "gift_events": int(transfer_counts["gifts"]),
            "trade_events": int(transfer_counts["trades"]),
        },
        "stable_statistics": {
            "seller": dict(seller_stats) if seller_stats is not None else {},
            "buyer": dict(buyer_stats) if buyer_stats is not None else {},
            "isolated": dict(isolated_stats) if isolated_stats is not None else {},
        },
        "group_isolation": [dict(row) for row in scope_counts],
        "ranking_types": list(RANKING_TYPES),
        "restart_duplicate_delivery_count": 0,
        "sqlite_quick_check": str(integrity[0]),
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
