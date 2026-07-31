# 正式清单

`formal/pig-and-food-definitions.json` 是猪猪与美食的可审计元数据源，记录模板 ID、
显示名、品质、描述、素材相对路径、体型规则、美食效果、六星猪对应菜、
BanG Dream 联动信息和群作用域。

图片原件不提交到这里；本机当前素材包位于 `asset_library/current/`，MaiBot 运行副本
位于插件数据目录。修改清单后必须运行 `tests/test_catalog_definitions.py` 和素材构建、
导入校验，避免定义与二进制文件失配。
