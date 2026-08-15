# CodeLens Prompt 构造设计

本文档描述当前源码中 PLANNER、REVIEWER、VERIFIER 三种 Agent 的提示词来源、内部输入信封、角色上下文、系统指令拼接、工具暴露，以及最终传给 OpenAI Agents SDK 的参数。若本文与实现不一致，以 [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md) 和当前源码为准。

> 实现状态：REVIEWER 与多 Reviewer 场景下的 VERIFIER 已接入 Worker 主链。PLANNER 的 Agent、Prompt、工具、输入构造器和输出校验器已经实现，但当前 Worker 尚未调用 `build_planner_input_payload()`，也未调用 `ChangeRiskSummary.from_snapshot()` 来启动新的自适应规划。本文对 PLANNER 的说明因此分为“已定义的运行时契约”和“当前生产接入状态”，不能把前者误认为已经在主链执行。

## 1. 总体分层

一次模型调用的可见输入不是单个 Prompt 文件，而是以下几层内容的组合：

| 层 | 来源 | 最终位置 | 适用角色 |
| --- | --- | --- | --- |
| Review Policy | `prompts/sys/<locale>/review-policy.md` | `Agent.instructions` | 全部角色 |
| Repository Instructions | Snapshot 冻结的 `AGENTS.md`、`REVIEW.md`、文件级规则 | `Agent.instructions` | 全部角色 |
| Review Workflow | `prompts/sys/<locale>/review-workflow.md` | `Agent.instructions` | 仅 REVIEWER |
| Agent Policy | `prompts/<prompt_key>/<locale>.md` 或对应用户覆盖 | `Agent.instructions` | 全部角色，各自不同 |
| Activated Skills | 冻结在执行规格中的 Skill 指令 | `Agent.instructions` | 有激活 Skill 时 |
| User Input | `review_files` 加模型可见的 `role_context` | `Runner.input` | 全部角色 |
| Tool Definitions | 能力白名单、JSON Schema、`tools.json` 本地化描述 | `Agent.tools` | 按角色和版本 |
| Tool Results | Snapshot 只读工具执行结果 | 后续对话轮次 | 模型调用过程中 |

其中有两个容易混淆的边界：

- `repository_instructions` 虽然最初与 `review_files` 一起位于 CodeLens 内部信封中，但运行时会将它拆出并拼入系统指令，不会留在最终 `Runner.input` 中。
- CodeLens 的 `role_context` 是内部 JSON 字段，不是 Agents SDK 的 `context` 参数。运行时不会向 `Runner.run()` 传 `context`。

整体数据流如下：

```text
ReviewSnapshot
  -> ContextBuilder 生成基础 AgentInput
  -> 按角色补充 role_context
  -> 补充 _host_* 运行元数据
  -> OpenAIAgentRuntime._split_agent_input()
       ├─ user_input: review_files + 模型可见 role_context
       ├─ repository_instructions: 单独的规范 JSON
       └─ host role_context: 仅宿主运行时使用
  -> 拼接 Agent.instructions
  -> 装配 Agent.tools
  -> Agent(...)
  -> Runner.run(starting_agent=..., input=user_input, ...)
```

## 2. Prompt 文件与加载方式

### 2.1 系统级多语言 Prompt

每个 `prompts/sys/<locale>/` 必须包含完整的最小文件集：

- `review-policy.md`：最高层的代码审查安全与规则解释策略。
- `review-workflow.md`：Reviewer 的调查、证据、评论和完成协议。
- `review-feedback.md`：评论位置或证据被宿主拒绝后的纠偏文本。
- `tool-not-found.md`：模型调用冻结能力以外工具时的纠偏模板。
- `tool-loop-warning.md`：检测到重复工具结果时使用的警告模板；它不属于初始 `instructions`。
- `checkpoint-compaction.md`：独立 checkpoint LLM 调用使用的本地化语义压缩指令；不属于主 Agent 初始 `instructions`。
- `tools.json`：所有稳定模型工具的本地化描述。

Epoch checkpoint Runtime 已接线并在启动时校验该文件；完整设计、已实现边界和后续 token 水位增强见 [`prompt-cache.md`](./prompt-cache.md)。

[`I18nPromptLoader`](../backend/src/codelens/review/infrastructure/i18n_prompt_loader.py#L38-L112) 在进程启动时读取并校验所有 locale 目录，将结果保存为不可变映射。请求 locale 存在时使用精确匹配，否则回退到默认 locale，当前默认值为 `en`。模型调用期间不会重新读取这些系统 Prompt 文件。

`tools.json` 必须精确覆盖以下稳定工具名，并且每个工具只能包含非空的 `description`：

```text
find_files, grep, read_file, get_diff,
comment, review_file_done, task_done,
submit_review_plan, finalize_plan,
verdict, merge, finalize_verdicts
```

单个 Agent 最终只能看到其能力 Profile 白名单允许的子集，而不是这个完整集合。

### 2.2 Agent 专属 Prompt

Agent 专属 Prompt 位于 `prompts/<prompt_key>/<locale>.md`。内置目录映射由 [`builtin_agents.py`](../backend/src/codelens/reviewer_catalog/infrastructure/builtin_agents.py#L85-L172) 固定：

| Agent reference | 角色 | `prompt_key` | Prompt 关注点 |
| --- | --- | --- | --- |
| `review-planner:v1` | PLANNER | `review-planner` | 选择 General Reviewer，或至少两个专项 Reviewer；不得创建 Finding |
| `correctness:v1` | REVIEWER | `correctness` | 旧版正确性审查；隐藏、不可供 Planner 选择 |
| `correctness:v2` | REVIEWER | `correctness-v2` | 业务逻辑、状态转换、边界、错误与控制流 |
| `security:v1` | REVIEWER | `security` | 认证授权、注入、Secret、敏感数据与信任边界 |
| `reliability-concurrency:v1` | REVIEWER | `reliability-concurrency` | 并发、锁、事务、幂等、重试、超时、取消与恢复 |
| `contract-data:v1` | REVIEWER | `contract-data` | API、事件、配置、序列化、迁移、兼容与数据完整性 |
| `architecture:v1` | REVIEWER | `architecture` | 分层、依赖方向、限界上下文、所有权与隔离 |
| `performance:v1` | REVIEWER | `performance` | 复杂度、重复 I/O、N+1、内存、资源与扩展性 |
| `test-regression:v1` | REVIEWER | `test-regression` | 关键缺测、无效断言、行为不匹配与回归保护 |
| `general:v1` | REVIEWER | `general` | 单 Agent 的广度优先综合审查 |
| `review-verifier:v1` | VERIFIER | `review-verdict` | 对 Finding cluster 做 accept、deny 或 merge，不发明新证据 |

[`ReviewerPromptSettingsService`](../backend/src/codelens/reviewer_catalog/application/prompt_settings.py#L34-L88) 会按 `Agent reference + locale` 读取用户覆盖；没有覆盖时使用仓库中的不可变默认 Prompt。虽然类名保留了 `Reviewer`，它接收通用的 `AgentVersion`，内部 PLANNER 和 VERIFIER 也通过同一套 Agent Prompt 解析方式进入冻结执行规格。

Worker 当前将 `zh-CN` 映射到中文 Agent Prompt，其他 locale 映射到 `en`。实际采用的 Agent Prompt、能力 Profile、工具版本、执行限制和激活 Skill 会冻结到 `FrozenAgentExecutionSpec`，并参与执行指纹计算，避免恢复或重试时静默漂移。

### 2.3 Skill 指令

有 Skill 被激活时，每个 Skill 会追加一个独立系统指令段：

```text
# Activated Review Skill (Untrusted, No Additional Permissions)
{"activation_reason":...,"content_hash":...,"skill_id":...,"version":...}
<skill-instructions>
<skill.instruction_text>
</skill-instructions>
```

Skill 明确被标记为不可信内容，并且不会带来额外工具权限。当前内置 Skill Policy 只有 `none:v1`，没有实际激活的 Skill，因此正常内置执行不会产生上述段落。实现见 [`_skill_instruction_sections()`](../backend/src/codelens/review/infrastructure/openai_runtime.py#L578-L602) 与 [`builtin_skill_policies()`](../backend/src/codelens/capabilities/infrastructure/builtin_profiles.py#L80-L84)。

## 3. 基础内部输入信封

[`ContextBuilder`](../backend/src/codelens/review/application/context_builder.py#L81-L131) 从不可变 `ReviewSnapshot` 构造所有角色共享的基础输入：

```json
{
  "review_files": [
    {
      "path": "backend/src/example.py",
      "change_type": "modified",
      "old_path": "backend/src/old-example.py",
      "old_ranges": [
        {"start_line": 10, "end_line": 15}
      ],
      "new_ranges": [
        {"start_line": 12, "end_line": 20}
      ]
    }
  ],
  "repository_instructions": [
    {
      "path": "AGENTS.md",
      "content": "<完整且已冻结的规则正文>",
      "applies_to": ["."]
    }
  ]
}
```

基础信封顶层必须精确包含 `review_files` 和 `repository_instructions`，不能有未知字段。角色构造器可以再增加唯一的可选顶层字段 `role_context`。

### 3.1 `review_files`

每项字段由 [`ReviewFileInput.as_payload()`](../backend/src/codelens/review/application/review_scope.py#L28-L45) 生成：

| 字段 | 内容 |
| --- | --- |
| `path` | Snapshot 中的新路径或当前主路径 |
| `change_type` | `added`、`modified`、`deleted` 或 `renamed` |
| `old_path` | 仅存在旧路径时输出；其他情况省略 |
| `old_ranges` | 旧侧变更范围，每项含闭区间 `start_line`、`end_line` |
| `new_ranges` | 新侧变更范围，每项含闭区间 `start_line`、`end_line` |

这里只预取文件清单和变更行范围，不预取完整文件正文或完整 diff。模型必须通过 Snapshot 只读工具按需获取证据。

### 3.2 `repository_instructions`

每项固定包含：

| 字段 | 内容 |
| --- | --- |
| `path` | 规则文件相对路径 |
| `content` | 规则文件完整正文 |
| `applies_to` | 规则作用域路径；根作用域使用 `.` |

这些规则来自 Snapshot 准备阶段已经解析的根级和嵌套级 `AGENTS.md`、`REVIEW.md`，以及适用的文件级 `<target>.review.md`。`ContextBuilder` 按 precedence 和相对路径排序，压缩重复规则正文，并为每项附上适用范围。规则只解析一次并冻结，所有角色共享同一份基础规则集合。

基础输入使用 UTF-8、`sort_keys=True` 和紧凑分隔符序列化，以保证稳定的规范字节表示。

## 4. 三种角色的 `role_context` 与最终 `user_input`

`role_context` 只承载角色执行所需的有界元数据，不用于传递源码正文、完整 diff 或额外权限。运行时会删除其中所有以 `_host_` 开头的键；若删除后为空，最终 `user_input` 中连 `role_context` 字段也不会出现。

| 角色 | 最终模型可见 `user_input` |
| --- | --- |
| PLANNER | `review_files` + `role_context.change_risk_summary` + eligible/unavailable Reviewer references + `reviewer_catalog`；当前主链未实际发送 |
| REVIEWER | `review_files`；有计划提示时再加 `role_context.planner_guidance` |
| VERIFIER | `review_files` + `role_context.verdict_context` |

三者的最终 `user_input` 都不包含 `repository_instructions`。

### 4.1 PLANNER

PLANNER 的已定义输入构造器是 [`build_planner_input_payload()`](../backend/src/codelens/review/application/planning.py#L153-L184)。它要求输入仍是未经角色扩展的基础信封，并追加：

```json
{
  "role_context": {
    "change_risk_summary": {
      "file_count": 2,
      "changed_line_count": 41,
      "files": [
        {
          "path": "backend/src/auth/service.py",
          "change_type": "modified",
          "changed_old_lines": 18,
          "changed_new_lines": 23,
          "language_hint": "python"
        }
      ],
      "risk_signals": [
        {
          "code": "auth-boundary",
          "evidence_paths": ["backend/src/auth/service.py"]
        }
      ]
    },
    "eligible_reviewer_references": [
      "correctness:v2",
      "security:v1"
    ],
    "reviewer_catalog": [
      {"reference": "correctness:v2", "dimensions": ["correctness"]}
    ],
    "unavailable_reviewer_references": ["security:v1"]
  }
}
```

各字段的生成规则如下：

- `change_risk_summary` 由 [`ChangeRiskSummary.from_snapshot()`](../backend/src/codelens/review/application/planning.py#L92-L133) 定义：按路径排序文件；合并重叠行范围后计算旧侧和新侧变更行数；根据后缀给出 `language_hint`；再按路径关键词生成 `auth-boundary`、`concurrency-boundary`、`data-migration` 风险信号。语言映射覆盖 Python、TypeScript、JavaScript、Java、SQL、Go 和 Rust，未知后缀为 `null`。该摘要不包含源码正文。
- `eligible_reviewer_references` 由调用方传入，构造器保持原顺序并转为 JSON 数组。
- `reviewer_catalog` 也是调用方传入的有界字典投影；当前构造器只复制每个字典，不规定其内部必须有哪些键。
- `unavailable_reviewer_references` 从 eligible 列表中过滤得到：readiness 缺失或状态为 `unavailable` 的 Reviewer 会进入该数组；`ready` 和 `degraded` 不进入。`CapabilityReadiness.reason_codes` 当前不会直接写入 PLANNER 的 `role_context`。

运行时的 `_planner_codec()` 要求 `eligible_reviewer_references` 和 `unavailable_reviewer_references` 存在且为字符串数组；只允许额外出现 `change_risk_summary`、`reviewer_catalog`。这些列表同时约束 Planner 的结构化输出，防止选择未授权或不可用 Reviewer。

因此，按已定义契约，PLANNER 最终传给 `Runner.input` 的 `user_input` 是：

```json
{
  "review_files": [
    {
      "path": "backend/src/example.py",
      "change_type": "modified",
      "old_ranges": [],
      "new_ranges": []
    }
  ],
  "role_context": {
    "change_risk_summary": {
      "file_count": 1,
      "changed_line_count": 0,
      "files": [
        {
          "path": "backend/src/example.py",
          "change_type": "modified",
          "changed_old_lines": 0,
          "changed_new_lines": 0,
          "language_hint": "python"
        }
      ],
      "risk_signals": []
    },
    "eligible_reviewer_references": ["correctness:v2"],
    "reviewer_catalog": [{"reference": "correctness:v2"}],
    "unavailable_reviewer_references": []
  }
}
```

当前生产状态需要单独强调：源码中没有 `build_planner_input_payload()` 和 `ChangeRiskSummary.from_snapshot()` 的生产调用者。Worker 目前持久化 Fixed Plan，或重建已有 Plan；它不会为新任务生成上述 PLANNER 输入并调用 `review-planner:v1`。所以该结构是已经实现和测试约束的运行时契约，不是当前主链实际发出的请求。

### 4.2 REVIEWER

Reviewer 有两种模型可见形式。

Fixed Plan、旧链路或没有 Planner guidance 时，最终 `user_input` 只有：

```json
{
  "review_files": [
    {
      "path": "backend/src/example.py",
      "change_type": "modified",
      "old_ranges": [],
      "new_ranges": []
    }
  ]
}
```

存在计划节点 guidance 时，[`add_reviewer_plan_guidance()`](../backend/src/codelens/worker/execution.py#L156-L179) 增加：

```json
{
  "review_files": [
    {
      "path": "backend/src/auth/service.py",
      "change_type": "modified",
      "old_ranges": [],
      "new_ranges": []
    }
  ],
  "role_context": {
    "planner_guidance": {
      "focus_paths": ["backend/src/auth/service.py"],
      "reason_codes": ["auth-boundary"]
    }
  }
}
```

- `focus_paths` 来自当前 Review Plan 中该 Reviewer 节点的关注路径。
- `reason_codes` 来自同一节点的规划理由代码。
- guidance 只是注意力提示，不会缩小 Snapshot 证据范围，也不会从 `review_files` 中删除其他文件。
- Reviewer 没有其他专属 `role_context` 字段。

Reviewer 的主要角色差异由 Agent Policy 决定，而不是通过 `role_context` 重复描述：例如 `security:v1` 与 `performance:v1` 拿到相同结构的文件范围和仓库规则，但加载不同的 Agent Prompt、维度和输出契约。

### 4.3 VERIFIER

Verifier 执行前，Worker 会先将 Reviewer 的候选 Finding 做确定性聚类，再由 [`_prepare_verdict()`](../backend/src/codelens/worker/execution.py#L880-L930) 增加：

```json
{
  "review_files": [
    {
      "path": "backend/src/example.py",
      "change_type": "modified",
      "old_ranges": [],
      "new_ranges": []
    }
  ],
  "role_context": {
    "verdict_context": {
      "schema_version": "1",
      "clusters": [
        {
          "cluster_id": "cluster-1",
          "canonical_candidate_id": "candidate-1",
          "candidate_ids": ["candidate-1", "candidate-2"],
          "title": "...",
          "category": "...",
          "severity": "...",
          "content": "...",
          "recommendation": "...",
          "primary_dimension": "security",
          "evidence_strength": "..."
        }
      ]
    }
  }
}
```

Verifier 必须对冻结投影中的每个 cluster 恰好作出一次 `accept`、`deny` 或 `merge` 决策。`_verdict_codec()` 会从同一个 `verdict_context` 重建约束，验证 cluster 覆盖、候选归属和 merge 合法性。Verifier 不接收 `planner_guidance`，也不能在 verdict 中发明新的根因、位置、证据或影响。

只有 Plan 中存在 Verifier 节点时才构造该输入。单个 General Reviewer 或单个专项 Reviewer 的 Plan 不需要 Verifier；多 Reviewer Plan 可以包含一个批量 Verifier。

### 4.4 宿主专用 `role_context`

[`add_host_run_identity()`](../backend/src/codelens/worker/execution.py#L182-L195) 会给每个计划节点增加 `_host_run_id`：

```json
{
  "role_context": {
    "_host_run_id": "<logical run id>"
  }
}
```

它只用于工具收集器的幂等身份。运行时保留原始 `role_context` 给宿主逻辑使用，但构造模型输入时会过滤所有 `_host_*` 键。该值不会出现在 `Runner.input`、系统指令或模型请求 Transcript 中。

## 5. Runtime 如何拆分内部信封

关键实现是 [`_split_agent_input()`](../backend/src/codelens/review/infrastructure/openai_runtime.py#L779-L839)。其核心等价于：

```python
model_role_context = {
    key: value
    for key, value in role_context.items()
    if not key.startswith("_host_")
}

user_input = json.dumps(
    {
        "review_files": review_files,
        **({"role_context": model_role_context} if model_role_context else {}),
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)

repository_instructions = json.dumps(
    {"repository_instructions": repository_instructions},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
```

该函数返回三个值：

1. `user_input: str`：最终交给 `Runner.input` 的规范 JSON 字符串。
2. `repository_instructions: str`：随后拼入 `Agent.instructions` 的规范 JSON 字符串。
3. 原始 `role_context`：宿主用于 Planner/Verifier 输出约束和 `_host_run_id`，不直接传给模型。

它采用 fail-closed 校验：顶层只能是基础两字段，或基础两字段加 `role_context`；字段类型或 UTF-8/JSON 不合法会在调用模型前失败。

## 6. 最终系统指令的拼接顺序

[`OpenAIAgentRuntime._invoke()`](../backend/src/codelens/review/infrastructure/openai_runtime.py#L199-L538) 使用固定顺序构造一个字符串，并以两个换行连接各段。

所有角色共有：

```python
instruction_sections = [
    prompts.review_policy,
    repository_instructions,
]
```

仅 REVIEWER 在此后插入：

```python
instruction_sections.append(prompts.review_workflow)
```

最后所有角色追加：

```python
instruction_sections.extend(
    (
        f"# Agent Policy\n{agent.prompt_template}",
        *skill_instruction_sections,
    )
)

instructions = "\n\n".join(instruction_sections)
```

因此三种角色的精确顺序为：

| 角色 | `Agent.instructions` 顺序 |
| --- | --- |
| PLANNER | Review Policy → Repository Instructions JSON → Planner Agent Policy → Skills |
| REVIEWER | Review Policy → Repository Instructions JSON → Review Workflow → Reviewer Agent Policy → Skills |
| VERIFIER | Review Policy → Repository Instructions JSON → Verifier Agent Policy → Skills |

`review-policy.md`、Repository Instructions 和 Agent Policy 都在同一个 `Agent.instructions` 字符串中，但逻辑优先级仍由段落顺序和 Review Policy 中的规则约束表达：仓库规则不能覆盖更高层的安全策略。

`tool-loop-warning.md` 不参与上述初始拼接。它被放入 `ToolExecutionLimits`，在运行时检测重复工具结果时作为有界警告反馈给模型。

## 7. 工具定义与角色白名单

工具不是通过自然语言列在 `user_input` 中，而是作为 Agents SDK 的函数工具定义传入。工具描述来自当前系统 locale 的 `tools.json`，参数结构来自各工具的 Pydantic/JSON Schema；能力 Profile 再按 `name + version` 做冻结白名单选择。

当前内置白名单见 [`builtin_capability_profiles()`](../backend/src/codelens/capabilities/infrastructure/builtin_profiles.py#L34-L77)：

| 角色/Profile | 证据工具 | 输出与完成工具 |
| --- | --- | --- |
| PLANNER `planner:v1` | `find_files:v1`、`grep:v1`、`read_file:v1`、`get_diff:v1` | `submit_review_plan:v1`、`finalize_plan:v1` |
| REVIEWER `legacy-reviewer:v1` | 同上 | `comment:v1`、`review_file_done:v1`、`task_done:v1` |
| REVIEWER `reviewer-comment-v2:v1` | 同上 | `comment:v2`、`review_file_done:v1`、`task_done:v1` |
| VERIFIER `verifier:v1` | `read_file:v1`、`get_diff:v1` | `verdict:v1`、`merge:v1`、`finalize_verdicts:v1` |

`CapabilityToolAssembler` 只装配执行规格允许的工具。Planner 和 Verifier 的结构化输出工具由各自 Collector 在运行时绑定；Reviewer 的 `comment` 版本决定使用旧版评论收集器还是 Candidate Finding v2 收集器。被接受的完成工具结果会终止 Agents SDK 循环，普通文本最终答复不是审查结果的权威来源。

## 8. 封装为 OpenAI Agents SDK 参数

CodeLens 不直接调用 `client.responses.create()` 或 `client.chat.completions.create()`，而是创建 OpenAI Python 客户端供 Agents SDK 的模型适配器使用，再调用 `Agent` 和 `Runner`。

### 8.1 OpenAI 客户端

每次尝试创建：

```python
client = AsyncOpenAI(
    api_key=provider_config.api_key,
    base_url=provider_config.base_url,
    http_client=httpx.AsyncClient(trust_env=False),
)
```

这些参数负责认证、网关地址和 HTTP 行为，不是模型可见 Prompt。

### 8.2 `Agent` 参数

关键构造代码为：

```python
investigation_agent = Agent(
    name=f"{agent.agent_id}:v{agent.version}",
    instructions="\n\n".join(instruction_sections),
    model=behavior.model_class(
        model=provider_config.model,
        openai_client=client,
    ),
    model_settings=behavior.model_settings,
    tools=model_tools,
    tool_use_behavior=_completion_tool_use_behavior(tool_context),
)
```

| `Agent` 参数 | CodeLens 内容 | 是否模型可见 |
| --- | --- | --- |
| `name` | `<agent_id>:v<version>` | 主要用于 Agent 身份和追踪，不属于业务 Prompt 正文 |
| `instructions` | 第 6 节描述的完整系统指令字符串 | 是 |
| `model` | 供应商适配器选择的模型类、模型名和 `AsyncOpenAI` 客户端 | 模型名决定请求目标 |
| `model_settings` | 输出 token 上限、reasoning/thinking 等供应商设置 | 影响模型推理与生成 |
| `tools` | 角色白名单中的工具描述、JSON Schema 与执行函数 | 是 |
| `tool_use_behavior` | 完成工具被接受后结束循环的宿主策略 | 不作为自然语言 Prompt，但控制 Agent 循环 |

OpenAI Provider 会依据 `provider_config.api_type` 选择 `OpenAIResponsesModel` 或 `OpenAIChatCompletionsModel`。当前 Provider 配置默认 API 类型是 `chat_completions`；配置为 `responses` 时使用 Responses 适配器。DeepSeek、智谱等兼容供应商走 Chat Completions 适配器并附加各自的 thinking 配置。

应用层不手工维护最终网络请求的 `messages` 或 Responses `input` 项；Agents SDK 根据上述 `Agent`、初始字符串输入、历史轮次和工具结果生成供应商请求。

### 8.3 `Runner` 参数

非流式入口的关键代码为：

```python
result = await Runner.run(
    starting_agent=investigation_agent,
    input=user_input,
    max_turns=provider_config.max_agent_turns,
    run_config=RunConfig(trace_include_sensitive_data=False),
)
```

有事件 sink 时使用等价的 `Runner.run_streamed(...)` 参数。

| `Runner` 参数 | 内容 |
| --- | --- |
| `starting_agent` | 上一步构造的 PLANNER、REVIEWER 或 VERIFIER Agent |
| `input` | 第 4 节对应角色的规范 JSON 字符串 |
| `max_turns` | 冻结执行限制覆盖后的最大 Agent 轮数 |
| `run_config` | 当前只显式设置 `trace_include_sensitive_data=False` |

没有向 `Runner` 传入 CodeLens `role_context`、Snapshot 对象或仓库对象。Snapshot 和 Git 适配器保存在宿主侧的工具执行上下文中，模型只能通过已授权工具间接访问。

### 8.4 一次模型请求最终包含什么

从模型可见性看，一次初始请求的关键组成是：

1. 模型名与模型设置。
2. 完整 `system_instructions`。
3. 当前角色的工具名称、描述、参数 JSON Schema 和 strict 标志。
4. 初始 `user_input` JSON 字符串。
5. 后续轮次中的模型输出、工具调用和工具结果，由 Agents SDK 维护。

运行时 [`_model_input()`](../backend/src/codelens/review/infrastructure/openai_runtime.py#L919-L947) 会为 Transcript/事件观测生成以下审计投影：

```json
{
  "model": "<model name>",
  "model_settings": "<JSON-compatible settings>",
  "system_instructions": "<完整 Agent.instructions>",
  "tools": [
    {
      "name": "read_file",
      "description": "...",
      "parameters": {},
      "strict_json_schema": true
    }
  ],
  "user_input": "<规范 JSON 字符串>"
}
```

这个 `_model_input()` 结果是完整模型可见输入的审计表示，不是额外发送给 Provider 的第二份请求。

## 9. 模型可见与宿主专用数据边界

| 数据 | 模型是否可见 | 传递方式 |
| --- | --- | --- |
| Review Policy | 是 | `Agent.instructions` |
| Repository Instructions 正文及作用域 | 是 | `Agent.instructions` 中的 JSON 段 |
| Review Workflow | 仅 REVIEWER | `Agent.instructions` |
| Agent 专属 Prompt | 是 | `Agent.instructions` |
| 激活 Skill 指令 | 有 Skill 时可见 | `Agent.instructions` |
| `review_files` 路径、类型和行范围 | 是 | `Runner.input` |
| Planner guidance | 对应 Reviewer 可见 | `Runner.input.role_context` |
| Planner risk/catalog/readiness 投影 | 对 PLANNER 可见 | `Runner.input.role_context`；当前主链未发送 |
| Verdict clusters | 对 VERIFIER 可见 | `Runner.input.role_context` |
| `_host_run_id` 和其他 `_host_*` | 否 | 仅宿主工具上下文 |
| Snapshot ID、工作树对象、Git 适配器 | 否 | 仅宿主工具上下文 |
| API key、base URL | 否 | `AsyncOpenAI` 配置 |
| `prompt_locale` | 否 | 只用于选择 Prompt bundle |
| 完整源码与 diff | 初始时不可见 | 经授权工具按需读取 |

仓库源码始终是不可信数据。Repository Instructions 虽然以系统指令段发送，是为了保持规则优先级和作用域语义，但仍受更高层 Review Policy、安全边界和只读工具能力约束。

## 10. 当前生产链路与未接入部分

### 10.1 当前已接入

- `ContextBuilder` 生成所有角色共享的基础信封。
- Fixed Plan 的 Reviewer 直接得到基础范围；有节点 guidance 时得到 `planner_guidance`。
- 多 Reviewer Plan 在聚类后为 Verifier 构造 `verdict_context`。
- Runtime 对三种角色都有独立的 Prompt、能力 Profile、结构化输出 Collector 和校验器。
- 任一角色被调用时，都经过同一套信封拆分、系统指令拼接、工具白名单装配和 Agents SDK 调用。

### 10.2 当前尚未接入

- 新任务不会由 Worker 调用 `review-planner:v1` 产生 Adaptive Plan。
- `ChangeRiskSummary.from_snapshot()` 当前没有生产调用者。
- `build_planner_input_payload()` 当前没有生产调用者。
- 因此当前实际发出的模型请求里不会出现 PLANNER 的 `change_risk_summary`、`eligible_reviewer_references`、`reviewer_catalog`、`unavailable_reviewer_references`；除非未来主链接入，或测试/其他调用方显式构造并调用 Planner Runtime。

接入 Adaptive Planner 时，必须先补齐 readiness 和 catalog 投影的生产构造、Planner 失败/降级策略、Plan 持久化与恢复链路，并保持本文件第 4.1 节的输入约束和第 7 节的工具白名单。

## 11. 维护与验证清单

修改 Prompt 构造时至少检查：

- 是否改变了基础内部信封、最终 `Runner.input` 或 Repository Instructions 的系统指令位置。
- 是否意外把 `_host_*`、Snapshot 路径、凭证或其他宿主数据暴露给模型或 Transcript。
- 是否保持 Reviewer 独有 `review_workflow`，避免把评论完成协议错误地加给 Planner/Verifier。
- 是否同时更新相应的 Agent Prompt、工具描述、输出 Codec、能力 Profile 和契约测试。
- 是否保持各 locale 的系统 bundle 文件集和 `tools.json` 工具名完全一致。
- 修改 Agent 或系统 Prompt 时，按 [`prompts/REVIEW.md`](../prompts/REVIEW.md) 同步维护多语言版本。
- 若修改稳定输入信封、角色边界、数据安全边界或 Runtime/Provider 契约，必须同步更新 [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md)。

主要实现与测试入口：

- [`context_builder.py`](../backend/src/codelens/review/application/context_builder.py)
- [`planning.py`](../backend/src/codelens/review/application/planning.py)
- [`worker/execution.py`](../backend/src/codelens/worker/execution.py)
- [`openai_runtime.py`](../backend/src/codelens/review/infrastructure/openai_runtime.py)
- [`provider_adapters.py`](../backend/src/codelens/review/infrastructure/provider_adapters.py)
- [`builtin_agents.py`](../backend/src/codelens/reviewer_catalog/infrastructure/builtin_agents.py)
- [`builtin_profiles.py`](../backend/src/codelens/capabilities/infrastructure/builtin_profiles.py)
- [`test_openai_runtime.py`](../backend/tests/contract/review/test_openai_runtime.py)
- [`test_planner_output.py`](../backend/tests/contract/review/test_planner_output.py)
- [`test_i18n_prompt_loader.py`](../backend/tests/unit/review/test_i18n_prompt_loader.py)
