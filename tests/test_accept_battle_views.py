"""The offline battle art gate includes deterministic cards for every new interaction family."""

from pig_catcher.domain.battle import new_state
from pig_catcher.domain.battle_catalog import BATTLE_VERSION, FIGHTERS
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from tools.accept_battle_views import deterministic_mechanic_cases


def _snapshot(fighter, index: int) -> dict:
    return {
        "fighter_id": fighter.fighter_id,
        "template_id": fighter.template_id,
        "pig_instance_id": f"pig-{index}",
        "player_id": f"qq:player-{index}",
        "player_name": f"玩家{index}",
        "name": fighter.name,
        "short_code": f"T{index}",
        "rarity": 5,
        "image_relpath": "",
        "display_tags": ("战斗猪",),
        "size_value": 100.0,
        "weight_value": 200.0,
        "favorite": False,
        "level": 0,
        "trait_bonus": 0,
        "tool_id": "",
    }


def test_deterministic_mechanic_cards_cover_new_battle_rules() -> None:
    identity = CommandIdentity(ScopeKey("qq-official", "fixture"), "stream", "player-0", "玩家0")
    state = new_state([_snapshot(fighter, index) for index, fighter in enumerate(FIGHTERS)])
    match = {
        "battle_id": "BVISUALFIXTURE",
        "status": "active",
        "definition_version": BATTLE_VERSION,
        "expires_ms": 60_000,
    }

    cases, evidence = deterministic_mechanic_cases(state, match, identity, 0)
    names = [name for name, _view in cases]
    assert names == [
        "13c-domain-clash-sukuna-win",
        "13d-domain-clash-tie",
        "13e-solo-simple-domain",
        "13f-black-flash-loan-infinity-space",
        "13g-purple-reset-cycle",
        "13h-round-carry",
    ]
    assert evidence[names[0]]["wheel"] == (("side-0", 4), ("side-1", 3), ("tie", 3))
    assert evidence[names[0]]["outcome"] == "side-0"
    assert evidence[names[0]]["boost_side"] == 0
    assert evidence[names[0]]["bonus_gain"] > 0
    assert evidence[names[1]]["outcome"] == "tie"
    assert evidence[names[2]]["wheel"] == (("hit", 8), ("simple-domain", 2))
    assert evidence[names[2]]["outcome"] == "simple-domain"
    assert evidence[names[3]]["black_flash_stacks"] == 1
    assert evidence[names[3]]["loan_gain"] == 1
    assert evidence[names[3]]["space_slash_gain"] >= 29
    assert "无下限·防御" in str(evidence[names[3]]["infinity_adjustments"])
    assert all(view.fighters and view.wheels for _name, view in cases)
    assert "空间斩" in dict(cases)["13f-black-flash-loan-infinity-space"].text()
    purple = evidence["13g-purple-reset-cycle"]
    assert purple["first_purple_used_steps"] == 2
    assert purple["second_purple_used_steps"] == 1
    assert purple["final_purple_weight_steps"] == 0
    carry = evidence["13h-round-carry"]
    assert carry["round"] == 3
    assert [item["round_start_weight"] for item in carry["carryover"]] == [8, 8]
    assert "历史折半继承3" in cases[-1][1].text()
