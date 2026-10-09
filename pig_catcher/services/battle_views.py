"""将已提交对战事实投影成图卡；不在渲染中抽盘或做资源结算。"""

from __future__ import annotations

from fractions import Fraction

from ..domain.battle import weight_label
from ..domain.battle_catalog import (
    COUNT_WHEEL,
    DANIYA_FORM_DISILLUSION,
    DANIYA_FORM_STAGING,
    FIGHTERS_BY_ID,
    FIREFLY_FORM_FIREFLY,
    FIREFLY_FORM_SAM,
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
from ..domain.daniya_battle import FIXED_BLACK_HOLE_WHEEL
from ..domain.daniya_catalog import BLACK_HOLE
from ..domain.dispatch import MATERIALS, safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchPigCard
from ..domain.display import format_length, format_weight
from ..domain.mirror_battle_catalog import LUOLI_STATUS_HELP
from ..domain.models import CommandIdentity
from ..domain.xixi_battle_catalog import (
    EMPEROR_GAIN,
    HEXTECH_NAMES,
    HEXTECH_SPECS,
    XIXI_CORE_NAME,
    XIXI_FORM_CELESTIAL,
    XIXI_FORM_EMPEROR,
)

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
    BLACK_HOLE: "黑洞",
}

XIXI_FORM_NAMES = {XIXI_FORM_CELESTIAL: "西天帝路线", XIXI_FORM_EMPEROR: "西西天帝"}

FIREFLY_FORM_NAMES = {
    FIREFLY_FORM_FIREFLY: "流萤",
    FIREFLY_FORM_SAM: "萨姆",
}

# 塑型、苍赫和布景招式在状态里按“十分之一抽取权重”保存；
# 它们不是 MOVE_WEIGHT_SCALE=10000 的主轮盘单位，展示时必须单独换算。
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
    if fighter_id == "firefly":
        try:
            return FIREFLY_FORM_NAMES[form_id]
        except KeyError as exc:
            raise ValueError(f"未知流萤抱抱猪形态：{form_id}") from exc
    if fighter_id == "xixi":
        try:
            return XIXI_FORM_NAMES[form_id]
        except KeyError as exc:
            raise ValueError(f"未知西西猪形态：{form_id}") from exc
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
    if event.get("realtime_repeated"):
        note_parts.append("实时演算重复抽中：本次胜利权重基底改为+10并再抽2次")
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
    elif event.get("music_activated") and int(event.get("extra_draws", 0)):
        note_parts.append("音乐状态首次开启；本次再抽1次")
    if int(event.get("juejue_success_next_action_bonus_added", 0)):
        note_parts.append("子盘判定成功：自己下回合出招数+1")
    if Fraction(event["opponent_reduction"]):
        note_parts.append(f"请求削减对方本回合权重{weight_label(event['opponent_reduction'])}")
    if int(event["opponent_next_debt"]):
        note_parts.append(f"请求令对方下回合出招数-{event['opponent_next_debt']}")
    if int(event["opponent_next_bonus"]):
        note_parts.append(f"本次失败令对方下回合出招数+{event['opponent_next_bonus']}")
    if (
        event.get("extra_draws")
        and not event.get("music_repeated")
        and not event.get("realtime_repeated")
        and not (event.get("music_activated") and int(event.get("extra_draws", 0)))
    ):
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
    if "miumiu-noop" in event.get("tags", ()):
        return Line(f"{event['ordinal']}. \u2800", "本招未产生数值或效果", "")
    if event.get("effects_disabled"):
        return Line(
            f"{event['ordinal']}. {event['name']}",
            f"招式效果失效 → 累计{weight_label(shown_total)}",
            "丸山大姐达妮娅-世界·发龙图：本招数值、功能、再抽、贷款与领域资格均不生效。",
        )
    effective_fighter_id = str(
        event.get("functional_fighter_id")
        if event.get("daniya_world_forced")
        else event.get("fighter_id")
        or ""
    )
    if effective_fighter_id == "juejue":
        return _juejue_event_line(
            event,
            adjustment,
            shown_total=shown_total,
            domain_bonus=domain_bonus,
        )
    numeric_base = bool(
        event.get("numeric_base", event.get("base", 0))
        or event.get("has_numeric_contribution")
    )
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
    elif opponent_reduction:
        value = f"对方-{weight_label(opponent_reduction)} → 累计{weight_label(shown_total)}"
        note = "定向减权招式；功能与伤势效果独立结算"
    else:
        if event.get("black_flash_bonus", 0):
            value = (
                f"黑闪领悟+{weight_label(event['black_flash_bonus'])}"
                f" → 累计{weight_label(shown_total)}"
            )
        elif event.get("extra_draws"):
            value = f"再抽{event['extra_draws']}次"
        else:
            value = f"功能生效 → 累计{weight_label(shown_total)}"
        note = "功能招式不加战斗强化；待用×2保留" if event["double_pending"] else "功能招式不加战斗强化"
    fighter_id = effective_fighter_id
    effect_tags = set(event.get("functional_tags", event.get("tags", ())))
    if fighter_id == "daniya":
        form = _fighter_form_name("daniya", str(event.get("form_before", DANIYA_FORM_STAGING)))
        note += f"；来源形态：{form}"
        if opponent_reduction:
            note += f"；同时令对方本回合-{weight_label(opponent_reduction)}"
        if event.get("daniya_world_forced"):
            note += "；世界·114514强制使用达妮娅招式盘"
        if "daniya-staging" in effect_tags:
            note += (
                f"；蚀域抽取加权 "
                f"{_scaled_weight(event.get('daniya_domain_steps_before', 0), DYNAMIC_DRAW_STEP_SCALE)}"
                f" → {_scaled_weight(event.get('daniya_domain_steps_after', 0), DYNAMIC_DRAW_STEP_SCALE)}"
            )
        if "daniya-disillusion" in effect_tags:
            exhaust_bonus = _scaled_weight(event.get("opponent_exhaust_bonus_units", 0), INJURY_WEIGHT_SCALE)
            note += f"；对方力竭盘永久+{exhaust_bonus}"
        if "daniya-timed-collapse" in effect_tags:
            note += "；本回合为对方追加1层计时溃灭被动"
        if "daniya-domain" in effect_tags:
            note += "；领域战胜利或单方命中后切换幻灭形态，自己下回合出招数+1"
            if event.get("daniya_domain_carried_units"):
                note += (
                    "；带入领域战加权+"
                    f"{_scaled_weight(event['daniya_domain_carried_units'], DYNAMIC_DRAW_STEP_SCALE)}"
                )
        if "daniya-flawless" in effect_tags:
            note += "；本回合自身领域战胜利权重+0.2"
        if "daniya-loan" in effect_tags:
            note += "；对方本回合领域战胜利权重-0.2"
        if "daniya-world-disable-next" in effect_tags:
            note += "；对方下回合所有招式与领域效果失效"
        if "daniya-world-force-next" in effect_tags:
            note += f"；对方下回合强制使用{form}达妮娅招式盘"
        if "daniya-world-work" in effect_tags:
            if "daniya_domain_draw_only_steps_after" in event:
                note += (
                    "；再抽1次，蚀域出现权重+1"
                    if str(event.get("form_before")) == DANIYA_FORM_STAGING
                    else "；再抽1次，对方力竭盘永久+0.5"
                )
            else:
                note += (
                    "；蚀域出现/领域战权重各+2"
                    if str(event.get("form_before")) == DANIYA_FORM_STAGING
                    else "；对方力竭盘永久+2"
                )
        if "daniya-world-dragon-image" in effect_tags:
            note += "；回合末随机令对方一招的全部胜利权重归零，功能保留"
        if "daniya-world-injury-guard" in effect_tags:
            note += "；若本回合落败，伤势结果降低一级"
        if "daniya-world-damage-immunity" in effect_tags:
            note += "；对方本回合直接减权与重装伤害无效"
    elif fighter_id == "asamu":
        if event.get("forced"):
            note += f"；奶龙覆盖：原本将抽中“{event.get('original_move_name') or '未知招式'}”"
        dynamic = (
            ("喝奶茶", "asamu_tea_bonus_before", "asamu_tea_bonus_after"),
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
            note += "；领域战胜利或单方命中后复制对方2个随机招式"
        if event.get("asamu_future_gain"):
            note += f"；睡觉成长额外+{weight_label(event['asamu_future_gain'])}"
    elif fighter_id == "yilu":
        if event.get("yilu_babel_redeploy"):
            note += "；巴别塔再部署，本干员完整效果生效2次"
        if event.get("yilu_specialist_redeploy"):
            note += "；特种再部署：本次限定抽取非医疗、非特种的其他干员"
        if event.get("yilu_marker_events"):
            note += (
                f"；当前指示物{event.get('yilu_markers', 0)}，"
                f"累计{event.get('yilu_markers_total', 0)}"
            )
        if event.get("yilu_consumed_markers"):
            note += f"；消耗指示物{event['yilu_consumed_markers']}"
        if event.get("yilu_threshold_draws"):
            note += f"；跨越9倍数里程碑，再抽{event['yilu_threshold_draws']}次"
        if event.get("yilu_true_damage_added"):
            note += f"；真伤翻倍层数+{event['yilu_true_damage_added']}"
        if event.get("yilu_sniper_shots"):
            shots = event["yilu_sniper_shots"]
            note += (
                f"；狙击连射{len(shots)}次，逐枪基础加权合计+"
                f"{sum(int(item.get('base_bonus', 0)) for item in shots)}"
            )
        if event.get("yilu_medic_recoveries"):
            recoveries = event["yilu_medic_recoveries"]
            recovered = sum(int(item.get("recovered", False)) for item in recoveries)
            note += f"；冥土追魂令重伤/力竭盘减半×{len(recoveries)}"
            if recovered:
                note += f"，并完成重伤→轻伤恢复×{recovered}"
        if event.get("yilu_specialist_draws_added"):
            note += f"；限定再部署其他干员{event['yilu_specialist_draws_added']}次"
        if "yilu-defender" in event.get("tags", ()):
            hits = sum(int(item.get("hit", False)) for item in event.get("yilu_defender_checks", ()))
            note += f"；重装70%预判命中{hits}/{len(event.get('yilu_defender_checks', ())) or 1}"
        if "yilu-babel" in event.get("tags", ()):
            note += "；下一抽限定干员且效果生效2次；下回合-1招"
        if event.get("yilu_future_gain"):
            note += f"；先锋/明日成长额外+{weight_label(event['yilu_future_gain'])}"
    elif fighter_id == "firefly":
        form_before = _fighter_form_name(
            "firefly",
            str(event.get("form_before") or FIREFLY_FORM_FIREFLY),
        )
        form_after = _fighter_form_name(
            "firefly",
            str(event.get("form_after") or FIREFLY_FORM_FIREFLY),
        )
        note += f"；形态：{form_before} → {form_after}"
        note += (
            f"；燃芯 {event.get('firefly_fuel_before', 0)} → "
            f"{event.get('firefly_fuel_after', 0)}"
        )
        if event.get("firefly_echo"):
            note += "；本招按残梦回声结算"
            if Fraction(event.get("firefly_echo_scale", 1)) != 1:
                note += "（本次效果50%）"
        if event.get("firefly_collapse_passive_gain"):
            note += (
                f"；溃败联动额外+{weight_label(event['firefly_collapse_passive_gain'])}"
            )
        if event.get("firefly_collapse_to_add"):
            note += (
                f"；对手溃败+{weight_label(event['firefly_collapse_to_add'])}"
                f"（待结算至{weight_label(event.get('firefly_target_collapse_after_pending', 0))}/3）"
            )
        if event.get("firefly_starfield"):
            note += "；对手本回合正向增益减半，本回合力竭权重-0.25"
        if event.get("firefly_opponent_exhaust_delta_units"):
            delta = _scaled_weight(event["firefly_opponent_exhaust_delta_units"], INJURY_WEIGHT_SCALE)
            note += f"；对手本回合力竭权重+{delta}"
        if event.get("firefly_domain_followup"):
            note += "；结算后延长已有萨姆形态，下回合自己+1招、对手溃败+1"
        if event.get("firefly_next_sam_bonus_used"):
            note += f"；赤染之茧储备+{event['firefly_next_sam_bonus_used']}已消耗"
        if event.get("firefly_first_sam_reduction"):
            note += f"；萨姆本回合第一招令对手额外-{event['firefly_first_sam_reduction']}"
        if event.get("firefly_collapse_debt_triggered"):
            note += "；命中前已有2层溃败，对手下回合-1招"
        if event.get("firefly_conditional_reduction_applied"):
            note += f"；对手本回合已有增益，额外-{event['firefly_conditional_reduction_applied']}"
        if event.get("firefly_choice"):
            choice = event["firefly_choice"]
            options = " / ".join(str(item.get("name")) for item in choice.get("options", ()))
            note += f"；候选：{options}；自动选定“{choice.get('selected_name', '未知')}”"
        if event.get("firefly_forced_choice"):
            note += f"；承接第{event.get('firefly_choice_source_ordinal')}招的确定选择"
        if event.get("firefly_domain_choice") == "extend-sam":
            note += "；领域结算选择延长萨姆形态1回合"
        elif event.get("firefly_domain_choice") == "return-firefly":
            note += "；领域结算选择流萤形态，并使下回合+1招"
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
        note += "；" + "、".join(adjustment["reasons"])
        note += "；本招全部胜率数值归零，功能保留" if deducted == original else "；部分数值调整，功能保留"
    if domain_bonus:
        reason = str(domain_bonus.get("reason") or "领域战获胜")
        note += f"；{reason}，本招额外+{weight_label(domain_bonus['gain'])}（本回合仅一次）"
    mirror = event.get("mirror", {})
    if mirror.get("kind") == "miumiu":
        note += f"；润化{mirror['humidity_before']}→{mirror['humidity_after']}"
        if "mimic_reference" in mirror:
            note += f"；共同预演参考数值{weight_label(mirror['mimic_reference'])}（只取数值）"
        if mirror.get("next_rebuild_bonus"):
            note += f"；下回合每次数值招式+{mirror['next_rebuild_bonus']}"
        if mirror.get("disturb_next"):
            note += "；对方下回合正向数值变化-20%"
        if mirror.get("blank_consumed"):
            note += f"；已消耗{mirror['blank_consumed']}层润化，领域命中后结算"
    if mirror.get("kind") == "luoli":
        note += f"；黄瓜+{mirror['cucumbers_generated']}，给对方账单+{mirror['bills_generated']}"
        if mirror.get("allergy"):
            note += "；心音与猫毛过敏本回合已触发"
        if mirror.get("injury_before") != mirror.get("injury_after"):
            labels = {"heavy": "重伤", "light": "轻伤", "none": "无伤"}
            note += f"；伤势{labels[mirror['injury_before']]}→{labels[mirror['injury_after']]}"
    if mirror.get("rebuild_bonus"):
        note += f"；上回合重构额外+{mirror['rebuild_bonus']}"
    if event.get("daniya_native"):
        note += (
            f"；虚质粒子{weight_label(event['daniya_particle_before'])}"
            f"→{weight_label(event['daniya_particle_after'])}"
        )
        if event.get("daniya_black_hole_factor") is not None:
            note += f"；黑洞自身加权与敌减权×{weight_label(event['daniya_black_hole_factor'])}"
        if event.get("daniya_transition"):
            transition = event["daniya_transition"]
            note += f"；{DANIYA_FORM_NAMES[transition['before']]}→黑洞，伤势清除"
    xixi = event.get("xixi", {})
    if xixi.get("native"):
        note += f"；法术涌动标记{xixi['mark_before']}→{xixi['mark_after']}"
        if xixi.get("automatic"):
            note += "；符文禁锢自动施放，不占手抽次数"
        if xixi.get("overload_weight_cleared"):
            note += "；超负荷额外出现权重已清空"
        if xixi.get("cooldown_until") is not None:
            note += f"；第{xixi['cooldown_until'] + 1}回合可再次抽到"
        if event.get("xixi_all_moves_bonus"):
            note += f"；奥数专精与连续涌动：本轮每招额外+{weight_label(event['xixi_all_moves_bonus'])}"
        if xixi.get("stop"):
            note += f"；沙漏已停止后续{event.get('xixi_stopped_pending', 0)}次手抽，本轮免抽伤势"
        if xixi.get("delayed_hextech_round") is not None:
            note += f"；回归基本功：第{xixi['delayed_hextech_round']}回合抽海克斯，本招不进入领域战"
    if event.get("xixi_gain_factor") is not None and Fraction(event["xixi_gain_factor"]) != 1:
        note += f"；现实器/沙漏整轮自身加权×{weight_label(event['xixi_gain_factor'])}"
    if event.get("xixi_reduction_factor") is not None and Fraction(event["xixi_reduction_factor"]) != 1:
        note += f"；整轮敌减权×{weight_label(event['xixi_reduction_factor'])}"
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
    if event.get("generated_by") == "xixi-prison-auto-overload":
        return wheel_card(
            "move", f"第{event['ordinal']}招 · 自动超负荷", ((event["name"], 1),), None,
            "符文禁锢消费标记后自动施放；本次没有抽取招式盘，也不消耗手抽次数。",
        )
    source_fighter_id = str(
        event.get("functional_fighter_id")
        if event.get("daniya_world_forced")
        else event.get("fighter_id")
        if generated_mimic
        else event.get("source_fighter_id") or event.get("fighter_id") or ""
    )
    if source_fighter_id not in FIGHTERS_BY_ID:
        raise ValueError(f"未知招式盘来源：{source_fighter_id}")

    definition = FIGHTERS_BY_ID[source_fighter_id]
    moves_by_id = {move.move_id: move for move in fighter_moves(source_fighter_id, definition_version)}
    wheel_move_ids = tuple(str(move_id) for move_id in event.get("draw_wheel_move_ids") or ())
    from ..domain.battle_catalog import Move
    for move_id in wheel_move_ids:
        if move_id.startswith("miumiu-noop-"):
            moves_by_id[move_id] = Move(move_id, "\u2800")
    exact_moves = tuple(moves_by_id.get(move_id) for move_id in wheel_move_ids)
    if wheel_move_ids and all(move is not None for move in exact_moves):
        moves = exact_moves
    elif source_fighter_id in {"juejue", "daniya", "firefly", "xixi"} and not generated_copy and not generated_mimic:
        moves = fighter_form_moves(
            source_fighter_id,
            str(event.get("form_before") or ""),
            definition_version,
        )
    else:
        moves = fighter_moves(source_fighter_id, definition_version)

    units = event.get("draw_wheel_units")
    scale = int(event.get("draw_weight_scale") or MOVE_WEIGHT_SCALE)
    options = (
        tuple((move.name, _scaled_weight(units[index], scale)) for index, move in enumerate(moves))
        if units and len(units) == len(moves)
        else tuple((move.name, _move_weight(move)) for move in moves)
    )
    # 冷却装备在冻结盘中的权重为0；不绘制不可抽取的扇区，保留其他实际比例。
    zero_weight_labels = tuple(label for label, weight in options if weight == 0)
    options = tuple((label, weight) for label, weight in options if weight > 0)

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
    if zero_weight_labels:
        note += "本次不可抽取：" + "、".join(zero_weight_labels) + "。"
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
        if str(event.get("functional_fighter_id") or "juejue") != "juejue":
            # Battle v13/v14 的114514会让撅撅猪临时使用达妮娅形态盘；
            # 这类历史事件不属于撅撅猪自己的切换轨迹。
            continue
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
    current = _fighter_form_name(
        "daniya",
        str(side.get("daniya_form") or DANIYA_FORM_STAGING),
    )
    turn = side.get("turn", {})
    track: list[str] = []
    for event in turn.get("events", ()):
        before_id = str(event.get("form_before") or side.get("daniya_form") or DANIYA_FORM_STAGING)
        before = _fighter_form_name("daniya", before_id)
        after = _fighter_form_name("daniya", str(event.get("form_after") or before_id))
        if not track:
            track.append(before)
        if after != track[-1]:
            track.append(after)
    if not track:
        track.append(current)
    elif current != track[-1]:
        track.append(current)
    if "daniya_particles" in side:
        facts = [
            f"虚质粒子{weight_label(side['daniya_particles'])}",
            f"蚀域命中{side.get('daniya_domain_hits', 0)}/7",
            f"永久敌胜权减少{weight_label(side.get('daniya_permanent_reduction', 0))}",
            f"永久领域战胜权+{_scaled_weight(side.get('daniya_permanent_domain_units', 0), 10)}",
        ]
        if side.get("daniya_form") == BLACK_HOLE:
            facts += ["固定伤势82.3%无伤 / 12.49%逐层受伤 / 5.21%力竭", "计时溃灭被动：敌力竭权重+回合数×5"]
        else:
            key = (
                "daniya_transform_staging_units" if side.get("daniya_form") == DANIYA_FORM_STAGING
                else "daniya_transform_disillusion_units"
            )
            facts.append(f"黑洞转化招出现权重额外+{_scaled_weight(side.get(key, 0), MOVE_WEIGHT_SCALE)}")
        return f"当前形态 · {current}", " → ".join(track), " · ".join(facts)
    domain_draw_units = int(side.get("daniya_domain_steps", 0)) + int(
        side.get("daniya_domain_draw_only_steps", 0)
    )
    facts = [
        f"蚀域抽取加权+{_scaled_weight(domain_draw_units, DYNAMIC_DRAW_STEP_SCALE)}",
        f"自身力竭盘被永久加权+"
        f"{_scaled_weight(side.get('injury_exhaust_bonus_units', 0), INJURY_WEIGHT_SCALE)}",
    ]
    if turn.get("daniya_collapse_count"):
        facts.append(f"本回合计时溃灭主动层×{turn['daniya_collapse_count']}")
    if turn.get("daniya_world_damage_immunity"):
        facts.append("旧规则NMSL伤害免疫")
    if turn.get("daniya_injury_guard"):
        facts.append("NMSL伤势降级已待命")
    dragon_count = len(turn.get("daniya_dragon_image_ordinals", ()))
    if dragon_count:
        facts.append(f"发龙图随机失效待结算×{dragon_count}")
    if turn.get("daniya_world_effects_disabled"):
        facts.append("本回合全部招式效果失效")
    if turn.get("daniya_world_forced_move_ids"):
        forced_form = _fighter_form_name(
            "daniya",
            str(turn.get("daniya_world_forced_form") or DANIYA_FORM_STAGING),
        )
        facts.append(f"本回合强制使用{forced_form}达妮娅招式盘")
    return f"当前形态 · {current}", " → ".join(track), " · ".join(facts)


def _xixi_state_projection(side: dict, *, round_number: int | None = None) -> tuple[str, str, str]:
    form = str(side.get("xixi_form") or XIXI_FORM_CELESTIAL)
    acquired = tuple(side.get("xixi_hextech", ()))
    facts = [
        f"法术涌动标记{side.get('xixi_mark', 0)}/1",
        f"超负荷永久胜权+{weight_label(side.get('xixi_overload_bonus', 0))}",
        f"超负荷出现权重额外+{_scaled_weight(side.get('xixi_overload_weight_units', 0), MOVE_WEIGHT_SCALE)}",
        f"领悟：{XIXI_CORE_NAME}",
    ]
    if side.get("xixi_shield"):
        facts.append("由心及物：下次败北免抽伤势")
    if "goliath" in acquired:
        facts.append(f"歌莉娅巨人：敌有效正收益{weight_label(side.get('xixi_goliath_gain', 0))}/100")
    if side.get("xixi_permanent_action_bonus"):
        facts.append("回归基本功：永久+1招、曲径折跃仅成长、简易领域40%")
    for queued in side.get("xixi_hextech_queue", ()):
        facts.append(f"第{queued['due']}回合待领海克斯")
    round_number = int(side.get("turn", {}).get("round", 0)) if round_number is None else round_number
    for move_id, until in side.get("xixi_cooldowns", {}).items():
        if until >= round_number:
            facts.append(f"{'现实器' if move_id == 'xixi-reality' else '中亚沙漏'}冷却至第{until}回合末")
    if form == XIXI_FORM_EMPEROR:
        facts.append("全部招式+21亿；固定99%无伤 / 1%力竭；装备退出抽取盘")
    names = "、".join(HEXTECH_NAMES[key] for key in acquired) or "尚未获得"
    return _fighter_form_name("xixi", form), f"海克斯{len(acquired)}/4 · {names}", " · ".join(facts)


def _asamu_state_projection(side: dict) -> tuple[str, str, str]:
    turn = side.get("turn", {})
    injury = {"none": "无伤", "light": "轻伤", "heavy": "重伤"}.get(
        str(side.get("injury_state", "none")), str(side.get("injury_state", "none"))
    )
    tea = MOVE_WEIGHT_SCALE + int(side.get("asamu_tea_bonus_units", 0))
    prime = (
        MOVE_WEIGHT_SCALE // 5
        + int(side.get("asamu_prime_bonus_units", 0))
        + int(side.get("asamu_prime_temp_bonus_units", 0))
    )
    facts = [
        f"睡觉成长：后续每招+{side.get('asamu_future_gain_bonus', 0)}",
        f"喝奶茶/全盛姿态权重 {tea / MOVE_WEIGHT_SCALE:g}/{prime / MOVE_WEIGHT_SCALE:g}",
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


def _yilu_state_projection(side: dict) -> tuple[str, str, str]:
    turn = side.get("turn", {})
    facts = [
        f"持有指示物{side.get('yilu_markers', 0)}",
        f"累计指示物{side.get('yilu_markers_total', 0)}",
        f"基础加权：永久+{side.get('yilu_future_base_bonus', 0)} / 本回合+{turn.get('yilu_round_base_bonus', 0)}",
        f"本回合干员{turn.get('yilu_operator_placements', 0)}/10",
    ]
    if turn.get("yilu_double_operator_draws"):
        facts.append(f"待再部署干员×{turn['yilu_double_operator_draws']}")
    if turn.get("yilu_specialist_operator_draws"):
        facts.append(f"待特种限定再部署×{turn['yilu_specialist_operator_draws']}")
    if turn.get("yilu_true_damage_layers"):
        facts.append(f"本回合真伤翻倍×{turn['yilu_true_damage_layers']}")
    if turn.get("yilu_injury_recovery_layers"):
        facts.append(f"冥土追魂×{turn['yilu_injury_recovery_layers']}")
    if turn.get("yilu_injury_worsen_layers"):
        facts.append(f"旧版特种伤势加重×{turn['yilu_injury_worsen_layers']}")
    return "罗德岛干员编队", "指示物持续整场", " · ".join(facts)


def _firefly_state_projection(side: dict) -> tuple[str, str, str]:
    form_id = str(side.get("firefly_form") or FIREFLY_FORM_FIREFLY)
    current = _fighter_form_name("firefly", form_id)
    turn = side.get("turn", {})
    track: list[str] = []
    for event in turn.get("events", ()):
        if str(event.get("functional_fighter_id") or "firefly") != "firefly":
            # 同上：历史114514事件应展示招式来源，但不能污染流萤/萨姆形态。
            continue
        before = _fighter_form_name(
            "firefly",
            str(event.get("form_before") or form_id),
        )
        after = _fighter_form_name(
            "firefly",
            str(event.get("form_after") or form_id),
        )
        if not track:
            track.append(before)
        if after != track[-1]:
            track.append(after)
    if not track:
        track.append(current)
    elif current != track[-1]:
        track.append(current)
    facts = [
        f"燃芯{side.get('firefly_fuel', 0)}/3",
        f"溃败{weight_label(side.get('firefly_collapse', 0))}/3",
    ]
    if form_id == FIREFLY_FORM_SAM:
        facts.append(f"萨姆剩余{side.get('firefly_sam_rounds_remaining', 0)}回合")
    if side.get("firefly_next_sam_gain_bonus"):
        facts.append(f"下一次萨姆技能+{side['firefly_next_sam_gain_bonus']}")
    sam_draw = int(turn.get("firefly_sam_draw_bonus_units", 0))
    if sam_draw:
        facts.append(f"本回合萨姆招式出现权重+{sam_draw / MOVE_WEIGHT_SCALE:g}")
    if turn.get("firefly_forced_choices"):
        facts.append(f"飞萤之火待选择招式×{len(turn['firefly_forced_choices'])}")
    return f"当前形态 · {current}", " → ".join(track), " · ".join(facts)


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
    if interactions.get("mirror"):
        panels.append(Panel("润化与黄瓜账单结算", tuple(
            Line(names[int(fact["side"])], fact["text"]) for fact in interactions["mirror"]
        )))
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
    for fact in interactions.get("daniya_dragon_images", ()):
        target = names[int(fact["target_side"])]
        if fact.get("selected_ordinal") is None:
            value, note = "没有有效目标", f"{target}本回合没有仍有效的正数招式"
        else:
            value = f"令{target}第{fact['selected_ordinal']}招胜率归零"
            note = (
                f"扣除胜利权重{weight_label(fact.get('cancelled_gain', 0))}；"
                "该招的再抽、贷款、领域与状态效果均保留。"
            )
        mechanism_lines.append(
            Line(
                names[int(fact["side"])] + f" · 世界·发龙图（第{fact['source_ordinal']}招）",
                value,
                note,
            )
        )
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
            reason = fact.get("round_reduction_suppression_reason") or "时空保护"
            facts.append(f"本轮减权被{reason}免疫")
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
        if fact.get("next_effects_disabled"):
            facts.append("下回合所有招式与领域效果失效")
        if fact.get("next_forced_move_ids"):
            forced_form = _fighter_form_name(
                "daniya",
                str(fact.get("next_forced_form") or DANIYA_FORM_STAGING),
            )
            facts.append(f"下回合全部改抽{forced_form}达妮娅招式盘")
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
    for fact in interactions.get("yilu_defender_results", ()):
        if fact.get("suppressed_by_daniya_nmsl"):
            value, note = "重装伤害被NMSL免疫", "不归零招式数值，也不追加-5胜率。"
        elif fact.get("selected_ordinal") is not None:
            value = f"{names[int(fact['target_side'])]}第{fact['selected_ordinal']}招数值失效"
            note = (
                f"70%判定命中；归零{weight_label(fact.get('cancelled_gain', 0))}，"
                f"并申请对方-{weight_label(fact.get('opponent_reduction', 0))}。"
            )
        elif fact.get("hit"):
            value, note = "预判命中但无可失效数值招式", "不追加-5胜率。"
        else:
            value, note = "70%判定未命中", "本次重装只保留自身基础效果。"
        v5_lines.append(Line(names[int(fact["side"])] + " · 干员放置·重装", value, note))
    for event in interactions.get("asamu_domain_copies", ()):
        value = f"复制{names[int(event['source_side'])]}的“{event['source_move_name']}”"
        note = f"第{event['copy_slot']}份复制；数值{_signed_weight(event.get('gain', 0))}"
        if event.get("domain_reentry_suppressed"):
            note += "；复制到领域招式时不再次触发领域判定"
        v5_lines.append(Line("领域·呃呃阿萨姆奶茶", value, note))
    retaliation_before = interactions.get("retaliation_snapshot", ())
    retaliation_after = interactions.get("retaliation_after_snapshot", ())
    for fact in interactions.get("retaliations", ()):
        value = "交换双方权重后再加成" if fact.get("swapped") else "优势状态直接加成"
        if len(retaliation_before) == 2 and len(retaliation_after) == 2:
            note = (
                f"连续结算{fact.get('count', 1)}次；"
                f"{weight_label(retaliation_before[0])}/{weight_label(retaliation_before[1])} → "
                f"{weight_label(retaliation_after[0])}/{weight_label(retaliation_after[1])}"
            )
        else:
            # 兼容已经落库、仅保存单方数值的早期 v7 回合事实。
            note = (
                f"连续结算{fact.get('count', 1)}次；"
                f"本方{weight_label(fact.get('before', 0))} → "
                f"{weight_label(fact.get('after', 0))}"
            )
        v5_lines.append(Line(names[int(fact["side"])] + " · 以牙还牙", value, note))
    for fact in interactions.get("yilu_true_damage", ()):
        v5_lines.append(
            Line(
                names[int(fact["side"])] + " · 近卫真伤",
                f"{weight_label(fact['before'])} → {weight_label(fact['after'])}",
                f"本回合胜率连续翻倍{fact['layers']}次。",
            )
        )
    for fact in interactions.get("firefly_collapse_updates", ()):
        v5_lines.append(
            Line(
                names[int(fact["source_side"])] + " · 溃败烙印",
                f"{names[int(fact['target_side'])]}：{weight_label(fact['before'])} → {weight_label(fact['after'])}/3",
                f"本回合命中累计{weight_label(fact['added'])}层；超过3层的部分不会继续增加。",
            )
        )
    for fact in interactions.get("firefly_domain_continuations", ()):
        v5_lines.append(Line(names[int(fact["side"])] + " · 星海余焰",
                             f"下回合自己+{fact['count']}招、对手溃败+{fact['count']}",
                             f"萨姆形态延长{fact['count']}回合" if fact["extended_sam"] else "保持流萤形态"))
    if v5_lines:
        panels.append(
            Panel(
                "战斗猪 · 回合机制",
                tuple(v5_lines),
                "失效统一只将一招的全部胜率数值归零；抽数、状态、领域及其他功能事实全部保留。",
            )
        )
    return tuple(panels)


def _v19_interaction_projection(
    interactions: dict, names: list[str],
) -> tuple[tuple[Panel, ...], tuple[BattleWheelCard, ...]]:
    lines, hex_wheels = [], []
    for fact in interactions.get("daniya_v19", ()):
        name = names[int(fact["side"])]
        kind = fact.get("kind")
        if kind == "permanent-reduction":
            lines.append(Line(
                name + " · 虚质粒子", f"敌胜权实际-{weight_label(fact['applied'])}",
                f"本轮累计永久减权{weight_label(fact['value'])}；消耗粒子后仍保留。",
            ))
        elif kind in {"current-move-cancel", "domain-ignore"} or "target" in fact and "deduction" in fact:
            lines.append(Line(
                name + " · 无视", f"对方第{fact['ordinal']}招胜权-{weight_label(fact['deduction'])}",
                "已发生的原生成长保留；被取消的领域不参加领域判定。"
                if kind == "current-move-cancel" else "依据本轮冻结招式选取。",
            ))
        elif kind in {"domain-double", "domain-self-double"}:
            lines.append(Line(
                name + " · 布景蚀域", f"第{fact['ordinal']}招额外+{weight_label(fact['gain'])}",
                "通用领域倍率后，本招独立额外×2。" if kind == "domain-self-double" else "随机选中的本轮招式再×2。",
            ))
        elif kind == "black-hole":
            lines.append(Line(
                name + " · 黑洞之形", "进入独立黑洞盘，清除已有伤势", "伤势盘固定82.3% / 12.49% / 5.21%。",
            ))
        elif kind == "force-defeat":
            lines.append(Line(
                name + " · 深黯 终末 恒常", names[int(fact["target"])] + "本轮强制落败",
                "伤势仍遵循对方固定盘或免抽保护。",
            ))
    for fact in interactions.get("xixi", ()):
        name = names[int(fact["side"])]
        if fact.get("ignore"):
            lines.append(Line(
                name + " · 符文无视", f"敌方第{fact['target_ordinal']}招胜权-{weight_label(fact['cancelled_gain'])}",
            ))
        if fact.get("overload_next_action_bonus"):
            lines.append(Line(name + " · 超负荷连击", "下回合出招数+1"))
        giant = fact.get("goliath")
        if giant:
            lines.append(Line(
                name + " · 歌莉娅巨人",
                f"敌有效正收益{weight_label(giant['before'])}→{weight_label(giant['after'])}/100",
                "免抽伤势保护仍有效。" if giant["guard_active"] else "已达到100，后续败北恢复抽取伤势盘。",
            ))
        grant = (
            fact.get("core_hextech") or fact.get("domain_hit", {}).get("hextech")
            or fact.get("delayed_hextech", {}).get("result")
        )
        if grant:
            if fact.get("delayed_hextech"):
                lines.append(Line(name + " · 延迟海克斯", f"第{fact['delayed_hextech']['due']}回合兑现",
                                  "回归基本功在3回合后的排队奖励；读取保存的落点，不重新抽取。"))
            if grant.get("available"):
                lines.append(Line(
                    name + " · 海克斯", grant["name"], f"已获得{grant['count']}/4种；重复符文不再进入候选池。",
                ))
                hex_wheels.append(wheel_card("move", name + " · 海克斯落点",
                                             tuple((HEXTECH_NAMES[key], weight) for key, weight in grant["wheel"]),
                                             grant["name"], "保存的海克斯抽取结果。"))
                if grant.get("emperor"):
                    lines.append(Line(
                        name + " · 西西天帝", "全招+21亿，恢复全部伤势", "固定99%无伤 / 1%力竭，现实器与沙漏不再入盘。",
                    ))
            elif grant.get("reason") == "empty":
                lines.append(Line(name + " · 海克斯", "四种已集齐，候选池为空"))
    panels = (Panel("粒子与符文 · 本轮结算", tuple(lines)),) if lines else ()
    return panels, tuple(hex_wheels)


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
                "reason": str(
                    domain.get("boost_reason")
                    or ("领域命中" if domain.get("mode") == "solo" else "领域战获胜")
                ),
            }
    for index, count_side in enumerate(count_sides):
        turn = count_side["turn"]
        turn.setdefault("ready", False)
        if turn["raw"] is not None:
            options = turn.get("count_wheel") or (HEAVY_COUNT_WHEEL if count_side["heavy"] else COUNT_WHEEL)
            count_cards[index] = wheel_card(
                "count",
                "本回合出招数落点",
                tuple((f"{number}招", weight) for number, weight in options),
                f"{turn['raw']}招",
                f"原始{turn['raw']}招 - 贷款{weight_label(turn['debt'])}招 = 实际{turn['effective']}招。",
            )
    if round_result:
        events = [
            event
            for side in round_result["after"]
            for event in side["turn"].get("events", ())
        ]
    if round_result and round_result.get("miumiu_reconstructions"):
        events = [event for player in round_result["after"] for event in player["turn"].get("events", ())]
        panels.append(Panel("\u2800模式 · 回合重构", (
            Line("水面重映", "双方胜利权重清零", "本回合旧招式效果已撤销，以下展示重抽后的完整出招。"),
        )))
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
        if definition_version >= 19 and turn.get("xixi_delayed_hextech"):
            delayed_panels, delayed_wheels = _v19_interaction_projection(
                {"xixi": tuple({"side": index, "delayed_hextech": fact}
                               for fact in turn["xixi_delayed_hextech"])},
                [player["snapshot"]["player_name"] for player in display_sides],
            )
            panels.extend(delayed_panels)
            wheel_cards.extend(delayed_wheels)
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
                base = 0 if Fraction(carry["round_start_weight"]) == 0 else 5
                inherited = Fraction(carry["round_start_weight"]) - base
                weight_breakdown = (
                    f"基础{base} + 历史折半继承{weight_label(inherited)} + "
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
                base = 0 if start == 0 else 5
                inherited = start - base
                current = Fraction(side["weight"]) - start
                weight_breakdown = (
                    f"基础{base} + 历史折半继承{weight_label(inherited)} + 本回合净增{weight_label(current)}"
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
        elif snap.get("fighter_id") == "xixi":
            form, form_track, mechanic_summary = _xixi_state_projection(
                side, round_number=round_result["round"] if round_result else state["round"],
            )
        elif snap.get("fighter_id") == "asamu":
            form, form_track, mechanic_summary = _asamu_state_projection(side)
        elif snap.get("fighter_id") == "yilu":
            form, form_track, mechanic_summary = _yilu_state_projection(side)
        elif snap.get("fighter_id") == "firefly":
            form, form_track, mechanic_summary = _firefly_state_projection(side)
        elif snap.get("fighter_id") == "luoli":
            form = "黄瓜与账单"
            form_track = f"黄瓜{side.get('luoli_cucumbers', 0)} · 账单{side.get('luoli_bills', 0)}"
            mechanic_summary = " · ".join(filter(None, (
                "心音：本回合黄瓜增益翻倍，回合末保留黄瓜" if turn.get("luoli_heart") else "",
                "猫毛过敏：账单减益翻倍，回合末保留账单" if turn.get("luoli_allergy") else "",
                "睡觉增益：本回合获取与生成数量×2" if turn.get("luoli_sleep_bonus") else "",
                "通常每回合结束清除最多5根黄瓜、5张账单",
            )))
        elif snap.get("fighter_id") == "miumiu" and state["version"] >= 17:
            form = "流形与润化"
            form_track = f"润化{side.get('miumiu_humidity', 0)}层 · 本场持续保留"
            mechanic_summary = "观测与拟态只复制数值；润化用于恢复、重构与领域爆发。"
            if turn.get("miumiu_rebuild_bonus"):
                mechanic_summary += f"本回合每次数值招式+{turn['miumiu_rebuild_bonus']}。"
        elif snap.get("fighter_id") == "miumiu":
            form = "\u2800模式" if side.get("miumiu_mode") else "流形"
            form_track = "记录与适应"
            mechanic_summary = (
                "正在使用映照的招式盘；"
                + ("力竭保护尚未使用" if side.get("miumiu_exhaust_guard") else "力竭保护已用尽")
                if side.get("miumiu_mode") else "以双方共同基础预演为比较依据，不受出招先后影响。"
            )
        world_notes = []
        if turn.get("daniya_world_effects_disabled"):
            world_notes.append("发龙图：本回合全部招式与领域效果失效")
        if turn.get("daniya_world_forced_move_ids"):
            forced_form = _fighter_form_name(
                "daniya",
                str(turn.get("daniya_world_forced_form") or DANIYA_FORM_STAGING),
            )
            world_notes.append(f"114514：本回合强制改抽{forced_form}达妮娅招式盘")
        if world_notes:
            mechanic_summary = " · ".join(
                part for part in (mechanic_summary, *world_notes) if part
            )
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
                risk=(
                    "固定伤势盘"
                    if side.get("daniya_form") == BLACK_HOLE or side.get("xixi_form") == XIXI_FORM_EMPEROR
                    else ("初始风险", "轻伤风险", "重伤风险")[side["risk"]]
                ),
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
        loser_fighter = display_sides[round_result["loser"]]["snapshot"]["fighter_id"]
        fixed_injury = bool(round_result.get("injury_modifiers", {}).get("fixed"))
        injury_scale = (
            sum(Fraction(weight) for _key, weight in round_result["injury_wheel"]) / 100
            if fixed_injury else INJURY_WEIGHT_SCALE
        )
        def injury_name(key):
            return XIXI_CORE_NAME if key == "core" and loser_fighter == "xixi" else INJURY_NAMES[key]
        if not round_result.get("injury_skip_reason"):
            wheel_cards.append(
                wheel_card(
                    "injury", loser + " · 伤势盘落点",
                    tuple((injury_name(key), _scaled_weight(weight, injury_scale))
                          for key, weight in round_result["injury_wheel"]),
                    injury_name(round_result["injury"]),
                    "固定概率，不受其他伤势效果影响。" if fixed_injury
                    else "扇区按本轮抽取时的风险权重绘制；标记是已经保存的结果。",
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
                solo_hit_boost = domain.get("mode") == "solo"
                domain_lines.append(
                    Line(
                        "领域命中加倍" if solo_hit_boost else "领域胜方加倍",
                        f"{names[int(domain['boost_side'])]} {target}合计额外 "
                        f"+{weight_label(domain['bonus_gain'])}",
                        (
                            "任意战斗猪单方领域命中后，只把一份仍有效领域胜率翻倍。"
                            if solo_hit_boost
                            else "普通领域只翻倍一份；撅撅猪两个不同领域同回合齐出且胜出时，两份相加后一起翻倍。"
                        ),
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
        if definition_version >= 19:
            new_panels, hex_wheels = _v19_interaction_projection(interactions, names)
            panels.extend(new_panels)
            wheel_cards.extend(hex_wheels)
        modifiers = round_result.get("injury_modifiers")
        if modifiers and not fixed_injury:
            firefly_delta = Fraction(modifiers.get("firefly_current_delta_units", 0))
            lines = [
                Line(
                    "力竭权重倍率",
                    f"×{modifiers.get('total_exhaust_multiplier', 1)}",
                    f"厄运传递×{modifiers.get('misfortune_count', 0)}；"
                    f"计时溃灭常驻{modifiers.get('daniya_passive_layers', 0)}层 / "
                    f"主动{modifiers.get('daniya_active_layers', 0)}层；"
                    f"每层×{modifiers.get('daniya_layer_multiplier', 1)}",
                ),
                Line(
                    "永久力竭加权",
                    f"+{_scaled_weight(modifiers.get('permanent_exhaust_bonus_units', 0), INJURY_WEIGHT_SCALE)}",
                    "达妮娅幻灭招式累积，直接加入本次伤势盘。",
                ),
                Line(
                    "溃败 / 本回合修正",
                    f"+{_scaled_weight(modifiers.get('firefly_collapse_bonus_units', 0), INJURY_WEIGHT_SCALE)} / "
                    f"{_signed_weight(firefly_delta / INJURY_WEIGHT_SCALE)}",
                    "溃败每层令力竭权重+0.1；流萤技能与焦土陨击的即时修正只作用于本回合。",
                ),
            ]
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
        injury_label = injury_name(round_result["injury"])
        if round_result["injury"] == "injured":
            injury_label += " → " + injury_name(round_result["injury_effective"])
        if round_result.get("daniya_injury_guarded"):
            effective = str(round_result.get("injury_after_daniya_guard", round_result["injury"]))
            effective_label = "无伤" if effective == "none" else INJURY_NAMES[effective]
            injury_label += f"（NMSL降为{effective_label}）"
        if round_result.get("injury_rewound"):
            injury_label += "（本轮已回溯）"
        injury_lines = [
            Line(
                loser + (" · 免抽伤势" if round_result.get("injury_skip_reason") else "的伤势盘"),
                str(round_result["injury_skip_reason"]) if round_result.get("injury_skip_reason") else injury_label,
                "本轮未进行伤势抽取，不会受伤、力竭或领悟核心。"
                if round_result.get("injury_skip_reason") else ("固定概率：" if fixed_injury else "抽取权重：")
                + " / ".join(
                    f"{injury_name(k)} {_scaled_weight(v, injury_scale)}{'%' if fixed_injury else ''}"
                    for k, v in round_result["injury_wheel"]
                ),
            )
        ]
        if round_result.get("forced_injury_suppressed"):
            injury_lines.append(Line(
                "固定伤势盘保护", "本轮仍按固定概率抽取", "强制落败已生效，强制力竭未覆盖固定伤势盘。",
            ))
        guard = round_result.get("daniya_particle_guard")
        if guard:
            injury_lines.append(Line(
                "虚质粒子抵伤", f"降低{guard['layers']}级，剩余{weight_label(guard['particles_remaining'])}粒子",
                "本轮抵伤事实已保存，消耗后永久敌减权仍保留。",
            ))
        if round_result.get("miumiu_exhaust_guarded"):
            injury_lines.append(Line(loser + " · 水镜庇护", "免疫本次力竭", "保护已消耗，原有伤势风险不变。"))
        if round_result.get("injury_rewound"):
            injury_lines.append(
                Line(
                    loser + " · 时之沙·回溯",
                    "撤销本轮新伤势",
                    "只撤销本轮新抽到的轻伤或重伤；没有清除历史风险，也不能挽救力竭。",
                )
            )
        if round_result.get("daniya_injury_guarded"):
            injury_lines.append(
                Line(
                    loser + " · 世界·NMSL",
                    "伤势结果降低一级",
                    "轻伤化解、重伤降为轻伤、力竭倒下降为重伤；掌握核心保持原结果。",
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
        "sand-accelerate": "进入加速盘；成功后增加胜利权重、按档位追加抽取，并令自己下回合+1招",
        "sand-delay": "进入时延盘；成功后压低对方本回合权重、令自己下回合+1招，并影响对方下回合出招数",
        "sand-body": "对方本回合第一个仍有效的数值招式胜利权重减半（向下取整；同回合不叠）",
        "sand-seal": "纯数值招式",
        "switch-virtual": "即时切换至虚拟声，并从虚拟声轮盘再抽2次",
        "sand-domain": "主盘抽取权重1、单领域战权重2.5；单方领域命中或领域战获胜后，对方下回合-1招、自己下回合+1招",
        "virtual-realm": "再抽1次；下一次加速或时延判定必定成功",
        "future-simulation": "每次抽中都独立随机令对方本回合一个带胜利权重的招式无效",
        "realtime-compute": "首次再抽1次并令两种领域抽取权重各+1；重复抽中改为胜利权重+10并再抽2次",
        "virtual-mimic": (
            "大/小轮盘各50%；复制其他战斗猪可模仿招式的数值、一般功能与定向效果；"
            "复制领域不重开领域战，复制招式的追加抽数不递归"
        ),
        "make-real": "下次再次使用时额外+5，逐次累加",
        "louder": "首次进入本回合音乐状态，之后每招固定+5并再抽1次；重复抽中不叠层，改为再抽2次",
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
            f"成功+{tier.gain + level}、再抽{tier.extra_draws}次，并令自己下回合+1招",
            "失败无额外惩罚"
            if not tier.failure_debt
            else f"失败后自己下回合出招数-{tier.failure_debt}",
        )
        for tier in JUEJUE_ACCELERATION_TIERS
    )
    delay_lines = tuple(
        Line(
            f"{tier.tier}档 · 基础成功率{tier.success_chance}%",
            f"成功自身+{tier.gain + level}、对方本回合-{tier.opponent_reduction}，自己下回合+1招",
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
            "实时演算重复抽中改为+10并再抽2次，但领域权重不重复叠层；音乐首次再抽1次，重复再抽2次且不增加音乐层数。",
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
    if "daniya-v19" in move.tags:
        numeric = Fraction(move.resolved_gain_tenths, VICTORY_WEIGHT_SCALE)
        return move.description + (
            f"强化+{level}：基础自身加权变为{weight_label(numeric + level)}。" if level and numeric > 0 else ""
        )
    gain = Fraction(move.resolved_gain_tenths, VICTORY_WEIGHT_SCALE)
    enhanced = gain + level if gain > 0 else gain
    numeric = f"自身胜率{_signed_weight(enhanced)}" if gain else ""
    if move.resolved_opponent_reduction_tenths:
        numeric += f"、对方-{weight_label(Fraction(move.resolved_opponent_reduction_tenths, VICTORY_WEIGHT_SCALE))}"
    numeric = numeric.lstrip("、")
    return numeric


def _daniya_wheels(identity: CommandIdentity, level: int) -> BattleView:
    forms = FIGHTERS_BY_ID["daniya"].forms
    return view(
        identity, "达妮娅猪 · 三形态战斗轮盘",
        banner=f"强化+{level}。初始布景，蚀域命中转幻灭；25粒子、7次蚀域命中或转化招进入黑洞。",
        wheels=(
            *(wheel_card("move", f"达妮娅猪 · {form.name}",
                          tuple((move.name, _move_weight(move)) for move in form.moves),
                          note="基础出现权重；实战战报保存当时的动态盘。") for form in forms),
            wheel_card("injury", "黑洞 · 固定伤势盘",
                       tuple((INJURY_NAMES[key], _scaled_weight(weight, 100))
                             for key, weight in FIXED_BLACK_HOLE_WHEEL),
                       note="82.3%无伤 / 12.49%受伤 / 5.21%力竭；受伤先轻伤、再重伤，概率始终固定。"),
            *_common_battle_wheels(),
        ),
        panels=(
            *(Panel(form.name + "招式", tuple(
                Line(move.name, _daniya_effect(move, level), f"基础抽取权重{_move_weight(move)}")
                for move in form.moves
            )) for form in forms),
            Panel("虚质粒子与伤势", (
                Line("新获粒子", "每招+0.5，包括贷款和被无视的原生招式",
                     "布景/幻灭每点永久敌减权0.521；黑洞每点0.823。消耗粒子不撤销减权。"),
                Line("布景/幻灭抵伤", "严格大于5/10/15点时，减少1/2/3层伤势并消耗1/2/3点",
                     "每回合末最多结算一次；幻灭谎言再减一级。黑洞使用固定盘。"),
                Line("进入黑洞", "已有伤势重置，粒子恢复到至少25点",
                     "粒子继续增长，每点使自身胜权与敌减权额外增加8%；25点为×3。"),
                Line("计时的溃灭", "黑洞中改为被动，敌力竭权重额外+当前回合数×5",
                     "此前永久累积保留；对方固定盘和免抽保护仍有效。"),
            )),
        ),
        hints=("旧对局按其保存的规则继续；此处展示新对局的Battle v19。",
               "原生粒子与形态成长不因数值无视回滚；复制招式不领取来源的粒子和形态资源。"),
    )


def _xixi_wheels(identity: CommandIdentity, level: int) -> BattleView:
    moves = FIGHTERS_BY_ID["xixi"].moves
    return view(
        identity, "西西猪 · 西天帝战斗轮盘",
        banner=f"强化+{level}。先用 /战斗猪 形态 西天帝 选择路线；原型尚未开放，对局中不能换路线。",
        wheels=(
            wheel_card("move", "西天帝路线 · 基础九格盘", tuple((move.name, _move_weight(move)) for move in moves),
                       note="标记连招会增加超负荷权重；冷却装备暂时退出盘，成帝后永久退出。"),
            wheel_card("move", "海克斯 · 未获得的符文",
                       tuple((name, weight) for _key, name, _gain, weight in HEXTECH_SPECS),
                       note="每种只会获得一次；领悟核心或领域命中时抽取。"),
            wheel_card("injury", "西西天帝 · 固定伤势盘", (("无伤", 99), ("力竭倒下", 1)),
                       note="集齐四种海克斯后永久使用；其他招式不能修改概率。"),
            *_common_battle_wheels(),
        ),
        panels=(
            Panel("西天帝路线招式", tuple(Line(
                move.name, move.description + (f"强化后基础胜权+{move.gain + level}。" if level and move.gain else ""),
                f"基础抽取权重{_move_weight(move)}",
            ) for move in moves)),
            Panel("奥数专精与装备", (
                Line("胜权提升高出招数", "原出招权重×[1+min(200,胜权)/200×(出招数−1)]",
                     "胜权下限取0，上限按200计算；重伤盘仍只有1至4招。"),
                Line("出招数提升全轮胜权", "每多出1招，本轮每个招式+2",
                     "自动超负荷和追加出招参与统计；沙漏停止的次数不参与。"),
                Line("现实器", "整轮己方胜权与敌减权×1.3，敌方对应数值×0.7",
                     "覆盖已经出过的招式以及本轮后置收益。"),
                Line("装备冷却", "使用当轮及下一回合不能再抽到", "第r回合使用，第r+2回合恢复。"),
                Line(XIXI_CORE_NAME, "保留解除重伤和核心+1，并抽取一次海克斯", "免抽伤势时不会领悟核心。"),
            )),
            Panel("四种海克斯", (
                Line("物理转魔法", "全部招式永久+6"),
                Line("由心及物", "下一次败北免抽伤势", "保护消耗一次；不受伤、不力竭、不领悟核心。"),
                Line("歌莉娅巨人", "全部招式永久+8", "取得后敌方有效正收益累计未达到100时，败北免抽伤势。"),
                Line("回归基本功", "全部招式永久+10、出招数永久+1、轻伤/重伤/力竭权重减半",
                     "曲径折跃只成长超负荷，不进入领域战；简易领域成功率40%，每次使用3回合后抽海克斯。"),
                Line("西西天帝", f"集齐后全部招式+{EMPEROR_GAIN:,}",
                     "清除全部伤势；99%无伤/1%力竭固定盘；现实器与中亚沙漏永久退出抽取。"),
            )),
        ),
        hints=("连续法术涌动每次使整轮各招额外+4；标记只保留一层。",
               "曲径折跃每次永久超负荷+6；手抽超负荷清空出现加权，自动超负荷不清空。"),
    )


def _asamu_effect(move, level: int) -> str:
    gain = Fraction(move.resolved_gain_tenths, VICTORY_WEIGHT_SCALE)
    numeric = f"胜率+{weight_label(gain + level)}" if gain > 0 else ""
    mechanics = {
        "asamu-bathe": "喝奶茶抽取权重+0.5",
        "asamu-milk-tea": "全盛姿态抽取权重永久+0.1，并重置喝奶茶当前加权",
        "asamu-sleep": "本场之后所有招式胜率额外+5，可无限累积",
        "asamu-prime": "再抽2次，并清空憋个大的临时出现权重",
        "asamu-charge-up": "再抽1次；全盛姿态临时出现权重+1，打出后清空",
        "asamu-pressure-king": "本层对方每个数值招式独立33%失效；功能保留",
        "asamu-misfortune-transfer": "双方本回合力竭倒下权重各×5",
        "asamu-milk-dragon": "依次覆盖对方下回合第一、第二……招为发奶龙",
        "asamu-tit-for-tat": "回合末落后则交换双方权重并再+4；未落后则自身+40",
        "asamu-domain": "领域战胜利或单方命中后随机使用对方2个招式；复制领域不再次判定",
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
        banner=f"展示强化+{level}的数值。喝奶茶与憋个大的动态养成全盛姿态，睡觉永久叠加后续招式胜率。",
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
                    Line("领域·呃呃阿萨姆奶茶", "领域胜利或命中复制对方2招", "逐份展示来源、数值与领域再入抑制。"),
                ),
            ),
        ),
        hints=("传奇耐压王抽取权重随无伤/轻伤/重伤变为0.5/1/2。", "厄运传递和计时的溃灭按独立倍率乘入伤势盘。"),
    )


def _yilu_effect(move, level: int) -> str:
    mechanics = {
        "yilu-vanguard": "胜率+5；再抽1次、指示物+2；此后所有招式基础胜率+2",
        "yilu-guard": "指示物+1后全部融合；每点+5，消耗≥8触发本回合胜率翻倍",
        "yilu-defender": "胜率+2、指示物+1；70%令对方随机一招数值归零并额外-5",
        "yilu-caster": "指示物+3；每6点换+40，可连续；不足6再+1指示物",
        "yilu-sniper": "等概率连射1至10枪；每枪独立吃先锋/明日加成；消耗指示物令之后每枪累积+2",
        "yilu-medic": "权重0.2；清空指示物，重伤/力竭盘×0.5；重伤恢复轻伤，再+2指示物",
        "yilu-specialist": "权重0.5；清空指示物，再抽2次非医疗、非特种干员，并+1指示物",
        "yilu-domain": "胜率+32.5；领域战获胜或单方命中后，下回合+1招且该回合所有招式基础胜率+1",
        "yilu-babel-ghost": "下一抽限定干员且完整效果×2；下回合-1招",
    }[move.move_id]
    return mechanics + (f"；基础正数招式受强化+{level}" if move.resolved_gain_tenths > 0 else "")


def _yilu_wheels(identity: CommandIdentity, level: int) -> BattleView:
    moves = FIGHTERS_BY_ID["yilu"].moves
    return view(
        identity,
        "熠～噜猪 · 罗德岛干员战斗盘",
        banner=f"展示强化+{level}的规则。指示物跨回合保留；累计每跨9点再抽1次。",
        wheels=(
            wheel_card("move", "熠～噜猪 · 基础招式盘", tuple((move.name, _move_weight(move)) for move in moves)),
            *_common_battle_wheels(),
        ),
        panels=(
            Panel(
                "干员与领域",
                tuple(
                    Line(move.name, _yilu_effect(move, level), f"基础抽取权重 {_move_weight(move)}")
                    for move in moves
                ),
                "每回合最多放置10名干员；第10名落地后强制结束己方本回合连锁。",
            ),
            Panel(
                "指示物与再部署",
                (
                    Line("指示物", "持有量可消费，累计量不倒退", "累计总量每达到9的倍数，再抽1次。"),
                    Line("巴别塔的恶灵", "下一名干员效果按顺序完整执行2次", "只占1个干员放置名额。"),
                    Line("特种再部署", "限定非医疗、非特种干员2次", "两次分别抽取并正常计算指示物与10名上限。"),
                    Line("近卫真伤", "融合消耗至少8时本回合胜率翻倍", "多次触发按层连续翻倍。"),
                ),
            ),
        ),
        hints=(
            "末日方舟的“明日”会在双方领域对抗获胜或单方领域命中时触发。",
            "医疗会即时把重伤恢复为轻伤；特种不再加重伤势盘。",
        ),
    )


def _firefly_wheels(identity: CommandIdentity, level: int) -> BattleView:
    definition = FIGHTERS_BY_ID["firefly"]
    firefly_options = []
    sam_options = []
    lines = []
    for move in definition.moves:
        base_units = move.resolved_draw_weight_units
        firefly_units = base_units - MOVE_WEIGHT_SCALE // 10 if "sam-skill" in move.tags else base_units
        firefly_options.append((move.name, max(1, firefly_units) / MOVE_WEIGHT_SCALE))
        sam_options.append((move.name, base_units / MOVE_WEIGHT_SCALE))
        training = f"；强化后基础正数+{level}" if move.resolved_gain_tenths > 0 else ""
        lines.append(
            Line(
                move.name,
                move.description + training,
                f"基础抽取权重 {_move_weight(move)}",
            )
        )
    return view(
        identity,
        "栖夜流萤抱抱猪 · 双形态共鸣战斗盘",
        banner=f"展示强化+{level}的规则。燃芯、溃败、形态与剩余回合均写入战斗状态，可断线恢复。",
        wheels=(
            wheel_card(
                "move",
                "流萤形态 · 基础招式盘（0层燃芯）",
                tuple(firefly_options),
                note="流萤形态的萨姆招式基础出现权重-0.1；每层燃芯再为所有萨姆招式+0.1。",
            ),
            wheel_card(
                "move",
                "萨姆形态 · 基础招式盘（0层燃芯）",
                tuple(sam_options),
                note="萨姆形态抽到流萤技能时触发残梦回声，不退出萨姆形态。",
            ),
            *_common_battle_wheels(),
        ),
        panels=(
            Panel("流萤 / 萨姆招式", tuple(lines)),
            Panel(
                "双形态共鸣",
                (
                    Line("燃芯", "最多3层", "每层令萨姆技能胜率+5、出现权重+0.1；点燃星海按技能规则结算后清空。"),
                    Line("溃败", "最多3层",
                         "每层令萨姆技能额外+5；1/2/3层令对手本回合力竭权重+0.08/+0.18/+0.35，半层按相邻档插值。"),
                    Line("流萤 → 萨姆", "抽到萨姆技能立即切换", "萨姆形态持续2回合；流萤形态的该招基础出现权重-0.1。"),
                    Line("萨姆中的流萤技能", "残梦回声",
                         "不退出萨姆、不获燃芯；飞萤之火选萨姆技+12，选流萤技按半效结算（溃败亦为半层）。"),
                ),
                "飞萤之火自动择优并保存候选。领域结算后延长已有萨姆形态1回合，下回合对手溃败+1、自己+1招。",
            ),
        ),
        hints=(
            "领域命中或领域战获胜时，领域自身有效胜率翻倍；焦土陨击的+12不重复翻倍。",
            "焦土陨击在对手满3层溃败时再使其胜率-15、本回合力竭权重+0.2。",
        ),
    )


def wheels(identity: CommandIdentity, fighter_id: str, level: int = 0) -> BattleView:
    if fighter_id == "juejue":
        return _juejue_wheels(identity, level)
    if fighter_id == "daniya":
        return _daniya_wheels(identity, level)
    if fighter_id == "xixi":
        return _xixi_wheels(identity, level)
    if fighter_id == "asamu":
        return _asamu_wheels(identity, level)
    if fighter_id == "yilu":
        return _yilu_wheels(identity, level)
    if fighter_id == "firefly":
        return _firefly_wheels(identity, level)
    definition = FIGHTERS_BY_ID[fighter_id]
    moves = []
    wheel_name = {"miumiu": "流形与润化招式盘", "luoli": "黄瓜与账单招式盘"}.get(fighter_id, "等权招式盘")
    for move in definition.moves:
        effect = f"胜利权重+{move.gain + level}" if move.gain else ""
        if move.opponent_reduction:
            effect += f"；对方胜利权重-{move.opponent_reduction}"
        if move.draws:
            effect += f" 再抽{move.draws}次"
        if move.loan:
            effect += "；下个数值招式×2，下回合扣1招"
        if move.description:
            effect += "；" + move.description
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
                definition.name + " · " + wheel_name,
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
            Panel(wheel_name, tuple(moves)),
            *((Panel("黄瓜与账单状态", tuple(Line(name, summary) for name, summary in LUOLI_STATUS_HELP)),)
              if fighter_id == "luoli" else ()),
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
            "任意战斗猪单方领域命中或领域战获胜后，都会触发领域效果并翻倍一份仍有效领域胜率；撅撅猪双领域胜出使用双领域特例。",
            "无下限每回合只免疫对方首个仍有效的数值招式；领域同回合只判定一次。",
        ),
    )
