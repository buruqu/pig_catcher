"""可复制的抓猪插件纯文字帮助。"""

from __future__ import annotations

from ..version import FRAMEWORK_PHASE, PLUGIN_VERSION

_TOPICS: dict[str, tuple[str, ...]] = {
    "抓猪": (
        "/抓猪",
        "/抓群友",
        "/抓猪档案",
        "/抓猪详情 <猪名#短编号>",
    ),
    "背包": (
        "/猪猪背包 [页码] [品质=数字] [排序=方式]",
        "/猪猪图鉴 [页码] [品质=数字|未收集]",
        "/猪猪纪录 [页码]",
    ),
    "道具": (
        "/使用道具 <道具名称>",
        "/取消道具 <抓猪|做菜>",
    ),
    "做菜": (
        "/做菜 <猪名#短编号>",
        "/吃菜 <美食名#短编号>",
        "/使用美食 <美食名#短编号>",
        "/美食背包 [页码] [品质=数字] [排序=方式]",
        "/美食图鉴 [页码] [品质=数字|未收集]",
        "/美食详情 <美食名#短编号>",
    ),
    "商城": (
        "/猪猪商城 [页码] [分类=全部|抓猪|做菜|升级]",
        "/购买 <商品名称> [数量]",
        "/售卖猪猪 <猪名#短编号>",
        "/售卖美食 <美食名#短编号>",
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

_OPEN_TOPICS = frozenset(_TOPICS)

_TOPIC_ALIASES = {
    "仓库": "背包",
    "图鉴": "背包",
    "经济": "商城",
}


def _topic_block(topic: str) -> str:
    suffix = "" if topic in _OPEN_TOPICS else "·尚未开放"
    lines = [f"【{topic}指令{suffix}】"]
    lines.extend(_TOPICS[topic])
    return "\n".join(lines)


def format_help(topic: str = "") -> str:
    """按主题返回便于复制的纯文字帮助，并标明开放边界。"""

    normalized = str(topic or "").strip()
    normalized = _TOPIC_ALIASES.get(normalized, normalized)
    notice = (
        f"当前版本：v{PLUGIN_VERSION}（第 {FRAMEWORK_PHASE} 轮）\n"
        "已开放抓猪、收藏、做菜、美食、商城、赠送、双方确认交易、"
        "展示位和七类群排行。"
    )
    if normalized and normalized not in {"全部", *list(_TOPICS)}:
        topics = "、".join(_TOPICS)
        return (
            f"【抓猪插件·指令帮助】\n\n{notice}\n\n未知帮助主题：{normalized}\n可用主题：{topics}\n示例：/抓猪帮助 做菜"
        )
    if normalized and normalized != "全部":
        return (
            "【抓猪插件·指令帮助】\n\n"
            f"{notice}\n\n"
            f"{_topic_block(normalized)}\n\n"
            "资产选择器格式：名称#8位短编号\n"
            "示例：/抓猪详情 粉红小香猪#A19F2C3D\n"
            "赠送与报价必须明确 @ 一位当前群成员。"
        )

    lines = [
        "【抓猪插件·指令帮助】",
        "",
        notice,
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
            "资产选择器格式：名称#8位短编号",
            "示例：/做菜 粉红小香猪#A19F2C3D",
            "交易示例：/猪猪交易 粉红小香猪#A19F2C3D @群友 100",
            "",
            "所有玩法仅响应显式斜杠命令，不读取普通聊天，也不使用 LLM。",
        ]
    )
    return "\n".join(lines)
