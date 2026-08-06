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
| `uat_production_recovery.py` | 发送失败、锁库、缺图、备份恢复和重启幂等 |

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

所有脚本都可先加 `--help` 查看参数。输出统一写到被 Git 忽略的 `artifacts/`，不要把
验收图片、临时数据库或运行日志当作正式素材保存。
