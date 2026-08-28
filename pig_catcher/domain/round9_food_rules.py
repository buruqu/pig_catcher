"""第九期审核通过的菜品规则：目录同步与验收的单一映射。"""

from __future__ import annotations

ROUND9_FOOD_EFFECTS: dict[str, tuple[str, dict[str, object]]] = {
    "food-r4-hot-pig": ("catch-reward-bonus", {"uses": 3, "coin_bonus": 20, "experience_multiplier": 1.5}),
    "food-r4-orange-milk": ("next-cook-quality", {"shift_percent": 10, "uses": 2}),
    "food-r4-souffle": ("next-pig-stature", {"mode": "mini", "strength": 0.35, "uses": 2}),
    "food-r4-sausage-pig": ("food-supply-pack", {"pack_id": "sausage-pig"}),
    "food-r4-pig-fries": ("food-supply-pack", {"pack_id": "pig-fries"}),
    "food-r4-kitty-burger": ("next-pig-stature", {"mode": "giant", "strength": 0.28, "uses": 3}),
    "food-r4-pig-cola": ("food-supply-pack", {"pack_id": "pig-cola"}),
    "food-r5-tiramisu": ("next-cook-quality", {"shift_percent": 25, "uses": 3}),
    "food-r5-chocolate-pig": ("food-supply-pack", {"pack_id": "pig-chocolate"}),
    "food-r5-yolk-pig": ("cook-serving-bonus", {"uses": 3, "multiplier": 1.6}),
    "food-r5-strawberry-milk": ("catch-duplication-chance", {"chance_percent": 90, "uses": 3}),
    "food-r5-burger-combo": ("food-supply-pack", {"pack_id": "pig-burger-meal"}),
}

GROUP_FOOD_PREFIXES = (
    "food-g1092931381-",
    "food-g237716658-",
    "food-qo5e5854406d0297d6feae696a13e3a339-",
    "food-qo9ea2810f378fbd7dc3219c56ceab3520-",
)

SIX_STAR_REVISIONS: dict[str, tuple[str, dict[str, object]]] = {
    "yilu-green-core-pie": ("yilu-food-lottery", {}),
    "mist-blue-keyboard-daifuku": ("shuffled-catch-distribution", {"uses": 10}),
    "juejue-pie": (
        "permanent-window-catch",
        {
            "count": 1,
            "max_bonus": 5,
            "overflow_weekly_bonus": 1,
            "overflow_coin": 12222,
            "overflow_coupon": "asset-code-change",
        },
    ),
    "daniya-bubble-jelly": (
        "permanent-six-star-progress",
        {
            "catch_bonus_per_stack": 0.2,
            "cook_bonus_per_stack": 2,
            "max_stacks": 5,
            "overflow_coin": 22222,
            "overflow_coupon": "pig-choice",
        },
    ),
}


def reviewed_food_revisions() -> dict[str, tuple[str, dict[str, object]]]:
    result = {key: (effect, dict(params)) for key, (effect, params) in ROUND9_FOOD_EFFECTS.items()}
    for prefix in GROUP_FOOD_PREFIXES:
        for suffix, (effect, params) in SIX_STAR_REVISIONS.items():
            result[prefix + suffix] = (effect, dict(params))
    return result
