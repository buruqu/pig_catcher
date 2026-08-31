"""按真实路由、配置和商品定义生成可复制的分专题帮助；不读取玩家状态。"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import PigCatcherConfig
from ..domain.feature_shop import FEATURE_SHOP_PRODUCTS, FEATURE_SHOP_SYSTEM_LABELS
from ..domain.gameplay import ITEM_DEFINITIONS


@dataclass(frozen=True, slots=True)
class HelpLine:
    text: str
    gate: str = "always"


@dataclass(frozen=True, slots=True)
class HelpTopic:
    commands: tuple[HelpLine, ...]
    notes: tuple[str | HelpLine, ...] = ()
    related: tuple[str, ...] = ()
    gate: str = ""


# gate 与 plugin.py 命令的真实开关一致；奖励库存不依赖成就统计或商店道具开关。
_TOPICS: dict[str, HelpTopic] = {
    "抓猪": HelpTopic(
        (HelpLine("/抓猪", "catch"), HelpLine("/抓群友", "catch")),
        (
            "额度按时段刷新，不是每到整点刷新；临时、今日、限时和专属额度按各自期限结算。",
            "结果卡显示本次最终概率、使用的加成与剩余次数；专属额度不挤占普通额度。",
            "等级概率收益到 Lv.21 封顶；后续里程碑给猪币，称号可在展示中看见。",
            "不同平台和群的数据分别保存；两个入口对应同一现实群，也不会自动合并账号。",
        ),
        ("背包", "道具", "叠加"),
    ),
    "背包": HelpTopic(
        (
            HelpLine("/猪猪背包 [页码] [品质=数字] [排序=方式]", "inventory"),
            HelpLine("/猪猪背包 1 品质=4 排序=价值", "inventory"),
            HelpLine("/猪猪详情 <猪名#短编号>", "inventory"),
            HelpLine("/美食背包 [页码] [品质=数字] [排序=方式]", "food_inventory"),
            HelpLine("/美食详情 <美食名#短编号>", "food_inventory"),
            HelpLine("/猪猪图鉴 [品质=数字]；/猪猪图鉴 品质=未收集", "catalog"),
            HelpLine("/美食图鉴 [品质=数字]；/美食图鉴 品质=未收集", "food_catalog"),
            HelpLine("/收藏 <猪猪|美食> <名称[#短编号]>", "inventory"),
            HelpLine("/取消收藏 <猪猪|美食> <名称[#短编号]>", "inventory"),
            HelpLine("/切换 猪保千 [短编号]；/切换 初华猪 [短编号]", "catch"),
        ),
        (
            "页码从1开始；品质筛选为1至6。背包默认按价值排序，图鉴不用页码。",
            "猪猪排序：获得时间、品质、价值、体型、重量、名称；美食排序：获得时间、品质、价值、份量、名称。",
            "精确选择用名称#编号；编号为4至16位英文字母或数字，不区分大小写。",
            "按名称收藏会标记所有符合条件的同名资产；收藏猪菜不会被消耗、转让或批量选中。",
            "旅行中、交易中、乐队或战斗保护也会限制处置，取消收藏不等于解除这些保护。",
            "有多只同名换装猪时填写短编号；切换只换立绘，不另造资产或改变属性。",
        ),
        ("批量", "做菜", "排行"),
    ),
    "做菜": HelpTopic(
        (
            HelpLine("/做菜 [猪名[#短编号]]", "cook"),
            HelpLine("/吃菜 [美食名[#短编号]]", "eat"),
            HelpLine("/使用美食 <美食名[#短编号]>", "eat"),
            HelpLine("/是；/否（仅确认最后一份同名菜）", "eat"),
            HelpLine("/美食背包 [页码] [品质=数字] [排序=方式]", "food_inventory"),
            HelpLine("/美食详情 <美食名#短编号>", "food_inventory"),
        ),
        (
            "只填名称，自动选最低价值的可用同名资产；收藏、占用或受保护的资产会跳过。",
            "省略名称时仅在可用的1至3星中自动选择，不会随手消耗高星。",
            HelpLine("吃菜仅剩最后一份可食用同名菜时，30秒内 /是 执行、/否 退出，超时自动取消。", "eat"),
            HelpLine("精确填写名称#编号可直接吃菜，但不能绕过收藏或其他保护。", "eat"),
            HelpLine("1至5星猪不会做出六星菜；六星猪才有对应的六星做菜通道。", "cook"),
            HelpLine("KFC、宿傩、五条的专属菜只来自对应原料，不进入其他原料的通用菜池。", "cook"),
            "菜品详情显示这份菜的实际效果；已有实例可能保留获得时的效果快照。",
        ),
        ("批量", "叠加", "术式"),
    ),
    "批量": HelpTopic(
        (
            HelpLine("/批量做菜 [品质]；例如：/批量做菜 四星", "cook"),
            HelpLine("/批量售卖 <猪猪|美食> [品质]", "sell"),
            HelpLine("/批量售卖 美食 <菜名>；例如：/批量售卖 美食 猪寿司拼盘", "sell"),
            HelpLine("/开启批量保留", "batch"),
            HelpLine("/关闭批量保留", "batch"),
        ),
        (
            "不写品质默认只处理1至3星；明确品质可选1至5星。按菜名批售同样不含六星。",
            "默认每种联动猪保留价值最高的1只，不是把所有联动猪都留下。",
            "开启批量保留后，其他猪猪或美食也按每种模板保留一件最高价值实例；这是统一开关，不填类型。",
            "收藏、锁定和受保护的资产先排除，不作为可消耗候选；同价按固定编号顺序选择。",
            HelpLine("普通道具与非六星菜的做菜效果可用于批量做菜，按实际结算逐次扣除并显示剩余。", "cook"),
            HelpLine("存在待使用的六星做菜加成时禁止批量做菜，请先单次处理；六星抓猪加成不因此拦截。", "cook"),
            "批量不以精确编号绕过保护；想处理留存的一件，请先明确取消保护并使用单件指令。",
        ),
        ("背包", "做菜", "商城"),
    ),
    "商城": HelpTopic(
        (
            HelpLine("/猪猪商城 [分类=全部|抓猪|做菜|升级]", "store"),
            HelpLine("/猪猪商城 派遣", "dispatch_store"),
            HelpLine("/猪猪商城 巡演", "tour_store"),
            HelpLine("/猪猪商城 对战", "battle_store"),
            HelpLine("/购买 <商品名称> [数量]", "store"),
            HelpLine("/升级 <猪饲料|厨具>", "store"),
            HelpLine("/售卖猪猪 [猪名[#短编号]]", "sell"),
            HelpLine("/售卖美食 [美食名[#短编号]]", "sell"),
            HelpLine("/猪币账本 [页码]", "ledger"),
        ),
        (
            HelpLine("派遣、巡演、对战器具各自放在独立分商城，不会混入主商城“全部”。", "feature_store"),
            HelpLine("功能器具购买后直接进入对应玩法库存，在玩法流程中选择；原有材料制作入口保留。", "feature_store"),
            "购买数量不等于立刻使用；单件售卖按名称选最低价值的可售资产，省略名称仅选1至3星。",
            "旧猪菜保留原有价值，新获得的资产采用当前数值；已收藏或占用的资产不可售卖。",
        ),
        ("道具", "批量", "叠加"),
    ),
    "道具": HelpTopic(
        (
            HelpLine("/道具背包 [页码]"),
            HelpLine("/使用道具 <道具名称> [连续使用数量]", "items"),
            HelpLine("/使用道具 主厨香料 3", "items"),
            HelpLine("/取消道具 <抓猪|做菜>", "items"),
            HelpLine("/使用奖励券 编号修改券 <猪猪|美食> <名称#编号> <新编号>"),
            HelpLine("/使用奖励券 猪猪自选券 <猪名>"),
            HelpLine("/使用奖励券 确认；/使用奖励券 取消"),
            HelpLine("/重铸编号 <猪猪|美食> <原编号> <新编号>"),
        ),
        (
            HelpLine("同名道具可填写数量安排连续使用，抓猪与做菜各有独立槽；重新安排会替换该槽的队列。", "items"),
            HelpLine("安排时不提前扣库存，兼容操作成功结算后逐次消耗，结果卡显示剩余次数。", "items"),
            HelpLine("取消只清空尚未使用的安排，不会重复发还库存；不兼容或被专属效果覆盖时不消耗。", "items"),
            "编号修改券按指令直接改号；猪猪自选券先预览，30秒内用专用确认执行，超时失效。",
            "编号可用4至16位字母数字、大小写等同；不能占用现存资产编号或绕过资产保护。",
            "奖励券每次只选择一张，不使用道具的连续队列；兑换材料与玩法券请看奖励帮助。",
        ),
        ("奖励", "商城", "叠加"),
    ),
    "交易": HelpTopic(
        (
            HelpLine("/猪猪赠送 <猪名[#短编号]> @成员", "gift"),
            HelpLine("/美食赠送 <美食名[#短编号]> @成员", "gift"),
            HelpLine("/猪猪交易 <猪名[#短编号]> @成员 <猪币>", "trade"),
            HelpLine("/美食交易 <美食名[#短编号]> @成员 <猪币>", "trade"),
            HelpLine("/接受交易 <交易号>", "trade"),
            HelpLine("/拒绝交易 <交易号>", "trade"),
            HelpLine("/取消交易 <交易号>", "trade"),
            HelpLine("/我的交易 [全部|待处理|已完成|已拒绝|已取消|已过期] [页码]", "trade"),
        ),
        (
            "必须真实 @ 当前群的一位成员；请从QQ成员选择器点选，不要手敲昵称或猜测官方OpenID。",
            "只写名称会选最低价值的可转让实例；收藏、占用及玩法保护不能绕过。",
            HelpLine("赠送立即结算，不另等接收确认。", "gift"),
            HelpLine("交易由受邀人接受后同时转移资产和猪币，余额不足不会部分成交。", "trade"),
            "黑名单同时限制送出与接收；不同平台或群之间不能靠这些指令转移资产。",
        ),
        ("背包", "商城"),
    ),
    "排行": HelpTopic(
        (
            HelpLine("/猪猪排行 [综合|抓猪|美食|价值|巨物|数量|猪币] [页码]", "ranking"),
            HelpLine("/猪猪纪录 [页码]", "records"),
            HelpLine("/今日巨物", "records"),
            HelpLine("/设置展示 <猪猪|美食> <名称[#短编号]>", "showcase"),
            HelpLine("/设置展示 <猪猪|美食> 取消", "showcase"),
        ),
        (
            "综合榜、持有资产与历史获得记录是不同指标，不等于周冲榜积分。",
            "今日巨物看今天实际抓获时的体长和体重，归属原抓获者，不因赠送或售出改写。",
            "展示只填名称会选最高价值的一件，可以展示收藏资产；不会把它消耗掉。",
        ),
        ("周榜", "成就", "背包"),
    ),
    "成就": HelpTopic(
        (
            HelpLine("/猪猪成就 [分类] [页码]", "achievements"),
            HelpLine("/成就详情 <完整成就名>", "achievements"),
            HelpLine("/成就排行 [页码]", "achievements"),
            HelpLine("/佩戴成就 <完整成就名>", "cosmetics"),
            HelpLine("/成就徽章 [查看 页码]；/成就徽章 <1|2|3> <徽章名或ID>", "cosmetics"),
            HelpLine("/成就徽章 卸下 <1|2|3>", "cosmetics"),
            HelpLine("/取消佩戴成就", "cosmetics"),
        ),
        (
            HelpLine("分类包括远行手账、巡演纪念、比划档案、三栖生活；隐藏条目解锁前不公开准确条件。", "achievements"),
            "已获得的称号牌、边框和徽章可在角色展示中呈现，不额外提升概率。",
            "佩戴会更新该奖励提供的外观部位（徽章默认第1位），其他部位保留；取消佩戴成就会全部卸下。",
            "徽章默认1格；获得500点里程碑的三格展示架后开放3格，徽章不能重复佩戴，周榜牌与宝箱徽章同样可用。",
            "成就统计关闭但周榜开启时，仍可佩戴已获得的周榜奖励。",
        ),
        ("奖励", "周榜"),
    ),
    "奖励": HelpTopic(
        (
            HelpLine("/成就奖励 [页码]"),
            HelpLine("/使用成就券 <券名>；/成就奖励 使用 <玩法券名>"),
            HelpLine("/成就奖励 停用 <玩法券名>"),
            HelpLine("/成就奖励 材料 基础材料自选份 训练矿石 10"),
            HelpLine("/成就奖励 确认；/成就奖励 取消"),
            HelpLine("/使用奖励券 编号修改券 <猪猪|美食> <名称#编号> <新编号>"),
            HelpLine("/使用奖励券 猪猪自选券 <猪名>"),
            HelpLine("/使用奖励券 确认；/使用奖励券 取消"),
            HelpLine("/重铸编号 <猪猪|美食> <原编号> <新编号>"),
            HelpLine("/打开成就宝箱 <抓猪|做菜|图鉴|外观>", "achievements"),
            HelpLine("/领取成就纪念猪 <未收集的公共五星猪名>", "achievements"),
        ),
        (
            "材料自选份和奖励券不可赠送或交易；已拥有的奖励库存不因成就统计关闭而消失。",
            "材料自选1份换1个材料，可选数量1至10000；训练材料自选不含舞台组件。",
            "材料兑换与猪猪自选都需30秒内用对应的专用确认，不能用 /是 代替。",
            "玩法券一次只选一张，触发后清除选择，不会自动连用下一张。",
            "路费券和行李券互斥；其他用途各有槽位。预览不扣券，实际出发、结算或开战等触发才扣。",
            "例外：巡演档期券使用即补1张档期，需已有乐队，满7张不扣券；旧成就券以激活回执为准。",
            "不同确认入口互不代办；道具连续使用安排另见道具专题。",
        ),
        ("道具", "成就"),
    ),
    "周榜": HelpTopic(
        (
            HelpLine("/抓猪线 [页码]", "weekly"),
            HelpLine("/zzx [页码]", "weekly"),
            HelpLine("/佩戴成就 抓猪冲刺！！！", "weekly"),
        ),
        (
            "第一期「抓猪冲刺！！！」按活动期内抓猪价值累计，不是钱包余额或当前背包总价值。",
            "以榜单显示的本期名称、起止时间和计分口径为准，平台与群分别统计。",
            "结算后前十名领奖：第一、第二、第三各有专属色段牌，第四至第十获得十牌。",
            "只有已结算获得的本期奖励可以佩戴；未结束活动不会提前发牌。",
        ),
        ("排行", "成就", "奖励"),
        "weekly",
    ),
    "派遣": HelpTopic(
        (
            HelpLine("/猪猪派遣；/猪猪派遣 路线", "dispatch"),
            HelpLine("/猪猪派遣 编队 1 苯猪、野猪", "dispatch"),
            HelpLine("/猪猪派遣 确认；/猪猪派遣 取消", "dispatch"),
            HelpLine("/猪猪派遣 出发 1 青草近郊 4小时", "dispatch"),
            HelpLine("/猪猪派遣 返程；/猪猪派遣 召回 1", "dispatch"),
            HelpLine("/派遣背包 [配方]；/派遣背包 制作 区域地图 2", "dispatch"),
            HelpLine("/派遣游记 [页码|旅程编号]；/派遣游记 纪念品 1", "dispatch"),
            HelpLine("/派遣奇遇；/派遣奇遇 <奇遇编号> <1或2>", "dispatch"),
            HelpLine("/猪猪派遣 帮助（器具、材料转换与全部操作）", "dispatch"),
        ),
        (
            "最多3队，每队1至3只，至少一只1至3星、最多一只高星；同名默认选低价值空闲猪。",
            "编队、出发、召回各自预览后2分钟内确认，仅确认最后一项；收藏猪须精确编号并确认。",
            "时长4/8/12/24小时，每趟至多一个器具；派遣中的猪暂不能被消耗或转让。",
            "离线照常计时，到期奖励自动入账；返程记录不会丢。召回只结算完整4小时块，费用不退。",
            "材料与熟练度用于派遣、巡演和战斗养成，不增加普通抓猪的概率或次数。",
        ),
        ("奖励", "巡演", "对战"),
        "dispatch",
    ),
    "巡演": HelpTopic(
        (
            HelpLine("/组建乐队 <乐队名>", "tour"),
            HelpLine("/乐队编队 1 偶像猪、天才猪、武士道猪", "tour"),
            HelpLine("/猪猪巡演 确认；/猪猪巡演 取消", "tour"),
            HelpLine("/我的猪猪乐队", "tour"),
            HelpLine("/猪猪巡演 主题 星星落进练习室", "tour"),
            HelpLine("/猪猪巡演 排练", "tour"),
            HelpLine("/猪猪巡演 出发；/巡演继续；/巡演一键", "tour"),
            HelpLine("/巡演游记 [页码|巡演编号]；/巡演游记 收藏 1", "tour"),
            HelpLine("/巡演联演 @成员；/巡演联演 接受", "tour"),
            HelpLine("/猪猪巡演 帮助（角色、高光、路线、器材与培养）", "tour"),
        ),
        (
            "每套阵容3至5只，可保存3套并自由跨乐队混搭；不必同一原作乐队才能演出。",
            "付费或变更操作先预览，2分钟内用巡演专用确认；排练为免费预估，不发奖励。",
            "每天补1张档期，上限7张；联演邀请5分钟有效，双方各消耗一张。",
            "三站完成后结算整趟奖励；乐队成员及培养猪有独立保护，处置前需移出阵容并解除保护。",
        ),
        ("派遣", "奖励", "成就"),
        "tour",
    ),
    "对战": HelpTopic(
        (
            HelpLine("/战斗猪 设置 <宿傩猪|五条猪|撅撅猪|达妮娅猪|阿萨姆猪>", "battle"),
            HelpLine("/战斗猪 确认；/战斗猪 取消", "battle"),
            HelpLine("/战斗猪；/战斗猪 强化", "battle"),
            HelpLine("/比划比划 @成员；/比划比划 接受", "battle"),
            HelpLine("/比划比划 拒绝；/比划比划 取消", "battle"),
            HelpLine("/出招数 → /出招（双方完成后立即结算并展示完整招式）", "battle"),
            HelpLine("/对战状态；/对战记录 [页码]", "battle"),
            HelpLine("/战斗猪 轮盘 <战斗猪名>", "battle"),
            HelpLine("/战利品抓猪", "loot"),
            HelpLine("/战斗猪 帮助（轮盘、器具、认输与解除保护）", "battle"),
        ),
        (
            "双方先设置战斗猪，设置与强化等需2分钟内确认；每天可主动1场、应战1场，接受才扣。",
            "邀请5分钟有效，每群同时一场；开战后10分钟无有效行动自动无奖励结束。",
            "每回合用完整权重结算；本回合净增只向上取整保留50%到后续回合，基础5不折半。",
            "强化只加招式的胜利数值，不改变抽到招式的概率。",
            HelpLine("自然力竭的败者额外3次战利品抓猪，猪归胜者；基础五星30%、六星15%，只受败者永久加成。", "loot"),
            HelpLine("战利品不占普通额度、未使用次数跨日保留；认输不发。", "loot"),
            "黑闪基础+10并令后续数值招式+1；苍/赫提高两种茈的出招盘权重，任意茈发动后归零重算。",
            "领域、无下限、数值无效、形态切换与专属战斗机制均在回合结算图中展示。",
            "战斗招式不触发日常群术式。",
        ),
        ("派遣", "奖励", "术式"),
        "battle",
    ),
    "术式": HelpTopic(
        (
            HelpLine("/领域展开 伏魔御厨子", "cook"),
            HelpLine("/术式顺转 苍；/术式反转 赫", "cook"),
            HelpLine("/虚式 茈", "cook"),
            HelpLine("/转轮盘", "eat"),
            HelpLine("/重置额度", "catch"),
        ),
        (
            HelpLine("先吃对应专属菜获得使用机会，再发动指令；同群同时只运行一种群术式。", "cook"),
            HelpLine("伏魔御厨子作用于接下来全群10次成功抓猪，自动做菜；通常发动者与抓获者各得一份。", "cook"),
            HelpLine("苍将全群接下来5次抓获归发动者；赫将接下来5次抓获随机分配给群友。", "cook"),
            HelpLine("已满足苍、赫的使用条件后可发动茈，随机获得5只六星猪；战斗盘里的同名招式不算。", "cook"),
            HelpLine("猪保千猪排轮盘给3次转盘机会，用转轮盘指令逐次抽取；回执会列出奖项和剩余机会。", "eat"),
            HelpLine("糖醋排骨先给一次重置额度机会，执行后才发全群专属额度及奖励，不是吃下即重置。", "catch"),
            "群加成卡优先显示发动者昵称；剩余次数、期限和本次结果以回执为准。",
        ),
        ("做菜", "叠加"),
    ),
    "叠加": HelpTopic(
        (),
        (
            "普通抓猪：概率、体型、复制、奖励按效果分组；不同组可叠加，同组按使用顺序先用最早兼容项。",
            "一份菜可占多组，如黑猪麻汤圆同时占概率和体型组；不是每份菜都能同时生效。",
            "抓猪与做菜各能安排一种道具队列；同槽新安排替换旧安排，不把两种道具混用。",
            "普通做菜：一个概率组、一个份量组与兼容道具可一起用，份量最多×2；多次效果逐次扣除。",
            "六星原料的基础成功10%；超级主厨香料+10点、一猪六吃+15点、猪饺最多5层各+1点。",
            "以上组合为40%；普通临时层上限50%，云冻每层另加2点、最多5层，本组合加满层云冻为50%。",
            "六星专属效果优先，忽略的普通道具或菜品保留、不消耗，留到下一次兼容操作。",
            "雾蓝键盘大福：10次专属抓猪，每次随机洗牌基础星级概率；不叠加等级、饲料、永久或临时加成。",
            "糖醋排骨、猪鼻蛋包饭的普通全群倍率是明确例外，可叠加普通道具和非六星菜，同类倍率取较强项。",
            "群专属额度按注明的期限使用；不要把额外额度、全群倍率与普通时段上限当成同一个效果。",
            "有六星做菜加成待用时不能批量做菜；普通道具与非六星菜可以批量，逐项计算顺序与剩余。",
            "群术式接管抓获时，普通复制效果延后，不额外复制术式产物；战利品抓猪走独立规则。",
            "撅撅猪派已满永久5层后再吃：本周基础额度+1、12222币和编号修改券；达到满层当次不额外领。",
            "达妮娅泡泡云冻已满永久层后再吃：22222币和猪猪自选券；旧菜实例以其效果详情为准。",
            "判定以实际结果卡的最终概率为准；未完成的操作不扣效果，已结算但未出高品质不等于操作失败。",
        ),
        ("道具", "奖励", "批量"),
        "gameplay",
    ),
}

HELP_TOPICS = tuple(_TOPICS)

_TOPIC_ALIASES = {
    "仓库": "背包",
    "图鉴": "背包",
    "经济": "商城",
    "商店": "商城",
    "美食": "做菜",
    "吃菜": "做菜",
    "赠送": "交易",
    "周冲榜": "周榜",
    "活动": "周榜",
    "乐队": "巡演",
    "战斗": "对战",
    "券": "奖励",
    "加成": "叠加",
}
_INDEX_ALIASES = {"", "全部", "目录", "首页", "帮助"}


def _gates(settings: PigCatcherConfig) -> dict[str, bool]:
    features = settings.features
    return {
        "always": True,
        "catch": features.catching_enabled,
        "inventory": features.inventory_enabled,
        "food_inventory": features.food_inventory_enabled,
        "catalog": features.catalog_enabled,
        "food_catalog": features.food_catalog_enabled,
        "items": features.items_enabled,
        "cook": features.cooking_enabled,
        "eat": features.eating_enabled,
        "store": features.store_enabled,
        "sell": features.selling_enabled,
        "ledger": features.ledger_enabled,
        "gift": settings.trading.gift_enabled,
        "trade": settings.trading.trade_enabled,
        "showcase": features.showcase_enabled,
        "ranking": features.ranking_enabled,
        "records": features.records_enabled,
        "achievements": features.achievements_enabled,
        "weekly": features.weekly_competitions_enabled,
        "cosmetics": features.achievements_enabled or features.weekly_competitions_enabled,
        "dispatch": features.dispatch_enabled,
        "tour": features.tour_enabled,
        "battle": features.battle_enabled,
        "dispatch_store": features.store_enabled and features.dispatch_enabled,
        "tour_store": features.store_enabled and features.tour_enabled,
        "battle_store": features.store_enabled and features.battle_enabled,
        "feature_store": features.store_enabled
        and (features.dispatch_enabled or features.tour_enabled or features.battle_enabled),
        "loot": features.battle_enabled and features.catching_enabled,
        "batch": features.selling_enabled or features.cooking_enabled,
        "gameplay": features.catching_enabled or features.cooking_enabled or features.eating_enabled,
    }


def _available(topic: HelpTopic, gates: dict[str, bool]) -> bool:
    return gates[topic.gate] if topic.gate else any(gates[line.gate] for line in topic.commands)


def _visible_notes(topic: HelpTopic, gates: dict[str, bool]) -> list[str]:
    return [
        note.text if isinstance(note, HelpLine) else note
        for note in topic.notes
        if not isinstance(note, HelpLine) or gates[note.gate]
    ]


def _directory(gates: dict[str, bool]) -> list[str]:
    available = [name for name, topic in _TOPICS.items() if _available(topic, gates)]
    return ["、".join(available[index : index + 4]) for index in range(0, len(available), 4)]


def _index(gates: dict[str, bool]) -> str:
    entries = (
        HelpLine("/抓猪 — 抓猪与当前额度", "catch"),
        HelpLine("/猪猪背包 — 查看猪猪；/猪猪图鉴 — 收集进度", "inventory"),
        HelpLine("/做菜 猪名 — 选择原料做菜", "cook"),
        HelpLine("/吃菜 菜名 — 使用美食效果", "eat"),
        HelpLine("/美食背包 — 查看美食", "food_inventory"),
        HelpLine("/猪猪商城 — 商品、升级与价格", "store"),
    )
    lines = ["【抓猪帮助·主菜单】"]
    for entry in entries:
        if gates[entry.gate]:
            # 图鉴和背包是两个开关，不能因合并显示而绕过其中一个。
            text = entry.text
            if entry.gate == "inventory" and not gates["catalog"]:
                text = "/猪猪背包 — 查看猪猪"
            lines.append(text)
    if gates["catalog"] and not gates["inventory"]:
        lines.append("/猪猪图鉴 — 收集进度")
    lines.extend(
        (
            "",
            "名称可直接使用；名称#编号精确指定，收藏资产不被消耗。",
            "【分功能帮助】输入 /抓猪帮助 功能名",
            *_directory(gates),
            "例如：/抓猪帮助 道具；返回主菜单：/抓猪帮助",
            "方括号为可选项，尖括号为需替换的内容，不要连括号输入。",
        )
    )
    return "\n".join(lines)


def _configured_notes(topic: str, settings: PigCatcherConfig, gates: dict[str, bool]) -> list[str]:
    if topic == "抓猪":
        catching = settings.catching
        hours = "、".join(f"{hour:02d}:00" for hour in catching.quota_refresh_hours)
        timezone = catching.daily_reset_timezone
        zone = "北京时间" if timezone == "Asia/Shanghai" else timezone
        return [
            f"当前配置：基础每时段{catching.daily_limit}次，冷却{catching.cooldown_seconds:g}秒。",
            f"刷新时间：{zone} {hours}；实际额外额度以自己的结果卡为准。",
        ]
    if topic == "做菜" and gates["cook"]:
        return [f"当前做菜冷却：{settings.cooking.cook_cooldown_seconds:g}秒。"]
    if topic == "商城" and gates["store"]:
        economy = settings.economy
        prices = [
            "【当前升级价格·逐级猪币】",
            "猪饲料：" + " / ".join(map(str, economy.feed_upgrade_prices)),
            "厨具：" + " / ".join(map(str, economy.cookware_upgrade_prices)),
            "【当前道具·猪币/件】",
        ]
        prices.extend(f"{item.display_name} {item.price}：{item.effect_summary}" for item in ITEM_DEFINITIONS)
        enabled_feature_gates = {
            "派遣": gates["dispatch_store"],
            "巡演": gates["tour_store"],
            "对战": gates["battle_store"],
        }
        for system, label in FEATURE_SHOP_SYSTEM_LABELS.items():
            if not enabled_feature_gates[label]:
                continue
            prices.append(f"【{label}独立商城·猪币/件】")
            prices.extend(
                f"{product.display_name} {product.unit_price}：{product.effect_summary}"
                for product in FEATURE_SHOP_PRODUCTS
                if product.system is system
            )
        return prices
    if topic == "交易" and gates["trade"]:
        trading = settings.trading
        return [f"当前交易邀请{trading.offer_expiry_minutes:g}分钟过期；单笔最高{trading.max_trade_price}猪币。"]
    return []


def format_help(topic: str = "", *, settings: PigCatcherConfig | None = None) -> str:
    """生成当前配置下的公开帮助，未知主题与“全部”也不会刷出全量指令。"""

    effective = settings if settings is not None else PigCatcherConfig()
    gates = _gates(effective)
    # 仅显示一个短而安全的提示，不能反射无限长度文本或群通知标记。
    normalized = str(topic or "").strip()
    normalized = _TOPIC_ALIASES.get(normalized, normalized)
    if normalized in _INDEX_ALIASES:
        return _index(gates)
    definition = _TOPICS.get(normalized)
    if definition is None:
        label = "".join(char for char in normalized[:32] if char.isprintable())
        label = label.translate(str.maketrans({"@": "＠", "[": "［", "]": "］", "<": "＜", ">": "＞"}))
        suffix = "…" if len(normalized) > 32 else ""
        return f"未知帮助主题：{label}{suffix}\n\n{_index(gates)}"
    if not _available(definition, gates):
        return f"【抓猪帮助·{normalized}】\n该功能当前未启用。\n返回主菜单：/抓猪帮助"

    lines = [f"【抓猪帮助·{normalized}】"]
    lines.extend(line.text for line in definition.commands if gates[line.gate])
    lines.extend(("", "【规则与提示】", *_visible_notes(definition, gates)))
    configured = _configured_notes(normalized, effective, gates)
    if configured:
        lines.extend(("", *configured))
    related = [name for name in definition.related if _available(_TOPICS[name], gates)]
    if related:
        lines.extend(("", "相关：" + "；".join(f"/抓猪帮助 {name}" for name in related)))
    lines.append("返回主菜单：/抓猪帮助")
    return "\n".join(lines)
