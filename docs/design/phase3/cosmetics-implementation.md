# 2.0 全量外观制作与接入

阶段：全量制作与接入。开发树隔离于生产；不发布公告、不连接 QQ、不改玩家数据。

## 覆盖口径

作者清单在 `pig_catcher/rendering/assets/ui/cosmetics/definitions.json`。覆盖真实奖励来源的并集：

| 来源 | 涵盖内容 |
| --- | --- |
| 基础成就、隐藏成就已解锁、终极成就 | 原有称号、联动收藏印章、事件徽章和边框 |
| 派遣、巡演、对战、三系统综合 | `ACTIVITY_REWARDS` 全部外观，非仅代表样张 |
| 成就积分里程碑 | 淡粉/金色边框、传说称号、三格徽章展示架 |
| 成就自选宝箱 | 粉金边框与自选徽章 |
| 第1期「抓猪冲刺！！！」 | 称号、赛道边框、1/2/3/10牌 |

共 **83 个稳定奖励身份：31 称号、16 整卡边框、35 徽章和1展示架**。
35徽章内有4个周榜名次牌，采用横向活动牌，不冒充圆章；4–10名共享10牌。
清单保存美术身份和名称，不包含成就触发条件。没有添加无法领取的假奖励。
这里统计的是外观覆盖，不是机制完工数量。复核发现三格徽章展示架目前仍只有单徽章佩戴槽；
此次仅补齐其图形资源，原设计的实际三槽机制尚待补齐，已在第32号交付记录列为上线前缺口。

## 正式资产规格

- 31称号和4周榜牌：每个独立 `1200×360` PNG 成品，精确中文由本地字体绘制；有界 `600×180` WebP。
- 31普通徽章和1展示架：独立原创SVG徽记、`256×256`透明PNG，`128×128` WebP。
- 16整卡边框：`192×192`透明九切片PNG/SVG、`480×600`展示预览和`240×300` WebP。
- 七个原创无字插画母版分别对应收藏、远行、巡演、战斗、终极、第一期活动，以及「雨爱」独立的蓝伞与绣球雨幕；不使用官方队标或角色图作新装饰。
- 母版是原字节保留的实际PNG。白底插画不称作透明；透明仅指成品轮廓、徽章、九切片边缘。
- 每件奖励有明确的主题与原创徽记，颜色不是唯一识别方式。标题和名次为准确程序文字，不依赖生成模型写字。
- 首期四档使用不同外轮廓、章体与色段。以后每期必须独立登记主题/母版；构建器拒绝不同期复用同一母版哈希。
- 原有 `all-giants-dynamic` 等稳定ID保留，不改历史奖励。当前整卡边框为静态透明装饰；没有声称新增动画文件。

## 运行 API 与 Jinja

```python
cosmetic_detail(id_or_existing_name, kind=None, revealed=True, variant="compact")
cosmetic_cards(rewards, revealed=True)
clear_cosmetic_cache()
```

返回字段保留 `name/glyph/color/family`，增加 `id/kind/available/masked/image_data_url/frame_data_url/is_plate/rank/width/height`。
`glyph`保留兼容键但为空，真正图像由PNG/WebP承载。`is_plate`把周榜横牌与普通徽章区分。
`compact`用于一般结果和列表；`detail`仅在独立大图确有需要时读取完整PNG。
名字优先做准确匹配，历史同名称号/边框用`kind`消歧；无kind时优先称号。

`cosmetic_components.html` 提供：

```jinja
{% from 'cosmetic_components.html' import cosmetic_plate, cosmetic_badge, cosmetic_preview, cosmetic_frame %}
{{ cosmetic_plate(value, compact=True) }}
{{ cosmetic_badge(value) }}
{{ cosmetic_preview(value) }}
{{ cosmetic_frame(value) }}
```

参数可用稳定ID、已有准确显示名或已构建的安全视图。CSS由渲染器并入主题。
`cosmetic_plate`接收ID且`compact=False`时使用1200px PNG，保证领奖大牌准确清晰；小榜单预览默认使用600px WebP。
整卡`cosmetic_frame`只加绝对定位、`pointer-events:none`的20px边缘九切片，不产生padding/margin，不移动猪菜动画槽。
宏的alt只用名称，不把内部奖励ID、文件路径、触发条件写进可见图片。

## 安全、隐藏与资源预算

1. 归属/解锁校验属于服务与视图层，只有已确认的佩戴奖励或已解锁奖励才传入。
2. 隐藏未解锁使用 `revealed=False` 或直接不构建外观；该分支在ID解析和图像读取前返回统一无ID占位。
3. 未注册ID、路径输入、未来未设计的活动牌返回无ID/无路径安全空外观，不读文件，不使用001代替。
4. 图片路径只来自已审核manifest且必须位于cosmetics目录，首次读取检查签名、大小和SHA-256。
5. 模块内Base64缓存有 **4MiB / 64项** 双上限，卸载时可显式清理；不用内存缓存整个所有称号原图。
6. 成品缺图保留已授权名称并显示“外观图暂缺”；坏哈希/越界立即报错，不悄悄接受损坏图。
7. `manifest.json`逐文件登记尺寸、格式、实际alpha状态、字节数、SHA-256，以及母版/作者清单/生成脚本来源哈希。
8. 发布文件走现有Git LFS；本地校阅图库和DOM报告只在被忽略的artifacts，不属于玩家公告。

## 复现与验证

在独立开发树运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/build_cosmetic_art.py
.\.venv\Scripts\python.exe -m pytest tests/test_cosmetic_art.py -q
.\.venv\Scripts\python.exe -m ruff check pig_catcher/rendering/cosmetics.py tools/build_cosmetic_art.py tests/test_cosmetic_art.py
```

需要七个审核母版位于`rendering/assets/ui/masters/`，本地无头Chromium与中文字体可用。
默认字体为本机微软雅黑粗体；只使用它进行图片导出、不复制字体文件进仓库。可通过`--browser`/`--font`显式指定。
构建器禁用网络请求，不连接用户现有Chrome会话；核对全部来源字节未变化后才发布manifest。
本地证据：`artifacts/cosmetics-art-v1/index.html`与`layout-check.json`。

专项测试覆盖全量真实奖励并集、逐项原生SVG、83成品与哈希、35独立横牌、透明边缘、隐藏零读图、未知输入、路径越界、坏哈希、名称消歧、缓存预算、Jinja宏和九切片布局不侵占素材槽。
此项验收只证明外观资源与接口；真实命令渲染接线、四群/双Bot、长文、动画与生产迁移仍以整体验收为准。

## 首期周榜接线

`weekly_competition.html` 在第1期展示本期称号与1/2/3/10四档奖励预览，明确标为预览而不是已获奖。
`weekly_competition_award.html` 根据实际结算名次选择独立色段牌；第4–10名使用10牌但仍显示各自真实名次。称号与赛道边框一并展示。
两者均要求`season_number == 1`才读取首期资源。未来期、未登记期不读取首期图片，安全显示当期实际赛事信息；不能借001冒充新活动设计。
榜单长昵称允许换行，奖励名称允许换行，不靠省略号隐藏信息。

## 本轮实测记录

- 全量导出83件外观：99张PNG、83张WebP、99份原创SVG；83件DOM导出检查无文字裁切、越界或缺图，来源哈希未变化。
- `tests/test_cosmetic_art.py`：107项通过，包含六个实际领奖档位、四牌预览、未来期/未知期零读取首期美术；Ruff通过。
- `artifacts/weekly-cosmetics-phase3-r2/report.json`：满榜、空榜、已结算、首名领奖4张真实Chromium图均无裁切/越界/缺图/媒体截断。
- 已逐图查看首期1/2/3/10牌、雨爱、完整领奖图与周榜接触表；首期名次牌准确，雨幕母版没有拉伸。
- 周榜4档小预览改用WebP后，满榜HTML由3,291,175字节降到856,011字节，仍使用独立制作的真实图片。
