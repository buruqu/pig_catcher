# 抓猪额度运维手册

## 1. 当前规则

所有时间固定使用北京时间 `Asia/Shanghai`：

| 当前窗口 | 每群每位玩家基础额度 | 下一次刷新 |
| --- | ---: | --- |
| 00:00–09:00 | 5 次 | 09:00 |
| 09:00–12:00 | 5 次 | 12:00 |
| 12:00–19:00 | 5 次 | 19:00 |
| 19:00–次日 00:00 | 5 次 | 次日 00:00 |

同一窗口内，成功抓猪后冷却 `20` 秒。进入新窗口或完成群级手动重置后，上一窗口或
重置前的成功回执不再参与当前计数和冷却。历史回执、资产、图鉴、账本与终身抓取统计
始终保留。

## 2. 收到“重置某群抓猪次数”时

只提取用户明确指定的群号，不猜测、不使用全局重置。仓库根目录执行：

```powershell
uv run python .\tools\reset_catch_quota.py --group-id <精确群号>
```

生产默认数据目录会自动解析为
`C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher`。非默认环境必须显式增加：

```powershell
--data-dir C:\path\to\local.pig-catcher
```

命令严格按以下顺序执行：

1. 验证数据库中存在 `qq:<群号>` 范围；不存在时拒绝操作。
2. 使用 SQLite 在线备份生成 `backups/pig_catcher-pre-quota-reset-*.sqlite3`。
3. 统计该群当前窗口内、上次有效重置后的成功抓取次数和玩家数。
4. 写入群级 `catch-quota-window-reset` 审计事件。
5. 运行 `PRAGMA quick_check` 并以 JSON 输出结果。

重置通过审计时间戳改变“有效窗口起点”，不删除或改写任何抓猪回执。

## 3. 群内管理员命令

在需要重置的群内，由“访问控制 → 插件管理员”中已配置的成员发送：

```text
/重置
```

命令不接收群号，固定重置当前消息所在群。执行顺序与命令行一致：先验证管理员和群范围，
再在线备份、写入群级审计事件，并发送归零次数与涉及人数的文字摘要。同一消息重复投递时
不会再次备份、重置或公示。

NapCat 接入可在管理员列表填写数字 QQ 号；QQ 官方机器人不会提供数字 QQ 号，必须填写
该机器人的成员 OpenID，推荐使用 `qq-official:<成员OpenID>` 形式。

## 4. MaiBot 管理面板

MaiBot 首页的“抓猪运营管理”卡片包含“重置抓猪次数”入口。点击后：

1. 在“额度重置”分区填写一个精确群号。
2. 打开“重置当前时段”一次性开关。
3. 保存配置。

插件会完成与命令行相同的备份和审计式重置，并在成功后把开关自动恢复为关闭。群号
保留，方便下次核对；再次重置仍必须重新打开开关并保存。

同一卡片还提供赠送/交易黑名单和群公告入口，详见
[抓猪运营控制面板手册](09-admin-panel-operations.md)。

## 5. 核验

命令行返回应满足：

- `status` 为 `ok`；
- `scope_id` 等于期望的 `qq:<群号>`；
- `integrity_check` 为 `["ok"]`；
- `backup_path` 文件存在；
- `cleared_catches` 与本次希望归零的有效次数一致。

数据库审计查询：

```sql
SELECT audit_event_id, scope_id, actor_user_id, action, detail_json, created_at
FROM audit_events
WHERE action = 'catch-quota-window-reset'
ORDER BY created_at DESC
LIMIT 20;
```

禁止直接删除 `command_receipts`、清空 `player_statistics` 或修改玩家资产来达到重置效果。

## 6. 双接入群作用域说明

同一个实际 QQ 群通过 NapCat 与 QQ 官方 Bot 接入时会形成不同 `scope_id`，额度与玩家数据
不会自动合并。当前对应关系为：

| 实际群 | NapCat 范围 | QQ 官方范围 |
| --- | --- | --- |
| 第一组 | `qq:1092931381` | `qq-official:5E5854406D0297D6FEAE696A13E3A339` |
| 第二组 | `qq:237716658` | `qq-official:9EA2810F378FBD7DC3219C56CEAB3520` |

重置额度时必须操作消息实际进入的范围，不能把“一一对应”理解为两个范围共用计数。
群专属猪猪/美食内容会同步四份，但额度、资产、账本和玩家偏好仍分别持久化。
