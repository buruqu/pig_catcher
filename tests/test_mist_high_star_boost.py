"""雾蓝先换位后增强：遍历720种排列，覆盖自定义概率和历史剩余次数。"""

from itertools import product

import pytest

from pig_catcher.domain.food_effects import ActiveFoodEffect, apply_catch_effects


@pytest.mark.parametrize("base", [(40, 30, 17, 8, 4, 1), (6, 5, 4, 3, 2, 1), (0, 0, 0, 1, 2, 3)])
def test_all_permutations_boost_destination_high_stars_after_shuffle(base):
    # 已用7次的旧存档无需新增参数，增强后仍只剩3次。
    effect = ActiveFoodEffect("old-mist", "shuffled-catch-distribution", {"uses": 10}, 10, 7, "", "old")
    permutations = set()
    for choices in product(*(range(size) for size in (6, 5, 4, 3, 2))):
        rolls = [(choice + 0.5) / size for choice, size in zip(choices, (6, 5, 4, 3, 2), strict=True)]
        random = iter(rolls)
        result = apply_catch_effects(
            (1, 1, 1, 1, 1, 95), (effect,), random_value=random.__next__, shuffle_base_weights=base
        )
        permutations.add(result.shuffle_permutation)
        shuffled = [base[index - 1] for index in result.shuffle_permutation]
        weighted = [value * (5 if index >= 3 else 1) for index, value in enumerate(shuffled)]
        expected = [value / sum(weighted) * 100 for value in weighted]
        assert result.weights == pytest.approx(expected)
        assert sum(result.weights) == pytest.approx(100)
        assert all(0 <= value <= 100 for value in result.weights)
        assert sum(result.weights[3:]) >= sum(shuffled[3:]) / sum(shuffled) * 100 - 1e-10
        assert result.shuffle_rolls == tuple(rolls) and next(random, None) is None
        assert result.consumed_entry_ids == ("old-mist",)
        assert "剩余 2/10 次" in " ".join(result.summaries)
    assert len(permutations) == 720
