# 本机素材库

`current/` 保存当前正式素材导入包，包含：

| 位置 | 内容 |
| --- | --- |
| `current/assets.json` | 可供导入工具读取的 Asset Manifest 3 |
| `current/build-report.json` | 102 项素材、101 份唯一二进制的 SHA-256 校验报告 |
| `current/media/猪猪素材库/` | 一至五星公共猪猪素材 |
| `current/media/美食素材库/` | 公共美食素材 |
| `current/media/定制猪群友库/` | 按 QQ 群号隔离的六星定制猪 |
| `current/media/定制美食库/` | 按 QQ 群号隔离的六星定制美食 |

该目录中的图片和 GIF 保留原始字节，不做重编码；动画列表页只提取确定性预览帧，
单项详情仍使用原动画。`current/` 被 Git 忽略，以免群专属图片进入公共仓库。

正式名称、描述、品质、概率属性和授权范围维护在
`catalogs/formal/pig-and-food-definitions.json`。新增或替换素材后，应使用
`tools/build_asset_package.py` 从完整来源目录重新构建包，再用
`tools/import_asset_catalog.py` 校验并原子导入。不要直接修改 MaiBot 数据目录中的
校验值目录。
