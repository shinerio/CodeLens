# CodeLens 内置 Review 工具

## 1. 文档范围

CodeLens 在每个 Reviewer Agent 的单次运行中提供 7 个模型可见工具：

| 类别 | 工具 | 主要用途 | 是否保存任务内状态 |
| --- | --- | --- | --- |
| 证据读取 | `find_files` | 按目录和 glob 查找 Snapshot 中的可见文件 | 否 |
| 证据读取 | `grep` | 用正则表达式搜索 Snapshot 中的可见文本 | 否 |
| 证据读取 | `read_file` | 按行读取 `current`、`base` 或 `head` 版本的文件 | 否 |
| 证据读取 | `get_diff` | 获取单个变更文件的 `base-to-current` diff | 否 |
| 结果提交 | `comment` | 提交并解析一批候选 Finding | 是 |
| 运行控制 | `review_file_done` | 批量声明已经完成调查的 Review 文件 | 是 |
| 运行控制 | `task_done` | 声明本次调查完成 | 是 |

这里的“工具”特指 OpenAI Agents SDK 暴露给模型的函数工具，不是 HTTP API、CLI 命令或供用户直接执行的 Shell 命令。MVP 不向模型提供 Shell、网络、任意文件系统、第三方 MCP、Skills、LSP、Serena、CodeGraph、codebase-memory 或通用沙箱工具。

权威实现位于：

- [`snapshot_tools.py`](../backend/src/codelens/review/infrastructure/snapshot_tools.py)：4 个只读证据工具；
- [`comment_collector.py`](../backend/src/codelens/review/infrastructure/comment_collector.py)：`comment`、`review_file_done` 和 `task_done`；
- [`openai_runtime.py`](../backend/src/codelens/review/infrastructure/openai_runtime.py)：工具组装、运行和转录事件；
- [`tools.json`](../prompts/sys/zh-CN/tools.json)：简体中文模型可见说明。

## 2. 共同调用约定

### 2.1 调用主体和生命周期

工具由模型在 Review 调查过程中按 JSON Schema 调用，不面向终端用户。每个 Agent Run 都创建独立的 `FilesystemReviewTools` 和 `ReviewCommentCollector`，因此工具读取范围、累计评论和完成状态不会跨 Agent Run 共享。

运行流程如下：

```text
ReviewSnapshot 冻结
        |
        v
Runtime 创建本次 Agent Run 的 7 个工具并校验契约
        |
        v
模型使用 get_diff/read_file 调查 -> comment 提交已证实问题
        -> review_file_done 批量声明文件 -> task_done 结束调查
        |
        v
宿主只用 comment 已解析并接受的记录生成 FindingBatch
```

模型最终自然语言文本不用于生成 Finding；需要进入报告的问题必须通过 `comment` 提交并被接受。根据系统 Review 工作流，即使没有 Finding，也必须让每个文件通过查看与声明门禁，并调用一次被接受的 `task_done`。

### 2.2 严格 JSON Schema

参数默认必须显式提供，不应依赖 Python 实现中的默认值。例如，调用 `find_files` 时 `path` 和 `pattern` 都必填，调用 `grep` 时 `pattern`、`path` 和 `file_pattern` 都必填。唯一例外是 `read_file` 的 `start_line` 与 `end_line`：两者可以同时省略以读取有界全文，也只能同时提供。除 `read_file` 为表达真正可省略字段而使用非严格 provider schema 外，参数类型、枚举、字符串长度和数组数量仍由 SDK 生成的 Pydantic 参数适配器校验；额外字段还会在本地工具边界再次被拒绝。

本文示例使用以下逻辑格式表示模型函数调用：

```json
{
  "name": "tool_name",
  "arguments": {}
}
```

工具的实际返回值是紧凑 JSON 字符串。示例为了可读性进行了缩进。

### 2.3 路径和版本语义

- 所有模型可见路径都是规范化的 Git 风格仓库相对路径；在 macOS、Linux 和 Windows 上都使用 `/`。
- 路径不能是绝对路径，不能包含 `..`、反斜杠或 NUL 字符，也不会暴露宿主机 worktree 的绝对路径。
- `find_files.path` 用空字符串 `""` 表示仓库根目录；其他工具的文件路径不能为空。
- 模型只能看到 Snapshot Manifest 中来源为 `target` 或 `context` 的条目。
- `current` 是冻结且经过 Manifest 哈希复验的内容，可能包含创建 Review 时捕获的工作区 overlay。
- `base` 和 `head` 分别来自 Snapshot 固定的 base/head Git OID；工具不接受模型提供的任意 commit、branch 或 ref。

### 2.4 只读、隔离和完整性

4 个证据工具的唯一数据源是当前任务冻结后的 `ReviewSnapshot`。每次读取都会重新检查路径可见性和当前内容哈希；普通文件还要确认解析后的真实路径仍位于任务 worktree 内。符号链接读取的是链接目标文本，并校验该文本的哈希。删除条目必须在文件系统中仍然不存在且其 Manifest 哈希与空内容一致。

读取 `base` 或 `head` 时，工具先复验对应的当前 Snapshot 条目，再通过受限的 Git 参数数组从固定 OID 读取对象。整个过程不执行 Shell，不访问网络，不读取用户的可变原始工作区，也不写入任何文件。

生产 Runtime 不为证据工具设置独立调用次数上限；调查仍受 Agent 最大模型回合数、单次工具输出上限和用户取消约束。`grep` 的隔离进程启动阶段单独受限，只有 Worker 就绪后才开始计算正则求值时限，避免把解释器冷启动误判为表达式超时。每次模型可见工具调用及结果会以 `tool_call`/`tool_result` 事件进入运行转录，并用于过程报告中的工具调用统计。

## 3. `find_files`

### 3.1 功能

在指定可见目录下按路径段感知的 glob 查找文件。适合先了解 Snapshot 中有哪些目标文件和上下文文件，再决定读取或搜索范围。

### 3.2 参数

| 参数 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `path` | string | 必填，最长 1024 字符 | 要搜索的仓库相对目录；根目录使用 `""` |
| `pattern` | string | 必填，1–512 字符 | 相对于 `path` 的 POSIX 风格 glob |

glob 语义：

- `*` 只匹配一个路径段；
- `**` 匹配任意层级；
- 模式匹配完整相对路径，而不是任意子串；
- 模式不能以 `/` 开头，不能包含 `..` 或 `\`。

### 3.3 使用示例

查找 `backend` 下任意层级的 Python 测试文件：

```json
{
  "name": "find_files",
  "arguments": {
    "path": "backend",
    "pattern": "**/test_*.py"
  }
}
```

成功响应：

```json
{
  "paths": [
    "backend/tests/unit/review/test_snapshot_tools.py"
  ],
  "truncated": false
}
```

最多返回 200 条按路径排序的结果且不提供分页。存在更多匹配时只返回前 200 条，并设置 `truncated: true`；模型应缩小 `path` 或细化 `pattern` 后重新搜索，而不是重复相同调用。

### 3.4 工作原理

1. 校验 `path` 和 `pattern` 都是规范化的相对路径表达。
2. 从当前 Snapshot Manifest 中筛出 `target` 和 `context` 条目，并按路径排序。
3. 把 `path` 转换为目录前缀，只对其下候选路径应用逐段 glob 匹配。
4. 返回有界路径列表和截断标记。该工具只枚举 Manifest，不遍历用户工作区，也不依赖操作系统的 `find`、`glob` 或 Shell。

## 4. `grep`

### 4.1 功能

用 Python 正则表达式逐行搜索所有可见 UTF-8 文本。适合查找符号用法、配置键、错误文本或调用线索。

### 4.2 参数

| 参数 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `pattern` | string | 必填，1–512 字符 | Python `re` 语法的正则表达式 |
| `path` | string | 必填，最长 1024 字符 | 要搜索的可见文件或目录；根目录使用 `""` |
| `file_pattern` | string | 必填，1–512 字符 | 相对于 `path` 的路径段感知 glob；不过滤时使用 `**` |

### 4.3 使用示例

```json
{
  "name": "grep",
  "arguments": {
    "pattern": "ReviewCommentCollector\\(",
    "path": "backend/src",
    "file_pattern": "**/*.py"
  }
}
```

成功响应：

```json
{
  "matches": [
    {
      "path": "backend/src/codelens/review/infrastructure/openai_runtime.py",
      "line": 180,
      "text": "        comment_collector = ReviewCommentCollector("
    }
  ],
  "truncated": false
}
```

`file_pattern` 使用与 `find_files.pattern` 相同的 `*`/`**` 语义。`path` 是目录时，它匹配相对于该目录的候选路径；`path` 是精确文件时，它匹配文件名且不能扩大搜索范围。

每个匹配包含仓库相对路径、从 1 开始的行号和最多 200 字符的该行文本。单次最多扫描 1 MiB 内容、返回 200 个匹配且不提供分页；任一上限触发时 `truncated` 为 `true`。模型应缩小内容 `pattern`、`path` 或细化 `file_pattern` 后重新搜索，不能通过重复相同调用获取剩余结果。含 NUL 字节的二进制条目会被跳过。

### 4.4 工作原理

1. 在主进程中编译正则，提前拒绝非法表达式。
2. 将 `path` 解析为单个可见文件或目录范围，使用 `file_pattern` 过滤候选，再按路径顺序逐一读取条目；每次读取都复验 Manifest 哈希，二进制内容不参与搜索。
3. 从过滤后的文件起点组装不超过扫描上限的文本，并交给使用 `spawn` 创建的独立子进程。
4. 子进程就绪后逐行执行正则搜索并收集有界结果。默认计算时限为 30 秒；超时后宿主会终止该进程，必要时再强制杀死，避免灾难性回溯长期占用 Runtime。
5. 若扫描或结果上限触发截断，返回 `truncated: true`；调用方必须细化搜索条件后重试。

`grep` 只能搜索 `path` 指定的可见文件或目录范围。需要先枚举候选路径时，可配合 `find_files`，再用 `read_file` 精读匹配上下文。

## 5. `read_file`

### 5.1 功能

读取一个可见文件在指定版本中的连续行区间。适合查看 diff 上下文、追踪调用关系，或比较冻结内容与固定 Git revision。

### 5.2 参数

| 参数 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `path` | string | 必填，1–1024 字符 | 可见文件的仓库相对路径 |
| `start_line` | integer | 可选，`>= 1` | 起始行，包含该行；必须与 `end_line` 同时提供或同时省略 |
| `end_line` | integer | 可选，`>= start_line` | 结束行，包含该行；必须与 `start_line` 同时提供，一次最多 500 行 |
| `version` | string | 必填，`current`、`base` 或 `head` | 要读取的版本 |

版本含义：

| 版本 | 数据来源 | 典型用途 |
| --- | --- | --- |
| `current` | Manifest 哈希验证后的冻结文件 | 查看本次 Review 的最终输入，包括已捕获 overlay |
| `base` | Snapshot 固定 `base_oid` 的 Git 对象 | 查看变更前内容；重命名文件会自动使用旧路径 |
| `head` | Snapshot 固定 `head_oid` 的 Git 对象 | 查看目标 revision，不一定包含未提交 overlay |

新增文件不存在 `base` 版本，删除文件不存在 `head` 版本，此时调用会失败。

同时省略 `start_line` 和 `end_line` 时，工具从第一行读取到文件结尾，但仍受 500 行和 64 KiB 上限约束。这适合直接读取常见的小文件全文。

### 5.3 使用示例

```json
{
  "name": "read_file",
  "arguments": {
    "path": "backend/src/codelens/review/infrastructure/snapshot_tools.py",
    "start_line": 172,
    "end_line": 193,
    "version": "current"
  }
}
```

成功响应：

```json
{
  "path": "backend/src/codelens/review/infrastructure/snapshot_tools.py",
  "version": "current",
  "start_line": 172,
  "end_line": 193,
  "content": "172|    async def find_files(...):\n173|        ...",
  "truncated": false
}
```

`content` 中每个非空输出行使用 `行号|正文` 格式。单个 Snapshot 源文件超过 1 MiB 时会在载入正文前被拒绝；单次输出最多 64 KiB，超过时设置 `truncated: true`；二进制文件会被拒绝。

有界全文调用可以省略两个行号：

```json
{
  "name": "read_file",
  "arguments": {
    "path": "backend/src/codelens/review/infrastructure/snapshot_tools.py",
    "version": "current"
  }
}
```

全文超过任一上限时 `truncated` 为 `true`，模型应改用明确且更小的行范围继续读取。

### 5.4 工作原理

1. 校验版本枚举、路径可见性和行范围；两个行号只能同时存在，`end_line - start_line >= 500` 会被拒绝。
2. `current` 直接读取并哈希验证冻结条目；`base`/`head` 先验证冻结条目，再以 `git show <固定 OID>:<路径>` 读取。
3. 按字节内容切分行；显式范围按首尾都包含的区间截取，省略范围时从第一行截取至 EOF 或 500 行上限。
4. 应用 64 KiB 输出上限、UTF-8 替换解码和行号前缀，返回实际范围元数据及截断状态。

## 6. `get_diff`

### 6.1 功能

返回一个已变更可见文件的 `base-to-current` unified diff。它是判断问题是否由本次变更引入、选择 `comment.side` 和精确引用 `existing_code` 的主要证据来源。

### 6.2 参数

| 参数 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `path` | string | 必填，1–1024 字符 | 必须是本次 `review_files` 中的可见变更文件 |

### 6.3 使用示例

```json
{
  "name": "get_diff",
  "arguments": {
    "path": "backend/src/codelens/review/infrastructure/snapshot_tools.py"
  }
}
```

成功响应：

```json
{
  "path": "backend/src/codelens/review/infrastructure/snapshot_tools.py",
  "content": "diff --git a/... b/...\n--- a/...\n+++ b/...\n@@ ... @@\n-old\n+new\n",
  "truncated": false
}
```

单个 Snapshot 源文件超过 1 MiB 时会在载入正文前被拒绝。输出最多 64 KiB，超过时 `truncated` 为 `true`。未变更的 context 文件即使可见，也不能调用该工具。

### 6.4 工作原理

1. 校验路径属于 Manifest 可见条目，同时属于确定性构造的 `review_files`。
2. 读取并哈希验证冻结的 `current` 字节；除新增文件外，从固定 `base_oid` 读取变更前字节。重命名文件自动使用 `old_path` 读取 base。
3. 宿主使用 Python `difflib.unified_diff` 和 3 行上下文生成 diff，而不是从可变工作区读取或让模型传入 Git ref。
4. 新增文件生成 `/dev/null -> b/path` 元数据，重命名生成 `rename from/to` 元数据；二进制变更返回 `Binary files ... differ`，符号链接使用对应 mode。
5. 应用 64 KiB 上限并返回截断状态。

因此，这里的 `current` 与 Git `head` 不是同义词：如果 Snapshot 捕获了未提交的 workspace overlay，`get_diff` 会包含 overlay，而普通的 `base..head` Git diff 不会。

## 7. `comment`

### 7.1 功能

批量提交 1–20 条有证据支持的候选评论。工具不会信任模型计算的行号，也不允许模型提交 hunk ID 或内容哈希；它会根据原样代码摘录在冻结 Snapshot 中重新定位并验证，只有成功解析的评论才进入最终 FindingBatch。

### 7.2 参数

顶层参数：

| 参数 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `comments` | array | 必填，1–20 项 | 本批候选评论 |

每个 `comments[]` 项：

| 字段 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `path` | string | 1–240 字符 | 变更文件路径 |
| `side` | string | `old` 或 `new` | `old` 对应删除行，`new` 对应新增行 |
| `existing_code` | string | 1–8000 字符 | 对应侧变更行的精确、连续原文，不含 diff 标记 |
| `title` | string | 1–240 字符 | Finding 标题 |
| `content` | string | 1–8000 字符 | 问题、触发条件和影响说明 |
| `recommendation` | string | 1–8000 字符 | 修复建议，不承载可自动应用的补丁 |
| `category` | string | 1–240 字符 | Reviewer 使用的问题分类 |
| `severity` | string | `critical`、`high`、`medium`、`low` 或 `info` | 已证实影响的严重程度 |
| `confidence` | number | 0–1 | 证据置信度，且不得低于当前 Reviewer 的阈值 |

字符串会去除首尾空白；空字符串和额外字段会被拒绝。`existing_code` 应只包含对应侧实际变化的最小连续片段，不要加入 `+`/`-` 标记或未修改上下文。若同样文本出现多次，应引用足够多的连续变更行来明确位置。

### 7.3 使用示例

```json
{
  "name": "comment",
  "arguments": {
    "comments": [
      {
        "path": "backend/src/example.py",
        "side": "new",
        "existing_code": "result = cache[key]",
        "title": "缺少缓存键存在性检查",
        "content": "当 key 首次出现时会抛出 KeyError，使请求返回 500。",
        "recommendation": "读取前检查键，或使用能够表达缺失值的访问方式。",
        "category": "correctness",
        "severity": "high",
        "confidence": 0.93
      }
    ]
  }
}
```

成功响应：

```json
{
  "accepted": true,
  "accepted_count": 1,
  "comment_count": 1,
  "rejected_comments": [],
  "rejected_count": 0
}
```

`accepted_count` 和 `rejected_count` 是本批接受数和拒绝数，`comment_count` 是本 Agent Run 当前累计评论数。批次按顺序逐条独立解析；候选级语义失败记录在 `rejected_comments`，其中 `index` 是输入 `comments` 数组中从零开始的索引，`reason` 是拒绝原因。同批其他有效评论仍会被处理，`accepted` 表示本批至少接受了一项。模型只应修正并重试被拒绝的索引，不应重复提交已经接受的评论。若整个参数信封无法校验或 Snapshot、Git、I/O 完整性失效，工具调用仍整体失败。

### 7.4 工作原理

每条评论按以下过程处理：

1. 用 Pydantic 严格校验字段，并检查 `confidence` 不低于 Reviewer 的 `confidence_floor`。
2. 调用内部 diff 读取，以 `existing_code` 在指定 `side` 的 unified diff 中做规范化滑动窗口匹配。
3. 若 diff 匹配失败，再读取 `old -> base` 或 `new -> current` 的完整文件做回退匹配。匹配时忽略首尾空白、空行以及可选的首字符 diff 标记，但返回冻结文件中的绝对行号。
4. 要求解析出的完整行范围恰好落在指定侧唯一一个 changed hunk 内；无法解析、越界、侧别不符、包含未变更上下文或跨 hunk 的候选都会被拒绝。
5. 从已验证字节派生 `excerpt_hash`，并由 Snapshot 派生行号、side、changed hunk ID 和整文件删除标记。模型不能提供或覆盖这些字段。
6. 形成 Finding 候选：`critical/high/medium` 映射为 `blocking`，`low/info` 映射为 `non_blocking`；`content` 进入 evidence、impact 和 explanation，`recommendation` 单独保存。

`comment` 只修改本次运行内存中的收集器，不写数据库、Artifact 或文件。Runtime 完成后才把已解析候选编码成稳定的 `schema_version: "1"` FindingBatch；模型最终文本以及模型臆造的位置元数据都不会成为报告依据。

## 8. `review_file_done`

### 8.1 功能

批量声明当前 Reviewer 已完成哪些 Review 文件的调查。只有本次 Agent Run 已经通过模型可见的 `get_diff` 或 `read_file` 成功查看过的路径才能记录；`find_files`、`grep` 以及宿主为 Finding 定位而执行的内部读取都不构成文件查看证据。

### 8.2 参数

| 参数 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `reviewed_files` | array[string] | 必填，1–2000 项；每项 1–1024 字符 | `review_files` 中已经完成调查的精确路径 |

### 8.3 使用示例

```json
{
  "name": "review_file_done",
  "arguments": {
    "reviewed_files": ["src/cache.py", "src/service.py"]
  }
}
```

成功响应：

```json
{
  "accepted": true,
  "missing_evidence_files": [],
  "recorded_files": ["src/cache.py", "src/service.py"]
}
```

### 8.4 工作原理

工具以 Snapshot 生成的完整 `review_files` 校验路径，并检查 `FilesystemReviewTools` 在本次 Run 内记录的成功查看证据。范围外路径会拒绝整个调用；缺少证据的路径通过 `missing_evidence_files` 返回，批次中其余具备证据的路径仍会记录。重复声明幂等，不会重复计数或改变 Finding。

## 9. `task_done`

### 9.1 功能

尝试声明当前 Reviewer 已完成全部 Review 文件的调查。它不创建 Finding；没有问题时也必须使用它结束调查。模型最终文本不能替代一次被接受的 `task_done`。

### 9.2 参数

| 参数 | 类型 | 约束 | 含义 |
| --- | --- | --- | --- |
| `summary` | string | 必填，1–8000 字符 | 本次调查的简短总结 |

### 9.3 拒绝与成功响应

调用示例：

```json
{
  "name": "task_done",
  "arguments": {
    "summary": "已检查全部 Review 文件，并提交 1 条评论。"
  }
}
```

文件尚未完整覆盖时返回：

```json
{
  "accepted": false,
  "incomplete_retry_count": 1,
  "max_incomplete_review_retries": 3,
  "missing_evidence_files": ["src/cache.py"],
  "undeclared_files": ["src/service.py"]
}
```

`missing_evidence_files` 表示尚未成功调用 `get_diff` 或 `read_file`；`undeclared_files` 表示已经查看，但没有通过 `review_file_done` 记录。模型必须按具体原因补齐后再次调用 `task_done`。

全部完成时返回：

```json
{
  "accepted": true,
  "comment_count": 1,
  "forced_completion": false,
  "reviewed_files": ["src/cache.py", "src/service.py"]
}
```

### 9.4 重试耗尽

`GET/PUT /api/settings/review-completion` 的 `max_incomplete_review_retries` 控制每个 Agent Run 最多打回多少次，允许 0–20，默认 3。每个 Run 启动时读取当前设置；超过该次数后，下一次不完整的 `task_done` 会被接受并返回 `forced_completion: true` 和精确的 `incomplete_files`。Runtime 保留当前已验证 Finding，把该 Agent 的不完整覆盖状态持久化到 checkpoint，并立即结束 SDK Agent Loop。最终聚合时，全部 Agent 覆盖完整的任务进入 `completed`，任一 Agent 强制完成的任务进入 `partial`，同时写入 `review_coverage_incomplete` 生命周期告警；Web 会合并所有 Agent 的未完成路径并提示用户。若模型在任何 `task_done` 被接受前自行结束，Runtime 会把该 Agent Run 判为失败。

## 10. 契约装载与可观测性

工具的自然语言说明与参数 Schema 分开维护：参数结构由带类型标注和 Pydantic 约束的实现生成，模型可见说明来自 `prompts/sys/<locale>/tools.json`。应用启动时，`I18nPromptLoader` 会完整读取各语言包，并要求每个语言包恰好包含这 7 个稳定工具；缺失、额外或空说明都会导致启动失败。未知语言回退到配置的默认语言，工具名称和 JSON 字段不会本地化。

Runtime 在模型调用前还会扫描工具名称、说明和 Schema，禁止暴露 `snapshot_id`、`hunk_id`、`content_hash`、`excerpt_hash`、内部规则链、Context Plan 和优先级等宿主内部概念。流式运行时，SDK 的工具调用和结果分别映射为稳定的 `tool_call` 与 `tool_result` 转录记录，并保留 tool name/call ID 用于前端执行过程展示和终态过程报告。

## 11. 常见错误与处理

| 错误场景 | 原因 | 建议处理 |
| --- | --- | --- |
| `Snapshot context path is not visible` | 路径不规范或不在 Manifest 的 target/context 范围 | 先用 `find_files` 获取可见路径，不要猜测宿主路径 |
| `Snapshot context content changed` | 冻结内容、符号链接目标或删除状态与 Manifest 哈希不一致 | 停止依赖该证据；该任务的隔离或完整性已失效 |
| `path is unavailable in version` | 新增文件读 `base`、删除文件读 `head`，或固定 revision 中无该对象 | 改用存在的版本，并结合 `get_diff` 判断变更类型 |
| `grep pattern evaluation timed out` | 正则存在高开销回溯 | 简化表达式，使用更具体的前缀、字符类或边界 |
| `existing_code cannot be resolved to a line range` | 摘录在所选版本中找不到 | 从 `get_diff` 重新复制对应侧的精确连续变更行 |
| `existing_code must quote only consecutive changed ... lines` | 摘录包含上下文、跨 hunk、side 错误或不在变更范围 | 移除上下文和 diff 标记，修正 `old/new` 侧别 |
| `comment confidence is below this reviewer's threshold` | 置信度低于该 Reviewer 配置阈值 | 补充证据；无法证实时放弃该候选，而不是虚增置信度 |
| `reviewed_files contains paths outside this Review` | `review_file_done` 提交了不属于当前 `review_files` 的路径 | 使用首次输入中的精确目标路径，不要提交 context 文件或旧重命名路径 |
| `missing_evidence_files` 非空 | 对应文件尚未成功调用 `get_diff` 或 `read_file` | 先查看这些文件，再用 `review_file_done` 声明 |
| `undeclared_files` 非空 | 文件已经查看但没有声明完成 | 用 `review_file_done` 批量声明这些路径，再重试 `task_done` |
| `task_done has already been called` | 同一 Agent Run 重复结束 | 首次成功后不要再次调用 |

## 12. 设计边界总结

内置工具遵循三条核心原则：

1. **证据不可变**：所有源码证据来自冻结 Snapshot 或固定 Git OID，并在读取时重新验证。
2. **模型最小权限**：模型只能读取允许的仓库相对路径、提交评论和声明完成，不能写文件、执行任意进程、访问网络或选择任意 Git ref。
3. **输出确定化**：模型负责提供问题内容和原样代码证据，宿主负责解析位置、校验 changed hunk、派生哈希和构造最终 FindingBatch。

更高层的架构边界和稳定契约见 [`../ARCHITECTURE.md`](../ARCHITECTURE.md) 的“MVP 内置 Review 工具”与“数据、安全与执行边界”。
