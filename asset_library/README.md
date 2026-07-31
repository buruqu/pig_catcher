# 本机素材库

`asset_library/current/` 用于保存部署者在本机生成的完整素材导入包，通常包含：

| 位置 | 内容 |
| --- | --- |
| `current/assets.json` | 可供导入工具读取的 Asset Manifest 4 |
| `current/build-report.json` | 模板、媒体和 SHA-256 校验报告 |
| `current/media/猪猪素材库/` | 一至五星公共猪猪素材 |
| `current/media/美食素材库/` | 一至五星公共美食素材 |
| `current/media/定制猪群友库/<群号>/` | 按群号隔离的六星定制猪 |
| `current/media/定制美食库/<群号>/` | 按群号隔离的六星定制美食 |

`current/` 已被 Git 忽略。公开仓库不提供实际素材包、真实群号或任何群专属媒体。

图片和 GIF 在构建时保留原始字节；动画列表页只生成确定性预览，单项详情仍可使用原动画。正式名称、描述、品质、概率属性和授权范围由素材定义清单维护。

新增或替换素材后，应使用 `tools/build_asset_package.py` 重新构建，再使用 `tools/import_asset_catalog.py` 校验并原子导入。不要直接修改 MaiBot 插件数据目录中的校验值目录。

不同授权群即使暂时使用相同图片，也必须分别保存在各自群号目录，并使用不同的猪/美食模板 ID。构建器只允许同一作用域内按 SHA-256 去重，不跨群复用媒体路径。
