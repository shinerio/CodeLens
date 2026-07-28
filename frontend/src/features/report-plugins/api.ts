import { api } from "../../shared/api/client";
import type {
  InstallPluginRequest,
  PluginRecord,
  SetAutoExportRequest,
  UpdateConfigRequest,
} from "./types";

const PLUGIN_QUERY_KEY = ["report-plugins"] as const;

export { PLUGIN_QUERY_KEY };

export async function listPlugins(): Promise<PluginRecord[]> {
  return api<PluginRecord[]>("/report-plugins");
}

export async function installPlugin(request: InstallPluginRequest): Promise<PluginRecord> {
  return api<PluginRecord>("/report-plugins/install", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function enablePlugin(pluginId: string): Promise<PluginRecord> {
  return api<PluginRecord>(`/report-plugins/${pluginId}/enable`, {
    method: "PUT",
  });
}

export async function disablePlugin(pluginId: string): Promise<PluginRecord> {
  return api<PluginRecord>(`/report-plugins/${pluginId}/disable`, {
    method: "PUT",
  });
}

export async function updatePluginConfig(
  pluginId: string,
  request: UpdateConfigRequest,
): Promise<PluginRecord> {
  return api<PluginRecord>(`/report-plugins/${pluginId}/config`, {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

export async function setAutoExport(
  pluginId: string,
  request: SetAutoExportRequest,
): Promise<PluginRecord> {
  return api<PluginRecord>(`/report-plugins/${pluginId}/auto-export`, {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

export async function uninstallPlugin(pluginId: string): Promise<void> {
  await api<void>(`/report-plugins/${pluginId}`, {
    method: "DELETE",
  });
}
