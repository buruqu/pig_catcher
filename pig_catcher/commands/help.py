"""可复制的抓猪插件纯文字帮助。"""

from __future__ import annotations

_TOPICS: dict[str, tuple[str, ...]] = {
    "抓猪": (
        "/抓猪",
        "/抓群友",
    ),
    "背包": (
        "/猪猪背包 [页码] [品质=数字] [排序=方式]",
        "/猪猪详情 <猪名#短编号>",
        "/猪猪图鉴 [品质=数字|未收集]",
        "/猪猪纪录 [页码]",
        "/今日巨物",
    ),
    "道具": (
        "/使用道具 <道具名称>",
        "/取消道具 <抓猪|做菜>",
    ),
    "做菜": (
        "/做菜 [猪名#短编号]",
        "/吃菜 [美食名#短编号]",
        "/使用美食 <美食名#短编号>",
        "/美食背包 [页码] [品质=数字] [排序=方式]",
        "/美食图鉴 [品质=数字|未收集]",
        "/美食详情 <美食名#短编号>",
    ),
    "商城": (
        "/猪猪商城 [分类=全部|抓猪|做菜|升级]",
        "/购买 <商品名称> [数量]",
        "/升级 <猪饲料|厨具>",
        "/售卖猪猪 [猪名#短编号]",
        "/售卖美食 [美食名#短编号]",
        "/批量售卖 <猪猪|美食>",
        "/猪币账本 [页码]",
    ),
    "交易": (
        "/猪猪赠送 <猪名#短编号> @成员",
        "/美食赠送 <美食名#短编号> @成员",
        "/猪猪交易 <猪名#短编号> @成员 <猪币>",
        "/美食交易 <美食名#短编号> @成员 <猪币>",
        "/接受交易 <交易号>",
        "/拒绝交易 <交易号>",
        "/取消交易 <交易号>",
        "/我的交易 [全部|待处理|已完成|已拒绝|已取消|已过期] [页码]",
    ),
    "排行": (
        "/猪猪排行 [综合|抓猪|美食|价值|巨物|数量|猪币] [页码]",
        "/设置展示 <猪猪|美食> <名称#短编号>",
        "/设置展示 <猪猪|美食> 取消",
    ),
}

_TOPIC_ALIASES = {
    "仓库": "背包",
    "图鉴": "背包",
    "经济": "商城",
}


def _topic_block(topic: str) -> str:
    lines = [f"【{topic}指令】"]
    lines.extend(_TOPICS[topic])
    return "\n".join(lines)


def _usage_block() -> str:
    return "\n".join(
        (
            "【使用提示】",
            "同名资产请使用：名称#短编号（4-16位字母数字，不区分大小写）",
            "猪猪详情使用背包中显示的名称#短编号。",
            "做菜、吃菜和单件售卖可省略编号，自动处理最低价值的 1 至 3 星资产。",
            "赠送与交易必须明确 @ 一位当前群成员。",
        )
    )


def format_help(topic: str = "") -> str:
    """按主题返回只保留命令和必要提示的纯文字帮助。"""

    normalized = str(topic or "").strip()
    normalized = _TOPIC_ALIASES.get(normalized, normalized)
    if normalized and normalized not in {"全部", *list(_TOPICS)}:
        topics = "、".join(_TOPICS)
        return (
            "【抓猪插件·指令帮助】\n\n"
            f"未知帮助主题：{normalized}\n"
            f"可用主题：{topics}\n"
            "示例：/抓猪帮助 做菜"
        )
    if normalized and normalized != "全部":
        return (
            "【抓猪插件·指令帮助】\n\n"
            f"{_topic_block(normalized)}\n\n"
            f"{_usage_block()}"
        )

    lines = [
        "【抓猪插件·指令帮助】",
        "",
        "/抓猪帮助 [抓猪|背包|道具|做菜|商城|交易|排行]",
        "",
    ]
    for index, topic_name in enumerate(_TOPICS):
        if index:
            lines.append("")
        lines.append(_topic_block(topic_name))
    lines.extend(
        [
            "",
            _usage_block(),
        ]
    )
    return "\n".join(lines)
