"""通用背包及奖励券：隔离、自然属性、锁保护、原子回滚与重放。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pig_catcher.commands.item_bag import parse_item_bag_request
from pig_catcher.domain.errors import AssetStateConflictError, DomainValidationError, ReceiptConflictError
from pig_catcher.domain.gameplay import generate_pig_attributes
from pig_catcher.domain.item_bag import CODE_CHANGE_COUPON, LEGACY_CODE_CHANGE_COUPON, PIG_CHOICE_COUPON
from pig_catcher.domain.models import CommandIdentity
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.repositories.achievement_coupons import AchievementCouponRepository
from pig_catcher.infrastructure.repositories.achievements import AchievementRepository
from pig_catcher.infrastructure.repositories.asset_codes import AssetCodeRepository
from pig_catcher.infrastructure.repositories.dispatch import iso_ms, timestamp_ms
from pig_catcher.infrastructure.repositories.economy import EconomyRepository
from pig_catcher.infrastructure.repositories.framework import FrameworkRepository
from pig_catcher.infrastructure.repositories.gameplay import GameplayRepository
from pig_catcher.services.item_bag import ItemBagService

from .test_dispatch import NOW, seed_pigs
from .test_gameplay import MutableClock, SequenceRandom, _database_with_catalog, _food_entry, _identity, _pig_entry


@dataclass
class World:
    db: PigCatcherDatabase
    service: ItemBagService
    clock: MutableClock
    identity: CommandIdentity
    pig_id: str
    food_id: str

    @property
    def now(self) -> str:
        return iso_ms(timestamp_ms(self.clock.now()))

    async def use(self, text: str, *, message_id: str | None = None, identity: CommandIdentity | None = None):
        return await self.service.execute(
            replace(identity or self.identity, message_id=message_id or uuid4().hex), text
        )

    async def grant(self, coupon: str, quantity: int = 1, *, source: str | None = None, identity=None):
        identity = identity or self.identity
        async with self.db.transaction() as session:
            await FrameworkRepository().touch_identity(session, identity=identity, now=self.now)
            return await self.service.grant_coupon(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                coupon_id=coupon,
                quantity=quantity,
                source_id=source or uuid4().hex,
                now=self.now,
            )

    async def quantity(self, coupon: str, *, identity=None) -> int:
        async with self.db.transaction(immediate=False) as session:
            return await self.service.repository.quantity(session, (identity or self.identity).player_id, coupon)


@pytest.fixture
async def world(tmp_path: Path):
    db = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("low", rarity=1),
            _pig_entry("high", rarity=5),
            _pig_entry("six", rarity=6, group_id="100"),
            _pig_entry("other-six", rarity=6, group_id="101", display_name="另群六星猪"),
            _food_entry("food", effect_id="", effect_params={}, group_id=None, rarity=4, display_name="测试菜"),
        ],
    )
    identity = _identity(display_name="<script>不是代码</script>", message_id="seed")
    clock = MutableClock(NOW)
    service = ItemBagService(db, clock=clock, random_source=SequenceRandom(*([0.5] * 100)))
    pig_id = (await seed_pigs(db, identity, count=1))[0]
    async with db.transaction() as session:
        now = iso_ms(timestamp_ms(NOW))
        await session.execute("UPDATE pig_instances SET short_code='OLDPIG01' WHERE pig_instance_id=?", (pig_id,))
        await EconomyRepository().insert_food_instance(
            session,
            values={
                "food_instance_id": "food-instance",
                "short_code": "OLDFOOD1",
                "scope_id": identity.scope.value,
                "owner_player_id": identity.player_id,
                "template_id": "food",
                "template_version": 1,
                "source_pig_instance_id": None,
                "rarity": 4,
                "display_name_snapshot": "测试菜",
                "portion_weight": 10.0,
                "fat_category": "balanced",
                "official_value": 400,
                "effect_id": "",
                "effect_params_json": "{}",
                "ruleset_version": 37,
                "random_snapshot_json": "{}",
                "acquired_at": now,
                "updated_at": now,
            },
        )
    yield World(db, service, clock, identity, pig_id, "food-instance")
    await db.close()


@pytest.mark.parametrize("text,page", [("", 1), ("1", 1), ("15", 15), ("999999", 999999)])
def test_bag_parser(text, page):
    assert parse_item_bag_request(text).args == {"page": page}


@pytest.mark.parametrize("text", ["0", "-1", "１", "1 2", "1.0", "9999999", "删库"])
def test_bag_parser_rejects_invalid_page(text):
    with pytest.raises(DomainValidationError):
        parse_item_bag_request(text)


@pytest.mark.parametrize("name", ["编号修改券", "编号修改卷", "asset-code-change"])
def test_coupon_parser_accepts_new_spelling_and_old_code(name):
    request = parse_item_bag_request(f"{name} 猪猪 猪 猪#Abcd Efgh", section="coupon")
    assert request.action == "rename"
    assert request.args == {"asset_kind": "猪猪", "selector": "猪 猪#Abcd", "new_code": "Efgh"}
    assert parse_item_bag_request("猪猪自选卷 6星测试猪", section="coupon").args == {"selector": "6星测试猪"}


@pytest.mark.parametrize("text", ["", "编号修改券", "编号修改券 猪猪", "编号修改券 猪猪 A", "陌生券 xyz", "猪猪自选券"])
def test_coupon_parser_requires_explicit_selection(text):
    with pytest.raises(DomainValidationError):
        parse_item_bag_request(text, section="coupon")


async def test_grant_same_source_is_idempotent_and_parameter_conflict(world):
    w = world
    first = await w.grant(PIG_CHOICE_COUPON, 2, source="same-food")
    assert await w.grant(PIG_CHOICE_COUPON, 2, source="same-food") == first
    with pytest.raises(ReceiptConflictError):
        await w.grant(PIG_CHOICE_COUPON, 3, source="same-food")
    assert await w.quantity(PIG_CHOICE_COUPON) == 2
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM reward_coupon_grants"))[0] == 1


async def test_grant_rolls_back_with_eating_transaction_and_rejects_cross_scope(world):
    w = world
    with pytest.raises(RuntimeError):
        async with w.db.transaction() as session:
            await w.service.grant_coupon(
                session,
                player_id=w.identity.player_id,
                scope_id=w.identity.scope.value,
                coupon_id=PIG_CHOICE_COUPON,
                source_id="failed-food",
                now=w.now,
            )
            raise RuntimeError("fake eating failure")
    assert await w.quantity(PIG_CHOICE_COUPON) == 0
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM reward_coupon_grants"))[0] == 0
    async with w.db.transaction() as session:
        with pytest.raises(DomainValidationError, match="所属"):
            await w.service.grant_coupon(
                session,
                player_id=w.identity.player_id,
                scope_id="qq:101",
                coupon_id=PIG_CHOICE_COUPON,
                source_id="cross",
                now=w.now,
            )


@pytest.mark.parametrize("quantity", [0, -1, 10001, 1.5, True])
async def test_grant_rejects_invalid_quantity(world, quantity):
    with pytest.raises(DomainValidationError):
        await world.grant(PIG_CHOICE_COUPON, quantity)


async def test_bag_store_queues_legacy_activation_and_activity_selection_not_double_counted(world):
    w = world
    async with w.db.transaction() as session:
        await EconomyRepository().add_item_inventory(
            session, player_id=w.identity.player_id, item_id="lucky-whistle", quantity=5, now=w.now
        )
        await GameplayRepository().arm_item(
            session,
            player_id=w.identity.player_id,
            action_type="catching",
            item_id="lucky-whistle",
            remaining_uses=3,
            now=w.now,
        )
        for kind, reward, quantity in (
            ("ticket", "dispatch-luggage", 3),
            ("ticket", "achievement-catch", 2),
            ("chest", "materials-choice", 48),
        ):
            await AchievementRepository().grant_reward(
                session,
                player_id=w.identity.player_id,
                reward_type=kind,
                reward_id=reward,
                quantity=quantity,
                now=w.now,
            )
        await AchievementCouponRepository().select(session, w.identity.player_id, "dispatch-luggage", w.now)
        await session.execute(
            "INSERT INTO achievement_ticket_effects VALUES(?,?,'achievement-catch','catching',3,1,?,?)",
            ("active-ticket", w.identity.player_id, w.now, w.now),
        )
        entries = {
            entry.item_id: entry
            for entry in await w.service.repository.entries(session, player_id=w.identity.player_id)
        }
    assert (entries["lucky-whistle"].total, entries["lucky-whistle"].available) == (5, 2)
    assert entries["lucky-whistle"].state == "已装备 1 · 排队 2"
    assert (entries["dispatch-luggage"].total, entries["dispatch-luggage"].available) == (3, 2)
    assert (entries["achievement-catch"].total, entries["achievement-catch"].available) == (4, 2)
    assert (entries["materials-choice"].total, entries["materials-choice"].available) == (48, 48)
    view = (await w.service.bag(w.identity)).view
    assert "总数已包含" in view.text()
    assert "口袋行李券" in view.text() and "基础材料自选份" in view.text()


async def test_bag_empty_scope_isolation_page_clamp_and_tools(world):
    w = world
    empty_text = (await w.service.bag(w.identity)).view.text()
    assert "背包空空" in empty_text
    assert "/猪猪成就 查看外观收藏" in empty_text
    assert "/抓猪成就" not in empty_text
    await w.grant(PIG_CHOICE_COUPON)
    other = _identity(group_id="101")
    assert "背包空空" in (await w.service.bag(other)).view.text()
    async with w.db.transaction() as session:
        for table, tool in (("dispatch_tools", "region-map"), ("tour_tools", "cable"), ("battle_tools", "wristband")):
            await session.execute(f"INSERT INTO {table} VALUES(?,?,2)", (w.identity.player_id, tool))
        for index in range(12):
            await AchievementRepository().grant_reward(
                session,
                player_id=w.identity.player_id,
                reward_type="ticket",
                reward_id=f"unknown-{index}",
                quantity=1,
                now=w.now,
            )
    view = (await w.service.bag(w.identity, 999999)).view
    assert view.page == view.page_count == 2
    assert len(view.panels) == 8
    all_text = "\n".join([(await w.service.bag(w.identity, page)).view.text() for page in (1, 2)])
    assert "区域地图" in all_text and "备用线缆" in all_text and "练习护腕" in all_text


@pytest.mark.parametrize(
    "kind,selector,table,id_column,instance",
    [
        ("猪猪", "1星测试猪#oldpig01", "pig_instances", "pig_instance_id", "pig"),
        ("美食", "测试菜#oldfood1", "food_instances", "food_instance_id", "food"),
    ],
)
async def test_rename_preserves_favorite_attributes_and_history_idempotently(
    world, kind, selector, table, id_column, instance
):
    w = world
    instance_id = w.pig_id if instance == "pig" else w.food_id
    async with w.db.transaction() as session:
        await session.execute(f"UPDATE {table} SET is_favorite=1 WHERE {id_column}=?", (instance_id,))
    before = dict(await w.db.fetch_one(f"SELECT * FROM {table} WHERE {id_column}=?", (instance_id,)))
    await w.grant(CODE_CHANGE_COUPON, 2)
    result = await w.use(f"编号修改卷 {kind} {selector} MixEd987", message_id="rename-one")
    repeat = await w.use(f"编号修改卷 {kind} {selector} MixEd987", message_id="rename-one")
    assert result.receipt.receipt_id == repeat.receipt.receipt_id
    assert "MIXED987" in result.view.text() and "此券剩余：1" in result.view.text()
    after = dict(await w.db.fetch_one(f"SELECT * FROM {table} WHERE {id_column}=?", (instance_id,)))
    assert {k: v for k, v in before.items() if k not in {"short_code", "updated_at"}} == {
        k: v for k, v in after.items() if k not in {"short_code", "updated_at"}
    }
    assert after["short_code"] == "MIXED987"
    assert await w.quantity(CODE_CHANGE_COUPON) == 1
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM reward_coupon_uses"))[0] == 1
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM audit_events WHERE action='reward-coupon-asset-renamed'"))[
        0
    ] == 1


async def test_rename_legacy_ticket_and_new_ticket_priority(world):
    w = world
    async with w.db.transaction() as session:
        await AchievementRepository().grant_reward(
            session,
            player_id=w.identity.player_id,
            reward_type="ticket",
            reward_id=LEGACY_CODE_CHANGE_COUPON,
            quantity=1,
            now=w.now,
        )
    await w.grant(CODE_CHANGE_COUPON)
    await w.service.reforge_identifier(
        replace(w.identity, message_id="new"), asset_kind="猪猪", old_code="oldpig01", new_code="first987"
    )
    assert await w.quantity(CODE_CHANGE_COUPON) == 0
    assert await w.quantity(LEGACY_CODE_CHANGE_COUPON) == 1
    await w.service.reforge_identifier(
        replace(w.identity, message_id="old"), asset_kind="猪猪", old_code="first987", new_code="second98"
    )
    assert await w.quantity(LEGACY_CODE_CHANGE_COUPON) == 0


@pytest.mark.parametrize("code", ["oldpig01", "oldfood1", "abc", "a" * 17, "中文编号", "abc_123"])
async def test_rename_invalid_or_occupied_codes_keep_coupon(world, code):
    w = world
    await w.grant(CODE_CHANGE_COUPON)
    with pytest.raises((DomainValidationError, AssetStateConflictError)):
        await w.use(f"编号修改券 猪猪 oldpig01 {code}")
    assert await w.quantity(CODE_CHANGE_COUPON) == 1
    assert (await w.db.fetch_one("SELECT short_code FROM pig_instances WHERE pig_instance_id=?", (w.pig_id,)))[
        0
    ] == "OLDPIG01"


async def test_rename_no_coupon_ownership_cross_scope_and_duplicate_names(world):
    w = world
    with pytest.raises(DomainValidationError, match="没有可用"):
        await w.use("编号修改券 猪猪 oldpig01 noCoupon")
    other = _identity(user_id="other")
    await w.grant(CODE_CHANGE_COUPON, identity=other)
    with pytest.raises(AssetStateConflictError):
        await w.use("编号修改券 猪猪 oldpig01 noOwner1", identity=other)
    other_scope = _identity(group_id="101")
    await w.grant(CODE_CHANGE_COUPON, identity=other_scope)
    with pytest.raises(AssetStateConflictError):
        await w.use("编号修改券 猪猪 oldpig01 noScope1", identity=other_scope)
    await w.grant(CODE_CHANGE_COUPON)
    await seed_pigs(w.db, w.identity, count=1)
    with pytest.raises(DomainValidationError, match="同名"):
        await w.use("编号修改券 猪猪 1星测试猪 choose12")
    await w.use("编号修改券 猪猪 1星测试猪#oldpig01 exact987")


@pytest.mark.parametrize("lock", ["trade", "dispatch", "tour", "battle", "tour-protection", "battle-protection"])
async def test_rename_respects_all_activity_and_trade_locks(world, lock):
    w = world
    await w.grant(CODE_CHANGE_COUPON)
    async with w.db.transaction() as session:
        if lock == "trade":
            await session.execute(
                "UPDATE pig_instances SET state='locked-for-trade' WHERE pig_instance_id=?", (w.pig_id,)
            )
        elif lock.endswith("protection"):
            table = "tour_protections" if lock.startswith("tour") else "battle_protections"
            await session.execute(
                f"INSERT INTO {table} VALUES(?,?,?,1)", (w.pig_id, w.identity.player_id, w.identity.scope.value)
            )
        else:
            await session.execute(
                "INSERT INTO asset_occupancies VALUES(?,?,?,?,?,9999999999999,?)",
                (w.pig_id, w.identity.player_id, w.identity.scope.value, lock, "activity", w.now),
            )
    with pytest.raises(AssetStateConflictError):
        await w.use("编号修改券 猪猪 oldpig01 never987")
    assert await w.quantity(CODE_CHANGE_COUPON) == 1


async def test_rename_audit_failure_rolls_back_code_coupon_and_receipt(world, monkeypatch):
    w = world
    await w.grant(CODE_CHANGE_COUPON)

    async def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(w.service.assets, "insert_audit_event", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await w.use("编号修改券 猪猪 oldpig01 failed99")
    assert await w.quantity(CODE_CHANGE_COUPON) == 1
    assert (await w.db.fetch_one("SELECT short_code FROM pig_instances WHERE pig_instance_id=?", (w.pig_id,)))[
        0
    ] == "OLDPIG01"
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM reward_coupon_uses"))[0] == 0


async def test_choice_preview_confirm_natural_attributes_and_no_gameplay_reward(world):
    w = world
    await w.grant(PIG_CHOICE_COUPON, 2)
    preview = await w.use("猪猪自选卷 6星测试猪")
    assert "等待确认" in preview.view.title and preview.view.pigs[0].image_relpath
    assert await w.quantity(PIG_CHOICE_COUPON) == 2
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM pig_instances"))[0] == 1
    result = await w.use("确认", message_id="confirm")
    assert "6星测试猪" in result.view.text()
    instance = await w.db.fetch_one("SELECT * FROM pig_instances WHERE template_id='six'")
    attrs = generate_pig_attributes(
        rarity=6,
        length_min=30,
        length_max=70,
        weight_min=20,
        weight_max=120,
        fat_profile="balanced",
        random_values=(0.5,) * 5,
    )
    assert (instance["size_value"], instance["weight_value"], instance["official_value"]) == (
        attrs.size_value,
        attrs.weight_value,
        attrs.official_value,
    )
    assert json.loads(instance["random_snapshot_json"])["source"] == "reward-pig-choice"
    assert tuple(
        await w.db.fetch_one("SELECT coin_balance,experience FROM players WHERE player_id=?", (w.identity.player_id,))
    ) == (0, 0)
    assert (
        await w.db.fetch_one("SELECT total_catches FROM player_statistics WHERE player_id=?", (w.identity.player_id,))
    )[0] == 0
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM command_receipts WHERE command_name='pig-catcher.catch'"))[
        0
    ] == 0
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM group_records"))[0] == 0
    assert (await w.db.fetch_one("SELECT acquired_count FROM pig_catalog_entries WHERE template_id='six'"))[0] == 1
    assert await w.quantity(PIG_CHOICE_COUPON) == 1
    assert len(w.service.random_source.values) == 95
    repeat = await w.use("确认", message_id="confirm")
    assert repeat.receipt.receipt_id == result.receipt.receipt_id
    assert len(w.service.random_source.values) == 95


@pytest.mark.parametrize("case", ["another-group", "disabled", "revoked", "consent"])
async def test_choice_revalidates_scope_enablement_and_authorization(world, case):
    w = world
    await w.grant(PIG_CHOICE_COUPON)
    if case == "another-group":
        with pytest.raises(DomainValidationError, match="当前群"):
            await w.use("猪猪自选券 另群六星猪")
    else:
        await w.use("猪猪自选券 6星测试猪")
        async with w.db.transaction() as session:
            if case == "disabled":
                await session.execute("UPDATE pig_templates SET enabled=0 WHERE template_id='six'")
            elif case == "revoked":
                await session.execute("UPDATE scope_pig_templates SET authorized=0 WHERE template_id='six'")
            else:
                await session.execute("UPDATE scope_pig_templates SET consent_status='revoked' WHERE template_id='six'")
        with pytest.raises(DomainValidationError, match="当前群"):
            await w.use("确认")
    assert await w.quantity(PIG_CHOICE_COUPON) == 1
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM pig_instances WHERE template_id IN('six','other-six')"))[0] == 0


async def test_choice_updated_template_and_same_name_ambiguity_fail_closed(world):
    w = world
    await w.grant(PIG_CHOICE_COUPON)
    await w.use("猪猪自选券 5星测试猪")
    async with w.db.transaction() as session:
        await session.execute("UPDATE pig_templates SET template_version=template_version+1 WHERE template_id='high'")
    with pytest.raises(DomainValidationError, match="模板已更新"):
        await w.use("确认")
    async with w.db.transaction() as session:
        await session.execute("UPDATE pig_templates SET display_name='1星测试猪' WHERE template_id='high'")
    with pytest.raises(DomainValidationError, match="不唯一"):
        await w.use("猪猪自选券 1星测试猪")
    await w.use("猪猪自选券 high")
    await w.use("确认")


@pytest.mark.parametrize("elapsed", [30, 31, 3600])
async def test_choice_expires_without_consumption(world, elapsed):
    w = world
    await w.grant(PIG_CHOICE_COUPON)
    await w.use("猪猪自选券 5星测试猪")
    w.clock.value += timedelta(seconds=elapsed)
    with pytest.raises(DomainValidationError, match="自动失效"):
        await w.use("确认")
    assert await w.quantity(PIG_CHOICE_COUPON) == 1
    await w.service.bag(w.identity)
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM item_coupon_choices"))[0] == 0


async def test_choice_cancel_replacement_cross_player_and_restart(world):
    w = world
    await w.grant(PIG_CHOICE_COUPON)
    await w.use("猪猪自选券 1星测试猪")
    await w.use("取消")
    with pytest.raises(DomainValidationError):
        await w.use("确认")
    await w.use("猪猪自选券 1星测试猪")
    await w.use("猪猪自选券 5星测试猪")
    other = _identity(user_id="other")
    with pytest.raises(DomainValidationError):
        await w.use("确认", identity=other)
    await w.db.close()
    await w.db.open()
    w.service = ItemBagService(w.db, clock=w.clock, random_source=SequenceRandom(*([0.5] * 5)))
    await w.use("确认", message_id="restart-confirm")
    await w.use("确认", message_id="restart-confirm")
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM pig_instances WHERE template_id='high'"))[0] == 1
    assert await w.quantity(PIG_CHOICE_COUPON) == 0


async def test_confirm_concurrency_and_fingerprint_conflict(world):
    w = world
    await w.grant(PIG_CHOICE_COUPON, 2)
    await w.use("猪猪自选券 5星测试猪")
    first, second = await asyncio.gather(w.use("确认", message_id="concurrent"), w.use("确认", message_id="concurrent"))
    assert first.receipt.receipt_id == second.receipt.receipt_id
    assert await w.quantity(PIG_CHOICE_COUPON) == 1
    with pytest.raises(ReceiptConflictError):
        await w.use("取消", message_id="concurrent")


async def test_choice_receipt_failure_rolls_back_pig_catalog_coupon_and_choice(world, monkeypatch):
    w = world
    await w.grant(PIG_CHOICE_COUPON)
    await w.use("猪猪自选券 5星测试猪")

    async def fail(*args, **kwargs):
        raise RuntimeError("receipt failure")

    monkeypatch.setattr(w.service.receipts, "reserve", fail)
    with pytest.raises(RuntimeError, match="receipt failure"):
        await w.use("确认")
    assert await w.quantity(PIG_CHOICE_COUPON) == 1
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM pig_instances WHERE template_id='high'"))[0] == 0
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM pig_catalog_entries WHERE template_id='high'"))[0] == 0
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM item_coupon_choices"))[0] == 1
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM reward_coupon_uses"))[0] == 0


async def test_private_replay_hides_revoked_image(world):
    w = world
    await w.grant(PIG_CHOICE_COUPON)
    first = await w.use("猪猪自选券 6星测试猪", message_id="private-preview")
    assert first.view.pigs[0].image_relpath
    async with w.db.transaction() as session:
        await session.execute("UPDATE scope_pig_templates SET authorized=0 WHERE template_id='six'")
    again = await w.use("猪猪自选券 6星测试猪", message_id="private-preview")
    assert again.view.pigs[0].image_relpath == ""


async def test_coupon_ledgers_cannot_be_mutated(world):
    w = world
    await w.grant(CODE_CHANGE_COUPON)
    await w.use("编号修改券 猪猪 oldpig01 audit123")
    for table in ("reward_coupon_grants", "reward_coupon_uses"):
        for operation in (f"DELETE FROM {table}", f"UPDATE {table} SET created_at='tomorrow'"):
            with pytest.raises(sqlite3.IntegrityError, match="不可改写"):
                async with w.db.transaction() as session:
                    await session.execute(operation)


async def test_released_old_code_reusable_after_rename(world):
    w = world
    await w.grant(CODE_CHANGE_COUPON, 2)
    await w.use("编号修改券 猪猪 oldpig01 newpig09")
    await w.use("编号修改券 美食 oldfood1 oldpig01")
    async with w.db.transaction(immediate=False) as session:
        assert await AssetCodeRepository.code_is_occupied(session, "oldpig01")
        assert not await AssetCodeRepository.code_is_occupied(session, "oldfood1")
    assert (await w.db.fetch_one("SELECT short_code FROM food_instances WHERE food_instance_id=?", (w.food_id,)))[
        0
    ] == "OLDPIG01"
