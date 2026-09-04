# 抓猪 2.0.22 正式发布总结

## 发布信息

- 正式版本：`2.0.22`
- 数据协议：`Schema63`
- 数值协议：`Ruleset61`
- 战斗协议：`Battle v14`
- 素材协议：`Manifest4`
- 发布时间：2026-09-04
- 发布方式：四个既有 QQ／QQ 官方会话范围无删档升级
- 群公告：本次未自动发送

本版增强撅撅猪的时之沙与虚拟声轮盘，只影响升级后创建的Battle v14场次；Battle v1–v13
已经提交的随机事实、历史战报、玩家资产、养成和战利品均不追溯改写。

## 发布门禁

- 整仓回归：`2036 passed in 267.09s`。
- Chromium离线生成44张战斗图，文字裁切、越界、破图和媒体裁切均为0。
- Ruff、Python `compileall`、离线依赖锁、JSON／TOML解析、敏感文件扫描与`git diff --check`通过。
- 上线前正式库为Schema62，`quick_check=ok`、外键异常0、账本异常0。
- 发布窗口先等待存量Battle v13自然结束，并用临时插入闸门仅阻止新场创建；旧场到期按现行
  规则结算后停机，闸门在迁移前移除，最终备份和线上库均不含该临时对象。
- 生产快照副本使用正式包代码完成Schema62→63迁移；除`schema_migrations`增加一行外，
  所有既有表行数保持不变，旧2.0.21代码会拒绝打开Schema63副本。

## 正式包与回滚点

- 无密钥正式包：
  `D:\MaiBotArchives\pig_catcher\release_candidates\pig-catcher-2.0.22-battle-v14-published-20260904-171503`
- 正式包受控文件887个、载荷499,177,333字节、清单SHA-256：
  `1d9a9bc22c7beef43c8376cdc29f89268e947128d1fd66bf8e2b24af2a5a0ac8`；密钥输入复制数0。
- 停机最终数据库备份：
  `D:\MaiBotArchives\pig_catcher\releases\2.0.22-battle-v14-20260904-161330\pre-live-schema62-final-zero-active.sqlite3`
- 最终备份SHA-256：`72edf5a16d6d6c0b0a29f5c9ff117d1a86c3325c7ad74b82e8852051414fe1de`。
- 代码回滚包：
  `D:\MaiBotArchives\pig_catcher\release_candidates\pig-catcher-2.0.21-battle-v13-published-20260904-141454`

生产回滚必须同时恢复上述Schema62数据库备份与2.0.21代码包，不能让旧代码直接读取Schema63数据库。

## 上线结果

- 正式包与运行目录逐项比较：缺失0、哈希不一致0。
- 迁移时119名玩家、9,496只玩家猪、5,083道玩家美食、17,221条猪币账本、16,118条命令回执
  和98场历史对战均保持不变；本次不是删档、补发或数值重置。
- 上线后正式库为Schema63，`quick_check=ok`、外键异常0、账本异常0；231只猪、113道菜，
  共344项目录激活，素材巡检缺失0。
- 启动日志确认`local.pig-catcher v2.0.22`加载成功，`official-primary`与`official-secondary`
  两路QQ官方消息网关均已就绪，插件及网关没有新增warning/error。
- 发布核验时尚无新建Battle v14群内实战；离线战斗图和网关健康不冒充真实QQ双人实战。
