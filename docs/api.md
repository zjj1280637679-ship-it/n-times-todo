# Closed API v1

只读审计上下文另有独立的 `audit` CLI / `export_audit` Python 入口，详见 [审计使用说明](audit.md)。它不属于下列七个 `todo.*` 工具，不需构造会初始化存储的 `TodoStore`。

输入均为 `{"tool":"todo.NAME","arguments":{...}}`。未知键、重复JSON键、非有限数字被拒绝。成功为 `{ok:true,data:...}`，预期业务失败为 `{ok:false,error:{code,detail}}`。CLI `call` 在业务失败时退出1。

## get / history / diff

- `todo.get`: 可选 `version`（非负整数）；缺省读当前。返回完整快照。
- `todo.history`: 可选 `after`（缺省-1）、`limit`（缺省100，1..1000）。用返回的 `next_after` 翻页，null表示本次读取时已到末尾。
- `todo.diff`: 必需 `before`、`after` 版本号，两个方向均允许。

## update

必需 `expected_version`、`request_id`、`changes`；可选 `reason`、`actor`。

- `expected_version` 是上次读取到的版本。并发更新仅一个请求可赢；冲突后先重新读和协调，不自动覆盖。
- `request_id` 长1..100，字母数字起始，只含字母数字及 `_.:-`。首次提交与重试使用同一个ID和完全相同的语义参数。修改期望版本、原因或内容都要新ID。
- `changes` 为1..200个操作，按顺序在事务内全部成功或全部失败。
- task 有 `id`、`title`、`status`、`notes`。add可省id，返回新生成ID；status缺省todo，notes缺省空串。status是todo/doing/blocked/done。
- 标题上限2000字符、notes上限20000、reason上限4000、actor上限200。单快照序列化2 MB总限优先。

操作：

```json
{"op":"add","task":{"id":"t1","title":"示例","status":"todo","notes":""}}
{"op":"edit","id":"t1","set":{"status":"done","notes":"合成验证完成"}}
{"op":"remove","id":"t1"}
{"op":"move","id":"t1","before":"t2"}
{"op":"move","id":"t1","before":null}
```

`edit.set` 仅title/status/notes且不能为空；不能改任务ID。曾经使用的ID不允许通过add重新分配给其他任务；如需恢复旧任务，用restore，或添加全新ID。

成功写回包含version、created_at、hash、parent_hash、kind、changes、replayed、views。更新返回摘要与diff，不回传完整snapshot；需要全量时调用get。`replayed=true` 表示原请求已提交，不是新增版本。

`changes` 自动差异包含added/removed（完整任务）、modified（按ID与字段提供before/after）、completed/reopened（ID列表）、order（仅共同存活任务相对顺序改变时给before/after）。新增或删除导致索引位移，不误报所有存活任务移动。新增一项且初始status=done记录为新增；completed仅表示已有事项从非done变done。

## restore

```json
{"tool":"todo.restore","arguments":{"version":1,"expected_version":3,"request_id":"restore-001","reason":"恢复原方案，但保留此前判断"}}
```

复制v1任务，追加v4并标记restored_from=1；v2/v3不删除。恢复值来自存储的旧页，不接受调用方伪造旧before。新页的diff与恢复前最新版本比较。

## verify / sync

无参数。verify检查完整快照链、差异重算以及请求ID/digest与版本的双向绑定。不是作者身份认证。sync只补齐缺失的派生页，已有相同页不动，不同页报view_conflict。

## 常见错误

- version_conflict：未提交，需要重新理解最新清单。
- request_conflict：同ID被用于不同请求，未提交。
- task_not_found / duplicate_task_id：整批未提交。
- invalid_arguments / snapshot_too_large：整批未提交。
- integrity_error：停止写入，调查存储，勿换新目录伪装历史连续。
- store_unavailable：调用结果可能需要核实。若进程在提交后响应前失联，用相同ID及原请求安全核对/重放。
- 写入成功但views.pending：数据库已经提交，视图未就绪，不是更新失败。
