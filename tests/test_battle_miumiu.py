from copy import deepcopy

import pytest

from pig_catcher.commands.battle import parse_battle_request
from pig_catcher.domain import battle
from pig_catcher.domain.battle_catalog import FIGHTERS, MIUMIU_MOVES
from pig_catcher.domain.miumiu import activate_blank, add_blank_slots, blank_moves
from pig_catcher.services.battle_views import _event_move_wheel, move_line
from tests.test_battle import world  # noqa: F401 - isolated integration fixture


def state_for(right="gojo", seed="miu"):
    return battle.new_state(
        [{"fighter_id": f, "level": 0, "trait_bonus": 0, "tool_id": ""} for f in ("miumiu", right)], seed=seed
    )


def finish_moves(state, seed, order=(0, 1)):
    for side in order:
        battle.roll_count(state, side, seed)
        count = 0
        while not state["sides"][side]["turn"]["done"]:
            battle.play_chunk(state, side, seed)
            count += 1
            assert count < 100


@pytest.mark.parametrize("right", [f.fighter_id for f in FIGHTERS])
@pytest.mark.parametrize("seed", ["miu-a", "miu-b", "miu-c"])
def test_new_wheel_complete_rounds_and_command_order_independence(right, seed):
    a, b = state_for(right, seed), state_for(right, seed)
    for _ in range(3):
        if a["status"] != "active":
            break
        finish_moves(a, seed)
        finish_moves(b, seed, (1, 0))
        ar, br = battle.resolve_round(a, seed), battle.resolve_round(b, seed)
        assert ar["winner"] == br["winner"]
        assert ar["winner_weight_units"] == br["winner_weight_units"]
        assert ar["injury_effective"] == br["injury_effective"]
        for player in ar["after"]:
            for event in player["turn"]["events"]:
                move_line(event)
                _event_move_wheel(event, a["version"])


def test_reconstruction_restores_old_states_and_copies_only_wheel():
    state = state_for("daniya")
    for player in state["sides"]:
        player["turn"].update(raw=3, effective=2, pending=0, done=True, draws=5, debt=1)
    state["sides"][0]["next_debt"] = 10
    state["sides"][0]["core"] = 7  # caused in discarded round
    state["sides"][0]["miumiu_domain_triggers"] = 2
    state["sides"][1]["heavy"] = True
    state["sides"][1]["snapshot"]["level"] = 5
    moves = battle._available_moves(state["sides"][1])
    activate_blank(state, 0, moves, [m.resolved_draw_weight_units for m in moves])
    player = state["sides"][0]
    assert player["weight"] == player["round_start_weight"] == 0
    assert player["next_debt"] == player["core"] == player["snapshot"]["level"] == 0
    assert not player["heavy"]
    assert player["turn"]["pending"] == 2
    assert player["turn"]["draws"] == 5  # ordinal namespace never reuses a receipt key
    add_blank_slots(state)
    add_blank_slots(state)
    blanks = blank_moves(state["sides"][1])
    assert [m.resolved_draw_weight_units for m in blanks] == [40000, 40000]


def test_blank_domain_now_consumes_humidity_without_legacy_reconstruction(monkeypatch):
    state = state_for()
    for side in state["sides"]:
        side["turn"].update(raw=1, effective=1, pending=1, done=False)
    move = MIUMIU_MOVES[-1]
    event = battle.apply_move(state["sides"][0], move, side=0)
    event.update(side=0, fighter_id="miumiu", round=1)
    state["sides"][0]["turn"]["events"].append(event)
    enemy = battle.apply_move(state["sides"][1], battle.FIGHTERS_BY_ID["gojo"].moves[0], side=1)
    enemy.update(side=1, fighter_id="gojo", round=1)
    state["sides"][1]["turn"]["events"].append(enemy)
    original = battle.choose

    def force(seed, key, wheel, **kwargs):
        if "domain:solo" in key:
            return "hit", 0
        if "miumiu-mode" in key:
            return True, 0
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", force)
    result = battle.resolve_round(state, "replay")
    assert "miumiu_reconstructions" not in result
    assert not state["sides"][0].get("miumiu_mode")
    assert state["sides"][0]["miumiu_humidity"] == 0
    assert any(fact.get("consumed") == 2 for fact in result["interactions"]["mirror"])


def test_noop_does_not_get_auras_or_spend_double():
    state = state_for()
    player = state["sides"][0]
    player.update(miumiu_blanks=[4], black_flash_stacks=20, yilu_future_base_bonus=10, double=True)
    player["turn"].update(raw=1, effective=1, pending=1, done=False)
    event = battle.apply_move(player, blank_moves(player)[0])
    assert event["gain"] == 0 and event["extra_draws"] == 0
    assert player["double"]


def test_preview_consumes_prior_milk_dragon_before_comparing_order():
    a = state_for("asamu", "milk-preview")
    a["sides"][0]["asamu_milk_dragon_next_count"] = 2
    a["round_origin"] = deepcopy(a["sides"])
    b = deepcopy(a)
    finish_moves(a, "milk-preview")
    finish_moves(b, "milk-preview", (1, 0))
    assert a["miumiu_observations"] == b["miumiu_observations"]
    assert (
        battle.resolve_round(a, "milk-preview")["winner_weight_units"]
        == battle.resolve_round(b, "milk-preview")["winner_weight_units"]
    )


async def test_humidity_service_receipts_are_atomic_and_replayable(world, monkeypatch):  # noqa: F811
    await world.start()
    match = await world.match()
    state = battle.loads(match["state_json"])
    state["sides"][0]["snapshot"].update(fighter_id="miumiu", name="空白缪缪流形猪")
    state["round_origin"] = deepcopy(state["sides"])
    async with world.db.transaction() as session:
        await session.execute(
            "UPDATE battle_matches SET state_json=? WHERE battle_id=?", (battle.dumps(state), match["battle_id"])
        )
    original = battle.choose

    def force(seed, key, wheel, **kwargs):
        if key.endswith(":count"):
            return 1, 0
        if "miumiu-reconstruction" not in seed and ":0:move:" in key:
            return 9, 0  # Miumiu's invisible domain
        if "domain:solo" in key:
            return "hit", 0
        if "miumiu-mode" in key:
            return True, 0
        return original(seed, key, wheel, **kwargs)

    monkeypatch.setattr(battle, "choose", force)
    for actor in (world.a, world.b):
        await world.send(section="count", actor=actor)
    await world.send(section="move", actor=world.a)
    first = await world.send(section="move", actor=world.b, mid="mirror-settle")
    counts = len(await world.db.fetch_all("SELECT * FROM battle_moves"))
    await world.db.close()
    await world.db.open()
    repeated = await world.send(section="move", actor=world.b, mid="mirror-settle")
    assert first.receipt.receipt_id == repeated.receipt.receipt_id
    assert len(await world.db.fetch_all("SELECT * FROM battle_moves")) == counts
    row = await world.db.fetch_one("SELECT result_json FROM battle_rounds")
    summary = battle.loads(row[0])
    assert "miumiu_reconstructions" not in summary
    assert counts == sum(len(p["turn"]["events"]) for p in summary["after"])
    assert "润化" in first.view.text()
    assert any(fact.get("consumed") == 2 for fact in summary["interactions"]["mirror"])


async def test_every_registered_wheel_is_queryable_with_public_miumiu_descriptions(world):  # noqa: F811
    for fighter in FIGHTERS:
        request = parse_battle_request("轮盘 " + fighter.name)
        assert request.args["fighter_id"] == fighter.fighter_id
    preview = await world.send("轮盘 空白缪缪流形猪")
    assert "流形与润化招式盘" in preview.view.text()
    assert "25%" in preview.view.text() and "无上限" in preview.view.text()
    assert "润化" in preview.view.text() and "最终结算点数" in preview.view.text()
    assert "力竭保护" not in preview.view.text() and "回合重构" not in preview.view.text()
