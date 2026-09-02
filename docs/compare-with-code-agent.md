# CodeLens vs 通用 Agent（CodeAgent / Codex / Claude Code）对比分析

## 一、定位差异：专用 CR 工作台 vs 通用编码助手

| 维度 | CodeLens | 通用 Agent（CodeAgent/Codex/Claude Code） |
|---|---|---|
| **核心目标** | 对代码变更进行**多维度专业审查**，输出结构化、可验证的 Finding | **辅助人类完成编码任务**——写代码、改 bug、跑命令、答问题 |
| **输出物** | 带 severity/evidence/location 的结构化审查报告 + 平台评论导出 | 代码修改、终端命令执行结果、自然语言回答 |
| **交互模式** | 一次性提交 → DAG 自动执行 → 结果交付（异步批处理） | 多轮对话、实时交互、人在回路 |
| **用户角色** | Reviewer（审查者）/ 质量门禁 | Developer（开发者）/ 编码者 |

CodeLens 的设计哲学是：**代码审查是一个有明确输入（diff）、明确输出（findings）、明确质量约束（evidence-based）的工程化流程**，不是一个开放式对话。

---

## 二、架构差异：确定性 DAG 编排 vs 自由 Agent 链

### CodeLens：Host 控制的确定性 DAG

```
Planner → [Reviewer_1, Reviewer_2, ...] (并行) → Verifier → Deduplicator
                                          ↘ Remediator (零依赖，与 Reviewer 并行)
```

- **Plan 在执行前冻结**（`ReviewPlan.create()` 拓扑不变量校验，SHA-256 哈希锁定）
- **模型不能修改自己的执行图**——只能通过专用 output tool 提交结果
- **每个角色有独立的完成条件守卫**：
  - Reviewer: `task_done` 拒绝未审查的 diff 文件
  - Verifier: `finalize_verdicts` 校验每个 cluster 被覆盖且仅一次
  - Deduplicator: `deduplicate_done` 校验所有 finding 被判定
  - Remediator: `remediation_done` 校验所有 existing finding 被评估
- **从持久化检查点恢复**——节点完成后写入 artifact，重启跳过已完成节点

### 通用 Agent：LLM 自主决策

- 模型决定调用什么工具、何时停止、输出什么
- 没有 DAG 拓扑约束，没有跨角色的质量守卫
- 适合开放式任务（"帮我实现这个功能"），但不适合需要**可重复、可审计、可恢复**的工程流程

---

## 三、隔离与安全模型：冻结快照 vs 直接操作

| | CodeLens | 通用 Agent |
|---|---|---|
| **代码访问** | 通过 `ReviewSnapshot`（detached worktree 的冻结副本），模型永远看不到 worktree 路径 | 直接读写工作区文件 |
| **工具能力** | 4 个只读 evidence tool（`find_files`/`grep`/`read_file`/`get_diff`）+ 角色专属 output tool | 文件读写、终端执行、网络访问等 |
| **变更能力** | **零变更**——reviewer 不能修改任何代码 | 可直接修改代码、执行命令 |
| **输入信任** | AGENTS.md/REVIEW.md 在 task 创建时冻结，运行时不可篡改 | 指令在对话中动态变化 |

CodeLens 的安全边界设计确保：**审查过程不受 Prompt Injection 影响，审查结果可追溯到冻结的代码版本**。通用 Agent 的信任模型更适合"信任用户指令"的场景。

---

## 四、质量保障流水线：多角色 QA vs 单模型输出

这是 CodeLens 与通用 Agent 最本质的差异。

### 1. 证据链要求（Evidence-Based Findings）

- Reviewer 提交 `comment` 时必须附带：path、side（old/new）、精确代码 excerpt、evidence
- Host 端验证：路径可见性、content hash、hunk 范围、excerpt 一致性
- **不能提供证据的发现会被丢弃**——不是"模型说了就算"

### 2. 三层质量过滤

| 阶段 | 作用 | 机制 |
|---|---|---|
| **Verifier** | 裁决每个 FindingCluster | accept/deny/merge，`finalize_verdicts` 校验覆盖率 |
| **Deduplicator** | 与历史发现去重 | 确定性预过滤（path+line+category）+ LLM 判断 |
| **Remediator** | 检测历史发现是否已修复 | 确定性预过滤（文件未变=unresolved）+ LLM 判断 |

通用 Agent 的输出是模型直接生成的内容，没有后置的质量过滤层。

### 3. 收敛控制（Anti-Convergence）

- 连续相同工具调用检测（fingerprint = tool name + canonical args + raw result）
- No-progress nudge（N 轮无发现/无完成时注入提示）
- Completion nudge（有调查无 `task_done` 时注入）
- All-files-reviewed nudge（所有 diff 文件已审查但未结束时注入）

### 4. Quality Bar（审查质量标准）

系统 prompt 中明确要求：只报告"证据能建立 trigger → changed-code mechanism → concrete harmful outcome 因果链"的缺陷。证据不足时不报告。

---

## 五、上下文工程：精密 token 管理 vs 朴素上下文

| | CodeLens | 通用 Agent |
|---|---|---|
| **Token 计数** | tiktoken cl100k_base，计数**全部输入**（prompt+tools+conversation） | 通常不精确计数 |
| **上下文压缩** | Epoch Checkpoint Compaction：独立模型调用生成 `CheckpointSummary`（investigation_summary + evidence_conclusions + eliminated_hypotheses + open_investigations + next_actions），替换旧 evidence tool 结果为 placeholder | 滑动窗口或截断 |
| **Prompt 缓存** | 压缩不修改 system instructions，保留 prompt cache prefix | 无此优化 |
| **Evidence Replay** | 压缩后模型重新 read 相同参数 → 消耗 replay allowance，重置 no-progress streak | 无此概念 |
| **Prompt 分层** | 5 层：review-policy → repository_instructions → review-workflow → agent_policy → skills，按角色注入 | 单一 system prompt |

---

## 六、专业化 vs 通用化的具体体现

### 1. 8 种专业 Reviewer 维度

| Reviewer | 关注点 |
|---|---|
| correctness:v2 | 逻辑正确性 |
| security:v2 | 安全漏洞 |
| reliability-concurrency:v2 | 可靠性与并发 |
| contract-data:v2 | 契约与数据一致性 |
| architecture:v2 | 架构合理性 |
| performance:v2 | 性能问题 |
| test-regression:v2 | 测试与回归 |
| general:v2 | 全维度浅扫（必须单独运行） |

通用 Agent 是"一个模型做所有事"，CodeLens 是"每个维度有专门 agent，带专门 prompt 和专门工具"。

### 2. 平台集成

- **Plugin 系统**：Trigger（webhook/git hook）→ Review → Report（导出到 CodeHub/GitLab/本地文件）
- **FindingExportEnvelopeV2**：结构化导出包含 plan、coverage、findings with source snippets、remediation summary
- **CodeHub 集成**：line-level diff note posting，HEAD SHA gating，resolved finding 标记

通用 Agent 没有与代码审查平台的原生集成。

### 3. 可观测性

- **Process Report**：LLM 调用数、token 统计（input/output/cached）、工具调用（accepted/rejected/unclassified）、rejection reasons、agent 维度分解
- **Transcript**：完整执行记录（prompt、reasoning、output、tool call/result），支持 stage/reviewer 过滤
- **SSE 实时事件流**：24 种事件类型，支持 `Last-Event-ID` 断线重连
- **Coverage Ledger**：Planned/Completed/Failed/Omitted 文件级追踪

---

## 七、CodeLens 的核心优势总结

1. **可重复性**：冻结输入 + 持久化检查点 → 同一 review 可从任何中断点恢复，结果可复现
2. **可审计性**：每个 finding 有完整证据链（哪个 reviewer → 哪个 evidence tool → 哪段代码 → verifier 裁决 → dedup 结果）
3. **零假完成保证**：每个 DAG 角色有独立的完成条件守卫，不能"假装做完"
4. **质量过滤流水线**：Verifier → Deduplicator → Remediator 三层过滤，减少误报和重复
5. **安全隔离**：冻结快照 + 只读工具 + 零变更能力 → 审查过程不受注入攻击影响
6. **专业分工**：8 种维度 Reviewer 各有专精 prompt 和工具，比通用模型"一锅烩"更深入
7. **平台原生集成**：webhook 触发 → 审查 → 评论导出，形成闭环
8. **精密上下文管理**：token 精确计数 + epoch compaction + prompt cache 保留 → 大规模 diff 不会爆上下文

---

## 八、适用场景对比

| 场景 | 推荐工具 | 原因 |
|---|---|---|
| MR/PR 代码审查 | **CodeLens** | 多维度并行审查、证据链、平台集成 |
| 大规模变更质量门禁 | **CodeLens** | DAG 编排、收敛控制、coverage 追踪 |
| 历史发现追踪（是否已修复） | **CodeLens** | Remediator 闭环 |
| 写新功能/修 bug | 通用 Agent | 需要文件修改和命令执行 |
| 代码解释/问答 | 通用 Agent | 需要交互式对话 |
| 重构/迁移 | 通用 Agent | 需要实际修改代码 |

---

## 九、总结

CodeLens 是把代码审查当作**工程化流水线**来设计的——有输入冻结、有 DAG 编排、有多角色 QA、有证据验证、有平台集成。通用 Agent 是把编码当作**对话**来设计的——灵活、交互、但缺乏审查场景所需的质量保证体系。两者不是替代关系，而是**互补关系**：通用 Agent 帮你写代码，CodeLens 帮你审查代码。
