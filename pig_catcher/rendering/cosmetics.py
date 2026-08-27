"""Original code-native achievements: postmarks, stage tickets and notebooks.

No official band logos or externally fetched images are used. Stable reward IDs
select the visual identity; neither names from chat nor arbitrary CSS are trusted.
"""

from ..domain.activity_achievements import ACTIVITY_REWARDS

_FRAMES = {
    "frame-five-postmarks": ("✉", "#588b80", "postmarks"),
    "frame-five-city-lights": ("♪", "#a45c92", "lights"),
    "frame-nine-colors": ("♫", "#9d65a9", "nine-colors"),
    "frame-arena-notebook": ("⚖", "#5a7895", "notebook"),
    "frame-training-graduation": ("Ⅴ", "#af8440", "graduation"),
    "frame-triple-realization": ("◎", "#8d6bbb", "realization"),
    "frame-three-books": ("Ⅲ", "#ab567e", "three-books"),
}
_BADGES = dict(
    zip(
        (
            "badge-five-postmarks",
            "badge-big-small-travel",
            "badge-duet-tickets",
            "badge-front-backstage",
            "badge-first-bout",
            "badge-five-opponents",
            "badge-two-paths",
            "badge-nine-moves",
            "badge-ten-moves",
            "badge-three-loans",
        ),
        ("✉", "♧", "♫", "◐", "⚖", "Ⅴ", "Ⅱ", "Ⅸ", "Ⅹ", "↻"),
        strict=True,
    )
)


def cosmetic_detail(reward_id: str) -> dict:
    reward_id = next((key for key, item in ACTIVITY_REWARDS.items() if item["name"] == reward_id), reward_id)
    definition = ACTIVITY_REWARDS.get(reward_id, {})
    glyph, color, family = _FRAMES.get(reward_id, (_BADGES.get(reward_id, "✦"), "#af5a80", "keepsake"))
    return {"name": definition.get("name", reward_id), "glyph": glyph, "color": color, "family": family}


def cosmetic_cards(rewards) -> tuple[dict, ...]:
    return tuple(
        {"id": r.reward_id, **cosmetic_detail(r.reward_id)}
        for r in rewards
        if r.reward_id in ACTIVITY_REWARDS and r.reward_type in {"title", "frame", "badge"}
    )
