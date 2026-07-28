import { api } from "../../shared/api/client";
import type {
  CreateModelGateway,
  GatewayTestResult,
  InstructionFileSettings,
  ModelGatewayCatalog,
  RecentRepositorySettings,
  ResetAllSettingsResponse,
  ReviewCompletionSettings,
  RuntimeLogLevel,
  RuntimeLogLevelSettings,
  ToolLimits,
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

export async function getRecentRepositorySettings(): Promise<RecentRepositorySettings> {
  return api<RecentRepositorySettings>("/settings/repositories");
}

export async function updateRecentRepositoryLimit(
  recentRepositoryLimit: number,
): Promise<RecentRepositorySettings> {
  return api<RecentRepositorySettings>("/settings/repositories", {
    method: "PUT",
    body: JSON.stringify({ recent_repository_limit: recentRepositoryLimit }),
  });
}

export async function getInstructionFileSettings(): Promise<InstructionFileSettings> {
  return api<InstructionFileSettings>("/settings/instruction-files");
}

export async function updateInstructionFileSettings(
  settings: InstructionFileSettings,
): Promise<InstructionFileSettings> {
  return api<InstructionFileSettings>("/settings/instruction-files", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function getReviewCompletionSettings(): Promise<ReviewCompletionSettings> {
  return api<ReviewCompletionSettings>("/settings/review-completion");
}

export async function updateReviewCompletionSettings(
  settings: ReviewCompletionSettings,
): Promise<ReviewCompletionSettings> {
  return api<ReviewCompletionSettings>("/settings/review-completion", {
    method: "PUT",
    body: JSON.stringify(settings),
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

export async function testGatewayConnectivity(gatewayId: string): Promise<GatewayTestResult> {
  return api<GatewayTestResult>(`/settings/model-gateways/${gatewayId}/test-connectivity`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function testGatewayAvailability(gatewayId: string): Promise<GatewayTestResult> {
  return api<GatewayTestResult>(`/settings/model-gateways/${gatewayId}/test-availability`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function getToolLimits(): Promise<ToolLimits> {
  return api<ToolLimits>("/settings/tool-limits");
}

export async function updateToolLimits(limits: ToolLimits): Promise<ToolLimits> {
  return api<ToolLimits>("/settings/tool-limits", {
    method: "PUT",
    body: JSON.stringify(limits),
  });
}

export async function resetAllSettings(): Promise<ResetAllSettingsResponse> {
  return api<ResetAllSettingsResponse>("/settings/reset-all", {
    method: "POST",
    body: JSON.stringify({}),
  });
}
