export type TriggerManifest = {
  plugin_id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  entry_point: string;
  trigger_type: string;
  supported_events: string[];
  config_schema: Record<string, unknown>;
  min_codelens_version: string | null;
};

export type TriggerConfig = {
  repository_paths: string[];
  events: string[];
  scope_type: string;
  base_ref: string | null;
  target_ref: string | null;
  selected_agents: string[];
  prompt_locale: string;
  debounce_seconds: number;
  extra: Record<string, unknown>;
};

export type TriggerRecord = {
  plugin_id: string;
  manifest: TriggerManifest;
  is_enabled: boolean;
  is_builtin: boolean;
  install_path: string | null;
  config: TriggerConfig;
};

export type UpdateTriggerConfigRequest = {
  repository_paths?: string[];
  events?: string[];
  scope_type?: string;
  base_ref?: string | null;
  target_ref?: string | null;
  selected_agents?: string[];
  prompt_locale?: string;
  debounce_seconds?: number;
};

export type InstallHooksRequest = {
  repository_paths: string[];
};

export type HookStatusResponse = {
  repository_path: string;
  hooks: Record<string, boolean>;
};
