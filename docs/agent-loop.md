# Reviewer Agent Loop v2

Reviewer 的模型可见循环只有三类动作：读取冻结证据、维护 Candidate Comment、请求完成。

```text
receive review_files
  -> find_files / grep / read_file / get_diff
  -> optional comment(...)
  -> later evidence contradicts comment?
       `- retract_comment(candidate_ids, reason)
  -> task_done(summary)
       |- needs_action + missing_review_files: continue
       `- success: stop loop
```

## Tool Result 与恢复

每个模型可见工具始终返回单个 Tool Result v2 JSON Object。宿主只按 `status` 分类执行结果；模型按稳定 Diagnostic code 和完整 `suggested_arguments` 恢复，不从本地化 message 猜测状态。未知参数、参数校验错误、工具可恢复异常和内部非法返回也必须使用相同 envelope。

`partial` 表示已经获得有效但不完整的证据；应使用返回的下一范围、cursor 或收窄建议继续。`needs_action` 表示当前动作尚未形成可接受完成。`rejected` 与 `failed` 不能作为证据。

## 完成与 Comment 生命周期

- 宿主以 `ReviewFileScope.review_paths` 为覆盖全集。
- `read_file` 只有完整可用行计入对应 Review 文件证据；`get_diff` 只有完整返回一个文件的 metadata 和全部 hunks 才计入该文件覆盖。
- `task_done` 不接收文件计数或已完成文件列表；缺失时返回稳定排序的全部 `missing_review_files`。
- 超过冻结的不完整重试上限后，`task_done` 以 `forced_completion=true` 成功并保留缺失路径，使 Review 进入 partial。
- 只有 status 为 `success` 的 `task_done` 结束 SDK loop；comment 与 retract 永不终止。
- `comment` 接受项返回宿主生成的 `candidate_id`。`retract_comment` 只撤销当前 Reviewer 当前 Run 的 Candidate，幂等并保留审计；最终 Candidate batch 只包含 active 项。
- 自然语言 summary 不具撤销语义；成功完成后任何 Comment 或撤销调用返回 `reviewer_already_completed`。

## 连续重复熔断

无进展熔断只计算连续相同的工具名、canonical arguments 与原始 Tool Result。第二次连续重复仍返回原 status，并在 diagnostics 追加 `repeated_identical_call`；达到阈值才中止 Run。A→B→A 的第三次 A 重新从 1 计数。告警始终通过 Tool Result serializer 附加，不能在 JSON 后拼接自然语言。

## Context compaction replay

旧证据被 context compaction 替换时，占位是 `needs_action` Tool Result，marker 为 `codelens_context_compaction_v2`，并包含原工具、完整 arguments 与 `evidence_compacted` 建议。占位不构成证据。模型用完全相同参数重读时消费一次 replay allowance：调用仍消耗总工具预算、timeout 和真实 I/O，但不增加 no-progress streak，并重置此前连续 streak。参数变化、非证据工具或已经消费的 allowance 不豁免。

运行时不提供文件写入、Shell、网络、任意 Git、动态工具发现或文件完成声明工具。
