# 目录定义

`formal/pig-and-food-definitions.json` 是公开猪猪与美食的可审计元数据源，记录模板 ID、显示名、品质、描述、素材相对路径、体型规则、美食效果和联动信息。

公开清单只包含一至五星公共元数据，有意排除真实群号、群专属六星定义和媒体文件。部署者应在私有素材源中为每个授权群维护独立六星模板，并通过 `paired_food_template_id` 为每只六星猪绑定唯一的同群六星菜。

图片原件不提交到这里。本机素材包可放在 Git 忽略目录 `asset_library/current/`，MaiBot 运行副本位于宿主为插件分配的数据目录。

修改清单后必须运行 `tests/test_catalog_definitions.py`，并完成素材构建与导入校验，避免定义和二进制文件失配。
