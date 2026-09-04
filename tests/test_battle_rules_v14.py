"""Battle v14：撅撅猪数值、重复抽取与成功分支加招。"""

from pig_catcher.domain.battle import apply_move, choose, new_state
from pig_catcher.domain.battle_catalog import (
    BATTLE_RULE_VERSION,
    JUEJUE_ACCELERATION_TIERS,
    JUEJUE_DELAY_TIERS,
    JUEJUE_TIME_MOVES,
    JUEJUE_VIRTUAL_MOVES,
)
from pig_catcher.services.battle_views import move_line


def _player(*, pending: int = 12) -> dict:
    current = new_state(
        [
            {"fighter_id": fighter_id, "level": 0, "trait_bonus": 0, "tool_id": ""}
            for fighter_id in ("juejue", "sukuna")
        ],
        seed="battle-v14",
    )
    player = current["sides"][0]
    player["turn"].update(raw=pending, effective=pending, pending=pending, done=False)
    return player


def _failed_subwheel_seed(kind: str) -> str:
    tiers = JUEJUE_ACCELERATION_TIERS if kind == "acceleration" else JUEJUE_DELAY_TIERS
    tier_wheel = tuple((tier.tier, 1) for tier in tiers)
    key = "1:0:move:1:nested"
    for index in range(10_000):
        seed = f"battle-v14-{kind}-failure-{index}"
        tier_value, _ = choose(seed, f"{key}:tier", tier_wheel, version=BATTLE_RULE_VERSION)
        tier = next(item for item in tiers if item.tier == tier_value)
        success, _ = choose(
            seed,
            f"{key}:success",
            ((True, tier.success_chance), (False, 100 - tier.success_chance)),
            version=BATTLE_RULE_VERSION,
        )
        if not success:
            return seed
    raise AssertionError(f"找不到{kind}失败种子")


def test_v14_catalog_has_the_requested_juejue_numeric_buffs() -> None:
    assert BATTLE_RULE_VERSION == 14
    assert JUEJUE_TIME_MOVES[0].move_id == "sand-sculpt"
    assert JUEJUE_TIME_MOVES[0].gain == 15
    assert JUEJUE_VIRTUAL_MOVES[1].move_id == "future-simulation"
    assert JUEJUE_VIRTUAL_MOVES[1].gain == 15
    assert JUEJUE_VIRTUAL_MOVES[7].move_id == "chaos-domain"
    assert JUEJUE_VIRTUAL_MOVES[7].gain == 20


def test_v14_acceleration_and_delay_only_add_next_round_action_on_success() -> None:
    for move in (JUEJUE_TIME_MOVES[2], JUEJUE_TIME_MOVES[3]):
        player = _player()
        player["juejue_guaranteed"] = True
        event = apply_move(player, move, version=BATTLE_RULE_VERSION)
        assert event["subwheel"]["success"] is True
        assert event["juejue_success_next_action_bonus_added"] == 1
        assert player["next_action_bonus"] == 1

    for kind, move in (("acceleration", JUEJUE_TIME_MOVES[2]), ("delay", JUEJUE_TIME_MOVES[3])):
        player = _player()
        event = apply_move(
            player,
            move,
            seed=_failed_subwheel_seed(kind),
            round_number=1,
            side=0,
            version=BATTLE_RULE_VERSION,
        )
        assert event["subwheel"]["success"] is False
        assert event["juejue_success_next_action_bonus_added"] == 0
        assert player["next_action_bonus"] == 0


def test_v14_realtime_repeat_and_music_first_repeat_have_distinct_draw_counts() -> None:
    realtime_player = _player()
    first_realtime = apply_move(
        realtime_player,
        JUEJUE_VIRTUAL_MOVES[2],
        version=BATTLE_RULE_VERSION,
    )
    repeat_realtime = apply_move(
        realtime_player,
        JUEJUE_VIRTUAL_MOVES[2],
        version=BATTLE_RULE_VERSION,
    )
    assert first_realtime["special_base"] == 5
    assert first_realtime["extra_draws"] == 1
    assert first_realtime["realtime_activated"] and not first_realtime["realtime_repeated"]
    assert repeat_realtime["special_base"] == 10
    assert repeat_realtime["extra_draws"] == 2
    assert repeat_realtime["realtime_repeated"] and not repeat_realtime["realtime_activated"]

    music_player = _player()
    first_music = apply_move(music_player, JUEJUE_VIRTUAL_MOVES[5], version=BATTLE_RULE_VERSION)
    repeat_music = apply_move(music_player, JUEJUE_VIRTUAL_MOVES[5], version=BATTLE_RULE_VERSION)
    assert first_music["music_activated"] and first_music["extra_draws"] == 1
    assert repeat_music["music_repeated"] and repeat_music["extra_draws"] == 2
    assert repeat_music["music_gain"] == 5

    for event in (repeat_realtime, first_music, repeat_music):
        event["fighter_id"] = "juejue"
    assert "胜利权重基底改为+10并再抽2次" in move_line(repeat_realtime).note
    assert "音乐状态首次开启；本次再抽1次" in move_line(first_music).note
    assert "音乐状态不叠层；本次重复抽中改为再抽2次" in move_line(repeat_music).note


def test_v14_successful_subwheel_next_round_bonus_is_visible_in_move_card() -> None:
    player = _player()
    player["juejue_guaranteed"] = True
    event = apply_move(player, JUEJUE_TIME_MOVES[2], version=BATTLE_RULE_VERSION)
    event["fighter_id"] = "juejue"
    assert "子盘判定成功：自己下回合出招数+1" in move_line(event).note


def test_v13_replay_keeps_pre_v14_values_and_does_not_gain_new_actions() -> None:
    player = _player()
    sculpt = apply_move(player, JUEJUE_TIME_MOVES[0], version=13)
    future = apply_move(player, JUEJUE_VIRTUAL_MOVES[1], version=13)
    chaos = apply_move(player, JUEJUE_VIRTUAL_MOVES[7], version=13)
    first_realtime = apply_move(player, JUEJUE_VIRTUAL_MOVES[2], version=13)
    repeat_realtime = apply_move(player, JUEJUE_VIRTUAL_MOVES[2], version=13)
    first_music = apply_move(player, JUEJUE_VIRTUAL_MOVES[5], version=13)

    assert (sculpt["special_base"], future["special_base"], chaos["special_base"]) == (5, 5, 15)
    assert first_realtime["special_base"] == repeat_realtime["special_base"] == 5
    assert first_realtime["extra_draws"] == repeat_realtime["extra_draws"] == 1
    assert not repeat_realtime["realtime_repeated"]
    assert first_music["extra_draws"] == 0

    successful = _player()
    successful["juejue_guaranteed"] = True
    acceleration = apply_move(successful, JUEJUE_TIME_MOVES[2], version=13)
    assert acceleration["subwheel"]["success"] is True
    assert acceleration["juejue_success_next_action_bonus_added"] == 0
    assert successful["next_action_bonus"] == 0
