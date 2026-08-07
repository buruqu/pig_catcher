# 本机素材库

`current/` 保存当前正式素材导入包；`archive/` 保存按版本命名的本机回滚包。两者都被 Git 忽略，整理目录时不得删除。
当前正式包为 `v1.6.7`（目录哈希 `976761ab…`；六星定制内容已在四个群全量开放，修复官方群跨群授权）。本次正式包包含：

| 位置 | 内容 |
| --- | --- |
| `current/assets.json` | 可供导入工具读取的 Asset Manifest 4 |
| `current/build-report.json` | 210 项模板、162 份唯一内容、213 份隔离存储媒体的 SHA-256 校验报告 |
| `current/media/猪猪素材库/` | 一至五星公共猪猪素材 |
| `current/media/美食素材库/` | 公共美食素材 |
| `current/media/定制猪群友库/` | 按 QQ 群号隔离的六星定制猪 |
| `current/media/定制美食库/` | 按 QQ 群号隔离的六星定制美食 |
| `archive/v*/` | 历史版本素材包，仅用于本机核验和回滚 |

该目录中的图片和 GIF 保留原始字节，不做重编码；动画列表页只提取确定性预览帧，
单项详情仍使用原动画。正式包被 Git 忽略，以免群专属图片进入公共仓库。

正式名称、描述、品质、概率属性和授权范围维护在
`catalogs/formal/pig-and-food-definitions.json`。新增或替换素材后，应使用
`tools/build_asset_package.py` 从完整来源目录重新构建包，再用
`tools/import_asset_catalog.py` 校验并原子导入。不要直接修改 MaiBot 数据目录中的
校验值目录。

两个授权群即使暂时使用相同图片，也必须分别保存在各自群号目录，并使用不同的
猪/美食模板 ID；构建器只允许同一作用域内按 SHA-256 去重，不跨群复用媒体路径。
