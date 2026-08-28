"""Fourth round: all 48 predicates, real activity settlement and replay safety."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from pig_catcher.domain.achievements import ACHIEVEMENT_BY_ID, ACHIEVEMENT_DEFINITIONS, LEGACY_ACHIEVEMENT_DEFINITIONS
from pig_catcher.domain.activity_achievements import ACTIVITY_IDS, ACTIVITY_REWARDS, FIXED_SETS, LEGACY_REGULAR_IDS
from pig_catcher.domain.activity_progress import MOVE_ALIASES, THEME_ALIASES, VENUE_ALIASES, progress, reduce_fact
from pig_catcher.domain.battle import new_state
from pig_catcher.domain.battle_catalog import FIGHTERS_BY_ID
from pig_catcher.domain.dispatch import MATERIAL_SCALE
from pig_catcher.domain.tour import score_stage
from pig_catcher.domain.tour_catalog import CHARACTERS, THEMES, VENUES, default_plan
from pig_catcher.infrastructure.repositories.materials import MaterialRepository
from pig_catcher.services.achievements import AchievementService

from .test_battle import world as _battle_fixture
from .test_dispatch import world as dispatch_world  # noqa: F401
from .test_tour import character, pure_members  # noqa: F401
from .test_tour import world as _tour_fixture

battle_world = _battle_fixture
tour_world = _tour_fixture


def fact(state, system, event, data, *, source="source", at=1, player="p"):
    reduce_fact(
        state,
        {"source_type": system, "subevent_id": event, "source_id": source, "occurred_ms": at, "player_id": player},
        data,
    )


def trip(*, names=("pig-r2-tiny",), hours=24, region="echo-mine", start=0, slot=1, tool="", rarity=2):
    return {
        "starts_ms": start,
        "snapshot": {
            "slot": slot,
            "hours": hours,
            "region_id": region,
            "tool_id": tool,
            "members": [
                {"pig_instance_id": f"p{i}-{name}", "template_id": name, "rarity": rarity}
                for i, name in enumerate(names)
            ],
        },
        "progress": {"settled_hours": hours, "rewards": []},
    }


def tour_data(ids=("tomoe", "ako", "layer"), *, theme="poppin", grade="S", at=1000):
    members = pure_members(ids, full=True)
    for m in members:
        m["pig_instance_id"] = "p0-" + m["template_id"]
    plan = default_plan(theme)
    plan["highlights"] = list(ids[:2])
    stages = []
    for number in (1, 2, 3):
        stage = score_stage(members, plan, equipment=1, stage_number=number, seed="test")
        stage.update(grade=grade, occurred_ms=at, theme_qualified=True)
        stages.append(stage)
    return {"stages": stages, "joint_id": ""}


def match_data(fighter="sukuna", *, heavy=0, core=0):
    snapshots = [
        {
            "player_id": pid,
            "pig_instance_id": "battle-pig-" + pid,
            "fighter_id": fighter,
            "level": 0,
            "trait_bonus": 0,
            "tool_id": "",
        }
        for pid in ("p", "q")
    ]
    state = new_state(snapshots)
    state["sides"][0].update(risk=heavy, core=core)
    return {"natural_end": True, "status": "completed", "winner_id": "p", "state": state}


def test_runtime_pack_matches_reviewed_design_and_freezes_old_denominators():
    design = json.loads(
        (Path(__file__).parents[1] / "docs/design-data/three-systems-achievements-r1.json").read_text(encoding="utf-8")
    )
    assert design["runtime_registration_allowed"] is False
    assert len(LEGACY_ACHIEVEMENT_DEFINITIONS) == 82
    assert len(LEGACY_REGULAR_IDS) == 49
    assert len(ACHIEVEMENT_DEFINITIONS) == 130
    assert len(ACTIVITY_IDS) == 48
    assert sum(d.points for d in ACHIEVEMENT_DEFINITIONS) == 4040
    added = [ACHIEVEMENT_BY_ID[e["achievement_id"]] for e in design["entries"]]
    assert sum(d.points for d in added) == 1665
    assert sum(d.hidden for d in added) == 14
    assert sum(r.quantity for d in added for r in d.rewards if r.reward_type == "coin") == 68900
    for entry, actual in zip(design["entries"], added, strict=True):
        assert actual.name == entry["name"] and actual.condition.metric == entry["condition"]["metric"]
        assert [(r.reward_id, r.quantity) for r in actual.rewards] == [
            (r["reward_id"], r["quantity"]) for r in entry["rewards"]
        ]
    assert len([r for r in ACTIVITY_REWARDS.values() if r["kind"] in {"frame", "badge", "title"}]) == 38
    for fighter in FIGHTERS_BY_ID.values():
        assert set(MOVE_ALIASES[fighter.fighter_id]) == {m.move_id for m in fighter.moves}
        assert set(MOVE_ALIASES[fighter.fighter_id].values()) == FIXED_SETS[f"battle-{fighter.fighter_id}-moves-v1"]
    # 后续主题不能追溯扩大旧版成就的固定九主题条件。
    fixed_themes = FIXED_SETS["tour-band-themes-v1"]
    assert len(fixed_themes) == 9
    assert fixed_themes < {THEME_ALIASES.get(t.theme_id, t.theme_id) for t in THEMES}
    assert "yumemita" not in fixed_themes
    assert {VENUE_ALIASES.get(v.venue_id, v.venue_id) for v in VENUES} == FIXED_SETS["tour-venues-v1"]


def complete_state(code):
    state = {}
    if code in {"D01", "D05", "D06", "D09", "D11", "D13", "D14"}:
        if code == "D13":
            for n, region in enumerate(("echo-mine", "old-workshop", "windbell-forest")):
                fact(
                    state,
                    "dispatch",
                    "completed",
                    trip(names=("a", "b", "c"), hours=8, region=region, start=n * 1000, slot=n + 1),
                    source=str(n),
                )
        elif code == "D14":
            fact(
                state,
                "dispatch",
                "completed",
                trip(names=("pig-r2-tiny", "pig-r2-elephant", "c"), region="windbell-forest"),
            )
        else:
            for n in range(20 if code in {"D05", "D06"} else 8 if code == "D09" else 1):
                names = ("a", "b", "c") if code == "D05" else (f"pig{n}",) if code == "D06" else ("pig-r2-tiny",)
                fact(state, "dispatch", "completed", trip(names=names), source=str(n))
    elif code in {"D02", "D03"}:
        for n in range(180):
            fact(state, "dispatch", "block:1", {"effective_seconds_added": 4 * 3600}, source=str(n))
    elif code in {"D04", "D08"}:
        for region in FIXED_SETS["dispatch-regions-v1"]:
            for tool in FIXED_SETS["dispatch-tools-v1"]:
                fact(state, "dispatch", "completed", trip(region=region, tool=tool))
    elif code == "D07":
        data = trip()
        data["progress"]["rewards"] = [
            {"material_id": "training-ore", "delta_units": 1000 * MATERIAL_SCALE, "source_kind": "dispatch-base"}
        ]
        fact(state, "dispatch", "completed", data)
    elif code == "D10":
        fact(
            state,
            "dispatch",
            "event:1",
            {"rewards": [{"souvenir_id": key} for key in FIXED_SETS["dispatch-souvenirs-v1"]]},
        )
    elif code == "D12":
        fact(state, "dispatch", "block:1", {"effective_seconds_added": 14400, "forced": True, "hit": True})
    elif code in {"T01", "T02", "T03", "T04", "T05", "T06", "T11", "T14"}:
        ids = ("tomori", "soyo", "taki", "sakiko", "mutsumi") if code == "T14" else ("tomoe", "ako", "layer")
        for n in range(50 if code == "T03" else 10 if code == "T02" else 1):
            fact(state, "tour", "completed", tour_data(ids, grade="SS"), source=str(n))
    elif code == "T07":
        ids = sorted({c.identity for c in CHARACTERS.values()})[:15]
        data = tour_data()
        data["stages"][0]["highlights"] = [{"identity": i} for i in ids]
        fact(state, "tour", "completed", data)
    elif code == "T08":
        fact(state, "tour", "practice-training", {"own_experience_after": 2200, "experience_after": 2200})
    elif code == "T09":
        for venue in VENUES:
            data = tour_data()
            data["stages"][0]["plan"]["venue"] = venue.venue_id
            fact(state, "tour", "completed", data)
    elif code == "T10":
        for n in range(3):
            fact(state, "tour", "completed", {**tour_data(), "verified_partner": str(n)})
    elif code == "T12":
        for theme in THEMES:
            fact(state, "tour", "completed", tour_data(theme=theme.theme_id))
    elif code == "T13":
        for template in ("pig-bandori-hhw-misaki", "pig-bandori-hhw-michelle"):
            data = tour_data()
            for s in data["stages"]:
                s["members"][0]["template_id"] = template
            fact(state, "tour", "completed", data)
    elif code in {"B01", "B02", "B03", "B04", "B05", "B13", "B14"}:
        for n in range(50):
            data = match_data(heavy=2, core=3)
            data["state"]["sides"][1]["snapshot"]["player_id"] = f"q{n}"
            if n % 2:
                data["state"]["sides"].reverse()
            fact(state, "battle", "finished", data, source=str(n))
    elif code in {"B06", "B07", "B08", "X02"}:
        for fighter in ("gojo", "sukuna"):
            for level in range(1, 6):
                fact(
                    state,
                    "battle",
                    "upgrade",
                    {
                        "payer_id": "p",
                        "pig_instance_id": f"battle-pig-{fighter}",
                        "from_level": level - 1,
                        "to_level": level,
                        "archetype": fighter,
                        "natural_ore_units_before": 60 * MATERIAL_SCALE,
                    },
                )
        data = match_data()
        data["state"]["sides"][0]["snapshot"]["pig_instance_id"] = "battle-pig-gojo"
        fact(state, "battle", "finished", data)
    elif code in {"B09", "B10", "B11", "B12"}:
        fighter = "gojo" if code == "B10" else "sukuna"
        for move in FIGHTERS_BY_ID[fighter].moves:
            fact(
                state,
                "battle",
                "move:1:1",
                {"move_id": move.move_id, "loan": move.loan, "gain": move.gain, "multiplier": 1},
            )
        if code == "B12":
            for n in range(3):
                fact(state, "battle", f"move:1:{n + 2}", {"move_id": "loan", "loan": True, "gain": 0})
            fact(state, "battle", "move:1:5", {"move_id": "dismantle", "gain": 20, "multiplier": 2})
        if code == "B11":
            data = match_data()["state"]["sides"]
            data[0]["turn"].update(effective=0, draws=0, debt=2)
            fact(state, "battle", "round:1", {"side": 0, "result": {"winner": 0, "before": data}})
        fact(state, "battle", "finished", match_data(fighter))
    elif code == "X01":
        fact(state, "dispatch", "completed", trip())
        fact(state, "tour", "completed", tour_data())
        fact(state, "battle", "finished", match_data())
    elif code == "X03":
        fact(
            state,
            "tour",
            "equipment-upgraded",
            {
                "level": 1,
                "costs": {"stage-components": 20},
                "paid": True,
                "natural_stage_components_before_units": 20 * MATERIAL_SCALE,
            },
        )
        fact(state, "tour", "completed", tour_data())
    elif code == "X05":
        template = character("tomoe").template_id
        for _ in range(2):
            fact(state, "dispatch", "completed", trip(names=(template,)), at=100)
        for _ in range(3):
            fact(state, "tour", "completed", tour_data(), at=1000)
    elif code == "X06":
        for n in range(1, 6):
            fact(state, "battle", f"loot:{n}", {"role": "actor"})
    return state


_RUNTIME = json.loads(
    (Path(__file__).parents[1] / "pig_catcher/domain/data/activity_achievements_v1.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("entry", _RUNTIME["entries"], ids=lambda e: e["code"])
def test_each_of_48_achievements_has_a_provable_reducer_path(entry):
    state = complete_state(entry["code"])
    definition = ACHIEVEMENT_BY_ID[entry["id"]]
    unlocked = set(FIXED_SETS["three-systems-public-v1"]) if entry["code"] == "X04" else set()
    value, _ = progress(state, definition, unlocked)
    assert value == definition.condition.target
    assert progress({}, definition, set())[0] == 0


def test_recall_and_non_natural_battles_do_not_unlock_normal_achievements():
    state = {}
    fact(state, "dispatch", "recalled", trip())
    fact(state, "battle", "move:1:1", {"move_id": "loan", "loan": True, "gain": 0})
    fact(state, "battle", "finished", {**match_data(core=8, heavy=2), "status": "surrendered", "natural_end": False})
    assert state.get("values", {}).get("dispatch.completed_trips", 0) == 0
    assert state.get("values", {}).get("battle.natural_finishes", 0) == 0
    assert state["battles"] == {}


def test_material_rewards_conversions_and_coupons_are_not_natural_production():
    state = {}
    data = trip()
    data["progress"]["rewards"] = [
        {"material_id": "training-ore", "delta_units": 2000 * MATERIAL_SCALE, "source_kind": kind}
        for kind in (
            "achievement-reward",
            "achievement-choice",
            "achievement-coupon",
            "dispatch-tool-conversion",
            "test",
        )
    ]
    fact(state, "dispatch", "completed", data)
    assert state["values"]["dispatch.natural_main_materials"] == 0


def test_mixed_stage_conditions_do_not_use_only_the_final_lineup():
    state, data = {}, tour_data()
    data["stages"][0]["bands"] = ["roselia"]
    data["stages"][0]["grade"] = "A"
    fact(state, "tour", "completed", data)
    assert not state["values"].get("tour.mixed_three_band_all_s")
    assert not state["values"].get("tour.double_drums_layer_all_s")
    assert not state["values"].get("tour.three_stage_ss")


def test_personal_growth_and_chronology_are_not_inherited():
    state = {}
    fact(state, "tour", "practice-training", {"own_experience_after": 50, "experience_after": 2300})
    fact(state, "battle", "upgrade", {"payer_id": "q", "pig_instance_id": "p", "to_level": 5})
    data = tour_data()
    fact(
        state,
        "tour",
        "equipment-upgraded",
        {
            "level": 1,
            "costs": {"stage-components": 20},
            "paid": True,
            "natural_stage_components_before_units": 20 * MATERIAL_SCALE,
        },
        at=2000,
    )
    fact(state, "tour", "completed", data, at=3000)
    assert not state.get("values", {}).get("tour.own_member_level10")
    assert not state.get("values", {}).get("battle.own_full_training")
    assert not state.get("values", {}).get("journey.dispatch_gear_tour")


async def test_real_tour_outbox_rewards_idempotency_and_material_ledger(tour_world):
    world = tour_world
    await world.send("一键")
    result = await world.send("确认")
    service = AchievementService(world.db, clock=world.clock)
    unlocks = await service.process_receipt(result.receipt)
    assert "tour-first-finale" in {u.achievement_id for u in unlocks}
    assert await service.process_receipt(result.receipt) == ()
    rows = await world.db.fetch_all("SELECT * FROM material_ledger WHERE source_kind='achievement-reward'")
    assert len(rows) == 1 and rows[0]["delta_units"] == 8 * MATERIAL_SCALE
    async with world.db.transaction() as session:
        assert await MaterialRepository().reconcile(session) == []
    assert (await world.db.fetch_one("SELECT COUNT(*) FROM achievement_activity_queue WHERE processed_at IS NULL"))[
        0
    ] == 0


async def test_real_battle_both_players_and_five_deliveries(battle_world):
    w = battle_world
    match = await w.fight()
    service = AchievementService(w.db, clock=w.clock)
    await service.process_activity_facts(scope_id=w.a.scope.value, receipt_id="battle-unlocks")
    players = await service.notification_players(scope_id=w.a.scope.value, receipt_id="battle-unlocks")
    assert set(players) == {w.a.player_id, w.b.player_id}
    rows = await w.db.fetch_all("SELECT player_id FROM achievement_unlocks WHERE achievement_id='battle-first-finish'")
    assert len(rows) == 2
    assert await service.process_activity_facts(scope_id=w.a.scope.value, receipt_id="retry") == ()
    loser = w.b if match["winner_id"] == w.a.player_id else w.a
    for _ in range(5):
        await w.send(section="loot", actor=loser)
    await service.process_activity_facts(scope_id=w.a.scope.value, receipt_id="loot-unlocks")
    row = await w.db.fetch_one(
        "SELECT player_id FROM achievement_unlocks WHERE achievement_id='hidden-journey-five-delivered'"
    )
    assert row is not None and row[0] == loser.player_id


async def test_failure_rolls_back_rewards_not_business_and_retry_is_exactly_once(tour_world, monkeypatch):
    w = tour_world
    await w.send("一键")
    receipt = (await w.send("确认")).receipt
    service = AchievementService(w.db, clock=w.clock)
    original = service._grant_rewards

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("simulated reward failure")

    monkeypatch.setattr(service, "_grant_rewards", fail)
    with pytest.raises(RuntimeError):
        await service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id=receipt.receipt_id)
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM tour_runs WHERE status='completed'"))[0] == 1
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_unlocks WHERE achievement_id='tour-first-finale'"))[
        0
    ] == 0
    monkeypatch.setattr(service, "_grant_rewards", original)
    await asyncio.gather(
        *(
            service.process_activity_facts(scope_id=w.identity.scope.value, receipt_id=receipt.receipt_id)
            for _ in range(2)
        )
    )
    assert (await w.db.fetch_one("SELECT COUNT(*) FROM achievement_unlocks WHERE achievement_id='tour-first-finale'"))[
        0
    ] == 1


async def test_hidden_detail_masks_conditions_progress_and_rewards(tour_world):
    service = AchievementService(tour_world.db, clock=tour_world.clock)
    for entry in _RUNTIME["entries"]:
        if entry["hidden"]:
            detail = await service.detail(tour_world.identity, entry["name"])
            assert detail.name == "？？？" and detail.progress == 0 and detail.target == 1
            assert detail.rewards == () and detail.description == entry["hint"]
