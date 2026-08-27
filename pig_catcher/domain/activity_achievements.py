"""Reviewed, versioned activity achievement catalogue; never import design documents.

The JSON is runtime data. Definitions and fixed collection denominators are
validated at startup, while factual reducers live separately from presentation.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

_DATA = json.loads((Path(__file__).resolve().parent / "data/activity_achievements_v1.json").read_text(encoding="utf-8"))
ACTIVITY_REWARDS = MappingProxyType(_DATA["rewards"])
FIXED_SETS = MappingProxyType({key: frozenset(value) for key, value in _DATA["fixed_snapshots"].items()})
ACTIVITY_CATEGORIES = frozenset(_DATA["modules"].values())
ACTIVITY_IDS = frozenset(entry["id"] for entry in _DATA["entries"])
LEGACY_REGULAR_IDS = tuple(sorted(FIXED_SETS["legacy-regular-v1"]))


def definitions():
    # Deferred import deliberately avoids a registry / definition type cycle.
    from .achievements import (
        AchievementCondition,
        AchievementConditionKind,
        AchievementDefinition,
        AchievementReward,
        AchievementTier,
    )

    result = []
    for entry in _DATA["entries"]:
        condition = entry["condition"]
        snapshot = condition.get("snapshot")
        if snapshot and (snapshot not in FIXED_SETS or len(FIXED_SETS[snapshot]) != condition["target"]):
            raise ValueError(f"Invalid achievement snapshot: {entry['id']}")
        rewards = []
        for reward in entry["rewards"]:
            definition = ACTIVITY_REWARDS[reward["reward_id"]]
            rewards.append(AchievementReward(definition["kind"], reward["reward_id"], reward["quantity"]))
        result.append(
            AchievementDefinition(
                achievement_id=entry["id"],
                name=entry["name"],
                category=entry["category"],
                tier=AchievementTier(entry["tier"]),
                description=condition["description"],
                hint=entry["hint"],
                condition=AchievementCondition(
                    AchievementConditionKind(condition["kind"]),
                    condition["metric"],
                    condition["target"],
                    {
                        key: value
                        for key, value in condition.items()
                        if key not in {"kind", "metric", "target", "description"}
                    },
                ),
                rewards=tuple(rewards),
                hidden=entry["hidden"],
            )
        )
    if len(result) != len(ACTIVITY_IDS):
        raise ValueError("Duplicate activity achievement identifiers")
    return tuple(result)
