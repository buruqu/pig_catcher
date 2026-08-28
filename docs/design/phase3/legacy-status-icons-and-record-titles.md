# 旧页面状态图标与纪录称号接线

日期：2026-08-28。范围仅为隔离的 `pig_catcher-2.0-dev`，未上线、未发公告、未连接正式机器人。

## 实际覆盖

| 页面 | 本次接入 |
| --- | --- |
| 玩家档案 | 猪币、等级、猪/菜数量、图鉴、饲料、厨具、抓猪、展示位等小图标；保留既有真实佩戴外观 |
| 猪猪背包 | 收藏、占用、巨物/迷你、双顶、体长/重量、描述标签；隐私、动画、缺图的独立占位 |
| 美食背包 | 收藏、份量重量；隐私、动画、缺图的独立占位 |
| 猪猪图鉴 | 未发现、未发现群专属、授权撤回、缺图、动画分开；联动收集与描述标签图标 |
| 美食图鉴 | 同上；未公开或已撤权的效果文字不额外揭示 |
| 猪猪纪录 | 品种纪录、全群绝对纪录、巨物目击的小图标，以及真实纪录/目击持有者佩戴称号 |
| 今日巨物 | 体型/重量双榜、空状态、图片隐私提示；按原始抓取者显示真实佩戴称号 |

这不是猪猪和菜品原图重绘。品质仍由真实 `rarity` 输出原有 1～6 个星号，编号、数值、榜单排序、计数及原动画媒体槽保持原业务含义。

## 图标 API 与语义

`pig_catcher/rendering/asset_icons.py` 的 `asset_icon(key)` 是独立白名单入口，已注册至 Jinja。共有 27 个规范键：

- 17 个本次原创 SVG：`giant`、`mini`、`dual-giant`、`double-top`、`double-mini`、`favorite`、`busy`、`protected`、`private`、`hidden`、`unseen`、`missing`、`animated`、`length`、`weight`、`record`、`tag`。
- 10 个白名单映射复用既有原创功能图标：猪、菜、图鉴、猪币、饲料、厨具、抓猪、展示位、联动收集、等级。
- SVG 全为本地固定路径结构，不用 Unicode 代替图形，不接收任意图片地址或 SVG 文本。未知标签只显示中性吊牌图案，标签文字由 Jinja 正常转义。
- 本地图形缓存上限 32 项，无文件读取、外网请求、隐藏图片预读。
- `asset_components.html` 提供 `asset_chip`、`asset_media_state`、`asset_body` 三个小宏；调用方先决定授权及是否展示事实。

“双项巨物”和“双顶壮硕”有意使用不同图案：前者是已有绝对体长/重量标准，后者是同品种区间的双高分位；双顶迷你也与普通迷你分开。背包从已存分位值投影：双顶壮硕为体长 ≥0.92 且重量 ≥0.88；双顶迷你为体长 ≤0.08 且重量 ≤0.15。阈值沿用已有成就/社会规则，这里只作展示，不发成就、不改生成规则。授权撤回时不投影这些额外标签。

收藏心形同时保留“已收藏”文字；占用固定使用沙漏图形，状态文字可包含派遣/巡演阶段。没有专门保护字段的页面不会为了展示盾牌而虚构一个新状态。

## 真实账号与只读查询

1. `records_page`、`global_records`、`giant_sightings` 的既有 SELECT 补取已经存在的 `player_id`；不改 WHERE、排序、计数或数据库结构。
2. `RecordEntry` / `GiantSightingEntry` 经 adapter 转入可选 `player_id`；`DailyGiantEntry` 原本已有该字段，直接传递。
3. `handle_records` 将普通纪录、绝对纪录、目击里的 ID 去重；`handle_daily_giants` 将两个榜里的 ID 去重。每个指令只调用一次既有 `cosmetics_for_players` 批量只读查询，空集合不查询。
4. 仅成就或周榜外观功能启用时补充佩戴称号；无外观、旧 DTO 无 ID、未登记称号均安全不展示。
5. 只按完整、已限定群作用域的稳定玩家 ID 查询；不按昵称猜账号。今日榜沿用原抓取回执 ID，不改成现持有者；赠送不使历史抓取人的称号被接收者取代。
6. 内部 `player_id` 不进入公开文本或图片 alt。显示名称仍是原 DTO 提供的群昵称/显示名，称号名称与图像走已有严格外观注册表。

## 实际出图发现并修复

旧页面的 `.game-sheet` 没有相对定位，且部分页面没有 `data-achievement-frame`，但 `achievement_strip` 会放入绝对定位的 `.cosmetic-edge`。长背包曾因此把底部装饰画在浏览器视口中段。`cosmetic.css` 补充 `.game-sheet{position:relative}`，将边框安全锚定到完整卡片；不会改变媒体槽尺寸，也不依赖示意用的旧 `::after` 装饰。

## 验证与复现

专门测试：`tests/test_asset_status_art.py`，46 项。覆盖 27 键 SVG 结构及输入白名单、未知值、语义区分、分位边界、私有素材不读取、品质星数不变、原文件哈希、同昵称/跨群隔离、历史资产转移、成就或仅周榜开关，以及每条指令一次批量查外观。

结合 `tests/test_rendering.py`、`tests/test_social.py` 和完整三轮插件指令流程测试，最终结果为 **88 passed**；本次修改的 Python 模块全部通过 Ruff。

离线视觉工具：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/accept_asset_status_views.py --output artifacts/legacy-status-icons-next
```

工具使用合成 DTO、5 张已明确为 common 的本地正式素材、独立无头 Chromium；阻止 HTTP/HTTPS，不访问任何数据库或用户浏览器，不调用机器人。输出目录必须不存在。

最终证据在 `artifacts/legacy-status-icons-r3/`：

- 7 类非空页面、3 类空状态、1 张原生图标图谱，共 **11 张实际图片**。
- 页面 HTML、10 张 320px 宽缩略图、完整 contact sheet 与 `report.json`。
- 11 页 `clippedText / outside / brokenImages / clippedMedia` 均为空；5 张源素材的 SHA-256 前后相同。
- 已人工查看纪录全图、背包全图/320px 图、今日巨物 320px 图及图标图谱；长昵称、称号、占用文字不遮挡数值。

此处是本轮视觉与针对性隔离回归结果，不替代最终全部玩法验收，也不表示生产已经部署。
