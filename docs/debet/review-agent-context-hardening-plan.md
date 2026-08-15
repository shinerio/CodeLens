# Review Agent 上下文工程加固计划

- 状态：待评审
- 来源：`review_629295310c0c4006b02e307056fa8efb` 深度回放分析
- 范围：Review Agent 的 checkpoint、证据协议、覆盖门禁、可观测性与失败降级
- 明确排除：本计划不包含 replay 评测集建设与评测流程改造

## 0. 新会话执行引导

如果在新会话中继续执行本计划，请先向执行者提供以下信息：

```text
请阅读 /Users/shinerio/Workspace/code/CodeLens/docs/superpower/plans/review-agent-context-hardening-plan.md，
按该计划实施。先和我确认第 8 节待确认决策点，再从 Phase 0/1 开始。
不要建设 replay 评测集。
```

执行环境与状态：

- 仓库：`/Users/shinerio/Workspace/code/CodeLens`
- 当前分支：`dev-graph-engineering`
- 本计划文档路径：`review-agent-context-hardening-plan.md`
- 明确排除：replay 评测集建设与评测流程改造
- 开发前应重新检查 `git status --short`，确认是否存在用户新增或无关未提交改动
- 第一步：运行 `../../backend/tests/unit/review/test_context_compaction.py` 确认 Phase 0 基线
- 开发顺序：待用户确认第 8 节决策点后，从 Phase 1 开始逐 Phase 独立提交
- 每个 Phase 完成后运行相关单测；全部完成后运行后端相关测试、ruff、mypy 门禁

本文档自身已经包含：

- 问题来源与关键结论
- 目标与非目标
- 分阶段技术方案
- 涉及文件
- 验收标准
- 测试矩阵
- 风险与灰度顺序
- 待确认决策点

因此新会话不需要依赖原始 review transcript 才能开工；如需追溯原始回放证据，再打开：

```text
data/artifacts/transcripts/review_629295310c0c4006b02e307056fa8efb.json
```

---

## 1. 背景与结论

该 Review 运行整体质量较高：最终 25/25 文件覆盖，Finding 证据链完整，宿主 `task_done` coverage gate 两次成功阻止过早完成。但回放也暴露出四类系统性风险：

1. **关键状态过度依赖模型自由文本**  
   两次 checkpoint 的 `evidence_conclusions`、`eliminated_hypotheses`、`open_investigations`、`next_actions` 全为空，调查状态主要藏在 `investigation_summary` 中。

2. **Evidence ID 绑定协议不够强**  
   checkpoint 中出现 `evidence_fc396e11` 这类截断引用。当前宿主签发格式是 `evidence_` + 24 位小写十六进制字符，但模型输出侧缺少完整的创建、校验、观测闭环。

3. **checkpoint 禁用后的硬水位策略过于脆断**  
   provider 能力拒绝或连续 checkpoint 失败会禁用 checkpoint；上下文继续增长到 hard watermark 后直接抛 `ContextCheckpointError`，runtime 将整次 Review 归为不可重试永久失败，缺少确定性降级路径。

4. **coverage 仍是 path-level**  
   当前只能证明“看过该文件”，不能证明“看过所有 changed hunk / required range”。partial read 或局部 read 后仍可能通过 path-level gate。

现有架构方向应保留：

```text
Immutable Prefix
+ Single Epoch Checkpoint
+ Active Tail
+ Full Audit Transcript
```

改进原则是：**宿主权威状态和机器可验证证据绑定承担上下文管理，模型专注判断，不复述状态。**

---

## 2. 目标与非目标

### 目标

1. Evidence ID 只允许宿主签发格式，并能识别 malformed / truncated 引用。
2. checkpoint 输出从自由文本升级为严格结构化语义状态。
3. reviewed / missing / comments / tool budget 等权威状态由宿主注入，不依赖模型摘要。
4. checkpoint summarizer 有独立 timeout / cancellation 语义。
5. checkpoint 被禁用后仍可通过确定性降级压缩继续运行，避免可恢复抖动升级为整次失败。
6. coverage 从 path-level 逐步升级到 required range / hunk-level。
7. transcript 能还原 checkpoint 真实发生时间与降级原因。
8. 降低重复读取核心文件造成的上下文和成本浪费。

### 非目标

1. 不做多 checkpoint 堆叠摘要，保持 single epoch checkpoint。
2. 不把 repository 全量规则注入所有 Review。
3. 不引入通用文件系统逃逸能力，所有读取仍限定在 Snapshot。
4. 不在本计划中建设 replay 评测集。
5. 不承诺 token-level 精确压缩；第一阶段仍以字节水位为主，token/model-aware 水位作为后续增强。

---

## 3. 分阶段落地计划

## Phase 0：确认基线与保护现有修复

### 已具备的基线

当前代码中应保留并验证：

- Evidence ID 匹配从宽松的 `evidence_[A-Za-z0-9_-]+` 收紧为：

```python
r"\bevidence_[a-f0-9]{24}\b"
```

- `ContextCheckpointTracker.reset_context()` 清空 checkpoint 计数、压缩字节数与 payload。

### 工作项

| 项目 | 内容 |
|---|---|
| 基线测试 | 运行 `../../backend/tests/unit/review/test_context_compaction.py` |
| 行为确认 | malformed ID 必须失败，reset 后不得泄漏上一次 attempt 状态 |
| 变更纪律 | 后续每个 Phase 独立提交，避免一个提交混杂协议、策略和观测 |

### 验收标准

- 相关单测通过。
- 不引入行为回退。

---

## Phase 1（P0）：Evidence ID 协议加固

### 1.1 宿主签发协议

保持唯一合法格式：

```text
evidence_[24位小写hex]
```

完整正则：

```python
_EVIDENCE_ID_PATTERN = re.compile(r"evidence_[a-f0-9]{24}")
```

校验必须使用 `fullmatch`，避免前缀匹配绕过。

### 1.2 结构化字段强校验

`CheckpointEvidenceConclusion.evidence_ids` 与 `CheckpointEliminatedHypothesis.evidence_ids` 增加 Pydantic validator：

- 每个元素必须完整匹配 24 位 hex；
- 禁止重复 ID；
- 禁止空字符串、前缀、截断值、大小写混合值；
- 所有 ID 必须存在于当前宿主 `evidence_index`。

### 1.3 自由文本中的 malformed ID 检测

`investigation_summary` 虽然是文本，但不能允许其中出现明显伪造或截断的 evidence token。

建议逻辑：

1. 用宽泛 token 正则扫描文本：
   ```python
   evidence_[A-Za-z0-9_-]+
   ```
2. 先剔除宿主已签发的完整 ID；
3. 剩余 token 视为 malformed / truncated；
4. checkpoint 校验失败并计入指标；
5. 不尝试自动纠错或模糊匹配，避免错误绑定证据。

### 1.4 指标与日志

`ContextCheckpointTracker` 增加：

```text
invalid_evidence_reference_count
```

日志字段：

```text
error_type=invalid_evidence_reference
known_evidence_count
malformed_evidence_id_count
```

后续可在 agent run 汇总中输出，便于确认 prompt + schema 修复是否有效。

### 1.5 Prompt 强化

`prompts/sys/{zh-CN,en}/checkpoint-compaction.md` 明确要求：

- evidence ID 是宿主签发的不透明标识；
- 只能从 `host_evidence_index` 复制；
- 必须是 `evidence_` + 24 位小写 hex；
- 不得缩短、改写、推断或拼接过长 ID；
- 给出正例与反例：
  ```text
  正确：evidence_0123456789abcdef01234567
  错误：evidence_fc396e11
  错误：EVIDENCE_0123456789ABCDEF01234567
  ```

### 1.6 暂不做短别名

`E1` / `E2` 短别名虽能降低模型抄写错误，但需要额外的双向映射与审计解释，第一轮不引入。若 malformed 指标仍高，再单独评估。

### 涉及文件

```text
backend/src/codelens/review/infrastructure/context_checkpoint.py
backend/tests/unit/review/test_context_compaction.py
prompts/sys/zh-CN/checkpoint-compaction.md
prompts/sys/en/checkpoint-compaction.md
```

### 验收标准

- malformed / truncated / unknown / duplicated ID 均拒绝。
- 合法 ID 正常通过。
- 失败可观测。
- 中英文 prompt 均包含完整协议与反例。

---

## Phase 2（P0）：结构化 checkpoint 输出

### 2.1 输出格式

checkpoint summarizer 输出必须是严格 JSON，而不是 Markdown 自由文本。

目标 JSON：

```json
{
  "schema_version": "codelens_review_checkpoint_v1",
  "investigation_summary": "...",
  "evidence_conclusions": [
    {
      "evidence_ids": ["evidence_..."],
      "conclusion": "..."
    }
  ],
  "eliminated_hypotheses": [
    {
      "evidence_ids": ["evidence_..."],
      "hypothesis": "...",
      "reason": "..."
    }
  ],
  "open_investigations": ["..."],
  "next_actions": ["..."]
}
```

允许 Markdown fence 包裹，但内容必须是一个合法 JSON object。

### 2.2 解析策略

将 `checkpoint_summary_from_text` 改为：

1. strip；
2. 识别并剥离 Markdown fence；
3. `json.loads`；
4. 确认顶层是 object；
5. `CheckpointSummary.model_validate`；
6. 交给 evidence ID 校验。

任何一步失败都视为 checkpoint failure，不做宽松修复。

### 2.3 是否使用 SDK structured output

首选方案：继续使用 text output，但要求严格 JSON 并由宿主解析。

理由：

- 避免依赖 provider 特定 structured output 能力；
- provider 不支持时不影响 text 通道；
- 宿主仍拥有严格 schema 校验。

可选增强：如果 provider adapter 已确认支持 structured output，可通过配置启用 `output_type=CheckpointSummary`，但默认不走该路径。

### 2.4 结构化字段最低要求

不要求每类字段都必须非空，因为合法调查状态可能确实没有已排除假设或结论。但需要满足：

- `open_investigations` 与 `next_actions` 不能同时为空，除非任务已完成；
- `evidence_conclusions` 中的每条结论必须绑定证据；
- 上一个 checkpoint 中仍有效的 structured state 必须携带；
- prompt 明确禁止把所有状态塞进 `investigation_summary`。

### 2.5 Prompt 修改

中英文 prompt 增加：

- 输出必须是一个 JSON object；
- 字段定义与空值策略；
- `investigation_summary` 只作为高层概述，不承载全部状态；
- 结论、排除假设、未决问题、下一步必须进入对应字段；
- 上一 checkpoint 的有效结构化状态必须保留。

### 涉及文件

```text
backend/src/codelens/review/infrastructure/context_checkpoint.py
backend/src/codelens/review/infrastructure/openai_runtime.py
backend/tests/unit/review/test_context_compaction.py
backend/tests/contract/review/test_openai_runtime.py
prompts/sys/zh-CN/checkpoint-compaction.md
prompts/sys/en/checkpoint-compaction.md
```

### 验收标准

- 自由文本输出被拒绝。
- fence + 合法 JSON 通过。
- schema extra field 被拒绝。
- evidence binding 校验生效。
- provider 能力拒绝会进入统一降级策略，而不是立即造成整次 Review 永久失败。

---

## Phase 3（P0）：宿主权威状态注入 checkpoint

### 3.1 新增 host state provider

在 runtime 构造 checkpoint filter 时传入回调：

```python
host_state_provider: Callable[[], Mapping[str, object]]
```

每次真正发生 checkpoint 时调用，避免读取过期状态。

### 3.2 第一版 host state 内容

```json
{
  "coverage": {
    "total_review_file_count": 25,
    "reviewed_file_count": 13,
    "missing_review_files": [
      "backend/tests/contract/review/test_openai_runtime.py"
    ],
    "uncovered_review_ranges": []
  },
  "comments": {
    "active_comment_count": 1,
    "retracted_comment_count": 0
  },
  "completion": {
    "is_completed": false,
    "incomplete_retry_count": 0,
    "max_incomplete_review_retries": 2
  },
  "tool_budget": {
    "remaining_tool_calls": 20,
    "max_tool_calls": 80
  }
}
```

第一阶段先落 coverage、comments、completion；tool budget 若现有 limiter 未暴露，可单独改造。

### 3.3 状态来源

- coverage 来自 `FilesystemReviewTools`；
- comments / completion 来自 `ReviewCommentCollector`；
- tool budget 来自 `ToolExecutionLimiter`；
- 通过 `RuntimeToolContext` 暴露只读快照，不把内部可变对象直接交给 checkpoint。

### 3.4 注入位置

同一个 host state 同时注入：

1. `CheckpointSummaryRequest.model_input()`：供 summarizer 使用；
2. checkpoint envelope 的 `host_state`：供主 Agent 后续继续使用。

注意：host state 是宿主权威数据，但必须保持 JSON-safe、有界、脱敏，不包含 snapshot hash、instruction chain、内部路径或敏感配置。

### 3.5 checkpoint envelope 结构

```json
{
  "schema_version": "codelens_review_checkpoint_v1",
  "host_state": {
    "coverage": {},
    "comments": {},
    "completion": {},
    "evidence_index": []
  },
  "semantic_summary": {},
  "degraded_reason": null
}
```

### 3.6 与模型状态的冲突处理

结构化语义状态可以描述判断，但不能覆盖宿主权威事实：

- coverage 以 host state 为准；
- active comment count 以 host state 为准；
- tool budget 以 host state 为准；
- summarizer 不需要复述这些值；
- 若模型输出与宿主状态冲突，保留宿主状态并允许语义摘要描述不确定性。

### 涉及文件

```text
backend/src/codelens/review/infrastructure/context_checkpoint.py
backend/src/codelens/review/infrastructure/capability_tools.py
backend/src/codelens/review/infrastructure/snapshot_tools.py
backend/src/codelens/review/infrastructure/comment_collector.py
backend/src/codelens/review/infrastructure/openai_runtime.py
backend/tests/unit/review/test_capability_tools.py
backend/tests/unit/review/test_context_compaction.py
backend/tests/contract/review/test_openai_runtime.py
```

### 验收标准

- checkpoint 后模型能直接看到 missing files 与权威覆盖状态；
- host state 不依赖模型自由文本；
- state 序列化有界且稳定；
- 主 Agent 上下文中 host state 与 evidence index 可用。

---

## Phase 4（P0）：checkpoint 独立 timeout / cancellation

### 4.1 配置

为 checkpoint summarizer 增加独立 deadline：

```text
checkpoint_timeout_seconds
```

建议默认：

```text
120 seconds
```

可复用 provider execution limit 的配置边界：

```text
minimum: 1
maximum: 300
```

不与普通工具 timeout 混用，因为 checkpoint 是一次模型调用，不是 Snapshot 工具调用。

### 4.2 执行语义

在 filter 中包裹：

```python
async with asyncio.timeout(checkpoint_timeout_seconds):
    result = await summarizer.summarize(...)
```

要求：

- timeout 后 cancellation 传播到 SDK Runner；
- 不吞掉 `CancelledError`；
- 只把 `TimeoutError` 归类为 checkpoint failure；
- timeout 计入 failure count；
- 连续 3 次 timeout 打开 circuit。

### 4.3 Runtime 注入

`OpenAIAgentRuntime` 读取 provider / execution 配置并传给 `build_context_checkpoint_filter`。

### 4.4 测试

- 快速成功：不受影响；
- 超时：异常类型正确、指标正确；
- 连续三次 timeout：circuit open；
- 外部任务取消：不伪装成 checkpoint timeout。

### 涉及文件

```text
backend/src/codelens/reviewer_catalog/domain/provider_config.py
backend/src/codelens/reviewer_catalog/infrastructure/file_provider_config.py
backend/src/codelens/review/infrastructure/openai_runtime.py
backend/src/codelens/review/infrastructure/context_checkpoint.py
backend/tests/contract/review/test_openai_runtime.py
backend/tests/unit/review/test_context_compaction.py
```

### 验收标准

- checkpoint 卡死不会消耗整个 Agent timeout；
- timeout 可观测；
- cancellation 语义不被破坏。

---

## Phase 5（P0）：hard watermark 安全降级

### 5.1 核心策略

checkpoint summarizer 失败不应该直接让整次 Review 在 hard watermark 永久失败。

当 semantic checkpoint 不可用时，允许宿主执行 deterministic compaction：

1. 保留 immutable prefix；
2. 保留完整 `host_state`；
3. 保留 `evidence_index`，包括 tool name 与 canonical re-read arguments；
4. 丢弃旧工具结果正文；
5. 使用上一个合法 checkpoint 的 semantic summary，或使用保守 fallback summary；
6. 标记 `degraded_reason`。

### 5.2 fallback summary

如果没有旧 semantic checkpoint，使用保守摘要：

```text
Host deterministic fallback:
- old evidence result bodies were omitted;
- precise semantic conclusions were not inferred;
- consult host_state.coverage;
- use host_evidence_index re-read arguments when needed.
```

必须明确：

- fallback 不推断缺陷；
- fallback 不声称语义无损；
- 后续需要精确内容时通过 evidence replay / 原工具参数重读。

### 5.3 保留旧 checkpoint

如果此前已有合法 semantic checkpoint：

- 优先携带旧 `CheckpointSummary`；
- 新增 evidence 进入 host evidence index；
- `degraded_reason` 说明本轮未能生成新语义摘要；
- 不把旧结论绑定到新 evidence。

### 5.4 何时仍抛 hard watermark 异常

只有在结构性无法安全压缩时才失败，例如：

- 到达 hard watermark 但没有任何 complete round 可压缩；
- keep-recent 设置导致没有可选择结果；
- checkpoint envelope 自身序列化异常；
- immutable prefix 或 covered index 状态不一致。

这类失败表示宿主状态机错误，而不是模型 summarizer 抖动。

### 5.5 失败分类

`ContextCheckpointError` 保留为不可重试的内部安全失败；但以下情况不再到达该路径：

- provider structured/text checkpoint 能力拒绝；
- checkpoint JSON 无效；
- checkpoint timeout；
- 连续 3 次 checkpoint failure。

这些进入 deterministic fallback，并记录 degraded checkpoint。

### 5.6 观测字段

`ContextCheckpointTracker` 增加：

```text
degraded_checkpoint_count
last_degraded_reason
```

Agent run 输出与 transcript 增加：

```text
context_degraded_checkpoint_count
context_last_degraded_reason
```

### 涉及文件

```text
backend/src/codelens/review/infrastructure/context_checkpoint.py
backend/src/codelens/review/infrastructure/openai_runtime.py
backend/src/codelens/review/domain/ports.py
backend/src/codelens/review/application/orchestrator.py
backend/src/codelens/review/application/process_report.py
backend/tests/unit/review/test_context_compaction.py
backend/tests/contract/review/test_openai_runtime.py
```

### 验收标准

- provider 400 / 404 / 422 拒绝后，后续 watermark 触发 deterministic compaction，而不是永久失败；
- 连续 3 次失败后同样可降级；
- 已有 semantic checkpoint 时保留旧语义；
- fallback 明确标记 degraded；
- 结构性不可压缩仍然 fail closed。

---

## Phase 6（P0）：checkpoint 真实事件写入 transcript

### 6.1 事件类型

新增 runtime event：

```text
context_checkpoint
```

事件 payload 使用完整 checkpoint envelope 或其摘要。为避免 transcript 膨胀，建议记录：

- checkpoint count；
- compacted result count；
- original bytes；
- compressed bytes；
- evidence count；
- degraded / degraded reason；
- failure count。

是否保存完整 envelope 需单独权衡；完整 envelope 已进入模型上下文，但 transcript 也要保证审计可还原。

### 6.2 事件时间

事件必须在 checkpoint 提交成功后立即写入 sink，确保 transcript 时间线反映真实发生点，而不是 agent 完成后补记。

### 6.3 指标汇总

`process_report` 聚合：

```text
context_checkpoint_count
context_degraded_checkpoint_count
context_checkpoint_failure_count
context_invalid_evidence_reference_count
```

### 验收标准

- 可以从 transcript 判断每次 checkpoint 的真实顺序；
- 可以区分正常与 degraded checkpoint；
- 可以聚合 malformed evidence reference 指标。

---

## Phase 7（P1）：range / hunk-level coverage gate

### 7.1 必需覆盖单元

以 Snapshot 的 immutable changed range 为准：

```text
(path, side, start_line, end_line)
```

不直接暴露 hunk ID 给模型，避免泄露宿主内部标识。

### 7.2 覆盖判定

#### `get_diff`

解析返回的 unified diff hunk header，将已返回的完整 hunk 映射到 required range：

- hunk 完整返回：标记对应 range covered；
- oversized hunk：不标记 covered；
- partial page：已完整返回的 hunk 可标记，剩余不标记。

#### `read_file`

满足以下任一条件才标记 range covered：

1. 读取范围完整包含 required range；
2. 文件完整读取成功；
3. 请求范围与 required range 精确匹配。

仅请求上下文中几行、读取 partial、或读取无关行，不应覆盖该 range。

#### 保守策略

如果无法可靠映射，则不标记 covered，不得为了提高覆盖率而放宽。

### 7.3 `task_done` 输出

在现有 missing files 之外增加：

```json
{
  "missing_review_ranges": [
    {
      "path": "backend/src/...",
      "side": "new",
      "start_line": 10,
      "end_line": 40
    }
  ]
}
```

第一阶段可以继续以 path-level gate 拒绝完成，但响应中先返回 missing ranges，让模型适应新协议。

### 7.4 两阶段启用

建议分两步，避免一次性上线造成大量任务无法完成：

1. **observability mode**：返回 missing ranges 并记录指标，但 completion 仍按 path-level；
2. **enforcement mode**：所有 required ranges 覆盖后才允许 `task_done`。

对空 diff / 无 hunk 文件单独定义：文件可见即可覆盖。

### 7.5 checkpoint 注入

host state 中增加：

```json
"uncovered_review_ranges": []
```

让 checkpoint 后模型能继续剩余范围，而不依赖记忆。

### 涉及文件

```text
backend/src/codelens/review/infrastructure/snapshot_tools.py
backend/src/codelens/review/infrastructure/comment_collector.py
backend/src/codelens/review/infrastructure/capability_tools.py
backend/src/codelens/review/infrastructure/openai_runtime.py
backend/tests/unit/review/test_snapshot_tools.py
backend/tests/unit/review/test_comment_collector_v2.py
```

### 验收标准

- partial read 不再隐性证明全文件覆盖；
- get_diff oversized hunk 不标记覆盖；
- missing ranges 明确返回给模型；
- enforcement mode 下所有 required ranges 覆盖才可完成。

---

## Phase 8（P1）：剩余工作计划与 evidence replay

### 8.1 剩余工作计划

checkpoint host state 中提供：

```json
"remaining_work": {
  "missing_review_files": [],
  "uncovered_review_ranges": [],
  "suggested_next_actions": []
}
```

`suggested_next_actions` 应由宿主基于覆盖状态生成确定性动作，而不是让 summarizer 复述。

### 8.2 精确重读 allowance

现有 `CompactedEvidenceReplayRegistry` 已有设计，但生产路径尚未完整接线。

目标：

- 新 evidence 被 compact 后注册 exact replay allowance；
- 模型用完全相同参数重读时允许一次；
- 重读成功不触发 identical tool loop；
- 重读失败或参数不同则正常消耗预算与 loop 检测；
- 注册与消费计数写入 agent run。

### 8.3 不新增通用 read_evidence 工具

第一阶段只做 exact replay allowance，不暴露新的 `read_evidence(evidence_id)` 工具。理由：

- 避免新增证据索引到文件系统的第二套路径解析；
- 避免模型通过 ID 读取任意历史结果；
- exact replay 保留原工具边界与审计参数。

### 涉及文件

```text
backend/src/codelens/review/infrastructure/context_checkpoint.py
backend/src/codelens/review/infrastructure/evidence_replay.py
backend/src/codelens/review/infrastructure/capability_tools.py
backend/src/codelens/review/infrastructure/tool_contract.py
backend/src/codelens/review/infrastructure/openai_runtime.py
backend/tests/unit/review/test_tool_contract.py
backend/tests/unit/review/test_context_compaction.py
```

### 验收标准

- compact 后必要重读不被 loop breaker 误杀；
- 非精确重读不享受 allowance；
- replay 注册与消费可观测。

---

## Phase 9（P1）：批量工具输出与模型感知水位

### 9.1 批量输出预算

当前 parallel tool calls 的单个结果均有上限，但一批结果叠加可能快速推高上下文。

增加 run-level 约束：

```text
max_parallel_tool_output_bytes
max_tool_result_batch_bytes
```

策略：

- 每个结果仍保留现有 byte limit；
- 同一批返回超过总预算时，将部分成功结果降级为 needs_action；
- 明确提示未执行或被截断的调用及恢复方式；
- 不丢弃已执行结果的审计记录。

### 9.2 模型感知 watermark

第一阶段继续使用 byte watermark，但增加观测：

```text
estimated_active_context_bytes
last_input_tokens
model_context_window_tokens
watermark_kind=bytes|tokens
```

后续根据 provider 配置切换：

```text
hard watermark = min(model_context_limit - safety_margin, byte_hard_watermark)
```

不做自研通用 tokenizer；优先使用 provider-reported input tokens 或模型配置中的 conservative context limit。

### 验收标准

- parallel batch 不会一次性突破压缩触发点太远；
- watermark 决策可解释；
- token 水位不会依赖不可靠的本地 tokenizer。

---

## Phase 10（P2）：上下文与模型策略优化

### 10.1 Repository instructions 注入范围

保持现有 Snapshot containment，但继续收敛：

- 只注入对 target path 生效的规则；
- 对多文件 Review 按文件类型或目录分组；
- prompt cache 稳定的前提下减少无关规则重复。

### 10.2 文件类型路由

根据扩展名与 Review scope 选择策略：

- Python / TypeScript / Go 核心逻辑；
- Markdown / prompt / config 文档；
- 测试文件；
- 自动生成文件标记。

第一阶段只做策略提示与指标，不引入复杂路由系统。

### 10.3 模型分层

评估：

- core reviewer 使用强模型；
- docs / config reviewer 使用轻量模型；
- checkpoint summarizer 使用中低成本高指令跟随模型；
- verifier 独立配置。

前提是 provider capability fingerprint 与 prompt hash 体系能区分模型配置，避免缓存和审计混淆。

### 10.4 cache 可观测性

保留并增强：

```text
cached_input_tokens
cache_write_input_tokens
checkpoint_input_tokens
active_context_estimate
immutable_prefix_bytes
checkpoint_payload_bytes
```

用于验证 checkpoint 是否破坏 cache prefix。

---

## 4. 建议实施顺序与提交切分

建议每个 Phase 一个独立 PR 或 commit：

1. `phase-0-baseline`
2. `phase-1-evidence-id-protocol`
3. `phase-2-structured-checkpoint`
4. `phase-3-host-authoritative-state`
5. `phase-4-checkpoint-timeout`
6. `phase-5-deterministic-watermark-fallback`
7. `phase-6-checkpoint-transcript-events`
8. `phase-7-range-coverage`
9. `phase-8-remaining-work-and-replay`
10. `phase-9-batch-and-model-aware-watermark`
11. `phase-10-context-and-model-routing`

依赖关系：

```text
Phase 1 → Phase 2 → Phase 3 → Phase 5
Phase 4 可与 Phase 2/3 并行，但必须在 Phase 5 前合入
Phase 6 依赖 Phase 5 的 degraded 指标
Phase 7 依赖 Phase 3 的 host state 结构
Phase 8 依赖 Phase 7 与 evidence index
Phase 9 依赖 Phase 5
Phase 10 独立，可最后评估
```

---

## 5. 核心测试矩阵

### Evidence 协议

- 合法 24 位 hex ID；
- unknown ID；
- truncated ID；
- 大小写错误；
- duplicated ID；
- malformed ID 出现在 structured 字段；
- malformed ID 出现在 `investigation_summary`。

### Checkpoint schema

- fence + JSON；
- 裸 JSON；
- Markdown 自由文本；
- extra field；
- 缺少 required field；
- 空字符串；
- `schema_version` 错误；
- structured state 全空。

### Host state

- 13/25 覆盖；
- 24/25 覆盖；
- 25/25 覆盖；
- active comments / retracted comments；
- completion retry 状态；
- host state JSON 序列化稳定性。

### Timeout / fallback

- checkpoint 快速成功；
- checkpoint timeout；
- 连续三次失败；
- provider 400 / 404 / 422；
- disabled 后再次超过 trigger；
- disabled 后到达 hard watermark；
- 有旧 semantic checkpoint；
- 无旧 semantic checkpoint；
- 无 complete round 可压缩。

### Coverage

- full file read；
- partial read；
- exact range read；
- get_diff full hunk；
- get_diff partial page；
- oversized hunk；
- deleted file old side；
- added file new side；
- renamed old/new path；
- path covered 但 range 未覆盖。

### Transcript / report

- checkpoint 事件顺序；
- degraded 原因；
- malformed evidence 指标；
- compaction usage；
- replay registered / consumed。

---

## 6. 主要风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 严格 JSON 降低 checkpoint 成功率 | 增加失败与降级次数 | prompt 给出精确 schema；降级路径兜底；跟踪失败率 |
| deterministic fallback 丢语义 | 后续判断需要重读证据 | 保留 evidence index 和 exact replay allowance；明确 degraded |
| hunk-level gate 过严 | 任务难以完成 | 先 observability mode，再 enforcement；返回具体 missing ranges |
| host state 过大 | 抵消压缩收益 | 字段白名单 + 数量与字节数上限 |
| replay allowance 被滥用 | 浪费工具预算 | 只允许一次完全相同参数；计入预算与审计 |
| provider 差异 | checkpoint 能力不稳定 | 保持 provider-neutral text JSON；能力拒绝进入 fallback |
| token watermark 不准 | 过早或过晚压缩 | 先观测，不直接强制启用；使用 provider-reported usage 校准 |

---

## 7. 上线与观察指标

建议灰度顺序：

1. 只启用 Evidence ID 严格校验与结构化 JSON，观察 checkpoint failure；
2. 启用 host state 与 transcript 事件，不改变失败策略；
3. 启用 deterministic fallback，确认 hard watermark 永久失败下降；
4. 启用 range coverage observability；
5. 启用 range coverage enforcement；
6. 启用 replay allowance；
7. 再评估 model-aware watermark。

核心观察指标：

```text
checkpoint_success_rate
checkpoint_failure_count
checkpoint_timeout_count
provider_compaction_unsupported_count
degraded_checkpoint_count
hard_watermark_failure_count
invalid_evidence_reference_count
average_active_context_bytes
checkpoint_compression_ratio
exact_replay_registered_count
exact_replay_consumed_count
task_done_rejection_count
missing_review_file_count
missing_review_range_count
review_completion_status
```

成功标准：

- malformed evidence ID 接近 0；
- checkpoint structured fields 不再长期为空；
- hard watermark 永久失败显著下降；
- degraded checkpoint 后仍能完成必要覆盖；
- hunk-level enforcement 不显著增加超时或工具预算耗尽。

---

## 8. 待确认决策点

请在评审时重点确认：

1. **deterministic fallback 是否可接受**  
   它会保留证据索引和重读参数，但可能丢失未被旧 checkpoint 覆盖的新语义细节。

2. **checkpoint 输出格式**  
   默认严格 JSON text；是否要在确认支持的 provider 上额外启用 SDK structured output？

3. **range coverage 启用方式**  
   建议先 observability，再 enforcement。是否接受两阶段上线？

4. **checkpoint timeout 默认值**  
   建议 120 秒，是否合适？

5. **host state 是否包含 tool budget**  
   需要暴露 limiter 内部快照；有助于模型规划，但可能诱导模型围绕预算行动。

6. **transcript 是否保存完整 checkpoint envelope**  
   完整 envelope 更可审计，但会增加 artifact 体积；也可以只保存摘要并保留模型上下文版本。
