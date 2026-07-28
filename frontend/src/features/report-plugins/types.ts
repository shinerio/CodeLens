export type PluginManifest = {
  plugin_id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  entry_point: string;
  config_schema: Record<string, unknown>;
  min_codelens_version: string | null;
};

export type PluginRecord = {
  plugin_id: string;
  manifest: PluginManifest;
  is_enabled: boolean;
  is_builtin: boolean;
  install_path: string | null;
  config: Record<string, unknown>;
  auto_export: boolean;
};

export type ExportResult = {
  plugin_id: string;
  task_id: string;
  success: boolean;
  output_path: string | null;
  error: string | null;
  exported_at: string;
};

export type InstallPluginRequest = {
  git_url: string;
  ref?: string | null;
};

export type UpdateConfigRequest = {
  config: Record<string, unknown>;
};

export type SetAutoExportRequest = {
  enabled: boolean;
};
