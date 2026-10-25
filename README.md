# N次贴的待办事项 · N-times Todo

每次更新不是擦掉旧清单，而是翻到新一页：**完整快照 + UTC 时间 + 自动差异标注**。

- 每次成功的新请求保存完整版本，不只保存差量。
- 自动生成 `versions/00000001.json` 和 `versions/00000001.md`；每页含完整事项与相对上一版的变化。
- 新增、删除、字段修改、完成、重新打开和排序变化分别标注。
- 任意两版可对比；恢复旧版会追加新版本，绝不倒退历史指针。
- AI 只需发变化，工具自动复制原清单并应用；不用重复输出全部背景。
- Python 标准库运行时，无网络请求、API 密钥或外部模型依赖。

这是 **本地 CLI / Python 库 + instruction-only 插件技能**，不是已连接所有应用的后台服务。自动记录覆盖通过本程序提交的更新；其他应用必须显式接适配器，手动编辑导出页不会自动写回。

## 快速使用

需要 Python 3.10+。在项目根目录：

```sh
python -m pip install .
n-times-todo --store /your/private/todo-notebook init
n-times-todo --store /your/private/todo-notebook call < examples/add.json
n-times-todo --store /your/private/todo-notebook call < examples/finish.json
n-times-todo --store /your/private/todo-notebook show
n-times-todo --store /your/private/todo-notebook diff 1 2
n-times-todo --store /your/private/todo-notebook verify
```

不安装也可运行（从项目根目录）：

```sh
PYTHONPATH="$PWD/src" python -m n_times_todo.cli --store /your/private/todo-notebook init
```

`examples/` 是合成示例。不要把真实笔记目录放进公开仓库。默认目录 `.n-times-todo/` 已加入 `.gitignore`，但 `.gitignore` 不是秘密扫描或访问控制。

## AI 入口

宿主配置固定私有 store；模型请求不含文件路径。

```json
{"tool":"todo.update","arguments":{"expected_version":0,"request_id":"plan-001","reason":"用户确认本轮目标","changes":[{"op":"add","task":{"id":"demo","title":"验证示例交付"}}]}}
```

仅传本次差异；程序保存完整新页。成功响应包含新版本、hash、变化与视图状态，不强制把整份清单回传给模型。

| 工具 | 作用 |
|---|---|
| `todo.get` | 当前或指定版本的完整清单 |
| `todo.update` | 原子应用一批增删改排序，追加版本 |
| `todo.history` | 分页查看版本记录 |
| `todo.diff` | 对比任意两版 |
| `todo.restore` | 将历史内容作为新版本追加 |
| `todo.verify` | 校验快照哈希链及请求索引 |
| `todo.sync` | 补生成缺失的 JSON/Markdown 页，不改历史 |

详见 [API](docs/api.md)、[一致性与信任边界](docs/architecture.md)。

## 只读审计上下文

`audit` 将显式提供的原始目标、证据、完整当前快照和版本差异打包，供 AI 或人工检查遗漏、目标漂移与误解。它不输出这些语义检查已经通过的结论，也不自动修改目标或任务。

```sh
n-times-todo --store /your/private/todo-notebook audit --since 0 --expected-version 2 < examples/audit-context.json
PYTHONPATH="$PWD/src" python examples/audit-demo.py
```

第一行的合成证据对应上面的两次示例更新（当前版本 2、任务 `write` / `test`）；第二行独立创建临时合成 store，输出审计包后清理。`audit` 不初始化不存在的 store，不生成或修复旧页面，支持现有运行时默认的 rollback journal store；WAL store 会明确拒绝。Python 入口为 `export_audit(...)`。输入格式、缺证状态、版本绑定与并发边界见 [审计导出说明](docs/audit.md)。

## 日历本的“页”是什么

`history.sqlite3` 是唯一权威，事务一次保存完整快照与请求索引。
`versions/` 中的完整 JSON / Markdown 是自动物化页面，可直接查看与导出。

数据库提交完成后才生成页面：如果磁盘或目录问题导致页面暂时生成失败，响应仍报告已保存的新版本，并标记 `views.status=pending`。修复环境后运行 `sync`，不要盲目再提交一个新请求。若已有页内容冲突，保留该文件并报错，不自动覆盖。

版本号是稳定顺序；UTC 是记录时间，系统时钟可能回拨，不作为版本排序权威。每次**明确的新更新**即使净内容没变也追加一页；同一请求的重试不追加。

## 验证

```sh
PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
python scripts/check_model.py
```

自动化测试涵盖真实 SQLite 并发、子进程在提交前/后退出、幂等重放、恢复追加、视图失败修复和异常输入。有限模型覆盖两写者、两请求 ID、两抽象内容值和至多两次提交；它不是完整实现的形式化证明。

## 当前边界

- 单机私有 store，不是协作云同步、可信身份认证或防同一账户管理员篡改的存证系统。
- 哈希链能发现不一致，不能防止持有全部文件的人重写整条链或替换成完整旧备份。`actor`、`reason` 是调用方声明，不是身份签名。
- 依赖 SQLite 事务和可靠本地文件系统；普通 API 没有删历史功能，但系统管理员仍能删除文件。
- 每次读取校验完整历史，历史增长会增加成本；不宣称无限规模。当前限制：1000 项、每次 200 个变化、单个完整快照 2 MB。
- 导出页将任务文字作为纯文本显示，不渲染用户提供的 Markdown 图片、链接或 HTML。
- 秘密、密钥和不该长期保留的信息不要放入历史。删除当前事项不会删除其旧版本。
- 此版没有外部文件 watcher，也没有暗中改写任何助手或应用的后台待办。

MIT License. 公开仓库只发布程序、合成示例与测试。
