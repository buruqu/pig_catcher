"""Run an isolated, command-level acceptance test against formal pig assets."""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import logging
import shutil
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image, ImageSequence
from playwright.async_api import Browser, async_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

_PLUGIN_MODULE_NAME = "_pig_catcher_third_round_uat"
_PLUGIN_SPEC = importlib.util.spec_from_file_location(
    _PLUGIN_MODULE_NAME,
    PROJECT_ROOT / "plugin.py",
    submodule_search_locations=[str(PROJECT_ROOT)],
)
if _PLUGIN_SPEC is None or _PLUGIN_SPEC.loader is None:
    raise RuntimeError("Unable to create the pig catcher UAT plugin module.")
_PLUGIN_MODULE = importlib.util.module_from_spec(_PLUGIN_SPEC)
sys.modules[_PLUGIN_MODULE_NAME] = _PLUGIN_MODULE
_PLUGIN_SPEC.loader.exec_module(_PLUGIN_MODULE)
create_plugin = _PLUGIN_MODULE.create_plugin


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--authorized-group", default="1092931381")
    parser.add_argument("--ordinary-group", default="1092931382")
    return parser.parse_args()


def clone_formal_data(
    *,
    source_data_dir: Path,
    target_data_dir: Path,
    database_filename: str,
) -> None:
    """Create a transactionally consistent DB clone plus an asset tree copy."""

    target_data_dir.mkdir(parents=True, exist_ok=False)
    source_database = source_data_dir / database_filename
    target_database = target_data_dir / database_filename
    with (
        sqlite3.connect(source_database) as source,
        sqlite3.connect(target_database) as target,
    ):
        source.backup(target)
    source_assets = source_data_dir / "assets"
    if not source_assets.is_dir():
        raise RuntimeError(f"Formal asset directory is missing: {source_assets}")
    shutil.copytree(source_assets, target_data_dir / "assets")


class FixedRandom:
    """Deterministic random source used to reach boundary rarity buckets."""

    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)

    def random(self) -> float:
        return next(self.values, 0.5)


class ChromiumRender:
    """Minimal local implementation of MaiBot's html2png capability."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser

    async def html2png(self, html: str, **kwargs: object) -> object:
        viewport = dict(kwargs.get("viewport") or {})
        page = await self.browser.new_page(
            viewport={
                "width": int(viewport.get("width", 1200)),
                "height": int(viewport.get("height", 1600)),
            }
        )
        try:
            async def block_network(route: Any) -> None:
                if str(route.request.url).startswith(("http://", "https://")):
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", block_network)
            await page.set_content(html, wait_until="load")
            selector = str(kwargs.get("selector") or "body")
            locator = page.locator(selector)
            await locator.wait_for(state="visible")
            payload = await locator.screenshot(type="png", animations="disabled")
            with Image.open(BytesIO(payload)) as image:
                width, height = image.size
            return {
                "image_base64": base64.b64encode(payload).decode("ascii"),
                "mime": "image/png",
                "width": width,
                "height": height,
            }
        finally:
            await page.close()


class DeliveryCollector:
    """Persist every local send so the UAT remains auditable."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.texts: list[dict[str, str]] = []
        self.images: list[dict[str, object]] = []

    async def text(self, text: str, stream_id: str) -> bool:
        self.texts.append({"stream_id": stream_id, "text": text})
        return True

    async def image(self, image_base64: str, stream_id: str) -> bool:
        payload = base64.b64decode(image_base64)
        suffix = ".gif" if payload.startswith((b"GIF87a", b"GIF89a")) else ".png"
        path = self.output_dir / f"delivery-{len(self.images) + 1:02d}{suffix}"
        path.write_bytes(payload)
        frame_count = 1
        durations: list[int] = []
        loop: int | None = None
        with Image.open(BytesIO(payload)) as image:
            frame_count = int(getattr(image, "n_frames", 1))
            if suffix == ".gif":
                durations = [
                    int(frame.info.get("duration") or 0)
                    for frame in ImageSequence.Iterator(image)
                ]
                loop = int(image.info.get("loop", 0))
        self.images.append(
            {
                "stream_id": stream_id,
                "path": str(path),
                "format": suffix.removeprefix(".").upper(),
                "frame_count": frame_count,
                "durations_ms": durations,
                "loop": loop,
            }
        )
        return True


class LocalContext:
    def __init__(
        self,
        *,
        data_dir: Path,
        browser: Browser,
        collector: DeliveryCollector,
    ) -> None:
        self.paths = SimpleNamespace(data_dir=str(data_dir))
        self.render = ChromiumRender(browser)
        self.send = collector
        self.logger = logging.getLogger("pig_catcher.third_round_uat")


def build_message(
    *,
    group_id: str,
    group_name: str,
    user_id: str,
    display_name: str,
    stream_id: str,
    message_id: str,
) -> dict[str, object]:
    return {
        "platform": "qq",
        "session_id": stream_id,
        "message_id": message_id,
        "message_info": {
            "group_info": {"group_id": group_id, "group_name": group_name},
            "user_info": {
                "user_id": user_id,
                "user_nickname": display_name,
                "user_cardname": display_name,
            },
            "additional_config": {},
        },
    }


def command_kwargs(
    message: dict[str, object],
    **matched_groups: str | None,
) -> dict[str, object]:
    return {
        "matched_groups": matched_groups,
        "raw_message": "",
        "message": message,
    }


def configure_plugin(plugin: Any) -> None:
    config = plugin.get_default_config()
    config["maintenance"]["enabled"] = False
    config["catching"]["cooldown_seconds"] = 0
    config["catching"]["daily_limit"] = 100
    plugin.set_plugin_config(config)


async def invoke(
    records: list[dict[str, object]],
    collector: DeliveryCollector,
    label: str,
    awaitable: Any,
    *,
    expected_new_deliveries: int = 1,
) -> tuple[bool, str, int]:
    before = len(collector.images) + len(collector.texts)
    result = await awaitable
    after = len(collector.images) + len(collector.texts)
    delivered = after - before
    if result[0] is not True:
        raise AssertionError(f"{label} failed: {result}")
    if delivered != expected_new_deliveries:
        raise AssertionError(
            f"{label} delivered {delivered} messages; expected "
            f"{expected_new_deliveries}."
        )
    records.append(
        {
            "command": label,
            "result": list(result),
            "new_deliveries": delivered,
        }
    )
    return result


async def seed_item(plugin: Any, player_id: str) -> None:
    database = plugin.database
    if database is None:
        raise RuntimeError("Plugin database is unavailable.")
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO item_inventory(player_id, item_id, quantity, updated_at)
            VALUES (?, 'giant-corn', 2, '2026-07-28T00:00:00.000Z')
            ON CONFLICT(player_id, item_id) DO UPDATE SET
                quantity = excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (player_id,),
        )


async def load_plugin(
    *,
    data_dir: Path,
    browser: Browser,
    collector: DeliveryCollector,
) -> Any:
    plugin = create_plugin()
    plugin._set_context(
        LocalContext(data_dir=data_dir, browser=browser, collector=collector)
    )
    configure_plugin(plugin)
    await plugin.on_load()
    components = plugin.get_components()
    command_count = sum(
        component["type"] == "COMMAND"
        for component in components
    )
    if len(components) != 31 or command_count != 30:
        raise AssertionError(
            "MaiBot component registration is not exactly 30 commands and 1 home card."
        )
    return plugin


async def query_one(database: Any, statement: str, parameters: tuple[object, ...]) -> dict[str, object]:
    row = await database.fetch_one(statement, parameters)
    if row is None:
        raise AssertionError(f"Expected one row for query: {statement}")
    return dict(row)


async def run_uat(args: argparse.Namespace) -> dict[str, object]:
    source_data_dir = args.data_dir.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    clone_data_dir = output_dir / "data"
    clone_formal_data(
        source_data_dir=source_data_dir,
        target_data_dir=clone_data_dir,
        database_filename=args.database_filename,
    )
    collector = DeliveryCollector(output_dir)
    command_records: list[dict[str, object]] = []

    async with async_playwright() as playwright:
        launch_options: dict[str, object] = {"headless": True}
        if args.browser_executable is not None:
            launch_options["executable_path"] = str(args.browser_executable.resolve())
        browser = await playwright.chromium.launch(**launch_options)
        plugin = await load_plugin(
            data_dir=clone_data_dir,
            browser=browser,
            collector=collector,
        )
        try:
            database = plugin.database
            gameplay = plugin.gameplay_service
            if database is None or gameplay is None:
                raise RuntimeError("Third-round runtime did not initialize.")

            group_a = str(args.authorized_group)
            stream_a = f"uat-stream-{group_a}"
            user_a = "990000001"
            player_a = f"qq:{group_a}:{user_a}"
            catch_a = build_message(
                group_id=group_a,
                group_name="第三轮定制猪验收群",
                user_id=user_a,
                display_name="第三轮验收员甲",
                stream_id=stream_a,
                message_id="uat-a-catch-six",
            )
            gameplay.random_source = FixedRandom([0.999] * 7)
            await invoke(
                command_records,
                collector,
                "/抓猪（授权群六星边界）",
                plugin.handle_catch(
                    stream_id=stream_a,
                    **command_kwargs(catch_a),
                ),
            )
            await invoke(
                command_records,
                collector,
                "/抓猪（同消息进程内重复）",
                plugin.handle_catch(
                    stream_id=stream_a,
                    **command_kwargs(catch_a),
                ),
                expected_new_deliveries=0,
            )
            pig_a = await query_one(
                database,
                """
                SELECT pig_instance_id, display_name_snapshot, short_code, rarity
                FROM pig_instances
                WHERE owner_player_id = ?
                ORDER BY acquired_at
                LIMIT 1
                """,
                (player_a,),
            )
            if int(pig_a["rarity"]) != 6:
                raise AssertionError(f"Authorized group did not draw rarity 6: {pig_a}")
            selector = f"{pig_a['display_name_snapshot']}#{pig_a['short_code']}"

            query_specs = (
                (
                    "/抓猪档案",
                    plugin.handle_profile,
                    {},
                ),
                (
                    "/抓猪详情",
                    plugin.handle_pig_detail,
                    {"selector": selector},
                ),
                (
                    "/猪猪背包 1 排序=价值",
                    plugin.handle_inventory,
                    {"arguments": "1 排序=价值"},
                ),
                (
                    "/猪猪图鉴",
                    plugin.handle_catalog,
                    {"arguments": ""},
                ),
                (
                    "/猪猪纪录 1",
                    plugin.handle_records,
                    {"arguments": "1"},
                ),
            )
            for index, (label, handler, groups) in enumerate(query_specs, start=1):
                message = build_message(
                    group_id=group_a,
                    group_name="第三轮定制猪验收群",
                    user_id=user_a,
                    display_name="第三轮验收员甲",
                    stream_id=stream_a,
                    message_id=f"uat-a-query-{index}",
                )
                await invoke(
                    command_records,
                    collector,
                    label,
                    handler(
                        stream_id=stream_a,
                        **command_kwargs(message, **groups),
                    ),
                )

            await seed_item(plugin, player_a)
            item_commands = (
                (
                    "/使用道具 巨物玉米",
                    plugin.handle_use_item,
                    {"item_name": "巨物玉米"},
                ),
                (
                    "/取消道具 抓猪",
                    plugin.handle_cancel_item,
                    {"action": "抓猪"},
                ),
                (
                    "/使用道具 巨物玉米（再次装备）",
                    plugin.handle_use_item,
                    {"item_name": "巨物玉米"},
                ),
            )
            for index, (label, handler, groups) in enumerate(item_commands, start=1):
                message = build_message(
                    group_id=group_a,
                    group_name="第三轮定制猪验收群",
                    user_id=user_a,
                    display_name="第三轮验收员甲",
                    stream_id=stream_a,
                    message_id=f"uat-a-item-{index}",
                )
                await invoke(
                    command_records,
                    collector,
                    label,
                    handler(
                        stream_id=stream_a,
                        **command_kwargs(message, **groups),
                    ),
                )

            catch_with_item = build_message(
                group_id=group_a,
                group_name="第三轮定制猪验收群",
                user_id=user_a,
                display_name="第三轮验收员甲",
                stream_id=stream_a,
                message_id="uat-a-catch-item",
            )
            gameplay.random_source = FixedRandom([0.0] * 7)
            await invoke(
                command_records,
                collector,
                "/抓猪（成功后消耗已装备道具）",
                plugin.handle_catch(
                    stream_id=stream_a,
                    **command_kwargs(catch_with_item),
                ),
            )
            item_balance = await query_one(
                database,
                """
                SELECT quantity
                FROM item_inventory
                WHERE player_id = ? AND item_id = 'giant-corn'
                """,
                (player_a,),
            )
            armed_count = await query_one(
                database,
                """
                SELECT COUNT(*) AS count
                FROM armed_items
                WHERE player_id = ? AND action_type = 'catching'
                """,
                (player_a,),
            )
            if int(item_balance["quantity"]) != 1 or int(armed_count["count"]) != 0:
                raise AssertionError(
                    "A successful equipped catch did not consume exactly one item."
                )

            await plugin.on_unload()
            sends_before_restart_duplicate = len(collector.images) + len(collector.texts)
            plugin = await load_plugin(
                data_dir=clone_data_dir,
                browser=browser,
                collector=collector,
            )
            database = plugin.database
            gameplay = plugin.gameplay_service
            if database is None or gameplay is None:
                raise RuntimeError("Restarted third-round runtime did not initialize.")
            await invoke(
                command_records,
                collector,
                "/抓猪（插件重启后重复）",
                plugin.handle_catch(
                    stream_id=stream_a,
                    **command_kwargs(catch_with_item),
                ),
                expected_new_deliveries=0,
            )
            if len(collector.images) + len(collector.texts) != sends_before_restart_duplicate:
                raise AssertionError("Restart duplicate unexpectedly published a message.")

            group_b = str(args.ordinary_group)
            stream_b = f"uat-stream-{group_b}"
            user_b = "990000002"
            player_b = f"qq:{group_b}:{user_b}"
            catch_b = build_message(
                group_id=group_b,
                group_name="第三轮普通验收群",
                user_id=user_b,
                display_name="第三轮验收员乙",
                stream_id=stream_b,
                message_id="uat-b-catch-highest",
            )
            gameplay.random_source = FixedRandom([0.999] * 7)
            await invoke(
                command_records,
                collector,
                "/抓猪（普通群最高可用档）",
                plugin.handle_catch(
                    stream_id=stream_b,
                    **command_kwargs(catch_b),
                ),
            )
            pig_b = await query_one(
                database,
                """
                SELECT rarity, template_id
                FROM pig_instances
                WHERE owner_player_id = ?
                """,
                (player_b,),
            )
            if int(pig_b["rarity"]) != 5:
                raise AssertionError(
                    f"Ordinary group crossed the six-star boundary: {pig_b}"
                )

            counts = await query_one(
                database,
                """
                SELECT
                    COUNT(*) AS pig_count,
                    COUNT(DISTINCT idempotency_key) AS receipt_key_count
                FROM pig_instances
                LEFT JOIN command_receipts
                  ON command_receipts.result_object_id = pig_instances.pig_instance_id
                WHERE owner_player_id IN (?, ?)
                """,
                (player_a, player_b),
            )
            if int(counts["pig_count"]) != 3 or int(counts["receipt_key_count"]) != 3:
                raise AssertionError(f"Catch idempotency counts are wrong: {counts}")

            animated_deliveries = [
                image
                for image in collector.images
                if image["format"] == "GIF" and int(image["frame_count"]) > 1
            ]
            if not animated_deliveries:
                raise AssertionError("The animated six-star catch was flattened.")

            return {
                "formal_data_dir": str(source_data_dir),
                "isolated_data_dir": str(clone_data_dir),
                "schema_version": int(
                    (
                        await query_one(
                            database,
                            "SELECT MAX(version) AS version FROM schema_migrations",
                            (),
                        )
                    )["version"]
                ),
                "registered_commands": len(plugin.get_components()),
                "command_records": command_records,
                "deliveries": {
                    "images": collector.images,
                    "texts": collector.texts,
                },
                "assertions": {
                    "authorized_group_rarity": int(pig_a["rarity"]),
                    "ordinary_group_rarity": int(pig_b["rarity"]),
                    "item_quantity_after_success": int(item_balance["quantity"]),
                    "armed_item_count_after_success": int(armed_count["count"]),
                    "committed_catches": int(counts["pig_count"]),
                    "unique_catch_receipts": int(counts["receipt_key_count"]),
                    "restart_duplicate_suppressed": True,
                    "animated_delivery_count": len(animated_deliveries),
                },
            }
        finally:
            await plugin.on_unload()
            await browser.close()


async def async_main() -> None:
    args = parse_args()
    report = await run_uat(args)
    report_path = args.output.resolve() / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report["assertions"], ensure_ascii=False, indent=2))
    print(f"report: {report_path}")


if __name__ == "__main__":
    asyncio.run(async_main())
