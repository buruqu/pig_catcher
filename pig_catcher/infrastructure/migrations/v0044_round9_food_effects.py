"""Schema44：第九期效果与未用完雾蓝次数转换；不改历史消费或资产属性。"""

import json

from .model import Migration

# 迁移快照固定在此处，不从未来可能更新的正式目录导入数值。
_PUBLIC = {
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
_GROUP_PREFIXES = (
    "food-g1092931381-",
    "food-g237716658-",
    "food-qo5e5854406d0297d6feae696a13e3a339-",
    "food-qo9ea2810f378fbd7dc3219c56ceab3520-",
)
_SIX = {
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
_FROZEN_RULES = {**_PUBLIC, **{prefix + suffix: rule for prefix in _GROUP_PREFIXES for suffix, rule in _SIX.items()}}
_UPDATES: list[str] = []
for _template, (_effect, _params) in _FROZEN_RULES.items():
    _encoded = json.dumps(_params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for _table in ("food_templates", "food_instances"):
        _active = "AND state IN ('active', 'locked-for-trade')" if _table == "food_instances" else ""
        _UPDATES.append(
            f"UPDATE {_table} SET effect_id='{_effect}', effect_params_json='{_encoded}', "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            f"WHERE template_id='{_template}' {_active}"
        )
_MIST_IDS = ",".join(f"'{prefix}mist-blue-keyboard-daifuku'" for prefix in _GROUP_PREFIXES)

MIGRATION_0044 = Migration(
    version=44,
    name="round9-food-effects",
    statements=(
        *_UPDATES,
        f"""
        UPDATE player_food_effects
        SET effect_id='shuffled-catch-distribution',
            params_json='{{"uses":' || granted_uses || '}}',
            expires_at=NULL,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE effect_id='next-high-star-catch'
          AND consumed_uses < granted_uses
          AND (expires_at IS NULL OR expires_at='' OR julianday(expires_at)>julianday('now'))
          AND source_food_instance_id IN (
              SELECT food_instance_id FROM food_instances WHERE template_id IN ({_MIST_IDS})
          )
    """,
    ),
)
