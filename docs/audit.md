# 只读审计上下文导出

`audit` 提供一个供后续判断使用的证据包：显式输入的原始目标、当前完整任务快照、基线快照、区间内逐版变化，以及有来源和版本绑定的证据。程序校验结构、引用和历史一致性；遗漏、目标漂移与误解的判断留给 AI 或人工。输出中的 `assessment.status` 始终为 `not_evaluated`。

导出不修改目标、任务、版本、数据库、已有 JSON / Markdown 页面或输入证据；不存在的 store 不会被初始化。它不调用外部模型，不需要凭证、网络或定时任务。

## 快速运行合成样例

从仓库根目录运行：

```sh
PYTHONPATH="$PWD/src" python examples/audit-demo.py
```

此脚本先用正常写 API 在临时目录中建立版本 0、1、2，再执行只读导出并打印 JSON；退出时清理该临时目录。样例中的 `write` 已完成，`test` 仍在进行中；为 `test` 明确保留缺失测试报告，为较旧版本保留一条不确定的全局说明。内容全部为合成数据。

也可在一个新的合成 store 上运行 CLI：

```sh
demo_store="$(mktemp -d)/synthetic-store"
n-times-todo --store "$demo_store" init
n-times-todo --store "$demo_store" call < examples/add.json
n-times-todo --store "$demo_store" call < examples/finish.json
n-times-todo --store "$demo_store" audit --since 0 --expected-version 2 < examples/audit-context.json
```

CLI 从标准输入读取一个 JSON 对象，成功时向标准输出打印审计包并退出 0；运行时失败则输出 `{ "ok": false, "error": { "code": "...", "detail": "..." } }` 并退出 1。命令行语法错误沿用 argparse 的标准错误输出和退出码 2。`audit` 没有输出文件参数；若需要保存，重定向由 shell 完成，请将输出放在 store 和输入证据之外。包会包含完整任务、笔记和证据原文，真实审计包应保存在私有位置。

`--since` 是对比基线版本，默认 0。当前快照始终是本次数据库读事务所见的 head，不能选择历史版本冒充“当前”。`--expected-version` 可省略；指定后必须与本次所见 head 相同，否则返回 `version_conflict`。要避免将面向旧快照准备的上下文误用于新 head，调用方应传入该参数。

## Python 接口

```python
from n_times_todo import export_audit, TodoError

context = {
    "goal": {"source": "user:original-request", "text": "原始目标原文"},
    "evidence": [],
}
try:
    bundle = export_audit(
        "/your/private/todo-notebook",
        context=context,
        since=0,
        expected_version=2,
    )
except TodoError as error:
    print(error.code, error.detail)
```

函数返回可序列化为 JSON 的字典，不修改传入的 `context`。它直接接收宿主选择的 store 路径，无需先构造会初始化 store 的 `TodoStore`。此入口独立于现有的七个 `todo.*` 工具，现有工具行为保持原样。

## 输入格式与证据绑定

上下文对象只接受 `goal` 和 `evidence`，两者均可省略。`{}` 是有效输入：目标标为 `missing`，证据列表为空，不会从任务文字、`reason` 或旧页面推断目标。

- `goal` 为 `null` 或 `{ "source": "来源定位文字", "text": "原始目标原文" }`。提供目标时两项均须为非空文字。
- `evidence` 为最多 200 项的列表，每项必须包含 `id`、`source`、`status`、`version`、`task_ids`、`text`，可另带 `snapshot_hash`。
- `id` 是本次包内唯一的证据 ID。`source` 是调用方声明的来源定位文字，不是经过认证的出处。
- `version` 是该条证据明确绑定的已有版本。`task_ids` 为该版本中存在且不重复的稳定任务 ID；空列表表示全局说明，不自动绑定所有任务。
- `snapshot_hash` 可选；提供时必须与该版本的已校验快照 hash 相符。导出时每条证据都会补上实际快照 hash，便于后续复核。

来源最多 4000 个字符，目标和每条证据正文最多 20000 个字符；任务 ID 列表最多 1000 项。证据和任务 ID 沿用现有 ID 规则：1–100 个 ASCII 字符，以字母或数字开头，后续可含字母、数字、`_`、`.`、`:`、`-`。`snapshot_hash` 须为 64 位小写十六进制 SHA-256。上下文另有 2 MB 大小限制，CLI 原始 JSON 输入同样限 2 MB；超限会拒绝。

文字须为有效 UTF-8 且不含 NUL；版本须为 `0..2**63-1` 的整数，布尔值不算整数。Python 接口的 2 MB 限制按规范化后的上下文计算。

| `status` | `text` | 含义 |
|---|---|---|
| `provided` | 非空文字 | 已显式提供的材料；不表示材料真实或足以证明完成 |
| `uncertain` | 非空文字或 `null` | 保留调用方的不确定状态 |
| `missing` | 必须为 `null` | 明确声明所需材料缺失 |

`source` 中即使包含 URL 或文件路径，也只作为文字保存；入口不访问来源、不读取额外文件、不运行内容。任务、目标、来源和证据中的指令文字也是待审阅数据，不应作为后续审计者的执行指令。

完整可运行输入见 [audit-context.json](../examples/audit-context.json)。它绑定样例的版本 2；将其用于不同任务 ID 或不同版本的 store 会明确失败，不会自动调整绑定。

## 包含什么

顶层 `schema_version` 为 1，`kind` 为 `todo.audit_context`。

| 字段 | 内容 |
|---|---|
| `selection` | `since_version`、事务所见 `head_version` / `head_hash` |
| `goal` | `provided` / `missing` 状态、来源和原文；不改写原始目标 |
| `baseline`、`current` | 基线与当前的完整快照，包含稳定 task ID、版本、hash、笔记与存储元数据 |
| `comparison` | 基线到当前的端点差异，沿用现有稳定 ID 差异语义 |
| `transitions` | 基线之后到 head 的逐版元数据和变化，保留中途新增又删除、改名又恢复、restore 和无净变化版本 |
| `evidence` | 原证据、来源、状态、task ID / 版本 / 快照 hash 绑定 |
| `coverage` | 结构性的当前任务缺证列表、显式缺失证据 ID 和不确定证据 ID |
| `assessment` | 尚未进行语义判断的状态及判断边界 |
| `integrity` | 数据库历史一致性检查及其信任边界 |

目标和证据的 `content_sha256` 绑定其 `text` 的 UTF-8 原文字节；`null` 文字对应 `null`。它用于关联确切输入文字，不认证来源或证明事实。快照 hash 则沿用存储层的完整快照 hash。

证据可以引用基线之前的版本，引用仍由完整历史校验。包中只附基线和当前的完整快照，以及所选区间的变化；区间外历史版本的完整任务正文不额外附带。`integrity` 报告读取时对原数据库的验证结果，包本身不是可独立重算整条存储哈希链的数据库备份。

`coverage.current_task_ids_without_provided_evidence` 仅做机械关联：当前任务若没有 `status=provided`、`version=head` 且 `task_ids` 显式包含该 ID 的证据，就列入其中。较旧版本的证据不自动搬到新版，即使任务文字未变；全局说明也不自动覆盖各任务。此列表不等于“已经发现遗漏”，有证据也不等于“已经完成”。部分证据、矛盾材料、缺少原始目标等问题仍需后续判断。

## 只读和并发边界

入口检查现有 store 和 SQLite 文件，以 `mode=ro` 打开数据库并启用 `query_only`，在同一读事务内检查 schema、完整哈希链和请求索引，再选取 head、基线、逐版变化与证据引用。不会执行建表、初始化、追加版本、chmod 或 `sync`，也不读取物化页面。缺失页面和已有冲突页面均保持原样。

支持现有运行时默认的 SQLite rollback journal 模式。WAL store 在打开连接前明确返回 `unsupported_journal_mode`，避免 SQLite 的只读 WAL 连接仍可能创建或更新共享内存旁文件；入口不使用忽略锁和日志的 `immutable=1`。不支持外部进程同时切换 journal 模式或替换数据库文件。

正常并发写入时，包中的数据库内容来自一个一致的 SQLite 视图；包可能对应写入者提交前或提交后的完整版本，不会混合两版内容。读取也可能短暂阻塞写入者，锁超时返回 `store_unavailable`，调用方可重试读取。证据输入独立于数据库事务，其来源、内容和对版本的声明由调用方负责。

本次 head 是读取时观察到的版本，不保证导出结束时仍是最新版本。哈希链和索引一致性不能认证作者、证据真实性或防止整个数据库被替换为完整旧备份。普通文件读取还可能更新文件系统访问时间；只读保证不包括操作系统的 atime 行为。

## 失败与限制

| 错误码 | 典型情况 |
|---|---|
| `store_not_found` | store 或 `history.sqlite3` 不存在；不创建路径 |
| `unsafe_store` | 不安全的 store / 数据库路径，如符号链接 |
| `invalid_store` | 空文件或非 SQLite 数据库 |
| `unsupported_store_version` | 不支持的数据库 schema 版本 |
| `unsupported_journal_mode` | WAL 数据库 |
| `integrity_error` | 快照链、差异或请求索引不一致 |
| `store_unavailable` | 访问错误、SQLite 错误或读锁超时 |
| `invalid_arguments` | 未知字段、错误类型、负数 / 布尔版本、重复 ID、非法状态或文字；CLI JSON 重复键 / 非有限数 |
| `version_not_found` | 基线或证据所绑定版本不存在 |
| `version_conflict` | `expected_version` 与事务所见 head 不同 |
| `evidence_version_mismatch` | 声明的 `snapshot_hash` 与对应版本不符 |
| `evidence_task_not_found` | 证据所列 task ID 不在其绑定版本中 |

参数和证据引用错误不会部分导出一个声称有效的包，也不会自动更改版本或删掉不匹配材料。修改显式输入后可重新导出。缺少目标、空证据、不确定或缺失证据本身是允许保留的状态。

入口会检查完整历史并打包所选区间的变化，成本随历史增长；它不声明无限规模，也不将正常的缺证状态升级为已完成的语义结论。现有存储、差异与渲染语义保持不变。
