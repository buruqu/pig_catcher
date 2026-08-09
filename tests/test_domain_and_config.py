"""领域不变量、选择器、访问控制与配置模型。"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pig_catcher.commands.help import format_help
from pig_catcher.commands.parsers import (
    parse_batch_sale_query,
    parse_catalog_query,
    parse_store_query,
    parse_upgrade_name,
)
from pig_catcher.config import AccessPolicy, PigCatcherConfig
from pig_catcher.domain.enums import Rarity
from pig_catcher.domain.errors import (
    DomainValidationError,
    FoodEffectError,
    MissingMessageIdError,
    ScopeValidationError,
    SelectorValidationError,
)
from pig_catcher.domain.food_effects import (
    ActiveFoodEffect,
    active_quota_effect_bonuses,
    apply_catch_effects,
    apply_cooking_effects,
    resolve_food_effect,
)
from pig_catcher.domain.gameplay import (
    ITEM_DEFINITIONS,
    generate_pig_attributes,
    level_progress,
    size_label,
    weight_label,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.ports import MessageKeyFactory
from pig_catcher.domain.quota import catch_quota_window, effective_catch_limit
from pig_catcher.domain.rules import (
    BASE_CATCH_WEIGHTS,
    LEVEL_CATCH_BONUS_CAP_LEVEL,
    apply_monotonic_high_rarity_multipliers,
    catch_weights,
    choose_rarity,
    cooking_weights,
    level_catch_bonus_scale,
    normalize_weights,
)
from pig_catcher.domain.selectors import parse_asset_selector


def test_scope_key_is_stable_and_group_scoped() -> None:
    scope = ScopeKey(platform="QQ", group_id="54321")
    assert scope.value == "qq:54321"
    assert ScopeKey.parse(scope.value) == scope


@pytest.mark.parametrize("value", ["", "qq", "qq:a:b"])
def test_scope_parse_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ScopeValidationError):
        ScopeKey.parse(value)


def test_asset_selector_supports_optional_short_code() -> None:
    assert parse_asset_selector("粉红小香猪").short_code is None
    selector = parse_asset_selector("粉红小香猪#A19F2C3D")
    assert selector.name == "粉红小香猪"
    assert selector.short_code == "A19F2C3D"


def test_catalog_query_has_filters_but_no_page_number() -> None:
    assert parse_catalog_query("").rarity is None
    assert parse_catalog_query("品质=4").rarity == 4
    assert parse_catalog_query("品质=未收集").undiscovered_only is True
    with pytest.raises(DomainValidationError, match="不需要填写页码"):
        parse_catalog_query("2")


def test_store_upgrade_and_batch_sale_parsers_match_new_commands() -> None:
    assert parse_store_query("").page == 1
    assert parse_store_query("分类=做菜").category == "做菜"
    with pytest.raises(DomainValidationError, match="单页"):
        parse_store_query("2")
    assert parse_upgrade_name("饲料") == "猪饲料"
    assert parse_upgrade_name("厨具升级") == "厨具"
    assert parse_batch_sale_query("猪猪").asset_kind.value == "pig"
    assert parse_batch_sale_query("美食").asset_kind.value == "food"


@pytest.mark.parametrize("value", ["#A19F2C3D", "猪#BAD", "猪#A19F2C3G"])
def test_asset_selector_rejects_ambiguous_syntax(value: str) -> None:
    with pytest.raises(SelectorValidationError):
        parse_asset_selector(value)


def test_message_key_is_stable_and_requires_message_id() -> None:
    identity = CommandIdentity(
        scope=ScopeKey("qq", "1"),
        stream_id="stream",
        user_id="2",
        display_name="成员",
        message_id="message-42",
    )
    assert MessageKeyFactory.build(identity, "catch") == MessageKeyFactory.build(identity, "catch")
    missing = CommandIdentity(
        scope=identity.scope,
        stream_id="stream",
        user_id="2",
        display_name="成员",
    )
    with pytest.raises(MissingMessageIdError):
        MessageKeyFactory.build(missing, "catch")


def test_catch_weights_are_normalized_and_transfer_missing_six_star() -> None:
    available = catch_weights(BASE_CATCH_WEIGHTS, six_star_available=True)
    unavailable = catch_weights(BASE_CATCH_WEIGHTS, six_star_available=False)
    assert BASE_CATCH_WEIGHTS == (40.0, 30.0, 17.0, 8.0, 4.0, 1.0)
    assert sum(available) == pytest.approx(100)
    assert sum(unavailable) == pytest.approx(100)
    assert sum(available[3:]) == pytest.approx(13.0)
    assert unavailable[5] == 0
    assert unavailable[4] == pytest.approx(5.0)


def test_feed_and_lucky_item_improve_high_rarity_share() -> None:
    baseline = catch_weights(feed_level=0, lucky_whistle=False)
    lucky_only = catch_weights(feed_level=0, lucky_whistle=True)
    boosted = catch_weights(feed_level=5, lucky_whistle=True)
    assert lucky_only == pytest.approx((34.0, 27.0, 16.0, 12.0, 7.0, 4.0))
    assert sum(boosted[2:]) > sum(baseline[2:])

    super_lucky = catch_weights(super_lucky_whistle=True)
    assert super_lucky == pytest.approx((27.0, 23.0, 15.0, 15.0, 12.0, 8.0))
    assert sum(super_lucky) == pytest.approx(100.0)
    assert sum(super_lucky[4:]) > sum(lucky_only[4:])
    radar = catch_weights(item_id="star-pig-radar")
    assert radar == pytest.approx((0.0, 0.0, 45.0, 30.0, 18.0, 7.0))
    with pytest.raises(DomainValidationError, match="一个"):
        catch_weights(lucky_whistle=True, super_lucky_whistle=True)


def test_monotonic_high_rarity_layer_holds_a_squeezed_tier_at_baseline() -> None:
    skewed = (0.0, 0.0, 10.0, 80.0, 9.0, 1.0)
    adjusted = apply_monotonic_high_rarity_multipliers(
        skewed,
        (1.0, 1.0, 1.0, 1.10, 1.20, 1.30),
    )
    assert sum(adjusted) == pytest.approx(100.0)
    assert adjusted[3] == pytest.approx(skewed[3])
    assert adjusted[4] > skewed[4]
    assert adjusted[5] > skewed[5]


@pytest.mark.parametrize(
    "item_id",
    ("", "lucky-whistle", "super-lucky-whistle", "star-pig-radar"),
)
def test_feed_and_level_never_reduce_any_high_rarity_tier(item_id: str) -> None:
    item_baseline = catch_weights(item_id=item_id)
    for feed_level in range(6):
        for player_level in (1, 5, 9, 13, 17, 21, 999):
            adjusted = catch_weights(
                item_id=item_id,
                feed_level=feed_level,
                player_level=player_level,
            )
            assert sum(adjusted) == pytest.approx(100.0)
            assert all(
                adjusted[index] >= item_baseline[index] - 1e-10
                for index in range(3, 6)
            )


def test_rebalanced_item_catalog_is_unique_and_priced_by_strength() -> None:
    expected_prices = {
        "幸运猪哨": 480,
        "超级幸运猪哨": 1320,
        "星辉探猪镜": 1680,
        "巨物玉米": 240,
        "增膘豆饼": 200,
        "精瘦青饲料": 200,
        "猪币悬赏牌": 620,
        "主厨香料": 480,
        "超级主厨香料": 1180,
        "精准刀工券": 220,
        "慢炖调料包": 260,
        "大份餐盒": 520,
        "稳火保底锅盖": 780,
        "升星炉芯": 1080,
        "丰收围裙": 460,
    }
    assert len(ITEM_DEFINITIONS) == 15
    assert {item.display_name: item.price for item in ITEM_DEFINITIONS} == expected_prices
    assert len({item.item_id for item in ITEM_DEFINITIONS}) == 15
    assert len({item.effect_summary for item in ITEM_DEFINITIONS}) == 15
    assert all(item.action_type in {"catching", "cooking"} for item in ITEM_DEFINITIONS)
    assert expected_prices["超级幸运猪哨"] < 2000
    assert expected_prices["超级主厨香料"] < 2000


def test_catch_attribute_items_have_distinct_bounded_profiles() -> None:
    common = {
        "rarity": Rarity.THREE,
        "length_min": 20.0,
        "length_max": 100.0,
        "weight_min": 10.0,
        "weight_max": 210.0,
        "fat_profile": "balanced",
        "random_values": (0.5, 0.5, 0.5, 0.5, 0.5),
    }
    baseline = generate_pig_attributes(**common)
    giant = generate_pig_attributes(**common, item_id="giant-corn")
    fattened = generate_pig_attributes(**common, item_id="fattening-bean-cake")
    lean = generate_pig_attributes(**common, item_id="lean-green-feed")

    assert giant.size_percentile == pytest.approx(baseline.size_percentile + 0.22)
    assert giant.weight_percentile == pytest.approx(baseline.weight_percentile + 0.14)
    assert fattened.fat_ratio == pytest.approx(baseline.fat_ratio + 22)
    assert fattened.weight_percentile == pytest.approx(baseline.weight_percentile + 0.12)
    assert lean.fat_ratio == pytest.approx(baseline.fat_ratio - 22)
    assert lean.size_percentile == pytest.approx(baseline.size_percentile + 0.10)
    assert lean.weight_percentile == pytest.approx(baseline.weight_percentile + 0.05)
    assert len({giant.official_value, fattened.official_value, lean.official_value}) == 3


def test_numeric_level_improves_catch_probability_with_a_hard_cap() -> None:
    baseline = catch_weights(player_level=1)
    growing = catch_weights(player_level=9)
    capped = catch_weights(player_level=LEVEL_CATCH_BONUS_CAP_LEVEL)
    far_beyond_cap = catch_weights(player_level=10**1000)

    assert level_catch_bonus_scale(1) == 0.0
    assert level_catch_bonus_scale(5) == 1.0
    assert level_catch_bonus_scale(LEVEL_CATCH_BONUS_CAP_LEVEL) == 5.0
    assert sum(growing[3:]) > sum(baseline[3:])
    assert sum(capped[3:]) > sum(growing[3:])
    assert far_beyond_cap == pytest.approx(capped)
    assert capped == pytest.approx(catch_weights(feed_level=5))
    with pytest.raises(DomainValidationError, match="玩家等级"):
        catch_weights(player_level=0)


def test_six_star_cooking_rule_is_fixed() -> None:
    assert cooking_weights(Rarity.SIX) == (0.0, 0.0, 0.0, 0.0, 90.0, 10.0)
    assert all(weights[5] == 0 for rarity, weights in ((r, cooking_weights(r)) for r in range(1, 6)))


def test_food_effects_are_one_shot_explicit_probability_adjustments() -> None:
    catch_effect = ActiveFoodEffect(
        effect_entry_id="catch-effect",
        effect_id="next-catch-quality",
        params={"multiplier": 1.35},
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at="2026-07-29T00:00:00.000Z",
    )
    catch_application = apply_catch_effects(BASE_CATCH_WEIGHTS, [catch_effect])
    assert sum(catch_application.weights[3:]) > sum(BASE_CATCH_WEIGHTS[3:])
    assert catch_application.consumed_entry_ids == ("catch-effect",)

    cook_effect = ActiveFoodEffect(
        effect_entry_id="six-star-effect",
        effect_id="next-six-star-cook",
        params={"six_star_percent": 20},
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at="2026-07-29T00:00:00.000Z",
    )
    cook_application = apply_cooking_effects(
        cooking_weights(6),
        [cook_effect],
        source_rarity=6,
    )
    assert cook_application.weights == (0.0, 0.0, 0.0, 0.0, 80.0, 20.0)
    assert cook_application.consumed_entry_ids == ("six-star-effect",)

    extreme_cook = ActiveFoodEffect(
        effect_entry_id="extreme-six-star-effect",
        effect_id="next-six-star-cook",
        params={"six_star_percent": 60},
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at="2026-07-29T00:00:00.000Z",
    )
    extreme_application = apply_cooking_effects(
        cooking_weights(6),
        [extreme_cook],
        source_rarity=6,
    )
    assert extreme_application.weights == (
        0.0,
        0.0,
        0.0,
        0.0,
        40.0,
        60.0,
    )


def test_six_star_food_effect_caps_support_extreme_rewards() -> None:
    rarity_grant = resolve_food_effect(
        "next-pig-rarity",
        {"rarity": 6, "multiplier": 12.0},
    )
    stature_grant = resolve_food_effect(
        "next-pig-stature",
        {"mode": "mini", "strength": 0.5},
    )
    assert rarity_grant.params["multiplier"] == 12.0
    assert stature_grant.params["strength"] == 0.5
    exact_catch = ActiveFoodEffect(
        effect_entry_id="exact-six-star-catch",
        effect_id="next-six-star-catch",
        params={"six_star_percent": 50},
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at="2026-08-04T00:00:00+08:00",
    )
    exact_application = apply_catch_effects(BASE_CATCH_WEIGHTS, [exact_catch])
    assert exact_application.weights[5] == pytest.approx(50.0)
    assert sum(exact_application.weights[:5]) == pytest.approx(50.0)
    assert resolve_food_effect(
        "weekly-window-catches", {"count": 5}
    ).params == {"count": 5}
    assert resolve_food_effect(
        "permanent-window-catch", {"count": 1, "max_bonus": 5}
    ).params == {"count": 1, "max_bonus": 5}
    with pytest.raises(FoodEffectError):
        resolve_food_effect(
            "next-food-rarity",
            {"rarity": 5, "multiplier": 12.0},
        )


def test_cooking_effects_wait_for_a_compatible_source_rarity() -> None:
    regular = ActiveFoodEffect(
        effect_entry_id="regular-effect",
        effect_id="next-cook-quality",
        params={"shift_percent": 8},
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at="2026-07-29T00:00:00.000Z",
    )
    custom = ActiveFoodEffect(
        effect_entry_id="custom-effect",
        effect_id="next-six-star-cook",
        params={"six_star_percent": 20},
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at="2026-07-29T00:00:01.000Z",
    )
    six_star = apply_cooking_effects(
        cooking_weights(6),
        [regular, custom],
        source_rarity=6,
    )
    assert six_star.weights == (0.0, 0.0, 0.0, 0.0, 80.0, 20.0)
    assert six_star.consumed_entry_ids == ("custom-effect",)

    regular_source = apply_cooking_effects(
        cooking_weights(3),
        [regular, custom],
        source_rarity=3,
    )
    assert regular_source.consumed_entry_ids == ("regular-effect",)
    assert regular_source.weights[2] > cooking_weights(3)[2]


def test_food_rarity_effect_cannot_bypass_six_star_cooking_rule() -> None:
    with pytest.raises(FoodEffectError):
        resolve_food_effect(
            "next-food-rarity",
            {"rarity": 6, "multiplier": 1.5},
        )
    with pytest.raises(FoodEffectError):
        resolve_food_effect(
            "next-pig-rarity",
            {"rarity": 1, "multiplier": 2.0},
        )


def _active_effect(
    effect_entry_id: str,
    effect_id: str,
    params: dict[str, object],
    *,
    created_at: str,
) -> ActiveFoodEffect:
    return ActiveFoodEffect(
        effect_entry_id=effect_entry_id,
        effect_id=effect_id,
        params=params,
        granted_uses=1,
        consumed_uses=0,
        expires_at="",
        created_at=created_at,
    )


def test_targeted_probability_food_only_uses_lower_rarity_as_donor() -> None:
    four_star_catch = _active_effect(
        "four-star-catch",
        "next-pig-rarity",
        {"rarity": 4, "multiplier": 2.4},
        created_at="2026-08-09T00:00:00.000Z",
    )
    catch_application = apply_catch_effects(BASE_CATCH_WEIGHTS, [four_star_catch])
    assert catch_application.weights[3] > BASE_CATCH_WEIGHTS[3]
    assert catch_application.weights[4:] == pytest.approx(BASE_CATCH_WEIGHTS[4:])

    five_star_catch = _active_effect(
        "five-star-catch",
        "next-pig-rarity",
        {"rarity": 5, "multiplier": 6.0},
        created_at="2026-08-09T00:00:00.000Z",
    )
    five_star_application = apply_catch_effects(BASE_CATCH_WEIGHTS, [five_star_catch])
    assert five_star_application.weights[4] > BASE_CATCH_WEIGHTS[4]
    assert five_star_application.weights[5] == pytest.approx(BASE_CATCH_WEIGHTS[5])

    four_star_food = _active_effect(
        "four-star-food",
        "next-food-rarity",
        {"rarity": 4, "multiplier": 2.4},
        created_at="2026-08-09T00:00:00.000Z",
    )
    base_cooking = cooking_weights(3)
    cooking_application = apply_cooking_effects(
        base_cooking,
        [four_star_food],
        source_rarity=3,
    )
    assert cooking_application.weights[3] > base_cooking[3]
    assert cooking_application.weights[4:] == pytest.approx(base_cooking[4:])


@pytest.mark.parametrize(
    "item_id",
    ("", "lucky-whistle", "super-lucky-whistle", "star-pig-radar"),
)
def test_group_probability_food_never_reduces_high_rarity_after_progression(
    item_id: str,
) -> None:
    before = catch_weights(item_id=item_id, feed_level=5, player_level=21)
    effect = _active_effect(
        "group-quality",
        "next-catch-quality",
        {"multiplier": 2.2},
        created_at="2026-08-09T00:00:00.000Z",
    )
    after = apply_catch_effects(before, [effect]).weights
    assert all(after[index] >= before[index] - 1e-10 for index in range(3, 6))


def test_same_family_catch_effects_do_not_stack_and_report_skipped() -> None:
    # 六星菜独占效果始终优先于普通概率菜，普通效果保留且不消耗。
    quality = _active_effect(
        "quality",
        "next-catch-quality",
        {"multiplier": 2.0},
        created_at="2026-08-07T00:00:00.000Z",
    )
    exact_six = _active_effect(
        "exact-six",
        "next-six-star-catch",
        {"six_star_percent": 50},
        created_at="2026-08-07T00:00:01.000Z",
    )
    rarity = _active_effect(
        "rarity",
        "next-pig-rarity",
        {"rarity": 5, "multiplier": 3.0},
        created_at="2026-08-07T00:00:02.000Z",
    )
    application = apply_catch_effects(BASE_CATCH_WEIGHTS, [quality, exact_six, rarity])
    assert application.consumed_entry_ids == ("exact-six",)
    assert len(application.summaries) == 1
    assert len(application.skipped_summaries) == 2
    assert all("独占" in text for text in application.skipped_summaries)
    # 体型组与概率组正交，可同时生效
    stature = _active_effect(
        "stature",
        "next-pig-stature",
        {"mode": "giant", "strength": 0.2},
        created_at="2026-08-07T00:00:00.000Z",
    )
    combined = apply_catch_effects(BASE_CATCH_WEIGHTS, [quality, stature])
    assert combined.consumed_entry_ids == ("quality", "stature")
    assert combined.stature_bias == pytest.approx(0.2)


def test_same_family_cooking_effects_do_not_stack_and_report_skipped() -> None:
    shift = _active_effect(
        "shift",
        "next-cook-quality",
        {"shift_percent": 8},
        created_at="2026-08-07T00:00:00.000Z",
    )
    rarity = _active_effect(
        "rarity",
        "next-food-rarity",
        {"rarity": 4, "multiplier": 2.0},
        created_at="2026-08-07T00:00:01.000Z",
    )
    application = apply_cooking_effects(
        cooking_weights(3),
        [shift, rarity],
        source_rarity=3,
    )
    assert application.consumed_entry_ids == ("shift",)
    assert len(application.skipped_summaries) == 1
    assert "未叠加" in application.skipped_summaries[0]
    # 不同名菜效果签名唯一性由目录测试覆盖；此处仅验证互斥分组


def test_six_star_exclusive_effects_override_weights_with_multi_uses() -> None:
    # 雾蓝键盘大福：固定高星分布 4/5/6 = 60/30/10，uses=5
    high_star = _active_effect(
        "high-star",
        "next-high-star-catch",
        {"uses": 5, "four_star_percent": 60, "five_star_percent": 30, "six_star_percent": 10},
        created_at="2026-08-07T00:00:00.000Z",
    )
    application = apply_catch_effects(BASE_CATCH_WEIGHTS, [high_star])
    assert application.weights == pytest.approx((0.0, 0.0, 0.0, 60.0, 30.0, 10.0))
    assert application.consumed_entry_ids == ("high-star",)
    # 彩彩修车猪慕斯：必出五星菜
    five_cook = _active_effect(
        "five-cook",
        "next-five-star-cook",
        {"uses": 5},
        created_at="2026-08-07T00:00:00.000Z",
    )
    cook_application = apply_cooking_effects(
        cooking_weights(3),
        [five_cook],
        source_rarity=3,
    )
    assert cook_application.weights == pytest.approx((0.0, 0.0, 0.0, 0.0, 100.0, 0.0))
    # 猪保千猪排轮盘：六档等概率
    even = _active_effect(
        "even",
        "even-catch-distribution",
        {"uses": 5},
        created_at="2026-08-07T00:00:00.000Z",
    )
    even_application = apply_catch_effects(BASE_CATCH_WEIGHTS, [even])
    assert all(
        pytest.approx(value) == pytest.approx(100.0 / 6)
        for value in even_application.weights
    )
    # 无六星素材时，轮盘效果把六星份额并入五星
    no_six = list(BASE_CATCH_WEIGHTS)
    no_six[5] = 0.0
    even_no_six = apply_catch_effects(no_six, [even])
    assert even_no_six.weights[4] == pytest.approx(100.0 / 6 * 2)
    assert even_no_six.weights[5] == 0.0
    # 糖醋排骨独占加权
    exclusive = _active_effect(
        "exclusive",
        "exclusive-catch-quality",
        {"multiplier": 3.0},
        created_at="2026-08-07T00:00:00.000Z",
    )
    exclusive_application = apply_catch_effects(BASE_CATCH_WEIGHTS, [exclusive])
    assert sum(exclusive_application.weights[3:]) > sum(BASE_CATCH_WEIGHTS[3:])
    # 独占效果与普通加权效果同属互斥组：只生效最早的一个
    mixed = apply_catch_effects(BASE_CATCH_WEIGHTS, [exclusive, high_star])
    assert mixed.consumed_entry_ids == ("exclusive",)
    assert len(mixed.skipped_summaries) == 1


def test_quota_reset_chance_resolves_to_one_use_grant() -> None:
    grant = resolve_food_effect("quota-reset", {"count": 1})
    assert grant.granted_uses == 1
    assert "/重置额度" in grant.summary


def test_balanced_four_and_five_star_effects_support_multi_use_and_quota_layers() -> None:
    four_star = resolve_food_effect(
        "next-catch-quality",
        {"multiplier": 1.5, "uses": 1},
    )
    five_star = resolve_food_effect(
        "next-catch-quality",
        {"multiplier": 2.2, "uses": 2},
    )
    six_cook_bonus = resolve_food_effect(
        "next-six-star-cook-bonus",
        {"bonus_percent": 22},
    )
    assert four_star.granted_uses == 1
    assert five_star.granted_uses == 2
    assert five_star.params["multiplier"] > four_star.params["multiplier"]
    assert six_cook_bonus.params == {"bonus_percent": 22.0}

    current = _active_effect(
        "current-window",
        "current-window-catches",
        {"count": 2},
        created_at="2026-08-09T00:00:00.000Z",
    )
    today = _active_effect(
        "today-window",
        "today-window-catches",
        {"count": 2},
        created_at="2026-08-09T00:00:01.000Z",
    )
    assert active_quota_effect_bonuses([current, today]) == (2, 2)


def test_ordinary_six_star_cook_bonus_stacks_then_caps_at_fifty_percent() -> None:
    bonus = _active_effect(
        "six-cook-bonus",
        "next-six-star-cook-bonus",
        {"bonus_percent": 22},
        created_at="2026-08-09T00:00:00.000Z",
    )
    item_adjusted = (0.0, 0.0, 0.0, 0.0, 78.0, 22.0)
    application = apply_cooking_effects(
        item_adjusted,
        [bonus],
        source_rarity=6,
    )
    assert application.weights == pytest.approx((0, 0, 0, 0, 56, 44))

    near_cap = apply_cooking_effects(
        (0, 0, 0, 0, 55, 45),
        [bonus],
        source_rarity=6,
    )
    assert near_cap.weights == pytest.approx((0, 0, 0, 0, 50, 50))


def test_numeric_level_and_honor_title_remain_separate_progress_tracks() -> None:
    assert level_progress(0).level == 1
    assert level_progress(50).level == 2
    assert level_progress(200).level == 3
    assert level_progress(50).title == "被猪拱"
    assert level_progress(100).title == "抓猪萌新"
    assert level_progress(200).next_threshold == 450
    huge_level = 10**100
    assert level_progress(50 * huge_level**2).level == huge_level + 1


def test_pig_attribute_labels_are_plain_language() -> None:
    assert size_label(0.05) == "迷你个体"
    assert size_label(0.95) == "超大个体"
    assert weight_label(0.05) == "轻盈"
    assert weight_label(0.95) == "重量级"


def test_choose_rarity_uses_left_closed_intervals() -> None:
    assert choose_rarity((50, 50, 0, 0, 0, 0), 0.0) is Rarity.ONE
    assert choose_rarity((50, 50, 0, 0, 0, 0), 0.499999) is Rarity.ONE
    assert choose_rarity((50, 50, 0, 0, 0, 0), 0.5) is Rarity.TWO
    assert (
        choose_rarity(
            catch_weights(six_star_available=False),
            math.nextafter(1.0, 0.0),
        )
        is Rarity.FIVE
    )


@pytest.mark.parametrize(
    "weights",
    [
        (1, 2),
        (0, 0, 0, 0, 0, 0),
        (1, 1, 1, 1, 1, -1),
    ],
)
def test_weight_validation_rejects_invalid_input(weights: tuple[float, ...]) -> None:
    with pytest.raises(DomainValidationError):
        normalize_weights(weights)


def test_access_policy_blacklist_has_priority() -> None:
    policy = AccessPolicy(
        group_whitelist=["100"],
        group_blacklist=["100"],
        user_whitelist=["200"],
        user_blacklist=[],
        denied_message="拒绝",
    )
    assert not policy.evaluate(group_id="100", user_id="200").allowed
    assert not policy.evaluate(group_id="999", user_id="200").allowed


def test_plugin_admin_supports_raw_and_platform_scoped_user_ids() -> None:
    policy = AccessPolicy(denied_message="拒绝")
    assert policy.is_admin(
        platform="qq",
        user_id="123456",
        admin_user_ids=["123456"],
    )
    assert policy.is_admin(
        platform="qq-official",
        user_id="member-openid",
        admin_user_ids=["qq-official:member-openid"],
    )
    assert not policy.is_admin(
        platform="qq-official",
        user_id="another-openid",
        admin_user_ids=["123456"],
    )


def test_effective_catch_limit_drops_consumed_old_window_extras_from_display() -> None:
    assert effective_catch_limit(
        base_limit=5,
        used_count=5,
        extra_granted=2,
        extra_consumed=0,
    ) == 7
    assert effective_catch_limit(
        base_limit=5,
        used_count=6,
        extra_granted=2,
        extra_consumed=1,
    ) == 7
    assert effective_catch_limit(
        base_limit=5,
        used_count=0,
        extra_granted=2,
        extra_consumed=2,
    ) == 5


def test_default_config_exposes_fixed_rules_and_chinese_schema() -> None:
    config = PigCatcherConfig()
    assert config.plugin.framework_phase == "6"
    assert config.catching.daily_limit == 5
    assert config.catching.cooldown_seconds == 20
    assert config.catching.quota_refresh_hours == [0, 9, 12, 19]
    assert config.catching.weights() == BASE_CATCH_WEIGHTS
    assert config.cooking.six_star_to_five_percent == 90
    assert config.cooking.six_star_to_six_percent == 10
    assert config.features.cooking_enabled is True
    assert config.features.ledger_enabled is True
    assert config.trading.gift_enabled is True
    assert config.trading.trade_enabled is True
    assert config.ranking.giant_size_threshold_cm == 120.0
    assert config.ranking.giant_weight_threshold_kg == 350.0
    schema = PigCatcherConfig.model_json_schema()
    serialized = str(schema)
    assert "启用插件" in serialized
    assert "群白名单" in serialized
    assert "目标群号" in serialized
    assert "重置当前时段" in serialized
    assert "赠送/收赠黑名单" in serialized
    assert "公告正文" in serialized
    assert config.blacklist_administration.__ui_label__ == "社交黑名单"
    assert config.announcement_administration.__ui_label__ == "群公告发送"


def test_admin_panel_operations_require_explicit_safe_inputs() -> None:
    with pytest.raises(ValidationError, match="至少选择一种"):
        PigCatcherConfig(
            blacklist_administration={
                "group_id": "100",
                "user_ids": ["member-openid"],
                "execute_blacklist_update": True,
            }
        )
    with pytest.raises(ValidationError, match="公告正文"):
        PigCatcherConfig(
            announcement_administration={
                "group_id": "100",
                "execute_send": True,
            }
        )
    with pytest.raises(ValidationError, match="必须先启用"):
        PigCatcherConfig(
            plugin={"enabled": False},
            announcement_administration={
                "group_id": "100",
                "content": "测试公告",
                "execute_send": True,
            },
        )


def test_quota_windows_follow_four_beijing_refreshes() -> None:
    cases = (
        (datetime(2026, 7, 27, 16, 0, tzinfo=UTC), 0, 9),
        (datetime(2026, 7, 28, 1, 0, tzinfo=UTC), 9, 12),
        (datetime(2026, 7, 28, 4, 0, tzinfo=UTC), 12, 19),
        (datetime(2026, 7, 28, 11, 0, tzinfo=UTC), 19, 0),
    )
    for now, start_hour, end_hour in cases:
        window = catch_quota_window(
            now,
            refresh_hours=[0, 9, 12, 19],
            timezone_name="Asia/Shanghai",
        )
        assert window.start.astimezone(timezone(timedelta(hours=8))).hour == start_hour
        assert window.end.astimezone(timezone(timedelta(hours=8))).hour == end_hour


def test_config_rejects_unsafe_paths_and_css_controls() -> None:
    with pytest.raises(ValidationError):
        PigCatcherConfig(storage={"database_filename": "../escape.sqlite3"})
    with pytest.raises(ValidationError):
        PigCatcherConfig(rendering={"font_family": "sans-serif; color: red"})
    with pytest.raises(ValidationError):
        PigCatcherConfig(rendering={"font_family": "   "})


def test_help_is_copyable_concise_text() -> None:
    text = format_help("做菜")
    assert "/做菜 [猪名#短编号]" in text
    assert "/升级 <猪饲料|厨具>" in format_help("商城")
    assert "/批量售卖 <猪猪|美食>" in format_help("商城")
    assert "【做菜指令】" in text
    assert "当前版本：" not in text
    assert "已开放抓猪" not in text
    full = format_help()
    assert "/抓猪档案" not in full
    assert "/抓猪详情" not in full
    assert "/抓猪档案" not in format_help("抓猪")
    assert "/抓猪详情" not in format_help("抓猪")
    assert "<img" not in text
