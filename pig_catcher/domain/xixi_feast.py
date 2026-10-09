"""西西星酪及其专属奖励的固定规则。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import FoodEffectError
from .food_lottery import validated_roll

XIXI_STAR_CHEESE_LOTTERY = "xixi-star-cheese-lottery"
XIXI_TARGETED_CATCH = "xixi-targeted-catch"
XIXI_SIX_STAR_COOK = "xixi-six-star-cook"
DANIYA_BIRTHDAY_FEAST = "daniya-birthday-feast"


def choose_star_cheese_prize(roll: float) -> str:
    value = validated_roll(roll)
    if value < 0.1:
        return "five-star"
    if value < 0.5:
        return "daniya-peach"
    if value < 0.9:
        return "xixi-mandarin"
    return "daniya-birthday-cake"


def scoped_template_id(scope_id: str, kind: str, suffix: str) -> str:
    try:
        platform, raw_id = scope_id.split(":", 1)
    except ValueError as exc:
        raise FoodEffectError("当前作用域不支持西西专属模板。") from exc
    if platform not in {"qq", "qq-official"} or not raw_id:
        raise FoodEffectError("当前作用域不支持西西专属模板。")
    prefix = "g" if platform == "qq" else "qo"
    return f"{kind}-{prefix}{raw_id.lower()}-{suffix}"


def targeted_catch_templates(
    templates: Sequence[Mapping[str, object]], scope_id: str, mode: str
) -> list[Mapping[str, object]]:
    if mode not in {"pair", "other-six"}:
        raise FoodEffectError("西西定向抓猪参数无效。")
    pair = {scoped_template_id(scope_id, "pig", name) for name in ("xixi", "daniya")}
    result = [
        row
        for row in templates
        if int(row["rarity"]) == 6
        and ((str(row["template_id"]) in pair) if mode == "pair" else (str(row["template_id"]) not in pair))
    ]
    if not result or (mode == "pair" and {str(row["template_id"]) for row in result} != pair):
        raise FoodEffectError("当前群缺少西西美食指定的有效六星猪，本次未消耗效果或次数。")
    return result
