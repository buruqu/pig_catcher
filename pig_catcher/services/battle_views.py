"""将已提交对战事实投影成图卡；不在渲染中抽盘或做资源结算。"""

from __future__ import annotations

from ..domain.battle import weight_label
from ..domain.battle_catalog import (
    COUNT_WHEEL,
    FIGHTERS_BY_ID,
    HEAVY_COUNT_WHEEL,
    INJURY_NAMES,
    INJURY_WHEELS,
    MATERIAL_IDS,
    TOOLS_BY_ID,
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


def move_line(event: dict) -> Line:
    if event["base"] > 0:
        note = (
            f"({event['base']} + 强化{event['training']} + 核心{weight_label(event['core'])} "
            f"- 伤势{event['penalty']}) ×{event['multiplier']}"
        )
        if event["trait_gain"] or event["tool_gain"]:
            note += f"，个体+{event['trait_gain']} / 器具+{event['tool_gain']}（不翻倍）"
        value = f"+{weight_label(event['gain'])} → 累计{weight_label(event['total'])}"
    else:
        value = f"再抽{event['extra_draws']}次"
        note = "功能招式不加战斗强化；待用×2保留" if event["double_pending"] else "功能招式不加战斗强化"
    if event["loan"]:
        note += f"；下回合扣招累计{weight_label(event['next_debt'])}，仅保留一份×2"
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
    display_sides = round_result["after"] if round_result else state["sides"]
    total = sum(side["weight"] for side in display_sides)
    cards, panels, wheel_cards = [], list(extra_panels), []
    count_cards: list[BattleWheelCard | None] = [None, None]
    move_cards: list[BattleWheelCard | None] = [None, None]
    action_lines: list[tuple[Line, ...]] = [(), ()]
    action_notes = ["", ""]
    # 伤势结算后可能已治愈；出招数图必须使用抽取时(before)的盘，而非刚变化的伤势。
    count_sides = round_result["before"] if round_result else display_sides
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
    if events:
        for event_side in (0, 1):
            side_events = [event for event in events if int(event["side"]) == event_side]
            if not side_events:
                continue
            last = side_events[-1]
            moves = FIGHTERS_BY_ID[last["fighter_id"]].moves
            move_cards[event_side] = wheel_card(
                "move",
                "第" + str(last["ordinal"]) + "招落点",
                tuple((move.name, move.draw_weight) for move in moves),
                last["name"],
                "本卡展示最后一招的真实落点；逐招数值均为已提交事实。",
            )
            visible_events = side_events if round_result else side_events[-8:]
            action_lines[event_side] = tuple(move_line(event) for event in visible_events)
            action_notes[event_side] = (
                f"本回合共{len(side_events)}招，以上为完整出招记录。"
                if round_result
                else f"本次实际执行{len(side_events)}招；超过8招展示末8招，全部事实可用 "
                f"/对战记录 {match['battle_id']} {side_events[0]['round']} 查看。"
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
        if turn["done"]:
            ready = "本回合已结算" if round_result and all_done else "已出完，等待对方"
        else:
            ready = "尚未出完"
        cards.append(
            FighterCard(
                snap["player_name"],
                snap["name"],
                snap["short_code"],
                snap["level"],
                weight_label(side["weight"]),
                f"{side['weight'] * 10000 // total / 100:.2f}%",
                count,
                "重伤 · 数值招式-1" if side["heavy"] else "正常出招盘",
                ("初始风险", "轻伤风险", "重伤风险")[side["risk"]],
                weight_label(side["core"]),
                f"下回合待扣{weight_label(side['next_debt'])}招",
                "已出完" if turn["done"] else f"待连抽{weight_label(turn['pending'])}次",
                tool_note,
                ready,
                count_cards[index],
                move_cards[index],
                action_lines[index],
                action_notes[index],
            )
        )
    if round_result:
        winner = display_sides[round_result["winner"]]["snapshot"]["player_name"]
        loser = display_sides[round_result["loser"]]["snapshot"]["player_name"]
        wheel_cards.append(
            wheel_card(
                "injury",
                loser + " · 伤势盘落点",
                tuple((INJURY_NAMES[key], weight) for key, weight in round_result["injury_wheel"]),
                INJURY_NAMES[round_result["injury"]],
                "扇区按本轮抽取时的风险权重绘制；标记是已经保存的结果。",
            )
        )
        panel_note = (
            "整场结束，败者获得5次额外战利品抓猪，全部归胜者。"
            if round_result["natural_end"]
            else "累计胜利权重保留，双方继续下一回合。"
        )
        panels.append(
            Panel(
                f"第{round_result['round']}回合 · {winner}胜",
                (
                    Line(
                        loser + "的伤势盘",
                        INJURY_NAMES[round_result["injury"]],
                        "抽取权重：" + " / ".join(f"{INJURY_NAMES[k]} {v}" for k, v in round_result["injury_wheel"]),
                    ),
                ),
                panel_note,
            )
        )
    remaining = max(0, (match["expires_ms"] - now_ms + 999) // 1000)
    if match["status"] == "pending":
        hints = (
            "受邀者：/比划比划 接受 或 /比划比划 拒绝；邀请者：/比划比划 取消。",
            "接受后才扣今日各自角色额度并锁定猪猪；自然力竭败者的5只战利品归胜者。",
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
        banner = banner or f"{winner} 获胜！{loser} 力竭倒下，获得5次额外战利品抓猪，抓到的猪全部归 {winner}。"
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
        win_percent=f"{display_sides[0]['weight'] * 10000 // total / 100:.2f}",
        panels=tuple(panels),
        hints=hints,
        wheels=tuple(wheel_cards),
        celebration=state["status"] == "completed"
        or (
            state["status"] == "active"
            and state["round"] == 1
            and any(s["snapshot"].get("tool_id") == "confetti" for s in display_sides)
        ),
    )


def wheels(identity: CommandIdentity, fighter_id: str, level: int = 0) -> BattleView:
    definition = FIGHTERS_BY_ID[fighter_id]
    moves = []
    for move in definition.moves:
        effect = f"胜利权重+{move.gain + level}" if move.gain else ""
        if move.draws:
            effect += f" 再抽{move.draws}次"
        if move.loan:
            effect += "；下个数值招式×2，下回合扣1招"
        moves.append(
            Line(move.name, effect.strip(), f"抽取权重 {move.draw_weight}；出现概率 {100 / len(definition.moves):.2f}%")
        )
    return view(
        identity,
        definition.name + " · 战斗轮盘",
        banner=f"展示强化+{level}的数值。功能招式不强化，抽中概率不随升级变化。",
        wheels=(
            wheel_card(
                "move",
                definition.name + " · 等权招式盘",
                tuple((move.name, move.draw_weight) for move in definition.moves),
                note="规则预览；没有抽取，强化只增加数值招式的胜利权重。",
            ),
            wheel_card("count", "正常出招数", tuple((f"{number}招", weight) for number, weight in COUNT_WHEEL)),
            wheel_card("count", "重伤出招数", tuple((f"{number}招", weight) for number, weight in HEAVY_COUNT_WHEEL)),
            *(
                wheel_card(
                    "injury",
                    ("初始风险", "轻伤风险", "重伤风险")[i],
                    tuple((INJURY_NAMES[key], weight) for key, weight in options),
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
                            " / ".join(f"{INJURY_NAMES[k]}:{w}" for k, w in wheel),
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
        ),
    )
