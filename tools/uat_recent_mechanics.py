"""Run recent-mechanic image UAT on an isolated production-data clone."""

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

from pig_catcher.domain.enums import AssetKind  # noqa: E402
from pig_catcher.domain.models import CommandIdentity, ScopeKey  # noqa: E402
from pig_catcher.services import AdministrationService  # noqa: E402
from tools.uat_catching_and_collection import (  # noqa: E402
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
    parser.add_argument("--group", default="1092931381")
    return parser.parse_args()


def configure_plugin(plugin: Any) -> None:
    config = plugin.get_default_config()
    config["maintenance"]["enabled"] = False
    config["catching"]["cooldown_seconds"] = 0
    config["catching"]["daily_limit"] = 100
    config["cooking"]["cook_cooldown_seconds"] = 0
    # Assert one core result image per action. Achievement notifications have a
    # separate enabled end-to-end suite and must not be mistaken for duplicates.
    config["features"]["achievements_enabled"] = False
    plugin.set_plugin_config(config)


def message(
    group_id: str,
    user_id: str,
    display_name: str,
    message_id: str,
) -> dict[str, object]:
    return build_message(
        group_id=group_id,
        group_name="近期机制生产副本验收群",
        user_id=user_id,
        display_name=display_name,
        stream_id=f"uat-recent-{group_id}",
        message_id=message_id,
    )


def identity(
    group_id: str,
    user_id: str,
    display_name: str,
    message_id: str,
) -> CommandIdentity:
    return CommandIdentity(
        scope=ScopeKey("qq", group_id),
        stream_id=f"uat-recent-{group_id}",
        user_id=user_id,
        display_name=display_name,
        message_id=message_id,
        group_name="近期机制生产副本验收群",
    )


async def grant_food(
    service: AdministrationService,
    *,
    group_id: str,
    target_user_id: str,
    target_display_name: str,
    display_name: str,
    sequence: int,
) -> str:
    admin = identity(
        group_id,
        "recent-uat-admin",
        "近期机制验收管理员",
        f"grant-{sequence}-{display_name}",
    )
    await service.grant_asset(
        admin,
        command_name=f"pig-catcher.uat-grant-{sequence}",
        target_user_id=target_user_id,
        asset_kind=AssetKind.FOOD,
        template_selector=display_name,
        requested_short_code=None,
    )
    database = service.database
    row = await database.fetch_one(
        """
        SELECT display_name_snapshot, short_code
        FROM food_instances
        WHERE owner_player_id = ? AND display_name_snapshot = ? AND state = 'active'
        ORDER BY acquired_at DESC, food_instance_id DESC
        LIMIT 1
        """,
        (f"qq:{group_id}:{target_user_id}", display_name),
    )
    if row is None:
        raise AssertionError(f"Failed to grant {display_name} to {target_display_name}")
    return f"{row['display_name_snapshot']}#{row['short_code']}"


async def finish_active_technique_on_next_catch(plugin: Any, group_id: str) -> None:
    """Shorten only the isolated UAT clone so every technique fits one run."""

    async with plugin.database.transaction() as session:
        await session.execute(
            """
            UPDATE group_technique_effects
            SET remaining_uses = 1
            WHERE scope_id = ? AND status = 'active'
            """,
            (f"qq:{group_id}",),
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
    collector = DeliveryCollector(deliveries)
    records: list[dict[str, object]] = []
    actor_user = "recent-uat-actor"
    catcher_user = "recent-uat-catcher"
    actor_name = "近期机制发动者"
    catcher_name = "近期机制抓猪者"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_options)
        plugin = create_plugin()
        configure_plugin(plugin)
        plugin._set_context(
            LocalContext(
                data_dir=cloned_data,
                browser=browser,
                collector=collector,
            )
        )
        await plugin.on_load()
        if plugin.gameplay_service is None or plugin._administration_service is None:
            raise AssertionError("Recent-mechanic UAT services did not load.")
        administration = plugin._administration_service
        await plugin.gameplay_service.profile(
            identity(args.group, actor_user, actor_name, "touch-actor")
        )
        await plugin.gameplay_service.profile(
            identity(args.group, catcher_user, catcher_name, "touch-catcher")
        )

        sequence = 0

        async def eat_food(display_name: str, label: str) -> None:
            nonlocal sequence
            sequence += 1
            selector = await grant_food(
                administration,
                group_id=args.group,
                target_user_id=actor_user,
                target_display_name=actor_name,
                display_name=display_name,
                sequence=sequence,
            )
            await invoke(
                records,
                collector,
                label,
                plugin.handle_eat(
                    stream_id=f"uat-recent-{args.group}",
                    **command_kwargs(
                        message(
                            args.group,
                            actor_user,
                            actor_name,
                            f"eat-{sequence}",
                        ),
                        selector=selector,
                    ),
                ),
            )

        await eat_food("猪保千猪排轮盘", "eat roulette food image")
        plugin.economy_service.random_source = FixedRandom([0.51])
        await invoke(
            records,
            collector,
            "roulette settlement image",
            plugin.handle_roulette(
                stream_id=f"uat-recent-{args.group}",
                **command_kwargs(
                    message(
                        args.group,
                        actor_user,
                        actor_name,
                        "spin-roulette",
                    )
                ),
            ),
        )

        await eat_food("伏魔朱焰咒纹猪蹄饭", "eat domain food image")
        await invoke(
            records,
            collector,
            "domain activation image",
            plugin.handle_domain_expansion(
                stream_id=f"uat-recent-{args.group}",
                **command_kwargs(
                    message(
                        args.group,
                        actor_user,
                        actor_name,
                        "activate-domain",
                    )
                ),
            ),
        )
        await finish_active_technique_on_next_catch(plugin, args.group)
        plugin.gameplay_service.random_source = FixedRandom([0.0] * 30)
        await invoke(
            records,
            collector,
            "domain auto-cook settlement image",
            plugin.handle_catch(
                stream_id=f"uat-recent-{args.group}",
                **command_kwargs(
                    message(
                        args.group,
                        catcher_user,
                        catcher_name,
                        "domain-catch",
                    )
                ),
            ),
        )

        for display_name, activation, technique_label in (
            ("五条猪无量苍蓝雪山", plugin.handle_lapse_blue, "blue"),
            ("五条猪无量赫焰雪山", plugin.handle_reversal_red, "red"),
        ):
            await eat_food(display_name, f"eat {technique_label} food image")
            await invoke(
                records,
                collector,
                f"{technique_label} activation image",
                activation(
                    stream_id=f"uat-recent-{args.group}",
                    **command_kwargs(
                        message(
                            args.group,
                            actor_user,
                            actor_name,
                            f"activate-{technique_label}",
                        )
                    ),
                ),
            )
            await finish_active_technique_on_next_catch(plugin, args.group)
            plugin.gameplay_service.random_source = FixedRandom([0.0] * 30)
            await invoke(
                records,
                collector,
                f"{technique_label} catch settlement image",
                plugin.handle_catch(
                    stream_id=f"uat-recent-{args.group}",
                    **command_kwargs(
                        message(
                            args.group,
                            catcher_user,
                            catcher_name,
                            f"catch-{technique_label}",
                        )
                    ),
                ),
            )

        plugin.gameplay_service.random_source = FixedRandom([0.0] * 50)
        await invoke(
            records,
            collector,
            "hollow purple settlement image",
            plugin.handle_hollow_purple(
                stream_id=f"uat-recent-{args.group}",
                **command_kwargs(
                    message(
                        args.group,
                        actor_user,
                        actor_name,
                        "activate-purple",
                    )
                ),
            ),
        )

        await eat_food("炸猪全家桶", "KFC tribute total image")
        await plugin.on_unload()
        await browser.close()

    if collector.texts:
        raise AssertionError(f"Recent-mechanic UAT used text fallback: {collector.texts}")
    report = {
        "status": "passed",
        "source_data": str(source_data),
        "cloned_data": str(cloned_data),
        "commands": records,
        "deliveries": collector.images,
        "text_fallbacks": collector.texts,
        "uat_acceleration": "每种群体术式在生产副本中缩短到下一次抓猪后结束",
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> None:
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
