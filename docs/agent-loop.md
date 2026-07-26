# OpenAI Agents SDK Agent Loop 详解

 Agent Loop 是运行机制，负责“模型调用 -> 工具执行 -> 结果回传 -> 再次调用 -> 停止”；其他几种是运行在这个循环之上的决策范式，决定每一轮让模型做什么。

   概念                所属层次              核心过程                                         适用场景
  ━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Agent Loop          Runtime / 控制循环    调模型、执行工具、维护历史、判断停止             所有工具型 Agent 的基础设施
  ──────────────────  ────────────────────  ───────────────────────────────────────────────  ──────────────────────────────
   ReAct               单循环决策策略        Reason -> Action -> Observation -> Reason        路径不确定、需要边调查边决策
  ──────────────────  ────────────────────  ───────────────────────────────────────────────  ──────────────────────────────
   Plan-and-Execute    任务组织策略          先生成计划，再逐步执行，最后汇总                 步骤明确、任务较复杂
  ──────────────────  ────────────────────  ───────────────────────────────────────────────  ──────────────────────────────
   Planner/Resolver    多阶段角色划分        Planner 拆解，Workers 执行，Resolver 汇总冲突    多 Agent、并行调查、结果综合
  ──────────────────  ────────────────────  ───────────────────────────────────────────────  ──────────────────────────────
   Reflection          质量改进策略          生成结果 -> 自检/批评 -> 修正 -> 再检查          对准确性要求高、允许增加成本

  它们的组合关系可以表示为：

  Agent Runtime
  └── Agent Loop
      ├── 规划阶段：Plan
      ├── 调查阶段：ReAct
      │   ├── 模型推理
      │   ├── 调用工具
      │   └── 读取观察结果
      ├── 反思阶段：Reflection
      │   ├── 检查证据是否充分
      │   └── 必要时返回调查阶段
      └── 汇总阶段：Resolve / Finalize

  以代码审查为例：

  Plan
    -> 列出需要审查的文件和风险点

  ReAct
    -> get_diff
    -> 观察 diff
    -> read_file
    -> 观察上下文
    -> grep 调用路径
    -> 判断是否构成问题

  Reflection
    -> 检查触发路径是否真实
    -> 检查证据和严重性是否匹配
    -> 证据不足则继续调查或放弃

  Resolve
    -> 去重并处理冲突
    -> 形成最终 Finding

  当前 CodeLens 的实现主要是“通用 Agent Loop + 类 ReAct 调查”：

  - OpenAI Agents SDK 的 Runner 负责循环和工具调度。
  - 模型根据当前上下文决定调用 get_diff、read_file、grep 等工具，属于 ReAct 风格。
  - comment 用来提交已确认的 Finding。
  - review_file_done 记录已经取证并完成调查的文件。
  - task_done 尝试结束调查，未覆盖全部文件时会被宿主打回。
  - 没有独立的结构化 Planner Agent。
  - 没有单独的 Resolver Agent。
  - 没有明确的 Reflection/Evaluator 循环。
  - FindingValidator 是宿主侧确定性校验，不等同于模型 Reflection。
  - comment 被拒绝后模型可以修正参数重试，这属于局部反馈循环，但还不是完整反思范式。

  因此可以这样理解：

  > Agent Loop 是发动机；ReAct、Plan、Reflection 和 Resolve 是不同的驾驶策略与流程编排方式。

  它们可以同时存在，并不互斥。复杂度通常依次增加：

  普通 Agent Loop
      -> ReAct
      -> Plan + ReAct
      -> Plan + ReAct + Reflection
      -> Planner + Workers + Reflection + Resolver

  层次越多，通常质量和可控性越强，但模型调用次数、延迟、Token 成本以及状态管理复杂度也会同步增加。

## 1. 文档范围

本文详细解释 CodeLens 使用的 OpenAI Agents SDK 如何启动 Agent、调用模型、解析工具调用、执行本地工具、把结果送回模型并最终停止。阅读目标是能够顺着 SDK 和 CodeLens 源码回答以下问题：

1. `Runner.run_streamed()` 调用后立即发生什么；
2. 一个 turn 如何定义，历史消息如何进入下一轮；
3. 模型返回多个工具调用时，SDK 如何调度；
4. 工具参数如何从 JSON 变成 Python 参数；
5. SDK 如何决定 `RunAgain`、`FinalOutput`、`Handoff` 或 `Interruption`；
6. `task_done`、普通最终文本、最大回合数和超时分别如何停止运行；
7. CodeLens 在 SDK 默认机制之外增加了哪些限制和输出规则。

本文以仓库当前锁定的 `openai-agents==0.18.3` 为基线。依赖版本见 [`backend/uv.lock`](../backend/uv.lock)，CodeLens 适配入口见 [`openai_runtime.py`](../backend/src/codelens/review/infrastructure/openai_runtime.py)。SDK 后续版本可能调整内部模块和私有函数，公共概念应以 OpenAI 官方文档为准：

- [Running agents](https://openai.github.io/openai-agents-python/running_agents/)
- [Streaming](https://openai.github.io/openai-agents-python/streaming/)
- [Tools](https://openai.github.io/openai-agents-python/tools/)
- [Results](https://openai.github.io/openai-agents-python/results/)
- [SDK v0.18.3 source](https://github.com/openai/openai-agents-python/tree/v0.18.3)

CodeLens 整体 Runtime、Prompt 和结果持久化见 [`runtime-mechanism.md`](./runtime-mechanism.md)，7 个业务工具的参数和返回值见 [`build-in-tool.md`](./build-in-tool.md)。

## 2. SDK 中的核心对象

先区分 Agent Loop 中几个职责不同的对象：

| 对象 | SDK/项目类型 | 职责 |
| --- | --- | --- |
| Agent 定义 | `agents.Agent` | 保存 instructions、model、model settings、tools、output type 和循环策略 |
| 循环入口 | `agents.Runner` | 提供 `run`、`run_sync`、`run_streamed` 公共 API |
| 实际执行器 | SDK `AgentRunner` | 维护 turn、消息、工具结果、handoff、guardrail 和停止状态 |
| 流式结果 | `RunResultStreaming` | 暴露事件队列、当前 turn、raw responses、final output 和后台任务 |
| 单轮模型结果 | `ModelResponse` | 保存一次模型调用的 output items、usage 和 response ID |
| 单轮决策 | `SingleStepResult` | 保存本轮新增 items 和 `NextStep*` 状态 |
| 函数工具 | `FunctionTool` | 保存名称、描述、JSON Schema 和 `on_invoke_tool` |
| 项目 Runtime | `OpenAIAgentRuntime` | 把 CodeLens 配置、Prompt、Snapshot 工具和 SDK 连接起来 |
| 项目结果收集器 | `ReviewCommentCollector` | 保存已接受评论，生成最终 FindingBatch |

`Agent` 是配置，不是一个自己运行的常驻进程。真正执行循环的是 `Runner` 背后的 `AgentRunner`。

## 3. CodeLens 如何构造 Agent

### 3.1 调用前准备

在进入 SDK 前，CodeLens 已经完成：

1. 冻结 `ReviewSnapshot`；
2. 用 `ContextBuilder` 生成 `review_files` 和 `repository_instructions`；
3. 加载当前语言的系统 Prompt；
4. 加载 Reviewer 专属 Prompt；
5. 读取当前激活模型网关；
6. 创建当前 Run 专属的证据工具和评论收集器。

`OpenAIAgentRuntime._invoke()` 随后创建 `Agent[None]`。其关键配置可简化为：

```python
agent = Agent(
    name="correctness:v1",
    instructions=(
        review_policy
        + review_workflow
        + reviewer_policy
    ),
    model=provider_model,
    model_settings=provider_model_settings,
    tools=[
        find_files,
        grep,
        read_file,
        get_diff,
        comment,
        review_file_done,
        task_done,
    ],
    tool_use_behavior=completion_tool_use_behavior,
)
```

### 3.2 SDK 默认值与显式完成策略

理解默认值对判断停止条件很重要。除 `tool_use_behavior` 外，CodeLens 当前没有设置以下 `Agent` 字段：

| 字段 | SDK 默认值 | 对 CodeLens 的影响 |
| --- | --- | --- |
| `output_type` | `None`，等价于普通字符串输出 | 没有工具调用时，模型文本成为 SDK final output |
| `handoffs` | 空列表 | 当前不会切换到另一个 Agent |
| `input_guardrails` | 空列表 | SDK 不运行输入 guardrail |
| `output_guardrails` | 空列表 | SDK final output 不经过 SDK output guardrail |
| `hooks` | `None` | CodeLens 主要通过 stream events 观测，不依赖 Agent hooks |
| `reset_tool_choice` | `True` | 工具调用后重置强制 tool choice，降低持续调用同一工具的风险 |

CodeLens 显式把 `tool_use_behavior` 设置为自定义函数：被接受的 `task_done` 直接成为 final output，其他函数工具结果仍返回模型继续下一轮。

CodeLens 也没有向 `Runner` 传入 `session`、`conversation_id`、`previous_response_id`、handoff 或人工审批状态。因此本文会先解释 SDK 完整状态机，再指出 CodeLens 实际走到的分支。

## 4. 启动：`run_streamed()` 做了什么

### 4.1 CodeLens 的调用

生产路径调用：

```python
stream = Runner.run_streamed(
    starting_agent=agent,
    input=input_text,
    max_turns=provider_config.max_agent_turns,
    run_config=RunConfig(trace_include_sensitive_data=False),
)

async for event in stream.stream_events():
    ...
```

注意 `Runner.run_streamed()` 本身不是 async 函数，不需要 `await`。它会创建并返回 `RunResultStreaming`，同时用 `asyncio.create_task()` 在后台启动真正的 Agent Loop。

### 4.2 `RunResultStreaming` 的初始状态

SDK 创建流式结果时，主要字段处于以下状态：

```text
input          = 首次用户输入
current_agent  = starting_agent
current_turn   = 0
max_turns      = CodeLens 网关配置
final_output   = None
is_complete    = False
new_items      = []
raw_responses  = []
event_queue    = 空队列
run_loop_task  = 后台 start_streaming task
```

后台任务负责产生事件；`stream_events()` 负责从队列消费事件、传播后台异常，并在退出前等待后台循环完成。

### 4.3 为什么必须持续消费事件

`run_streamed()` 返回不代表模型已经完成。调用方必须遍历 `stream_events()`，否则：

- 看不到模型 delta、工具调用和工具结果；
- 不能在正常位置收到后台 `MaxTurnsExceeded` 等异常；
- 资源清理和取消传播可能延后；
- `final_output`、`raw_responses` 和 `is_complete` 可能仍在变化。

CodeLens 的 `_run_observable()` 会一直消费到事件流结束，因此返回 Runtime 上层时，SDK Run 已经完成或已经抛出异常。

## 5. Turn 的精确定义

SDK 把“一次模型调用”定义为一个 turn。一个 turn 包括：

1. 准备本轮 system instructions、历史 items 和工具定义；
2. 调用一次模型；
3. 接收并解析完整模型响应；
4. 执行该响应要求的本地工具和相关副作用；
5. 计算下一步状态。

工具调用本身不会额外增加 turn。只有把工具结果发送给模型、再次调用模型时，才进入下一个 turn。

例如：

```text
turn 1: 模型 -> get_diff + read_file
        SDK -> 并发执行两个工具

turn 2: 模型读取两个工具结果 -> comment
        SDK -> 执行 comment

turn 3: 模型读取 comment acknowledgement -> review_file_done
        SDK -> 记录已经取证并调查完成的文件

turn 4: 模型读取文件完成结果 -> task_done
        SDK -> 执行 task_done
        CodeLens 自定义 tool_use_behavior -> FinalOutput
```

这是 4 个模型 turn、4 次模型请求；工具调用数量是 5 次。若 `task_done` 因覆盖不完整被拒绝，模型继续取证和声明文件，实际 turn 与工具调用数会继续增加。

## 6. 主循环状态机

SDK 每轮最终会产生四种 `NextStep` 之一：

```text
                         +----------------------+
                         |  run_single_turn     |
                         +----------+-----------+
                                    |
                                    v
                         解析 ModelResponse items
                                    |
                 +------------------+------------------+
                 |                  |                  |
                 v                  v                  v
          approval needed       handoff call      function tools
                 |                  |                  |
                 v                  v                  v
        NextStepInterruption  NextStepHandoff    执行工具并判断
                                                       |
                                      +----------------+---------------+
                                      |                                |
                                      v                                v
                              工具结果直接结束                   结果送回模型
                              NextStepFinalOutput                NextStepRunAgain

无待执行工具、无 handoff、存在普通文本
                 |
                 v
        NextStepFinalOutput
```

完整 Runner 支持：

- `NextStepRunAgain`：继续同一 Agent 的下一轮；
- `NextStepFinalOutput`：运行 output guardrails 和结束 hooks 后停止；
- `NextStepHandoff`：切换 Agent 后继续循环；
- `NextStepInterruption`：暂停并返回待审批项，之后可从 `RunState` 恢复。

CodeLens 没有配置 handoff、审批工具或 SDK guardrail，所以正常情况下只会出现 `RunAgain` 和 `FinalOutput`。

## 7. 每一轮如何准备模型请求

### 7.1 重新解析 Agent 配置

每个 turn 开始时，SDK都会重新取得：

- 当前 Agent 的 system prompt；
- 动态 Prompt 配置；
- 当前启用的工具集合；
- handoff 定义；
- 输出 schema；
- 模型和 model settings。

SDK 允许 instructions 和工具启用状态是动态函数。CodeLens 传入的是静态 instructions 和固定工具列表，因此每轮解析结果相同，但 SDK 仍按通用流程处理。

### 7.2 历史如何累积

SDK 将以下内容转换为下一轮模型输入：

```text
首次用户输入
+ 前几轮模型 message/reasoning items
+ 前几轮 tool call items
+ 对应 tool output items
```

CodeLens 没有使用 SDK `Session` 或服务端 `conversation_id`，因此本次 Run 的历史由 SDK 在内存中维护。对于 Chat Completions 和 Responses 两种 Provider Adapter，SDK 会分别转换成供应商需要的消息或 response items。

工具结果必须带有对应 `call_id`，模型才能把每个结果与原始工具调用关联。SDK 的 `ToolCallOutputItem` 负责保存这种关联。

### 7.3 工具定义如何进入请求

每次模型调用都会把当前工具集合交给 Model Adapter。每个函数工具包含：

```json
{
  "name": "get_diff",
  "description": "本地化工具说明",
  "parameters": {
    "type": "object",
    "properties": {},
    "required": []
  },
  "strict": true
}
```

实际 schema 根据 Python 参数类型生成。CodeLens 没有设置 `tool_choice` 或 `parallel_tool_calls`，因此是否调用工具、是否在一次响应中生成多个工具调用，主要由模型和网关默认行为决定。

## 8. 模型响应如何被解析

### 8.1 流式阶段

`run_single_turn_streamed()` 调用 Provider Model 的 `stream_response()`。供应商事件到达时，SDK先把原始事件放入 `RunResultStreaming._event_queue`：

```text
response.output_text.delta
response.reasoning_summary_text.delta
response.output_item.done
response.completed
...
```

当完整 tool call output item 到达时，SDK 会额外生成语义事件 `RunItemStreamEvent(name="tool_called")`。这时工具还不一定执行完成；它只是说明模型已经提出调用。

收到 terminal response 后，SDK 构造 `ModelResponse`：

- `output`：message、reasoning、function call 等 output items；
- `usage`：本轮 token 使用量；
- `response_id` 和 `request_id`。

### 8.2 `process_model_response()`

SDK 遍历 `ModelResponse.output`，把供应商类型转换成内部 `RunItem`，并按名称匹配工具：

```text
普通 message        -> MessageOutputItem
reasoning           -> ReasoningItem
function tool call  -> ToolCallItem + ToolRunFunction
handoff call        -> HandoffCallItem
其他 hosted tool    -> 对应 RunItem/执行计划
```

如果模型调用了不存在的函数工具，SDK会构造可回传模型的“tool not found”结果；对于某些缺失的 hosted tool，则会作为 `ModelBehaviorError` 失败。

CodeLens 在把工具交给 Runner 前还会调用 `_validate_model_tool_contract()`，提前拒绝泄露内部 Snapshot 元数据的工具说明或 schema。

## 9. 函数工具从定义到执行

### 9.1 `@function_tool` 生成什么

CodeLens 的 7 个工具都使用 SDK `@function_tool`。装饰器创建 `FunctionTool` 时会：

1. 读取 Python 函数签名；
2. 读取 `Annotated`、`Literal` 和 Pydantic Model；
3. 生成参数 Pydantic Model；
4. 生成 strict JSON Schema；
5. 保存工具名称和描述；
6. 生成统一的 async `on_invoke_tool(context, arguments_json)`。

CodeLens 使用 `description_override`，所以模型可见说明来自 `prompts/sys/<locale>/tools.json`，不是函数 docstring。

### 9.2 参数解析

模型给出的工具参数最初是 JSON 字符串。SDK 调用工具时依次执行：

```text
arguments JSON string
    |
    v
json.loads
    |
    v
Pydantic 参数 Model 校验
    |
    v
schema.to_call_args
    |
    v
Python positional args / keyword args
    |
    v
调用原始函数
```

JSON 语法错误、类型错误、缺少必填参数或违反枚举/范围约束时，会形成 `ModelBehaviorError`，再由 FunctionTool 的失败策略决定是返回模型可见错误还是终止 Run。

CodeLens 的 `reject_unknown_arguments()` 还会在 SDK 参数解析前检查额外字段。这样即使某个兼容网关没有完整执行 strict schema，也不会把未声明字段传入工具。

### 9.3 async 与同步函数

SDK 对两者的处理不同：

- async 工具直接在事件循环中 `await`；
- 同步工具通过 `asyncio.to_thread()` 隔离，避免阻塞 Agent 事件循环。

CodeLens 模型可见工具定义均为 async 包装函数；内部需要文件系统、Git 或 CPU 隔离时，再由项目 Adapter 自行使用线程或独立进程。

### 9.4 CodeLens 的额外包装顺序

一个 CodeLens FunctionTool 的实际调用边界从外到内大致是：

```text
ToolExecutionLimiter
  -> 消耗共享工具预算
  -> asyncio.timeout
  -> reject_unknown_arguments
  -> SDK FunctionTool failure handler
  -> JSON/Pydantic 参数解析
  -> 具体工具函数
  -> 重复参数/结果指纹检查
```

项目级工具预算、超时或重复循环异常发生在最外层，会使 Agent Run 失败。普通工具参数或业务校验错误通常由 SDK FunctionTool 失败处理器转换成模型可见错误文本，模型可以修正参数后重试。

这里还有一层异常转换需要注意：这些项目异常继承 CodeLens `DomainError`，并不继承 SDK `AgentsException`。SDK 函数工具批处理器会把它们包装为 `UserError`；`OpenAIAgentRuntime` 当前再把 `UserError` 归类为 `invalid_model_output`。因此它们能够中止循环，但 `tool_call_limit_exceeded`、`tool_invocation_timeout` 等项目异常身份当前不会原样穿透 Runner。这是现有实现行为，不应仅根据异常类名推断最终 Transcript 错误码。

## 10. 同一轮多个工具如何执行

模型可以在一次响应中返回多个函数工具调用。SDK 0.18.3 会建立 `ToolExecutionPlan`，再由 `_FunctionToolBatchExecutor` 调度。

默认情况下：

1. 每个函数工具调用创建一个 `asyncio.Task`；
2. 未设置 `max_function_tool_concurrency` 时，可用 slot 等于待执行工具数；
3. 多个函数工具并发执行；
4. SDK 用 `asyncio.wait(..., FIRST_COMPLETED)` 持续收集结果；
5. 最终结果仍按原始工具调用顺序构造 `FunctionToolResult`；
6. 所有结果转换成带 `call_id` 的 `ToolCallOutputItem`。

CodeLens 没有设置 SDK 的 `max_function_tool_concurrency`，所以同一模型响应中的多个函数工具可能并发。项目的 `ToolExecutionLimiter` 使用 `asyncio.Lock` 原子消耗共享预算，因此并发工具不会绕过总调用次数限制。

这也意味着工具实现不能假定“同一轮一定串行”。`FilesystemReviewTools` 主要是只读的；`ReviewCommentCollector` 和 `task_done` 带有任务内状态，阅读或修改它们时应考虑同一响应中并发调用的可能性。

### 10.1 工具失败时发生什么

可分为两类：

| 失败类型 | 典型例子 | 行为 |
| --- | --- | --- |
| 可修正调用错误 | 参数非法、代码片段无法定位、额外字段 | 通常转换成工具错误结果并回送模型 |
| Run 级边界错误 | 工具总次数耗尽、单次超时、重复结果循环 | 项目异常被 SDK 包装为 `UserError`，终止整个 Run |

并发批次中某个未被工具失败处理器吸收的异常，会触发 SDK 取消仍处于可取消阶段的兄弟任务，然后传播该异常。已经进入 post-invoke 阶段的任务会按 SDK 的清理策略等待或收集结果，以避免 hook 和状态半完成。

## 11. 工具结果为什么会触发下一轮

工具执行完成后，SDK先检查 `Agent.tool_use_behavior`。

SDK支持四种策略：

| 策略 | 行为 |
| --- | --- |
| `run_llm_again` | 把工具结果加入历史，返回 `NextStepRunAgain` |
| `stop_on_first_tool` | 第一个函数工具结果直接成为 final output |
| `StopAtTools` | 指定工具被调用时，其结果直接成为 final output |
| 自定义函数 | 根据全部函数工具结果决定是否结束 |

CodeLens 使用自定义 `ToolsToFinalOutputFunction`。函数工具执行后，它检查任务内 Collector 状态：`task_done` 已被宿主接受时返回 `ToolsToFinalOutputResult(is_final_output=True)`，立即结束 Agent Loop；其他工具以及被拒绝的 `task_done` 返回 `is_final_output=False`，工具结果进入历史并触发下一轮。

下一轮请求会包含类似的逻辑历史：

```json
[
  {"role": "user", "content": "初始 Review JSON"},
  {"type": "function_call", "name": "get_diff", "call_id": "call_1"},
  {"type": "function_call_output", "call_id": "call_1", "output": "{...diff...}"}
]
```

模型读取结果后决定继续取证、提交评论或结束。

## 12. SDK 如何判断普通 Final Output

工具和 handoff 处理完后，SDK检查本轮是否仍有待执行工具。

CodeLens 没有配置结构化 `output_type`，因此走 plain-text 分支：

- 如果本轮没有工具调用，最后一个 message 的文本成为 `NextStepFinalOutput`；
- 如果本轮没有工具调用且没有文本，空字符串也可成为 final output；
- 如果本轮包含工具调用，即使同时出现文本，也会先执行工具，再由 CodeLens 的自定义 `tool_use_behavior` 决定结束或进入下一轮；
- SDK final output 只表示 Agent Loop 已经结束，不表示 CodeLens 已接受任何 Finding。

进入 `NextStepFinalOutput` 后，SDK运行 output guardrails 和结束 hooks，设置：

```text
stream.final_output = 模型最终文本或 accepted task_done 对应的空字符串
stream.is_complete = True
```

然后向事件队列写入完成 sentinel，`stream_events()` 消费完剩余事件后退出。

## 13. `task_done` 如何有条件地停止 SDK

`task_done` 是 CodeLens 定义的 FunctionTool，不是 Agents SDK 内置的终止指令。

模型调用 `task_done` 时：

1. SDK按普通工具解析参数；
2. `ReviewCommentCollector.complete()` 将 Snapshot 的全部 Review 文件与已经通过 `review_file_done` 记录的文件比较；
3. 覆盖完整时返回 `accepted: true`；覆盖不完整时返回 `accepted: false`、`missing_evidence_files` 和 `undeclared_files`；
4. 自定义 `ToolsToFinalOutputFunction` 检查 `comment_collector.is_completed`；
5. 完成已接受时直接产生 `NextStepFinalOutput`，不再调用一次模型生成最终文本；
6. 完成被拒绝时产生 `NextStepRunAgain`，acknowledgement 被送入下一轮，模型可以继续取证、调用 `review_file_done` 并重试 `task_done`；
7. Runner 因普通最终文本提前结束时，`OpenAIAgentRuntime` 仍强制检查 `comment_collector.is_completed`，没有被接受的 `task_done` 就以 `review_completion_not_declared` 失败。

Runtime 没有使用下面这种仅按工具名称停止的配置：

```python
tool_use_behavior={"stop_at_tool_names": ["task_done"]}
```

因为 `StopAtTools` 无法区分 `accepted: false`，它会错误终止被打回的完成请求。当前自定义函数依据 Collector 的宿主状态决策，只有真正被接受的 `task_done` 才是停止开关。

为避免模型无限打回，每个 Run 使用启动时读取的 `max_incomplete_review_retries`；超过上限后，下一次不完整的 `task_done` 会被强制接受并立即停止，覆盖状态持久化为 `incomplete`。最终聚合时，全部 Agent 为 `complete` 才进入 `completed`；任一 Agent 为 `incomplete` 则进入 `partial`，并通过 `incomplete_files` 和 `review_coverage_incomplete` 显式保留覆盖缺口。

## 14. 最大回合数如何停止

CodeLens 把 `provider_config.max_agent_turns` 传给 Runner。SDK在准备每次模型调用前递增 `current_turn`，然后检查：

```python
current_turn += 1
if current_turn > max_turns:
    raise MaxTurnsExceeded(...)
```

因此 `max_turns=N` 最多允许 N 次模型调用，不是 N 次工具调用。

一个容易忽略的边界是：如果第 N 次模型调用返回工具调用，SDK仍会执行这些工具；当它准备第 N+1 次模型调用、希望把工具结果交回模型时，才发现超过 turn 上限并抛错。此时工具副作用已经发生。CodeLens 的证据工具只读，`comment` 只修改当前 Run 内存，所以不会写坏仓库或持久化业务数据，但本次 Review 仍会以最大回合数错误结束。

`OpenAIAgentRuntime` 捕获 SDK `MaxTurnsExceeded`，转换为稳定的 `AgentMaxTurnsExceededError`，不会把 SDK 异常类型暴露给应用层。

## 15. 超时、取消和其他停止方式

### 15.1 Agent 总超时

CodeLens 在消费 `stream.stream_events()` 外层使用：

```python
async with asyncio.timeout(provider_config.agent_timeout):
    async for event in stream.stream_events():
        ...
```

总时限覆盖模型流式响应、工具执行和后续 turn。超时会取消当前等待并被 Runtime 转换为 `agent_run_timeout`。

### 15.2 工具超时

每个 CodeLens 工具还由 `ToolExecutionLimiter` 单独包裹 `asyncio.timeout(tool_timeout_seconds)`。工具边界首先抛出 `ToolInvocationTimeoutError`；SDK 随后把它包装为 `UserError`，因此整个 Run 终止，不作为普通参数错误交给模型重试。Runtime 当前对外归类为 `invalid_model_output`。

`grep` 内部的正则子进程还有更短的独立执行时限，它属于工具实现边界，不等同于 SDK tool timeout。

### 15.3 SDK 显式取消

`RunResultStreaming.cancel()` 支持：

- `immediate`：取消后台任务、清理队列并立即完成；
- `after_turn`：允许本轮模型和工具执行结束，在下一轮前停止。

当前 CodeLens 没有直接调用 `stream.cancel()`。Review 取消意图由应用层持久化并在 Orchestrator 边界检查；它不是 SDK token 级即时取消协议。Agent 总超时或执行任务自身被取消时，`stream_events()` 的清理逻辑才会进一步取消 SDK 后台任务。

### 15.4 模型或 Provider 失败

以下情况也会终止循环：

- Provider HTTP 错误、连接错误、限流或超时；
- streaming response 进入 failed/incomplete/error terminal 状态；
- 模型拒绝且没有错误处理器；
- 非法 structured output；
- 未知 SDK next step；
- Run 级工具异常；
- guardrail tripwire（CodeLens 当前未配置）。

CodeLens 把供应商和 SDK 异常映射成 provider-neutral 领域错误，并只把脱敏后的稳定诊断写入 Transcript。

## 16. 流式事件在 CodeLens 中如何输出

SDK会产生两类事件：

1. `RawResponsesStreamEvent`：供应商原始流式语义事件；
2. `RunItemStreamEvent`：SDK完成某个语义 item 或工具阶段后的事件。

CodeLens `_visible_event()` 只映射其中一部分：

| SDK 事件 | CodeLens 事件 |
| --- | --- |
| `response.output_text.delta` | `model_output_delta` |
| `response.output_text.done` | `model_output_completed` |
| `response.reasoning_summary_text.delta` | `model_reasoning_delta` |
| `response.reasoning_summary_text.done` | `model_reasoning_completed` |
| `RunItemStreamEvent(name="tool_called")` | `tool_call` |
| `RunItemStreamEvent(name="tool_output")` | `tool_result` |

时序上，工具事件通常是：

```text
模型流中出现完整 function call item
    -> tool_call
SDK 执行本地 FunctionTool
    -> tool_result
SDK 将结果加入下一轮输入
```

Runtime 消费完事件流后，还会遍历 `raw_responses`，记录每次实际模型调用的完整供应商响应和 usage。`len(raw_responses)` 因而就是过程报告中的 LLM call count。

## 17. SDK Final Output 与 CodeLens 最终输出的区别

SDK结束时，`RunResultStreaming.final_output` 是模型最后一轮的普通文本。但 CodeLens 明确忽略它。

Runtime真正输出的是：

```python
canonical_bytes = output_codec.encode(
    comment_collector.finding_batch()
)
```

`finding_batch()` 只包含通过 `comment` 工具接受的候选。最终文本中即使包含新的问题、不同严重性或完全不同的 JSON，也不会进入 FindingBatch。

可以把两个输出理解为：

| 输出 | 作用 |
| --- | --- |
| SDK `final_output` | 告诉 Runner 循环已经正常结束 |
| CodeLens `FindingBatch` | 作为后端 Artifact 和 FindingValidator 的业务输入 |

之后 Orchestrator 才会写 Artifact、保存 checkpoint、执行 `FindingValidator` 并持久化最终 Findings。

## 18. CodeLens 实际状态机

去掉 CodeLens 没有使用的 handoff、approval、session 和 guardrail 分支后，当前实际流程可以简化为：

```text
Runner.run_streamed
    |
    v
创建 RunResultStreaming + 后台 start_streaming task
    |
    v
current_turn += 1，检查 max_agent_turns
    |
    v
组装 instructions + 全部历史 + 7 个工具 schema
    |
    v
Provider Model.stream_response
    |
    v
流式事件进入 event_queue，最终得到 ModelResponse
    |
    v
process_model_response
    |
    +-- 有 function calls
    |      |
    |      v
    |   并发执行工具
    |      |
    |      +-- 普通工具错误 -> 错误字符串回模型
    |      +-- Run 级限制错误 -> 整体失败
    |      |
    |      v
    |   自定义 tool_use_behavior
    |      |
    |      +-- accepted task_done -> NextStepFinalOutput
    |      |
    |      +-- 其他结果 -> tool outputs 加入历史 -> NextStepRunAgain
    |
    +-- 无 function calls
           |
           v
       最后一条文本成为 NextStepFinalOutput
           |
           v
       stream.is_complete = True
           |
           v
       CodeLens 忽略 SDK final_output
           |
           v
       检查 collector.is_completed
           |
           +-- false -> review_completion_not_declared
           |
           +-- true -> collector.finding_batch()
```

## 19. 阅读源码时值得特别关注的边界

### 19.1 turn 与 tool call 是两种预算

- `max_agent_turns` 限制模型请求次数；
- `max_tool_calls` 限制全部函数工具调用次数；
- 一次 turn 可以包含 0、1 或多个工具调用。

过程报告中的 LLM call count 和 tool call count 不应被混为一项。

### 19.2 工具执行可能并发

不要根据模型输出顺序假定工具按顺序执行。尤其是修改任务内状态的工具，需要显式设计并发语义。

### 19.3 tool result 会进入模型上下文

工具返回内容属于下一轮 Prompt 的一部分，也应视为不可信数据。CodeLens 工具只返回 Snapshot 证据或受控 acknowledgement，不能借工具结果扩大权限。

### 19.4 final text 只负责结束 SDK

CodeLens 的业务结果通道是 `comment`，不是模型最后一条 assistant message。排查“控制台有问题但报告为空”时，首先检查是否发生了成功的 `comment` 工具调用。

### 19.5 `task_done` 同时控制循环退出和任务终态

自定义工具策略只让被接受的 `task_done` 停止 SDK；拒绝结果进入下一轮模型继续处理。Runtime 返回后仍强制校验完成声明，并把覆盖状态持久化到 checkpoint，供最终聚合选择 `completed` 或 `partial`。排查完成异常时，应同时查看：

- `task_done` 返回的是 `accepted: true` 还是 `accepted: false`；
- `missing_evidence_files` 和 `undeclared_files` 是否得到后续处理；
- `task_done` 后是否又调用工具；
- 是否超过 `max_incomplete_review_retries` 并产生 `review_coverage_incomplete`；
- 是否重复得到相同工具结果；
- 是否到达 `max_agent_turns` 或 `max_tool_calls`。

### 19.6 流结束后才能安全读取完整结果

`run_streamed()` 返回的对象是进行中的可变结果。只有完整消费 `stream_events()` 后，`raw_responses`、usage、final output 和完成状态才稳定。

## 20. 调试一轮 Agent Loop 的推荐顺序

发生 Runtime 问题时，可以按下面顺序阅读 Transcript 和代码：

1. 查看 `prompt`：确认 model、model settings、system instructions、tool schema 和 initial user input；
2. 查看 `model_started` 与第一批 reasoning/output delta；
3. 查看 `tool_call` 的 name、call ID 和 arguments；
4. 查看对应 `tool_result` 是否是成功 JSON、可修正错误或限制错误；
5. 检查是否出现下一次模型 raw response，确认工具结果是否进入下一轮；
6. 查看 `model_completed` 和 `model_output`；
7. 对照 `llm_call_count`、input/output token 和 tool call count；
8. 最后检查 FindingValidator 是否跳过候选，而不是先假定 SDK 丢失输出。

涉及代码时，推荐按以下函数顺序追踪：

```text
OpenAIAgentRuntime._invoke
  -> OpenAIAgentRuntime._run_observable
  -> Runner.run_streamed
  -> AgentRunner.run_streamed
  -> start_streaming
  -> run_single_turn_streamed
  -> process_model_response
  -> execute_tools_and_side_effects
  -> _execute_tool_plan
  -> execute_function_tool_calls
  -> FunctionTool.on_invoke_tool
  -> NextStepRunAgain / NextStepFinalOutput
```

## 21. 一段接近真实行为的伪代码

下面的伪代码保留了 CodeLens 当前实际使用的 SDK分支：

```python
async def codelens_agent_loop(agent, initial_input, limits):
    history = []
    current_turn = 0
    collector = agent.comment_collector

    while True:
        current_turn += 1
        if current_turn > limits.max_agent_turns:
            raise MaxTurnsExceeded

        model_response = await stream_model(
            instructions=agent.instructions,
            input=[initial_input, *history],
            tools=agent.tools,
            model_settings=agent.model_settings,
        )

        parsed = process_model_response(model_response)

        if parsed.function_calls:
            tool_results = await execute_concurrently(
                parsed.function_calls,
                shared_call_budget=limits.max_tool_calls,
                per_call_timeout=limits.tool_timeout,
            )
            history.extend(parsed.output_items)
            history.extend(tool_results)

            if collector.is_completed:
                sdk_final_output = ""
                break
            continue

        sdk_final_output = parsed.last_text or ""
        break

    if not collector.is_completed:
        raise ReviewCompletionNotDeclared

    # 覆盖状态随 Artifact checkpoint 持久化，最终决定 completed 或 partial。
    return output_codec.encode(collector.finding_batch())
```

真实 SDK 还处理 provider retry、tracing、hooks、session、server conversation、handoff、approval、guardrail、hosted tools 和 interruption resume；CodeLens 当前没有启用这些分支，因此理解本节伪代码后再按需深入 SDK 私有实现即可。
