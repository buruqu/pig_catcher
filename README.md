# MaiBot 抓猪插件

这是“抓猪插件”的独立 MaiBot 插件仓库。当前正式版本为 `1.2.6`，基础六轮开发、经济成长扩展、群素材强隔离与六星定制菜一对一结算已经完成。

## 当前状态

- 插件 ID：`local.pig-catcher`
- 目标 MaiBot：`1.0.12`
- 目标 SDK：`maibot-plugin-sdk >=2.7.0,<3.0.0`
- Python：`>=3.12`，本机验收环境 `3.14.4`
- 数据协议：Schema `7`、Asset Manifest `4`、Ruleset `8`
- 交付阶段：`6`；当前组件为 29 个显式 `COMMAND`，不注册普通消息监听、Tool 或 LLM
- 当前群聊命令：`/抓猪帮助`、`/抓猪`（别名 `/抓群友`）、`/抓猪档案`、
  `/抓猪详情`、`/猪猪背包`、`/猪猪图鉴`、`/猪猪纪录`、`/使用道具`、`/取消道具`、
  `/做菜`、`/美食详情`、`/美食背包`、`/美食图鉴`、`/吃菜`（别名 `/使用美食`）、
  `/猪猪商城`、`/购买`、`/升级`、`/售卖猪猪`、`/售卖美食`、`/批量售卖`、`/猪币账本`、
  `/猪猪赠送`、`/美食赠送`、`/猪猪交易`、`/美食交易`、`/接受交易`、
  `/拒绝交易`、`/取消交易`、`/我的交易`、`/设置展示`、`/猪猪排行`

帮助保持纯文字，便于直接复制命令；已开放的业务结果优先发送白色、淡粉红图片，
图片渲染或发送失败时降级为完整文字，不回滚已提交的抓取、做菜、使用或经济结算。
默认抓猪基础概率为 `40% / 30% / 17% / 8% / 4% / 1%`；无当前群授权六星
素材时，最后 `1%` 转入五星。

## 已完成功能

- Manifest v2、SDK 生命周期、配置热更新和简体中文 WebUI 配置模型
- 群与用户黑白名单，黑名单优先
- 插件自有 SQLite Schema 7、逐级迁移、外键、WAL、显式事务和在线备份
- 消息 ID 幂等键与 `pending -> claimed -> sent/failed` 一次发送回执
- 素材 Manifest、实际媒体格式、逐帧解码、路径、尺寸、重复 ID 和授权校验
- 1 至 5 星公共素材与 6 星群专属素材的数据边界、授权和撤回
- 原子素材目录发布及过期暂存目录清理
- 白色、淡粉红本地 HTML/CSS 模板、隐私占位和 PNG 输出校验
- 122 项正式模板：95 只猪、27 道美食，其中 9 项动画、12 项群专属；两个授权群各自仍可见 92 只猪和 24 道美食
- GIF 与动画 WebP 逐帧卡片合成，保留帧数、时长和循环，不改写原始素材
- 12 只 BanG Dream 联动猪的角色、乐队、官方资料与固定 `X/5` 收集进度；Afterglow 与 Poppin'Party 均已完整收集 `5/5`
- 121 份正式媒体检查、9 项逐帧动画检查及两个群的六星可见性实测
- 图片生成或发送失败后的纯文字降级服务
- 启用素材文件缺失时的白粉占位图片，数据与结算不受影响
- 可替换随机源、时钟、身份、选择器和规则版本接口
- 六档真实抓取、群内六星资格、缺失六星权重转入五星及随机快照
- 相关体型与重量、直观体态标签、官方价值、猪币、经验、数值等级、展示称号和群纪录；抓猪与做菜结果直接展示等级进度
- 数值等级提供公开、封顶的抓猪与普通做菜概率加成：每 4 级形成一档，`Lv.21` 达到上限；荣誉称号仍只展示
- 北京时间自然日每群每人最多抓 22 次、成功抓取后冷却 20 秒，以及跨午夜语义
- 背包、详情、图鉴、档案和纪录查询
- 猪猪与美食图鉴不再分页，按一至六星分区在一张完整长图中列出全部可见条目
- 白名单群的群专属六星在发现前计入图鉴并显示保密占位，其他群完全不可见
- 8 种道具定义；抓猪与做菜道具均可装备、取消，并只在兼容动作成功提交后原子消耗
- 六档做菜矩阵、肥瘦食谱池、厨具加成，以及六星猪基础 `90% 五星 / 10% 六星` 规则；若产出六星，成品只能是该猪模板绑定的同群定制六星菜
- 美食详情、背包、图鉴和食用；低星菜提供主要经验收益，高星菜可排队一次性抓猪、做菜、品质、体型或额外次数效果
- 猪猪商城单页展示全部 8 种消耗品与 2 种永久升级；消耗品用 `/购买`，猪饲料与厨具用 `/升级`，均支持 `Lv.0-5`，并列出每一级概率数值
- 猪与美食按官方价值售卖；省略编号时自动处理最低价值低星资产，`/批量售卖` 可原子回收全部未锁定 1 至 3 星资产
- 所有猪币变化写入不可变账本并可实时对账
- 当前群原子赠送、五分钟双方确认交易、资产锁、过期自动解锁和双边零和账本
- 猪猪/美食展示位与综合、抓猪、美食、价值、巨物、数量、猪币七类群排行
- 特小猪 `4-16 cm / 0.35-6 kg`、大象 `120-260 cm / 350-1800 kg` 专属体格
- `NEW`、特殊体型评价、全群绝对体型/重量纪录和巨物目击永久留档
- 改变状态命令的进程内、并发和插件重启幂等保护
- 每小时生产巡检 SQLite 完整性、全库账本、122 个启用模板媒体和过期报价
- 抓取、档案、猪与美食详情、背包、图鉴、纪录、商城、账本和经济回执的白粉图片
- 单猪和单道美食 GIF/动画 WebP 保持动态；背包、图鉴和排行榜使用不超过 256px 的确定性 WebP 预览，原始素材字节不变
- pytest、Ruff、编译、严格 Manifest、运行中宿主重启、Chromium、隔离命令 UAT 和备份恢复演练

## 正式运行

生产默认保持每群每人每天成功抓猪 `22` 次、每次成功后冷却 `20` 秒。自动备份每
`24` 小时执行一次并保留最近 `7` 份；维护任务不会静默修复账本或素材异常，只会记录
清晰日志并保留数据。

第六轮已完成正式素材隔离副本上的图片发送失败、数据库写锁、素材缺失、重启恢复、
双群隔离、账本对账和备份恢复演练。按用户决定，真实 QQ 群内的人机命令回归由用户
上线后自行执行，不作为自动化结果冒充记录。

正式美食素材已配置可验证的高星效果。效果在吃菜事务中进入持久队列，插件重启后仍在；
只在下一次兼容动作成功提交时消耗。额外抓猪次数只在超过基础每日额度后扣除，并在
北京时间次日零点失效。

## 开发验证

建立独立开发环境并运行：

```powershell
uv sync --all-groups --locked
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q plugin.py pig_catcher tests tools
.\.venv\Scripts\ruff.exe check plugin.py pig_catcher tests tools
.\.venv\Scripts\python.exe tools\accept_complete_catalogs.py `
  --data-dir C:\path\to\plugin-data --output artifacts\complete-catalogs `
  --scope-id qq:group-id
```

正式版社交流程回归与第六轮故障恢复 UAT：

```powershell
uv run python .\tools\accept_social_and_ranking_views.py `
  --data-dir C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher `
  --output .\artifacts\social-and-ranking-views

uv run python .\tools\uat_social_and_rankings.py `
  --data-dir C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher `
  --output .\artifacts\production-social-regression

uv run python .\tools\uat_production_recovery.py `
  --data-dir C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher `
  --output .\artifacts\production-readiness
```

生成的本地验收图、隔离数据库和报告位于忽略目录 `artifacts/`，不会进入插件发布包。
可审计的正式清单定义保存在 `catalogs/formal/pig-and-food-definitions.json`；本机当前素材包
保存在 Git 忽略目录 `asset_library/current/`，运行副本位于 `ctx.paths.data_dir`，避免
群专属素材进入公共历史。人工查找入口见根目录 [目录导航.md](目录导航.md)。

## 设计文档

- [人工目录与素材导航](目录导航.md)
- [需求来源与追溯矩阵](docs/00-requirement-traceability.md)
- [产品需求基线](docs/01-product-requirements.md)
- [领域与数据模型](docs/02-domain-and-data-model.md)
- [命令与交互协议](docs/03-command-and-interaction.md)
- [概率、数值与经济系统](docs/04-economy-and-probability.md)
- [视觉与素材规范](docs/05-visual-and-asset-spec.md)
- [技术架构设计](docs/06-technical-design.md)
- [开发路线、验收与待确认项](docs/07-roadmap-and-acceptance.md)

## 不变原则

1. 只响应显式斜杠命令，不监听普通聊天，不使用 LLM。
2. `/抓猪帮助` 保持可复制纯文字；后续业务结果优先使用图片并提供文字降级。
3. 视觉以白色和淡粉红为主，不复制参考图的深蓝主题、水母素材、布局或文案。
4. 1 至 5 星使用公共素材；6 星按平台和群隔离，必须授权且支持撤回。
5. 金币、资产、料理、售卖和交易必须在事务中完成，并由消息回执保护幂等。
6. 只使用 MaiBot SDK 公共接口，不修改 MaiBot 核心代码。

## Codex Skill

后续开发使用：

```text
C:\Users\Administrator\.codex\skills\maibot-pig-catcher
```

Skill 名称为 `$maibot-pig-catcher`，会先读取当前阶段和设计文档，再执行素材、玩法、渲染、测试或实机验收任务。
