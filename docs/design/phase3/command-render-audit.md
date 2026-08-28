# 阶段 3：指令图片出口审查

> 2026-08-29 dev12补记：当前为 `2.0.0.dev12 / Schema46 / Ruleset40`，
> 84个 Command + 1个 HOME_CARD；新增 `/成就徽章` 的查询及槽位变更均接图片。
> 当前主出口统计为71条玩家图片路由、11条管理文字路由、2条帮助文字路由。
> 下文83条路由及当时测试数量保留为阶段3历史快照；本轮增量和离线验收边界见末节。

## 范围与口径

- 环境：`pig_catcher-2.0-dev`，只检查并补齐开发代码；未连接 QQ、未测试生产、未部署。
- 首次审查：2026-08-28 20:49（Asia/Shanghai）。
- 初始 `plugin.py` SHA-256：`B22DFB7D0B931655FA74084108AA47D4C9ED70B92067313D5BA099E938CEED64`。
- Python AST 枚举 `PigCatcherPlugin` 的全部 `@Command`：**83 条路由**。同一路由的别名、子命令、正则分支不重复计数。
- 本文核对的是“成功业务返回值 → 视图模型 → renderer → 图片交付”的实际接线，不是只数模板。没有把报错、帮助、最后一份菜确认、监管阻止、渲染失败后的文字兜底误算为成功路径缺图。
- 这是本轮开发覆盖检查，**不是最终全量验收**。实际图片布局、长内容、并发、QQ 传输及上线验收仍在最后一轮完成。

阶段3当时补齐结果：**70 条玩家路由的主要成功路径已接图片，另 11 条管理路由和 2 条帮助路由维持文字**，合计83条。下方保留当时逐项证据；dev12新增徽章路由后为71+11+2=84。

## 初始结论

| 分类 | 路由数 | 说明 |
| --- | ---: | --- |
| 主要成功路径接到图片 | 61 | 已调用真实 renderer，仍允许合理文字帮助/确认/错误及渲染兜底 |
| 成功路径混合 | 1 | `/使用成就券`：新活动券有图，旧成就券仍文字 |
| 玩家成功路径纯文字 | 8 | 两种立绘切换、两种批量保留设置、收藏保护、成就佩戴/卸下、成就宝箱 |
| 管理成功路径文字 | 11 | 管理查询 1 条，含持久回执的变更入口 10 条 |
| 纯帮助 | 2 | `/抓猪帮助`、`/猪管帮助`，可复制指令文字合理 |
| 合计 | **83** | 不应将 83 条全部声称为“所有成功功能已出图” |

传统抓猪/做菜/背包/图鉴/商城/赠送/交易、术式/轮盘/特殊吃菜奖励，以及 2.0 的成就总览/详情/排行、周冲榜、派遣、巡演、对战、道具背包均已有实际图片出口。初始遗漏集中在成功设置反馈和少量旧成就奖励操作。

## 全部路由逐项核对（初始快照）

“图”表示成功主路径已接入图片；“文”表示即使 renderer 健康也主动发文字；“混合”表示不同成功分支不一致。表中的名称是现有正则支持的入口，不新增或改名指令。

| # | 指令入口 | 初始出口 | 实际 renderer / 交付或委托路径 |
| --- | --- | --- | --- |
| 1 | `/抓猪帮助 [主题]` | 帮助文字 | `_reply_text`，可复制分主题帮助 |
| 2 | `/重置` | 管理文字 | `_deliver_text_receipt` |
| 3 | `/猪管帮助` | 帮助文字 | `_reply_text` |
| 4 | `/猪管监管 [案件]` | 管理文字 | `_reply_text`，查询列表/详情 |
| 5 | `/猪管监管解除` | 管理文字 | `_deliver_text_receipt` |
| 6 | `/猪管发币` | 管理文字 | `_handle_admin_target_coin_adjustment` → text receipt |
| 7 | `/猪管扣币` | 管理文字 | 同上 |
| 8 | `/猪管全员发币` | 管理文字 | `_handle_admin_all_coin_adjustment` → text receipt |
| 9 | `/猪管全员扣币` | 管理文字 | 同上 |
| 10 | `/猪管发放猪猪` 等发放别名 | 管理文字 | `_deliver_text_receipt` |
| 11 | `/猪管删除猪猪` 等删除别名 | 管理文字 | `_deliver_text_receipt` |
| 12 | `/猪管黑名单` | 管理文字 | 查询 `_reply_text`；变更 text receipt |
| 13 | `/猪管重置玩家` | 管理文字 | `_deliver_text_receipt` |
| 14 | `/重置额度` | 图 | `render_group_event`，已结算群重置回执 |
| 15 | `/抓猪`、`/抓群友` | 图 | `_render_pig_card`；术式截获时 `render_group_event` |
| 16 | `/切换 猪保千 [编号]` | 文 | `_reply_text`，成功未展示新立绘 |
| 17 | `/切换 初华猪 [编号]` | 文 | `_reply_text`，成功未展示新立绘 |
| 18 | `/领域展开 伏魔御厨子` | 图 | `_activate_group_technique_command` → `render_group_event` |
| 19 | `/术式顺转 苍` | 图 | 同上 |
| 20 | `/术式反转 赫` | 图 | 同上 |
| 21 | `/虚式 茈` | 图 | `render_group_event`，结果含获得猪猪 |
| 22 | `/开启批量保留` | 文 | `_set_batch_keep` → `_reply_text` |
| 23 | `/关闭批量保留` | 文 | 同上 |
| 24 | `/收藏`、`/取消收藏` 猪猪/美食 | 文 | `_deliver_text_receipt` |
| 25 | `/抓猪档案` | 图 | `render_profile` |
| 26 | `/猪猪详情`、`/抓猪详情` | 图 | `_render_pig_card` |
| 27 | `/猪猪背包` | 图 | `render_inventory` |
| 28 | `/猪猪图鉴` | 图 | `render_catalog` |
| 29 | `/猪猪纪录` | 图 | `render_records` |
| 30 | `/今日巨物` | 图 | `render_daily_giants` |
| 31 | `/使用道具` | 图 | `render_item_receipt` |
| 32 | `/取消道具` | 图 | `render_item_receipt` |
| 33 | `/做菜` | 图 | `_render_food_card` |
| 34 | `/批量做菜` | 图 | `render_batch_cook`，无可做原料也经 query 图片 |
| 35 | `/美食详情` | 图 | `_render_food_card` |
| 36 | `/美食背包` | 图 | `render_food_inventory` |
| 37 | `/美食图鉴` | 图 | `render_food_catalog` |
| 38 | `/转轮盘` | 图 | `render_group_event` |
| 39 | `/吃菜`、`/使用美食` | 图 | `_deliver_eat_result`：普通经济票据、群事件、特殊事件或 `render_food_rewards` |
| 40 | `/是`、`/否` | 图/合理文字 | 确认吃菜委托上项；取消、过期/无待确认为文字 |
| 41 | `/猪猪商城` | 图 | `render_store` |
| 42 | `/购买`（商城限定物品） | 图 | `render_economy_receipt` |
| 43 | `/升级`（饲料/厨具） | 图 | `render_economy_receipt` |
| 44 | `/售卖猪猪` | 图 | `render_economy_receipt` |
| 45 | `/售卖美食` | 图 | `render_economy_receipt` |
| 46 | `/批量售卖` | 图 | `render_economy_receipt` |
| 47 | `/猪币账本` | 图 | `render_ledger` |
| 48 | `/猪猪赠送`、`/美食赠送` | 图 | `render_economy_receipt`；监管阻止为文字，不是赠送成功缺图 |
| 49 | `/猪猪交易`、`/美食交易` | 图 | `render_economy_receipt` |
| 50 | `/接受交易` | 图 | `_handle_trade_resolution` → `render_economy_receipt` |
| 51 | `/拒绝交易` | 图 | 同上；明确结束交易也是结算结果图 |
| 52 | `/取消交易` | 图 | 同上 |
| 53 | `/我的交易` | 图 | `render_trade_list` |
| 54 | `/设置展示` | 图 | `render_economy_receipt` |
| 55 | `/猪猪成就` | 图 | `render_achievement_overview` / `render_achievement_page` |
| 56 | `/成就详情` | 图 | `render_achievement_page` |
| 57 | `/佩戴成就` | 文 | 普通成就和周榜外观两个成功分支均 `_reply_text` |
| 58 | `/取消佩戴成就` | 文 | `_reply_text` |
| 59 | `/使用成就券` | 混合 | 新活动券 `render_dispatch`；旧券 `_reply_text` |
| 60 | `/重铸编号` | 图 | `_deliver_item_bag_result` → `render_dispatch` |
| 61 | `/打开成就宝箱` | 文 | `_reply_text` |
| 62 | `/领取成就纪念猪` | 图 | `_render_pig_card` |
| 63 | `/成就排行` | 图 | `render_achievement_ranking` |
| 64 | `/抓猪线`、`/zzx` | 图 | `render_weekly_competition`，可触发周榜奖励图 |
| 65 | `/猪猪排行` | 图 | `render_ranking` |
| 66 | `/成就奖励` | 图 | `_activity_reward_command` → `render_dispatch` |
| 67 | `/道具背包` 等背包入口 | 图 | `_item_bag_command` → `render_dispatch` |
| 68 | `/使用奖励券` | 图 | 同上 |
| 69 | `/猪猪派遣` | 图 | `_dispatch_command` → `render_dispatch` |
| 70 | `/派遣背包` | 图 | 同上 |
| 71 | `/派遣游记` | 图 | 同上 |
| 72 | `/派遣奇遇` | 图 | 同上 |
| 73 | `/我的猪猪乐队`、`/组建乐队`、`/乐队编队`、`/乐队练习` | 图 | `_tour_command` → `render_tour` |
| 74 | `/猪猪巡演`、`/巡演继续`、`/巡演一键` | 图 | 同上 |
| 75 | `/巡演游记` | 图 | 同上 |
| 76 | `/巡演联演` | 图 | 同上 |
| 77 | `/战斗猪` | 图 | `_battle_command` → `render_battle` |
| 78 | `/比划比划` | 图 | 同上 |
| 79 | `/出招数` | 图 | 同上 |
| 80 | `/出招` | 图 | 同上 |
| 81 | `/对战状态` | 图 | 同上 |
| 82 | `/对战记录` | 图 | 同上 |
| 83 | `/战利品抓猪` | 图 | 同上 |

## 补齐边界与可复用结果

本轮接着补齐 #16、17、22、23、24、57、58、59、61 共 9 条玩家路由，保持指令名称、核心业务、随机数和经济结算不变。

| 入口 | 可复用数据与约束 |
| --- | --- |
| 收藏/取消收藏 | `FavoriteResult.receipt` 已是持久 `CommandReceipt`；复用回执图片发送，不重新调用 `set_favorite` |
| 两种立绘切换 | 服务返回 `(count, new_variant, message)`，**不是 PigView**，没有 CommandReceipt；用实际返回的新变体和消息做状态卡，不能重复切换来取图片 |
| 批量保留开/关 | 服务返回 `(bool, message)`，没有 CommandReceipt；完成设置后 query 图片交付，不重复设置 |
| 成就/周榜佩戴与卸下 | 使用服务返回的外观信息或已完成的设置事实；不要把隐藏外观 ID 当成用户昵称/展示文案 |
| 旧成就券 | 使用一次业务返回结果；服务操作幂等不等于可为渲染重跑操作，不曝光内部 ticket ID |
| 成就宝箱 | 使用已经结算的结果，显示真正奖励，不因生成图片再次开箱 |

管理边界维持文字：`/重置`、`/猪管监管解除`、四条发/扣币路由、发放/删除资产、黑名单变更、重置玩家，共 **10 条已有持久回执**。后续若需要管理图片可以复用这些回执，但本轮不扩展管理产品行为。`/猪管监管` 和黑名单只读分支不需要伪造交易回执；管理员可复制案件编号的文字查询是合理例外。

## 为什么有些文字不是缺图

- `_deliver_query` 和 `_deliver_receipt` 都会将实际 render callback 交给交付层；当 renderer/图片发送不可用时才回退到 `fallback_text`。不能因代码同时存在 summary 文本便判为文字主出口。
- `_deliver_text_receipt` 没有 renderer callback，属于明确选择文字。本轮只替换应当展示给玩家的成功反馈，不全局改这个方法。
- `_deliver_receipt` 保持先结算、按 receipt 领取发送权、成功标记/失败标记的生命周期；图片故障不能重新扣资产或重抽奖励。
- 最后一份同名美食的 `/是`、`/否` 30 秒确认应继续可复制；帮助、权限不足、功能关闭、参数错误、无库存和监管阻止无需伪装成成功图片。
- 派遣、巡演、对战的“帮助”分支仍发可复制文字；其正常查看、启动、领取、继续、结算等返回真实的 `render_dispatch` / `render_tour` / `render_battle`。
- 伏魔御厨子/苍/赫影响下的抓猪已走 `technique_catch_event_view` → `render_group_event`；并非只为激活指令做图。具体结果正确性仍由最后业务验收验证。
- 吃菜的 947 奖励使用 `render_food_rewards`，常规/群事件/术式专属菜分别走经济票据或群事件图；它们不是只有静态未调用模板。

## 本轮补齐结果

复核时间：2026-08-28 21:55（Asia/Shanghai）。当时 `plugin.py` SHA-256 为 `04F60D26CC66457D4474993A052FC2F009C4F4BDF70757207F0CD3017ECD638A`；其他阶段 3 接入仍在同一开发树进行，该哈希仅定位此次复核快照。

| 路由 | 补齐后的成功图片 | 结算与公开内容边界 |
| --- | --- | --- |
| #16、17 两种立绘切换 | `_deliver_pig_art_toggle` → `_render_pig_card` | 业务只切换一次；render 回调只查询切换后的猪详情。图片失败或查询时资产已转移，回退已完成的切换消息，不重新切换 |
| #22、23 批量保留开/关 | `_deliver_player_status` → `render_economy_receipt` | query 状态图；显示普通猪/菜、联动猪、收藏的真实现有规则，不虚构 CommandReceipt |
| #24 收藏/取消收藏 | 同上，使用已有 `FavoriteResult.receipt` | 复用持久回执、匹配数/变更数/资产种类；保留 claim/send 去重，不重做收藏动作 |
| #57 成就/周榜佩戴 | `_deliver_equipped_cosmetics` → `render_dispatch` 的 `presentation='cosmetics'` 分支 | 只读已佩戴外观，显示真实称号牌、边框、徽章；不是派遣，不启程、不消耗猪猪，不把内部奖励 ID 当文案 |
| #58 卸下外观 | `render_economy_receipt` 状态图 | 普通成就或只开启周榜均允许卸下，奖励库存不删除 |
| #59 旧成就券分支 | `render_economy_receipt` | 取一次激活操作的已保存 ticket ID 映射公开券名，不输出内部 ID；新活动券原图片路径不变 |
| #61 成就宝箱 | `render_economy_receipt` 奖励图 | 使用本次已结算奖励，重试仍显示最初选择；外观奖励映射为公开中文名，不因渲染再次开箱 |

补齐后统计：原 61 条图片主路由 + 8 条纯文字玩家路由 + 1 条旧券混合路由 = **70 条图片主路由**。玩家纯文字成功遗漏和旧券混合出口均已消除。管理 11 条和帮助 2 条保持明确文字例外。

其中无 CommandReceipt 的外观设置、旧券和宝箱使用 query 图片交付，不伪造持久回执。旧券/宝箱仍依赖原有 `achievement_operations` 的业务幂等：同消息不会重复扣券、开箱，但允许重看已结算结果；不应将其与收藏等“已发送回执不再公示”的发送去重混为一谈。立绘切换本轮只补展示，未增加服务层消息幂等协议；渲染回调不会再次调用切换操作。

## 聚焦验证

新增 `tests/test_command_status_rendering.py`，**42 项**参数化用例，覆盖：

- QQ、官方 Bot 1、官方 Bot 2 的批量保留与两种立绘切换；真实切换结果及正常错误文字边界。
- 猪猪/美食收藏与取消收藏，回执重复发送抑制，图片失败后不改变已提交资产状态。
- 全部 7 种旧成就券的图片与公开名，同消息重试不重复激活；关闭成就开关时不额外创建成就档案。
- 全部 4 种成就宝箱，图片正常/失败两种情况；同消息改参数也返回原奖励、不再次扣箱、不泄露外观内部 ID。
- 已解锁隐藏成就“雨爱”的真实外观预览；图片失败仍保留已佩戴状态，预览不启动派遣。
- 只开启周榜时的真实称号/1 牌预览、卸下外观和奖励库存保留；卸下不调用成就补录入口。

执行结果：

```text
python -m pytest tests/test_command_status_rendering.py tests/test_item_bag_plugin.py -q --disable-warnings --maxfail=3
56 passed in 15.12s

python -m pytest tests/test_plugin.py -q -k 'toggle_baogian or batch_keep_commands_toggle_player_preference' --disable-warnings
3 passed, 39 deselected in 1.51s

python -m ruff check plugin.py tests/test_command_status_rendering.py
All checks passed!

python -m pytest tests/test_command_status_rendering.py::test_weekly_only_cosmetics_can_be_previewed_and_removed_without_losing_rewards -q --disable-warnings
1 passed in 1.90s  # 加入“卸下不得触发成就补录”的回归断言后复测
```

测试使用临时 SQLite、真实插件服务/视图与模板接线、假的 MaiBot HTML→PNG/发送端；周榜佩戴场景用受控的已奖励外观设置替身，外观读取/预览/卸下和奖励库存断言均走真实代码。它证明正常图片出口和幂等边界，不证明 Chromium 的最终像素布局或 QQ 在线发图成功。

最后统一验收仍需覆盖：长昵称/长奖励实际布局、更多缩略图和立绘、佩戴与周榜牌组合、并发状态变化、图片发送双失败恢复、QQ 双 Bot 群路由。此文档不代表上述最终验收通过。

## dev12 增量：三格徽章与验收工具（2026-08-29）

新增第84条显式路由 `/成就徽章`；其他83条路由及其文字例外不改变。
支持空参数总览、`/成就徽章 查看 2` 翻页、`/成就徽章 1 徽章名或ID` 佩戴和
`/成就徽章 卸下 1`。仅查看本群本人已拥有的徽章/周榜牌，每页8枚，不暴露未解锁奖励。

真实接线为 `handle_achievement_badges` → `AchievementBadgeService.execute` →
`render_dispatch` 的 `presentation='cosmetics'` 分支。查询走 `_deliver_query`，
佩戴/卸下走持久回执的 `_deliver_receipt(track_progress=False)`，重投不重复变更，
渲染故障不会补发成就、猪币、材料或周榜奖励。派遣视图只作为共用外观渲染载体，不会启动派遣。

默认1格；500成就点里程碑授予三格展示架永久权益后开放第2、3格，不能靠查询补领权益。
同一徽章只能占一格，移动须先卸下；操作不消费徽章、不改其他槽、称号、边框或概率。
旧 `/佩戴成就` 兼容第一格，`/取消佩戴成就` 清除全部外观但保留库存和权益。
Schema46只迁移旧字段中实际持有的第一格徽章，未授权或未知外观不会被补发。

新增 `tools/accept_badge_showcase.py` 使用真实Chromium与正式素材的合成视图检查12个场景：
抓猪、做菜长说明、成就总览、背包、派遣、巡演、对战、完整/空展示架、旧一格、三周榜牌和动画。
报告包含DOM裁切/越界/坏图、槽位数、区域重叠/空隙、320像素缩略图和原动画保真；
素材哈希必须不变，不打开正式库、不接用户浏览器或QQ。

UAT共用组件校验现按导出名称、可调用处理器、正则、必要玩法路由及唯一HOME_CARD动态检查，
不继续硬编码历史命令数。另用 `tools/accept_v2_production_clone.py` 在只读源的独立副本中
检查两次迁移、旧数据保留、重开/回填幂等与旧代码恢复；不得对正式运行库迁移。
真实工具参数见 [开发与验收工具](../../../tools/README.md)。

这里记录新增图片出口、数据边界及验收方法，不代替主线最终报告中的运行结果。
离线渲染通过不等于真实QQ双Bot发送通过；2.0仍未部署，维护窗口和上线需另行确认。
