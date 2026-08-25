"""领域不变量、选择器、访问控制与配置模型。"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pig_catcher.commands.help import format_help
from pig_catcher.commands.parsers import (
    parse_admin_asset_grant,
    parse_admin_asset_selector,
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
    QUOTA_EXEMPT_CATCH_EFFECTS,
    ActiveFoodEffect,
    ActiveGroupFoodEffect,
    active_quota_effect_bonuses,
    apply_catch_effects,
    apply_cooking_effects,
    apply_group_catch_effects,
    apply_group_hidden_boost,
    group_hidden_boost_chance,
    has_compatible_exclusive_group_catch_effect,
    resolve_food_effect,
)
from pig_catcher.domain.gameplay import (
    ITEM_DEFINITIONS,
    apply_veteran_experience_bonus,
    generate_pig_attributes,
    level_progress,
    size_label,
    veteran_benefits,
    weight_label,
)
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.ports import MessageKeyFactory
from pig_catcher.domain.quota import (
    catch_quota_window,
    effective_catch_limit,
    stack_catch_quota_layers,
)
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
from pig_catcher.domain.short_codes import (
    GENERATED_SHORT_CODE_LENGTH,
    SHORT_CODE_ALPHABET,
    new_short_code,
)
from pig_catcher.domain.special_content import (
    TECHNIQUE_LAPSE_BLUE,
    domain_cooking_weights,
    is_crazy_thursday,
)


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
    friendly = parse_asset_selector("粉红小香猪#pig9Fun")
    assert friendly.short_code == "PIG9FUN"


def test_asset_codes_use_full_alphanumeric_case_insensitive_policy() -> None:
    assert SHORT_CODE_ALPHABET == "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    generated = new_short_code()
    assert len(generated) == GENERATED_SHORT_CODE_LENGTH
    assert set(generated) <= set(SHORT_CODE_ALPHABET)
    assert parse_asset_selector("猪#Abc1").short_code == "ABC1"
    assert parse_asset_selector("猪#abcdefghijklmnop").short_code == "ABCDEFGHIJKLMNOP"
    query = parse_admin_asset_grant("地球猪 Pig9Fun")
    assert query.template_selector == "地球猪"
    assert query.short_code == "PIG9FUN"
    assert parse_admin_asset_selector("地球猪#pIg9fUn") == "地球猪#PIG9FUN"


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
    named = parse_batch_sale_query("美食 猪寿司拼盘")
    assert named.asset_kind.value == "food"
    assert named.display_name == "猪寿司拼盘"
    assert named.rarity is None
    with pytest.raises(DomainValidationError, match="特定名称"):
        parse_batch_sale_query("猪猪 地球猪")


@pytest.mark.parametrize(
    "value",
    ["#A19F2C3D", "猪#BAD", "猪#BAD-1", "猪#ABCDEFGHIJKLMNOPQ"],
)
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
        "天逆鉾": 1000,
    }
    assert len(ITEM_DEFINITIONS) == 16
    assert {item.display_name: item.price for item in ITEM_DEFINITIONS} == expected_prices
    assert len({item.item_id for item in ITEM_DEFINITIONS}) == 16
    assert len({item.effect_summary for item in ITEM_DEFINITIONS}) == 16
    assert all(item.action_type in {"catching", "cooking"} for item in ITEM_DEFINITIONS)
    assert expected_prices["超级幸运猪哨"] < 2000
    assert expected_prices["超级主厨香料"] < 2000


def test_phase8_item_food_and_calendar_rules_are_explicit() -> None:
    spear = next(item for item in ITEM_DEFINITIONS if item.display_name == "天逆鉾")
    assert spear.item_id == "inverted-spear-of-heaven"
    assert spear.action_type == "cooking"
    assert spear.effect_summary == "解除术式"

    technique = resolve_food_effect(
        "technique-permit",
        {"technique_id": TECHNIQUE_LAPSE_BLUE},
    )
    assert technique.granted_uses == 1
    assert "/术式顺转 苍" in technique.summary
    tribute = resolve_food_effect(
        "group-coin-tribute",
        {"coin_per_player": 50},
    )
    assert tribute.params == {"coin_per_player": 50}

    assert is_crazy_thursday(
        datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
        timezone_name="Asia/Shanghai",
    )
    assert not is_crazy_thursday(
        datetime(2026, 8, 26, 0, 0, tzinfo=UTC),
        timezone_name="Asia/Shanghai",
    )
    assert domain_cooking_weights(6) == (0.0, 0.0, 0.0, 0.0, 75.0, 25.0)


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
    granted_uses: int = 1,
    consumed_uses: int = 0,
) -> ActiveFoodEffect:
    return ActiveFoodEffect(
        effect_entry_id=effect_entry_id,
        effect_id=effect_id,
        params=params,
        granted_uses=granted_uses,
        consumed_uses=consumed_uses,
        expires_at="",
        created_at=created_at,
    )


def _active_group_effect(
    entry_id: str,
    effect_id: str,
    params: dict[str, object],
    *,
    source_user_id: str,
    source_display_name: str,
    granted_uses: int = 0,
    consumed_uses: int = 0,
    created_at: str = "2026-08-11T04:00:00.000Z",
) -> ActiveGroupFoodEffect:
    return ActiveGroupFoodEffect(
        group_effect_entry_id=entry_id,
        effect_id=effect_id,
        params=params,
        granted_uses_per_player=granted_uses,
        consumed_uses=consumed_uses,
        source_user_id=source_user_id,
        source_display_name=source_display_name,
        starts_at="2026-08-11T04:00:00.000Z",
        expires_at="2026-08-12T04:00:00.000Z",
        created_at=created_at,
    )


def test_group_six_star_catch_effects_are_exclusive_and_show_activator_nickname() -> None:
    cloud_pot = _active_group_effect(
        "cloud-pot",
        "group-next-exclusive-high-star-catch",
        {
            "five_star_multiplier": 8,
            "six_star_multiplier": 8,
            "uses_per_player": 1,
            "self_coin": 18888,
            "other_coin": 1680,
            "source_label": "神龙化猪七星云海锅",
        },
        source_user_id="OFFICIAL_OPEN_ID",
        source_display_name="数佳",
        granted_uses=1,
    )
    weaker = _active_group_effect(
        "omelette",
        "group-window-high-star-boost",
        {
            "five_star_multiplier": 1.004,
            "six_star_multiplier": 1.004,
            "coin_per_player": 1004,
            "dedicated_catches": 1,
            "dedicated_only": True,
            "source_label": "猪鼻蛋包饭",
        },
        source_user_id="other-user",
        source_display_name="其他群友",
        granted_uses=1,
    )
    assert has_compatible_exclusive_group_catch_effect((cloud_pot, weaker))
    applied = apply_group_catch_effects(BASE_CATCH_WEIGHTS, (cloud_pot, weaker))
    assert applied.exclusive is True
    assert applied.consumed_entry_ids == ("cloud-pot",)
    assert applied.weights[4] > BASE_CATCH_WEIGHTS[4]
    assert applied.weights[5] > BASE_CATCH_WEIGHTS[5]
    assert "发动群友：数佳" in applied.summaries[0]
    assert "OFFICIAL_OPEN_ID" not in applied.summaries[0]
    assert all("other-user" not in text for text in applied.skipped_summaries)
    identifier_only = _active_group_effect(
        "identifier-only",
        "group-next-exclusive-high-star-catch",
        cloud_pot.params,
        source_user_id="OFFICIAL_OPEN_ID",
        source_display_name="OFFICIAL_OPEN_ID",
        granted_uses=1,
    )
    masked = apply_group_catch_effects(BASE_CATCH_WEIGHTS, (identifier_only,))
    assert "发动群友：未命名群友" in masked.summaries[0]
    assert "OFFICIAL_OPEN_ID" not in masked.summaries[0]


def test_group_window_effect_uses_strongest_multiplier_and_dedicated_quota() -> None:
    omelette = _active_group_effect(
        "omelette",
        "group-window-high-star-boost",
        {
            "five_star_multiplier": 1.004,
            "six_star_multiplier": 1.004,
            "coin_per_player": 1004,
            "dedicated_catches": 1,
            "dedicated_only": True,
            "source_label": "猪鼻蛋包饭",
        },
        source_user_id="1004",
        source_display_name="猪鼻哥",
        granted_uses=1,
        created_at="2026-08-11T04:00:00.000Z",
    )
    ribs = _active_group_effect(
        "ribs",
        "group-window-high-star-boost",
        {
            "five_star_multiplier": 1.007,
            "six_star_multiplier": 1.007,
            "coin_per_player": 1007,
            "dedicated_catches": 10,
            "source_label": "糖醋排骨",
            "hidden_boost_chance_percent": 10,
            "hidden_five_star_multiplier": 10.04,
            "hidden_six_star_multiplier": 10.04,
        },
        source_user_id="1455722694",
        source_display_name="千早の花火",
        granted_uses=10,
        consumed_uses=3,
        created_at="2026-08-11T04:00:01.000Z",
    )
    ordinary_food = apply_catch_effects(
        BASE_CATCH_WEIGHTS,
        [
            _active_effect(
                "ordinary-food",
                "next-catch-quality",
                {"multiplier": 2.2},
                created_at="2026-08-11T03:59:59.000Z",
            )
        ],
    )
    applied = apply_group_catch_effects(ordinary_food.weights, (omelette, ribs))
    assert applied.exclusive is False
    assert applied.consumed_entry_ids == ()
    assert applied.dedicated_entry_id == "ribs"
    assert applied.weights[4] >= ordinary_food.weights[4]
    assert applied.weights[5] >= ordinary_food.weights[5]
    assert "发动群友：千早の花火" in applied.summaries[0]
    assert "1455722694" not in applied.summaries[0]
    assert "本次结算后专属抓猪额度剩余 6/10 次" in applied.summaries[0]
    assert any("只取最高倍率" in text for text in applied.skipped_summaries)
    assert group_hidden_boost_chance(applied, (omelette, ribs)) == pytest.approx(10)
    missed = apply_group_hidden_boost(applied, (omelette, ribs), roll=0.10)
    assert missed.hidden_boost_triggered is False
    assert missed.weights == pytest.approx(applied.weights)
    boosted = apply_group_hidden_boost(applied, (omelette, ribs), roll=0.0999)
    assert boosted.hidden_boost_triggered is True
    direct_hidden = apply_monotonic_high_rarity_multipliers(
        ordinary_food.weights,
        (1.0, 1.0, 1.0, 1.0, 10.04, 10.04),
    )
    assert boosted.weights == pytest.approx(direct_hidden)
    assert "隐藏效果爆发" in boosted.summaries[-1]
    assert "×10.04/×10.04" in boosted.summaries[-1]

    exhausted_ribs = replace(ribs, consumed_uses=10)
    fallback = apply_group_catch_effects(
        ordinary_food.weights,
        (omelette, exhausted_ribs),
    )
    assert fallback.dedicated_entry_id == "omelette"
    assert "糖醋排骨全群加成" in fallback.summaries[0]
    assert "猪鼻蛋包饭全群额外抓猪" in fallback.summaries[1]
    assert "已由更高的 糖醋排骨群倍率覆盖" in fallback.summaries[1]
    assert all("猪鼻蛋包饭" not in text for text in fallback.skipped_summaries)


def test_pig_nose_group_boost_stops_after_its_extra_catch() -> None:
    params = {
        "five_star_multiplier": 1.004,
        "six_star_multiplier": 1.004,
        "coin_per_player": 1004,
        "dedicated_catches": 1,
        "dedicated_only": True,
        "source_label": "猪鼻蛋包饭",
    }
    available = _active_group_effect(
        "omelette",
        "group-window-high-star-boost",
        params,
        source_user_id="1004",
        source_display_name="猪鼻哥",
        granted_uses=1,
    )
    applied = apply_group_catch_effects(BASE_CATCH_WEIGHTS, (available,))
    assert applied.dedicated_entry_id == "omelette"
    assert applied.weights[4] > BASE_CATCH_WEIGHTS[4]
    assert applied.weights[5] > BASE_CATCH_WEIGHTS[5]
    assert "本次不消耗正常抓猪额度" in applied.summaries[0]
    assert "额外抓猪机会剩余 0/1 次" in applied.summaries[0]

    consumed = replace(available, consumed_uses=1)
    exhausted = apply_group_catch_effects(BASE_CATCH_WEIGHTS, (consumed,))
    assert exhausted.dedicated_entry_id == ""
    assert exhausted.weights == pytest.approx(BASE_CATCH_WEIGHTS)
    assert exhausted.summaries == ()


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


def test_effect_resolution_sorts_by_eaten_time_before_selecting_group() -> None:
    older_catch = _active_effect(
        "older-catch",
        "next-catch-quality",
        {"multiplier": 2.0},
        created_at="2026-08-07T00:00:00.000Z",
    )
    newer_catch = _active_effect(
        "newer-catch",
        "next-pig-rarity",
        {"rarity": 5, "multiplier": 3.0},
        created_at="2026-08-07T00:00:01.000Z",
    )
    catch_application = apply_catch_effects(
        BASE_CATCH_WEIGHTS,
        [newer_catch, older_catch],
    )
    assert catch_application.consumed_entry_ids == ("older-catch",)

    older_cook = _active_effect(
        "older-cook",
        "next-cook-quality",
        {"shift_percent": 8},
        created_at="2026-08-07T00:00:00.000Z",
    )
    newer_cook = _active_effect(
        "newer-cook",
        "next-food-rarity",
        {"rarity": 4, "multiplier": 2.0},
        created_at="2026-08-07T00:00:01.000Z",
    )
    cook_application = apply_cooking_effects(
        cooking_weights(3),
        [newer_cook, older_cook],
        source_rarity=3,
    )
    assert cook_application.consumed_entry_ids == ("older-cook",)


def test_six_star_exclusive_effects_override_weights_with_multi_uses() -> None:
    assert QUOTA_EXEMPT_CATCH_EFFECTS == {
        "next-six-star-catch",
        "next-high-star-catch",
        "even-catch-distribution",
    }
    # 雾蓝键盘大福：固定高星分布 4/5/6 = 60/30/10，uses=10
    high_star = _active_effect(
        "high-star",
        "next-high-star-catch",
        {
            "uses": 10,
            "four_star_percent": 60,
            "five_star_percent": 30,
            "six_star_percent": 10,
            "last_use_six_star_percent": 50,
        },
        created_at="2026-08-07T00:00:00.000Z",
        granted_uses=10,
    )
    application = apply_catch_effects(BASE_CATCH_WEIGHTS, [high_star])
    assert application.weights == pytest.approx((0.0, 0.0, 0.0, 60.0, 30.0, 10.0))
    assert application.consumed_entry_ids == ("high-star",)
    high_star_last = _active_effect(
        "high-star-last",
        "next-high-star-catch",
        high_star.params,
        created_at="2026-08-07T00:00:00.000Z",
        granted_uses=10,
        consumed_uses=9,
    )
    high_star_last_application = apply_catch_effects(
        BASE_CATCH_WEIGHTS,
        [high_star_last],
    )
    assert high_star_last_application.weights == pytest.approx(
        (0.0, 0.0, 0.0, 20.0, 30.0, 50.0)
    )
    assert "小保底触发" in high_star_last_application.summaries[0]
    # 彩彩修车猪慕斯：必出五星菜
    five_cook = _active_effect(
        "five-cook",
        "next-five-star-cook",
        {"uses": 10},
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
        {"uses": 10, "last_use_six_star_percent": 50},
        created_at="2026-08-07T00:00:00.000Z",
        granted_uses=10,
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
    even_last = _active_effect(
        "even-last",
        "even-catch-distribution",
        even.params,
        created_at="2026-08-07T00:00:00.000Z",
        granted_uses=10,
        consumed_uses=9,
    )
    even_last_application = apply_catch_effects(BASE_CATCH_WEIGHTS, [even_last])
    assert even_last_application.weights == pytest.approx(
        (10.0, 10.0, 10.0, 10.0, 10.0, 50.0)
    )
    assert "小保底触发" in even_last_application.summaries[0]
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
        {"bonus_percent": 15},
    )
    assert four_star.granted_uses == 1
    assert five_star.granted_uses == 2
    assert five_star.params["multiplier"] > four_star.params["multiplier"]
    assert six_cook_bonus.params == {"bonus_percent": 15.0}

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
        {"bonus_percent": 15},
        created_at="2026-08-09T00:00:00.000Z",
    )
    item_adjusted = (0.0, 0.0, 0.0, 0.0, 80.0, 20.0)
    application = apply_cooking_effects(
        item_adjusted,
        [bonus],
        source_rarity=6,
    )
    assert application.weights == pytest.approx((0, 0, 0, 0, 65, 35))

    near_cap = apply_cooking_effects(
        (0, 0, 0, 0, 55, 45),
        [bonus],
        source_rarity=6,
    )
    assert near_cap.weights == pytest.approx((0, 0, 0, 0, 50, 50))


def test_expanded_catch_food_effects_have_exact_monotonic_results() -> None:
    small_six = _active_effect(
        "pig-skin-milk",
        "next-small-six-star-catch",
        {"bonus_percent": 15},
        created_at="2026-08-11T00:00:00.000Z",
    )
    small_six_result = apply_catch_effects(BASE_CATCH_WEIGHTS, [small_six])
    assert small_six_result.weights[3:6] == pytest.approx((8.0, 4.0, 16.0))

    giant = _active_effect(
        "giant-tangyuan",
        "next-giant-five-star-catch",
        {
            "five_star_multiplier": 3.0,
            "stature_bias": 0.5,
            "giant_template_multiplier": 4.0,
        },
        created_at="2026-08-11T00:00:01.000Z",
    )
    giant_result = apply_catch_effects(BASE_CATCH_WEIGHTS, [giant])
    assert giant_result.weights[4] == pytest.approx(100.0 / 9.0)
    assert giant_result.weights[5] == pytest.approx(1.0)
    assert giant_result.stature_bias == pytest.approx(0.5)
    assert giant_result.giant_template_multiplier == pytest.approx(4.0)

    collaboration = _active_effect(
        "collaboration-stew",
        "next-collaboration-catch",
        {"three_star_percent": 15, "four_star_percent": 55, "five_star_percent": 30},
        created_at="2026-08-11T00:00:02.000Z",
    )
    collaboration_result = apply_catch_effects(BASE_CATCH_WEIGHTS, [collaboration])
    assert collaboration_result.weights == pytest.approx((0, 0, 15, 55, 30, 0))
    assert collaboration_result.collaboration_only is True

    high_pair = _active_effect(
        "pig-cookie",
        "next-five-six-star-catch",
        {"five_star_bonus_percent": 5, "six_star_bonus_percent": 3},
        created_at="2026-08-11T00:00:03.000Z",
    )
    high_pair_result = apply_catch_effects(BASE_CATCH_WEIGHTS, [high_pair])
    assert high_pair_result.weights[3:] == pytest.approx((8, 9, 4))
    assert sum(high_pair_result.weights[:3]) == pytest.approx(79)

    duplication = _active_effect(
        "duplication",
        "catch-duplication-chance",
        {"chance_percent": 55, "uses": 2},
        granted_uses=2,
        created_at="2026-08-11T00:00:04.000Z",
    )
    duplicated = apply_catch_effects(BASE_CATCH_WEIGHTS, [duplication])
    assert duplicated.weights == pytest.approx(BASE_CATCH_WEIGHTS)
    assert duplicated.duplicate_chance_percent == pytest.approx(55)
    assert duplicated.duplication_entry_id == "duplication"
    assert duplicated.consumed_entry_ids == ("duplication",)

    mist = _active_effect(
        "mist",
        "next-high-star-catch",
        {
            "uses": 5,
            "four_star_percent": 61.5385,
            "five_star_percent": 30.7692,
            "six_star_percent": 7.6923,
            "current_window_only": True,
        },
        granted_uses=5,
        created_at="2026-08-11T00:00:05.000Z",
    )
    mist_result = apply_catch_effects(
        BASE_CATCH_WEIGHTS,
        [duplication, mist],
    )
    assert mist_result.weights == pytest.approx(
        (0, 0, 0, 61.5385, 30.7692, 7.6923),
        abs=0.0001,
    )
    assert mist_result.consumed_entry_ids == ("mist",)
    assert any("独占" in summary for summary in mist_result.skipped_summaries)

    guaranteed = _active_effect(
        "guaranteed-six",
        "next-guaranteed-six-star-catch",
        {},
        created_at="2026-08-11T00:00:06.000Z",
    )
    guaranteed_result = apply_catch_effects(
        BASE_CATCH_WEIGHTS,
        [guaranteed],
    )
    assert guaranteed_result.weights == pytest.approx((0, 0, 0, 0, 0, 100))
    assert "next-guaranteed-six-star-catch" not in QUOTA_EXEMPT_CATCH_EFFECTS

    rolling_quota = _active_effect(
        "rolling-quota",
        "rolling-day-window-catches",
        {"count": 4},
        created_at="2026-08-11T00:00:07.000Z",
    )
    assert active_quota_effect_bonuses([rolling_quota]) == (4, 0)


def test_expanded_cooking_food_effects_include_five_dumpling_layers() -> None:
    five_star_target = _active_effect(
        "roe-gunkan",
        "next-food-rarity",
        {"rarity": 5, "multiplier": 2.0},
        created_at="2026-08-11T00:00:00.000Z",
    )
    targeted = apply_cooking_effects(
        cooking_weights(3),
        [five_star_target],
        source_rarity=3,
    )
    assert targeted.weights[4] == pytest.approx(200.0 / 51.0)
    assert targeted.weights[5] == 0.0

    extreme = _active_effect(
        "berry-cake",
        "next-extreme-five-star-cook",
        {"five_star_percent": 85},
        created_at="2026-08-11T00:00:01.000Z",
    )
    for source_rarity in range(1, 6):
        result = apply_cooking_effects(
            cooking_weights(source_rarity),
            [extreme],
            source_rarity=source_rarity,
        )
        assert result.weights[4] == pytest.approx(85.0)
        assert result.weights[5] == 0.0

    six_ways = _active_effect(
        "six-ways",
        "next-six-star-cook-bonus",
        {"bonus_percent": 15},
        created_at="2026-08-11T00:00:02.000Z",
    )
    dumplings = [
        _active_effect(
            f"dumpling-{index}",
            "next-stackable-six-star-cook-bonus",
            {"bonus_percent": 1, "max_stacks": 5},
            created_at=f"2026-08-11T00:00:0{index + 2}.000Z",
        )
        for index in range(1, 6)
    ]
    stacked = apply_cooking_effects(
        (0, 0, 0, 0, 80, 20),
        [six_ways, *dumplings],
        source_rarity=6,
    )
    assert stacked.weights == pytest.approx((0, 0, 0, 0, 60, 40))
    assert stacked.consumed_entry_ids == (
        "six-ways",
        "dumpling-1",
        "dumpling-2",
        "dumpling-3",
        "dumpling-4",
        "dumpling-5",
    )
    assert any("猪饺叠加 5 层" in summary for summary in stacked.summaries)

    repair = _active_effect(
        "repair",
        "six-star-cook-failure-return",
        {"uses": 3, "return_chance_percent": 75},
        granted_uses=3,
        created_at="2026-08-11T00:00:08.000Z",
    )
    protected = apply_cooking_effects(
        cooking_weights(6),
        [repair],
        source_rarity=6,
    )
    assert protected.weights == pytest.approx(cooking_weights(6))
    assert protected.consumed_entry_ids == ()
    assert "剩余 3/3 次" in protected.summaries[0]

    roulette = resolve_food_effect("roulette-chances", {"count": 3})
    assert roulette.granted_uses == 1
    assert "/转轮盘" in roulette.summary


def test_numeric_level_and_honor_title_remain_separate_progress_tracks() -> None:
    assert level_progress(0).level == 1
    assert level_progress(50).level == 2
    assert level_progress(200).level == 3
    assert level_progress(50).title == "被猪拱"
    assert level_progress(100).title == "抓猪萌新"
    assert level_progress(200).next_threshold == 450
    assert level_progress(28800).title == "百猪名捕"
    assert level_progress(490050).title == "抓猪永恒传说"
    huge_level = 10**100
    assert level_progress(50 * huge_level**2).level == huge_level + 1


def test_veteran_benefits_start_after_probability_cap_and_are_bounded() -> None:
    assert veteran_benefits(20).tier == 0
    first = veteran_benefits(21)
    assert first.catch_coin_bonus == 1
    assert first.cook_coin_bonus == 2
    assert first.experience_bonus_percent == 5
    assert first.next_tier_level == 31
    assert apply_veteran_experience_bonus(45, first) == 47
    maximum = veteran_benefits(100)
    assert maximum.tier == 5
    assert maximum.catch_coin_bonus == 5
    assert maximum.cook_coin_bonus == 10
    assert maximum.experience_bonus_percent == 25
    assert maximum.next_tier_level is None


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


def test_all_normal_quota_food_layers_stack_without_touching_dedicated_uses() -> None:
    layers = stack_catch_quota_layers(
        configured_base=5,
        permanent_bonus=1,
        weekly_bonus=5,
        current_window_bonus=2,
        today_window_bonus=2,
        extra_granted=3,
        extra_consumed=1,
    )
    assert layers.base_window_limit == 15
    assert layers.effective_limit(used_count=4) == 17
    assert layers.effective_limit(used_count=16) == 18


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
    assert config.regulation.mode == "自动执行"
    assert config.regulation.enabled_scope_ids == [
        "qq:237716658",
        "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
    ]
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
    assert "自动监管" in serialized
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
    assert "/做菜 [猪名[#短编号]]" in text
    assert "/升级 <猪饲料|厨具>" in format_help("商城")
    assert "/批量售卖 <猪猪|美食>" in format_help("商城")
    assert "【做菜指令】" in text
    assert "当前版本：" not in text
    assert "已开放抓猪" not in text
    full = format_help()
    assert "/抓猪档案" not in full
    assert "/抓猪详情" not in full
    assert "/猪猪详情 <猪名#短编号>" in full
    assert "/抓猪档案" not in format_help("抓猪")
    assert "/抓猪详情" not in format_help("抓猪")
    assert "<img" not in text
