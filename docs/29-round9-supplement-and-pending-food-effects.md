# 第九期补充：梦限大、熠～噜猪与待定美食效果

> 2026-08-28；隔离开发线 `develop/2.0`，基线 `e4ab85b` / `2.0.0.dev7`，交付版本 `2.0.0.dev8`。
> Schema41 / Ruleset37 / Manifest4。本文是历史开发记录，不是上线公告。
> 本文当时待定的13道高星菜现已接入效果；最新规则及验收安排见[后续本轮记录](30-round9-food-effects-and-item-bag.md)。
> 生产1.29.1代码、玩家数据、MaiBot和QQ均不操作；临时源素材的阿拉蕾图片修正为用户单独授权。

## 1. 本轮明确边界

- 第九期源目录当前70份图片；在上一轮46份基础上新增24份：14只公共猪、8道公共菜、1只六星猪及其对应六星菜。
- 按用户最新文件夹定品质；同字节的“猪猪？”从一星改为四星，保留模板ID，旧实例品质、价值、尺寸与历史不回算。
- 梦限大五人系列独立登记为5槽收藏；已有绿茶猪／薇欧拉保持单槽动画特别联动和舞台客串，不合并进五人团。
- 新联动猪补齐静态描述、角色资料、展示标签、体型重量、派遣特长及已有巡演体系登记；不修改旧评分公式或概率、价值规则。
- “熠～噜猪”与“熠～噜猪绿芯小猪派”按四个既有群作用域分别保存原件、模板和一对一配对。只新增内容，不合并身份或玩家数据。
- 所有本期新菜的特殊效果保持为空，由维护者后续逐项指定；仍可按现有规则产出、收藏、查看与基础品鉴。
- 新的一星美食“猪饲料”不是商城升级。美食命令与 `/升级 猪饲料` 各走原有业务，不新增同名商城商品。
- 不创建未提供的其他素材，不擅自设计专属配方概率、菜品能力、价值或新的永久加成。

## 2. 第九期全部待补效果的高星菜

包含上一轮与本轮，按名称去重；六星的四群副本只列一次。

| 序号 | 品质 | 美食 | 本次新增 | 效果状态 |
| --- | --- | --- | --- | --- |
| 1 | 四星 | 热猪 | 否 | 待维护者指定 |
| 2 | 四星 | 猪橘子牛奶 | 否 | 待维护者指定 |
| 3 | 四星 | 猪芙蕾 | 否 | 待维护者指定 |
| 4 | 四星 | 香肠猪 | 否 | 待维护者指定 |
| 5 | 四星 | 猪条 | 是 | 待维护者指定 |
| 6 | 四星 | 猪咪堡 | 是 | 待维护者指定 |
| 7 | 四星 | 猪可乐 | 是 | 待维护者指定 |
| 8 | 五星 | 提拉米猪 | 否 | 待维护者指定 |
| 9 | 五星 | 猪克力 | 否 | 待维护者指定 |
| 10 | 五星 | 猪包蛋 | 否 | 待维护者指定 |
| 11 | 五星 | 猪草莓牛奶 | 否 | 待维护者指定 |
| 12 | 五星 | 猪堡套餐 | 是 | 待维护者指定 |
| 13 | 六星 | 熠～噜猪绿芯小猪派 | 是 | 待维护者指定；原料仅对应熠～噜猪 |

一至三星的新菜继续作为基础经验／售卖素材，本轮不要求补特殊能力。
“香肠猪”按素材文件夹属于四星美食，不是猪模板。

## 3. 资料与图片审阅

### 梦限大五人

[官方阵容页](https://bang-dream.com/artist/yumemita/)与五人的官方立绘逐项核对；
中文角色名为本插件采用的译名，不把它们说成官网认证中文译名。猪化文案、体型、旅行特长和招牌是游戏原创设计。

| 猪猪 | 品质 | 对应角色与官方资料 | 已确认职能 | 原创巡演招牌 |
| --- | --- | --- | --- | --- |
| 阿拉蕾猪 | 五星 | [仲町あられ／Arale](https://bang-dream.com/artist/yumemita/nakamachi-arale/) | 主唱 | 扩音器先别开：互动曲目演出+2 |
| nnk猪 | 四星 | [宮永ののか](https://bang-dream.com/artist/yumemita/miyanaga-nonoka/) | 吉他 | 兔耳即兴摇摆：未练熟曲目舞台+2 |
| 律猪 | 四星 | [峰月律](https://bang-dream.com/artist/yumemita/minetsuki-ritsu/) | 节奏吉他 | 稳拍再添一碗：技术曲目抵消最多2分负面波动 |
| 都子猪 | 五星 | [藤都子](https://bang-dream.com/artist/yumemita/fuji-miyako/) | 键盘 | 截稿前的分镜：主题呼应时下一站编排+2 |
| 由乃猪 | 五星 | [千石ユノ](https://bang-dream.com/artist/yumemita/sengoku-yuno/) | DJ与音源操控 | 省电混音台：曲目风格组合不同时设备+2，上限仍5 |

按官方职能登记主唱、双吉他、键盘和DJ，不补造贝斯手或鼓手。新集合为`bandori-yumemita`，
5个独立槽位；旧绿茶猪／薇欧拉的`bandori-yumemita-viola`单槽集合和客串身份不变。
`Arale`拼写依据官方人物页及[官方英文页面](https://en.bang-dream.com/anime/yumemita/)，不是把猪名误识别成另一部作品角色。

五人均采用正常乐队猪尺度，最小/最大范围为32–120cm、24–340kg；具体逐猪范围见第28文档。
发型、耳机、乐器和小伙伴不被算成巨型身体。梦限大主题“梦与现实的信号”及
“连接梦境中／猪圈信号放大器／不掉线的安可”三首曲目是插件原创，未复制官方音乐、团标或立绘进发布包。
当前巡演十主题、30首曲目、52主体形态／50规范身份，评分封顶、成本及奖励不变。
旧“九种颜色”成就、首期固定九主题条件、130项成就目录和49项常规毕业条件保持不变。

### 其他补充素材

| 品质 | 新猪 | 新美食 |
| --- | --- | --- |
| 一星 | 深海猪、猪睡觉、猪身份证、猪骰 | 猪蛋、猪饲料 |
| 二星 | 你怎么跟猪一样、劲爆大只猪头、猪上班 | 猪笋 |
| 三星 | 渚交互、猪打call | 开猪罐头 |
| 四星 | 上表nnk猪、律猪；另有旧猪猪？从一星改档 | 猪条、猪咪堡、猪可乐 |
| 五星 | 上表阿拉蕾猪、都子猪、由乃猪 | 猪堡套餐 |
| 六星 | 熠～噜猪 | 熠～噜猪绿芯小猪派 |

- 20份非乐队图（含“猪猪？”同字节改档）与五张乐队猪图逐张审阅，两个新GIF的101帧逐帧解码。
- 劲爆大只猪头按巨大头部整体计140–420cm／400–8000kg；猪身份证按轻薄卡片计8–18cm／0.01–0.08kg；
  猪骰按桌面器物计3–20cm／0.08–6kg，不以“所有猪差不多重”覆盖素材差异。
- 渚交互按整段猪头谱面计60–240cm／30–360kg，延续E/S与泪滴的测量口径，不冒充乐队角色。
- 猪身份证图是虚构证件梗；文案不摘录地址、完整号码，不作为真实用户身份依据。
- 熠～噜猪只按黑绿长发、晶体与创可贴等可见特征写描述，不猜未确认的IP或真实群友身份。
- 猪打call保持6帧各100ms／600ms；你怎么跟猪一样保持95帧，首帧60ms、其余30ms／2880ms，均无限循环。
  后者470×180横幅完整`contain`，配置及导入CLI兼容下限统一180px；没有放宽字节、帧数、时长或内存上限。
  将来上线时，如WebUI已有200/256px等更高的已保存配置，需将“图片最短边”明确调为180；不暗中覆盖用户自定义校验阈值。

## 4. 实现与兼容

- 正式定义328条（223猪／105菜），公共175猪／57菜，群专属96条（四群各12猪／12菜）；
  每个授权scope可见187猪／69菜。257份唯一二进制、清单引用332份独立媒体路径（含备用图），仍由Git LFS管理。
  仓库另保留历史同字节别名文件“翻滚猪.jpg”，因此媒体目录实际333文件；本轮没有删除或改写该旧文件。
- 四个既有scope独立追加六星配对，不合并QQ号与OpenID，不改玩家身份、不读生产库。
  只有同scope熠～噜猪成功做成六星菜时对应其绿芯小猪派，不进入其他原料的普通菜池。
- “猪猪？”保留`pig-r1-pig-question`。导入器只额外允许该公共猪模板**单向1→4**的已批准改档；
  其他模板、反向或其他星级、种类替换、群作用域改变仍拒绝。导入事务与版本递增不变。
- 旧实例不改。分种纪录、全群纪录、巨物播报均改读实例名称和星级快照，避免改模板后把旧一星纪录显示为四星。
- 新猪补齐旅行特长。默认批量保留对梦限大生效：同模板保留最高价值的可选一只，收藏/在队等保护独立生效。
- “猪饲料”一星食物走食物命名空间，`/升级 猪饲料`继续走永久升级，两个定义互不替代。
- 未擅自设计汉堡、薯条、可乐的合成或套餐Buff；第九期23道菜全部`effect_id=""`和空参数，
  正常生成、图鉴、售卖、基础品鉴经验可用，13道高星菜特殊能力等待用户定稿。
- 完整图鉴的猪/菜缩略图修复Grid内在尺寸撑高导致的半截裁图；固定媒体轨道允许收缩，原图保持`contain`。
  视觉验收工具追加图片与媒体槽边界检查，不能只查文字和根容器后就认定没有裁图。

## 5. 阿拉蕾图片纠错

旧文件名为`阿雷拉猪.png`，图内文字为“阿蕾拉猪”。按用户纠正统一为**阿拉蕾猪**；
使用内置ImageGen的引用图片编辑模式，仅要求纠正底部名字，人工确认画面及字形后保存到
`第九期/五星猪猪/阿拉蕾猪.png`，并纳入开发发布包。
用户明确“原图不用备份，修改后删除”，因此校验修正图后已删除旧文件，**未制作原图备份**。
其他素材未进行图像编辑；保留dev7全部302份媒体的原字节，“猪猪？”仅改文件夹。

修正图：PNG，1254×1254，1,552,505字节；SHA256：
`9dbb362bab1e4c08f253e79000d65faf8ee69586b8de507bb3a796822cec7fa9`。

实际编辑提示词（生成工具仅执行此图的拼写修正，不含新能力设计）：

```text
Use case: text-localization. Input image 1 is the edit target: an existing square game pig character card. Change ONLY the four large dark-purple Chinese characters at the bottom from “阿蕾拉猪” to exactly “阿拉蕾猪”. Required text, character by character: 阿 / 拉 / 蕾 / 猪. Keep the same dark-purple color, hand-drawn bold calligraphic lettering style, text baseline, size and centered placement in the bottom white label area. Preserve every other part of the artwork as closely as possible: the cute pig's face and body, black/yellow cat ears, golden twin tails with pink left tips and blue right tips, hair clips, white collar and gem, colorful megaphone, purple triangular side background, yellow four-point stars and all composition. Keep the full square framing, no crop, no extra text, no watermark, no new decorations. Do not redesign or redraw the character. This is a spelling correction only; the final four-character name must read 阿拉蕾猪.
```

## 6. 验收

以下均使用隔离工作树、合成玩家及独立SQLite；离线通过不等于上线或真实QQ发送通过。

- 最终全量 **1029 passed in 111.34s**，比dev7增加93项；报告`artifacts/r9-supplement-release-tests.xml`。
  新补充专项85项（含四群历史记录12项）；首批目录／SHA／派遣135项回归保留，未弱化旧原件及旧效果断言。
- 正式清单已通过导入器，独立库Schema41、`integrity_check=ok`；
  catalog hash：`fbf1069064d924d833463220975b434836331471dbd6504b12970e95b331a826`。
- 第九期两批全部70份结果卡通过文字裁切、越界、坏图检测，`artifacts/r9-supplement-cards/report.json`。
  新动画6帧／600ms和95帧／2880ms、循环保持，结果GIF分别661,680与5,360,611字节，未扩大资源预算。
- 梦限大实际服务流程生成20张巡演图片：编队确认、五人阵容、十主题、新曲第4页、角色第8/9页、
  排练、出发确认、三站、终演及收藏；`artifacts/r9-supplement-tours-c/report.json`。
- 四scope各187猪／69菜，共8张完整图鉴通过；未发现的12个六星槽按既有规则隐藏。
  `artifacts/r9-supplement-catalogs-c/report.json`。修后175猪图+57菜图共232张媒体槽越界为0，
  `artifacts/r9-supplement-catalog-thumbnail-final-details/summary.json`。
  20+8图的文字裁切、根越界、坏图、媒体槽越界均为0；修前报告不作为通过证据。
- 人工核对联系表、阿拉蕾字形、六星菜完整卡、五人阵容、主题曲目及4/5星图鉴局部。
  本轮合计98张正式卡／图鉴／巡演页面，不重复计小缩略图和联系表。
- 生产`main`仍为`ae1d221`／1.29.1／Schema34／Ruleset29，正式代码工作区未改变。
  Ruff、compileall、原镜像离线锁文件及Git差异检查通过，第三方依赖未升级。
  原件SHA与正式目录一致；阿拉蕾是唯一按用户指示编辑的图片，错误原图已删除，无原图备份。

验收中实际修复：受控改档导入、旧纪录星级快照、十主题标题、猪/菜图鉴Grid裁切。
首次全量还提示Ruleset断言未从36同步到37；同步后重新完整执行，没有跳过测试。

## 7. 离线复现与后续

仅在隔离开发树中运行，输出目录必须是新目录；无头浏览器不会接管用户浏览器。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q pig_catcher plugin.py tools tests
uv lock --check --offline --default-index https://pypi.tuna.tsinghua.edu.cn/simple
.\.venv\Scripts\python.exe -X utf8 tools/import_asset_catalog.py --manifest asset_library/current/assets.json --data-dir artifacts/next-supplement-import
.\.venv\Scripts\python.exe -X utf8 tools/accept_display_tags_units.py --formal-round9 --output artifacts/next-round9-cards
```

完整图鉴用`tools/accept_complete_catalogs.py`和上述独立库逐scope运行。
下一步由维护者为上表13道高星菜逐项指定效果；价值方案、美术总工程、帮助分层和最终上线验收仍单独推进。
没有QQ公告、远端推送、进程重启或生产迁移。本地测试库和临时图片不进入Git。

完整猪标签和物理范围见 [全猪范围表](28-pig-physical-profile-table.md)。
上一轮历史验收保留在 [第九期首批交付](27-round9-content-and-physical-profiles.md)。
