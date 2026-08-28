# 第九期素材、全猪标签与体型重量审阅

## 本轮边界

用户于 2026-08-28 指定本轮先导入第九期素材，并逐图复核全部新旧猪猪，完成展示标签与体型、重量范围。
本轮属于隔离的 2.0 开发线；不部署至线上 1.29.1，不访问玩家生产库，不发布公告，不推送远程。

- 第九期：32 只公共猪猪（1/2/3/4/5 星分别为 7/7/8/4/6），14 道公共美食（分别为 2/2/2/4/4）。
- 品质以用户文件夹分类为准，不按图面色彩自行升降星。当前六星目录为空，不创建未来梦限大联动或六星占位。
- `渚打E`、`渚打S` 是用户指定的《BanG Dream!》EXIST、SAVIOR OF SONG（简称SOS）效率曲谱面片段猪化梗，不视作角色立绘或乐队成员。
- 全部新旧猪猪增加独立 `display_tags`；原有 `recipe_tags`、乐队身份和派遣特长分别保留各自语义。
- 全部猪模板显式记录体长、体重与 `mini/standard/giant` 档案。允许吨级，但保持既有 10000 cm / 100000 kg 校验上限。
- 新增高星美食本轮尚无用户指定特殊能力：接入正常产出、食用经验、背包和图鉴，特殊效果留待后续数值方案，不借用旧菜能力。
- 猪猪及美食基础价值、价值公式、出售价格算法、概率、道具和既有菜品效果均不在本轮调整。

## 属性与历史保护

原始媒体保留原字节、格式、帧时长和循环；审阅联系表只是临时证据，不替换原件。
四群复制的旧六星内容按相同语义同步，模板身份、媒体路径和玩家数据继续隔离。

体型范围采用游戏内视觉比例，而非将星球猪设为真实天体的天文质量；没有明确大小暗示的猪保留合理常规级别。
体重与体长继续由相关百分位生成；本轮不改变百分位分布或按百分位计算的价值。
吨/米仅为显示单位，数据库继续使用 kg/cm，排序与判定使用原始精度。

新范围只影响新生成的实例。已有猪的尺寸、重量、百分位、价值、编号、图鉴和历史纪录不回算。
派遣与其复用的战斗属性微调改用实例已存百分位，避免更新模板后重新归一化旧猪而使能力漂移；
进行中的旅程、对战仍保留原有已冻结快照。

全量逐猪数值、展示标签和图片依据见[172种猪猪的完整范围表](28-pig-physical-profile-table.md)。
205个模板包括四群各自保留的11种六星猪；新旧公共猪由同一份目录供四群使用，没有分支遗漏。

## 谱面梗核对

《EXIST》确为RAISE A SUILEN作品，见[官方单曲资料](https://bang-dream.com/discographies/2514/)。
《SAVIOR OF SONG》为RAISE A SUILEN×友希那的游戏收录曲，见[官方曲库](https://bang-dream.bushimo.jp/music/)；
SOS缩写由[谱面社区资料](https://gamewith.jp/bang-dream/article/show/300440)交叉核对。
“效率曲/车队反复打歌”的语境来自用户说明与社区用法，不宣称官方效率排名。
描述仅借用图中猪头单键、绿条滑键和粉键上滑；没有复用歌词，也不虚构确切小节或谱面难度。
两只按完整谱面组合采用60–240cm、30–360kg，与旧渚泪滴保持同一测量口径。

## 新菜目录

品质完全依照本轮素材文件夹，即使名称以“猪”结尾，放在美食目录里的也仍是美食。

| 品质 | 新增美食 |
| --- | --- |
| 一星 | 淀粉肠、猪骨汤 |
| 二星 | 咕咕猪包、渚肚鸡 |
| 三星 | 爱心馅猪包、猪葡萄牛奶 |
| 四星 | 热猪、猪橘子牛奶、猪芙蕾、香肠猪 |
| 五星 | 提拉米猪、猪克力、猪包蛋、猪草莓牛奶 |

只使用已有同星级价值和品鉴经验规则；本轮特殊效果为空，未擅自绑定专属原料或新Buff。
已有KFC、五条、宿傩的专属产出限制仍保留，不因扩充通用菜池失效。

## 技术契约

- 开发版本 `2.0.0.dev7`，Ruleset 36，Schema 41；素材 Manifest 保持版本 4 的向后兼容可选字段。
- Schema 41 仅为猪模板增加展示标签字段；旧清单未填标签仍可导入，旧实例不变。
- 所有正式猪模板必须有人工审阅的标签、完整范围和可追溯图片；新猪还须登记派遣特长。
- 标签不参与料理偏好、概率或材料产量判定；未发现/未授权六星的标签与图片一起遮罩。
- 通过既有导入校验器检查整个新素材包；仅在独立验收数据目录激活。

## 验收记录

2026-08-28，所有验证均使用隔离开发目录、合成玩家和独立数据库，未使用生产玩家数据。

- 完整pytest：**936 passed in 146.98s**，较上一轮增加121项；XML为`artifacts/r9-pytest-final.xml`。
  其中目录/SHA/动画53项、物理范围稳定44项、标签/单位/迁移24项。旧玩法回归一并通过。
- 完整包经正式导入器激活到`artifacts/r9-import-validation`，Schema41、`integrity_check=ok`，
  目录hash为`549a7377c72f549e03119460db51db92c60fac4e3749c9814c20fdc20edba8e9`。
- 298项素材预览全部生成，含18项动画模板、88项群专属模板；报告在`artifacts/r9-asset-cards-v2/report.json`。
- 第九期32猪14菜的46张真实结果卡通过DOM裁切、越界和坏图检查，见
  `artifacts/r9-formal-tags-units-20260828-d/report.json`；金额、实例和概率明确标作示例数据。
- 标签/吨米/长说明/隐私/固定动画槽13个边界场景通过，见`artifacts/r9-tags-units-20260828-a/report.json`。
  目视检查E/S、星体巨物、轻盈云猪、竖长热猪、极限长说明及联系表。
- 四个授权scope的完整图鉴各172猪/60菜，共8张长图，DOM无裁切、越界或坏图；
  证据分别为`artifacts/r9-catalogs-official-ceab`、`r9-catalogs-official-13e3`、`r9-catalogs-qq-1092`、`r9-catalogs-qq-2377`。
- 新两张动画：猪降临实际为WebP，60帧/3990ms；猪上吊为GIF，75帧/3000ms，均保持无限循环。
  验收结果卡分别约10.50MiB/8.29MiB，处于现有50MiB合成输出与256MiB估算内存预算内；未做真实QQ发送测试。
- 新46份源SHA与发布媒体逐字节相符；旧256份媒体文件未变化。Ruff、compileall、离线锁文件、Git差异检查通过。
  锁文件只更新本项目开发版本，不升级第三方依赖。

本轮修复了新图检查暴露的竖长原图裁切（Grid轨道自动最小尺寸）和新标签导致的卡片拥挤；
媒体槽仍保持原坐标。首次全量测试指出一个旧v33极简迁移夹具漏了真实存在的猪模板表，
另有开发配置版本未同步；已补全夹具与版本后重新跑完936项，未通过放宽生产迁移检查规避问题。

## 离线复现

以下命令仅在隔离2.0工作树执行，输出目录必须不存在；浏览器是独立无头绘图进程，不接管用户窗口。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check pig_catcher plugin.py tools tests
.\.venv\Scripts\python.exe -m compileall -q pig_catcher plugin.py tools tests
uv lock --check --offline --default-index https://pypi.tuna.tsinghua.edu.cn/simple
.\.venv\Scripts\python.exe tools/import_asset_catalog.py --manifest asset_library/current/assets.json --data-dir artifacts/r9-next-import
.\.venv\Scripts\python.exe tools/accept_display_tags_units.py --formal-round9 --output artifacts/r9-next-visual
.\.venv\Scripts\python.exe tools/accept_display_tags_units.py --output artifacts/r9-next-boundaries
.\.venv\Scripts\python.exe tools/accept_asset_cards.py --data-dir artifacts/r9-next-import --output artifacts/r9-next-all-assets --browser-executable 'C:\Program Files\Google\Chrome\Application\chrome.exe'
```

完整图鉴使用`tools/accept_complete_catalogs.py`，指定上述独立数据目录与四个scope分别执行。
纯原图、正式目录、代码、测试及本说明进入本地开发提交；大体积验收图和临时数据库留在忽略的`artifacts/`。
下一步仍需单独讨论完整数值方案、新菜特殊能力、未来联动与六星素材，以及大版本正式上线门禁。
