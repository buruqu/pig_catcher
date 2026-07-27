# MaiBot 抓猪插件

这是“抓猪插件”的独立项目仓库。当前处于第一轮设计阶段，只保存经过整理的产品、领域、交互、经济、视觉、技术和验收基线。

## 当前状态

- 工作名称：抓猪插件
- 目录名称：`pig_catcher`
- 暂定插件 ID：`local.pig-catcher`
- 目标宿主：MaiBot `1.0.12`
- 目标 SDK：`maibot-plugin-sdk 2.7.x`
- 本机 UAT Python：MaiBot `uv` 环境 `3.14.4`
- 当前阶段：设计完成，运行框架尚未创建

本轮有意不创建 `_manifest.json`、`plugin.py`、配置文件、数据库和素材目录。第二轮收到正式素材后，再按设计文档建立完整插件框架，避免占位代码和临时素材变成长期包袱。

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

1. 只响应明确的斜杠命令，不监听普通聊天消息，不使用 LLM。
2. 除 `/抓猪帮助` 返回可复制纯文字外，所有业务结果优先返回渲染图；渲染失败必须有可读文字兜底。
3. 视觉以白色和淡粉红色为主，参考图只用于分析信息层级，不复刻主题、素材、布局或装饰。
4. 1 至 5 星使用公共素材库；6 星使用按群隔离且经授权的定制素材库。
5. 金币、物品、交易、做菜和售卖都必须经过数据库事务，并具备重复消息幂等保护。
6. 插件只使用 MaiBot SDK 公开接口，不修改 MaiBot 核心代码。

## 文档优先级

发生冲突时按以下顺序处理：

1. 用户最新明确要求
2. `docs/07-roadmap-and-acceptance.md` 中已确认的决策
3. 其他设计文档
4. 代码中的既有行为

第二轮开始前，应先处理文档中的待确认项，并把结论写回设计文档。

## Codex Skill

后续开发使用：

```text
C:\Users\Administrator\.codex\skills\maibot-pig-catcher
```

调用名为 `$maibot-pig-catcher`。Skill 会先读取本仓库的当前阶段与设计文档，再执行框架、素材、玩法、渲染、测试或实机验收任务。
