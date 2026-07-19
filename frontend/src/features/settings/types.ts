export type ModelGateway = {
  gateway_id: string;
  name: string;
  model: string;
  base_url: string;
  is_active: boolean;
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
};

export type UpdateModelGateway = {
  name: string;
  api_key?: string;
  model: string;
  base_url: string;
};

export type OpenAISettings = {
  is_configured: boolean;
  model: string | null;
  base_url: string | null;
};
