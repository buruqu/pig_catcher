# 2026 中秋限定猪与月饼：素材候选

本轮只准备节日内容，不确定福利、活动玩法、投放时间、获取概率与四／五星月饼的吃菜效果；这些事项由用户另行设计。用户已确认最高五星。本文和配套 JSON、六张图片均为隔离候选，未并入正式目录或运行数据。

| 星级 | 猪猪 | 月饼 | 视觉要点 |
| ---: | --- | --- | --- |
| 2 | [灯笼照月猪](../asset_library/candidates/mid-autumn-2026/pig-lantern-moon.png) | [灯笼豆沙月饼](../asset_library/candidates/mid-autumn-2026/food-lantern-red-bean-mooncake.png) | 小猪提月亮灯笼；月饼有灯笼纹与豆沙切面 |
| 4 | [桂枝团圆猪](../asset_library/candidates/mid-autumn-2026/pig-osmanthus-reunion.png) | [桂花流心月饼](../asset_library/candidates/mid-autumn-2026/food-osmanthus-lava-mooncake.png) | 桂枝花冠和沾面粉的月饼师；桂花纹与金黄流心 |
| 5 | [玉兔捣糕猪](../asset_library/candidates/mid-autumn-2026/pig-jade-rabbit-mortar.png) | [玉兔冰皮月饼](../asset_library/candidates/mid-autumn-2026/food-jade-rabbit-snow-skin-mooncake.png) | 戴兔耳头饰的猪持玉杵；冰皮月饼有兔月纹与蓝金双色馅 |

三只猪仍清楚保留猪鼻、猪耳与猪蹄。玉兔只是头饰和月饼图案，避免把限定猪画成兔。六张独立原图位于 `asset_library/candidates/mid-autumn-2026/`，元数据在 `catalogs/candidates/mid-autumn-2026.json`；源图片保留生成时原始字节。素材由 Codex 图像生成工具根据本次用户请求创作；正式发布前需完成项目素材审核并把候选目录的授权标签替换为明确的发布口径。

公共一至五星模板不能使用群专属六星猪的 `paired_food_template_id`。表中的对应关系是内容策划关系，后续做菜专属渠道若被用户选用，应在活动规则中实现，不能假设目录字段已自动绑定。四／五星月饼效果仍未定，候选定义不填 `effect_id`；不可把上一版活动稿的福利、集章、概率或菜品效果复制进正式版。

正式接入时，先按最终投放规则加入限时获取校验，再合入正式目录和 Manifest；避免把候选公共模板导入后变成永久可抽。节后已有实例如何继续做菜、是否允许自选券取得限定猪，也待活动设计时一并确定。
