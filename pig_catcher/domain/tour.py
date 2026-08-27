"""巡演纯评分器：不读数据库、不消费旧效果，不按星级或同团人数乘分。"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from .tour_catalog import (
    CHARACTERS,
    ENSEMBLES,
    ENSEMBLES_BY_ID,
    SCORE_CAPS,
    SCORE_NAMES,
    SONGS_BY_ID,
    THEMES_BY_ID,
    TOOLS_BY_ID,
    TOUR_VERSION,
    VENUES_BY_ID,
    Ensemble,
    TourError,
    grade,
    training_level,
)


def canonical_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """趣味形态同台时只用首个实例代表该角色，不叠加其培养或招牌。"""
    result, identities = [], set()
    for member in members:
        character = CHARACTERS.get(member["template_id"])
        if character is None:
            raise TourError(f"{member['name']}还没有登记音乐角色身份，不能占器乐席位。")
        if character.identity not in identities:
            result.append(member)
            identities.add(character.identity)
    return result


def validate_formation(members: list[dict[str, Any]], center: str = "") -> list[dict[str, Any]]:
    if not 3 <= len(members) <= 5:
        raise TourError("每套阵容需要3至5只音乐角色猪。")
    if len({member["pig_instance_id"] for member in members}) != len(members):
        raise TourError("同一个猪实例不能同时占两个位置。")
    unique = canonical_members(members)
    if len(unique) < 3:
        raise TourError("至少需要三位不同角色；同角色形态不重复算成员。")
    roles = set().union(*(CHARACTERS[member["template_id"]].roles for member in unique))
    missing = {"主旋律", "节奏", "伴奏"} - roles
    if missing:
        raise TourError("阵容还缺少职能：" + "、".join(sorted(missing)) + "。不要求固定键盘位。")
    if center:
        selected = next((member for member in unique if member["pig_instance_id"] == center), None)
        if selected is None or "主旋律" not in CHARACTERS[selected["template_id"]].roles:
            raise TourError("中心位需要选择本阵容中负责主旋律的角色代表实例。")
    return unique


def ensemble_available(ensemble: Ensemble, members: list[dict[str, Any]]) -> bool:
    characters = [CHARACTERS[member["template_id"]] for member in canonical_members(members)]
    ids = {character.identity for character in characters}
    return set(ensemble.identities) <= ids and (
        not ensemble.band
        or any(
            character.band == ensemble.band and character.identity not in ensemble.identities
            for character in characters
        )
    )


def validate_plan(plan: dict, members: list[dict], *, fans: int) -> None:
    if plan["theme"] not in THEMES_BY_ID or plan["venue"] not in VENUES_BY_ID:
        raise TourError("巡演主题或场地没有登记。")
    venue = VENUES_BY_ID[plan["venue"]]
    if fans < venue.fans:
        raise TourError(f"{venue.name}需要累计{venue.fans}粉丝，目前尚未解锁。")
    songs = plan["songs"]
    if len(songs) != 3 or len(set(songs)) != 3 or any(song not in SONGS_BY_ID for song in songs):
        raise TourError("每站需要三首不同的已登记原创歌曲，依次为开场、中段和终曲。")
    unique = canonical_members(members)
    identities = {CHARACTERS[member["template_id"]].identity for member in unique}
    highlights = plan["highlights"]
    if len(highlights) > 2 or len(set(highlights)) != len(highlights) or not set(highlights) <= identities:
        raise TourError("每站最多两个不同角色的高光位，并且必须在当前阵容。")
    combo = plan["ensemble"]
    if combo not in {"auto", "none"} and (
        combo not in ENSEMBLES_BY_ID or not ensemble_available(ENSEMBLES_BY_ID[combo], members)
    ):
        raise TourError("当前阵容不符合这个合奏的角色条件。")
    if plan["tool"] and plan["tool"] not in TOOLS_BY_ID:
        raise TourError("未知巡演器具。")


def _random_at(seed: str, stage: int, field: str) -> float:
    raw = hashlib.sha256(f"tour-v1|{seed}|{stage}|{field}".encode()).digest()
    return int.from_bytes(raw[:8], "big") / (1 << 64)


def score_stage(
    members: list[dict],
    plan: dict,
    *,
    equipment: int = 0,
    steady_coupon: bool = False,
    song_plays: dict[str, int] | None = None,
    stage_number: int = 1,
    previous: dict | None = None,
    center: str = "",
    seed: str | None = None,
) -> dict[str, Any]:
    unique = validate_formation(members, center)
    validate_plan(plan, unique, fans=10**9)
    previous = previous or {}
    songs = [SONGS_BY_ID[song_id] for song_id in plan["songs"]]
    venue, theme = VENUES_BY_ID[plan["venue"]], THEMES_BY_ID[plan["theme"]]
    characters = [CHARACTERS[member["template_id"]] for member in unique]
    levels = [training_level(member.get("training_exp", 0)) for member in unique]
    rapport = [min(30, member.get("rapport", 0)) for member in unique]
    plays = [min(10, (song_plays or {}).get(song.song_id, 0)) for song in songs]
    highlights = plan["highlights"] or [character.identity for character in characters[:2]]
    combo_id = plan["ensemble"]
    if combo_id == "auto":
        combo_id = next((item.ensemble_id for item in ENSEMBLES[1:] if ensemble_available(item, unique)), "free")
    combo = ENSEMBLES_BY_ID.get(combo_id)
    tags = set().union(*(song.tags for song in songs))
    energies = [song.energy for song in songs]
    theme_match = sum(song.theme_id == theme.theme_id for song in songs) >= 2
    echo = songs[0].motif == songs[-1].motif
    ascending = energies[0] < energies[1] < energies[2]
    center = center or next(
        member["pig_instance_id"] for member, char in zip(unique, characters, strict=True) if "主旋律" in char.roles
    )
    components = {
        "ability": 20 + 15 * sum(levels) / (10 * len(unique)),
        "synergy": 12 * sum(rapport) / (30 * len(unique)) + 13 * sum(plays) / 30,
        "setlist": sum(4 for song in songs if set(song.tags) & set(venue.tags))
        + (9 if ascending else 3 * sum(energies[i] <= energies[i + 1] for i in (0, 1)))
        + (4 if echo else 0),
        "stage": (4 if theme_match else 0) + 2 * len(highlights),
        "equipment": min(5, max(0, equipment)),
    }
    base_components = dict(components)
    adjustments, carry, extra_photos, scenes = [], {}, [], []

    def add(component: str, amount: float, source: str) -> None:
        before = components[component]
        components[component] = min(SCORE_CAPS[component], before + amount)
        adjustments.append(
            {
                "source": source,
                "component": component,
                "requested": amount,
                "applied": round(components[component] - before, 3),
            }
        )

    # 风格只补充演出项，按比例而不是按人数叠五次全队增益。
    branch_matches = sum(member.get("branch", "") in tags for member in unique)
    if branch_matches:
        add("ability", branch_matches / len(unique), "成员风格")
    for component, amount in previous.get("carry", {}).items():
        add(component, min(4, amount), "上一站的准备")
    if combo:
        add("stage", 2, combo.name)
        if combo.ensemble_id != "free":
            add(combo.component, 2, combo.name)
    if plan["tool"] == "cue":
        add("setlist", 2, "提示卡")
    conditions = {
        "always": True,
        "echo": echo,
        "ascending": ascending,
        "contrast": len({tuple(song.tags) for song in songs}) > 1,
        "syncopation": energies[1] > energies[2] and energies[1] > energies[0],
        "interactive": "互动" in tags,
        "two_interactive": sum("互动" in song.tags for song in songs) >= 2,
        "festival": venue.venue_id == "campus" or "热烈" in tags,
        "mixed": len({character.band for character in characters}) >= 3,
        "new_song": min(plays) < 3,
        "narrative": "叙事" in tags or venue.venue_id == "theatre",
        "special": any("dj" in char.instruments or "violin" in char.instruments for char in characters)
        or sum("drums" in char.instruments for char in characters) >= 2,
        "ceremony": energies == [1, 2, 3],
        "technical": "技术" in tags,
        "novice": min(levels) < 5,
        "fantasy": bool(tags & {"幻想", "热烈"}),
        "theme": theme_match,
        "new_venue": previous.get("plan", {}).get("venue") != venue.venue_id,
        "next": stage_number < 3,
        "theme_next": theme_match and stage_number < 3,
        "technical_or_fantasy": bool(tags & {"技术", "幻想"}),
        "small": len(unique) == 3,
        "burst": max(energies) == 3 and energies[-1] >= energies[1],
        "idol": "偶像" in tags,
        "middle_burst": energies[1] == 3,
        "partner": len(highlights) == 2,
        "idol_or_story": bool(tags & {"偶像", "叙事"}),
        "new_mixed": len({char.band for char in characters}) >= 2 and min(rapport) < 10,
        "duet": combo is not None and combo.ensemble_id != "free",
        "previous": bool(previous),
        "reordered": sorted(previous.get("plan", {}).get("songs", [])) == sorted(plan["songs"])
        and previous.get("plan", {}).get("songs") != plan["songs"],
    }
    highlights_result, stability = [], 0
    for member, char in zip(unique, characters, strict=True):
        if char.identity not in highlights:
            continue
        sig = char.signature
        active = member["pig_instance_id"] == center if sig.condition == "center" else conditions[sig.condition]
        event = {
            "identity": char.identity,
            "pig_instance_id": member["pig_instance_id"],
            "template_id": char.template_id,
            "name": sig.name,
            "character": char.character,
            "summary": sig.summary,
            "triggered": active,
        }
        highlights_result.append(event)
        if not active:
            continue
        if sig.effect == "boost":
            add(sig.component, 2, sig.name)
        elif sig.effect in {"weakest", "previous"}:
            reference = previous["components"] if sig.effect == "previous" else components
            target = min(
                (key for key in SCORE_CAPS if key != "equipment"), key=lambda key: reference[key] / SCORE_CAPS[key]
            )
            add(target, 2, sig.name)
        elif sig.effect == "carry":
            carry[sig.component] = carry.get(sig.component, 0) + 2
        elif sig.effect == "stabilize":
            stability = max(stability, 2)
        elif sig.effect == "scene":
            scenes.append(venue.venue_id)
        elif sig.effect == "photo":
            extra_photos.append(char.identity)
        else:
            raise TourError("未知招牌处理器，未结算任何奖励。")
    if plan["tool"] == "recorder":
        extra_photos.append("recorder")
    photo_candidates = [char.identity for char in characters if char.identity not in highlights]
    photo_ids = list(dict.fromkeys(highlights + photo_candidates[: len(extra_photos)]))
    raw_variation = int(_random_at(seed, stage_number, "variation") * 7) - 3 if seed is not None else 0
    incident = (
        ("equipment", "transition", "nerves")[min(2, int(_random_at(seed, stage_number, "incident") * 3))]
        if seed is not None
        else "preview"
    )
    variation = raw_variation
    if variation < 0:
        variation = min(0, variation + stability)
        if plan["tool"] == "cable" and incident == "equipment":
            variation = 0
    coupon_recovery = -variation if steady_coupon and variation < 0 else 0
    if steady_coupon:
        variation = max(0, variation)
    components = {key: round(min(SCORE_CAPS[key], value), 3) for key, value in components.items()}
    base_score = round(sum(components.values()), 2)
    total = round(min(100, max(0, base_score + variation)), 2)
    weakest = sorted(SCORE_CAPS, key=lambda key: components[key] / SCORE_CAPS[key])[:2]
    return {
        "definition_version": TOUR_VERSION,
        "stage_number": stage_number,
        "plan": deepcopy(plan),
        "members": deepcopy(members),
        "canonical_ids": [char.identity for char in characters],
        "bands": sorted({char.band for char in characters}),
        "center": center,
        "base_components": base_components,
        "components": components,
        "adjustments": adjustments,
        "base_score": base_score,
        "variation_raw": raw_variation,
        "variation": variation,
        "coupon_recovery": coupon_recovery,
        "incident": incident,
        "score": total,
        "grade": grade(total),
        "preview": seed is None,
        "theme_qualified": theme_match,
        "ensemble": combo.ensemble_id if combo else "",
        "ensemble_story": combo.story if combo else "本场未安排合奏。",
        "highlights": highlights_result,
        "carry": carry,
        "photo_ids": photo_ids,
        "scene_ids": scenes,
        "equipment": equipment,
        "song_plays": dict(song_plays or {}),
        "tips": [
            f"{SCORE_NAMES[key]}仍有{SCORE_CAPS[key] - components[key]:.1f}分成长空间"
            for key in weakest
            if components[key] < SCORE_CAPS[key]
        ],
        "confetti": plan["tool"] == "confetti",
    }


def forecast_route(
    members: list[dict], plans: list[dict], *, equipment: int, song_plays: dict, center: str
) -> list[dict]:
    working, plays, previous, results = deepcopy(members), dict(song_plays), None, []
    for number, plan in enumerate(plans, 1):
        result = score_stage(
            working, plan, equipment=equipment, song_plays=plays, stage_number=number, previous=previous, center=center
        )
        results.append(result)
        for member in canonical_members(working):
            member["training_exp"] = member.get("training_exp", 0) + 20
            member["rapport"] = member.get("rapport", 0) + 1
        for song_id in plan["songs"]:
            plays[song_id] = plays.get(song_id, 0) + 1
        previous = result
    return results
