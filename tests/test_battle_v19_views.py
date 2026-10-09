"""以真实引擎事实验收 v19 图卡投影，渲染不得再抽签或制造抽签落点。"""

from copy import deepcopy
from fractions import Fraction

import pytest

from pig_catcher.domain import battle, daniya_battle, xixi_battle
from pig_catcher.domain.battle_catalog import FIGHTERS_BY_ID, fighter_form_moves, fighter_moves
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.rendering.feature_art import feature_wheel
from pig_catcher.services import battle_views as views

IDENTITY = CommandIdentity(ScopeKey("qq", "1092931381"), "v19-views", "200", "图卡验收员")


def state(left="xixi", right="gojo"):
    snapshots = []
    for index, fighter_id in enumerate((left, right)):
        definition = FIGHTERS_BY_ID[fighter_id]
        snapshots.append(
            {
                "fighter_id": fighter_id,
                "template_id": definition.template_id,
                "pig_instance_id": f"instance-{index}",
                "player_id": f"qq:1092931381:{200 + index}",
                "player_name": f"玩家{index}",
                "name": definition.name,
                "short_code": f"V19{index}",
                "rarity": 6 if fighter_id in {"xixi", "daniya"} else 5,
                "image_relpath": "",
                "display_tags": (),
                "size_value": 50,
                "weight_value": 70,
                "favorite": False,
                "level": 0,
                "trait_bonus": 0,
                "tool_id": "",
                **({"battle_form_id": "xixi-celestial"} if fighter_id == "xixi" else {}),
            }
        )
    return battle.new_state(snapshots, seed="v19-views", version=19)


def match(current):
    return {"battle_id": "BV19VIEWS", "status": current["status"], "definition_version": 19, "expires_ms": 60000}


def draw(current, side, move_id, monkeypatch):
    p = current["sides"][side]
    fighter_id = p["snapshot"]["fighter_id"]
    if fighter_id in {"daniya", "xixi"}:
        moves = fighter_form_moves(fighter_id, p[f"{fighter_id}_form"], 19)
    else:
        moves = fighter_moves(fighter_id, 19)
    index = next(i for i, move in enumerate(moves) if move.move_id == move_id)
    p["turn"].update(raw=1, effective=1, pending=1, done=False)
    original = battle.choose

    def selected(seed, key, wheel, **kwargs):
        if f":{side}:move:" in key and ":nested" not in key:
            return index, 0
        return original(seed, key, wheel, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(battle, "choose", selected)
        return battle.play_chunk(current, side, "v19-views", chunk_size=1)


def finish(current, monkeypatch, *, loser=0, injury="light"):
    original_choose, original_roll = battle.choose, battle.randbelow

    def chosen(seed, key, wheel, **kwargs):
        if key.endswith(":injury"):
            assert injury in dict(wheel)
            return injury, 0
        return original_choose(seed, key, wheel, **kwargs)

    def winner(seed, key, bound, **kwargs):
        if key.endswith(":winner"):
            return bound - 1 if loser == 0 else 0
        return original_roll(seed, key, bound, **kwargs)

    for p in current["sides"]:
        p["turn"].update(pending=0, done=True)
    with monkeypatch.context() as patch:
        patch.setattr(battle, "choose", chosen)
        patch.setattr(battle, "randbelow", winner)
        return battle.resolve_round(current, "v19-views")


def acquire(p, wanted):
    def select(seed, key, wheel, **kwargs):
        assert wanted in dict(wheel)
        return wanted, 0

    return xixi_battle.gain_hextech(p, "v19-views", wanted, choose=select)


def panel_text(card):
    return "\n".join(f"{line.label} {line.value} {line.note}" for panel in card.panels for line in panel.rows)


def injury_card(card):
    return next(wheel for wheel in card.wheels if wheel.kind == "injury")


@pytest.mark.parametrize(
    "form,move_id,expected_name",
    [
        ("staging", "daniya-staging-curtain", "达妮娅-布景·帷幕终景"),
        ("disillusion", "daniya-disillusion-knock", "达妮娅-幻灭·轻叩门扉"),
        ("black-hole", "daniya-black-hole-red-supergiant", "达妮娅-黑洞·红特超巨星"),
    ],
)
def test_daniya_actual_draw_projects_correct_form_names_and_saved_weights(monkeypatch, form, move_id, expected_name):
    current = state("daniya")
    p = current["sides"][0]
    if form == "black-hole":
        daniya_battle.enter_black_hole(p)
    else:
        p["daniya_form"] = form
    events = draw(current, 0, move_id, monkeypatch)
    card = views.matchup(IDENTITY, match(current), current, 0, events=events)
    wheel = card.fighters[0].move_wheel
    event = events[0]
    assert wheel.title.endswith(" · " + {"staging": "布景", "disillusion": "幻灭", "black-hole": "黑洞"}[form])
    assert wheel.segments[wheel.selected_index].label == expected_name == event["name"]
    assert [Fraction(str(segment.weight)) for segment in wheel.segments] == [
        Fraction(value, event["draw_weight_scale"]) for value in event["draw_wheel_units"]
    ]
    assert len(wheel.segments) == (4 if form == "black-hole" else 8)
    assert "世界·NMSL" not in card.text() and "发龙图" not in card.text()
    assert "虚质粒子" in card.fighters[0].action_lines[0].note


def test_daniya_three_rule_cards_use_precise_names_weights_and_fixed_probabilities():
    card = views.wheels(IDENTITY, "daniya")
    new_forms = [
        wheel for wheel in card.wheels if wheel.title in {"达妮娅猪 · 布景", "达妮娅猪 · 幻灭", "达妮娅猪 · 黑洞"}
    ]
    assert [len(wheel.segments) for wheel in new_forms] == [8, 8, 4]
    assert [s.weight for s in new_forms[-1].segments] == [4, 3, 2, 1]
    assert [s.label for s in new_forms[-1].segments] == [
        "达妮娅-黑洞·红特超巨星",
        "达妮娅-黑洞·沃尔夫拉叶星",
        "达妮娅-黑洞·铁核坍塌超新星",
        "达妮娅-黑洞·深黯 终末 恒常",
    ]
    wheel = next(w for w in card.wheels if w.title == "黑洞 · 固定伤势盘")
    assert [(s.label, s.weight) for s in wheel.segments] == [("无伤", 82.3), ("受伤", 12.49), ("力竭倒下", 5.21)]
    assert wheel.selected_index is None  # 规则卡没有抽过伤势


def test_successive_black_hole_injured_landing_progresses_without_changing_probabilities(monkeypatch):
    current = state("gojo", "daniya")
    daniya_battle.enter_black_hole(current["sides"][1])
    shown = []
    for expected in ("light", "heavy"):
        result = finish(current, monkeypatch, loser=1, injury="injured")
        assert result["injury_effective"] == expected
        card = views.matchup(IDENTITY, match(current), current, 0, round_result=result)
        wheel = injury_card(card)
        assert wheel.segments[wheel.selected_index].label == "受伤"
        assert "受伤 → " + {"light": "轻伤", "heavy": "重伤"}[expected] in card.text()
        shown.append([(s.label, s.weight) for s in wheel.segments])
    assert shown[0] == shown[1] == [("无伤", 82.3), ("受伤", 12.49), ("力竭倒下", 5.21)]


@pytest.mark.parametrize(
    "guard,expected", [("hourglass", "中亚沙漏"), ("shield", "由心及物"), ("goliath", "歌莉娅巨人")]
)
def test_guards_show_skipped_injury_without_a_fabricated_landing(monkeypatch, guard, expected):
    current = state()
    p = current["sides"][0]
    if guard == "hourglass":
        draw(current, 0, "xixi-hourglass", monkeypatch)
    else:
        acquire(p, "mind-over-matter" if guard == "shield" else "goliath")
        draw(current, 0, "xixi-attack", monkeypatch)
    draw(current, 1, "blue", monkeypatch)
    result = finish(current, monkeypatch)
    assert result["injury_roll"] is None and result["injury_skip_reason"] == expected
    card = views.matchup(IDENTITY, match(current), current, 0, round_result=result)
    assert not any(w.kind == "injury" for w in card.wheels)
    text = panel_text(card)
    assert expected in text and "免抽伤势" in text
    assert "本轮未进行伤势抽取" in text


def test_xixi_mark_and_auto_overload_display_the_real_source_without_fake_randomness(monkeypatch):
    current = state()
    surge = draw(current, 0, "xixi-surge", monkeypatch)[0]
    events = draw(current, 0, "xixi-prison", monkeypatch)
    prison, automatic = events
    assert "roll" in prison and "roll" not in automatic
    assert surge["xixi"]["mark_after"] == 1 and prison["xixi"]["mark_after"] == 0
    before = deepcopy(current)
    card = views.matchup(IDENTITY, match(current), current, 0, events=[surge, *events])
    assert current == before
    notes = " ".join(line.note for line in card.fighters[0].action_lines)
    assert "法术涌动标记0→1" in notes and "法术涌动标记1→0" in notes
    assert "自动施放，不占手抽次数" in notes
    wheel = card.fighters[0].move_wheel
    assert "自动超负荷" in wheel.title and "没有抽取招式盘" in wheel.note
    assert wheel.selected_index is None  # 自动施放不制造手抽盘的落点


def test_count_projection_uses_persisted_dynamic_wheel_even_after_weights_change():
    current = state()
    p = current["sides"][0]
    p.update(weight=100, heavy=True, risk=2, injury_state="heavy")
    fact = battle.roll_count(current, 0, "v19-views")
    stored = deepcopy(p["turn"]["count_wheel"])
    p["weight"] = 2000000000  # 图卡必须读之前抽签的盘
    card = views.matchup(IDENTITY, match(current), current, 0)
    wheel = card.fighters[0].count_wheel
    assert [s.label for s in wheel.segments] == [f"{n}招" for n, _weight in stored]
    assert [s.weight for s in wheel.segments] == [weight for _n, weight in stored]
    assert wheel.segments[wheel.selected_index].label == f"{fact['raw']}招"
    assert "5招" not in [s.label for s in wheel.segments]


def test_reality_whole_round_effect_and_equipment_cooldown_are_visible(monkeypatch):
    current = state()
    draw(current, 0, "xixi-attack", monkeypatch)
    reality = draw(current, 0, "xixi-reality", monkeypatch)[0]
    draw(current, 1, "blue", monkeypatch)
    result = finish(current, monkeypatch)
    card = views.matchup(IDENTITY, match(current), current, 0, round_result=result)
    assert result["after"][0]["turn"]["events"][0]["xixi_gain_factor"] == Fraction(13, 10)
    assert "第3回合可再次抽到" in card.text()
    assert "现实器" in card.text()
    assert "1.3" in card.text() and "0.7" in card.text()
    assert result["after"][0]["turn"]["events"][0]["gain"] > 16
    assert reality["xixi"]["cooldown_until"] == 2


def test_reality_cooldown_zero_sector_is_filtered_without_changing_saved_draw(monkeypatch):
    current = state()
    draw(current, 0, "xixi-reality", monkeypatch)
    attack = draw(current, 0, "xixi-attack", monkeypatch)[0]
    saved_ids = attack["draw_wheel_move_ids"]
    saved_units = attack["draw_wheel_units"]
    assert saved_units[saved_ids.index("xixi-reality")] == 0
    positive = [(move_id, units) for move_id, units in zip(saved_ids, saved_units, strict=True) if units > 0]
    definitions = {move.move_id: move.name for move in fighter_form_moves("xixi", "xixi-celestial", 19)}
    before = deepcopy(current)

    def no_redraw(*args, **kwargs):
        pytest.fail("渲染保存招式盘不得再次抽签")

    monkeypatch.setattr(battle, "choose", no_redraw)
    card = views.matchup(IDENTITY, match(current), current, 0, events=[attack])
    wheel = card.fighters[0].move_wheel
    assert current == before
    assert [(s.label, s.weight) for s in wheel.segments] == [
        (definitions[move_id], float(Fraction(units, attack["draw_weight_scale"])))
        for move_id, units in positive
    ]
    assert all(s.weight > 0 for s in wheel.segments)
    assert wheel.segments[wheel.selected_index].label == attack["name"]
    assert "本次不可抽取：" + definitions["xixi-reality"] in wheel.note
    svg = str(feature_wheel(wheel.segments, wheel.selected_index))
    assert '<svg class="feature-wheel"' in svg and "已抽中：" + attack["name"] in svg


def test_delayed_hextech_projects_saved_wheel_name_count_and_emperor(monkeypatch):
    current = state()
    p = current["sides"][0]
    for wanted in ("back-to-basics", "physical-to-magic", "goliath"):
        acquire(p, wanted)
    event = draw(current, 0, "xixi-realm-warp", monkeypatch)[0]
    assert event["xixi"]["delayed_hextech_round"] == 4
    future = views.matchup(IDENTITY, match(current), current, 0, events=[event])
    assert "第4回合抽海克斯" in future.text()
    assert "第4回合待领海克斯" in future.text()
    current["round"] = 4
    p["turn"] = battle.fresh_turn()
    battle.roll_count(current, 0, "v19-views")
    stored = p["turn"]["xixi_delayed_hextech"][0]
    grant = stored["result"]
    assert grant["hextech"] == "mind-over-matter" and grant["emperor"]
    # 投影从序列化事实恢复，禁抽签证明不会重新生成符文。
    restored = battle.loads(battle.dumps(current))
    with monkeypatch.context() as patch:
        patch.setattr(battle, "choose", lambda *a, **k: pytest.fail("投影重新抽签"))
        card = views.matchup(IDENTITY, match(restored), restored, 0)
    text = card.text()
    assert "第4回合兑现" in text and grant["name"] in text and "已获得4/4种" in text
    assert "全招+21亿" in text and "固定99%无伤 / 1%力竭" in text
    wheel = next(w for w in card.wheels if "海克斯落点" in w.title)
    assert [(s.label, s.weight) for s in wheel.segments] == [(grant["name"], 1)]
    assert wheel.segments[wheel.selected_index].label == grant["name"]


def test_emperor_actual_fixed_wheel_and_seven_slot_move_wheel(monkeypatch):
    current = state()
    p = current["sides"][0]
    for wanted in ("physical-to-magic", "mind-over-matter", "goliath", "back-to-basics"):
        acquire(p, wanted)
    p.update(xixi_shield=False, xixi_goliath_gain=100)
    event = draw(current, 0, "xixi-friends", monkeypatch)[0]
    running = views.matchup(IDENTITY, match(current), current, 0, events=[event])
    move_wheel = running.fighters[0].move_wheel
    assert len(move_wheel.segments) == 7
    assert not {"现实器", "中亚沙漏"}.intersection(s.label for s in move_wheel.segments)
    result = finish(current, monkeypatch, injury="none")
    card = views.matchup(IDENTITY, match(current), current, 0, round_result=result)
    wheel = injury_card(card)
    assert [(s.label, s.weight) for s in wheel.segments] == [("无伤", 99), ("力竭倒下", 1)]
    assert wheel.segments[wheel.selected_index].label == "无伤"
    assert card.fighters[0].risk == "固定伤势盘"
    assert "2,100,000,000" in card.text() or "21亿" in card.text()
