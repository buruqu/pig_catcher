# 正式清单

`formal/pig-and-food-definitions.json` 是猪猪与美食的可审计元数据源，记录模板 ID、
显示名、品质、描述、素材相对路径、体型规则、美食效果、六星猪对应菜、
BanG Dream 联动信息和群作用域。

图片原件不放在 `catalogs/`；当前正式素材包位于 `asset_library/current/` 并通过 Git LFS
发布，MaiBot 运行副本位于插件数据目录。修改清单后必须运行
`tests/test_catalog_definitions.py` 和素材构建、导入校验，避免定义与二进制文件失配。

当前群专属内容同时维护四个作用域：`qq:1092931381` 与
`qq-official:5E5854406D0297D6FEAE696A13E3A339` 为一组，`qq:237716658` 与
`qq-official:9EA2810F378FBD7DC3219C56CEAB3520` 为一组。每个作用域各有 8 只六星猪和
8 道配对六星菜。修改任一群专属名称、描述、数值或效果时必须同步四份；
`tests/test_catalog_definitions.py` 会做全字段语义对比。

Ruleset 13 的公共四星、五星菜平衡也只维护一份正式定义并由四个作用域共同读取；修改
效果类型、参数、次数或描述后必须重建 Asset Manifest。导入只影响此后生成的美食实例，
玩家背包中已存在的实例继续保留创建时效果快照，禁止为追平版本而静默改写。
