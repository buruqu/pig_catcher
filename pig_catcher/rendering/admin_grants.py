"""发放图只展示汇总和群昵称，不把全员ID清单塞进群消息。"""

from __future__ import annotations

import json

from ..domain.models import CommandReceipt
from .models import EconomyReceiptRowViewModel as Row
from .models import EconomyReceiptViewModel


def admin_grant_view(receipt: CommandReceipt) -> EconomyReceiptViewModel:
    data = json.loads(receipt.result_json)
    if receipt.result_type == "admin-asset-grant":
        return EconomyReceiptViewModel(
            eyebrow="猪管奖励 · 已存入背包",
            title="奖励已送达",
            badge_label="发放数量",
            badge_value="1",
            summary=f"{'★' * int(data['rarity'])} {data['display_name']}",
            rows=(Row("领取玩家", data.get("target_display_name", "指定玩家")), Row("资产编号", data["short_code"])),
            note="可在猪猪背包或美食背包查看。",
        )
    all_players = bool(data["all_players"])
    players = data["players"]
    total = int(data["granted_total"])
    rows = [
        Row("发放范围", f"本群 {len(players)} 人" if all_players else players[0]["display_name"]),
        Row("每人数量", str(data["quantity"])),
        Row("实际发放合计", str(total)),
    ]
    notes = [data["usage"]]
    if data["storage"] == "upgrade":
        notes.append("每份提升1级，达到10级后不再增加；实际发放合计仅统计本次新增等级。")
        if not all_players:
            rows.append(Row("当前等级", f"Lv.{players[0]['quantity_after']}"))
    elif not all_players and data["storage"] not in {"pig", "food"}:
        rows.append(Row("发放后库存", str(players[0]["quantity_after"])))
    if not all_players and data["storage"] in {"pig", "food"}:
        codes = [asset["short_code"] for asset in players[0]["assets"]]
        notes.append("资产编号：" + "、".join(codes[:8]) + ("；其余请在背包查看。" if len(codes) > 8 else "。"))
    if all_players:
        notes.append("奖励已直接到账，无需重复领取。")
    return EconomyReceiptViewModel(
        eyebrow="猪管奖励 · " + data["category"],
        title="全群奖励已送达" if all_players else "奖励已送达",
        badge_label="领取玩家",
        badge_value=f"{len(players)} 人",
        summary=data["display_name"],
        rows=tuple(rows),
        note=" ".join(notes),
    )
