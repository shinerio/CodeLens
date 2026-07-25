export type FailureDetails = {
  title: string;
  description: string;
  action: string;
  isUnknown?: boolean;
};

type LocalizedFailure = { en: FailureDetails; zh: FailureDetails };

const FAILURE_DETAILS: Record<string, LocalizedFailure> = {
  provider_server_error: localized(
    "Model gateway is temporarily unavailable",
    "The model service returned a server error before the review completed.",
    "Retry shortly. If it persists, check gateway availability and the Base URL in Settings.",
    "模型网关暂时不可用",
    "模型服务返回了服务器错误，Review 尚未完成。",
    "请稍后重试；若持续出现，请在设置中检查网关服务状态和 Base URL。",
  ),
  provider_rate_limited: localized(
    "Model gateway rate limited the review",
    "The provider temporarily rejected the request because its rate limit was reached.",
    "Wait briefly and retry, or reduce concurrent reviews.",
    "模型网关触发限流",
    "服务端因达到速率限制而暂时拒绝了请求。",
    "请稍候重试，或降低并发 Review 数量。",
  ),
  provider_request_rejected: localized(
    "Model gateway rejected the request",
    "The current model or gateway configuration did not accept this request.",
    "Check the model name, API type, and gateway URL in Settings.",
    "模型网关拒绝了请求",
    "当前模型或网关配置不接受该请求。",
    "请检查设置中的模型名称、API 类型和网关地址。",
  ),
  provider_timeout: localized(
    "Model gateway request timed out",
    "The gateway did not respond before the request timeout.",
    "Check gateway health and network latency, then retry.",
    "模型网关请求超时",
    "网关未能在请求超时前响应。",
    "请检查网关健康状态和网络延迟，然后重试。",
  ),
  agent_run_timeout: localized(
    "Agent execution timed out",
    "The Agent did not finish within the configured gateway timeout.",
    "Narrow the review scope or increase the Agent timeout in gateway settings.",
    "Agent 执行超时",
    "Agent 未能在网关配置的超时时间内完成。",
    "请缩小 Review 范围，或在网关设置中增加 Agent 超时时间。",
  ),
  provider_connection_error: localized(
    "Cannot connect to the model gateway",
    "CodeLens could not establish a connection to the configured model service.",
    "Check the Base URL and network access, then retry the review.",
    "无法连接模型网关",
    "CodeLens 无法连接到当前配置的模型服务。",
    "请检查 Base URL 和网络连通性，然后重试 Review。",
  ),
  max_model_turns_exceeded: localized(
    "Agent reached the maximum number of turns",
    "The Agent used all tool-call turns before completing the review.",
    "Narrow the review scope or increase the Agent turn limit.",
    "Agent 达到最大执行轮次",
    "Agent 在完成 Review 前已用尽工具调用轮次。",
    "请缩小 Review 范围，或调整 Agent 的轮次设置。",
  ),
  invalid_model_output: localized(
    "Model response is incompatible with the review workflow",
    "The model returned output that CodeLens could not use safely.",
    "Check model and API compatibility in Settings, then retry.",
    "模型响应与 Review 工作流不兼容",
    "模型返回了 CodeLens 无法安全使用的输出。",
    "请在设置中检查模型与 API 兼容性，然后重试。",
  ),
  missing_model_output: localized(
    "Model returned no usable review result",
    "The model finished without the structured result required by CodeLens.",
    "Check model compatibility and retry the review.",
    "模型未返回可用的 Review 结果",
    "模型结束执行时没有提供 CodeLens 所需的结构化结果。",
    "请检查模型兼容性，然后重试 Review。",
  ),
  invalid_comment_output: localized(
    "Review comments could not be validated",
    "The submitted comments did not resolve to the frozen changed lines.",
    "Retry the review or narrow its scope so the Agent can cite exact changed code.",
    "Review 评论无法通过校验",
    "模型提交的评论无法定位到冻结 Snapshot 的变更行。",
    "请重试或缩小 Review 范围，以便 Agent 精确引用变更代码。",
  ),
  repository_validation_failed: localized(
    "Repository Snapshot could not be created",
    "Git metadata or a changed path could not pass the Review trust boundary.",
    "Verify the repository and selected revisions, then retry.",
    "无法创建仓库 Snapshot",
    "Git 元数据或变更路径未能通过 Review 信任边界校验。",
    "请确认仓库与所选 revision 有效，然后重试。",
  ),
  repository_changed_during_review: localized(
    "Repository changed while the Snapshot was being created",
    "CodeLens stopped because the selected input was no longer stable.",
    "Return the repository to a stable state and start a new review.",
    "创建 Snapshot 时仓库发生变化",
    "所选输入不再稳定，CodeLens 已停止执行。",
    "请先让仓库恢复稳定状态，再发起新的 Review。",
  ),
  review_worktree_ownership_failed: localized(
    "Review worktree ownership could not be verified",
    "CodeLens refused to use an isolated checkout it could not prove it owns.",
    "Restart CodeLens or remove stale task data, then retry.",
    "无法验证 Review worktree 所有权",
    "CodeLens 拒绝使用无法证明归属的隔离 checkout。",
    "请重启 CodeLens 或清理陈旧任务数据，然后重试。",
  ),
  review_worktree_mutated: localized(
    "Frozen Review worktree was modified",
    "The isolated checkout changed after CodeLens froze the review input.",
    "Start a new review from a clean task.",
    "冻结的 Review worktree 被修改",
    "CodeLens 冻结 Review 输入后，隔离 checkout 又发生了变化。",
    "请从干净任务重新发起 Review。",
  ),
  internal_review_error: localized(
    "Internal review execution error",
    "CodeLens encountered an unexpected implementation error.",
    "Use this task ID to inspect worker.log, correct the reported cause, and retry.",
    "Review 内部执行错误",
    "CodeLens 遇到了未预期的实现异常。",
    "请使用当前任务 ID 检查 worker.log，修复记录的原因后重试。",
  ),
};

export function failureDetails(
  metadata: Record<string, string>,
  locale: "en" | "zh-CN",
): FailureDetails {
  const reasonCode = normalizeReasonCode(metadata);
  const details = FAILURE_DETAILS[reasonCode];
  if (details !== undefined) return locale === "zh-CN" ? details.zh : details.en;
  return locale === "zh-CN"
    ? {
        title: "未分类的 Review 失败",
        description: "执行过程中发生了尚未分类的错误。",
        action: "请使用当前任务 ID 检查 worker.log 和相关设置，然后重试。",
        isUnknown: true,
      }
    : {
        title: "Unclassified review failure",
        description: "An error without a stable classification interrupted the review.",
        action: "Use this task ID to inspect worker.log and relevant settings, then retry.",
        isUnknown: true,
      };
}

function normalizeReasonCode(metadata: Record<string, string>): string {
  if (metadata.reason_code !== undefined) return metadata.reason_code;
  if (metadata.error_type === "InvalidRepositoryError") return "repository_validation_failed";
  if (metadata.error_type === "TypeError") return "internal_review_error";
  return "unknown_review_error";
}

function localized(
  enTitle: string,
  enDescription: string,
  enAction: string,
  zhTitle: string,
  zhDescription: string,
  zhAction: string,
): LocalizedFailure {
  return {
    en: { title: enTitle, description: enDescription, action: enAction },
    zh: { title: zhTitle, description: zhDescription, action: zhAction },
  };
}
