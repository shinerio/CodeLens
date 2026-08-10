export type GatewayApiType = "responses" | "chat_completions";
export type ModelProviderVendor = "openai" | "deepseek" | "zhipu";
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
};

export type ModelGatewayCatalog = {
  active_gateway_id: string | null;
  gateways: ModelGateway[];
};

export type RuntimeLogLevel = "debug" | "info" | "warning" | "error";

export type RuntimeLogLevelSettings = {
  level: RuntimeLogLevel;
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
  exclude_binary: boolean;
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
  max_lines: number;
  max_path_chars: number;
  max_pattern_chars: number;
  regex_timeout_seconds: number;
  comment_batch_size: number;
  short_text_max: number;
  long_text_max: number;
  task_summary_max: number;
};

export type ResetAllSettingsResponse = {
  instruction_files: InstructionFileSettings;
  file_exclusions: FileExclusionSettings;
  review_completion: ReviewCompletionSettings;
  trigger_idempotency: TriggerIdempotencySettings;
  recent_repositories: RecentRepositorySettings;
  tool_limits: ToolLimits;
  logging: RuntimeLogLevelSettings;
  model_gateways: ModelGatewayCatalog;
};
