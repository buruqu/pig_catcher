# MaiBot 抓猪插件

这是“抓猪插件”的独立 MaiBot 插件仓库。当前版本为 `0.3.0`，已完成第三轮“抓猪与收藏”。

## 当前状态

- 插件 ID：`local.pig-catcher`
- 目标 MaiBot：`1.0.12`
- 目标 SDK：`maibot-plugin-sdk >=2.7.0,<3.0.0`
- Python：`>=3.12`，本机验收环境 `3.14.4`
- 数据协议：Schema `3`、Asset Manifest `2`、Ruleset `1`
- 当前组件：9 个显式 `COMMAND`，不注册普通消息监听、Tool 或 LLM
- 当前群聊命令：`/抓猪帮助`、`/抓猪`（别名 `/抓群友`）、`/抓猪档案`、
  `/抓猪详情`、`/猪猪背包`、`/猪猪图鉴`、`/猪猪纪录`、`/使用道具`、`/取消道具`

帮助保持纯文字，便于直接复制命令；已开放的业务结果优先发送白色、淡粉红图片，
图片渲染或发送失败时降级为完整文字，不回滚已提交的抓取或道具结算。

## 已完成功能

- Manifest v2、SDK 生命周期、配置热更新和简体中文 WebUI 配置模型
- 群与用户黑白名单，黑名单优先
- 插件自有 SQLite Schema 3、逐级迁移、外键、WAL、显式事务和在线备份
- 消息 ID 幂等键与 `pending -> claimed -> sent/failed` 一次发送回执
- 素材 Manifest、实际媒体格式、逐帧解码、路径、尺寸、重复 ID 和授权校验
- 1 至 5 星公共素材与 6 星群专属素材的数据边界、授权和撤回
- 原子素材目录发布及过期暂存目录清理
- 白色、淡粉红本地 HTML/CSS 模板、隐私占位和 PNG 输出校验
- 98 项正式素材：83 只猪、15 道美食，其中 9 项动画、4 项群专属
- GIF 与动画 WebP 逐帧卡片合成，保留帧数、时长和循环，不改写原始素材
- 5 只 BanG Dream 联动猪的角色、乐队、官方资料与固定 `X/5` 收集进度
- 98 项逐图检查、9 项逐帧动画检查及两个群的六星可见性实测
- 图片生成或发送失败后的纯文字降级服务
- 可替换随机源、时钟、身份、选择器和规则版本接口
- 六档真实抓取、群内六星资格、缺失六星权重转入五星及随机快照
- 相关体型与重量、肥瘦率、官方价值、猪币、经验、等级和群纪录
- 北京时间自然日次数、跨午夜冷却、背包、详情、图鉴、档案和纪录查询
- 8 种道具定义；已开放抓猪道具的装备、取消和成功抓取后原子消耗
- 改变状态命令的进程内、并发和插件重启幂等保护
- 抓取、档案、详情、背包、图鉴、纪录和道具回执的白粉图片
- 单猪 GIF/动画 WebP 保持动态；列表不抽取静态首帧，改为明确动态标记
- pytest、Ruff、编译、严格 Manifest、运行中宿主热重载、Chromium 和隔离命令 UAT

## 有意延后

- 第四轮：做菜、吃菜、商城、升级、猪币账本和官方售卖
- 第五轮：赠送、两阶段交易、展示设置和排行榜
- 第六轮：真实 QQ 多群全流程、故障注入、备份和恢复演练

商城尚未开放，因此正常玩家当前还没有购买一次性道具的入口；第三轮道具命令和
消耗语义已经完成，可承接第四轮商城写入的库存。

## 开发验证

建立独立开发环境并运行：

```powershell
uv sync --all-groups --locked
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q plugin.py pig_catcher tests tools
.\.venv\Scripts\ruff.exe check plugin.py pig_catcher tests tools
```

第三轮视觉验收与隔离命令 UAT：

```powershell
uv run python .\tools\accept_third_round.py `
  --data-dir C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher `
  --output .\artifacts\third-round-visual

uv run python .\tools\uat_third_round.py `
  --data-dir C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher `
  --output .\artifacts\third-round-uat
```

生成的本地验收图、隔离数据库和报告位于忽略目录 `artifacts/`，不会进入插件发布包。
可审计的 2B 清单定义保存在 `catalogs/2b/`；用户素材原件和运行副本保存在 Git 忽略
目录及 `ctx.paths.data_dir`，避免群专属素材进入公共历史。

## 设计文档

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
