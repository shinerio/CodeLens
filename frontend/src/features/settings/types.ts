export type GatewayApiType = "responses" | "chat_completions";
export type ModelProviderVendor = "openai" | "deepseek" | "zhipu" | "qwen";
export type ThinkingLevel = "disabled" | "low" | "medium" | "high";

export type ModelGateway = {
  gateway_id: string;
  name: string;
  model: string;
  base_url: string;
  vendor: ModelProviderVendor;
  is_active: boolean;
  api_type: GatewayApiType;
  max_tokens: number;
  thinking_level: ThinkingLevel;
  agent_timeout: number;
  max_agent_turns: number;
  max_tool_calls: number;
  max_identical_tool_results: number;
  tool_timeout_seconds: number;
  max_retries: number;
  retry_backoff_base: number;
  retry_max_delay: number;
  no_progress_rounds_threshold: number;
};

export type ModelGatewayCatalog = {
  active_gateway_id: string | null;
  gateways: ModelGateway[];
};

export type RuntimeLogLevel = "debug" | "info" | "warning" | "error";

export type RuntimeLoggingSettings = {
  default_level: RuntimeLogLevel;
  level: RuntimeLogLevel;
  model_output_enabled: boolean;
};

export type UpdateRuntimeLoggingSettings = {
  level: RuntimeLogLevel;
  model_output_enabled: boolean;
};

export type RecentRepositorySettings = {
  recent_repository_limit: number;
};

export type InstructionFileSettings = {
  root_max_lines: number;
  nested_max_lines: number;
};

export type FileExclusionSettings = {
  suffixes: string[];
  path_regexes: string[];
};

export type ReviewCompletionSettings = {
  max_incomplete_review_retries: number;
};

export type TriggerIdempotencySettings = {
  enabled: boolean;
};

export type CreateModelGateway = {
  name: string;
  api_key: string;
  model: string;
  base_url: string;
  vendor: ModelProviderVendor;
  api_type: GatewayApiType;
  max_tokens: number;
  thinking_level: ThinkingLevel;
  agent_timeout: number;
  max_agent_turns: number;
  max_tool_calls: number;
  max_identical_tool_results: number;
  tool_timeout_seconds: number;
  max_retries: number;
  retry_backoff_base: number;
  retry_max_delay: number;
  no_progress_rounds_threshold: number;
};

export type UpdateModelGateway = {
  name: string;
  api_key?: string;
  model: string;
  base_url: string;
  vendor: ModelProviderVendor;
  api_type: GatewayApiType;
  max_tokens: number;
  thinking_level: ThinkingLevel;
  agent_timeout: number;
  max_agent_turns: number;
  max_tool_calls: number;
  max_identical_tool_results: number;
  tool_timeout_seconds: number;
  max_retries: number;
  retry_backoff_base: number;
  retry_max_delay: number;
  no_progress_rounds_threshold: number;
};

export type GatewayTestResult = {
  ok: boolean;
  latency_ms: number | null;
  detail: string;
};

export type ToolLimits = {
  max_results: number;
  max_read_bytes: number;
  max_scan_bytes: number;
  max_source_bytes: number;
  max_file_payload_cache_bytes: number;
  max_lines: number;
  max_path_chars: number;
  max_pattern_chars: number;
  regex_timeout_seconds: number;
  comment_batch_size: number;
  short_text_max: number;
  long_text_max: number;
  task_summary_max: number;
  context_compaction_enabled: boolean;
  context_compaction_trigger_tokens: number;
  context_compaction_keep_recent_evidence_results: number;
  context_compaction_max_retries: number;
  context_compaction_retry_backoff_base: number;
  context_compaction_retry_max_delay: number;
  context_compaction_max_consecutive_failures: number;
};

export type ResetAllSettingsResponse = {
  instruction_files: InstructionFileSettings;
  file_exclusions: FileExclusionSettings;
  review_completion: ReviewCompletionSettings;
  trigger_idempotency: TriggerIdempotencySettings;
  recent_repositories: RecentRepositorySettings;
  tool_limits: ToolLimits;
  node_settings: NodeSettings;
  logging: RuntimeLoggingSettings;
  model_gateways: ModelGatewayCatalog;
};

export type NodeSettings = {
  memory_limit_mb: number;
  memory_check_interval_seconds: number;
  memory_cleanup_threshold_ratio: number;
  memory_reject_threshold_ratio: number;
  max_active_reviews: number;
  max_active_agent_runs: number;
  max_agent_runs_per_review: number;
};
