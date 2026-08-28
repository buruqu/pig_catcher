"""第九期实际吃菜、抓猪、批量做菜与既有存档的关键离线回归。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.economy import generate_food_attributes, scale_food_attributes
from pig_catcher.domain.enums import Rarity
from pig_catcher.domain.food_effects import resolve_food_effect
from pig_catcher.domain.gameplay import item_by_id
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.round9_food_rules import GROUP_FOOD_PREFIXES, reviewed_food_revisions
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.repositories import EconomyRepository, FrameworkRepository
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.services import EconomyService, GameplayService
from pig_catcher.services.command_state import iso_timestamp

from .test_asset_code_lifecycle import OWNER, SCOPE, _asset_values, _create_v41, _insert_sql
from .test_economy import (
    FixedClock,
    SequenceRandom,
    _database_with_catalog,
    _food_entry,
    _insert_food,
    _insert_pig,
    _pig_entry,
)

SCOPES = (
    "qq:1092931381",
    "qq:237716658",
    "qq-official:5E5854406D0297D6FEAE696A13E3A339",
    "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
)
RULES = reviewed_food_revisions()


def actor(scope: str = SCOPES[0], message: str = "seed") -> CommandIdentity:
    return CommandIdentity(ScopeKey.parse(scope), "round9-test", "same-user", "测试玩家", message)


def _key(scope: str, suffix: str) -> str:
    return GROUP_FOOD_PREFIXES[SCOPES.index(scope)] + suffix


@pytest.fixture
async def db(tmp_path: Path):
    entries = []
    for key, (effect, params) in RULES.items():
        prefix = next((prefix for prefix in GROUP_FOOD_PREFIXES if key.startswith(prefix)), "")
        rarity = 6 if prefix else (4 if key.startswith("food-r4-") else 5)
        entry = _food_entry(rarity, group_id="placeholder" if prefix else None)
        entry.update(template_id=key, image=key + ".png", display_name=key)
        entry.update(effect_id=effect, effect_params=params)
        if prefix:
            entry["group_scope_id"] = SCOPES[GROUP_FOOD_PREFIXES.index(prefix)]
            pig = _pig_entry(6, group_id="placeholder", paired_food_template_id=key)
            pig.update(
                template_id="pig" + key[4:], image="pig" + key[4:] + ".png", group_scope_id=entry["group_scope_id"]
            )
            entries.append(pig)
        entries.append(entry)
    database = await _database_with_catalog(
        tmp_path,
        group_id="1092931381",
        pig_rarities=(1, 2, 3, 4, 5, 6),
        food_rarities=(1, 2, 3, 4, 5, 6),
        extra_entries=tuple(entries),
        manifest_version=4,
    )
    try:
        yield database
    finally:
        await database.close()


def _clock() -> FixedClock:
    clock = FixedClock()
    clock.value = datetime(2026, 8, 30, 15, 50, tzinfo=UTC)  # 北京周日23:50
    return clock


def _economy(db, clock, random_source=None) -> EconomyService:
    return EconomyService(
        db,
        CookingSection(cook_cooldown_seconds=0),
        EconomySection(),
        clock=clock,
        random_source=random_source,
    )


async def _food(db, identity, clock, key):
    instance_id = uuid4().hex
    code = uuid4().hex[:8]
    async with db.transaction() as session:
        await FrameworkRepository().touch_identity(session, identity=identity, now=iso_timestamp(clock.now()))
    effect, params = RULES[key]
    await _insert_food(
        db,
        player_id=identity.player_id,
        scope_id=identity.scope.value,
        template_id=key,
        rarity=6 if key.startswith(GROUP_FOOD_PREFIXES) else (4 if key.startswith("food-r4-") else 5),
        display_name=key,
        official_value=900,
        short_code=code,
        instance_id=instance_id,
        effect_id=effect,
        effect_params=params,
        now=iso_timestamp(clock.now()),
    )
    return instance_id, f"{key}#{code}"


async def _eat(db, economy, identity, clock, key):
    food_id, selector = await _food(db, identity, clock, key)
    command = replace(identity, message_id=uuid4().hex)
    result = await economy.eat(command, selector)
    clock.value += timedelta(seconds=1)  # 明确同类队列次序，不依赖随机UUID排序
    return result, food_id, command, selector


async def _arm(db, gameplay, identity, clock, item_id, count=1):
    async with db.transaction() as session:
        await session.execute(
            "INSERT INTO item_inventory(player_id,item_id,quantity,updated_at) VALUES(?,?,?,?)",
            (identity.player_id, item_id, count, iso_timestamp(clock.now())),
        )
    await gameplay.arm_item(replace(identity, message_id=uuid4().hex), item_by_id(item_id).display_name, quantity=count)


async def _effects(db, player_id):
    return {
        str(row["source_food_instance_id"]): dict(row)
        for row in await db.fetch_all("SELECT * FROM player_food_effects WHERE player_id=?", (player_id,))
    }


async def test_mist_ten_independent_shuffles_ignore_all_modifiers_and_keep_queues(db):
    """最新规则：等级、饲料、达妮娅也不叠加，只有洗牌后的六档基础概率。"""
    clock, identity = _clock(), actor()
    economy = _economy(db, clock)
    _, reward_id, _, _ = await _eat(db, economy, identity, clock, "food-r4-hot-pig")
    _, stature_id, _, _ = await _eat(db, economy, identity, clock, "food-r4-souffle")
    _, mist_id, _, _ = await _eat(db, economy, identity, clock, _key(SCOPES[0], "mist-blue-keyboard-daifuku"))
    async with db.transaction() as session:
        await session.execute("UPDATE players SET experience=20000 WHERE player_id=?", (identity.player_id,))
        await session.execute(
            "INSERT INTO upgrades(player_id,upgrade_type,level,updated_at) VALUES(?,'feed',5,?)",
            (identity.player_id, iso_timestamp(clock.now())),
        )
        for _ in range(5):
            await EconomyRepository().increment_six_star_progress(
                session,
                player_id=identity.player_id,
                source_food_instance_id=mist_id,
                max_stacks=5,
                now=iso_timestamp(clock.now()),
            )
        for key, action in (
            ("catalog-guide", "catching"),
            ("giant-rescale", "catching"),
            ("achievement-catch", "catching"),
            ("achievement-firework", "visual"),
        ):
            await AchievementRepository().activate_ticket(
                session,
                effect_entry_id=key,
                player_id=identity.player_id,
                ticket_id=key,
                action_type=action,
                uses=1,
                now=iso_timestamp(clock.now()),
            )
    random = SequenceRandom(
        *[
            value
            for index in range(10)
            for value in ([0.0 if index % 2 == 0 else 0.999] * 5 + [0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5])
        ]
    )
    gameplay = GameplayService(
        db, CatchingSection(daily_limit=1, cooldown_seconds=0), clock=clock, random_source=random
    )
    await _arm(db, gameplay, identity, clock, "super-lucky-whistle")
    clock.value += timedelta(days=2)  # 过旧时段和自然日仍保留十次
    permutations = []
    for index in range(10):
        result = await gameplay.catch(replace(identity, message_id=f"mist-{index}"))
        snapshot = json.loads(
            (
                await db.fetch_one(
                    "SELECT random_snapshot_json FROM pig_instances WHERE pig_instance_id=?",
                    (result.pig.pig_instance_id,),
                )
            )[0]
        )
        order = snapshot["shuffle_permutation"]
        permutations.append(order)
        assert result.weights == pytest.approx(tuple(snapshot["base_weights"][n - 1] for n in order))
        assert result.daily_count == 0 and result.quota_exempt_catch
        assert result.exclusive_effect_active and not result.item_id
        assert len(snapshot["shuffle_rolls"]) == 5
        assert snapshot["food_effect_entry_ids"] == [
            (await _effects(db, identity.player_id))[mist_id]["effect_entry_id"]
        ]
        assert f"剩余 {9 - index}/10 次" in " ".join(result.effect_summaries)
    assert permutations[0] != permutations[1] and not random.values
    rows = await _effects(db, identity.player_id)
    assert rows[mist_id]["consumed_uses"] == rows[mist_id]["granted_uses"] == 10
    assert rows[mist_id]["expires_at"] is None
    assert rows[reward_id]["consumed_uses"] == rows[stature_id]["consumed_uses"] == 0
    assert all(row[0] == 0 for row in await db.fetch_all("SELECT consumed_uses FROM achievement_ticket_effects"))
    assert (await db.fetch_one("SELECT quantity FROM item_inventory WHERE item_id='super-lucky-whistle'"))[0] == 1
    assert (await gameplay.profile(identity)).daily_count == 0


async def test_hot_pig_rewards_stack_with_item_and_stature_but_same_rewards_queue(db):
    clock, identity = _clock(), actor()
    economy = _economy(db, clock)
    _, first, _, _ = await _eat(db, economy, identity, clock, "food-r4-hot-pig")
    _, second, _, _ = await _eat(db, economy, identity, clock, "food-r4-hot-pig")
    _, stature, _, _ = await _eat(db, economy, identity, clock, "food-r4-souffle")
    gameplay = GameplayService(
        db,
        CatchingSection(daily_limit=50, cooldown_seconds=0),
        clock=clock,
        random_source=SequenceRandom(*([0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5] * 4)),
    )
    await _arm(db, gameplay, identity, clock, "coin-bounty-tag")
    for index in range(4):
        result = await gameplay.catch(replace(identity, message_id=f"hot-{index}"))
        assert result.pig.rarity == 1
        assert result.coin_reward == (26 if index == 0 else 22)
        assert result.experience_reward == (12 if index == 0 else 8)
        rows = await _effects(db, identity.player_id)
        assert rows[first]["consumed_uses"] == min(3, index + 1)
        assert rows[second]["consumed_uses"] == max(0, index - 2)
        assert rows[stature]["consumed_uses"] == min(2, index + 1)
        assert "剩余" in " ".join(result.effect_summaries)


async def test_batch_cook_consumes_quality_fifo_and_servings_with_combined_cap(db):
    clock, identity = _clock(), actor()
    economy = _economy(db, clock, SequenceRandom(*([0.0, 0.0, 0.5] * 4)))
    _, quality_first, _, _ = await _eat(db, economy, identity, clock, "food-r5-tiramisu")
    _, quality_second, _, _ = await _eat(db, economy, identity, clock, "food-r4-orange-milk")
    _, serving, _, _ = await _eat(db, economy, identity, clock, "food-r5-yolk-pig")
    gameplay = GameplayService(db, CatchingSection(cooldown_seconds=0), clock=clock)
    await _arm(db, gameplay, identity, clock, "harvest-apron")
    for index in range(4):
        await _insert_pig(
            db,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id="pig-1-common",
            rarity=1,
            display_name="1星测试猪",
            official_value=100,
            short_code=f"BATCH{index}",
            instance_id=f"batch-{index}",
            now=iso_timestamp(clock.now()),
        )
    result = await economy.batch_cook(replace(identity, message_id="batch-r9"), rarity=1)
    assert result.pig_count == result.food_count == 4
    for index, food in enumerate(result.foods):
        snapshot = json.loads(
            (
                await db.fetch_one(
                    "SELECT random_snapshot_json FROM food_instances WHERE food_instance_id=?", (food.food_instance_id,)
                )
            )[0]
        )
        multiplier = 2.0 if index == 0 else (1.6 if index < 3 else 1.0)
        assert snapshot["output_multiplier"] == pytest.approx(multiplier)
        baseline = generate_food_attributes(
            rarity=Rarity(food.rarity),
            template_id=food.template_id,
            source_weight=60,
            source_weight_percentile=0.5,
            portion_roll=0.5,
        )
        expected = scale_food_attributes(baseline, multiplier=multiplier) if multiplier > 1 else baseline
        assert food.portion_weight == expected.portion_weight
        assert food.official_value == expected.official_value
    rows = await _effects(db, identity.player_id)
    assert rows[quality_first]["consumed_uses"] == rows[quality_first]["granted_uses"] == 3
    assert rows[quality_second]["consumed_uses"] == 1 and rows[quality_second]["granted_uses"] == 2
    assert rows[serving]["consumed_uses"] == rows[serving]["granted_uses"] == 3
    replay = await economy.batch_cook(replace(identity, message_id="batch-r9"), rarity=1)
    assert not replay.receipt_created and replay.receipt.result_json == result.receipt.result_json


@pytest.mark.parametrize(
    "suffix,coins,coupon",
    [
        ("juejue-pie", 12222, "asset-code-change"),
        ("daniya-bubble-jelly", 22222, "pig-choice"),
    ],
)
async def test_fifth_layer_then_sixth_and_seventh_overflow_replay_and_four_scope_isolation(db, suffix, coins, coupon):
    for scope in SCOPES:
        clock, identity = _clock(), actor(scope)
        economy = _economy(db, clock)
        for index in range(7):
            result, food_id, command, selector = await _eat(db, economy, identity, clock, _key(scope, suffix))
            assert result.effect.coin_bonus == (0 if index < 5 else coins)
            assert result.coin_balance == max(0, index - 4) * coins
            if index == 0 and suffix == "juejue-pie":
                async with db.transaction() as session:
                    await EconomyRepository().grant_weekly_catch_bonus(
                        session,
                        player_id=identity.player_id,
                        source_food_instance_id=food_id,
                        count=5,
                        expires_at=iso_timestamp(clock.now() + timedelta(days=7)),
                        now=iso_timestamp(clock.now()),
                    )
            if index >= 5:
                assert result.reward_payload["items"][0]["reward_id"] == coupon
        await db.close()
        await db.open()
        replay = await _economy(db, clock).eat(command, selector)
        assert not replay.receipt_created and replay.reward_payload == result.reward_payload
        assert replay.coin_balance == coins * 2
        quantity = await db.fetch_one(
            "SELECT quantity FROM achievement_reward_inventory WHERE player_id=? AND reward_id=?",
            (identity.player_id, coupon),
        )
        assert quantity[0] == 2
        if suffix == "juejue-pie":
            bonus = await db.fetch_one(
                "SELECT * FROM player_catch_quota_bonuses WHERE player_id=?", (identity.player_id,)
            )
            assert bonus["permanent_bonus"] == bonus["weekly_bonus"] == 5
            effects = await _effects(db, identity.player_id)
            assert len(effects) == 2
            assert all(row["expires_at"] == "2026-08-30T16:00:00.000Z" for row in effects.values())
            gameplay = GameplayService(db, CatchingSection(cooldown_seconds=0), clock=clock)
            assert (await gameplay.profile(identity)).daily_limit == 17
            clock.value = datetime(2026, 8, 30, 16, tzinfo=UTC)
            assert (await gameplay.profile(identity)).daily_limit == 15
            assert (
                await db.fetch_one(
                    "SELECT weekly_expires_at FROM player_catch_quota_bonuses WHERE player_id=?", (identity.player_id,)
                )
            )[0] == bonus["weekly_expires_at"]
        else:
            assert (
                await db.fetch_one(
                    "SELECT stacks FROM player_six_star_progress WHERE player_id=?", (identity.player_id,)
                )
            )[0] == 5
        # 同一个平台用户在另一scope没有被动领取本scope奖励。
        for untouched_scope in SCOPES[SCOPES.index(scope) + 1 :]:
            assert (
                await db.fetch_one(
                    "SELECT 1 FROM achievement_reward_inventory WHERE player_id=?", (actor(untouched_scope).player_id,)
                )
                is None
            )


async def test_migration44_preserves_history_and_mist_remaining_without_reviving_expired(tmp_path):
    path = tmp_path / "before-44.sqlite3"
    connection = _create_v41(path)
    for migration in MIGRATIONS:
        if 42 <= migration.version <= 43:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, "test")
            )
    connection.execute("PRAGMA user_version=43")
    keys = ["food-r4-hot-pig", *[prefix + "mist-blue-keyboard-daifuku" for prefix in GROUP_FOOD_PREFIXES]]
    before = {}
    for key_index, key in enumerate(keys):
        columns = [row[1] for row in connection.execute("PRAGMA table_info(food_templates)")]
        template = dict(
            zip(
                columns,
                connection.execute("SELECT * FROM food_templates WHERE template_id='code-food'").fetchone(),
                strict=True,
            )
        )
        template.update(template_id=key, effect_id="next-high-star-catch", effect_params_json='{"uses":5}')
        connection.execute(
            f"INSERT INTO food_templates({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(template.values()),
        )
        for state_index, state in enumerate(("active", "locked-for-trade", "sold", "consumed")):
            food_id = f"old-{key_index}-{state_index}"
            values = _asset_values("food", food_id, f"OLD{key_index}{state_index}", state=state)
            values.update(template_id=key, effect_id="next-high-star-catch", effect_params_json='{"uses":5}')
            connection.execute(_insert_sql("food", values), values)
            before[food_id] = dict(values)
        if key_index:
            for label, expiry in (("live", "2099-01-01T00:00:00.000Z"), ("expired", "2000-01-01T00:00:00.000Z")):
                connection.execute(
                    "INSERT INTO player_food_effects(effect_entry_id,player_id,source_food_instance_id,effect_id,"
                    "params_json,granted_uses,consumed_uses,expires_at,created_at,updated_at) "
                    "VALUES(?,?,?,'next-high-star-catch','{\"uses\":5}',5,2,?,'old','old')",
                    (f"{label}-{key_index}", OWNER, f"old-{key_index}-3", expiry),
                )
    connection.commit()
    connection.close()
    database = PigCatcherDatabase(path)
    await database.open()
    try:
        for food_id, old in before.items():
            new = dict(await database.fetch_one("SELECT * FROM food_instances WHERE food_instance_id=?", (food_id,)))
            if old["state"] in {"active", "locked-for-trade"}:
                effect, params = RULES[str(old["template_id"])]
                assert new["effect_id"] == effect and json.loads(new["effect_params_json"]) == params
                for field in ("effect_id", "effect_params_json", "updated_at"):
                    new.pop(field)
                    old.pop(field)
            assert new == old
        for row in await database.fetch_all("SELECT * FROM player_food_effects"):
            assert row["granted_uses"] == 5 and row["consumed_uses"] == 2
            if str(row["effect_entry_id"]).startswith("live"):
                assert row["effect_id"] == "shuffled-catch-distribution" and row["expires_at"] is None
                assert resolve_food_effect(row["effect_id"], json.loads(row["params_json"])).granted_uses == 5
            else:
                assert row["effect_id"] == "next-high-star-catch" and row["expires_at"].startswith("2000-")
        assert await database.fetch_all("PRAGMA foreign_key_check") == []
        assert (await database.fetch_one("SELECT scope_id FROM players WHERE player_id=?", (OWNER,)))[0] == SCOPE
    finally:
        await database.close()
