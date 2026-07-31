# MaiBot 抓猪插件

[![Version](https://img.shields.io/badge/version-1.2.6-ff8fb1)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![MaiBot](https://img.shields.io/badge/MaiBot-%E2%89%A51.0.12-ffb6c8)](_manifest.json)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

一个面向 MaiBot 的群聊收集、养成与交易插件。玩家可以抓猪、做菜、收集图鉴、经营猪币、赠送或交易资产，并在当前群查看排行榜。

插件只响应明确的斜杠命令，不监听普通聊天，也不调用 LLM。业务结算使用 SQLite 事务和幂等回执保护；图片渲染或发送失败时会自动降级为完整文字结果，不会撤销已经成功提交的结算。

## 主要功能

- 六档稀有度：一至五星为公共图鉴，六星支持按群授权和完全隔离。
- 抓猪与成长：每日次数、冷却、等级经验、体型重量、稀有度加成和群纪录。
- 做菜与食用：六档菜谱、厨具成长、一次性增益以及持久化效果队列。
- 资产与经济：背包、图鉴、商城、售卖、猪币账本、赠送和双向确认交易。
- 社交玩法：展示位、七类群排行榜、联动收集进度和巨物目击。
- 稳健运行：SQLite WAL、逐级迁移、原子事务、定时备份、完整性检查和幂等消息回执。
- 图片输出：白色与淡粉色卡片、GIF/动画 WebP 支持、紧凑预览及纯文字兜底。
- 六星配对：六星猪产出六星菜时，只能得到该猪绑定的、同一群授权的定制六星菜。

当前协议版本：数据库 Schema `7`、素材 Manifest `4`、规则集 `8`。

## 环境要求

- MaiBot `>=1.0.12,<2.0.0`
- Python `>=3.12`
- `maibot-plugin-sdk >=2.7.0,<3.0.0`
- 推荐使用 [uv](https://docs.astral.sh/uv/) 管理依赖

## 安装

在 MaiBot 的插件目录执行：

```powershell
cd <MaiBot目录>\plugins
git clone https://github.com/buruqu/pig_catcher.git
cd pig_catcher
uv sync --all-groups --locked
```

随后重启 MaiBot。启动日志中应出现插件 ID `local.pig-catcher` 和版本 `1.2.6`。

MaiBot 若已启用插件依赖自动安装，也可以让宿主根据 `_manifest.json` 安装运行依赖；`uv sync` 主要用于独立开发和测试。

## 配置

仓库中的 `config.toml` 是不包含真实群号或管理员账号的安全示例。常用配置如下：

```toml
[access]
group_whitelist = []
group_blacklist = []
user_whitelist = []
user_blacklist = []
admin_user_ids = []

[catching]
daily_limit = 22
cooldown_seconds = 20
rarity_1_weight = 40.0
rarity_2_weight = 30.0
rarity_3_weight = 17.0
rarity_4_weight = 8.0
rarity_5_weight = 4.0
rarity_6_weight = 1.0
```

空白名单表示不额外限制群或用户；黑名单优先。正式部署时请按需填写，并避免把真实群号、管理员账号、运行数据库或私有素材提交到公开仓库。

完整配置项及中文 WebUI 元数据见 [config.toml](config.toml) 与 [_manifest.json](_manifest.json)。

## 素材准备

公开仓库只提供通用代码和公共图鉴元数据，不包含图片、动画及任何群专属六星素材。请仅使用你有权使用和分发的素材。

1. 按 [素材规范](docs/05-visual-and-asset-spec.md) 准备源文件和清单。
2. 构建素材包：

   ```powershell
   uv run python tools\build_asset_package.py `
     --source <素材源目录> `
     --output <素材构建目录>
   ```

3. 导入 MaiBot 为插件分配的数据目录：

   ```powershell
   uv run python tools\import_asset_catalog.py `
     --manifest <素材构建目录>\assets.json `
     --data-dir <MaiBot目录>\data\plugins\local.pig-catcher
   ```

`asset_library/current/`、运行数据库、备份和验收产物均已被 Git 忽略。

### 群专属六星素材

每个群必须拥有独立的素材目录和独立模板记录，即使不同群暂时使用相同图片也不能共用运行路径。定义六星条目时：

- 猪和菜都要设置对应的 `group_scope_id`。
- 每只六星猪通过 `paired_food_template_id` 绑定唯一的同群六星菜。
- Manifest v4 会拒绝跨群、跨稀有度、重复配对或不完整配对。
- 撤销群授权后，该群的六星模板和媒体必须不可见、不可抽取。

仓库中的 [公共目录定义](catalogs/formal/pig-and-food-definitions.json) 有意不包含真实群号和六星私有条目。

## 命令

| 类别 | 命令 |
| --- | --- |
| 入门 | `/抓猪帮助`、`/抓猪`（别名 `/抓群友`）、`/抓猪档案`、`/抓猪详情` |
| 收集 | `/猪猪背包`、`/猪猪图鉴`、`/抓猪记录`、`/设置展示` |
| 道具 | `/使用道具`、`/取消道具` |
| 美食 | `/做菜`、`/美食详情`、`/美食背包`、`/美食图鉴`、`/吃菜` |
| 经济 | `/猪猪商城`、`/购买`、`/升级`、`/售卖猪猪`、`/售卖美食`、`/批量售卖`、`/猪币账本` |
| 社交 | `/猪猪赠送`、`/美食赠送`、`/猪猪交易`、`/美食交易`、`/接受交易`、`/拒绝交易`、`/取消交易`、`/我的交易` |
| 排行 | `/猪猪排行` |

`/抓猪帮助` 始终返回便于复制的纯文字。其余业务命令优先返回图片，渲染不可用时返回同等信息的文字结果。

## 默认规则

- 北京时间自然日内，每群每位玩家最多成功抓猪 `22` 次。
- 每次成功抓取后冷却 `20` 秒。
- 基础稀有度权重为 `40 / 30 / 17 / 8 / 4 / 1`。
- 当前群没有授权六星素材时，六星权重转入五星。
- 六星猪做菜的基础结果为 `90%` 五星、`10%` 六星；六星结果必须使用该猪绑定的同群定制菜。
- 猪币、资产、做菜、售卖与交易均在单个数据库事务中结算。

更完整的概率、成长和经济设计见 [概率与经济系统](docs/04-economy-and-probability.md)。

## 开发与验证

```powershell
uv sync --all-groups --locked
uv run pytest -q
uv run ruff check plugin.py pig_catcher tests tools
uv run python -m compileall -q plugin.py pig_catcher tests tools
uv lock --check
```

测试、UAT 和验收工具默认使用隔离数据库及 `artifacts/` 输出目录，不应直接指向正在运行的正式数据目录。

## 文档

- [产品需求基线](docs/01-product-requirements.md)
- [领域与数据模型](docs/02-domain-and-data-model.md)
- [命令与交互协议](docs/03-command-and-interaction.md)
- [概率、数值与经济系统](docs/04-economy-and-probability.md)
- [视觉与素材规范](docs/05-visual-and-asset-spec.md)
- [技术架构设计](docs/06-technical-design.md)
- [版本记录](CHANGELOG.md)
- [素材目录说明](asset_library/README.md)

## 安全与隐私

- 不要提交 `config.toml` 中的真实授权列表、管理员账号或其他平台标识。
- 不要提交 `data/`、SQLite 数据库、备份、日志、验收产物或群专属媒体。
- 导入素材前确认版权和使用授权；公开仓库中的元数据不授予任何第三方素材版权。

## 许可证

代码使用 [MIT License](LICENSE) 发布。素材版权和使用许可由素材提供者分别负责。
