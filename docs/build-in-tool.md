# Built-in Review Tools v2

模型只获得冻结 Capability Profile 明确列出的 v2 工具。所有工具使用 strict JSON Schema；可选语义必须以必填 nullable 字段表达。每次调用都返回一个合法 JSON Object，顶层字段固定为 `schema_version`、`tool`、`status`、`data`、`diagnostics`。`status` 只允许 `success`、`partial`、`needs_action`、`rejected`、`failed`；恢复建议通过 Diagnostic 的完整 `suggested_arguments` 提供。

所有模型路径都是仓库相对 POSIX 路径。空字符串、`.`、`./` 表示 Snapshot 根；绝对路径、drive path、反斜杠、NUL、重复分隔符和 `..` 必须拒绝。结果回显 requested path、normalized path 与 root/directory/file scope。

| 工具 | 严格输入 | 关键语义 |
| --- | --- | --- |
| `find_files:v2` | `path`、`pattern` | 有界、不分页、稳定排序；区分空目录、无 Glob 匹配和截断 |
| `grep:v2` | `pattern`、`mode`、`path`、`file_pattern` | `literal` 用于精确文本，`regex` 在隔离 Worker 中执行；区分无候选、无内容匹配、扫描或结果受限 |
| `read_file:v2` | `path`、`version`、nullable `line_range` | 保留空行和连续行号，在完整 UTF-8/物理行边界分页并给出下一范围 |
| `get_diff:v2` | `path`、nullable `cursor` | 按完整 unified diff hunk 分页，opaque cursor 绑定 Snapshot/path/位置 |
| `comment:v2` | `comments` | 每项独立校验；接受项返回 Run 内唯一 `candidate_id` |
| `retract_comment:v2` | `candidate_ids`、`reason` | 当前 Reviewer/Run 内幂等撤销；保留审计，最终只发布 active Candidate |
| `task_done:v2` | `summary` | 首次输入提供 `review_file_count`；未覆盖时返回宿主计算的已读/缺失/总数和 `needs_action`，只有 `success` 结束 Reviewer Loop |
| `finalize_plan:v2` | Planner 完整选择 | 一次提交并校验 General 单独或至少两个专项 Reviewer |
| `verdict:v2` | `cluster_ids`、`action` | Accept/Deny 已有 Cluster，批量不构成隐式 merge |
| `merge:v2` | Cluster IDs 与完整 Comment 字段 | 合并或重写已有 Cluster，允许单 Cluster |
| `finalize_verdicts:v2` | 完成声明 | 每个 Cluster 必须恰好覆盖一次 |

## Glob 与搜索

`find_files.pattern` 与 `grep.file_pattern` 共享同一语义：不包含 `/` 的 pattern 是 recursive basename pattern，因此 `*.py` 会匹配作用域任意深度的 Python 文件；包含 `/` 的 pattern 相对 `path` 匹配 POSIX 路径，`tests/*.py` 只匹配直接子文件，`tests/**/*.py` 才递归。`**` 只有作为完整路径段才合法；`**.py`、`src/**.py` 和 `foo**bar.py` 返回 `ambiguous_recursive_glob` 及可直接重试的建议。Glob 支持 segment 内的 `*`、`?` 和字符类，不解释为正则。

精确符号、字符串或错误文本搜索优先使用 `grep` 的 `literal` mode；只有确需表达式时使用 `regex`。当 `path` 精确指向可见文件时，该文件是唯一候选，`file_pattern` 不参与过滤。

## 分页与证据覆盖

`read_file.line_range=null` 表示从第 1 行开始的有界全文页；返回 `partial` 时使用 `next_line_range` 原样续读。`get_diff` 首次调用省略 cursor；返回 `partial/diff_page_incomplete` 时使用 `next_cursor`。任何 diff hunk、物理行或 UTF-8 code point 都不能被截断；单 hunk 超限时按 Diagnostic 中的 `read_file` 参数读取对应 base/current 范围。

只有包含完整可用行的 `read_file` 成功页，以及文件 metadata 和全部 hunks 已完整返回的 `get_diff`，才形成 evidence coverage。上下文压缩占位不是证据；必须按 `suggested_arguments` 精确重读。

## Comment 与控制工具

后续证据推翻已提交意见时必须调用 `retract_comment`。在 `task_done.summary` 或最终文本中说“撤回”不会改变 Candidate 状态。只有证据覆盖达到首次输入的 `review_file_count` 后才应调用 `task_done`，不得用它试探是否完成；`missing_review_files` 会回显宿主计算的覆盖进度。`task_done` 成功后 Comment 状态不可再改变。

所有控制工具与证据工具使用同一 Tool Result 合同；不存在旧版 Tool Contract、`accepted` outcome 启发式、文件完成声明工具或非严格 Schema 例外。
