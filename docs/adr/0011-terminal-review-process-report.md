# ADR 0011: Terminal Review Process Report

## Status

Accepted

## Context

Review 改进需要可比较的过程指标，例如真实 LLM 请求次数、输入和输出 token、每种内置工具的调用与结果次数、Agent 明细和执行时长。前端从可见事件临时计数会受到重连、折叠筛选和终态转录落盘竞态影响；直接保存供应商原始响应又会扩大敏感数据和供应商类型的暴露范围。

## Decision

OpenAI Runtime Adapter 只把供应商公开 usage 转换为内部 `UnvalidatedAgentOutput`，并为工具事件提取稳定的 `tool_name` 与 `tool_call_id`。Review Orchestrator 将模型名、provider response 数、输入 token、输出 token 和总 token 作为字符串 metadata 写入每个 Agent 的最终 `model_output` 转录条目。供应商对象、请求正文和原始响应正文不进入报告契约。

Application 层从任务的完整、已脱敏终态转录确定性聚合 `ReviewProcessReport`。工具结果通过 `tool_call_id` 与调用匹配，旧转录缺少 ID 时只按同一 Agent 的事件顺序兼容匹配。`GET /api/reviews/{task_id}/process-report` 对活动任务返回 `409 process_report_not_ready`，终态返回 LLM/token 总量、工具和 Agent 明细、时长、Finding 数以及 `usage_is_complete`。任何缺失的 usage 保持为零并将完整性标记设为 false，不用估算值冒充供应商计量。

前端仅通过该 HTTP/JSON 契约展示报告，不从 SSE 或可见控制台内容计算业务指标。报告查询等待终态转录可读后发起，以避开终态事件先于 Artifact 原子落盘的短暂窗口。

## Consequences

过程指标与用户看到的终态执行记录具有同一事实来源，重连和筛选不会改变统计。新增 Runtime Adapter 必须提供 provider response 级 usage 和稳定工具身份，或明确产生不完整 usage。旧任务仍可读取工具和时长等可恢复指标，但界面会标记 token 数据不完整。该方案不新增数据库表或迁移；转录 metadata 和过程报告 JSON 字段成为需要兼容维护的稳定契约。
