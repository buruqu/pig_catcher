"""巡演纯规则、确认、养成、跨群隔离与原子联演的离线验收。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import uuid4

import pytest

from pig_catcher.commands.tour import TourRequest, parse_tour_request
from pig_catcher.domain.dispatch import MATERIAL_SCALE, MATERIALS
from pig_catcher.domain.enums import AssetKind
from pig_catcher.domain.errors import AssetStateConflictError, MigrationError, PigCatcherError, ReceiptConflictError
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.tour import canonical_members, forecast_route, score_stage, validate_formation
from pig_catcher.domain.tour_catalog import (
    CHARACTERS,
    MAIN_FORMS,
    SCORE_CAPS,
    THEME_EMBLEMS,
    THEMES,
    TOOLS,
    TourError,
    default_plan,
    training_level,
)
from pig_catcher.domain.tour_views import TourView
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.infrastructure.migrations import MIGRATIONS
from pig_catcher.infrastructure.repositories import EconomyRepository, GameplayRepository
from pig_catcher.infrastructure.repositories.activity_locks import require_unoccupied, unoccupied_clause
from pig_catcher.infrastructure.repositories.materials import MaterialRepository
from pig_catcher.services.tour import TourService

from .test_dispatch import NOW, seed_pigs
from .test_gameplay import MutableClock, _database_with_catalog, _pig_entry


def character(identity: str):
    return next(CHARACTERS[key] for key in MAIN_FORMS if CHARACTERS[key].identity == identity)


def pure_members(ids=("kasumi", "tomoe", "layer"), *, full=False):
    return [
        {
            "pig_instance_id": f"pig-{i}",
            "template_id": character(c).template_id,
            "name": character(c).name,
            "training_exp": 2200 if full else 0,
            "rapport": 30 if full else 0,
            "branch": "",
            "rarity": 5,
        }
        for i, c in enumerate(ids)
    ]


@dataclass
class TourWorld:
    db: object
    clock: MutableClock
    service: TourService
    identity: CommandIdentity

    async def send(self, text="", section="tour", *, identity=None, message_id=None):
        return await self.service.execute(
            replace(identity or self.identity, message_id=message_id or uuid4().hex),
            parse_tour_request(text, section=section),
        )

    async def form(self, identity=None, ids=("kasumi", "tomoe", "layer"), slot=1):
        member = identity or self.identity
        for c in ids:
            await seed_pigs(self.db, member, template_id=character(c).template_id, count=1)
        await self.send("创建 测试混编团", "band", identity=member)
        await self.send(f"编队 {slot} " + "、".join(character(c).name for c in ids), "band", identity=member)
        await self.send("确认", identity=member)

    async def fund(self, identity=None):
        member = identity or self.identity
        async with self.db.transaction() as session:
            await EconomyRepository().apply_currency_change(
                session,
                player_id=member.player_id,
                scope_id=member.scope.value,
                amount=10000,
                reason_code="test",
                reason_text="offline",
                source_object_type="test",
                source_object_id="fixture",
                ledger_entry_id=uuid4().hex,
                idempotency_key=uuid4().hex,
                now=NOW.isoformat(),
            )
            for material in MATERIALS:
                await MaterialRepository().change(
                    session,
                    player_id=member.player_id,
                    scope_id=member.scope.value,
                    material_id=material,
                    delta_units=1000 * MATERIAL_SCALE,
                    source_kind="test",
                    source_id="fixture",
                    entry_key=uuid4().hex,
                    now=NOW.isoformat(),
                )


@pytest.fixture
async def world(tmp_path):
    entries = [_pig_entry(key, rarity=5, display_name=CHARACTERS[key].name) for key in MAIN_FORMS]
    entries.append(_pig_entry("pig-bandori-viola-green-tea", rarity=6, display_name="绿茶猪", group_id="100"))
    entries.append(_pig_entry("low", rarity=1, display_name="低星测试猪"))
    db = await _database_with_catalog(tmp_path, entries)
    clock = MutableClock(NOW)
    identity = CommandIdentity(ScopeKey("qq", "100"), "stream-100", "200", "巡演测试员", "fixture", "隔离测试群")
    world = TourWorld(db, clock, TourService(db, clock=clock, seed_factory=lambda: "tour-fixed-seed"), identity)
    await world.form()
    yield world
    await db.close()


def test_catalog_identities_and_non_probability_rules():
    assert len(MAIN_FORMS) == 47
    assert len({CHARACTERS[key].identity for key in MAIN_FORMS}) == 45
    assert sum(SCORE_CAPS.values()) == 100
    assert len(THEMES) == 9 and len(TOOLS) == 4
    assert training_level(0) == 0 and training_level(39) == 0 and training_level(40) == 1
    assert training_level(2199) == 9 and training_level(2200) == training_level(100000) == 10
    assert len({CHARACTERS[key].signature.name for key in MAIN_FORMS}) == 47


@pytest.mark.parametrize(
    "ids", [("kasumi", "tomoe", "layer"), ("tomoe", "ako", "layer"), ("tomori", "soyo", "taki", "sakiko", "mutsumi")]
)
def test_mixed_three_and_double_drums_can_reach_ss(ids):
    members = pure_members(ids, full=True)
    plan = default_plan()
    result = score_stage(members, plan, equipment=5, song_plays={song: 10 for song in plan["songs"]})
    assert result["score"] >= 92
    assert result["grade"] == "SS"
    assert all(0 <= result["components"][key] <= cap for key, cap in SCORE_CAPS.items())
    changed = [{**m, "rarity": 1, "official_value": 10**12, "weight": 0.01} for m in members]
    assert (
        score_stage(changed, plan, equipment=5, song_plays={s: 10 for s in plan["songs"]})["score"] == result["score"]
    )


def test_duplicate_forms_do_not_multiply_and_center_must_be_melody():
    members = pure_members()
    duplicate = {**members[0], "pig_instance_id": "duplicate", "training_exp": 2200}
    plan = default_plan()
    assert score_stage(members + [duplicate], plan)["score"] == score_stage(members, plan)["score"]
    assert len(canonical_members(members + [duplicate])) == 3
    with pytest.raises(TourError):
        validate_formation(members, members[1]["pig_instance_id"])


@pytest.mark.parametrize("seed", [str(i) for i in range(8)])
def test_random_bounded_reproducible_and_preview_never_random(seed):
    members = pure_members(full=True)
    a = score_stage(members, default_plan(), seed=seed)
    assert a == score_stage(members, default_plan(), seed=seed)
    assert -3 <= a["variation_raw"] <= 3 and 0 <= a["score"] <= 100
    assert score_stage(members, default_plan())["variation"] == 0


@pytest.mark.parametrize(
    "text,section,action",
    [
        ("", "band", "band"),
        ("编队 2 香澄猪、巴巴猪、LAYER猪", "band", "roster"),
        ("编排 1 1、2、3", "tour", "setlist"),
        ("器具 2 无", "tour", "tool"),
        ("高光 1 自动", "tour", "highlights"),
        ("收藏 2", "journal", "collections"),
        ("T12345678ab", "journal", "detail"),
        ("接受", "joint", "joint_accept"),
    ],
)
def test_parser(text, section, action):
    assert parse_tour_request(text, section=section).action == action


@pytest.mark.parametrize(
    "text,section",
    [
        ("编排 0 1、2、3", "tour"),
        ("编排 1 1、1、2", "tour"),
        ("高光 1 1、2、3", "tour"),
        ("路线 街头舞台", "tour"),
        ("编队 9 a、b、c", "band"),
        ("@只有昵称", "joint"),
        ("制作 备用线缆 -1", "band"),
        ("0", "journal"),
    ],
)
def test_parser_rejects_unsafe_inputs(text, section):
    with pytest.raises(TourError):
        parse_tour_request(text, section=section)


async def test_full_tour_growth_rewards_and_receipt_replay(world):
    first = await world.send("一键")
    assert "确认" in first.view.title and len(first.view.scorecards) == 3
    before = await world.db.fetch_one(
        "SELECT tickets,fans FROM tour_profiles WHERE player_id=?", (world.identity.player_id,)
    )
    result = await world.send("确认", message_id="whole-tour")
    assert "三站落幕" in result.view.title and len(result.view.scorecards) == 3
    assert result.view == TourView.from_payload(result.view.payload())
    replay = await world.send("确认", message_id="whole-tour")
    assert replay.receipt == result.receipt and replay.view == result.view
    after = await world.db.fetch_one(
        "SELECT tickets,fans FROM tour_profiles WHERE player_id=?", (world.identity.player_id,)
    )
    assert after[0] == before[0] - 1 and after[1] > before[1]
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_stages"))[0] == 3
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_proficiency WHERE experience=60"))[0] == 3
    assert (
        await world.db.fetch_one(
            "SELECT COUNT(*) FROM tour_contributions WHERE natural_exp=60 AND practice_exp=0 AND stages=3"
        )
    )[0] == 3
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_song_progress WHERE plays=3"))[0] == 3
    assert (
        await world.db.fetch_one(
            "SELECT COUNT(*) FROM activity_facts WHERE source_type='tour' AND subevent_id='completed'"
        )
    )[0] == 1
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM asset_occupancies"))[0] == 0
    async with world.db.transaction() as session:
        assert await MaterialRepository().reconcile(session) == []


async def test_preview_free_no_rng_no_progress_and_zero_tickets(world):
    world.service.seed_factory = lambda: (_ for _ in ()).throw(AssertionError("preview used RNG"))
    before = (await world.db.fetch_one("SELECT COUNT(*) FROM activity_facts"))[0]
    for _ in range(2):
        view = (await world.send("排练")).view
        assert len(view.scorecards) == 3
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM activity_facts"))[0] == before
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_proficiency"))[0] == 0
    async with world.db.transaction() as session:
        await world.service.repository.ticket_change(
            session,
            world.identity.player_id,
            -2,
            key="consume",
            reason="test",
            source="test",
            now_ms=int(NOW.timestamp() * 1000),
        )
    assert (await world.send("排练")).receipt is None
    with pytest.raises(TourError, match="档期"):
        await world.send("出发")


async def test_tickets_daily_cap_preserved_after_archive(world):
    world.clock.value += timedelta(days=20)
    await world.send("", "band")
    row = await world.db.fetch_one("SELECT tickets FROM tour_profiles WHERE player_id=?", (world.identity.player_id,))
    assert row[0] == 7
    await world.send("解散", "band")
    await world.send("确认")
    await world.send("创建 又见面了", "band")
    assert (
        await world.db.fetch_one("SELECT tickets FROM tour_profiles WHERE player_id=?", (world.identity.player_id,))
    )[0] == 7
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_ticket_ledger WHERE reason='initial'"))[0] == 1


async def test_sequential_restart_same_as_oneclick(world):
    await world.send("出发")
    await world.send("确认")
    await world.send("继续")
    assert (await world.db.fetch_one("SELECT fans FROM tour_profiles"))[0] == 0
    world.service = TourService(world.db, clock=world.clock, seed_factory=lambda: "tour-fixed-seed")
    await world.send("继续")
    await world.send("继续")
    first = json.loads((await world.db.fetch_one("SELECT summary_json FROM tour_runs"))[0])
    partner = replace(world.identity, user_id="partner", display_name="一键测试员")
    await world.form(partner)
    await world.send("一键", identity=partner)
    await world.send("确认", identity=partner)
    second = json.loads(
        (await world.db.fetch_one("SELECT summary_json FROM tour_runs WHERE player_id=?", (partner.player_id,)))[0]
    )
    for a, b in zip(first["stages"], second["stages"], strict=True):
        assert (a["score"], a["components"], a["variation"], a["plan"]) == (
            b["score"],
            b["components"],
            b["variation"],
            b["plan"],
        )
    assert (first["fans"], first["coins"]) == (second["fans"], second["coins"])


async def test_confirm_timeout_changed_roster_and_message_conflict(world):
    await world.send("出发")
    world.clock.value += timedelta(seconds=121)
    assert "过期" in (await world.send("确认")).view.title
    await world.send("出发", message_id="start")
    with pytest.raises(ReceiptConflictError):
        await world.service.execute(
            replace(world.identity, message_id="start"), TourRequest("start", {"unexpected": True})
        )
    await world.send("改名 新名", "band")
    with pytest.raises(TourError, match="改变"):
        await world.send("确认")
    assert (await world.db.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 2


async def test_practice_equipment_materials_style_tools(world):
    await world.fund()
    name = character("kasumi").name
    await world.send("练习 " + name, "band")
    result = await world.send("确认", message_id="practice")
    assert "50" in result.view.banner
    await world.send("确认", message_id="practice")
    with pytest.raises(TourError, match="今天"):
        await world.send("练习 " + name, "band")
    await world.send("器材 升级", "band")
    assert "升级" in (await world.send("确认")).view.title
    await world.send("制作 提示卡 3", "band")
    for i in range(1, 4):
        await world.send(f"器具 {i} 提示卡")
    await world.send("一键")
    await world.send("确认")
    assert (await world.db.fetch_one("SELECT quantity FROM tour_tools WHERE tool_id='cue'"))[0] == 0
    assert (await world.db.fetch_one("SELECT experience FROM tour_proficiency ORDER BY experience DESC"))[0] == 110
    async with world.db.transaction() as session:
        assert await MaterialRepository().reconcile(session) == []


async def test_members_protected_but_idle_dispatch_allowed_and_retire_preserves_xp(world):
    pig = await world.db.fetch_one(
        "SELECT * FROM pig_instances WHERE template_id=?", (character("kasumi").template_id,)
    )
    async with world.db.transaction() as session:
        with pytest.raises(AssetStateConflictError, match="乐队保护"):
            await require_unoccupied(session, pig["pig_instance_id"])
        assert (
            await session.fetch_all(
                f"SELECT * FROM pig_instances p WHERE p.owner_player_id=? {unoccupied_clause('pig', 'p')}",
                (world.identity.player_id,),
            )
            == []
        )
    with pytest.raises(sqlite3.IntegrityError, match="保护"):
        async with world.db.transaction() as session:
            await session.execute(
                "UPDATE pig_instances SET state='consumed' WHERE pig_instance_id=?", (pig["pig_instance_id"],)
            )
    from pig_catcher.commands.dispatch import parse_dispatch_request
    from pig_catcher.services.dispatch import DispatchService

    dispatch = DispatchService(world.db, clock=world.clock)
    await seed_pigs(world.db, world.identity, template_id="low", count=1)
    for text in ("编队 1 " + pig["display_name_snapshot"] + "、低星测试猪", "确认", "出发 1 青草近郊 4小时", "确认"):
        await dispatch.execute(replace(world.identity, message_id=uuid4().hex), parse_dispatch_request(text))
    with pytest.raises(TourError, match="活动"):
        await world.send("出发")
    world.clock.value += timedelta(hours=4)
    await world.send("出发")
    await world.send("确认")
    await world.send("继续")
    await world.send("解除保护 " + pig["display_name_snapshot"] + "#" + pig["short_code"], "band")
    await world.send("确认")
    assert (
        await world.db.fetch_one(
            "SELECT experience FROM tour_proficiency WHERE pig_instance_id=?", (pig["pig_instance_id"],)
        )
    )[0] == 20
    async with world.db.transaction() as session:
        await require_unoccupied(session, pig["pig_instance_id"])
    with pytest.raises(TourError, match="三|3"):
        await world.send("继续")


async def test_joint_consent_replay_and_same_group(world):
    partner = replace(world.identity, user_id="201", display_name="群友乙")
    await world.form(partner)
    invitation = TourRequest("joint_invite", {"target_user_id": "201"})
    await world.service.execute(replace(world.identity, message_id="invite"), invitation)
    assert (await world.db.fetch_one("SELECT SUM(tickets) FROM tour_profiles"))[0] == 4
    with pytest.raises(TourError, match="受邀者"):
        await world.send("接受", "joint")
    result = await world.send("接受", "joint", identity=partner, message_id="accept")
    assert len(result.view.scorecards) == 6
    assert "群友乙" in result.view.player_name
    assert (await world.db.fetch_one("SELECT SUM(tickets) FROM tour_profiles"))[0] == 2
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_stages"))[0] == 6
    await world.send("接受", "joint", identity=partner, message_id="accept")
    await world.send("接受", "joint", identity=partner)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_runs"))[0] == 2
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_joint_reservations"))[0] == 0
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM activity_facts WHERE subevent_id='joint-completed'"))[0] == 2


async def test_joint_stale_cancel_expiry_and_wrong_target(world):
    partner = replace(world.identity, user_id="201", display_name="群友乙")
    await world.form(partner)
    invite = TourRequest("joint_invite", {"target_user_id": "201"})
    await world.service.execute(replace(world.identity, message_id=uuid4().hex), invite)
    await world.send("改名 已改变", "band")
    result = await world.send("接受", "joint", identity=partner)
    assert "条件已改变" in result.view.title
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_runs"))[0] == 0
    await world.service.execute(replace(world.identity, message_id=uuid4().hex), invite)
    world.clock.value += timedelta(minutes=5)
    assert "没有待接受" in (await world.send("接受", "joint", identity=partner)).view.title
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_joint_reservations"))[0] == 0
    with pytest.raises(TourError):
        await world.service.execute(
            replace(world.identity, message_id=uuid4().hex), TourRequest("joint_invite", {"target_user_id": "nobody"})
        )


async def test_joint_atomic_rollback_if_second_band_fails(world, monkeypatch):
    partner = replace(world.identity, user_id="201", display_name="群友乙")
    await world.form(partner)
    await world.service.execute(
        replace(world.identity, message_id="invite"), TourRequest("joint_invite", {"target_user_id": "201"})
    )
    original = world.service.repository.play_stage

    async def fail_second(session, profile, run, **kwargs):
        if profile["player_id"] == partner.player_id:
            raise RuntimeError("injected")
        return await original(session, profile, run, **kwargs)

    monkeypatch.setattr(world.service.repository, "play_stage", fail_second)
    with pytest.raises(RuntimeError, match="injected"):
        await world.send("接受", "joint", identity=partner)
    assert (await world.db.fetch_one("SELECT SUM(tickets) FROM tour_profiles"))[0] == 4
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_runs"))[0] == 0
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_proficiency"))[0] == 0
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM asset_occupancies"))[0] == 0


@pytest.mark.parametrize(
    "scope",
    [
        ScopeKey("qq", "1092931381"),
        ScopeKey("qq", "237716658"),
        ScopeKey("qq-official", "5E5854406D0297D6FEAE696A13E3A339"),
        ScopeKey("qq-official", "9EA2810F378FBD7DC3219C56CEAB3520"),
    ],
)
async def test_four_scopes_independent_band_and_tickets(world, scope):
    other = replace(world.identity, scope=scope, stream_id="separate-stream")
    await world.form(other)
    await world.send("一键", identity=other)
    await world.send("确认", identity=other)
    assert (
        await world.db.fetch_one(
            "SELECT tickets,fans FROM tour_profiles WHERE player_id=?", (world.identity.player_id,)
        )
    )[0] == 2
    assert (await world.db.fetch_one("SELECT fans FROM tour_profiles WHERE player_id=?", (world.identity.player_id,)))[
        0
    ] == 0
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_runs WHERE scope_id=?", (scope.value,)))[0] == 1
    run = (await world.db.fetch_one("SELECT run_id FROM tour_runs WHERE scope_id=?", (scope.value,)))[0]
    with pytest.raises(TourError, match="本群"):
        await world.send(run, "journal")


async def test_next_stage_lineup_and_plan_changes_do_not_rewrite_completed_stage(world):
    await world.send("出发")
    await world.send("确认")
    await world.send("继续")
    before = (await world.db.fetch_one("SELECT snapshot_json FROM tour_stages WHERE stage_number=1"))[0]
    await seed_pigs(world.db, world.identity, template_id=character("ako").template_id, count=1)
    await world.send("编队 2 " + "、".join(character(c).name for c in ("tomoe", "ako", "layer")), "band")
    await world.send("确认")
    await world.send("主题 Afterglow")
    with pytest.raises(TourError, match="尚未"):
        await world.send("编排 1 3、2、1")
    second = await world.send("继续")
    assert "阵容已变更" in second.view.scorecards[0].note
    assert (await world.db.fetch_one("SELECT snapshot_json FROM tour_stages WHERE stage_number=1"))[0] == before
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM asset_occupancies"))[0] == 0
    await world.send("继续")


async def test_no_refund_on_abandon_or_repeat_and_partial_growth_kept(world):
    await world.send("出发")
    await world.send("确认")
    await world.send("继续")
    await world.send("结束")
    await world.send("确认", message_id="abandon")
    await world.send("确认", message_id="abandon")
    row = await world.db.fetch_one("SELECT tickets,fans FROM tour_profiles")
    assert tuple(row) == (1, 0)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_proficiency WHERE experience=20"))[0] == 3
    assert (await world.db.fetch_one("SELECT status FROM tour_runs"))[0] == "abandoned"
    with pytest.raises(TourError):
        await world.send("继续")


async def test_training_and_gear_insufficient_resources_atomic(world):
    with pytest.raises(TourError, match="不足"):
        await world.send("练习 1", "band")
    await world.fund()
    await world.send("器材 升级", "band")
    before = (await world.db.fetch_one("SELECT coin_balance FROM players"))[0]
    async with world.db.transaction() as session:
        await MaterialRepository().change(
            session,
            player_id=world.identity.player_id,
            scope_id=world.identity.scope.value,
            material_id="agility-fiber",
            delta_units=-1000 * MATERIAL_SCALE,
            source_kind="test",
            source_id="spend",
            entry_key="spend",
            now=NOW.isoformat(),
        )
    with pytest.raises(PigCatcherError, match="不足"):
        await world.send("确认")
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == before
    assert (await world.db.fetch_one("SELECT equipment FROM tour_profiles"))[0] == 0
    assert (await world.db.fetch_one("SELECT quantity FROM material_balances WHERE material_id='stage-components'"))[
        0
    ] == 1000


async def test_max_training_branch_free_first_then_paid_and_natural_contribution(world):
    await world.fund()
    pig = (
        await world.db.fetch_one(
            "SELECT pig_instance_id FROM pig_instances WHERE template_id=?", (character("kasumi").template_id,)
        )
    )[0]
    async with world.db.transaction() as session:
        await session.execute("INSERT INTO tour_proficiency VALUES(?,240,'')", (pig,))
    await world.send("风格 1 技术", "band")
    before = (await world.db.fetch_one("SELECT coin_balance FROM players"))[0]
    await world.send("确认")
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == before
    await world.send("风格 1 亲近", "band")
    await world.send("确认")
    assert (await world.db.fetch_one("SELECT coin_balance FROM players"))[0] == before - 20
    async with world.db.transaction() as session:
        await session.execute("UPDATE tour_proficiency SET experience=2200 WHERE pig_instance_id=?", (pig,))
    with pytest.raises(TourError, match="满级"):
        await world.send("练习 1", "band")
    await world.send("一键")
    await world.send("确认")
    assert (await world.db.fetch_one("SELECT experience FROM tour_proficiency WHERE pig_instance_id=?", (pig,)))[
        0
    ] == 2260
    assert (await world.db.fetch_one("SELECT natural_exp FROM tour_contributions WHERE pig_instance_id=?", (pig,)))[
        0
    ] == 60


async def test_trained_transferred_instance_keeps_level_not_old_owner_contribution(world):
    await world.fund()
    await world.send("练习 1", "band")
    await world.send("确认")
    pig = await world.db.fetch_one(
        "SELECT * FROM pig_instances WHERE template_id=?", (character("kasumi").template_id,)
    )
    await world.send("解除保护 1", "band")
    await world.send("确认")
    partner = replace(world.identity, user_id="successor")
    await world.send("创建 接棒团", "band", identity=partner)
    async with world.db.transaction() as session:
        assert await GameplayRepository().transfer_pig_owner(
            session, pig_instance_id=pig["pig_instance_id"], owner_player_id=partner.player_id, now=NOW.isoformat()
        )
        inherited = await world.service.repository.member(session, partner.player_id, pig["pig_instance_id"])
        assert inherited["training_exp"] == 50 and inherited["own_experience"] == 0 and inherited["rapport"] == 0
        protection = await session.fetch_one(
            "SELECT player_id,protected FROM tour_protections WHERE pig_instance_id=?", (pig["pig_instance_id"],)
        )
        assert tuple(protection) == (partner.player_id, 1)
    with pytest.raises(TourError, match="今天"):
        await world.send("练习 " + pig["display_name_snapshot"], "band", identity=partner)
    assert (
        pig["pig_instance_id"]
        not in (
            await world.db.fetch_one(
                "SELECT member_ids_json FROM tour_rosters WHERE player_id=?", (world.identity.player_id,)
            )
        )[0]
    )


async def test_guest_revocation_blocks_new_use_masks_history_and_allows_retire(world):
    guest_id = (await seed_pigs(world.db, world.identity, template_id="pig-bandori-viola-green-tea", count=1))[0]
    await world.send("客串 绿茶猪", "band")
    await world.send("确认")
    assert "绿茶猪" in (await world.send("", "band")).view.text()
    await world.send("一键")
    await world.send("确认", message_id="with-guest")
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_collections WHERE kind='客串纪念'"))[0] == 1
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_proficiency WHERE pig_instance_id=?", (guest_id,)))[
        0
    ] == 0
    async with world.db.transaction() as session:
        await session.execute(
            "UPDATE scope_pig_templates SET authorized=0,consent_status='revoked' "
            "WHERE template_id='pig-bandori-viola-green-tea'"
        )
    with pytest.raises(TourError, match="授权"):
        await world.send("出发")
    journal = await world.send("收藏 1", "journal")
    assert all(not pig.image_relpath for pig in journal.view.pigs if pig.template_id == "pig-bandori-viola-green-tea")
    await world.send("解除保护 绿茶猪", "band")
    await world.send("确认")
    assert (await world.db.fetch_one("SELECT guest_id FROM tour_profiles"))[0] is None
    await world.send("出发")


async def test_duplicate_message_concurrency_and_joints_cannot_overdraw(world):
    await world.send("一键")
    results = await asyncio.gather(*(world.send("确认", message_id="concurrent-finish") for _ in range(6)))
    assert all(r.receipt.idempotency_key == results[0].receipt.idempotency_key for r in results)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_runs"))[0] == 1
    assert (await world.db.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 1
    assert await world.db.fetch_all("PRAGMA foreign_key_check") == []
    assert await world.db.integrity_check() == ("ok",)


async def test_old_sale_gift_trade_and_cook_reject_protected_exact_pig(world):
    from pig_catcher.config.model import CookingSection, EconomySection, RankingSection, TradingSection
    from pig_catcher.services.economy import EconomyService
    from pig_catcher.services.social import SocialService

    await world.fund()
    pig = await world.db.fetch_one(
        "SELECT * FROM pig_instances WHERE template_id=?", (character("kasumi").template_id,)
    )
    selector = pig["display_name_snapshot"] + "#" + pig["short_code"]
    economy = EconomyService(world.db, CookingSection(), EconomySection(), clock=world.clock)
    social = SocialService(world.db, TradingSection(), RankingSection(), clock=world.clock)
    partner = replace(world.identity, user_id="recipient", message_id="recipient")
    for operation in (
        lambda: economy.sell_pig(replace(world.identity, message_id="sell"), selector),
        lambda: economy.cook(replace(world.identity, message_id="cook"), selector),
        lambda: social.gift(
            replace(world.identity, message_id="gift"), partner, asset_kind=AssetKind.PIG, selector_text=selector
        ),
        lambda: social.create_trade(
            replace(world.identity, message_id="trade"),
            partner,
            asset_kind=AssetKind.PIG,
            selector_text=selector,
            price=1,
        ),
    ):
        with pytest.raises(AssetStateConflictError, match="乐队保护"):
            await operation()
    async with world.db.transaction() as session:
        repo = EconomyRepository()
        kwargs = {
            "pig_instance_id": pig["pig_instance_id"],
            "player_id": world.identity.player_id,
            "scope_id": world.identity.scope.value,
            "now": NOW.isoformat(),
        }
        assert not await repo.consume_pig_for_cooking(session, **kwargs)
        assert not await repo.sell_pig(session, **kwargs)
    assert (
        await world.db.fetch_one("SELECT state FROM pig_instances WHERE pig_instance_id=?", (pig["pig_instance_id"],))
    )[0] == "active"


async def test_favorite_protection_separate_and_names_choose_lowest_value(world):
    extra = (await seed_pigs(world.db, world.identity, template_id=character("kasumi").template_id, count=1, value=1))[
        0
    ]
    async with world.db.transaction() as session:
        await session.execute("UPDATE pig_instances SET is_favorite=1 WHERE pig_instance_id=?", (extra,))
    await world.send("编队 2 星星猪、巴巴猪、LAYER猪", "band")
    pending = json.loads((await world.db.fetch_one("SELECT payload_json FROM tour_pending"))[0])
    assert pending["members"][0]["pig_instance_id"] != extra
    await world.send("取消")
    code = (await world.db.fetch_one("SELECT short_code FROM pig_instances WHERE pig_instance_id=?", (extra,)))[0]
    await world.send(f"编队 2 星星猪#{code}、巴巴猪、LAYER猪", "band")
    await world.send("确认")
    await world.send(f"解除保护 星星猪#{code}", "band")
    await world.send("确认")
    assert (await world.db.fetch_one("SELECT is_favorite FROM pig_instances WHERE pig_instance_id=?", (extra,)))[0] == 1


async def test_tour_receipt_does_not_add_catches_or_weekly_catch_value(world):
    from pig_catcher.services.achievements import AchievementService
    from pig_catcher.services.weekly_competitions import WeeklyCompetitionService

    await world.send("一键")
    receipt = (await world.send("确认")).receipt
    assert not await WeeklyCompetitionService(world.db, clock=world.clock).process_receipt(receipt)
    achievements = AchievementService(world.db, clock=world.clock)
    await achievements.process_receipt(receipt)
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM weekly_competition_entries"))[0] == 0
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM achievement_definition_snapshots"))[0] == 82


async def test_schema_37_migration_preserves_existing_assets_materials_and_economy(world, tmp_path):
    await world.fund()
    path = tmp_path / "migration-from-37.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT UNIQUE,applied_at TEXT)")
    for migration in MIGRATIONS:
        if migration.version > 37:
            break
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration.version, migration.name, "test"))
    connection.execute("PRAGMA user_version=37")
    original = {}
    for table in (
        "scopes",
        "players",
        "asset_manifest_imports",
        "pig_templates",
        "scope_pig_templates",
        "pig_instances",
        "currency_ledger",
        "material_balances",
        "material_ledger",
    ):
        rows = await world.db.fetch_all(f"SELECT * FROM {table} ORDER BY rowid")
        original[table] = [tuple(row) for row in rows]
        if rows:
            connection.executemany(
                f"INSERT INTO {table} VALUES(" + ",".join("?" for _ in rows[0]) + ")", original[table]
            )
    original_schema = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
    connection.commit()
    connection.close()
    migrated = PigCatcherDatabase(path)
    await migrated.open()
    try:
        assert await migrated.schema_version() == 38
        schema = {
            r["name"]: r["sql"]
            for r in await migrated.fetch_all("SELECT name,sql FROM sqlite_master WHERE type='table'")
        }
        assert all(schema[name] == sql for name, sql in original_schema.items())
        for table, rows in original.items():
            assert [tuple(row) for row in await migrated.fetch_all(f"SELECT * FROM {table} ORDER BY rowid")] == rows
        assert (await migrated.fetch_one("SELECT COUNT(*) FROM tour_profiles"))[0] == 0
        assert await migrated.integrity_check() == ("ok",)
        assert await migrated.fetch_all("PRAGMA foreign_key_check") == []
    finally:
        await migrated.close()


async def test_immutable_tour_ledgers_and_foreign_roster_guards(world):
    await world.send("一键")
    await world.send("确认")
    for sql in (
        "UPDATE tour_stages SET occurred_ms=0",
        "DELETE FROM tour_stages",
        "DELETE FROM tour_ticket_ledger",
        "UPDATE tour_runs SET stage_count=0",
        "DELETE FROM tour_collections",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            async with world.db.transaction() as session:
                await session.execute(sql)
    other = replace(world.identity, user_id="other-owner")
    other_pig = (await seed_pigs(world.db, other, template_id=character("kasumi").template_id, count=1))[0]
    with pytest.raises(sqlite3.IntegrityError, match="归属|本人"):
        async with world.db.transaction() as session:
            await session.execute(
                "UPDATE tour_rosters SET member_ids_json=? WHERE player_id=?",
                (json.dumps([other_pig]), world.identity.player_id),
            )


async def test_natural_dispatch_material_fact_linked_to_paid_equipment_and_use(world):
    await world.fund()
    async with world.db.transaction() as session:
        await MaterialRepository().change(
            session,
            player_id=world.identity.player_id,
            scope_id=world.identity.scope.value,
            material_id="stage-components",
            delta_units=20 * MATERIAL_SCALE,
            source_kind="dispatch-base",
            source_id="proof-trip",
            entry_key="proof-components",
            now=NOW.isoformat(),
        )
    await world.send("器材 升级", "band")
    await world.send("确认")
    fact = json.loads(
        (await world.db.fetch_one("SELECT payload_json FROM activity_facts WHERE subevent_id='equipment-upgraded'"))[0]
    )
    assert (
        fact["natural_stage_components_before_units"] == 20 * MATERIAL_SCALE and fact["costs"]["stage-components"] == 20
    )
    await world.send("一键")
    await world.send("确认")
    stages = await world.db.fetch_all("SELECT snapshot_json FROM tour_stages")
    assert all(json.loads(row[0])["equipment"] == 1 for row in stages)


async def test_ticket_midnight_no_cap_overflow_or_rename_reset(world):
    # NOW为北京时间08:00，跨午夜才补档期，而不是按距离上次使用满24h。
    world.clock.value = NOW.replace(hour=15, minute=59)
    await world.send("", "band")
    assert (await world.db.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 2
    world.clock.value += timedelta(minutes=1)
    await world.send("", "band")
    assert (await world.db.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 3
    await world.send("改名 午夜团", "band")
    assert (await world.db.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 3


@pytest.mark.parametrize(
    "object_type,name",
    [
        ("TRIGGER", "tour_protected_pig_no_dispose"),
        ("TRIGGER", "tour_profile_scope_insert"),
        ("TRIGGER", "tour_runs_scope_update"),
        ("TRIGGER", "tour_ticket_ledger_no_update"),
        ("TRIGGER", "tour_joint_reservation_owner"),
        ("INDEX", "idx_tour_active_player"),
        ("TABLE", "tour_pending"),
    ],
)
async def test_current_schema_rejects_missing_tour_tables_or_guards(world, tmp_path, object_type, name):
    path = tmp_path / "missing-tour-guard.sqlite3"
    await world.db.backup_to(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP {object_type} {name}")  # 仅离线夹具，固定参数，不接受用户SQL。
    database = PigCatcherDatabase(path)
    try:
        with pytest.raises(MigrationError, match=name):
            await database.open()
    finally:
        await database.close()


async def test_two_database_instances_cannot_duplicate_confirmation_payout(world):
    await world.send("一键")
    second = PigCatcherDatabase(world.db.path)
    await second.open()
    try:
        service = TourService(second, clock=world.clock, seed_factory=lambda: "never-retry-random")
        identity = replace(world.identity, message_id="same-confirm-across-connections")
        results = await asyncio.gather(
            world.service.execute(identity, TourRequest("confirm", {})),
            service.execute(identity, TourRequest("confirm", {})),
        )
        assert results[0].receipt.idempotency_key == results[1].receipt.idempotency_key
        assert results[0].view == results[1].view
        assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_runs"))[0] == 1
        assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_stages"))[0] == 3
        assert (await world.db.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 1
        assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_ticket_ledger WHERE delta<0"))[0] == 1
    finally:
        await second.close()


async def test_backup_restores_partial_tour_with_same_seed_and_settlement(world, tmp_path):
    await world.send("出发")
    await world.send("确认")
    await world.send("继续")
    backup = tmp_path / "partial-tour.sqlite3"
    await world.db.backup_to(backup)
    completed = await world.send("一键", message_id="continue-after-backup")
    restored = PigCatcherDatabase(backup)
    await restored.open()
    try:
        service = TourService(restored, clock=world.clock, seed_factory=lambda: "should-not-change")
        result = await service.execute(
            replace(world.identity, message_id="continue-after-backup"), TourRequest("all", {})
        )
        assert result.view == completed.view
        assert (await restored.fetch_one("SELECT tickets FROM tour_profiles"))[0] == 1
        assert (await restored.fetch_one("SELECT COUNT(*) FROM asset_occupancies"))[0] == 0
        assert await restored.integrity_check() == ("ok",)
        assert await restored.fetch_all("PRAGMA foreign_key_check") == []
    finally:
        await restored.close()


async def test_theme_cosmetics_require_actual_qualified_three_stage_completion(world):
    templates_before = [
        tuple(row) for row in await world.db.fetch_all("SELECT * FROM pig_templates ORDER BY template_id")
    ]
    assert set(THEME_EMBLEMS) == {theme.theme_id for theme in THEMES}
    assert len(set(THEME_EMBLEMS.values())) == 9
    with pytest.raises(TourError, match="尚未解锁"):
        await world.send("队徽 星星落进练习室", "band")
    async with world.db.transaction() as session:
        for row in await session.fetch_all("SELECT pig_instance_id FROM pig_instances"):
            await session.execute("INSERT INTO tour_proficiency VALUES(?,2200,'')", (row[0],))
            await session.execute(
                "INSERT INTO tour_contributions VALUES(?,?,2200,0,30)", (world.identity.player_id, row[0])
            )
        for song_id in default_plan()["songs"]:
            await session.execute("INSERT INTO tour_song_progress VALUES(?,?,10)", (world.identity.player_id, song_id))
    await world.send("一键")
    result = await world.send("确认")
    assert all(card.grade in {"S", "SS"} for card in result.view.scorecards)
    await world.send("队徽 星星落进练习室", "band")
    dressed = (await world.send("服装 星星落进练习室", "band")).view
    assert dressed.costume == "星星落进练习室" and dressed.emblem == THEME_EMBLEMS["poppin"]
    assert [
        tuple(row) for row in await world.db.fetch_all("SELECT * FROM pig_templates ORDER BY template_id")
    ] == templates_before
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM tour_collections WHERE kind='主题服装'"))[0] == 1
    assert (await world.send("服装 默认", "band")).view.costume == ""


@pytest.mark.parametrize("theme", [t.theme_id for t in THEMES])
def test_all_nine_themes_have_natural_ss_path_and_fixed_caps(theme):
    members = pure_members(full=True)
    plan = default_plan(theme)
    plan["venue"] = "dome"
    results = forecast_route(
        members,
        [plan, plan, plan],
        equipment=5,
        song_plays={s: 10 for s in plan["songs"]},
        center=members[0]["pig_instance_id"],
    )
    assert all(s["grade"] == "SS" and s["theme_qualified"] for s in results)


@pytest.mark.parametrize("template", MAIN_FORMS)
def test_every_registered_signature_evaluates_without_undefined_condition(template):
    char = CHARACTERS[template]
    members = pure_members()
    # 使用该角色加上能覆盖职能的香澄、LAYER、巴，最多四人。
    if char.identity not in {"kasumi", "tomoe", "layer"}:
        members.append(
            {"pig_instance_id": "extra", "template_id": template, "name": char.name, "training_exp": 0, "rapport": 0}
        )
    else:
        index = {"kasumi": 0, "tomoe": 1, "layer": 2}[char.identity]
        members[index]["template_id"] = template
    plan = default_plan(char.band)
    plan["highlights"] = [char.identity]
    first = score_stage(members, plan, stage_number=1, seed="all-characters")
    second = score_stage(members, plan, stage_number=2, previous=first, seed="all-characters")
    assert second["highlights"][0]["name"] == char.signature.name
    assert all(second["components"][key] <= cap for key, cap in SCORE_CAPS.items())
