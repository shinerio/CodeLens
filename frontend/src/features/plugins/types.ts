export type TriggerCapability = {
  trigger_type: string;
  supported_events: string[];
  entry_point: string;
  config_schema: Record<string, unknown>;
};

export type ReportCapability = {
  entry_point: string;
  config_schema: Record<string, unknown>;
};

export type PluginManifest = {
  plugin_id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  platform: string;
  capabilities: {
    trigger?: TriggerCapability;
    report?: ReportCapability;
  };
  min_codelens_version: string | null;
  name_i18n?: Record<string, string>;
  description_i18n?: Record<string, string>;
};

export type PluginRecord = {
  plugin_id: string;
  manifest: PluginManifest;
  is_builtin: boolean;
  install_path: string | null;
  trigger_enabled: boolean;
  report_enabled: boolean;
  report_auto_export: boolean;
  trigger_config: Record<string, unknown>;
  report_config: Record<string, unknown>;
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
  ref?: string;
};

export type InstallPluginResponse = {
  plugin_id: string;
  install_path: string;
  installed_at: string;
};

export type UpdateConfigRequest = {
  config: Record<string, unknown>;
};

export type HookStatusResponse = {
  is_installed: boolean;
  hook_path: string | null;
  repository_path: string;
  repositories: RepositoryHookStatus[];
};

export type RepositoryHookStatus = {
  repository_path: string;
  hooks: Record<string, boolean>;
  is_installed: boolean;
};
