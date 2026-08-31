"""The offline battle art gate includes deterministic cards for every new interaction family."""

from fractions import Fraction

from pig_catcher.domain.battle import new_state
from pig_catcher.domain.battle_catalog import (
    ASAMU_PIG_TEMPLATE_IDS,
    BATTLE_VERSION,
    DANIYA_PIG_TEMPLATE_IDS,
    FIGHTERS_BY_ID,
    JUEJUE_PIG_TEMPLATE_IDS,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.services.battle_views import wheels
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
    public_fighters = (FIGHTERS_BY_ID["sukuna"], FIGHTERS_BY_ID["gojo"])
    state = new_state([_snapshot(fighter, index) for index, fighter in enumerate(public_fighters)])
    match = {
        "battle_id": "BVISUALFIXTURE",
        "status": "active",
        "definition_version": BATTLE_VERSION,
        "expires_ms": 60_000,
    }

    cases, evidence = deterministic_mechanic_cases(
        state,
        match,
        identity,
        0,
        {
            "template_id": JUEJUE_PIG_TEMPLATE_IDS[0],
            "display_name": "撅撅猪",
            "rarity": 6,
            "image": "media/fixture/juejue.jpg",
            "display_tags": ["群友定制", "叠叠猪", "动态"],
        },
        {
            "template_id": DANIYA_PIG_TEMPLATE_IDS[0],
            "display_name": "达妮娅猪",
            "rarity": 6,
            "image": "media/fixture/daniya.png",
            "display_tags": ["群友定制", "泡泡", "布景"],
        },
        {
            "template_id": ASAMU_PIG_TEMPLATE_IDS[0],
            "display_name": "阿萨姆猪",
            "rarity": 6,
            "image": "media/fixture/asamu.png",
            "display_tags": ["群友定制", "奶茶", "动态"],
        },
    )
    names = [name for name, _view in cases]
    assert names == [
        "13c-domain-clash-sukuna-win",
        "13d-domain-clash-tie",
        "13e-solo-simple-domain",
        "13f-black-flash-loan-infinity-space",
        "13g-purple-reset-cycle",
        "13h-round-carry",
        "13i-juejue-form-switch",
        "13j-daniya-asamu-formal-art",
        "13k-daniya-domain-transition",
        "13l-unified-numeric-invalidation",
        "13m-daniya-collapse-rebound",
        "13n-asamu-dynamic-chain",
        "13o-asamu-domain-copies",
    ]
    assert evidence[names[0]]["wheel"] == (("side-0", 8), ("side-1", 6), ("tie", 6))
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
    assert all(view.fighters for _name, view in cases)
    assert all(view.wheels for name, view in cases if name != "13j-daniya-asamu-formal-art")
    assert "空间斩" in dict(cases)["13f-black-flash-loan-infinity-space"].text()
    purple = evidence["13g-purple-reset-cycle"]
    assert purple["first_purple_used_steps"] == 2
    assert purple["second_purple_used_steps"] == 1
    assert purple["final_purple_weight_steps"] == 0
    carry = evidence["13h-round-carry"]
    assert carry["round"] == 3
    assert [item["round_start_weight"] for item in carry["carryover"]] == [8, 8]
    assert "历史折半继承3" in dict(cases)["13h-round-carry"].text()
    switch = evidence["13i-juejue-form-switch"]
    assert switch["form_track"] == ["time-sand", "virtual-sound", "time-sand"]
    switch_text = dict(cases)["13i-juejue-form-switch"].text()
    assert "时之沙 → 虚拟声 → 时之沙" in switch_text
    assert "虚拟模仿" in switch_text
    assert "加速盘抽中" in switch_text and "最终成功率" in switch_text
    assert evidence["13j-daniya-asamu-formal-art"]["daniya_initial_form"] == "staging"
    transition = evidence["13k-daniya-domain-transition"]
    assert transition["staging_steps"] == [1, 2]
    assert transition["domain_draw_weight_units"] == 1200
    assert transition["domain_steps_after_draw"] == 0
    invalidation = evidence["13l-unified-numeric-invalidation"]
    assert invalidation["cancelled_own_gain"] == invalidation["doubled_own_gain_before_invalidation"]
    assert invalidation["doubled_opponent_reduction_preserved"] == 44
    assert invalidation["permanent_opponent_exhaust_bonus_units"] == 1
    assert evidence["13m-daniya-collapse-rebound"]["move_gain"] == Fraction(-521, 10)
    dynamic = evidence["13n-asamu-dynamic-chain"]
    assert dynamic["tea_weight_after_bathe"] == 1500
    assert dynamic["sleep_weight_after_tea"] == 1250
    assert dynamic["prime_weight_after_sleep"] == 300
    copies = evidence["13o-asamu-domain-copies"]
    assert copies["copy_count"] == 4
    copy_card = dict(cases)["13o-asamu-domain-copies"].fighters[1].move_wheel
    assert copy_card is not None and copy_card.selected_index is not None
    assert copy_card.segments[copy_card.selected_index].label == copies["copies"][-1]["source_move_name"]
    assert "复制自达妮娅猪" in copy_card.title and "真实来源落点" in copy_card.note


def test_juejue_dual_form_and_subwheels_have_a_deterministic_art_view() -> None:
    identity = CommandIdentity(ScopeKey("qq-official", "fixture"), "stream", "player-0", "玩家0")
    result = wheels(identity, "juejue", level=5)
    titles = [card.title for card in result.wheels]
    assert titles[:4] == [
        "撅撅猪 · 时之沙",
        "撅撅猪 · 虚拟声",
        "时之沙 · 加速盘",
        "时之沙 · 时延盘",
    ]
    text = result.text()
    assert all(value in text for value in ("即时换盘", "相对静止时间·零", "虚拟模仿", "主盘基础抽取权重为1"))
    assert all(
        value in text
        for value in (
            "单领域战权重2.5",
            "双领域5.5",
            "撤销本轮新伤势",
            "之后每个招式固定+5",
            "下一次加速与下一次时延成功率各+5个百分点",
        )
    )
