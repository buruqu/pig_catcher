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
from pig_catcher.services.battle_views import (
    _event_move_wheel,
    _mimic_fact,
    _v4_interaction_panels,
    move_line,
    wheels,
)
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
            "之后每招固定+5",
            "下一次加速与下一次时延成功率各+5个百分点",
        )
    )


def test_juejue_v6_wheel_explains_first_only_zero_repeat_music_and_effect_mimic() -> None:
    identity = CommandIdentity(ScopeKey("qq-official", "fixture"), "stream", "player-0", "玩家0")
    text = wheels(identity, "juejue", level=5).text()

    assert text.count("胜利权重+10") >= 3
    assert "本回合第一次加速+第一次时延均成功且档位和≥5" in text
    assert "重复抽中不叠层，改为再抽2次" in text
    assert "每次抽中都独立随机" in text
    assert "复制数值与一般效果" in text
    assert "抑制领域再入" in text
    assert "消除本回合一次加速失败产生的整笔下回合欠招" in text


def test_juejue_v6_mimic_and_event_facts_are_visible_without_exposing_internal_ids() -> None:
    mimic = {
        "available": True,
        "band": "large",
        "band_wheel": (("large", 1), ("small", 1)),
        "band_roll": 0,
        "source_wheel": ((0, 1),),
        "source_roll": 0,
        "source_fighter_id": "asamu",
        "source_move_id": "pressure-king",
        "source_name": "传奇耐压王",
        "base": 7,
        "direction": "self",
        "functional_fighter_id": "asamu",
        "functional_move_id": "pressure-king",
        "functional_tags": ("asamu-pressure-king",),
        "effect_summary": "对方每个数值招式独立进行失效判定",
        "extra_draws_suppressed": True,
        "domain_reentry_suppressed": True,
    }
    mimic_text = _mimic_fact(mimic)
    assert "同时复制效果" in mimic_text
    assert "追加抽数已抑制" in mimic_text
    assert "不会再次开启领域战" in mimic_text
    assert "pressure-king" not in mimic_text and "asamu" not in mimic_text

    event = {
        "ordinal": 4,
        "name": "虚拟声·把音乐开大声点！",
        "fighter_id": "juejue",
        "form_before": "virtual-sound",
        "form_after": "virtual-sound",
        "special_base": 0,
        "music_gain": 5,
        "subwheel": None,
        "relative_zero": {
            "checked": True,
            "roll": None,
            "wheel": (),
            "success": False,
            "gain": 0,
            "eligible": False,
            "reason": "首次加速判定失败",
            "first_acceleration": {"tier": 3, "success": False, "ordinal": 1},
            "first_delay": {"tier": 2, "success": True, "ordinal": 3},
        },
        "mimic": mimic,
        "sculpt_bonus_before": 0,
        "sculpt_bonus_after": 0,
        "sand_domain_steps_before": 0,
        "sand_domain_steps_after": 0,
        "sand_domain_switch_units_before": 0,
        "sand_domain_switch_units_after": 0,
        "realization_stacks_before": 0,
        "realization_stacks_after": 0,
        "guaranteed_before": False,
        "guaranteed_after": False,
        "realtime_activated": False,
        "future_simulation_activated": True,
        "sand_body_activated": False,
        "rewind_active": True,
        "rewind_debt_cleared": 3,
        "rewind_failure_ordinal": 2,
        "rewind_pending_count": 1,
        "music_repeated": True,
        "opponent_reduction": 0,
        "opponent_next_debt": 0,
        "opponent_next_bonus": 0,
        "training": 0,
        "core": 0,
        "penalty": 0,
        "multiplier": 1,
        "gain": 5,
        "total": 25,
        "extra_draws": 2,
        "tool_used": "",
    }
    line = move_line(event)
    assert "第一次子盘" in line.note
    assert "首次加速3档失败" in line.note
    assert "不满足发动条件" in line.note
    assert "未来模拟独立挂起1次" in line.note
    assert "撤销第2招加速失败产生的下回合-3招" in line.note
    assert "音乐状态不叠层" in line.note


def test_juejue_v6_each_future_simulation_has_its_own_source_in_settlement_view() -> None:
    interactions = {
        "domain": None,
        "adjustments": ((), ()),
        "future_simulations": (
            {
                "side": 0,
                "active": True,
                "source_ordinal": 2,
                "target_side": 1,
                "candidate_ordinals": (1, 3),
                "selected_ordinal": 1,
                "roll": 0,
                "cancelled_gain": 12,
            },
            {
                "side": 0,
                "active": True,
                "source_ordinal": 5,
                "target_side": 1,
                "candidate_ordinals": (3,),
                "selected_ordinal": 3,
                "roll": 0,
                "cancelled_gain": 8,
            },
        ),
        "sand_bodies": (),
        "zeroes": (),
        "round_reductions": (),
        "cross_effects": (),
    }
    panels = _v4_interaction_panels(interactions, ["玩家甲", "玩家乙"])
    text = "\n".join(panel.title + "\n" + "\n".join(line.label for line in panel.rows) for panel in panels)
    assert "未来模拟（第2招）" in text
    assert "未来模拟（第5招）" in text


def test_juejue_v6_domain_auto_mimic_has_a_real_selected_wheel_segment() -> None:
    event = {
        "ordinal": 9,
        "name": "虚拟声·虚拟模仿",
        "move_id": "virtual-mimic",
        "fighter_id": "juejue",
        "source_fighter_id": "asamu",
        "generated_by": "chaos-domain-auto-mimic",
        "draw_weight_scale": 1000,
        "draw_wheel_move_ids": ("virtual-mimic",),
        "draw_wheel_units": (1000,),
        "mimic": {"source_fighter_id": "asamu", "source_name": "传奇耐压王"},
    }
    card = _event_move_wheel(event, BATTLE_VERSION)
    assert card.selected_index == 0
    assert card.segments[0].label == "虚拟声·虚拟模仿"
    assert "乱序数虚时空自动发动" in card.title
    assert "阿萨姆猪" in card.note
