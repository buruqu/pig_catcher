# 抓猪 2.0 单群内部灰度包

> 适用范围：`qq:1092931381` 与
> `qq-official:5E5854406D0297D6FEAE696A13E3A339`。
>
> 发布方式：保留现有 1.x，另装 `local.pig-catcher-v2-internal`；内部灰度使用独立数据目录，
> 从已核验的在线备份克隆升级，不删除或原地升级生产库。

## 1. 隔离边界

- 正式开发源码的 Manifest ID 继续是 `local.pig-catcher`。构建工具只改写输出副本。
- 内部包固定 ID 为 `local.pig-catcher-v2-internal`，因此 MaiBot 数据目录独立为
  `data/plugins/local.pig-catcher-v2-internal/`。
- 命令路由固定使用两个 `platform:group_id` 作用域，不写入易变化的 `session_id`。这既覆盖数字
  QQ 群当前的两个 Bot 会话，也不会因聊天流重建而失效。
- 包内 `group_whitelist` 只含 `1092931381` 与
  `5E5854406D0297D6FEAE696A13E3A339`；命令组件另外只向上述两个精确作用域注册。
- 内部包默认不启用任何自动监管作用域，清空一次性额度、黑名单及公告开关，避免复制配置后误执行。
- WebUI 运营入口从包内 `_manifest.json` 动态读取插件 ID，不再把
  `local.pig-catcher` 写死在链接中。
- 构建只复制明确列出的源码、正式素材、依赖清单和素材导入工具；不复制数据库、备份、日志、
  `.env`、Excel、测试产物或 AppID/AppSecret 文件。

## 2. 可复现构建

在 2.0 工作树执行：

```powershell
uv run python .\tools\build_internal_preview.py `
  --output 'D:\MaiBotArchives\pig_catcher\internal-preview\pig_catcher_v2_internal-<本次批次>'
```

输出目录必须不存在，且不能位于源码目录内部。工具完成后会生成
`INTERNAL_PREVIEW_BUILD.json`，记录插件 ID、两群作用域、命令路由白名单、每个文件的 SHA-256、文件数和
总字节数。相同源码与相同参数应生成相同的业务文件哈希清单；构建工具从不覆盖旧包。

复核已有包：

```powershell
uv run python -c "from pathlib import Path; from tools.build_internal_preview import verify_internal_preview_package; print(verify_internal_preview_package(Path(r'<包目录>'))['plugin_id'])"
```

必须输出 `local.pig-catcher-v2-internal`，并且复核无数据库、环境文件、Excel、链接或 Junction。

## 3. 安装与不删档数据准备

以下步骤由获准的上线执行者在灰度切换时完成；构建工具本身不会碰 MaiBot 进程或生产数据。

1. 保持 `plugins/pig_catcher` 与 `data/plugins/local.pig-catcher` 原样不动。
2. 用 SQLite 在线备份接口，把现有生产库生成到插件目录之外的一次性克隆；不得复制运行中的
   主库、WAL 或 SHM 文件。
3. 将在线备份克隆放入新的 `data/plugins/local.pig-catcher-v2-internal/`，只让 2.0 代码打开该副本，
   完成当前内部数据库→Schema 52 迁移、重开幂等、`quick_check`、外键和账本校验。
4. 用内部包内的 `tools/import_asset_catalog.py`，将包内
   `asset_library/current/assets.json` 导入内部数据目录；核对四群模板总清单中的目标两群各
   187 猪/69 菜，不能把开发验收数据库直接当作玩家库。
5. 将已验证包原子放到新的 `plugins/pig_catcher_v2_internal/`。目录名可以与插件 ID 不同，
   但 `_manifest.json` 必须保持 `local.pig-catcher-v2-internal`。
6. 使用获准的 MaiBot 会话路由更新，使两个版本的同名命令按 `allowed_session` 分流；然后只重载
   新插件或在获准窗口重启 MaiBot。不要关闭浏览器或其他无关进程。
7. 先由管理员在两个配对作用域验证 `/抓猪帮助`、存档、图片、派遣、巡演、双人对战和群隔离，
   再开放普通成员测试。

内部灰度开始后，新产生的进度只写入内部数据目录。它不会自动回写 1.x，也不会自动并入 9 月 1 日
删档新服；是否保留灰度成果必须另行明确决定。

## 4. 回退

- 命令分流或加载失败时，先从路由中移除内部包的两个 `allowed_session`，恢复这两个会话命中 1.x。
- 卸载 `local.pig-catcher-v2-internal` 只影响内部包；不得把 1.x 指向 Schema 52 内部库。
- 保留内部库及构建清单用于复盘。确认不再需要前不得删除；回退不要求回滚或覆盖生产库。
- 真实 QQ 验收必须单独记录，离线测试和 Runner 加载不能代替群内收发、图片/GIF与双人交互验收。
