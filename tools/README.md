# 开发与验收工具

当前隔离开发基线：`2.0.0.dev13 / Schema48 / Ruleset41`，84个显式 Command + 1个 HOME_CARD。
本页下方保留各阶段的历史验证记录；历史测试数量不等于本轮全量验收结果。
验收只在独立输出和数据库副本上执行，不启动正式MaiBot、不连接QQ、不对生产库运行迁移。

## 素材

| 脚本 | 用途 |
| --- | --- |
| `build_asset_package.py` | 从完整素材来源构建不可变导入包 |
| `import_asset_catalog.py` | 校验并导入素材包 |
| `accept_asset_cards.py` | 逐项渲染并检查当前运行素材 |
| `accept_complete_catalogs.py` | 生成按品质完整排列的猪猪/美食图鉴 |

## 图片验收

| 脚本 | 用途 |
| --- | --- |
| `accept_catching_and_collection_views.py` | 抓猪、详情、背包、图鉴和纪录 |
| `accept_cooking_and_economy_views.py` | 做菜、美食、商城、升级、售卖和账本 |
| `accept_social_and_ranking_views.py` | 赠送、交易、展示位和排行榜 |
| `accept_display_tags_units.py` | 全猪标签、吨/米、长说明、隐私占位与固定动画槽 |
| `accept_badge_showcase.py` | 一格/三格徽章架、周榜牌、主要结果卡与原动画一致性；12个离线场景 |

第九期仅在隔离2.0中验收。`accept_display_tags_units.py`使用已发布目录与合成视图，
生成13个场景、DOM裁切/越界/坏图诊断、联系表和动画哈希检查；不连接生产库或QQ。
`accept_complete_catalogs.py`支持真实展示标签与动画中帧缩略图；第九期补充后的当前四个scope均应各有187猪/69菜。
本机可显式传入`--browser-executable 'C:\Program Files\Google\Chrome\Application\chrome.exe'`；
仅启动独立无头绘图进程，不接管用户已有浏览器。已有输出目录不可覆盖。

## 命令级 UAT

第三轮 2.0 对战仅在隔离开发目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_battle_rules.py tests/test_battle.py tests/test_battle_safety.py tests/test_battle_plugin.py
.\.venv\Scripts\python.exe tools/accept_battle_views.py --output artifacts/battle-acceptance-next
.\.venv\Scripts\python.exe tools/check_battle_balance.py --samples 3000
```

`accept_battle_views.py` 生成 25 张完整状态流图卡、DOM 诊断与联系表，使用临时合成数据和已有公开素材；
输出路径必须不存在。浏览器是独立无头进程，不连接用户已打开的浏览器、不连生产或 QQ。
`check_battle_balance.py` 对四种强化组合各模拟指定场数，比较观测胜率/回合/连招，不修改实际规则。
第三轮交付时记录为127项对战专项、670项全量回归通过；这是历史结果，完整步骤与接线见 [第三轮交付](../docs/25-battle-implementation-and-acceptance.md)。

| 脚本 | 用途 |
| --- | --- |
| `uat_catching_and_collection.py` | 正式素材下的抓取与收藏流程 |
| `uat_cooking_and_economy.py` | 做菜、道具、商城和经济流程 |
| `uat_social_and_rankings.py` | 双用户、双群、交易和排行流程 |
| `uat_production_recovery.py` | 当前组件/Schema 基线、发送失败、锁库、缺图、备份恢复和重启幂等 |
| `uat_recent_mechanics.py` | 生产数据克隆下的轮盘、领域、苍、赫、虚式茈和全家桶图片结算闭环 |
| `accept_v2_production_clone.py` | 只读源快照、两份独立2.0迁移、逐字段历史保留、成就回填、重开幂等和旧代码恢复验证 |

`uat_catching_and_collection.py` 及复用它的恢复验收按实际导出组件动态校验：
名称唯一、处理器存在且可调用、正则可编译、关键功能路由齐备，以及恰好一个 HOME_CARD。
报告记录实际组件数量，不再把历史命令数写死为加载门禁；当前84+1不代表84个无别名的文字入口。
共享克隆函数用SQLite只读连接加在线备份，不直接复制运行中数据库主文件；拒绝源/目标重叠、
越界数据库文件名及已经存在的目标。`--data-dir` 是只读输入，实际业务只在输出内的克隆运行。

### dev12 数据迁移验收

先通过 `--help` 核对参数。以下示例路径必须替换为已准备的离线数据库副本和匹配的1.x代码目录，
`--output` 必须是本开发仓库 `artifacts/` 下尚不存在的新目录：

```powershell
.\.venv\Scripts\python.exe tools/accept_v2_production_clone.py --help
.\.venv\Scripts\python.exe tools/accept_v2_production_clone.py --source-database "C:/path/to/offline-v1/pig_catcher.sqlite3" --legacy-code "C:/path/to/isolated-v1-code" --output artifacts/v2-clone-next --expected-source-schema 34
```

| 参数 | 语义 |
| --- | --- |
| `--source-database` | 必填；只读源库，连接使用 `mode=ro` 与 `query_only=ON`，永远不是迁移目标 |
| `--legacy-code` | 必填；与源库匹配的旧版插件代码，用于验证拒绝升级库及在升级前副本恢复；不启动机器人 |
| `--manifest` | 默认本仓库 `asset_library/current/assets.json`；只导入验收副本 |
| `--output` | 必填；开发仓库 `artifacts/` 下的独立新目录，不能与源目录重叠 |
| `--expected-source-schema` | 默认34；源版本或账本对账不符时拒绝继续，不修复源库 |
| `--resume-before-import` | 仅核验并继续资产导入/回填之前的同Schema迁移产物；不是任意中断、已回填或已完成目录的覆盖开关 |

产物包含升级前一致快照、`migration-a`/`migration-b` 两份迁移副本、旧版恢复副本、
已验证2.0备份和 `report.json`。逐一检查完整性、外键、余额账本、旧表每个原字段及已批准的规则迁移，
再次打开不得修改数据；回填重跑不得重复发奖。报告只包含计数、摘要和脱敏结果，不输出用户ID或回执正文。
运行前至少预留源库大小七倍的磁盘空余，并另外考虑素材副本空间。

本工具不是部署脚本。不要让2.0数据库类直接打开正式运行库，也不要用旧代码对升级库原地降级；
回退验收使用升级前副本。即使源连接为只读，正式库采样也必须另有明确授权；普通复测使用离线副本即可。

### dev12 徽章图片验收

```powershell
.\.venv\Scripts\python.exe tools/accept_badge_showcase.py --help
.\.venv\Scripts\python.exe tools/accept_badge_showcase.py --output artifacts/badge-showcase-next --browser-executable "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

`--output` 必填且不得已存在；`--browser-executable` 可选，默认上述Windows Chrome路径。
脚本使用合成视图和已发布素材，启动独立无头绘图进程，不打开数据库或用户现有浏览器会话。
12个场景覆盖抓猪、做菜长说明、成就总览、背包、派遣、巡演、对战、展示架、三格空位、
旧一格兼容、三枚周榜牌与原动画。输出完整图片、320像素缩略图、联系表和 `report.json`，
核验槽位数、文字裁切/越界/坏图、区域重叠/过大空隙、原素材哈希及动画帧/时长/循环保真。
该验收证明离线图片布局，不等于QQ真实发图或线上迁移验收。

## 本地存储清理

`cleanup_local_storage.py` 只扫描仓库忽略的 `artifacts/` 直接子项，默认列出最后修改时间超过
7 天的可再生验收产物；必须增加 `--apply` 才会删除。脚本拒绝仓库根、磁盘根、符号链接、
Junction 子项和任何不在精确 artifacts 根下的目标。正式数据库、备份、素材库和临时素材不在
它的作用范围内。

```powershell
python tools/cleanup_local_storage.py
python tools/cleanup_local_storage.py --older-than-days 7 --apply --manifest cleanup.json
```

如果 `artifacts/` 本身配置成专用归档盘的 Junction/符号链接，预览和执行都必须显式追加
`--allow-root-reparse-point`。工具会打印并记录解析后的绝对目标，避免静默清理链接目标。

## 额度运维

| 脚本 | 用途 |
| --- | --- |
| `reset_catch_quota.py` | 先在线备份，再精准重置一个指定群的当前抓猪时段；不接受“全部群”通配 |

黑名单与公告等日常运营优先使用 MaiBot 首页“抓猪运营管理”入口，操作方法见
`docs/09-admin-panel-operations.md`。

`tools/local/` 只保存包含本机群上下文的一次性运维脚本，已被 Git 忽略。它们不是正式
发布工具，不应复制到其他环境直接执行。

最常用命令：

```powershell
uv run python .\tools\reset_catch_quota.py --group-id <群号>
```

成功输出包含群范围、窗口起止、归零次数、受影响玩家、审计事件、备份路径和
`quick_check`。详见 `docs/08-catch-quota-operations.md`。

额度重置备份会与自动备份和其他操作前备份一起进入 `backups/` 的统一保留池，默认总共只
保留最新 7 份。工具输出的备份路径只证明本次操作已创建一致副本；长期发布或处罚保护点应
在核验后复制到插件运行备份目录之外。

所有脚本都可先加 `--help` 查看参数。输出统一写到被 Git 忽略的 `artifacts/`，不要把
验收图片、临时数据库或运行日志当作正式素材保存。

## 全量离线审计

2.0.0.dev9新增 `sync_round9_food_effects.py`（默认校验，`--write`同步仓库两份食品规则JSON）、
`accept_item_bag_views.py`、`accept_food_reward_views.py`。当时按用户要求只做必要检查，完整图片和回归留到最终统一验收；
各脚本使用 `--help` 查看隔离输出参数，不要对生产数据目录运行验收。
dev12进入统一离线验收，同时使用本页的迁移副本与徽章图片工具；具体完成数量和遗留边界以本轮验收报告为准。

```powershell
uv run pytest -q
uv run ruff check .
uv run python -m compileall -q pig_catcher plugin.py tools tests
uv lock --check
git diff --check
```

1.29.0 继续在 `tests/test_rendering.py` 验证渲染/平台发送分级背压、WebP 缓存、动画内存预算和猪币里程碑卡片；Schema 30
查询计划、在线备份并发、统一备份保留、维护节流和旧素材引用保护在
`tests/test_database_and_receipts.py` 验证，Schema 31 收藏迁移与名称直选/保护规则由数据库、
经济和社交测试覆盖；Schema 32 及周四限定、专属做菜、群体术式、虚式幂等和群猪币转账由
领域、抓猪与经济集成测试覆盖；Schema 33、六道菜重平衡、复制猪、六面轮盘与失败返还由
数据库、领域、抓猪和经济测试覆盖；轮盘、群体术式和全家桶的命令级图片结算由
`uat_recent_mechanics.py` 覆盖。不要把这些单元/集成门禁写成已经完成的真实 QQ 群 UAT。

批量保留相关回归同时覆盖批量售卖、批量做菜、联动猪默认保护、按模板最高价值保留、
仅开启做菜时的开关命令，以及四个 QQ 群作用域的内容同步。
