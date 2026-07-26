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

export type ReviewCompletionSettings = {
  max_incomplete_review_retries: number;
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
};

export type GatewayTestResult = {
  ok: boolean;
  latency_ms: number | null;
  detail: string;
};
