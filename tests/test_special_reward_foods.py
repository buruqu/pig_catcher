"""奖励菜保持六星、零价值，并与六星猪烹饪配方分离。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pig_catcher.assets.models import AssetManifest, AssetManifestEntry
from pig_catcher.domain.economy import generate_food_attributes, scale_food_attributes

from .test_economy import _food_entry, _pig_entry


def _reward_entry():
    entry = _food_entry(6, group_id="100", template_suffix="reward")
    entry["recipe_tags"] = ["special-reward-food", "zero-value"]
    return entry


def _manifest(entries):
    return AssetManifest.model_validate(
        {
            "manifest_version": 4,
            "catalog_id": "reward-food-tests",
            "source_label": "test",
            "entries": entries,
        }
    )


def test_reward_food_needs_no_pig_pair_but_regular_food_still_does():
    pig = _pig_entry(6, group_id="100", paired_food_template_id="food-6-group")
    food = _food_entry(6, group_id="100")
    assert len(_manifest([pig, food, _reward_entry()]).entries) == 3
    with pytest.raises(ValidationError, match="必须且只能"):
        _manifest([pig, food, _food_entry(6, group_id="100", template_suffix="unpaired")])


def test_reward_food_cannot_be_bound_as_a_cooking_recipe():
    reward = _reward_entry()
    pig = _pig_entry(6, group_id="100", paired_food_template_id=reward["template_id"])
    with pytest.raises(ValidationError, match="不能绑定特殊奖励菜"):
        _manifest([pig, reward])


@pytest.mark.parametrize("tags", [["special-reward-food"], ["zero-value"]])
def test_partial_reward_tags_are_rejected(tags):
    entry = _reward_entry()
    entry["recipe_tags"] = tags
    with pytest.raises(ValidationError, match="同时声明"):
        AssetManifestEntry.model_validate(entry)


@pytest.mark.parametrize("kind", ["pig", "common-food"])
def test_reward_tags_are_restricted_to_group_six_star_food(kind):
    entry = _pig_entry(6, group_id="100") if kind == "pig" else _food_entry(5)
    entry["recipe_tags"] = ["special-reward-food", "zero-value"]
    with pytest.raises(ValidationError, match="群专属六星美食"):
        AssetManifestEntry.model_validate(entry)


def test_zero_value_survives_generation_and_serving_scaling():
    kwargs = dict(
        rarity=6,
        template_id="food-g100-daniya-peach",
        source_weight=60,
        source_weight_percentile=0.5,
        portion_roll=0.5,
    )
    ordinary = generate_food_attributes(**kwargs)
    reward = generate_food_attributes(**kwargs, recipe_tags=("special-reward-food", "zero-value"))
    assert ordinary.official_value > 0
    assert reward.portion_weight == ordinary.portion_weight
    assert reward.official_value == scale_food_attributes(reward, multiplier=2).official_value == 0
