# Review Tool Contract v2 模型适配硬切执行计划

> 本计划是 [`2026-08-09-multi-agent-review-v2-hard-cut.md`](./2026-08-09-multi-agent-review-v2-hard-cut.md) 的后续增量计划，专门重写模型可见工具契约、上下文压缩重读和 Reviewer Comment 撤销语义。两份计划不冲突时共同生效；涉及工具返回、重复调用判定、证据压缩或 Comment 生命周期时，以本计划为准。
>
> v2 尚未发布。本计划要求原地破坏性升级，不保留旧参数、旧返回、旧状态推断、兼容 Adapter、双注册或 fallback。实现 Agent 不得为了“保险”重新引入兼容分支。

**目标：** 让 `find_files`、`grep`、`read_file`、`get_diff` 及所有控制类工具具备统一、严格、始终合法的 JSON v2 返回；让路径、Glob、搜索、分页和错误恢复符合代码模型的常见使用习惯；让上下文压缩引导的精确重读不触发无进展熔断；允许 Reviewer 撤销已被后续证据推翻的 Comment。

**技术栈：** Python 3.12、Pydantic v2、OpenAI Agents SDK、OpenAI Responses API、异步文件/Git Adapter、pytest、Ruff、mypy strict。

**执行原则：** 严格测试驱动。每个 Task 先提交能证明旧行为错误的失败测试，再做最小实现，聚焦测试变绿后才进入下一 Task。不得覆盖工作区已有改动；开始每个 Task 前检查 `git status --short`。

## 1. 问题基线与根因

本计划源于 Transcript：

```text
data/artifacts/transcripts/review_7aae3df27e6a4416a766339dbc422e7f.json
```

该次运行已完成，模型为 `deepseek-v4-flash`，共 491 条 Transcript 记录、119 次模型调用、56 次上下文压缩、96 个被压缩结果，累计输入约 500 万 token。135 次工具调用分布为：

| 工具 | 次数 |
| --- | ---: |
| `find_files` | 5 |
| `grep` | 36 |
| `read_file` | 52 |
| `get_diff` | 37 |
| `comment` | 1 |
| `task_done` | 4 |

已经确认的失配：

1. `grep` 的 36 次结果中有 18 次为空，其中 16 次是目录搜索配合 `file_pattern="*.py"`。当前 `_matches_posix_path_glob` 把 `*.py` 解释成仅匹配目录直接子项，模型则按 ripgrep/常用搜索工具习惯理解为递归匹配任意层级 basename，因此把稳定实现误判为 “flaky”。
2. `find_files` 同样采用分段 Path Glob。模型尝试 `compiler*.py`、`**.py` 得到空结果，改成 `**/*plan*.py` 后才返回文件。契约没有明确区分 basename pattern 与 relative-path pattern，也没有为错误的 `**.py` 给出可执行修正。
3. `read_file._add_line_prefixes` 丢弃空行，模型看到行号跳跃，无法确认是源文件空行还是工具漏读。
4. 同一 `get_diff` 调用第二次命中重复告警时，`ToolExecutionLimiter._attach_warning` 把中文自然语言直接追加到 JSON 字符串后面，导致整个返回不再是合法 JSON。
5. 当前无进展熔断按整次 Agent Run 全局累计相同 `(tool, arguments, result)` 指纹。上下文压缩却明确要求模型按原参数重读已压缩证据；正常恢复动作因此会被计为无进展。
6. 模型曾提交一个 Comment，后续读取 `_run_id` 实现后发现原判断错误，但只能在自然语言总结中说“撤回”。Collector 仍持久化先前 Candidate，因为没有模型可见撤销工具。
7. `reject_unknown_arguments`、Pydantic 参数错误、工具异常、Planner/Verifier 控制工具尚未共享同一返回合同；“举一反三”后仍可能从其他工具产生非 JSON 或难以分类的结果。

上述数字是回归评测基线，不是硬编码到生产逻辑的规则。

## 2. SDK 与模型语义定位

实现时必须坚持以下边界：

- 当前 OpenAI Agents SDK 提供 `function_tool`、`FileSearchTool`、`ShellTool`、`LocalShellTool`、`ApplyPatchTool`、`ComputerTool` 等能力，但没有可直接替换本项目 `grep`、`find_files`、`read_file`、`get_diff` 的内建实现。
- `FileSearchTool` 面向 Vector Store/语义检索，不满足冻结 Snapshot、精确路径、逐行证据、Diff 覆盖和只读隔离要求。
- Shell 工具虽然能模拟 `rg`/`find`/`sed`/`git diff`，但会破坏当前最小权限、Snapshot 可见范围、内容哈希验证、输出上限和稳定审计边界，不得引入。
- OpenAI Function Calling 只规定自定义工具的名称、说明、JSON Schema 和调用协议，不存在 `grep` 等任意自定义名称的隐藏官方 ABI。
- 因此继续维护项目内建只读工具是正确方向；模型适配应通过严格 Schema、明确说明、结构化结果和评测完成。语义应贴近模型常见的 ripgrep、basename glob、行号读取和 unified diff 使用习惯，但不得宣称与任一模型训练集存在不可验证的逐字 ABI。
- 真实 OpenAI 与兼容网关评测必须显式启用，不进入默认测试套件。

实现和评审时以官方一手资料核对 SDK 行为：

- Function Calling 与 strict schema：<https://developers.openai.com/api/docs/guides/function-calling>
- 模型工具设计与评测建议：<https://developers.openai.com/api/docs/guides/latest-model>
- Agents Sandbox 的文件系统/Shell 能力边界：<https://developers.openai.com/api/docs/guides/agents/sandboxes>

## 3. 已确认决策

### 3.1 版本与兼容

- 所有变更继续使用 v2 标识，直接重写未发布的 v2。
- 不新建 v3，不注册旧 v2 别名，不解析旧返回，不保留旧参数组合。
- Tool Contract Reference、Capability Profile、Prompt 和实现必须在同一次变更中同步。
- 删除所有依赖 `accepted` 布尔值、特定字段存在性或自然语言内容猜测执行结果的旧启发式逻辑。

### 3.2 统一 Tool Result

所有模型可见工具，无论成功、部分成功、需要后续动作、输入拒绝或内部失败，都必须返回单个合法 JSON Object：

```json
{
  "schema_version": "2",
  "tool": "grep",
  "status": "success",
  "data": {},
  "diagnostics": []
}
```

顶层字段固定且全部必填：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `schema_version` | string literal | 固定为 `"2"` |
| `tool` | string | 固定为实际调用的公开工具名 |
| `status` | enum | `success\|partial\|needs_action\|rejected\|failed` |
| `data` | object | 工具专属稳定 payload；无数据时为 `{}` |
| `diagnostics` | array | 稳定结构；无诊断时为 `[]` |

每条 Diagnostic：

```json
{
  "code": "no_content_matches",
  "message": "没有内容匹配给定模式。",
  "field": "pattern",
  "retryable": true,
  "suggested_arguments": {
    "pattern": "run_id",
    "mode": "literal",
    "path": "backend/src",
    "file_pattern": "*.py"
  }
}
```

Diagnostic 字段规则：

- `code`、`message`、`retryable` 必填。
- `field` 与 `suggested_arguments` 可选；没有意义时省略，不输出 `null`。
- `code`、字段名、枚举值必须稳定且只用英文；`message` 按运行语言本地化。
- `suggested_arguments` 必须是该工具完整、可直接重试且符合严格 Schema 的参数 Object，不能只返回零散提示。
- 不得把调用 ID、内部 Snapshot ID、绝对路径、Secret、异常堆栈或源码正文放入 Diagnostic。

状态的唯一宿主分类：

| status | Host outcome | 含义 |
| --- | --- | --- |
| `success` | accepted | 请求完整执行且结果完整 |
| `partial` | accepted | 已返回有效证据，但受分页/条数/字节边界限制，需按游标或建议继续 |
| `needs_action` | rejected | 没有形成可接受完成，需要模型执行明确后续动作 |
| `rejected` | rejected | 输入、状态或业务前置条件不允许执行 |
| `failed` | rejected | 内部执行失败，不能把结果当证据 |

Host 只按 `status` 分类，禁止再解析自然语言或工具专属字段。缺字段、未知状态、顶层非 Object、非 JSON 字符串均归为 `unclassified`，并以内部契约错误记录；不得伪装为成功。

如果内部工具意外返回非 JSON，模型边界必须兜底为：

```json
{
  "schema_version": "2",
  "tool": "read_file",
  "status": "failed",
  "data": {},
  "diagnostics": [
    {
      "code": "invalid_internal_tool_result",
      "message": "工具返回不符合内部合同。",
      "retryable": false
    }
  ]
}
```

同时在普通运行日志中只记录工具名、错误码和关联 ID，不记录敏感正文；Transcript 保存模型实际看到的结构化失败结果。

### 3.3 参数严格性

- 所有 Function Tool 使用严格 JSON Schema；每个 property 都必须出现在 `required`。
- 可选语义用“必填但可为 null”的字段表达，不再修改 SDK 生成 Schema 来关闭 strict。
- 顶层 `additionalProperties=false`；嵌套 Object 同样拒绝未知字段。
- 非法 JSON、未知字段、类型错误、枚举错误、范围错误统一返回 `rejected`，错误码分别稳定为 `invalid_arguments_json`、`unknown_argument`、`invalid_argument_type`、`invalid_argument_value` 或更精确业务码。
- `reject_unknown_arguments` 必须在 SDK/Pydantic 解析前后都能守住边界，并且返回 Tool Result JSON；不得返回裸字符串。

## 4. 通用路径与 Glob 合同

### 4.1 模型路径规范化

建立唯一的模型路径规范化模块，供四个证据工具、Comment 定位和游标校验复用：

| 输入 | 结果 |
| --- | --- |
| `""` | Snapshot 根目录 |
| `"."`、`"./"` | 规范化为 `""` |
| `"./src"` | `"src"` |
| `"src/"` | `"src"` |
| `"src//a.py"` | 拒绝，不静默修复歧义路径 |
| `/src/a.py`、Windows drive path | 拒绝绝对路径 |
| `..`、任意 `../` segment | 拒绝逃逸 |
| 反斜杠、NUL | 拒绝 |

规范化结果内部至少携带：

```python
requested_path: str
normalized_path: str
scope_type: Literal["root", "directory", "file"]
```

模型结果需要回显 `requested_path`、`normalized_path` 和最终识别的 `scope_type`，减少模型对作用域的猜测。不得暴露绝对 Worktree 路径。

### 4.2 Glob 语义

`find_files.pattern` 与 `grep.file_pattern` 共享完全相同的实现和说明：

- 不包含 `/` 的 pattern 是递归 basename pattern：`*.py`、`test_*.py`、`compiler*.py` 匹配作用域内任意深度文件的 basename。
- 包含 `/` 的 pattern 是相对 `path` 的 POSIX path pattern：`tests/*.py` 只匹配 `tests` 直接子文件；`tests/**/*.py` 才递归。
- `**` 仅在作为完整 path segment 时具有递归含义。
- `**.py`、`src/**.py`、`foo**bar.py` 等把 `**` 混入普通 segment 的写法拒绝为 `ambiguous_recursive_glob`，并返回合法建议，例如把 `**.py` 修正为 `*.py`。
- `/` pattern 不匹配 basename 之外的隐式前缀；所有匹配都相对规范化后的 `path`。
- 结果按规范化仓库相对 POSIX 路径稳定排序。
- Glob 不解释为正则；字符类、`?`、`*` 的支持范围必须在单元测试和 Prompt 中一致。

## 5. 四个证据工具的最终合同

### 5.1 `find_files:v2`

严格输入：

```json
{"path":"","pattern":"*.py"}
```

`path`、`pattern` 均必填；根目录使用空字符串，禁止依赖缺省值。

成功/空结果 `data`：

```json
{
  "requested_path": "./backend/src/",
  "normalized_path": "backend/src",
  "scope_type": "directory",
  "requested_pattern": "*.py",
  "effective_pattern": "*.py",
  "pattern_scope": "recursive_basename",
  "visible_file_count": 120,
  "matched_count": 35,
  "returned_count": 35,
  "paths": ["backend/src/codelens/app.py"],
  "truncated": false
}
```

规则：

- 保持有界、不分页、稳定排序。
- `matched_count` 是所有匹配数，`returned_count` 是实际返回数，`visible_file_count` 是路径作用域内全部可见文件数。
- 匹配超过 `max_results` 时为 `partial`、`truncated=true`，Diagnostic 使用 `result_limit_reached`；由于工具不分页，建议缩小 `path` 或 `pattern`。
- 空目录/不存在可见后代：`success`，`paths=[]`，Diagnostic `empty_directory_scope`。
- 目录存在且有可见文件但 Glob 无匹配：`success`，Diagnostic `no_files_match_pattern`，并回显 candidate 计数；不能与空目录混淆。
- 路径指向文件时拒绝为 `path_is_not_directory`，建议将 path 改为父目录和 basename pattern。

### 5.2 `grep:v2`

严格输入：

```json
{
  "pattern": "_run_id",
  "mode": "literal",
  "path": "backend/src",
  "file_pattern": "*.py"
}
```

四个字段全部必填。`mode` 只允许 `literal`、`regex`：

- `literal` 不解释正则元字符，作为模型查符号、字符串和错误文本的默认推荐模式。
- `regex` 沿用隔离线程/Worker、硬超时和结果上限；非法正则返回 `invalid_regular_expression`。
- `path` 精确指向可见文件时，以该文件为唯一候选，`file_pattern` 不参与过滤；这是显式优先级，不附加“忽略参数”噪声告警。
- `path` 为目录/根时，使用共享 Glob 过滤候选文件。
- 二进制文件不搜索，但计入 `skipped_binary_file_count`。

`data`：

```json
{
  "requested_path": "backend/src",
  "normalized_path": "backend/src",
  "scope_type": "directory",
  "pattern": "_run_id",
  "mode": "literal",
  "file_pattern": "*.py",
  "pattern_scope": "recursive_basename",
  "candidate_file_count": 84,
  "scanned_file_count": 84,
  "skipped_binary_file_count": 0,
  "scanned_bytes": 240000,
  "matched_file_count": 2,
  "match_count": 4,
  "returned_match_count": 4,
  "matches": [
    {"path":"backend/src/codelens/x.py","line_number":12,"line":"..."}
  ],
  "truncated": false
}
```

空结果必须区分：

- `candidate_file_count=0`：`success` + `no_candidate_files`，建议修正 path/file_pattern。
- 有候选且扫描完成但无命中：`success` + `no_content_matches`。
- 扫描字节上限先到：`partial` + `scan_limit_reached`，即使当前 matches 为空也不能伪装成确定“无匹配”。
- 匹配条数上限先到：`partial` + `result_limit_reached`。
- Regex Worker 超时：没有可靠完整结果时 `failed` + `regular_expression_timed_out`；若实现能保留已确认前缀结果，则 `partial`，但必须由测试固定，不允许不确定分支。

所有 match 按 path、line_number 稳定排序；返回原始行文本的边界必须受总结果字节限制。

### 5.3 `read_file:v2`

严格输入：

```json
{
  "path": "backend/src/codelens/x.py",
  "version": "current",
  "line_range": {"start_line": 1, "end_line": 200}
}
```

全文读取时 `line_range` 必须显式为 `null`。`version` 固定为 `current|base|head`。

`data`：

```json
{
  "requested_path": "backend/src/codelens/x.py",
  "normalized_path": "backend/src/codelens/x.py",
  "scope_type": "file",
  "version": "current",
  "requested_line_range": {"start_line":1,"end_line":200},
  "actual_line_range": {"start_line":1,"end_line":180},
  "total_lines": 460,
  "returned_bytes": 12500,
  "content": "   1 | from __future__ import annotations\n   2 | \n   3 | ...",
  "truncated": true,
  "next_line_range": {"start_line":181,"end_line":380}
}
```

规则：

- 保留每一个物理行，包括空行和只含空白字符的行；行号连续、与原文件一致。
- 行前缀格式必须固定并由快照测试保护。空行也输出前缀与换行，不能通过 `if line` 过滤。
- 不在 UTF-8 code point 中间截断。若单个超长行超过字节上限，可返回 UTF-8 安全前缀，但必须增加 `line_content_truncated=true`、`partial` 和 `line_exceeds_read_limit`；不得声称获得完整行证据。
- 正常分页优先在完整物理行边界停止，并给出完整、可直接重试的 `next_line_range`。
- `requested_line_range` 为 `null` 时，返回从第 1 行开始的有界全文页；截断则为 `partial` 并给出下一页。
- 请求 end 超过 EOF 可 clamp，`actual_line_range` 反映实际值；start 超过 EOF 返回 `rejected` + `line_range_out_of_bounds`。
- 只有 `success` 或包含至少一个完整可用行的 `partial` 才把 Review path 记入 evidence coverage；`rejected`、`failed`、仅超长行不完整前缀不计入。

### 5.4 `get_diff:v2`

严格输入：

```json
{"path":"backend/src","cursor":null}
```

`path`、`cursor` 都必填；首屏 `cursor=null`。

从“按文件切字节”重写为“按文件 + 完整 unified diff hunk”分页：

- 永不截断 UTF-8 code point、Diff 物理行或 hunk。
- Page 可以含多个文件；每个文件可以含本页的一组完整 hunk。
- Cursor 至少绑定：schema version、规范化 path、稳定 file index、next hunk index、冻结 Snapshot identity 的不可逆 hash。
- Cursor 对模型保持 opaque base64url 字符串；解码后任何字段、范围、Snapshot 或 path 不匹配都返回 `invalid_diff_cursor`。
- 不在返回或 Diagnostic 中暴露 Snapshot ID、绝对路径或内部 Artifact reference。
- 重命名、增删文件、无末尾换行、二进制标记和 metadata-only diff 都要保持合法 unified diff 语义。

`data`：

```json
{
  "requested_path": "backend/src",
  "normalized_path": "backend/src",
  "scope_type": "directory",
  "total_file_count": 5,
  "returned_file_count": 2,
  "completed_file_count": 1,
  "total_hunk_count": 12,
  "returned_hunk_count": 4,
  "files": [
    {
      "path": "backend/src/codelens/a.py",
      "change_type": "modified",
      "old_path": null,
      "header": "diff --git ...",
      "hunks": ["@@ ..."] ,
      "is_complete": true,
      "next_hunk_index": null
    }
  ],
  "has_more": true,
  "next_cursor": "opaque-value"
}
```

规则：

- 完整结束为 `success`；还有 cursor 为 `partial` + `diff_page_incomplete`。
- 一个 hunk 单独就超过最大返回字节时，不截断 hunk。返回该文件/hunk 的 base/current 行范围和 `read_file` 完整建议参数；若本页此前已有完整 hunk则为 `partial`，否则为 `needs_action`，Diagnostic `diff_hunk_exceeds_limit`。
- 超大 hunk 不能把文件标记 complete，也不能增加 `completed_file_count` 或 evidence coverage。
- 文件只有在其所有 Diff metadata 和全部 hunk 被完整返回后才计入 `reviewed_paths`。
- Cursor 续读必须精确从下一未返回 hunk 开始；重复同一 cursor 产生相同原始 Tool Result。

## 6. Reviewer 输出工具最终合同

### 6.1 `comment:v2`

保留现有 Comment Finding 输入字段与定位校验，重写输出和 Candidate 生命周期。

每个接受项返回：

```json
{
  "input_index": 0,
  "candidate_id": "candidate_...",
  "path": "backend/src/codelens/x.py",
  "side": "current",
  "title": "..."
}
```

`data` 至少包含 `submitted_count`、`accepted_count`、`rejected_count`、`active_comment_count`、`accepted_comments`、`rejected_comments`。部分接受为 `partial`；全部接受为 `success`；全部拒绝为 `rejected`。每个拒绝项保留 `input_index` 和稳定 `code`，不得只返回自然语言 reason。

`candidate_id` 由 Host 生成、Agent Run 内唯一且不可由模型指定。它进入 Transcript 和审计，但不成为最终 Finding ID。

### 6.2 新增 `retract_comment:v2`

Reviewer Profile 工具顺序固定为：

```text
find_files, grep, read_file, get_diff, comment, retract_comment, task_done
```

严格输入：

```json
{
  "candidate_ids": ["candidate_..."],
  "reason": "Later evidence shows _run_id is deterministic."
}
```

规则：

- `candidate_ids` 非空、去重、受 batch size 限制；`reason` 去空白后非空且有长度上限。
- 只能撤销当前 Reviewer 当前 Agent Run 创建的 Candidate。
- 幂等返回每项状态：`retracted`、`already_retracted`、`unknown_candidate`。
- 至少一个实际撤销且没有未知项：`success`；混合成功/未知：`partial`；全部未知：`rejected`；全部已撤销：`success`，但 Diagnostic 可标记 `no_state_change`。
- `data` 包含逐项结果、`retracted_count`、`already_retracted_count`、`unknown_count`、`active_comment_count`。
- 撤销不物理删除 Candidate；保留 payload、创建顺序、撤销原因和状态转换用于审计。
- `candidate_batch()` 和最终 Finding 流程只返回 active Candidate。
- 撤销后再次提交相同内容生成新 Candidate；若现有去重逻辑识别为同一业务 Candidate，则以“重新激活最新 payload、排到接受序列末尾”的单一规则实现并用测试固定，不能同时保留两个 active 副本。
- `task_done` 被接受后，Collector 防御性拒绝任何 Comment 或撤销调用，错误码 `reviewer_already_completed`。正常 SDK loop 会在 accepted `task_done` 后立即停止，这条规则用于防并发/异常调用。

Prompt 必须明确：后续证据推翻已提交意见时必须调用 `retract_comment`；在 `task_done.summary` 或最终文本中说“撤回”不改变 Candidate 状态。

### 6.3 `task_done:v2`

- 未覆盖所有 Review 文件且仍可重试：`needs_action` + `missing_review_files` Diagnostic，`suggested_arguments` 不适用于单个证据工具时可省略；`data` 保留 retry count 和缺失路径。
- 达到强制完成阈值：`success`，回显 `forced_completion=true`、`incomplete_files` 和 `active_comment_count`。
- 正常完成：`success`，包含 `active_comment_count`、`forced_completion=false`。
- 重复完成：`rejected` + `reviewer_already_completed`。
- 只有 Host 接受 `status=success` 的 `task_done` 后才停止 SDK loop。

## 7. Planner、Verifier 与运行时控制工具

统一合同必须覆盖所有模型可见控制工具，而不只 Reviewer：

- Planner：`finalize_plan:v2`。
- Final Verifier：`verdict:v2`、`merge:v2`、`finalize_verdicts:v2`。
- 任何实际注册的其他 model-visible output/control tool。

每个工具都必须：

- 使用同一 Tool Result serializer、状态枚举和 Diagnostic；
- 输入拒绝返回 `rejected`，缺少前置动作返回 `needs_action`，部分批量接受返回 `partial`；
- 成功结果返回工具专属 `data`，不能返回裸字符串或只含 `accepted` 的临时 Object；
- Tool Contract Reference 保持 v2，并通过 Profile 完整性测试确保实现、Prompt 描述和注册同时存在。

## 8. 无进展熔断与上下文压缩重读

### 8.1 连续重复熔断

将 `ToolExecutionLimiter` 的全局 `_result_counts` 改为一个连续状态：

```python
last_fingerprint: str | None
consecutive_identical_count: int
```

规则：

1. 指纹由 `tool_name + canonical arguments + original Tool Result` 生成。
2. canonical JSON 使用稳定 key 排序和紧凑编码；非 JSON 参数/结果走稳定字节表示，但模型边界仍兜底成 JSON。
3. Diagnostic 本地化 message、重复告警本身、call ID、时间戳不得进入指纹。
4. 当前指纹不同于上一次时，count 重置为 1；相同才递增。
5. count=2 时不改变 status，在现有 Tool Result 的 `diagnostics` 追加 `repeated_identical_call`，包含距离熔断剩余次数。
6. count 达到配置的 `max_identical_tool_results` 时抛出 provider-neutral `ToolLoopDetectedError`。
7. A → B → A 不构成连续重复，第三次 A 的 count 为 1。
8. 每次调用仍正常消耗总 tool-call budget 和 timeout；重复豁免只影响 no-progress streak。

严禁再通过字符串拼接附加告警。Serializer 必须解析/验证原结果后追加 Diagnostic，再重新输出合法 JSON。

### 8.2 压缩证据重读豁免

新增 Agent Run scoped `CompactedEvidenceReplayRegistry`，由上下文压缩器与 limiter 共享。

注册流程：

1. 某个 evidence `function_call_output` 第一次被压缩时，读取其原 `call_id`、工具名和原始完整 arguments。
2. 对 arguments 做与 limiter 相同的 canonicalization。
3. 按 `(tool_name, canonical_arguments)` 增加一次 replay allowance，并记录该 allowance 来自哪个 original call ID。
4. 同一 original call ID 在后续 filter pass 再次出现，不重复增加 allowance。

消费流程：

1. 后续模型精确调用同一 evidence tool 和 arguments 时，在执行成功后消费一份 allowance。
2. 调用仍消耗 tool call budget、执行 timeout 和实际 I/O；只是不进入/增加 no-progress streak。
3. 消费后将连续 streak 重置为空，防止压缩恢复前后的重复状态串联。
4. 参数有任何语义差异都不消费 allowance。
5. allowance 只适用于四个 evidence tools，不适用于 `comment`、`retract_comment`、`task_done`、Planner 或 Verifier 输出工具。
6. 新结果以后再次真正被压缩，可由其新 call ID 再注册一份 allowance。

上下文压缩 placeholder 也必须是合法 v2 JSON Tool Result，建议固定：

```json
{
  "schema_version": "2",
  "tool": "get_diff",
  "status": "needs_action",
  "data": {
    "compaction": "codelens_context_compaction_v2",
    "original_call_id": "call_...",
    "arguments": {"path":"...","cursor":null},
    "original_bytes": 12000,
    "reread_allowed": true
  },
  "diagnostics": [
    {
      "code": "evidence_compacted",
      "message": "该历史结果已从上下文压缩；请使用完全相同的参数重新调用工具。",
      "retryable": true,
      "suggested_arguments": {"path":"...","cursor":null}
    }
  ]
}
```

Placeholder 不是证据，不增加 `reviewed_paths`。它不得包含原结果正文、Snapshot ID 或绝对路径。`original_call_id` 只用于 Run 内审计和去重。

## 9. 目标模块与依赖边界

新增模块：

```text
backend/src/codelens/review/domain/tool_results.py
backend/src/codelens/review/infrastructure/model_paths.py
backend/src/codelens/review/infrastructure/evidence_replay.py
```

职责：

- `domain/tool_results.py`：provider-neutral immutable values，包括 `ToolResultStatus`、`ToolDiagnostic`、`ToolResult`、校验和 canonical serializer。不得 import OpenAI SDK。
- `infrastructure/model_paths.py`：Snapshot 模型可见相对路径和 Glob 解析/匹配；不得自行读文件或执行 Git。
- `infrastructure/evidence_replay.py`：Agent Run scoped replay allowance registry；使用 lock 保证潜在并发工具调用下注册/消费原子性。
- `snapshot_tools.py`：只负责冻结 Snapshot 上的证据读取、预算、覆盖和工具专属 payload。
- `tool_contract.py`：SDK Function Tool 边界、严格参数错误转换、统一结果验证、总调用预算、timeout、连续重复检测。
- `openai_runtime.py`：组装同一 Run 的 compaction tracker/replay registry/limiter，处理 Transcript 和 provider stream；不要把领域 Tool Result 规则重新实现一遍。
- `comment_collector.py`：Candidate active/retracted 状态机与 Reviewer 控制工具。

依赖方向必须符合 `docs/ARCHITECTURE.md`：领域值不依赖 SDK、文件系统、Git、Prompt 或 HTTP；Infrastructure 可依赖领域合同；Prompt 只描述合同，不成为业务规则来源。

## 10. 执行任务

### Task 0：冻结回归夹具和权威架构合同

**文件：**

- 修改：`docs/ARCHITECTURE.md`
- 修改：`docs/build-in-tool.md`
- 修改：`docs/agent-loop.md`
- 创建：`backend/tests/evaluation/review/test_tool_contract_transcript_replay.py`（若现有 evaluation 目录命名不同，沿用当前目录但保持职责）
- 使用只读夹具：`data/artifacts/transcripts/review_7aae3df27e6a4416a766339dbc422e7f.json`

- [ ] 先把本计划第 3–9 节的稳定跨边界合同写入 `ARCHITECTURE.md`；文档只保存长期架构规则，不复制实施 checklist。
- [ ] 在工具文档明确 basename/path Glob、literal/regex、严格 nullable 参数、Tool Result 状态和 Diff hunk cursor。
- [ ] 在 Agent loop 文档明确连续重复与 compaction replay allowance。
- [ ] 建立 Transcript 分析/回放测试工具，只抽取工具调用模式和结果形态；不得把 500 万 token 全量放进默认单元测试上下文。
- [ ] 固定基线断言：目录 `*.py` 可复现空结果失配、重复告警造成非 JSON、压缩后精确重读会命中旧全局计数、自然语言撤回不改变 Candidate。
- [ ] 数据 Artifact 可能含源码/Prompt，测试日志不得打印其全文；默认测试可使用脱敏精简派生夹具。

**验证：**

```bash
uv run --project backend pytest backend/tests/evaluation/review/test_tool_contract_transcript_replay.py -v
rg -n "Tool Result|retract_comment|recursive basename|连续重复|context compaction" docs/ARCHITECTURE.md docs/build-in-tool.md docs/agent-loop.md
```

### Task 1：统一 Tool Result 领域合同

**文件：**

- 创建：`backend/src/codelens/review/domain/tool_results.py`
- 创建：`backend/tests/unit/review/test_tool_results.py`
- 修改：`backend/src/codelens/review/infrastructure/tool_contract.py`
- 修改：`backend/tests/unit/review/test_tool_contract.py`

- [ ] 先测五种 status、Diagnostic 可选字段、稳定 JSON 顺序、Unicode、本地化 message 和 suggested arguments。
- [ ] 测非法 status、空 code、非 Object data、未知顶层字段和不可 JSON 序列化对象被拒绝。
- [ ] 测 Host outcome 只由 status 得出，删除旧 accepted/字段启发式后未知返回为 unclassified。
- [ ] 实现 typed immutable values 和唯一 serializer/parser。
- [ ] 将 `reject_unknown_arguments` 的裸字符串改为 `rejected` Tool Result。
- [ ] 加边界兜底：任何内部非 JSON/非法 envelope 变成 `failed/invalid_internal_tool_result`。
- [ ] 为 SDK/Pydantic validation error 建立稳定 code 映射，不把 provider 异常文本直接暴露给模型。

**验证：**

```bash
uv run --project backend pytest backend/tests/unit/review/test_tool_results.py backend/tests/unit/review/test_tool_contract.py -v
uv run --project backend ruff check backend/src/codelens/review/domain/tool_results.py backend/src/codelens/review/infrastructure/tool_contract.py
uv run --project backend mypy backend/src/codelens/review/domain/tool_results.py backend/src/codelens/review/infrastructure/tool_contract.py
```

### Task 2：统一模型路径和 Glob

**文件：**

- 创建：`backend/src/codelens/review/infrastructure/model_paths.py`
- 创建：`backend/tests/unit/review/test_model_paths.py`
- 修改：`backend/src/codelens/review/infrastructure/snapshot_tools.py`
- 修改：`backend/tests/unit/review/test_snapshot_tools.py`

- [ ] 表驱动测试第 4 节全部路径规范化案例。
- [ ] 表驱动测试 slashless recursive basename 和 slash-containing relative path 行为。
- [ ] 测 `*.py` 命中 `a.py`、`src/a.py`、`src/deep/a.py`；`tests/*.py` 不命中 `tests/unit/a.py`；`tests/**/*.py` 命中递归路径。
- [ ] 测 `**.py` 等歧义写法返回建议而非空结果。
- [ ] 测稳定排序、隐藏文件、Unicode path、symlink entry、文件/目录重名不可能状态。
- [ ] 删除 `snapshot_tools.py` 内重复 `_directory_prefix`/`_matches_posix_path_glob` 语义，统一走新模块。

### Task 3：重写 `find_files:v2`

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/snapshot_tools.py`
- 修改：`backend/tests/unit/review/test_snapshot_tools.py`

- [ ] 先覆盖 success、empty directory、no glob match、truncated、invalid path、ambiguous glob。
- [ ] 使用真实临时 Snapshot manifest，包含三层嵌套、空目录不可见事实、排除文件和 Unicode path。
- [ ] 实现第 5.1 节所有计数和 Diagnostic。
- [ ] 确认空匹配仍是合法 JSON success，不抛异常且不诱导模型重复同一调用。

### Task 4：重写 `grep:v2`

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/snapshot_tools.py`
- 修改：`backend/tests/unit/review/test_snapshot_tools.py`

- [ ] 先测 literal 与 regex 差异，例如 `a.b` 在 literal 模式不匹配 `axb`。
- [ ] 测目录 + `*.py` 递归、多层命中、精确文件 path 忽略 file_pattern、候选为零、内容无命中。
- [ ] 测 scan byte limit 与 result limit 分别产生 partial，计数准确。
- [ ] 测非法 regex、regex timeout、二进制跳过、UTF-8 replacement 边界和稳定排序。
- [ ] 保留隔离 regex 搜索和硬 timeout；不得在事件循环直接执行可能灾难回溯的表达式。
- [ ] 工具结果总字节不得超过 Runtime 限制；限制本身也要有测试。

### Task 5：重写 `read_file:v2`

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/snapshot_tools.py`
- 修改：`backend/tests/unit/review/test_snapshot_tools.py`
- 修改：`backend/tests/contract/review/test_openai_runtime.py`

- [ ] 先用含连续空行、只含空格行、CRLF、无末尾换行、中文和 emoji 的真实临时文件写失败测试。
- [ ] 断言行号连续、空行不消失、actual/requested range 与 total_lines 准确。
- [ ] 测 `line_range=null`、正常 range、EOF clamp、start beyond EOF、max_lines、max_bytes、单超长行。
- [ ] 把 schema 改成必填 nullable `line_range`，删除 `strict_json_schema=False` 和手改 required 数组。
- [ ] 分离“返回了任意字符串”和“形成完整可用 evidence”的覆盖判定。

### Task 6：按完整 Hunk 重写 `get_diff:v2`

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/snapshot_tools.py`
- 修改：`backend/tests/unit/review/test_snapshot_tools.py`
- 修改：`backend/tests/contract/review/test_openai_runtime.py`

- [ ] 使用真实临时 Git 仓库覆盖 added、modified、deleted、renamed、symlink、binary、metadata-only、多个 hunk 和无末尾换行。
- [ ] 测文件 path、目录 path、根 path、空匹配和非 Review file。
- [ ] 测一页跨文件、同文件跨页、cursor 稳定续读、cursor path/Snapshot/index 篡改拒绝。
- [ ] 测最大字节边界前完整 hunk 被保留，任何 hunk/行/UTF-8 都不被截断。
- [ ] 测单超大 hunk 返回 read_file ranges，且不计 coverage。
- [ ] `cursor` 改为 required nullable，删除非 strict schema 绕行。
- [ ] 只有完整读取一个文件的全部 hunk 才更新 `reviewed_paths`。

### Task 7：连续熔断与合法 JSON 告警

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/tool_contract.py`
- 修改：`backend/tests/unit/review/test_tool_contract.py`

- [ ] 先测第二次完全重复返回合法 JSON 且追加 Diagnostic。
- [ ] 测 A→A 达到 warning、A→B→A 重置、同 args 不同 result 重置、同 JSON 不同 key 顺序仍相同。
- [ ] 测达到阈值抛 `ToolLoopDetectedError`，同时每次仍消耗 call budget。
- [ ] 测本地化 message 不影响指纹，warning Diagnostic 不被下一次原始指纹吸收。
- [ ] 删除 `_result_counts` 和 `_attach_warning` 字符串拼接。

### Task 8：压缩重读 Registry

**文件：**

- 创建：`backend/src/codelens/review/infrastructure/evidence_replay.py`
- 创建：`backend/tests/unit/review/test_context_compaction.py`
- 修改：`backend/src/codelens/review/infrastructure/openai_runtime.py`
- 修改：`backend/src/codelens/review/infrastructure/tool_contract.py`
- 修改：`backend/tests/unit/review/test_openai_runtime_stream.py`

- [ ] 先测压缩 placeholder 是完整 Tool Result JSON、包含精确 arguments 和 suggested_arguments、不含原 evidence。
- [ ] 测同一 call ID 多次 filter 只注册一次；两个不同 call ID/相同 args 注册两份 allowance。
- [ ] 测精确重读消费 allowance、重置 streak、仍消耗总调用预算；不同 args 不消费。
- [ ] 测 allowance 只适用于四个 evidence tool；Comment/控制工具重复不豁免。
- [ ] 测重读结果再次压缩可注册新 allowance。
- [ ] 将 marker 硬切为 `codelens_context_compaction_v2`，不识别 v1 marker。
- [ ] 保持现有 compaction count/bytes 指标，同时新增 replay allowance registered/consumed 指标（若加入 Process Report，执行 Task 11 的投影同步）。

### Task 9：Comment 状态机与撤销工具

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/comment_collector.py`
- 修改：`backend/tests/unit/review/test_comment_collector_v2.py`
- 修改：`backend/src/codelens/review/infrastructure/capability_tools.py`
- 修改：`backend/tests/unit/review/test_capability_tools.py`
- 修改：`backend/src/codelens/capabilities/infrastructure/builtin_profiles.py`
- 修改：`backend/tests/unit/capabilities/test_builtin_profiles.py`

- [ ] 先测 comment 每个 accepted item 返回 candidate_id，批量 partial/all rejected status 正确。
- [ ] 为 Candidate 增加 active/retracted 状态与撤销审计，不改变最终 Finding 身份生成职责。
- [ ] 实现 `retract_comment` 的 success/partial/rejected/idempotent/current-run-only 行为。
- [ ] 测撤销后 `candidate_batch()` 不含该项，Transcript 仍有提交和撤销记录。
- [ ] 测撤销后重提、重复 candidate_ids、unknown ID、其他 Run ID、完成后调用和并发调用。
- [ ] 将 Reviewer capability 固定加入 `ToolContractReference("retract_comment", 2)`；Planner/Verifier 不得获得该工具。
- [ ] 更新 `task_done` 统一结果并以 active_comment_count 为准。

### Task 10：Planner/Verifier 控制工具统一结果

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/planning_tools.py`
- 修改：`backend/src/codelens/review/infrastructure/verdict_tools.py`
- 修改：`backend/tests/unit/review/test_capability_tools.py`
- 创建：`backend/tests/unit/review/test_planning_tools.py`
- 创建：`backend/tests/unit/review/test_verdict_tools.py`

- [ ] 对 `finalize_plan`、`verdict`、`merge`、`finalize_verdicts` 写 JSON envelope 测试。
- [ ] 覆盖批量部分接受、未知 cluster、重复覆盖、漏覆盖、重复 finalize 和完成后调用。
- [ ] 删除自然语言失败返回及 `accepted` 启发式。
- [ ] 确认 SDK loop 终止判断读取统一 status，同时保持各业务状态机不变量。

### Task 11：Runtime、Transcript、报告和错误流收口

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/openai_runtime.py`
- 修改：`backend/tests/unit/review/test_openai_runtime_stream.py`
- 修改：`backend/tests/contract/review/test_openai_runtime.py`
- 修改：`backend/src/codelens/review/application/process_report.py`
- 修改：`backend/tests/unit/review/test_process_report.py`
- 视实际 DTO 影响修改：HTTP Process Report 投影与前端类型/展示

- [ ] 所有正常 tool output、未知参数、Pydantic 错误、timeout、预算耗尽前的可返回错误均验证为合法 Tool Result。
- [ ] 不能作为普通工具结果恢复的 fatal exception 仍按 Agent Run failure 传播，但 Transcript 必须先记录结构化 `failed` 结果或稳定 failure event；由契约测试固定唯一行为。
- [ ] `task_done` 只有 success 才终止；comment/retract 永不终止。
- [ ] Host outcome 统计改用 status；新增 per-status count、non-JSON result count、compaction replay registered/consumed、loop abort count。
- [ ] Transcript 不因 serializer 二次编码出现 JSON string inside JSON string。
- [ ] 若前端显示新增指标，执行桌面 1280×800 的 loading/empty/failure/partial/long-text 检查；否则不要顺带改 UI。

### Task 12：Prompt、工具说明和 Profile 同步

**文件：**

- 修改：`prompts/sys/en/tools.json`
- 修改：`prompts/sys/zh-CN/tools.json`
- 修改：`prompts/sys/en/review-workflow.md`
- 修改：`prompts/sys/zh-CN/review-workflow.md`
- 修改：`prompts/sys/en/context-compaction.md`
- 修改：`prompts/sys/zh-CN/context-compaction.md`
- 修改：其他引用旧参数/返回的 Planner、Reviewer、Verifier Prompt
- 修改：`backend/src/codelens/review/infrastructure/i18n_prompt_loader.py`
- 修改：`backend/tests/unit/review/test_i18n_prompt_loader.py`
- 修改：`backend/src/codelens/capabilities/infrastructure/builtin_profiles.py`

- [ ] `tools.json` 增加 `retract_comment`，中英文 key 集合严格一致。
- [ ] 只在共享说明中解释一次 Tool Result 顶层合同，单工具 description 聚焦输入、结果字段和恢复动作，避免每次调用都浪费重复 token。
- [ ] 明确 `*.py` 的递归 basename 行为、含 `/` pattern 行为、`**.py` 非法。
- [ ] 推荐精确符号搜索使用 grep literal；只有需要表达式时使用 regex。
- [ ] 明确 read_file `line_range=null`、get_diff `cursor=null` 和 partial 续读。
- [ ] 明确 compaction placeholder 不是证据，必须用 suggested_arguments 精确重读；该重读允许一次且仍消耗工具预算。
- [ ] 明确错误 Comment 必须用 retract_comment 撤销，summary 不具撤销效果。
- [ ] Profile 矩阵测试最终应为：

```text
Reviewer: find_files grep read_file get_diff comment retract_comment task_done
Planner:  find_files grep read_file get_diff finalize_plan
Verifier: verdict merge finalize_verdicts（以及当前设计明确需要的只读证据工具）
```

以 `docs/ARCHITECTURE.md` 当前 Final Verifier 能力边界为准，不得仅为对称性额外授予工具。

### Task 13：端到端回放、评测与清理

**文件：**

- 修改：`backend/tests/evaluation/review/test_tool_contract_transcript_replay.py`
- 修改：相关 correctness fixture / fake model scenarios
- 删除：旧 v2 参数/返回兼容代码和不再成立的测试

- [ ] 构造确定性 fake-model 场景：`find_files("", "*.py")` → grep literal → read_file 分页 → get_diff hunk 分页 → comment → 新证据推翻 → retract_comment → task_done。
- [ ] 构造压缩场景：原结果压缩 → 精确重读 → 不增加 no-progress streak → 后续真正连续重复仍触发 warning/abort。
- [ ] 回放原 Transcript 的调用形态，确认目录 `*.py` 不再产生 16 次意外空结果，所有输出均可 `json.loads`。
- [ ] 搜索并删除 `strict_json_schema=False`、手改 required、JSON 后拼自然语言、旧 `_result_counts`、旧 compaction v1 marker 和 outcome 启发式。
- [ ] 更新现有文档示例，保证没有教授旧参数或旧结果。

**静态清理检查：**

```bash
rg -n 'strict_json_schema\s*=\s*False|_result_counts|codelens_context_compaction_v1|Tool arguments contain unsupported fields' backend/src prompts docs
rg -n 'start_line.*end_line|"accepted"\s*:' prompts docs/build-in-tool.md docs/agent-loop.md
```

第一条预期无匹配。第二条只允许出现在明确标为历史或非 Tool Result 业务数据的上下文，逐项人工确认。

## 11. 测试夹具矩阵

四个证据工具共享一个真实临时 Git/Snapshot fixture，至少包含：

```text
README.md
empty.txt
src/a.py
src/deep/compiler_plan.py
src/deep/blank_lines.py
tests/test_a.py
tests/unit/test_nested.py
unicode/中文.py
long/single_line.txt
binary/blob.bin
renamed/new_name.py  (old: renamed/old_name.py)
deleted/gone.py
link-to-a            (symlink)
```

Diff 至少制造：同文件 3 个相距较远的 hunk、单个超过 page limit 的 hunk、added/deleted/renamed、mode/symlink 变化、CRLF、无末尾换行、Unicode 内容。涉及 Git、Snapshot、排除和 symlink 的测试必须使用真实临时仓库，不能 mock Git 输出。

每个 model-visible tool 的通用参数化合同测试至少断言：

- success、partial、needs_action、rejected、failed 中适用状态；
- 输出始终是单个 JSON Object；
- 顶层字段集合和 schema_version 精确；
- tool 名正确；
- diagnostics code 稳定且 message 可本地化；
- 未知参数、非法类型、超限、timeout；
- 总输出字节上限；
- Transcript round-trip 不改变结构。

## 12. 评测方案与指标

### 12.1 离线确定性指标

- `non_json_tool_result_count == 0`。
- 旧 Transcript 中目录 `*.py` 的 16 个意外空结果，在新 matcher 上均获得正确候选或明确 `no_files_match_pattern` 诊断。
- 空行保留率 100%，返回行号与文件物理行一致。
- Diff page 中被截断 hunk/UTF-8/物理行数量为 0。
- 压缩后精确 replay 被豁免率 100%；非压缩连续重复在阈值处熔断率 100%。
- 已撤销 Candidate 进入最终 Candidate batch 的数量为 0。
- Profile/Prompt/Tool implementation 引用缺失数为 0。

### 12.2 显式真实模型评测

对 OpenAI Responses provider 与项目支持的兼容 gateway 分别显式运行代表性 Review，不作为默认 pytest 前置。记录：

- Review 成功率和 Finding precision；
- 后续被模型否定但未撤销的 Candidate 数；
- 空 grep 后使用同参数重复调用次数；
- 模型输出中 “flaky / tool result unexpected / 工具异常” 等判断次数；
- 总 tool calls、LLM turns、input/output tokens、运行时长；
- compaction count、replay registered/consumed；
- no-progress warning 和 loop abort；
- Review file evidence coverage、forced completion 数；
- non-JSON/unclassified tool result 数。

同一 Snapshot、Prompt、模型参数至少重复多次，报告均值与离散程度；不得以单次成功宣称模型适配完成。

## 13. 完整验证门禁

先执行每个 Task 的聚焦测试，再执行：

```bash
uv run --project backend pytest backend/tests/unit/review/test_tool_results.py -v
uv run --project backend pytest backend/tests/unit/review/test_model_paths.py -v
uv run --project backend pytest backend/tests/unit/review/test_snapshot_tools.py -v
uv run --project backend pytest backend/tests/unit/review/test_tool_contract.py -v
uv run --project backend pytest backend/tests/unit/review/test_comment_collector_v2.py -v
uv run --project backend pytest backend/tests/unit/review/test_capability_tools.py -v
uv run --project backend pytest backend/tests/unit/review/test_context_compaction.py -v
uv run --project backend pytest backend/tests/unit/review/test_openai_runtime_stream.py -v
uv run --project backend pytest backend/tests/unit/review/test_process_report.py -v
uv run --project backend pytest backend/tests/unit/review/test_i18n_prompt_loader.py -v
uv run --project backend pytest backend/tests/contract/review/test_openai_runtime.py -v
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
```

仅当 Task 11 实际修改前端时追加：

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

真实 provider/evaluation 命令必须采用仓库已有的显式启用开关；若当前没有统一入口，在实现评测 Task 时新增文档化入口，但不能把凭证、模型名或 endpoint 硬编码进源码。

## 14. 完成标准

只有全部满足才能关闭本计划：

- [ ] `docs/ARCHITECTURE.md` 已同步稳定合同，且实现没有越过 Snapshot/只读/Secret 边界。
- [ ] 所有 model-visible tool result 始终是统一合法 JSON v2；非 JSON 与 unclassified 指标为零。
- [ ] 所有工具参数使用 strict schema；nullable 字段显式传 null，不存在局部关闭 strict 的绕行。
- [ ] `*.py` 在目录作用域递归匹配 basename；含 `/` pattern 和独立 `**` 语义有完整测试。
- [ ] `grep` 明确区分 literal/regex、无候选/无内容匹配/扫描受限。
- [ ] `read_file` 不丢空行，不破坏 UTF-8，分页范围可直接继续。
- [ ] `get_diff` 只在完整 hunk 边界分页，cursor 绑定 Snapshot/path/位置，超大 hunk 引导 read_file 且不计完整覆盖。
- [ ] 重复调用告警通过 diagnostics 附加，JSON 不被字符串污染。
- [ ] 无进展熔断只计算连续相同结果；A→B→A 不熔断。
- [ ] Compaction 精确重读有一次性 allowance，仍受总预算和 timeout 约束，且不计无进展。
- [ ] `retract_comment` 当前 Run 内可用、幂等、可审计；retracted Candidate 不进入最终 batch。
- [ ] `task_done.summary` 不被解释为撤销；完成后 Comment 状态不可再变。
- [ ] Reviewer、Planner、Verifier Profile 只暴露各自批准的 v2 工具，Prompt/Loader/Profile/实现引用一致。
- [ ] 原 Transcript 的脱敏回放场景通过，所有新增离线指标达到第 12.1 节目标。
- [ ] 后端完整 pytest、Ruff、mypy 已实际执行并通过；若改前端，对应门禁和 1280×800 检查已执行。
- [ ] 没有覆盖、回退或混入用户原有改动；最终交接如实列出未运行或失败的验证。

## 15. 新 Agent 开工顺序

新 Agent 接手后按以下顺序开始，不要直接跳到实现：

1. 完整阅读根目录 `AGENTS.md` 和 `docs/ARCHITECTURE.md`。
2. 检查 `git status --short`，识别用户已有改动。
3. 确认 `.codegraph/` 存在，先用 CodeGraph 定位本计划涉及的当前符号和调用路径，再读取必要文件。
4. 对照 `2026-08-09-multi-agent-review-v2-hard-cut.md` 检查是否有尚未完成且会改变当前文件落点的前置工作。
5. 从 Task 0 开始更新架构合同并建立失败回归测试。
6. 严格按 Task 1→13 顺序推进；每个 Task 绿后再进入下一项。
7. 每次提交保持单一职责；不要混入无关重构、依赖升级或 UI 美化。
8. 最后按第 13 节执行完整门禁，并按第 14 节逐项验收。
