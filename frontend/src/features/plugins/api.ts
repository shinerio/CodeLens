import { api } from "../../shared/api/client";
import type {
  HookStatusResponse,
  InstallPluginRequest,
  PluginRecord,
  UpdateConfigRequest,
} from "./types";

const PLUGIN_QUERY_KEY = ["plugins"] as const;

export { PLUGIN_QUERY_KEY };

export async function listPlugins(): Promise<PluginRecord[]> {
  return api<PluginRecord[]>("/plugins");
}

export async function installPlugin(request: InstallPluginRequest): Promise<PluginRecord> {
  return api<PluginRecord>("/plugins/install", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function uninstallPlugin(pluginId: string): Promise<void> {
  await api<void>(`/plugins/${pluginId}`, {
    method: "DELETE",
  });
}

export async function enableTrigger(pluginId: string): Promise<PluginRecord> {
  return api<PluginRecord>(`/plugins/${pluginId}/trigger/enable`, {
    method: "PUT",
  });
}

export async function disableTrigger(pluginId: string): Promise<PluginRecord> {
  return api<PluginRecord>(`/plugins/${pluginId}/trigger/disable`, {
    method: "PUT",
  });
}

export async function enableReport(pluginId: string): Promise<PluginRecord> {
  return api<PluginRecord>(`/plugins/${pluginId}/report/enable`, {
    method: "PUT",
  });
}

export async function disableReport(pluginId: string): Promise<PluginRecord> {
  return api<PluginRecord>(`/plugins/${pluginId}/report/disable`, {
    method: "PUT",
  });
}

export async function updateTriggerConfig(
  pluginId: string,
  request: UpdateConfigRequest,
): Promise<PluginRecord> {
  return api<PluginRecord>(`/plugins/${pluginId}/trigger/config`, {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

export async function updateReportConfig(
  pluginId: string,
  request: UpdateConfigRequest,
): Promise<PluginRecord> {
  return api<PluginRecord>(`/plugins/${pluginId}/report/config`, {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

export async function setAutoExport(
  pluginId: string,
  enabled: boolean,
): Promise<PluginRecord> {
  return api<PluginRecord>(`/plugins/${pluginId}/report/auto-export?enabled=${enabled}`, {
    method: "PUT",
  });
}

export async function installHooks(pluginId: string): Promise<HookStatusResponse> {
  return api<HookStatusResponse>(`/plugins/${pluginId}/trigger/install-hooks`, {
    method: "POST",
  });
}

export async function uninstallHooks(pluginId: string): Promise<HookStatusResponse> {
  return api<HookStatusResponse>(`/plugins/${pluginId}/trigger/uninstall-hooks`, {
    method: "POST",
  });
}

export async function getHookStatus(pluginId: string): Promise<HookStatusResponse> {
  return api<HookStatusResponse>(`/plugins/${pluginId}/trigger/hook-status`);
}
