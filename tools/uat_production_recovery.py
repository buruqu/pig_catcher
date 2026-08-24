"""Run production-readiness and failure-recovery UAT on an isolated formal-data clone."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.version import FRAMEWORK_PHASE, PLUGIN_VERSION, SCHEMA_VERSION  # noqa: E402
from tools.uat_catching_and_collection import (  # noqa: E402
    DeliveryCollector,
    FixedRandom,
    LocalContext,
    build_message,
    clone_formal_data,
    command_kwargs,
    create_plugin,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--primary-group", default="9000001001")
    parser.add_argument("--isolated-group", default="9000001002")
    return parser.parse_args()


class FaultInjectingCollector(DeliveryCollector):
    """Record successful deliveries and fail selected image sends."""

    def __init__(self, output_dir: Path) -> None:
        super().__init__(output_dir)
        self.fail_next_image = False
        self.failed_image_attempts = 0

    async def image(self, image_base64: str, stream_id: str) -> bool:
        if self.fail_next_image:
            self.fail_next_image = False
            self.failed_image_attempts += 1
            raise RuntimeError("injected QQ image-send failure")
        return await super().image(image_base64, stream_id)


def configure_plugin(plugin: Any) -> None:
    config = plugin.get_default_config()
    config["maintenance"]["enabled"] = False
    config["storage"]["sqlite_busy_timeout_ms"] = 100
    config["catching"]["cooldown_seconds"] = 0
    config["catching"]["daily_limit"] = 100
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
        group_name=f"第六轮隔离验收群{group_id}",
        user_id=user_id,
        display_name=display_name,
        stream_id=f"uat-round6-{group_id}",
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


async def load_plugin(
    *,
    data_dir: Path,
    browser: Any,
    collector: FaultInjectingCollector,
) -> Any:
    plugin = create_plugin()
    configure_plugin(plugin)
    plugin._set_context(
        LocalContext(data_dir=data_dir, browser=browser, collector=collector)
    )
    await plugin.on_load()
    components = plugin.get_components()
    command_count = sum(item["type"] == "COMMAND" for item in components)
    home_card_count = sum(item["type"] == "HOME_CARD" for item in components)
    if len(components) != 50 or command_count != 49 or home_card_count != 1:
        raise AssertionError("正式版必须注册 49 个显式命令和 1 个运营首页卡片。")
    if plugin.gameplay_service is None:
        raise AssertionError("正式版抓猪服务未加载。")
    plugin.gameplay_service.random_source = FixedRandom(
        [value for _ in range(40) for value in (0.01, 0.15, 0.32, 0.58, 0.81)]
    )
    return plugin


def delivery_count(collector: FaultInjectingCollector) -> int:
    return len(collector.images) + len(collector.texts)


async def expect_success(
    records: list[dict[str, object]],
    collector: FaultInjectingCollector,
    label: str,
    awaitable: Any,
    *,
    deliveries: int = 1,
) -> tuple[bool, str, int]:
    before = delivery_count(collector)
    result = await awaitable
    delta = delivery_count(collector) - before
    if result[0] is not True or delta != deliveries:
        raise AssertionError(
            f"{label} failed: result={result}, deliveries={delta}, expected={deliveries}"
        )
    records.append(
        {
            "step": label,
            "success": True,
            "deliveries": delta,
            "level": result[2],
        }
    )
    return result


async def expect_failure(
    records: list[dict[str, object]],
    collector: FaultInjectingCollector,
    label: str,
    awaitable: Any,
) -> tuple[bool, str, int]:
    before = delivery_count(collector)
    result = await awaitable
    delta = delivery_count(collector) - before
    if result[0] is not False or delta != 1:
        raise AssertionError(f"{label} should fail once: result={result}, deliveries={delta}")
    records.append(
        {
            "step": label,
            "success": False,
            "deliveries": delta,
            "level": result[2],
            "expected_failure": True,
        }
    )
    return result


async def latest_active_pig(plugin: Any, player_id: str) -> dict[str, object]:
    row = await plugin.database.fetch_one(
        """
        SELECT
            instance.pig_instance_id,
            instance.display_name_snapshot,
            instance.short_code,
            template.image_relpath,
            instance.state
        FROM pig_instances AS instance
        JOIN pig_templates AS template
          ON template.template_id = instance.template_id
        WHERE instance.owner_player_id = ? AND instance.state = 'active'
        ORDER BY instance.acquired_at DESC, instance.pig_instance_id DESC
        LIMIT 1
        """,
        (player_id,),
    )
    if row is None:
        raise AssertionError(f"玩家没有可用猪猪：{player_id}")
    return dict(row)


def selector(row: dict[str, object]) -> str:
    return f"{row['display_name_snapshot']}#{row['short_code']}"


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
        raise AssertionError("无法给尚未建立的第六轮 UAT 玩家入账。")
    balance_after = int(row["coin_balance"]) + amount
    async with plugin.database.transaction() as session:
        await session.execute(
            """
            UPDATE players
            SET coin_balance = ?, updated_at = '2026-07-28T11:00:00.000Z'
            WHERE player_id = ?
            """,
            (balance_after, player_id),
        )
        await session.execute(
            """
            INSERT INTO currency_ledger(
                ledger_entry_id, player_id, scope_id, amount, balance_after,
                reason_code, reason_text, source_object_type, source_object_id,
                idempotency_key, created_at
            )
            VALUES(
                'round6-uat-seed', ?, ?, ?, ?,
                'uat-seed', '第六轮隔离验收入账', 'uat', 'seed',
                'round6-uat-seed', '2026-07-28T11:00:00.000Z'
            )
            """,
            (player_id, scope_id, amount, balance_after),
        )


async def database_counts(plugin: Any) -> dict[str, int]:
    row = await plugin.database.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM players) AS players,
            (SELECT COUNT(*) FROM pig_instances) AS pigs,
            (SELECT COUNT(*) FROM food_instances) AS foods,
            (SELECT COUNT(*) FROM command_receipts) AS receipts,
            (SELECT COUNT(*) FROM trade_offers) AS trades,
            (SELECT COUNT(*) FROM currency_ledger) AS ledger_entries
        """
    )
    if row is None:
        raise AssertionError("无法读取第六轮数据库计数。")
    return {key: int(row[key]) for key in row.keys()}


async def ledger_mismatch_count(plugin: Any) -> int:
    row = await plugin.database.fetch_one(
        """
        SELECT COUNT(*) AS mismatch_count
        FROM players AS player
        LEFT JOIN (
            SELECT player_id, COALESCE(SUM(amount), 0) AS ledger_total
            FROM currency_ledger
            GROUP BY player_id
        ) AS ledger ON ledger.player_id = player.player_id
        WHERE player.coin_balance <> COALESCE(ledger.ledger_total, 0)
        """
    )
    return int(row["mismatch_count"]) if row is not None else -1


async def pig_counts_by_scope(plugin: Any) -> dict[str, int]:
    rows = await plugin.database.fetch_all(
        """
        SELECT scope_id, COUNT(*) AS pig_count
        FROM pig_instances
        GROUP BY scope_id
        ORDER BY scope_id
        """
    )
    return {str(row["scope_id"]): int(row["pig_count"]) for row in rows}


async def run(args: argparse.Namespace) -> dict[str, object]:
    source_data = args.data_dir.resolve()
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    cloned_data = output_root / "data"
    deliveries_dir = output_root / "deliveries"
    deliveries_dir.mkdir()
    clone_formal_data(
        source_data_dir=source_data,
        target_data_dir=cloned_data,
        database_filename=args.database_filename,
    )

    collector = FaultInjectingCollector(deliveries_dir)
    records: list[dict[str, object]] = []
    primary_group = str(args.primary_group)
    isolated_group = str(args.isolated_group)
    seller_id = "round6-seller"
    buyer_id = "round6-buyer"
    seller_player = f"qq:{primary_group}:{seller_id}"
    buyer_player = f"qq:{primary_group}:{buyer_id}"
    seller_message = message(
        group_id=primary_group,
        user_id=seller_id,
        display_name="第六轮卖家",
        message_id="round6-catch-seller",
    )
    buyer_message = message(
        group_id=primary_group,
        user_id=buyer_id,
        display_name="第六轮买家",
        message_id="round6-catch-buyer",
    )

    launch_options: dict[str, object] = {"headless": True}
    if args.browser_executable is not None:
        launch_options["executable_path"] = str(args.browser_executable.resolve())

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_options)
        plugin = await load_plugin(
            data_dir=cloned_data,
            browser=browser,
            collector=collector,
        )
        initial_group_pigs = await pig_counts_by_scope(plugin)

        await expect_success(
            records,
            collector,
            "正常抓猪并发图",
            plugin.handle_catch(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(seller_message),
            ),
        )

        collector.fail_next_image = True
        await expect_success(
            records,
            collector,
            "QQ图片失败后文字降级",
            plugin.handle_catch(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(buyer_message),
            ),
        )
        if collector.failed_image_attempts != 1 or len(collector.texts) != 1:
            raise AssertionError("图片发送故障没有恰好降级为一条文字。")

        await expect_success(
            records,
            collector,
            "图片故障消息同进程幂等",
            plugin.handle_catch(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(buyer_message),
            ),
            deliveries=0,
        )

        seller_pig = await latest_active_pig(plugin, seller_player)
        missing_path = (cloned_data / str(seller_pig["image_relpath"])).resolve()
        if not missing_path.is_relative_to(cloned_data) or not missing_path.is_file():
            raise AssertionError("素材缺失注入目标不合法。")
        missing_bytes = missing_path.read_bytes()
        missing_path.unlink()
        maintenance_report = await plugin._maintenance.run_once()
        if maintenance_report.missing_asset_file_count != 1:
            raise AssertionError("维护巡检没有识别到恰好一个缺失素材。")
        await expect_success(
            records,
            collector,
            "素材缺失占位图",
            plugin.handle_pig_detail(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(
                    message(
                        group_id=primary_group,
                        user_id=seller_id,
                        display_name="第六轮卖家",
                        message_id="round6-missing-detail",
                    ),
                    selector=selector(seller_pig),
                ),
            ),
        )
        missing_path.write_bytes(missing_bytes)
        restored_report = await plugin._maintenance.run_once()
        if restored_report.missing_asset_file_count != 0:
            raise AssertionError("素材恢复后巡检仍报告缺失。")

        before_busy = await database_counts(plugin)
        lock = sqlite3.connect(
            cloned_data / args.database_filename,
            isolation_level=None,
        )
        lock.execute("PRAGMA busy_timeout = 100")
        lock.execute("BEGIN IMMEDIATE")
        try:
            busy_result = await expect_failure(
                records,
                collector,
                "SQLite写锁安全失败",
                plugin.handle_catch(
                    stream_id=f"uat-round6-{primary_group}",
                    **command_kwargs(
                        message(
                            group_id=primary_group,
                            user_id=seller_id,
                            display_name="第六轮卖家",
                            message_id="round6-database-busy",
                        )
                    ),
                ),
            )
        finally:
            lock.rollback()
            lock.close()
        if "抓猪暂时不可用" not in busy_result[1]:
            raise AssertionError("数据库繁忙没有返回稳定的中文错误。")
        if await database_counts(plugin) != before_busy:
            raise AssertionError("数据库繁忙期间出现了部分写入。")

        await seed_coins(
            plugin,
            player_id=buyer_player,
            scope_id=f"qq:{primary_group}",
            amount=500,
        )
        offer_message = message(
            group_id=primary_group,
            user_id=seller_id,
            display_name="第六轮卖家",
            message_id="round6-offer",
            target_user_id=buyer_id,
            target_display_name="第六轮买家",
        )
        await expect_success(
            records,
            collector,
            "创建待确认交易",
            plugin.handle_trade_offer(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(
                    offer_message,
                    kind="猪猪",
                    arguments=f"{selector(seller_pig)} @第六轮买家 50",
                ),
            ),
        )
        offer = await plugin.database.fetch_one(
            """
            SELECT trade_id, asset_instance_id
            FROM trade_offers
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if offer is None:
            raise AssertionError("重启恢复验收没有建立待处理交易。")
        trade_id = str(offer["trade_id"])
        asset_id = str(offer["asset_instance_id"])
        await plugin.on_unload()

        plugin = await load_plugin(
            data_dir=cloned_data,
            browser=browser,
            collector=collector,
        )
        await expect_success(
            records,
            collector,
            "图片故障消息重启幂等",
            plugin.handle_catch(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(buyer_message),
            ),
            deliveries=0,
        )
        recovered = await plugin.database.fetch_one(
            """
            SELECT offer.status, pig.state, pig.locked_trade_id
            FROM trade_offers AS offer
            JOIN pig_instances AS pig
              ON pig.pig_instance_id = offer.asset_instance_id
            WHERE offer.trade_id = ?
            """,
            (trade_id,),
        )
        if recovered is None or tuple(recovered) != (
            "pending",
            "locked-for-trade",
            trade_id,
        ):
            raise AssertionError("插件重启后交易状态或资产锁没有恢复。")

        async with plugin.database.transaction() as session:
            await session.execute(
                """
                UPDATE trade_offers
                SET expires_at = '2000-01-01T00:00:00.000Z'
                WHERE trade_id = ?
                """,
                (trade_id,),
            )
        expiry_report = await plugin._maintenance.run_once()
        expired = await plugin.database.fetch_one(
            """
            SELECT offer.status, pig.state, pig.locked_trade_id
            FROM trade_offers AS offer
            JOIN pig_instances AS pig
              ON pig.pig_instance_id = offer.asset_instance_id
            WHERE offer.trade_id = ? AND pig.pig_instance_id = ?
            """,
            (trade_id, asset_id),
        )
        if (
            expiry_report.expired_trade_offers != 1
            or expired is None
            or tuple(expired) != ("expired", "active", None)
        ):
            raise AssertionError("过期报价没有在维护任务中解锁。")
        records.append(
            {
                "step": "重启后过期报价解锁",
                "success": True,
                "deliveries": 0,
            }
        )

        await expect_success(
            records,
            collector,
            "第二群独立抓猪",
            plugin.handle_catch(
                stream_id=f"uat-round6-{isolated_group}",
                **command_kwargs(
                    message(
                        group_id=isolated_group,
                        user_id=seller_id,
                        display_name="第六轮卖家",
                        message_id="round6-isolated-catch",
                    )
                ),
            ),
        )
        current_group_pigs = await pig_counts_by_scope(plugin)
        test_scopes = {f"qq:{primary_group}", f"qq:{isolated_group}"}
        group_pigs = {
            scope_id: current_group_pigs.get(scope_id, 0)
            - initial_group_pigs.get(scope_id, 0)
            for scope_id in sorted(test_scopes)
        }
        unchanged_existing = all(
            current_group_pigs.get(scope_id, 0) == count
            for scope_id, count in initial_group_pigs.items()
            if scope_id not in test_scopes
        )
        unexpected_scopes = (
            set(current_group_pigs) - set(initial_group_pigs) - test_scopes
        )
        if (
            group_pigs
            != {f"qq:{primary_group}": 2, f"qq:{isolated_group}": 1}
            or not unchanged_existing
            or unexpected_scopes
        ):
            raise AssertionError(
                "双群隔离新增量异常："
                f"test={group_pigs}, unchanged_existing={unchanged_existing}, "
                f"unexpected={sorted(unexpected_scopes)}"
            )

        await expect_success(
            records,
            collector,
            "主群综合排行",
            plugin.handle_ranking(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(
                    message(
                        group_id=primary_group,
                        user_id=seller_id,
                        display_name="第六轮卖家",
                        message_id="round6-primary-ranking",
                    ),
                    arguments="综合 1",
                ),
            ),
        )
        await expect_success(
            records,
            collector,
            "第二群综合排行",
            plugin.handle_ranking(
                stream_id=f"uat-round6-{isolated_group}",
                **command_kwargs(
                    message(
                        group_id=isolated_group,
                        user_id=seller_id,
                        display_name="第六轮卖家",
                        message_id="round6-isolated-ranking",
                    ),
                    arguments="综合 1",
                ),
            ),
        )

        if await ledger_mismatch_count(plugin) != 0:
            raise AssertionError("正式版隔离流程结束后账本对账不一致。")
        final_counts = await database_counts(plugin)
        recovery_backup = output_root / "recovery-backup.sqlite3"
        await plugin.database.backup_to(recovery_backup)
        await plugin.on_unload()

        restored_data = output_root / "restored-data"
        restored_data.mkdir()
        shutil.copy2(recovery_backup, restored_data / args.database_filename)
        shutil.copytree(cloned_data / "assets", restored_data / "assets")
        restored_plugin = await load_plugin(
            data_dir=restored_data,
            browser=browser,
            collector=collector,
        )
        restored_counts = await database_counts(restored_plugin)
        if restored_counts != final_counts:
            raise AssertionError(
                f"恢复库计数不一致：before={final_counts}, after={restored_counts}"
            )
        if await restored_plugin.database.integrity_check() != ("ok",):
            raise AssertionError("恢复库 quick_check 未通过。")
        if await restored_plugin.database.schema_version() != SCHEMA_VERSION:
            raise AssertionError("恢复库 Schema 版本不正确。")
        await expect_success(
            records,
            collector,
            "恢复库重新加载并查询档案",
            restored_plugin.handle_profile(
                stream_id=f"uat-round6-{primary_group}",
                **command_kwargs(
                    message(
                        group_id=primary_group,
                        user_id=seller_id,
                        display_name="第六轮卖家",
                        message_id="round6-restored-profile",
                    )
                ),
            ),
        )
        await restored_plugin.on_unload()
        await browser.close()

    report = {
        "status": "passed",
        "plugin_version": PLUGIN_VERSION,
        "framework_phase": FRAMEWORK_PHASE,
        "schema_version": SCHEMA_VERSION,
        "component_count": 49,
        "production_defaults": {
            "daily_limit": 22,
            "cooldown_seconds": 20,
        },
        "uat_overrides": {
            "daily_limit": 100,
            "cooldown_seconds": 0,
            "sqlite_busy_timeout_ms": 100,
        },
        "steps": records,
        "step_count": len(records),
        "successful_images": len(collector.images),
        "successful_texts": len(collector.texts),
        "injected_image_failures": collector.failed_image_attempts,
        "group_pig_counts": group_pigs,
        "ledger_mismatch_count": 0,
        "final_counts": final_counts,
        "restored_counts": restored_counts,
        "recovery_backup": str(recovery_backup),
        "recovery_quick_check": "ok",
        "manual_qq_uat": "deferred-by-user",
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    report = asyncio.run(run(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
