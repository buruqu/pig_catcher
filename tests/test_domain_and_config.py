"""领域不变量、选择器、访问控制与配置模型。"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from pig_catcher.commands.help import format_help
from pig_catcher.config import AccessPolicy, PigCatcherConfig
from pig_catcher.domain.enums import Rarity
from pig_catcher.domain.errors import (
    DomainValidationError,
    MissingMessageIdError,
    ScopeValidationError,
    SelectorValidationError,
)
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
    assert sum(available) == pytest.approx(100)
    assert sum(unavailable) == pytest.approx(100)
    assert unavailable[5] == 0
    assert unavailable[4] > available[4]


def test_feed_and_lucky_item_improve_high_rarity_share() -> None:
    baseline = catch_weights(feed_level=0, lucky_whistle=False)
    boosted = catch_weights(feed_level=5, lucky_whistle=True)
    assert sum(boosted[2:]) > sum(baseline[2:])


def test_six_star_cooking_rule_is_fixed() -> None:
    assert cooking_weights(Rarity.SIX) == (0.0, 0.0, 0.0, 0.0, 90.0, 10.0)
    assert all(weights[5] == 0 for rarity, weights in ((r, cooking_weights(r)) for r in range(1, 6)))


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
    assert config.plugin.framework_phase == "2B"
    assert config.cooking.six_star_to_five_percent == 90
    assert config.cooking.six_star_to_six_percent == 10
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


def test_help_is_copyable_text_and_marks_gameplay_commands_unavailable() -> None:
    text = format_help("做菜")
    assert "/做菜 <猪名#短编号>" in text
    assert "粉红小香猪#A19F2C3D" in text
    assert "当前仅开放 /抓猪帮助" in text
    assert "<img" not in text
