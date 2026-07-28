import { api } from "../../shared/api/client";
import type {
  HookStatusResponse,
  InstallHooksRequest,
  TriggerRecord,
  UpdateTriggerConfigRequest,
} from "./types";

const TRIGGER_PLUGIN_QUERY_KEY = ["trigger-plugins"] as const;

export { TRIGGER_PLUGIN_QUERY_KEY };

export async function listTriggerPlugins(): Promise<TriggerRecord[]> {
  return api<TriggerRecord[]>("/trigger-plugins");
}

export async function enableTriggerPlugin(pluginId: string): Promise<TriggerRecord> {
  return api<TriggerRecord>(`/trigger-plugins/${pluginId}/enable`, {
    method: "PUT",
  });
}

export async function disableTriggerPlugin(pluginId: string): Promise<TriggerRecord> {
  return api<TriggerRecord>(`/trigger-plugins/${pluginId}/disable`, {
    method: "PUT",
  });
}

export async function updateTriggerConfig(
  pluginId: string,
  request: UpdateTriggerConfigRequest,
): Promise<TriggerRecord> {
  return api<TriggerRecord>(`/trigger-plugins/${pluginId}/config`, {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

export async function installHooks(
  pluginId: string,
  request: InstallHooksRequest,
): Promise<void> {
  await api<void>(`/trigger-plugins/${pluginId}/install-hooks`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function uninstallHooks(
  pluginId: string,
  request: InstallHooksRequest,
): Promise<void> {
  await api<void>(`/trigger-plugins/${pluginId}/uninstall-hooks`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function getHookStatus(
  pluginId: string,
  repositoryPaths: string[],
): Promise<HookStatusResponse[]> {
  const paths = encodeURIComponent(repositoryPaths.join(","));
  return api<HookStatusResponse[]>(
    `/trigger-plugins/${pluginId}/hook-status?repository_paths=${paths}`,
  );
}
