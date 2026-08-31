"""将已提交对战事实投影成图卡；不在渲染中抽盘或做资源结算。"""

from __future__ import annotations

from fractions import Fraction

from ..domain.battle import weight_label
from ..domain.battle_catalog import (
    COUNT_WHEEL,
    DANIYA_FORM_DISILLUSION,
    DANIYA_FORM_STAGING,
    FIGHTERS_BY_ID,
    HEAVY_COUNT_WHEEL,
    INJURY_NAMES,
    INJURY_WEIGHT_SCALE,
    INJURY_WHEELS,
    JUEJUE_ACCELERATION_TIERS,
    JUEJUE_DELAY_TIERS,
    JUEJUE_FORM_TIME,
    JUEJUE_FORM_VIRTUAL,
    LEGACY_LOOT_ATTEMPTS,
    LOOT_ATTEMPTS,
    MATERIAL_IDS,
    MOVE_WEIGHT_SCALE,
    TOOLS_BY_ID,
    VICTORY_WEIGHT_SCALE,
    fighter_form_moves,
    fighter_moves,
)
from ..domain.battle_views import BattleView, BattleWheelCard, BattleWheelSegment, FighterCard
from ..domain.dispatch import MATERIALS, safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchPigCard
from ..domain.display import format_length, format_weight
from ..domain.models import CommandIdentity

STATUS_NAMES = {
    "pending": "等待应战",
    "active": "交锋中",
    "completed": "力竭终局",
    "declined": "邀请已拒绝",
    "cancelled": "邀请已取消",
    "expired": "超时结束",
    "surrendered": "认输结束",
}

JUEJUE_FORM_NAMES = {
    JUEJUE_FORM_TIME: "时之沙",
    JUEJUE_FORM_VIRTUAL: "虚拟声",
}

DANIYA_FORM_NAMES = {
    DANIYA_FORM_STAGING: "布景",
    DANIYA_FORM_DISILLUSION: "幻灭",
}

# 塑型、苍赫和布景招式在状态里按“十分之一抽取权重”保存；
# 它们不是 MOVE_WEIGHT_SCALE=1000 的原始轮盘单位，展示时必须单独换算。
DYNAMIC_DRAW_STEP_SCALE = 10


def _required(record: dict, keys: tuple[str, ...], label: str) -> None:
    missing = [key for key in keys if key not in record]
    if missing:
        raise ValueError(f"{label}缺少已发布事实字段：{', '.join(missing)}")


def _form_name(form_id: str) -> str:
    try:
        return JUEJUE_FORM_NAMES[form_id]
    except KeyError as exc:
        raise ValueError(f"未知撅撅猪形态：{form_id}") from exc


def _bonus_text(value) -> str:
    if isinstance(value, dict):
        return "、".join(f"{key}{amount:+g}" for key, amount in value.items()) or "无"
    if isinstance(value, (tuple, list)):
        return "、".join(str(item) for item in value) or "无"
    return str(value) if value not in (None, "") else "无"


def _mimic_fact(mimic: dict, *, label: str = "虚拟模仿") -> str:
    _required(
        mimic,
        (
            "available",
            "band",
            "band_wheel",
            "band_roll",
            "source_wheel",
            "source_roll",
            "source_fighter_id",
            "source_move_id",
            "source_name",
            "base",
            "direction",
        ),
        label,
    )
    if not mimic["available"]:
        return f"{label}：当前冻结池没有可复制的数值招式，本次不增加权重"
    band = {"large": "大轮盘", "small": "小轮盘"}.get(str(mimic["band"]))
    if band is None:
        raise ValueError(f"未知虚拟模仿轮盘：{mimic['band']}")
    base = Fraction(mimic["base"])
    if mimic["direction"] == "self":
        direction = "自身增加" if base >= 0 else "自身减少"
    else:
        direction = "对手减少" if base >= 0 else "对手增加"
    source_numbers = []
    if base:
        source_numbers.append(f"{direction}{weight_label(abs(base))}")
    if Fraction(mimic.get("opponent_reduction", 0)):
        source_numbers.append(f"另带对手减权{weight_label(mimic['opponent_reduction'])}")
    result = f"{label}：{band}抽中“{mimic['source_name']}”，" + "、".join(source_numbers)
    effect_summary = mimic.get("effect_summary")
    if isinstance(effect_summary, dict):
        effect_parts = []
        if Fraction(effect_summary.get("opponent_reduction", 0)):
            effect_parts.append(f"对方本回合-{weight_label(effect_summary['opponent_reduction'])}")
        if int(effect_summary.get("opponent_next_debt", 0)):
            effect_parts.append(f"对方下回合-{effect_summary['opponent_next_debt']}招")
        if int(effect_summary.get("opponent_next_bonus", 0)):
            effect_parts.append(f"对方下回合+{effect_summary['opponent_next_bonus']}招")
        if int(effect_summary.get("opponent_next_milk_dragons", 0)):
            effect_parts.append(f"对方下回合前{effect_summary['opponent_next_milk_dragons']}招变为发奶龙")
        if int(effect_summary.get("opponent_exhaust_bonus_units", 0)):
            effect_parts.append(
                "对方力竭盘+"
                + str(_scaled_weight(effect_summary["opponent_exhaust_bonus_units"], INJURY_WEIGHT_SCALE))
            )
        if effect_summary.get("loan"):
            effect_parts.append("贷款状态与自身下回合欠招")
        if effect_summary.get("black_flash"):
            effect_parts.append("黑闪成长")
        if effect_summary.get("infinity"):
            effect_parts.append("无下限防御")
        effect_summary = "、".join(effect_parts)
    elif isinstance(effect_summary, (tuple, list)):
        effect_summary = "、".join(str(item) for item in effect_summary if item)
    if mimic.get("copied_domain_effect"):
        effect_summary = "、".join(item for item in (str(effect_summary or ""), mimic["copied_domain_effect"]) if item)
    if effect_summary:
        result += f"；同时复制效果：{effect_summary}"
    elif mimic.get("functional_tags"):
        result += "；同时复制该招的一般功能与定向效果"
    boundaries = []
    if mimic.get("extra_draws_suppressed"):
        boundaries.append("复制招式的追加抽数已抑制")
    if mimic.get("domain_reentry_suppressed"):
        boundaries.append("复制领域不会再次开启领域战")
    if mimic.get("copied_domain_effect_suppressed"):
        boundaries.append(str(mimic["copied_domain_effect_suppressed"]))
    if boundaries:
        result += "；" + "、".join(boundaries)
    return result


def _relative_zero_first_fact(record: object, label: str) -> str:
    """把 Battle v6 的首次子盘事实投影成人话；旧战报没有该字段时不猜。"""

    if not isinstance(record, dict):
        return f"首次{label}未出现"
    tier = record.get("tier")
    if tier is None:
        return f"首次{label}未出现"
    result = f"首次{label}{tier}档"
    if record.get("success") is True:
        result += "成功"
    elif record.get("success") is False:
        result += "失败"
    if record.get("ordinal") is not None:
        result += f"（第{record['ordinal']}招）"
    return result


def cost_text(costs: dict) -> str:
    return "、".join(
        f"{MATERIALS.get(MATERIAL_IDS.get(key, key), '猪币' if key == 'coins' else key)}×{amount}"
        for key, amount in costs.items()
    )


def pig_card(member: dict, note: str = "") -> DispatchPigCard:
    return DispatchPigCard(
        member["name"],
        member["short_code"],
        member["rarity"],
        member.get("image_relpath", ""),
        (f"战斗强化 +{member.get('level', 0)}", *member.get("display_tags", ())[:2]),
        note or f"{format_length(member['size_value'])} · {format_weight(member['weight_value'])}",
        bool(member.get("favorite")),
        member["template_id"],
    )


def view(identity: CommandIdentity, title: str, **kwargs) -> BattleView:
    return BattleView(title=title, player_name=safe_display_name(identity.display_name, identity.user_id), **kwargs)


def _scaled_weight(value, scale: int) -> int | float:
    """将精确权重转为轮盘组件可序列化的数值；文字仍使用weight_label保真。"""
    exact = Fraction(value, scale)
    return int(exact) if exact.denominator == 1 else float(exact)


def _signed_weight(value) -> str:
    exact = Fraction(value)
    return ("+" if exact > 0 else "") + weight_label(exact)


def _percent(value, total) -> str:
    denominator = Fraction(total)
    if denominator <= 0:
        return "0.00%"
    return f"{float(Fraction(value) * 100 / denominator):.2f}%"


def _fighter_form_name(fighter_id: str, form_id: str) -> str:
    if fighter_id == "juejue":
        return _form_name(form_id)
    if fighter_id == "daniya":
        try:
            return DANIYA_FORM_NAMES[form_id]
        except KeyError as exc:
            raise ValueError(f"未知达妮娅猪形态：{form_id}") from exc
    return form_id


def effective_total_after(event: dict, adjustments: dict[int, dict], domain_bonus: dict | None = None):
    ordinal = int(event["ordinal"])
    total = Fraction(event["total"]) - sum(
        (Fraction(item["gain"]) for cancelled_ordinal, item in adjustments.items() if cancelled_ordinal <= ordinal),
        Fraction(0),
    )
    if domain_bonus and int(domain_bonus.get("ordinal") or 0) <= ordinal:
        total += Fraction(domain_bonus.get("gain") or 0)
    return total


def _juejue_event_line(
    event: dict,
    adjustment: dict | None,
    *,
    shown_total: int,
    domain_bonus: dict | None,
) -> Line:
    _required(
        event,
        (
            "form_before",
            "form_after",
            "special_base",
            "music_gain",
            "subwheel",
            "relative_zero",
            "mimic",
            "sculpt_bonus_before",
            "sculpt_bonus_after",
            "sand_domain_steps_before",
            "sand_domain_steps_after",
            "sand_domain_switch_units_before",
            "sand_domain_switch_units_after",
            "realization_stacks_before",
            "realization_stacks_after",
            "guaranteed_before",
            "guaranteed_after",
            "realtime_activated",
            "future_simulation_activated",
            "sand_body_activated",
            "rewind_active",
            "opponent_reduction",
            "opponent_next_debt",
            "opponent_next_bonus",
        ),
        "撅撅猪招式",
    )
    form_before = _form_name(str(event["form_before"]))
    form_after = _form_name(str(event["form_after"]))
    gain = Fraction(event["gain"])
    value = (
        f"+{weight_label(gain)} → 累计{weight_label(shown_total)}"
        if gain > 0
        else f"功能结算 → 累计{weight_label(shown_total)}"
    )
    note_parts = [f"来源盘：{form_before}"]
    special_base = Fraction(event["special_base"])
    if special_base:
        note_parts.append(
            f"本次数值基底{special_base}，强化+{event['training']}，核心+{weight_label(event['core'])}，"
            f"伤势-{event['penalty']}，倍率×{event['multiplier']}"
        )
    if Fraction(event["music_gain"]):
        note_parts.append(f"虚拟声音乐状态额外+{weight_label(event['music_gain'])}")
    subwheel = event["subwheel"]
    if subwheel is not None:
        _required(
            subwheel,
            (
                "kind",
                "tier",
                "tier_wheel",
                "tier_roll",
                "base_chance",
                "sculpt_bonus",
                "specific_bonus",
                "guaranteed",
                "chance",
                "success_wheel",
                "success_roll",
                "success",
            ),
            "撅撅猪子盘",
        )
        kind = {"acceleration": "加速", "delay": "时延"}.get(str(subwheel["kind"]))
        if kind is None:
            raise ValueError(f"未知撅撅猪子盘：{subwheel['kind']}")
        note_parts.append(
            f"{kind}盘抽中{subwheel['tier']}档；最终成功率{subwheel['chance']}%"
            f"（基础{subwheel['base_chance']}%，塑型+{subwheel['sculpt_bonus']}%，"
            f"专项+{subwheel['specific_bonus']}%）"
        )
        note_parts.append("本次判定成功" if subwheel["success"] else "本次判定失败")
        if subwheel["guaranteed"]:
            note_parts.append("本次消耗必定成功效果")
    mimic = event["mimic"]
    if mimic is not None:
        note_parts.append(_mimic_fact(mimic))
    relative_zero = event["relative_zero"]
    if relative_zero is not None:
        _required(relative_zero, ("checked", "roll", "wheel", "success", "gain"), "相对静止时间·零")
        first_acceleration = relative_zero.get("first_acceleration")
        first_delay = relative_zero.get("first_delay")
        if first_acceleration is not None or first_delay is not None:
            note_parts.append(
                "零只读取本回合第一次子盘："
                + _relative_zero_first_fact(first_acceleration, "加速")
                + "、"
                + _relative_zero_first_fact(first_delay, "时延")
            )
        if relative_zero["checked"]:
            if relative_zero.get("eligible", True):
                note_parts.append(
                    "相对静止时间·零判定成功：自身额外+"
                    + weight_label(relative_zero["gain"])
                    + "，对方本回合招式无效"
                    if relative_zero["success"]
                    else "相对静止时间·零判定失败"
                )
            else:
                note_parts.append(
                    "相对静止时间·零不满足发动条件"
                    + (f"：{relative_zero['reason']}" if relative_zero.get("reason") else "")
                )
    if form_before != form_after:
        note_parts.append(f"即时切换为{form_after}；本招追加抽取从新形态轮盘继续")
    if int(event["sculpt_bonus_after"]) > int(event["sculpt_bonus_before"]):
        note_parts.append(f"下一次加速/时延成功率加成累计至+{event['sculpt_bonus_after']}%")
    if int(event["sand_domain_steps_after"]) > int(event["sand_domain_steps_before"]):
        note_parts.append(
            f"领域·荒时之沙出现权重累计+"
            f"{_scaled_weight(event['sand_domain_steps_after'], DYNAMIC_DRAW_STEP_SCALE)}"
        )
    if int(event["realization_stacks_after"]) > int(event["realization_stacks_before"]):
        note_parts.append(f"化虚为实累计至{event['realization_stacks_after']}层")
    if event["guaranteed_after"] and not event["guaranteed_before"]:
        note_parts.append("下一次加速或时延必定成功")
    if event["realtime_activated"]:
        note_parts.append("实时演算首次生效：本回合两种领域出现权重各+1")
    if event["future_simulation_activated"]:
        note_parts.append("未来模拟独立挂起1次：回合末随机取消对方一个有效数值招式")
    if event["sand_body_activated"]:
        note_parts.append("沙之形体已展开：回合末将对方第一招有效数值减半")
    if event["rewind_active"]:
        note_parts.append("回溯已挂起：本回合失败时可撤销新抽到的轻伤或重伤")
    if int(event.get("rewind_debt_cleared", 0)):
        source = (
            f"第{event['rewind_failure_ordinal']}招"
            if event.get("rewind_failure_ordinal") is not None
            else "一笔"
        )
        note_parts.append(
            f"回溯已撤销{source}加速失败产生的下回合-{event['rewind_debt_cleared']}招"
        )
    if int(event.get("rewind_pending_count", 0)):
        note_parts.append(f"尚有{event['rewind_pending_count']}次回溯等待本回合后续加速失败")
    if event.get("music_repeated"):
        note_parts.append("音乐状态不叠层；本次重复抽中改为再抽2次")
    if Fraction(event["opponent_reduction"]):
        note_parts.append(f"请求削减对方本回合权重{weight_label(event['opponent_reduction'])}")
    if int(event["opponent_next_debt"]):
        note_parts.append(f"请求令对方下回合出招数-{event['opponent_next_debt']}")
    if int(event["opponent_next_bonus"]):
        note_parts.append(f"本次失败令对方下回合出招数+{event['opponent_next_bonus']}")
    if event.get("extra_draws") and not event.get("music_repeated"):
        note_parts.append(f"本回合再抽{event['extra_draws']}次")
    if adjustment:
        deducted = Fraction(adjustment["gain"])
        original = Fraction(event["gain"])
        value = (
            f"原+{weight_label(original)} · 结算归零 → 累计{weight_label(shown_total)}"
            if deducted >= original
            else f"原+{weight_label(original)} · 扣除{weight_label(deducted)}"
            f" → 累计{weight_label(shown_total)}"
        )
        note_parts.append("、".join(adjustment["reasons"]))
    if domain_bonus:
        ordinals = tuple(int(item) for item in domain_bonus.get("ordinals", ()))
        target = (
            "第" + "、".join(str(item) for item in ordinals) + "招合计"
            if len(ordinals) > 1
            else "本招"
        )
        note_parts.append(
            f"领域判定胜出，{target}额外+{weight_label(domain_bonus['gain'])}（已提交结算事实）"
        )
    if event["tool_used"]:
        note_parts.append(f"{TOOLS_BY_ID[event['tool_used']].name}已消耗")
    return Line(f"{event['ordinal']}. {event['name']}", value, "；".join(note_parts))


def move_line(
    event: dict,
    adjustment: dict | None = None,
    *,
    effective_total=None,
    domain_bonus: dict | None = None,
) -> Line:
    shown_total = Fraction(event["total"]) if effective_total is None else Fraction(effective_total)
    if event.get("fighter_id") == "juejue":
        return _juejue_event_line(
            event,
            adjustment,
            shown_total=shown_total,
            domain_bonus=domain_bonus,
        )
    numeric_base = bool(event.get("numeric_base", event.get("base", 0)))
    special_base = Fraction(event.get("special_base", event.get("base", 0)))
    gain = Fraction(event.get("gain", 0))
    opponent_reduction = Fraction(event.get("opponent_reduction", 0))
    if numeric_base:
        note = (
            f"({_signed_weight(special_base)} + 强化{event['training']} + 核心{weight_label(event['core'])} "
            f"- 伤势{event['penalty']}) ×{event['multiplier']}"
        )
        if event.get("black_flash_bonus", 0):
            note += f"，黑闪领悟+{event['black_flash_bonus']}（不翻倍）"
        if event["trait_gain"] or event["tool_gain"]:
            note += f"，个体+{event['trait_gain']} / 器具+{event['tool_gain']}（不翻倍）"
        value = f"{_signed_weight(gain)} → 累计{weight_label(shown_total)}"
    else:
        value = (
            f"黑闪领悟+{weight_label(event['black_flash_bonus'])} → 累计{weight_label(shown_total)}"
            if event.get("black_flash_bonus", 0)
            else f"再抽{event['extra_draws']}次"
        )
        note = "功能招式不加战斗强化；待用×2保留" if event["double_pending"] else "功能招式不加战斗强化"
    fighter_id = event.get("fighter_id")
    if fighter_id == "daniya":
        form = _fighter_form_name("daniya", str(event.get("form_before", DANIYA_FORM_STAGING)))
        note += f"；来源形态：{form}"
        if opponent_reduction:
            note += f"；同时令对方本回合-{weight_label(opponent_reduction)}"
        if "daniya-staging" in event.get("tags", ()):
            note += (
                f"；蚀域抽取加权 "
                f"{_scaled_weight(event.get('daniya_domain_steps_before', 0), DYNAMIC_DRAW_STEP_SCALE)}"
                f" → {_scaled_weight(event.get('daniya_domain_steps_after', 0), DYNAMIC_DRAW_STEP_SCALE)}"
            )
        if "daniya-disillusion" in event.get("tags", ()):
            exhaust_bonus = _scaled_weight(event.get("opponent_exhaust_bonus_units", 0), INJURY_WEIGHT_SCALE)
            note += f"；对方力竭盘永久+{exhaust_bonus}"
        if "daniya-timed-collapse" in event.get("tags", ()):
            note += "；对方本回合力竭权重×5；若未力竭，自己下回合力竭权重×5"
        if "daniya-domain" in event.get("tags", ()):
            note += "；领域胜利后切换幻灭形态，自己下回合出招数+1"
    elif fighter_id == "asamu":
        if event.get("forced"):
            note += f"；奶龙覆盖：原本将抽中“{event.get('original_move_name') or '未知招式'}”"
        if event.get("asamu_big_stacks_after") != event.get("asamu_big_stacks_before"):
            note += f"；憋大层数 {event.get('asamu_big_stacks_before', 0)} → {event.get('asamu_big_stacks_after', 0)}"
        dynamic = (
            ("喝奶茶", "asamu_tea_bonus_before", "asamu_tea_bonus_after"),
            ("睡觉", "asamu_sleep_bonus_before", "asamu_sleep_bonus_after"),
            ("全盛姿态", "asamu_prime_bonus_before", "asamu_prime_bonus_after"),
        )
        for label, before_key, after_key in dynamic:
            before, after = event.get(before_key), event.get(after_key)
            if before is not None and after is not None and before != after:
                before_weight = _scaled_weight(before, MOVE_WEIGHT_SCALE)
                after_weight = _scaled_weight(after, MOVE_WEIGHT_SCALE)
                note += f"；{label}附加抽取权重 {before_weight} → {after_weight}"
        if "asamu-pressure-king" in event.get("tags", ()):
            note += "；本层独立判定对方每个数值招式33%失效；只归零胜率数值，功能保留"
        if "asamu-misfortune-transfer" in event.get("tags", ()):
            note += "；双方本回合力竭倒下权重各×5"
        if event.get("opponent_next_milk_dragons"):
            note += f"；对方下回合前{event['opponent_next_milk_dragons']}招将被依次覆盖为发奶龙"
        if "asamu-tit-for-tat" in event.get("tags", ()):
            note += "；回合末若落后则交换双方权重并再+4，否则自身+40"
        if "asamu-domain" in event.get("tags", ()):
            note += "；领域胜利后复制对方4个随机招式"
    if event["loan"]:
        note += f"；下回合扣招累计{weight_label(event['next_debt'])}，仅保留一份×2"
    if event.get("extra_draws"):
        note += f"；再抽{event['extra_draws']}次"
    if "black-flash" in event.get("tags", ()):
        note += f"；黑闪领悟现为+{event.get('black_flash_stacks', 0)}"
    if "blue-red" in event.get("tags", ()):
        note += (
            f"；两种茈的抽取权重累计+"
            f"{_scaled_weight(event.get('purple_weight_steps', 0), DYNAMIC_DRAW_STEP_SCALE)}"
        )
    if "purple" in event.get("tags", ()):
        used = int(event.get("purple_weight_steps_used", event.get("purple_weight_steps_before", 0)))
        note += (
            f"；本次茈盘加权+{_scaled_weight(used, DYNAMIC_DRAW_STEP_SCALE)}"
            "已消耗，使用后归零重新累计"
        )
    if "infinity" in event.get("tags", ()):
        note += "；本回合无下限防御已展开（多次不叠加）"
    if adjustment:
        deducted = Fraction(adjustment["gain"])
        original = Fraction(event["gain"])
        value = (
            f"原{_signed_weight(original)} · 结算归零 → 累计{weight_label(shown_total)}"
            if deducted == original
            else f"原{_signed_weight(original)} · 调整{_signed_weight(-deducted)}"
            f" → 累计{weight_label(shown_total)}"
        )
        note += "；" + "、".join(adjustment["reasons"]) + "；本招全部胜率数值归零，功能保留"
    if domain_bonus:
        note += f"；领域战获胜，本招额外+{weight_label(domain_bonus['gain'])}（本回合仅一次）"
    if event["tool_used"]:
        note += f"；{TOOLS_BY_ID[event['tool_used']].name}已消耗"
    return Line(f"{event['ordinal']}. {event['name']}", value, note)


def wheel_card(kind: str, title: str, options: tuple, selected=None, note: str = "") -> BattleWheelCard:
    """只投影给定的权重与已抽结果，不执行任何抽签。"""
    return BattleWheelCard(
        kind,
        title,
        tuple(BattleWheelSegment(str(label), weight) for label, weight in options),
        next((i for i, (label, _weight) in enumerate(options) if label == selected), None),
        note,
    )


def _event_move_wheel(event: dict, definition_version: int) -> BattleWheelCard:
    """Project the wheel which actually produced an event.

    Forced milk-dragon events keep the opponent's original wheel/roll, while
    Assam-domain copies keep the source fighter's wheel.  Neither case can be
    reconstructed from ``fighter_id`` plus the final move name alone.
    """

    generated_copy = event.get("generated_by") == "asamu-domain-copy"
    generated_mimic = event.get("generated_by") == "chaos-domain-auto-mimic"
    source_fighter_id = str(
        event.get("fighter_id")
        if generated_mimic
        else event.get("source_fighter_id") or event.get("fighter_id") or ""
    )
    if source_fighter_id not in FIGHTERS_BY_ID:
        raise ValueError(f"未知招式盘来源：{source_fighter_id}")

    definition = FIGHTERS_BY_ID[source_fighter_id]
    moves_by_id = {move.move_id: move for move in fighter_moves(source_fighter_id, definition_version)}
    wheel_move_ids = tuple(str(move_id) for move_id in event.get("draw_wheel_move_ids") or ())
    exact_moves = tuple(moves_by_id.get(move_id) for move_id in wheel_move_ids)
    if wheel_move_ids and all(move is not None for move in exact_moves):
        moves = exact_moves
    elif source_fighter_id in {"juejue", "daniya"} and not generated_copy and not generated_mimic:
        moves = fighter_form_moves(source_fighter_id, str(event.get("form_before") or ""))
    else:
        moves = fighter_moves(source_fighter_id, definition_version)

    units = event.get("draw_wheel_units")
    scale = int(event.get("draw_weight_scale") or MOVE_WEIGHT_SCALE)
    options = (
        tuple((move.name, _scaled_weight(units[index], scale)) for index, move in enumerate(moves))
        if units and len(units) == len(moves)
        else tuple((move.name, move.draw_weight) for move in moves)
    )

    selected_move_id = str(
        event.get("original_move_id")
        if event.get("forced")
        else event.get("source_move_id")
        if generated_copy
        else event.get("move_id")
        or ""
    )
    selected = moves_by_id[selected_move_id].name if selected_move_id in moves_by_id else str(event["name"])

    form_id = ""
    if definition.forms:
        for form in definition.forms:
            if tuple(move.move_id for move in form.moves) == wheel_move_ids:
                form_id = form.form_id
                break
        if not form_id and not generated_copy:
            form_id = str(event.get("form_before") or "")
    form_suffix = f" · {_fighter_form_name(source_fighter_id, form_id)}" if form_id else ""

    if event.get("forced"):
        title_suffix = f"{form_suffix} · 奶龙覆盖"
        note = f"高亮原始抽取落点“{selected}”；随后被覆盖为“{event['name']}”。"
    elif generated_copy:
        title_suffix = f" · 复制自{definition.name}{form_suffix}"
        note = f"高亮阿萨姆领域复制时的真实来源落点；实际施放“{event['name']}”。"
    elif generated_mimic:
        mimic = event.get("mimic") or {}
        copied_fighter_id = str(mimic.get("source_fighter_id") or event.get("source_fighter_id") or "")
        copied_fighter = FIGHTERS_BY_ID.get(copied_fighter_id)
        copied_from = copied_fighter.name if copied_fighter else str(mimic.get("source_name") or "其他战斗猪")
        title_suffix = " · 乱序数虚时空自动发动"
        note = f"高亮领域命中后自动生成的虚拟模仿；本次复制来源：{copied_from}。"
    else:
        title_suffix = form_suffix
        note = "本卡展示最后一招的真实落点；逐招数值均为已提交事实。"
    return wheel_card(
        "move",
        f"第{event['ordinal']}招落点{title_suffix}",
        options,
        selected,
        note,
    )


def _juejue_state_projection(side: dict) -> tuple[str, str, str]:
    _required(
        side,
        (
            "juejue_form",
            "juejue_form_roll",
            "juejue_sculpt_bonus",
            "juejue_acceleration_bonus",
            "juejue_delay_bonus",
            "juejue_guaranteed",
            "juejue_sand_domain_steps",
            "juejue_sand_domain_switch_units",
            "juejue_realization_stacks",
            "juejue_mimic_pool",
        ),
        "撅撅猪战斗状态",
    )
    turn = side["turn"]
    _required(
        turn,
        (
            "juejue_music",
            "juejue_realtime",
            "juejue_future_simulation",
            "juejue_sand_body",
            "juejue_zero_checked",
            "juejue_zero_active",
            "juejue_acceleration_tier",
            "juejue_delay_tier",
            "juejue_rewind",
            "events",
        ),
        "撅撅猪本回合状态",
    )
    current = _form_name(str(side["juejue_form"]))
    track: list[str] = []
    for event in turn["events"]:
        _required(event, ("form_before", "form_after"), "撅撅猪切换轨迹")
        before = _form_name(str(event["form_before"]))
        after = _form_name(str(event["form_after"]))
        if not track:
            track.append(before)
        if after != track[-1]:
            track.append(after)
    if not track:
        track.append(current)
    facts: list[str] = []
    if side["juejue_sculpt_bonus"]:
        facts.append(f"塑型判定+{side['juejue_sculpt_bonus']}个百分点")
    if side["juejue_acceleration_bonus"]:
        facts.append(f"下次加速+{side['juejue_acceleration_bonus']}个百分点")
    if side["juejue_delay_bonus"]:
        facts.append(f"下次时延+{side['juejue_delay_bonus']}个百分点")
    if side["juejue_guaranteed"]:
        facts.append("下一次加速/时延必定成功")
    domain_units = int(side["juejue_sand_domain_steps"]) + int(side["juejue_sand_domain_switch_units"])
    if domain_units:
        facts.append(f"荒时之沙抽取权重+{_scaled_weight(domain_units, DYNAMIC_DRAW_STEP_SCALE)}")
    if side["juejue_realization_stacks"]:
        facts.append(f"化虚为实累计{side['juejue_realization_stacks']}层")
    if turn["juejue_music"]:
        facts.append("音乐状态：后续数值招式+5")
    if turn["juejue_realtime"]:
        facts.append("实时演算：本回合领域盘加权")
    future_count = int(
        turn.get("juejue_future_simulation_count", 0)
        or len(turn.get("juejue_future_simulation_ordinals", ()))
        or len(turn.get("juejue_future_simulations", ()))
        or bool(turn["juejue_future_simulation"])
    )
    if future_count:
        facts.append(f"未来模拟待结算×{future_count}")
    if turn["juejue_sand_body"]:
        facts.append("沙之形体已展开")
    rewind_pending = int(
        turn.get("juejue_rewind_pending_count", 0)
        or len(turn.get("juejue_rewind_pending_ordinals", ()))
    )
    if rewind_pending:
        facts.append(f"回溯待消除加速失败×{rewind_pending}")
    elif turn["juejue_rewind"]:
        facts.append("回溯已挂起")
    if turn["juejue_zero_checked"]:
        facts.append("相对静止时间·零已成功" if turn["juejue_zero_active"] else "相对静止时间·零未触发")
    music_repeats = len(turn.get("juejue_music_repeat_ordinals", ()))
    if music_repeats:
        facts.append(f"音乐重复再抽×{music_repeats}")
    first_acceleration = turn.get("juejue_first_acceleration") or turn.get("juejue_zero_first_acceleration")
    first_delay = turn.get("juejue_first_delay") or turn.get("juejue_zero_first_delay")
    if first_acceleration is not None or first_delay is not None:
        facts.append(
            "零判定首档："
            + _relative_zero_first_fact(first_acceleration, "加速")
            + "、"
            + _relative_zero_first_fact(first_delay, "时延")
        )
    else:
        # Battle v4/v5 历史战报只保存最高成功档，继续按旧字段准确展示。
        if turn["juejue_acceleration_tier"]:
            facts.append(f"旧规则本回合加速最高{turn['juejue_acceleration_tier']}档")
        if turn["juejue_delay_tier"]:
            facts.append(f"旧规则本回合时延最高{turn['juejue_delay_tier']}档")
    return f"当前形态 · {current}", " → ".join(track), " · ".join(facts) or "暂无待结算机制"


def _daniya_state_projection(side: dict) -> tuple[str, str, str]:
    current = _fighter_form_name("daniya", str(side.get("daniya_form", DANIYA_FORM_STAGING)))
    turn = side.get("turn", {})
    track: list[str] = []
    for event in turn.get("events", ()):
        before = _fighter_form_name("daniya", str(event.get("form_before", side.get("daniya_form"))))
        after = _fighter_form_name("daniya", str(event.get("form_after", before)))
        if not track:
            track.append(before)
        if after != track[-1]:
            track.append(after)
    if not track:
        track.append(current)
    elif current != track[-1]:
        track.append(current)
    facts = [
        f"蚀域抽取加权+{_scaled_weight(side.get('daniya_domain_steps', 0), DYNAMIC_DRAW_STEP_SCALE)}",
        f"自身力竭盘被永久加权+"
        f"{_scaled_weight(side.get('injury_exhaust_bonus_units', 0), INJURY_WEIGHT_SCALE)}",
    ]
    if turn.get("daniya_collapse_count"):
        facts.append(f"本回合计时溃灭×{turn['daniya_collapse_count']}")
    if side.get("next_exhaust_multiplier_round") is not None:
        facts.append(
            f"第{side['next_exhaust_multiplier_round']}回合自身力竭权重×{side.get('next_exhaust_multiplier', 1)}"
        )
    return f"当前形态 · {current}", " → ".join(track), " · ".join(facts)


def _asamu_state_projection(side: dict) -> tuple[str, str, str]:
    turn = side.get("turn", {})
    injury = {"none": "无伤", "light": "轻伤", "heavy": "重伤"}.get(
        str(side.get("injury_state", "none")), str(side.get("injury_state", "none"))
    )
    tea = 1000 + int(side.get("asamu_tea_bonus_units", 0))
    sleep = 1000 + int(side.get("asamu_sleep_bonus_units", 0))
    prime = 200 + int(side.get("asamu_prime_bonus_units", 0))
    facts = [
        f"憋大永久层数{side.get('asamu_big_stacks', 0)}（后续每个招式的胜率数值每层+3）",
        f"喝奶茶/睡觉/全盛姿态权重 {tea / 1000:g}/{sleep / 1000:g}/{prime / 1000:g}",
    ]
    milk = int(side.get("asamu_milk_dragon_next_count", 0))
    if milk:
        facts.append(f"自己下回合前{milk}招将变为发奶龙")
    if turn.get("asamu_pressure_ordinals"):
        facts.append(f"耐压王{len(turn['asamu_pressure_ordinals'])}层独立判定")
    if turn.get("asamu_misfortune_count"):
        facts.append(f"厄运传递×{turn['asamu_misfortune_count']}")
    if turn.get("asamu_retaliation_ordinals"):
        facts.append(f"以牙还牙×{len(turn['asamu_retaliation_ordinals'])}待结算")
    if turn.get("forced_milk_dragon_used"):
        facts.append(f"本回合奶龙覆盖{turn['forced_milk_dragon_used']}招")
    return f"当前伤势 · {injury}", "动态抽取盘", " · ".join(facts)


def _v4_interaction_panels(interactions: dict, names: list[str]) -> tuple[Panel, ...]:
    _required(
        interactions,
        (
            "domain",
            "adjustments",
            "future_simulations",
            "sand_bodies",
            "zeroes",
            "round_reductions",
            "cross_effects",
        ),
        "v4回合交互",
    )
    panels: list[Panel] = []
    mechanism_lines: list[Line] = []
    for fact in interactions["future_simulations"]:
        _required(
            fact,
            (
                "side",
                "active",
                "target_side",
                "candidate_ordinals",
                "selected_ordinal",
                "roll",
                "cancelled_gain",
            ),
            "未来模拟结算",
        )
        if not fact["active"]:
            continue
        target = names[int(fact["target_side"])]
        if fact["selected_ordinal"] is None:
            value, note = "没有有效目标", f"{target}本回合没有仍有效的数值招式"
        else:
            value = f"取消{target}第{fact['selected_ordinal']}招"
            note = f"已扣除胜利权重{weight_label(fact['cancelled_gain'])}；招式功能保留"
        source_ordinal = fact.get("source_ordinal")
        source = (
            names[int(fact["side"])] + f" · 未来模拟（第{source_ordinal}招）"
            if source_ordinal is not None
            else names[int(fact["side"])] + " · 未来模拟"
        )
        mechanism_lines.append(Line(source, value, note))
    for fact in interactions["sand_bodies"]:
        _required(
            fact,
            (
                "side",
                "active",
                "target_side",
                "selected_ordinal",
                "original_gain",
                "remaining_gain",
                "cancelled_gain",
            ),
            "沙之形体结算",
        )
        if not fact["active"]:
            continue
        target = names[int(fact["target_side"])]
        if fact["selected_ordinal"] is None:
            value, note = "没有有效目标", f"{target}本回合没有仍有效的数值招式"
        else:
            value = f"{target}第{fact['selected_ordinal']}招减半"
            note = (
                f"有效数值{weight_label(fact['original_gain'])} → "
                f"{weight_label(fact['remaining_gain'])}，向下取整"
            )
        mechanism_lines.append(Line(names[int(fact["side"])] + " · 沙之形体", value, note))
    for fact in interactions["zeroes"]:
        _required(
            fact,
            (
                "side",
                "active",
                "relative_zero",
                "dual_domain",
                "target_side",
                "cancelled_ordinals",
                "cancelled_gain",
                "cross_debuff_suppressed",
            ),
            "时空静止结算",
        )
        if not fact["active"]:
            continue
        source = []
        if fact["relative_zero"]:
            source.append("相对静止时间·零")
        if fact["dual_domain"]:
            source.append("双领域时空静止")
        target = names[int(fact["target_side"])]
        ordinals = "、".join(str(item) for item in fact["cancelled_ordinals"]) or "无"
        mechanism_lines.append(
            Line(
                names[int(fact["side"])] + " · " + " + ".join(source),
                f"令{target}本回合数值招式失效",
                f"受影响招式：{ordinals}；共扣除{weight_label(fact['cancelled_gain'])}；功能保留；"
                + ("同时免疫本轮跨回合负面效果" if fact["cross_debuff_suppressed"] else ""),
            )
        )
    if mechanism_lines:
        panels.append(
            Panel(
                "撅撅猪 · 回合机制结算",
                tuple(mechanism_lines),
                "这里只展示已经提交的结果；形态切换、追加抽取和功能招式不会因数值失效而倒流。",
            )
        )

    effect_lines: list[Line] = []
    for fact in interactions["round_reductions"]:
        _required(fact, ("side", "requested", "applied", "floor", "source_ordinals"), "本轮减权结算")
        if not Fraction(fact["requested"]):
            continue
        source = "、".join(str(item) for item in fact["source_ordinals"]) or "领域自动模仿"
        effect_lines.append(
            Line(
                names[int(fact["side"])] + " · 本轮权重削减",
                f"请求{weight_label(fact['requested'])} / 实扣{weight_label(fact['applied'])}",
                f"来源招式：{source}；结算不会低于本回合起始权重{weight_label(fact['floor'])}",
            )
        )
    for fact in interactions["cross_effects"]:
        _required(
            fact,
            (
                "source_side",
                "source_ordinal",
                "target_side",
                "round_reduction",
                "round_reduction_suppressed",
                "next_debt",
                "next_bonus",
                "debt_suppressed",
            ),
            "跨方效果结算",
        )
        source = names[int(fact["source_side"])]
        target = names[int(fact["target_side"])]
        facts = []
        if Fraction(fact["round_reduction"]):
            facts.append(f"本轮减权{weight_label(fact['round_reduction'])}")
        elif fact["round_reduction_suppressed"]:
            facts.append("本轮减权被时空保护免疫")
        if int(fact["next_debt"]):
            facts.append(f"下回合-{fact['next_debt']}招")
        elif fact["debt_suppressed"]:
            facts.append("跨回合欠招被时空保护免疫")
        if int(fact["next_bonus"]):
            facts.append(f"下回合+{fact['next_bonus']}招")
        if int(fact.get("next_milk_dragons", 0)):
            facts.append(f"下回合前{fact['next_milk_dragons']}招覆盖为发奶龙")
        if int(fact.get("exhaust_bonus_units", 0)):
            facts.append(f"力竭盘永久+{_scaled_weight(fact['exhaust_bonus_units'], INJURY_WEIGHT_SCALE)}")
        if fact.get("directed_effect_suppressed"):
            facts.append("定向负面效果被时空保护免疫")
        if facts:
            effect_lines.append(
                Line(
                    f"{source}第{fact['source_ordinal']}招 → {target}",
                    "、".join(facts),
                    "跨方效果在数值归零与时空保护之后统一结算。",
                )
            )
    if effect_lines:
        panels.append(Panel("本回合跨方效果", tuple(effect_lines), "所有减权与下回合招数均来自已提交事实。"))

    v5_lines: list[Line] = []
    transition = interactions.get("daniya_transition")
    if transition:
        v5_lines.append(
            Line(
                names[int(transition["side"])] + " · 蚀域",
                f"{_fighter_form_name('daniya', transition['before'])} → "
                f"{_fighter_form_name('daniya', transition['after'])}",
                f"领域胜利完成形态切换；下回合出招数+{transition.get('next_action_bonus', 0)}。",
            )
        )
    for fact in interactions.get("pressure_checks", ()):
        target = names[int(fact["target_side"])]
        value = f"{target}第{fact['target_ordinal']}招数值失效" if fact["hit"] else "本层判定未命中"
        note = (
            f"来源第{fact['source_ordinal']}招；扣除{weight_label(fact['cancelled_gain'])}；"
            "该招全部胜率数值归零，功能照常结算。"
        )
        v5_lines.append(Line(names[int(fact["side"])] + " · 传奇耐压王", value, note))
    for event in interactions.get("asamu_domain_copies", ()):
        value = f"复制{names[int(event['source_side'])]}的“{event['source_move_name']}”"
        note = f"第{event['copy_slot']}份复制；数值{_signed_weight(event.get('gain', 0))}"
        if event.get("domain_reentry_suppressed"):
            note += "；复制到领域招式时不再次触发领域判定"
        v5_lines.append(Line("领域·呃呃阿萨姆奶茶", value, note))
    for fact in interactions.get("retaliations", ()):
        before, after = fact.get("before", ()), fact.get("after", ())
        value = "交换双方权重后再加成" if fact.get("swapped") else "优势状态直接加成"
        note = (
            f"连续结算{fact.get('count', 1)}次；"
            f"{weight_label(before[0])}/{weight_label(before[1])} → "
            f"{weight_label(after[0])}/{weight_label(after[1])}"
        )
        v5_lines.append(Line(names[int(fact["side"])] + " · 以牙还牙", value, note))
    if v5_lines:
        panels.append(
            Panel(
                "达妮娅猪 / 阿萨姆猪 · 回合机制",
                tuple(v5_lines),
                "失效统一只将一招的全部胜率数值归零；抽数、状态、领域及其他功能事实全部保留。",
            )
        )
    return tuple(panels)


def matchup(
    identity: CommandIdentity,
    match: dict,
    state: dict,
    now_ms: int,
    *,
    title: str = "",
    banner: str = "",
    events: list[dict] | None = None,
    round_result: dict | None = None,
    extra_panels: tuple[Panel, ...] = (),
) -> BattleView:
    definition_version = int(match.get("definition_version") or 1)
    loot_attempts = LEGACY_LOOT_ATTEMPTS if definition_version == 1 else LOOT_ATTEMPTS
    display_sides = round_result["after"] if round_result else state["sides"]
    total = sum(side["weight"] for side in display_sides)
    cards, panels, wheel_cards = [], list(extra_panels), []
    count_cards: list[BattleWheelCard | None] = [None, None]
    move_cards: list[BattleWheelCard | None] = [None, None]
    action_lines: list[tuple[Line, ...]] = [(), ()]
    action_notes = ["", ""]
    # 伤势结算后可能已治愈；出招数图必须使用抽取时(before)的盘，而非刚变化的伤势。
    count_sides = round_result["before"] if round_result else display_sides
    adjustment_maps = [{}, {}]
    domain_bonus_maps: list[dict | None] = [None, None]
    if round_result:
        for side, entries in enumerate(round_result.get("interactions", {}).get("adjustments", ())):
            adjustment_maps[side] = {int(item["ordinal"]): item for item in entries}
        domain = round_result.get("interactions", {}).get("domain") or {}
        boost_side = domain.get("boost_side")
        if boost_side in (0, 1) and domain.get("boosted_ordinal") is not None:
            domain_bonus_maps[int(boost_side)] = {
                "ordinal": int(domain["boosted_ordinal"]),
                "ordinals": tuple(int(item) for item in domain.get("boosted_ordinals", ())),
                "gain": Fraction(domain.get("bonus_gain") or 0),
            }
    for index, count_side in enumerate(count_sides):
        turn = count_side["turn"]
        turn.setdefault("ready", False)
        if turn["raw"] is not None:
            options = HEAVY_COUNT_WHEEL if count_side["heavy"] else COUNT_WHEEL
            count_cards[index] = wheel_card(
                "count",
                "本回合出招数落点",
                tuple((f"{number}招", weight) for number, weight in options),
                f"{turn['raw']}招",
                f"原始{turn['raw']}招 - 贷款{weight_label(turn['debt'])}招 = 实际{turn['effective']}招。",
            )
    if round_result and not events:
        events = [
            event
            for side in round_result["after"]
            for event in side["turn"].get("events", ())
        ]
    if events:
        for event_side in (0, 1):
            side_events = [event for event in events if int(event["side"]) == event_side]
            if not side_events:
                continue
            last = side_events[-1]
            move_cards[event_side] = _event_move_wheel(last, definition_version)
            visible_events = side_events
            action_lines[event_side] = tuple(
                move_line(
                    event,
                    adjustment_maps[event_side].get(int(event["ordinal"])),
                    effective_total=(
                        effective_total_after(
                            event,
                            adjustment_maps[event_side],
                            domain_bonus_maps[event_side],
                        )
                        if round_result
                        else None
                    ),
                    domain_bonus=(
                        domain_bonus_maps[event_side]
                        if domain_bonus_maps[event_side]
                        and int(domain_bonus_maps[event_side]["ordinal"]) == int(event["ordinal"])
                        else None
                    ),
                )
                for event in visible_events
            )
            action_notes[event_side] = (
                f"本回合共{len(side_events)}招，以上为完整出招记录。"
                if round_result
                else f"本次实际执行{len(side_events)}招，以上不裁切展示全部已提交事实；可用 "
                f"/对战记录 {match['battle_id']} {side_events[0]['round']} 复核。"
            )
    all_done = all(side["turn"].get("done", False) for side in count_sides)
    for index, side in enumerate(display_sides):
        snap, turn = side["snapshot"], side["turn"]
        turn.setdefault("ready", False)
        if match["status"] == "pending" and snap.get("coupon_preview"):
            panels.append(
                Panel(
                    f"{snap['player_name']} · 已选成就券",
                    tuple(Line(c["name"], f"库存{c['quantity']}张", c["effect"]) for c in snap["coupon_preview"]),
                    "等待应战，不扣券；接受后只制作入场外观，不改变出招与胜利权重。",
                )
            )
        if snap.get("achievement_entry"):
            coupon = snap["achievement_entry"]
            panels.append(
                Panel(
                    f"{snap['player_name']} · 原创入场海报",
                    (
                        Line(
                            "今天的主角，先站稳再说！",
                            f"{snap['name']}将训练手账翻到空白的一页：这场比划，由我们写下。",
                            f"{coupon['name']} · 剩余{coupon['remaining']}张 · 仅外观，初始胜利权重仍为5",
                        ),
                    ),
                )
            )
        raw = turn["raw"]
        count = (
            "等待 /出招数"
            if raw is None
            else f"抽中{raw} - 贷款{weight_label(turn['debt'])} = 实际{turn['effective']}招"
        )
        tool = snap.get("tool_id", "")
        tool_note = (
            "无器具"
            if not tool
            else TOOLS_BY_ID[tool].name + (" · 已触发" if side["tool_used"] else " · 待触发/终局退回")
        )
        mechanic_notes = []
        if side.get("black_flash_stacks"):
            mechanic_notes.append(f"黑闪领悟+{weight_label(side['black_flash_stacks'])}")
        if side.get("purple_weight_steps"):
                mechanic_notes.append(
                    f"茈盘+{_scaled_weight(side['purple_weight_steps'], DYNAMIC_DRAW_STEP_SCALE)}"
                )
        if mechanic_notes:
            tool_note += " · " + " · ".join(mechanic_notes)
        if turn["done"]:
            ready = "本回合已结算" if round_result and all_done else "已出完，等待对方"
        else:
            ready = "尚未出完"
        if definition_version >= 3:
            if round_result and round_result.get("carryover"):
                carry = round_result["carryover"][index]
                inherited = Fraction(carry["round_start_weight"]) - 5
                weight_breakdown = (
                    f"基础5 + 历史折半继承{weight_label(inherited)} + "
                    f"本回合净增{weight_label(carry['round_gain'])}"
                )
                next_weight = (
                    "整场已结束，本回合权重不再迁移"
                    if carry.get("next_round_weight") is None
                    else (
                        f"本回合净增按50%向上取整保留{weight_label(carry['retained_gain'])}；"
                        f"下回合起始{weight_label(carry['next_round_weight'])}"
                    )
                )
            else:
                start = Fraction(side.get("round_start_weight", side["weight"]))
                inherited = start - 5
                current = Fraction(side["weight"]) - start
                weight_breakdown = (
                    f"基础5 + 历史折半继承{weight_label(inherited)} + 本回合净增{weight_label(current)}"
                )
                next_weight = "回合结算后，仅本回合净增的50%向上取整迁移"
        else:
            weight_breakdown = "旧规则：跨回合完整保留累计权重"
            next_weight = ""
        form = form_track = mechanic_summary = ""
        if snap.get("fighter_id") == "juejue":
            form, form_track, mechanic_summary = _juejue_state_projection(side)
        elif snap.get("fighter_id") == "daniya":
            form, form_track, mechanic_summary = _daniya_state_projection(side)
        elif snap.get("fighter_id") == "asamu":
            form, form_track, mechanic_summary = _asamu_state_projection(side)
        cards.append(
            FighterCard(
                player_name=snap["player_name"],
                pig_name=snap["name"],
                short_code=snap["short_code"],
                level=snap["level"],
                weight=weight_label(side["weight"]),
                chance=_percent(side["weight"], total),
                count=count,
                injury="重伤 · 数值招式-1" if side["heavy"] else "正常出招盘",
                risk=("初始风险", "轻伤风险", "重伤风险")[side["risk"]],
                core=weight_label(side["core"]),
                debt=f"下回合待扣{weight_label(side['next_debt'])}招",
                pending="已出完" if turn["done"] else f"待连抽{weight_label(turn['pending'])}次",
                tool=tool_note,
                ready=ready,
                form=form,
                form_track=form_track,
                mechanic_summary=mechanic_summary,
                weight_breakdown=weight_breakdown,
                next_weight=next_weight,
                count_wheel=count_cards[index],
                move_wheel=move_cards[index],
                action_lines=action_lines[index],
                action_note=action_notes[index],
            )
        )
    if round_result:
        winner = display_sides[round_result["winner"]]["snapshot"]["player_name"]
        loser = display_sides[round_result["loser"]]["snapshot"]["player_name"]
        interactions = round_result.get("interactions", {})
        if definition_version >= 4:
            _required(
                interactions,
                (
                    "domain",
                    "adjustments",
                    "future_simulations",
                    "sand_bodies",
                    "zeroes",
                    "round_reductions",
                    "cross_effects",
                ),
                "v4回合交互",
            )
        wheel_cards.append(
            wheel_card(
                "injury",
                loser + " · 伤势盘落点",
                tuple(
                    (INJURY_NAMES[key], _scaled_weight(weight, INJURY_WEIGHT_SCALE))
                    for key, weight in round_result["injury_wheel"]
                ),
                INJURY_NAMES[round_result["injury"]],
                "扇区按本轮抽取时的风险权重绘制；标记是已经保存的结果。",
            )
        )
        domain = interactions.get("domain")
        if domain:
            names = [side["snapshot"]["player_name"] for side in display_sides]
            if definition_version >= 4:
                _required(
                    domain,
                    (
                        "mode",
                        "wheel",
                        "weight_scale",
                        "strengths",
                        "outcome",
                        "winner",
                        "hit_side",
                        "domain_counts",
                        "domain_ids",
                        "dual_juejue",
                        "boost_side",
                        "boosted_ordinal",
                        "boosted_ordinals",
                        "bonus_gain",
                        "effects",
                        "auto_mimic",
                        "nullified_side",
                        "cross_debuff_suppressed",
                    ),
                    "v4领域结算",
                )
            labels = {
                "side-0": names[0] + "领域胜",
                "side-1": names[1] + "领域胜",
                "tie": "领域平手",
                "hit": "领域命中",
                "simple-domain": "简易领域免疫",
            }
            scale = int(domain.get("weight_scale") or 1)
            domain_options = tuple((labels[key], _scaled_weight(weight, scale)) for key, weight in domain["wheel"])
            wheel_cards.insert(
                0,
                wheel_card(
                    "domain",
                    "领域战判定" if domain["mode"] == "clash" else "领域命中判定",
                    domain_options,
                    labels[domain["outcome"]],
                    "同回合多次领域只判定一次；图中显示换算后的领域权重，落点是已提交事实。",
                ),
            )
            domain_lines = [
                Line("判定结果", labels[domain["outcome"]], domain.get("effect") or "没有追加领域效果"),
                Line(
                    "领域次数",
                    f"{names[0]} ×{domain['domain_counts'][0]} / {names[1]} ×{domain['domain_counts'][1]}",
                    "每方即使多次展开，本回合仍只判一次。",
                ),
            ]
            if domain["mode"] == "clash" and definition_version >= 4:
                tie_weight = next(weight for key, weight in domain["wheel"] if key == "tie")
                domain_lines.append(
                    Line(
                        "领域战权重",
                        f"{names[0]} {_scaled_weight(domain['strengths'][0], scale)} / "
                        f"{names[1]} {_scaled_weight(domain['strengths'][1], scale)} / "
                        f"平手 {_scaled_weight(tie_weight, scale)}",
                        "宿傩、普通领域、撅撅猪单领域与双领域均按各自已发布权重进入同一轮盘。",
                    )
                )
            if domain.get("boost_side") in (0, 1) and domain.get("bonus_gain"):
                ordinals = tuple(int(item) for item in domain.get("boosted_ordinals", ()))
                target = (
                    "第" + "、".join(str(item) for item in ordinals) + "招"
                    if ordinals
                    else f"第{domain['boosted_ordinal']}招"
                )
                domain_lines.append(
                    Line(
                        "领域胜方加倍",
                        f"{names[int(domain['boost_side'])]} {target}合计额外 "
                        f"+{weight_label(domain['bonus_gain'])}",
                        "普通领域只翻倍一份；撅撅猪两个不同领域同回合齐出且胜出时，两份相加后一起翻倍。",
                    )
                )
            dual = [names[index] for index, active in enumerate(domain.get("dual_juejue", ())) if active]
            if dual:
                domain_lines.append(
                    Line(
                        "双领域共鸣",
                        "、".join(dual),
                        "荒时之沙与乱序数虚时空同时成立；胜出后对方本回合数值招式失效。",
                    )
                )
            if domain.get("nullified_side") in (0, 1):
                domain_lines.append(
                    Line(
                        "时空静止目标",
                        names[int(domain["nullified_side"])],
                        "只令本回合数值贡献失效，不倒流已经发生的抽数与功能。",
                    )
                )
            if domain.get("auto_mimic") is not None:
                auto = domain["auto_mimic"]
                domain_lines.append(
                    Line(
                        "乱序数虚时空 · 自动模仿",
                        _mimic_fact(auto, label="领域自动模仿"),
                        (
                            f"本次自身增加{weight_label(auto.get('gain', 0))}；"
                            f"对手减权请求{weight_label(auto.get('opponent_reduction', 0))}"
                        ),
                    )
                )
            for effect in domain.get("effects", ()):
                domain_lines.append(Line("领域追加效果", str(effect), "已在回合状态中提交"))
            if domain.get("cross_debuff_suppressed"):
                domain_lines.append(Line("时空保护", "跨回合负面效果已免疫", "领域自身的正面效果仍正常结算。"))
            panels.append(
                Panel(
                    "本回合领域判定",
                    tuple(domain_lines),
                    "领域败方（或平手双方）的领域数值归零；领域功能是否命中，以本卡已提交事实为准。",
                )
            )
        if definition_version >= 4:
            names = [side["snapshot"]["player_name"] for side in display_sides]
            panels.extend(_v4_interaction_panels(interactions, names))
        modifiers = round_result.get("injury_modifiers")
        if modifiers:
            lines = [
                Line(
                    "力竭权重倍率",
                    f"×{modifiers.get('total_exhaust_multiplier', 1)}",
                    f"厄运传递×{modifiers.get('misfortune_count', 0)}；"
                    f"计时溃灭×{modifiers.get('current_collapse_multiplier', 1)}；"
                    f"反噬×{modifiers.get('rebound_multiplier', 1)}",
                ),
                Line(
                    "永久力竭加权",
                    f"+{_scaled_weight(modifiers.get('permanent_exhaust_bonus_units', 0), INJURY_WEIGHT_SCALE)}",
                    "达妮娅幻灭招式累积，直接加入本次伤势盘。",
                ),
            ]
            for rebound in round_result.get("collapse_rebounds", ()):
                lines.append(
                    Line(
                        names[int(rebound["caster_side"])] + " · 计时反噬",
                        "目标本回合力竭" if rebound["target_exhausted"] else "反噬登记至下回合",
                        f"反噬回合：{rebound.get('rebound_round') or '无'}",
                    )
                )
            panels.append(Panel("本回合伤势修正", tuple(lines), "轮盘扇区已经包含以上精确修正。"))
        panel_note = (
            f"整场结束，败者获得{loot_attempts}次额外战利品抓猪，全部归胜者。"
            if round_result["natural_end"]
            else (
                "本回合使用完整结算权重抽胜负；各自本回合净增的50%向上取整后迁移到下一回合。"
                if int(match.get("definition_version") or 1) >= 3
                else "旧规则累计胜利权重完整保留，双方继续下一回合。"
            )
        )
        injury_lines = [
            Line(
                loser + "的伤势盘",
                INJURY_NAMES[round_result["injury"]]
                + ("（本轮已回溯）" if round_result.get("injury_rewound") else ""),
                "抽取权重："
                + " / ".join(
                    f"{INJURY_NAMES[k]} {_scaled_weight(v, INJURY_WEIGHT_SCALE)}"
                    for k, v in round_result["injury_wheel"]
                ),
            )
        ]
        if round_result.get("injury_rewound"):
            injury_lines.append(
                Line(
                    loser + " · 时之沙·回溯",
                    "撤销本轮新伤势",
                    "只撤销本轮新抽到的轻伤或重伤；没有清除历史风险，也不能挽救力竭。",
                )
            )
        panels.append(
            Panel(
                f"第{round_result['round']}回合 · {winner}胜",
                tuple(injury_lines),
                panel_note,
            )
        )
    remaining = max(0, (match["expires_ms"] - now_ms + 999) // 1000)
    if match["status"] == "pending":
        hints = (
            "受邀者：/比划比划 接受 或 /比划比划 拒绝；邀请者：/比划比划 取消。",
            f"接受后才扣今日各自角色额度并锁定猪猪；自然力竭败者的{loot_attempts}只战利品归胜者。",
        )
        banner = (
            banner or f"{display_sides[1]['snapshot']['player_name']}，请在{remaining}秒内应战。尚未消耗额度或器具。"
        )
    elif state["status"] == "active":
        if all(side["turn"].get("done", False) for side in state["sides"]):
            hints = (
                "该对局从旧确认流程恢复：任一参战者再输入一次 /出招，即按已保存招式结算本回合。",
                f"{remaining}秒内需推进；不会重新抽取已经保存的出招数或招式。",
            )
        else:
            hints = (
                f"第{state['round']}回合：双方各自 /出招数 → /出招；第二位完成出招时自动结算。长连锁可继续 /出招。",
                f"{remaining}秒内需有有效推进，查询和重复消息不延长；超时或认输不发战利品。",
            )
    elif state["status"] == "completed":
        winner = display_sides[state["winner"]]["snapshot"]["player_name"]
        loser = display_sides[1 - state["winner"]]["snapshot"]["player_name"]
        banner = banner or (
            f"{winner} 获胜！{loser} 力竭倒下，获得{loot_attempts}次额外战利品抓猪，"
            f"抓到的猪全部归 {winner}。"
        )
        hints = (
            "/战利品抓猪 领取自然力竭败者专属次数；/对战记录 查看完整过程。",
            "未触发器具退回；本场临时核心、伤势、贷款全部结束，不修改普通抓猪加成。",
        )
    else:
        hints = (
            "本场没有自然力竭胜负，不发放战利品；/对战记录 查看过程。",
            "未触发器具退回，解除本场猪猪占用。",
        )
    return view(
        identity,
        title or STATUS_NAMES[state["status"]],
        banner=banner,
        pigs=tuple(pig_card(side["snapshot"]) for side in display_sides),
        fighters=tuple(cards),
        battle_id=match["battle_id"],
        round_label=(
            f"第{round_result['round'] if round_result else state['round']}回合 · {STATUS_NAMES[state['status']]}"
        ),
        win_percent=_percent(display_sides[0]["weight"], total),
        panels=tuple(panels),
        hints=hints,
        wheels=tuple(wheel_cards),
        retention_mode=("half-round" if int(match.get("definition_version") or 1) >= 3 else "legacy-full"),
        celebration=state["status"] == "completed"
        or (
            state["status"] == "active"
            and state["round"] == 1
            and any(s["snapshot"].get("tool_id") == "confetti" for s in display_sides)
        ),
    )


def _juejue_move_effect(move, level: int) -> str:
    numeric = f"胜利权重+{move.gain + level}；" if move.gain else ""
    effects = {
        "sand-sculpt": "荒时之沙抽取权重+0.1（领域后清除）；下一次加速/时延成功率+5个百分点",
        "sand-rewind": (
            "消除本回合一次加速失败产生的整笔下回合欠招；当前没有待消除失败时可先挂起；"
            "本回合若落败且新抽中轻伤或重伤，仍撤销本轮新伤势；不能挽救力竭或回溯旧风险"
        ),
        "sand-accelerate": "进入加速盘；成功后增加胜利权重并按档位追加抽取",
        "sand-delay": "进入时延盘；成功后压低对方本回合权重并影响对方下回合出招数",
        "sand-body": "对方本回合第一个仍有效的数值招式胜利权重减半（向下取整；同回合不叠）",
        "sand-seal": "纯数值招式",
        "switch-virtual": "即时切换至虚拟声，并从虚拟声轮盘再抽2次",
        "sand-domain": "主盘抽取权重1、单领域战权重2.5；单方领域命中或领域战获胜后，对方下回合-1招、自己下回合+1招",
        "virtual-realm": "再抽1次；下一次加速或时延判定必定成功",
        "future-simulation": "每次抽中都独立随机令对方本回合一个带胜利权重的招式无效",
        "realtime-compute": "再抽1次；本回合荒时之沙与乱序数虚时空的抽取权重各+1（不叠）",
        "virtual-mimic": (
            "大/小轮盘各50%；复制其他战斗猪可模仿招式的数值、一般功能与定向效果；"
            "复制领域不重开领域战，复制招式的追加抽数不递归"
        ),
        "make-real": "下次再次使用时额外+5，逐次累加",
        "louder": "首次进入本回合音乐状态，之后每招固定+5；重复抽中不叠层，改为再抽2次",
        "switch-sand": (
            "即时切换至时之沙并再抽1次；下一次荒时之沙+0.5抽取权重；"
            "下一次加速与下一次时延成功率各+5个百分点"
        ),
        "chaos-domain": (
            "主盘抽取权重1、单领域战权重2.5；单方领域命中或领域战获胜后"
            "自动虚拟模仿1次、下回合+1招，并保证下一次加速或时延成功"
        ),
    }
    try:
        return numeric + effects[move.move_id]
    except KeyError as exc:
        raise ValueError(f"撅撅猪轮盘存在未投影招式：{move.move_id}") from exc


def _juejue_wheels(identity: CommandIdentity, level: int) -> BattleView:
    definition = FIGHTERS_BY_ID["juejue"]
    forms = {form.form_id: form for form in definition.forms}
    if set(forms) != {JUEJUE_FORM_TIME, JUEJUE_FORM_VIRTUAL}:
        raise ValueError("撅撅猪正式目录必须且只能包含时之沙与虚拟声两张形态盘。")
    time_moves = forms[JUEJUE_FORM_TIME].moves
    virtual_moves = forms[JUEJUE_FORM_VIRTUAL].moves
    move_lines = tuple(
        Line(
            f"{_form_name(form_id)} · {move.name}",
            _juejue_move_effect(move, level),
            f"基础抽取权重{_scaled_weight(move.resolved_draw_weight_units, MOVE_WEIGHT_SCALE)}；"
            "切换后，尚未执行的追加抽取立即改用新形态盘。",
        )
        for form_id, form_moves in ((JUEJUE_FORM_TIME, time_moves), (JUEJUE_FORM_VIRTUAL, virtual_moves))
        for move in form_moves
    )
    acceleration_lines = tuple(
        Line(
            f"{tier.tier}档 · 基础成功率{tier.success_chance}%",
            f"成功+{tier.gain + level}并再抽{tier.extra_draws}次",
            "失败无额外惩罚"
            if not tier.failure_debt
            else f"失败后自己下回合出招数-{tier.failure_debt}",
        )
        for tier in JUEJUE_ACCELERATION_TIERS
    )
    delay_lines = tuple(
        Line(
            f"{tier.tier}档 · 基础成功率{tier.success_chance}%",
            f"成功自身+{tier.gain + level}、对方本回合-{tier.opponent_reduction}",
            (
                f"成功后对方下回合-{tier.opponent_debt}招；" if tier.opponent_debt else ""
            )
            + (
                f"失败后对方下回合+{tier.failure_opponent_bonus}招"
                if tier.failure_opponent_bonus
                else "失败无额外结果"
            ),
        )
        for tier in JUEJUE_DELAY_TIERS
    )
    return view(
        identity,
        "撅撅猪 · 双形态战斗轮盘",
        banner=(
            f"展示强化+{level}的数值。入场以等概率固定一种形态；切换招式即时换盘，随后追加抽取使用新盘。"
        ),
        wheels=(
            wheel_card(
                "move",
                "撅撅猪 · 时之沙",
                tuple(
                    (move.name, _scaled_weight(move.resolved_draw_weight_units, MOVE_WEIGHT_SCALE))
                    for move in time_moves
                ),
                note="八格基础等权且领域主盘权重也是1；塑型、实时演算与切回会改变领域的真实抽取权重。",
            ),
            wheel_card(
                "move",
                "撅撅猪 · 虚拟声",
                tuple(
                    (move.name, _scaled_weight(move.resolved_draw_weight_units, MOVE_WEIGHT_SCALE))
                    for move in virtual_moves
                ),
                note="八格基础等权且领域主盘权重也是1；虚拟模仿的大盘/小盘各占50%。",
            ),
            wheel_card(
                "subwheel",
                "时之沙 · 加速盘",
                tuple((f"{tier.tier}档", 1) for tier in JUEJUE_ACCELERATION_TIERS),
                note="三档等权；先抽档位，再按已提交的最终成功率判定。",
            ),
            wheel_card(
                "subwheel",
                "时之沙 · 时延盘",
                tuple((f"{tier.tier}档", 1) for tier in JUEJUE_DELAY_TIERS),
                note="三档等权；失败结果同样会写入本回合事实并在图中展示。",
            ),
            wheel_card("count", "正常出招数", tuple((f"{number}招", weight) for number, weight in COUNT_WHEEL)),
            wheel_card(
                "count", "重伤出招数", tuple((f"{number}招", weight) for number, weight in HEAVY_COUNT_WHEEL)
            ),
            *(
                wheel_card(
                    "injury",
                    ("初始风险", "轻伤风险", "重伤风险")[i],
                    tuple(
                        (INJURY_NAMES[key], _scaled_weight(weight, INJURY_WEIGHT_SCALE))
                        for key, weight in options
                    ),
                    note="扇区面积为抽取权重占比；不是胜利概率。",
                )
                for i, options in enumerate(INJURY_WHEELS)
            ),
        ),
        panels=(
            Panel("双形态招式", move_lines, "每个实际落点会保存来源形态、切换前后形态与真实动态轮盘。"),
            Panel("加速盘", acceleration_lines, "塑型、切换与必定成功效果只影响下一次兼容判定。"),
            Panel("时延盘", delay_lines, "成功与失败都可能改变下一回合出招数；溢出欠招不跨两回合。"),
            Panel(
                "组合机制",
                (
                    Line(
                        "相对静止时间·零",
                        "本回合第一次加速+第一次时延均成功且档位和≥5时判定50%",
                        "后续重复加速/时延不参与凑档；成功后自身固定+40，对方本回合全部数值贡献无效。",
                    ),
                    Line(
                        "双领域",
                        "荒时之沙 + 乱序数虚时空",
                        "仅在双方都开领域且撅撅猪获胜时，两份仍有效领域数值相加再翻倍；模仿不参与翻倍。",
                    ),
                    Line(
                        "虚拟模仿",
                        "大盘≥20 / 小盘<20",
                        "按数值绝对值分盘；复制数值与一般效果，但抑制领域再入和复制招式的追加抽数。",
                    ),
                ),
                "组合技只结算一次；图中展示已提交的领域权重、模仿来源和最终落点。",
            ),
        ),
        hints=(
            "当前形态与完整切换轨迹会显示在参战猪猪下方；长连锁不省略事实。",
            "实时演算本回合不重复叠层；音乐重复抽中改为再抽2次，不增加音乐层数。",
            "回溯还能消除一笔加速失败产生的整笔欠招；不能取消力竭，也不降低历史风险。",
            "未来模拟每次抽中独立结算；虚拟模仿候选池随对战规则版本冻结。",
            "领域招式主盘基础抽取权重为1；领域战另算：普通3、宿傩4、撅撅猪单领域2.5、双领域5.5、平手3。",
        ),
    )


def _common_battle_wheels() -> tuple[BattleWheelCard, ...]:
    return (
        wheel_card("count", "正常出招数", tuple((f"{number}招", weight) for number, weight in COUNT_WHEEL)),
        wheel_card("count", "重伤出招数", tuple((f"{number}招", weight) for number, weight in HEAVY_COUNT_WHEEL)),
        *(
            wheel_card(
                "injury",
                ("初始风险", "轻伤风险", "重伤风险")[i],
                tuple((INJURY_NAMES[key], _scaled_weight(weight, INJURY_WEIGHT_SCALE)) for key, weight in options),
                note="扇区面积为抽取权重占比；不是胜利概率。",
            )
            for i, options in enumerate(INJURY_WHEELS)
        ),
    )


def _move_weight(move) -> int | float:
    return _scaled_weight(move.resolved_draw_weight_units, MOVE_WEIGHT_SCALE)


def _daniya_effect(move, level: int) -> str:
    gain = Fraction(move.resolved_gain_tenths, VICTORY_WEIGHT_SCALE)
    enhanced = gain + level if gain > 0 else gain
    numeric = f"自身胜率{_signed_weight(enhanced)}" if gain else ""
    if move.resolved_opponent_reduction_tenths:
        numeric += f"、对方-{weight_label(Fraction(move.resolved_opponent_reduction_tenths, VICTORY_WEIGHT_SCALE))}"
    mechanics = {
        "daniya-staging-dream-feast": "下一次蚀域主盘权重+0.1",
        "daniya-staging-mimic-bubble": "下一次蚀域主盘权重+0.1",
        "daniya-staging-final-curtain": "下一次蚀域主盘权重+0.1",
        "daniya-staging-greeting": "下一次蚀域主盘权重+0.1",
        "daniya-disillusion-dream-feast": "对方力竭盘永久+0.1",
        "daniya-disillusion-banish": "对方力竭盘永久+0.1",
        "daniya-disillusion-final-curtain": "对方力竭盘永久+0.1",
        "daniya-disillusion-knock": "对方力竭盘永久+0.1",
        "daniya-flawless": "再抽2次",
        "daniya-unfinished-lie": "再抽1次；下一次数值招式自身加权及对方减权同步×2；下回合-1招",
        "daniya-timed-collapse": "对方本回合力竭权重×5；若对方未力竭，自己下回合力竭权重×5",
        "daniya-domain": "领域胜利后切换幻灭形态，自己下回合+1招",
    }[move.move_id]
    return "；".join(part for part in (numeric, mechanics) if part)


def _daniya_wheels(identity: CommandIdentity, level: int) -> BattleView:
    definition = FIGHTERS_BY_ID["daniya"]
    forms = {form.form_id: form for form in definition.forms}
    form_wheels = tuple(
        wheel_card(
            "move",
            f"达妮娅猪 · {DANIYA_FORM_NAMES[form_id]}",
            tuple((move.name, _move_weight(move)) for move in forms[form_id].moves),
            note="形态专属4招与公共4招同盘；计时的溃灭基础权重0.5，其余基础权重1。",
        )
        for form_id in (DANIYA_FORM_STAGING, DANIYA_FORM_DISILLUSION)
    )
    lines = tuple(
        Line(
            f"{DANIYA_FORM_NAMES[form_id]} · {move.name}",
            _daniya_effect(move, level),
            f"抽取权重 {_move_weight(move)}；正数胜率受强化，-52.1与功能数值不受强化。",
        )
        for form_id in (DANIYA_FORM_STAGING, DANIYA_FORM_DISILLUSION)
        for move in forms[form_id].moves
    )
    return view(
        identity,
        "达妮娅猪 · 双形态战斗轮盘",
        banner=f"展示强化+{level}的数值。默认布景；蚀域在领域战胜利后即时切换为幻灭。",
        wheels=(*form_wheels, *_common_battle_wheels()),
        panels=(
            Panel("双形态招式", lines, "重复出现的公共招式是同一规则，只因当前形态不同而进入不同轮盘。"),
            Panel(
                "形态与伤势机制",
                (
                    Line("布景", "每次布景数值招式令下次蚀域抽取权重+0.1", "蚀域实际使用后清除累计。"),
                    Line("幻灭", "每次幻灭数值招式令对方力竭盘永久+0.1", "精确累积，不提前取整。"),
                    Line("计时的溃灭", "自身-52.1", "若对手未在本回合力竭，×5反噬登记到自己下回合。"),
                ),
            ),
        ),
        hints=(
            "招式抽取权重按千分之一保存；胜利权重按十分之一保存，图中文字展示精确小数。",
            "数值失效只归零胜率数值，贷款、形态与伤势功能仍保留。",
        ),
    )


def _asamu_effect(move, level: int) -> str:
    gain = Fraction(move.resolved_gain_tenths, VICTORY_WEIGHT_SCALE)
    numeric = f"胜率+{weight_label(gain + level)}" if gain > 0 else ""
    mechanics = {
        "asamu-bathe": "喝奶茶抽取权重+0.5",
        "asamu-milk-tea": "睡觉抽取权重+0.25，并重置喝奶茶当前加权",
        "asamu-sleep": "全盛姿态抽取权重永久+0.1，并重置睡觉当前加权",
        "asamu-prime": "再抽1次",
        "asamu-charge-up": "本场后续每个招式的胜率数值额外+3，可无限累积",
        "asamu-pressure-king": "本层对方每个数值招式独立33%失效；功能保留",
        "asamu-misfortune-transfer": "双方本回合力竭倒下权重各×5",
        "asamu-milk-dragon": "依次覆盖对方下回合第一、第二……招为发奶龙",
        "asamu-tit-for-tat": "回合末落后则交换双方权重并再+4；未落后则自身+40",
        "asamu-domain": "领域胜利后随机使用对方4个招式；复制领域不再次发起领域判定",
    }[move.move_id]
    return "；".join(part for part in (numeric, mechanics) if part)


def _asamu_wheels(identity: CommandIdentity, level: int) -> BattleView:
    definition = FIGHTERS_BY_ID["asamu"]
    moves = definition.moves
    total_units = sum(move.resolved_draw_weight_units for move in moves)
    lines = tuple(
        Line(
            move.name,
            _asamu_effect(move, level),
            f"基础抽取权重 {_move_weight(move)}；基础出现概率 "
            f"{100 * move.resolved_draw_weight_units / total_units:.2f}%（动态加权前）。",
        )
        for move in moves
    )
    return view(
        identity,
        "阿萨姆猪 · 动态战斗轮盘",
        banner=f"展示强化+{level}的数值。喝奶茶、睡觉、全盛姿态会在战斗中动态改变抽取权重。",
        wheels=(
            wheel_card("move", "阿萨姆猪 · 基础招式盘", tuple((move.name, _move_weight(move)) for move in moves)),
            *_common_battle_wheels(),
        ),
        panels=(
            Panel("动态招式盘", lines, "展示基础盘；实战落点卡会显示当时的精确动态轮盘。"),
            Panel(
                "特殊结算",
                (
                    Line("传奇耐压王", "每层独立33%", "命中后该招全部胜率数值归零，抽数与技能功能照常。"),
                    Line(
                        "发奶龙",
                        "覆盖对方下回合前序招式",
                        "原抽取落点会保存在事实中；被覆盖的发奶龙不反向施加覆盖。",
                    ),
                    Line(
                        "以牙还牙",
                        "无伤0.4 / 轻伤0.749 / 重伤0.947",
                        "回合末共用同一结算快照；每次抽中各给后置奖励，双方权重交换至多一次。",
                    ),
                    Line("领域·呃呃阿萨姆奶茶", "领域胜利复制对方4招", "逐份展示来源、数值与领域再入抑制。"),
                ),
            ),
        ),
        hints=("憋个大的永久+3只增加招式胜率数值，不改变抽中权重。", "厄运传递和计时的溃灭按独立倍率乘入伤势盘。"),
    )


def wheels(identity: CommandIdentity, fighter_id: str, level: int = 0) -> BattleView:
    if fighter_id == "juejue":
        return _juejue_wheels(identity, level)
    if fighter_id == "daniya":
        return _daniya_wheels(identity, level)
    if fighter_id == "asamu":
        return _asamu_wheels(identity, level)
    definition = FIGHTERS_BY_ID[fighter_id]
    moves = []
    for move in definition.moves:
        effect = f"胜利权重+{move.gain + level}" if move.gain else ""
        if move.draws:
            effect += f" 再抽{move.draws}次"
        if move.loan:
            effect += "；下个数值招式×2，下回合扣1招"
        total_units = sum(item.resolved_draw_weight_units for item in definition.moves)
        moves.append(
            Line(
                move.name,
                effect.strip(),
                f"抽取权重 {_move_weight(move)}；出现概率 "
                f"{100 * move.resolved_draw_weight_units / total_units:.2f}%",
            )
        )
    return view(
        identity,
        definition.name + " · 战斗轮盘",
        banner=f"展示强化+{level}的数值。功能招式不强化，抽中概率不随升级变化。",
        wheels=(
            wheel_card(
                "move",
                definition.name + " · 等权招式盘",
                tuple((move.name, _move_weight(move)) for move in definition.moves),
                note="规则预览；没有抽取，强化只增加数值招式的胜利权重。",
            ),
            wheel_card("count", "正常出招数", tuple((f"{number}招", weight) for number, weight in COUNT_WHEEL)),
            wheel_card("count", "重伤出招数", tuple((f"{number}招", weight) for number, weight in HEAVY_COUNT_WHEEL)),
            *(
                wheel_card(
                    "injury",
                    ("初始风险", "轻伤风险", "重伤风险")[i],
                    tuple(
                        (INJURY_NAMES[key], _scaled_weight(weight, INJURY_WEIGHT_SCALE))
                        for key, weight in options
                    ),
                    note="扇区面积为抽取权重占比；不是胜利概率。",
                )
                for i, options in enumerate(INJURY_WHEELS)
            ),
        ),
        panels=(
            Panel("等权招式盘", tuple(moves)),
            Panel(
                "出招数与伤势盘",
                (
                    Line("正常出招数", " / ".join(f"{n}次:{w}" for n, w in COUNT_WHEEL)),
                    Line("重伤出招数", " / ".join(f"{n}次:{w}" for n, w in HEAVY_COUNT_WHEEL)),
                    *(
                        Line(
                            ("初始风险", "轻伤风险", "重伤风险")[i],
                            " / ".join(
                                f"{INJURY_NAMES[k]}:{_scaled_weight(w, INJURY_WEIGHT_SCALE)}" for k, w in wheel
                            ),
                        )
                        for i, wheel in enumerate(INJURY_WHEELS)
                    ),
                ),
                "这里的冒号后是抽取权重，不是百分比。",
            ),
        ),
        hints=(
            "每次核心解除重伤并使后续数值招式+1，无叠加上限；历史风险不降低。",
            "个体体型/体重在各自模板范围的平均位置≥75%：每回合首个数值招式另+1，不参与贷款翻倍。",
            "黑闪基础+10并再抽2次；每次黑闪令后续数值招式再+1。苍/赫令两种茈的抽取权重各+0.1，任意茈发动后归零重算。",
            "双领域胜方仅一份仍有效的领域招式权重翻倍；本回合净增仅有50%向上取整迁移到后续回合。",
            "无下限每回合只免疫对方首个仍有效的数值招式；领域同回合只判定一次。",
        ),
    )
