# 2026 中秋限定猪与猪月饼：素材候选 v2

本轮只准备节日内容，不确定福利、活动玩法、投放时间、获取概率与四／五星月饼的吃菜效果；这些事项由用户另行设计。用户已确认最高五星，并要求猪猪改为现有素材库画风、月饼印猪脸、保留三种猪月饼。本文和配套 JSON、六张图片均为隔离候选，未并入正式目录或运行数据。第一版透明背景插画保留在 `asset_library/candidates/mid-autumn-2026/`，其元数据 `catalogs/candidates/mid-autumn-2026.json` 已被本版取代。

| 星级 | 猪猪 | 月饼 | 视觉要点 |
| ---: | --- | --- | --- |
| 2 | [提灯猪](../asset_library/candidates/mid-autumn-2026-v2/pig-lantern.png) | [豆沙猪月饼](../asset_library/candidates/mid-autumn-2026-v2/food-red-bean-pig-mooncake.png) | 扁平小猪提月亮灯笼；猪脸压印与豆沙切面 |
| 4 | [桂花猪](../asset_library/candidates/mid-autumn-2026-v2/pig-osmanthus.png) | [桂花猪月饼](../asset_library/candidates/mid-autumn-2026-v2/food-osmanthus-pig-mooncake.png) | 桂花冠和沾面粉的做饼猪；猪脸压印与金黄流心 |
| 5 | [玉兔猪](../asset_library/candidates/mid-autumn-2026-v2/pig-jade-rabbit.png) | [冰皮猪月饼](../asset_library/candidates/mid-autumn-2026-v2/food-snow-skin-pig-mooncake.png) | 戴兔耳头饰的猪持玉杵；猪脸压印与蓝金双色馅 |

三只猪按照现有素材库的扁平桃粉猪、黑点眼、白色聚光区、深紫边角和底部手写名牌制作；仍清楚保留猪鼻、猪耳与猪蹄。玉兔只是头饰，避免把限定猪画成兔。三款月饼正面都印有猪脸，切面和配色区分豆沙、桂花流心与冰皮。六张独立原图位于 `asset_library/candidates/mid-autumn-2026-v2/`，元数据在 `catalogs/candidates/mid-autumn-2026-v2.json`；源图片保留生成时原始字节。素材由 Codex 图像生成工具根据本次用户请求和用户提供的库内风格参考创作；正式发布前需完成项目素材审核并把候选目录的授权标签替换为明确的发布口径。

公共一至五星模板不能使用群专属六星猪的 `paired_food_template_id`。表中的对应关系是内容策划关系，后续做菜专属渠道若被用户选用，应在活动规则中实现，不能假设目录字段已自动绑定。四／五星月饼效果仍未定，候选定义不填 `effect_id`；不可把上一版活动稿的福利、集章、概率或菜品效果复制进正式版。

正式接入时，先按最终投放规则加入限时获取校验，再合入正式目录和 Manifest；避免把候选公共模板导入后变成永久可抽。节后已有实例如何继续做菜、是否允许自选券取得限定猪，也待活动设计时一并确定。
