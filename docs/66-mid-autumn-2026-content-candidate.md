# 2026 中秋限定猪与猪月饼：隔离候选 v3

用户确认限定内容最高五星，猪猪沿用素材库的扁平画风，月饼印猪脸，并将提灯猪改名为「灯笼照月猪」。灯笼照月猪的插画已重画为四条腿。六张候选图位于 `asset_library/candidates/mid-autumn-2026-v3/`，配套定义在 `catalogs/candidates/mid-autumn-2026-v3.json`。v1 和 v2 仅供比对；v3 尚未并入正式素材目录、生产数据库或运行中的插件。

| 星级 | 专属原料猪 | 只由该猪制作的猪月饼 | 食用效果 |
| ---: | --- | --- | --- |
| 2 | [灯笼照月猪](../asset_library/candidates/mid-autumn-2026-v3/pig-lantern.png) | [豆沙猪月饼](../asset_library/candidates/mid-autumn-2026-v3/food-red-bean-pig-mooncake.png) | 下一次抓猪六星概率固定 25%；中秋加成为固定 100% |
| 4 | [桂花猪](../asset_library/candidates/mid-autumn-2026-v3/pig-osmanthus.png) | [桂花猪月饼](../asset_library/candidates/mid-autumn-2026-v3/food-osmanthus-pig-mooncake.png) | 下一次用六星猪做菜，六星菜概率加 25 个百分点；中秋加 50 个百分点 |
| 5 | [玉兔猪](../asset_library/candidates/mid-autumn-2026-v3/pig-jade-rabbit.png) | [冰皮猪月饼](../asset_library/candidates/mid-autumn-2026-v3/food-snow-skin-pig-mooncake.png) | 食用后得 10000 猪币；中秋食用得 20000 猪币 |

中秋加强时段按北京时间 2026 年 9 月 25 日 00:00（含）至 10 月 1 日 00:00（不含），覆盖 9 月 25—30 日。当前实现以**实际抓猪、做菜或食用时刻**判断是否加强；前两道月饼先吃后触发，效果会保留到匹配的动作成功结算，失败的动作不消耗效果。豆沙固定概率不受饲料、等级、道具或其他概率菜加成；它使用普通抓猪额度，不额外发放次数。桂花为百分点加成，与常规六星做菜加成相加，最高不超过 100%；若六星菜独占效果接管本次做菜，桂花效果保留。冰皮猪币在食用交易内记入个人流水，重复消息只重放原回执。

产出规则不改变现有做菜星级概率：只有来源猪与月饼相匹配，且本次做菜结果落在对应的 2／4／5 星时，才产出对应月饼；其他星级照常出普通菜。三道猪月饼已排除普通随机菜池和其他随机赠菜池，其他猪无法产出。公共一至五星猪不使用专属六星猪的 `paired_food_template_id` 字段，配对逻辑在做菜服务中单独处理。

节日活动玩法、福利、限定猪的获取与节后供应规则由用户继续设计。候选素材暂不进入正式目录；不得因此让限定猪永久随机出现在抓猪池中。全部规则完成后再一并准备上线版本。
