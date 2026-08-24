# 开发与验收工具

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

## 命令级 UAT

| 脚本 | 用途 |
| --- | --- |
| `uat_catching_and_collection.py` | 正式素材下的抓取与收藏流程 |
| `uat_cooking_and_economy.py` | 做菜、道具、商城和经济流程 |
| `uat_social_and_rankings.py` | 双用户、双群、交易和排行流程 |
| `uat_production_recovery.py` | 当前组件/Schema 基线、发送失败、锁库、缺图、备份恢复和重启幂等 |

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

```powershell
uv run pytest -q
uv run ruff check .
uv run python -m compileall -q pig_catcher plugin.py tools tests
uv lock --check
git diff --check
```

1.25.0 继续在 `tests/test_rendering.py` 验证图片排队背压、WebP 缓存和动画内存预算；Schema 30
查询计划、在线备份并发、统一备份保留、维护节流和旧素材引用保护在
`tests/test_database_and_receipts.py` 验证，Schema 31 收藏迁移与名称直选/保护规则由数据库、
经济和社交测试覆盖。不要把这些单元/集成门禁写成已经完成的真实 QQ 群 UAT。

批量保留相关回归同时覆盖批量售卖、批量做菜、联动猪默认保护、按模板最高价值保留、
仅开启做菜时的开关命令，以及四个 QQ 群作用域的内容同步。
