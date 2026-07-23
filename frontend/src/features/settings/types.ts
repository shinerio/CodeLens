export type GatewayApiType = "responses" | "chat_completions";

export type ModelGateway = {
  gateway_id: string;
  name: string;
  model: string;
  base_url: string;
  is_active: boolean;
  api_type: GatewayApiType;
};

export type ModelGatewayCatalog = {
  active_gateway_id: string | null;
  gateways: ModelGateway[];
};

export type RuntimeLogLevel = "debug" | "info" | "warning" | "error";

export type RuntimeLogLevelSettings = {
  level: RuntimeLogLevel;
};

export type CreateModelGateway = {
  name: string;
  api_key: string;
  model: string;
  base_url: string;
  api_type: GatewayApiType;
};

export type UpdateModelGateway = {
  name: string;
  api_key?: string;
  model: string;
  base_url: string;
  api_type: GatewayApiType;
};

export type OpenAISettings = {
  is_configured: boolean;
  model: string | null;
  base_url: string | null;
};

export type GatewayTestResult = {
  ok: boolean;
  latency_ms: number | null;
  detail: string;
};
