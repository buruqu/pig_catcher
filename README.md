# MaiBot 抓猪插件

这是“抓猪插件”的独立 MaiBot 插件仓库。当前版本为 `0.1.0`，已完成第二轮 `2A` 无正式素材框架期。

## 当前状态

- 插件 ID：`local.pig-catcher`
- 目标 MaiBot：`1.0.12`
- 目标 SDK：`maibot-plugin-sdk >=2.7.0,<3.0.0`
- Python：`>=3.12`，本机验收环境 `3.14.4`
- 数据协议：Schema `1`、Asset Manifest `1`、Ruleset `1`
- 当前群聊命令：仅 `/抓猪帮助 [主题]`

玩法命令尚未开放。帮助会列出后续完整命令格式，但会明确标注当前处于 `2A`，不会产生假抓取、假资产或假结算。

## 2A 已完成

- Manifest v2、SDK 生命周期、配置热更新和简体中文 WebUI 配置模型
- 群与用户黑白名单，黑名单优先
- 插件自有 SQLite、逐级迁移、外键、WAL、显式事务和在线备份
- 消息 ID 幂等键与 `pending -> claimed -> sent/failed` 一次发送回执
- 素材 Manifest、PNG/WebP、路径、尺寸、重复 ID 和授权校验
- 1 至 5 星公共素材与 6 星群专属素材的数据边界、授权和撤回
- 原子素材目录发布及过期暂存目录清理
- 白色、淡粉红本地 HTML/CSS 模板、隐私占位和 PNG 输出校验
- 图片生成或发送失败后的纯文字降级服务
- 可替换随机源、时钟、身份、选择器和规则版本接口
- pytest、Ruff、编译、严格 Manifest、宿主加载和 Chromium 视觉验收

## 有意延后

- `2B`：用户提供的正式猪、美食、字体和授权素材
- 第三轮：真实抓猪、属性、背包、详情、图鉴、经验和群纪录
- 第四轮：做菜、吃菜、商城、升级、猪币账本和官方售卖
- 第五轮：赠送、两阶段交易、展示设置和排行榜
- 第六轮：运行中 MaiBot 与真实 QQ 多群全流程验收

## 开发验证

在 MaiBot 环境中运行：

```powershell
C:\Users\Administrator\MaiBot\.venv\Scripts\python.exe -m pytest
C:\Users\Administrator\MaiBot\.venv\Scripts\python.exe -m compileall -q plugin.py pig_catcher tests
C:\Users\Administrator\MaiBot\.venv\Scripts\ruff.exe check plugin.py pig_catcher tests
```

生成的本地视觉验收图位于忽略目录 `artifacts/`，不会进入插件发布包。正式素材进入 `2B` 前也不会进入 Git 历史。

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
