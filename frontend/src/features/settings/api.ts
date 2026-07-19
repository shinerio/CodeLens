import { api } from "../../shared/api/client";
import type {
  CreateModelGateway,
  ModelGatewayCatalog,
  OpenAISettings,
  RuntimeLogLevel,
  RuntimeLogLevelSettings,
  UpdateModelGateway,
} from "./types";

export async function listModelGateways(): Promise<ModelGatewayCatalog> {
  return api<ModelGatewayCatalog>("/settings/model-gateways");
}

export async function getRuntimeLogLevel(): Promise<RuntimeLogLevelSettings> {
  return api<RuntimeLogLevelSettings>("/settings/logging");
}

export async function updateRuntimeLogLevel(
  level: RuntimeLogLevel,
): Promise<RuntimeLogLevelSettings> {
  return api<RuntimeLogLevelSettings>("/settings/logging", {
    method: "PUT",
    body: JSON.stringify({ level }),
  });
}

export async function createModelGateway(
  request: CreateModelGateway,
): Promise<ModelGatewayCatalog> {
  return api<ModelGatewayCatalog>("/settings/model-gateways", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function updateModelGateway(
  gatewayId: string,
  request: UpdateModelGateway,
): Promise<ModelGatewayCatalog> {
  return api<ModelGatewayCatalog>(`/settings/model-gateways/${gatewayId}`, {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

export async function activateModelGateway(gatewayId: string): Promise<ModelGatewayCatalog> {
  return api<ModelGatewayCatalog>("/settings/active-model-gateway", {
    method: "PUT",
    body: JSON.stringify({ gateway_id: gatewayId }),
  });
}

export async function deleteModelGateway(gatewayId: string): Promise<ModelGatewayCatalog> {
  return api<ModelGatewayCatalog>(`/settings/model-gateways/${gatewayId}`, {
    method: "DELETE",
    body: JSON.stringify({}),
  });
}

/** Compatibility query retained for older callers while the UI uses the gateway catalog. */
export async function getOpenAISettings(): Promise<OpenAISettings> {
  return api<OpenAISettings>("/settings/openai");
}
