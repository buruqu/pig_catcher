"""特殊奖励美食的模板标签与价值规则。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

SPECIAL_REWARD_FOOD_TAG = "special-reward-food"
ZERO_VALUE_FOOD_TAG = "zero-value"


def template_recipe_tags(template: Mapping[str, object]) -> tuple[str, ...]:
    """读取已导入模板的食谱标签，兼容尚无标签的旧素材。"""

    payload = json.loads(str(template.get("recipe_tags_json") or "[]"))
    if not isinstance(payload, list) or any(not isinstance(tag, str) for tag in payload):
        raise ValueError("美食模板食谱标签必须为字符串列表")
    return tuple(payload)


def is_reward_only_food(tags: Iterable[str]) -> bool:
    return SPECIAL_REWARD_FOOD_TAG in tags


def is_zero_value_food(tags: Iterable[str]) -> bool:
    return ZERO_VALUE_FOOD_TAG in tags
