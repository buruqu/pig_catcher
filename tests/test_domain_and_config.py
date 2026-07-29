"""领域不变量、选择器、访问控制与配置模型。"""

from __future__ import annotations

import math

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
    apply_catch_effects,
    apply_cooking_effects,
    resolve_food_effect,
)
from pig_catcher.domain.gameplay import level_progress, size_label, weight_label
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.domain.ports import MessageKeyFactory
from pig_catcher.domain.rules import (
    BASE_CATCH_WEIGHTS,
    catch_weights,
    choose_rarity,
    cooking_weights,
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
    boosted = catch_weights(feed_level=5, lucky_whistle=True)
    assert sum(boosted[2:]) > sum(baseline[2:])


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


def test_numeric_level_and_honor_title_are_separate_cosmetic_progress() -> None:
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


def test_default_config_exposes_fixed_rules_and_chinese_schema() -> None:
    config = PigCatcherConfig()
    assert config.plugin.framework_phase == "6"
    assert config.catching.daily_limit == 20
    assert config.catching.cooldown_seconds == 20
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
