import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Download,
  ExternalLink,
  FolderGit2,
  Package,
  Plus,
  Power,
  RefreshCw,
  Trash2,
  Webhook,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import { RepositoryBrowser } from "../repositories/RepositoryBrowser";
import {
  disableReport,
  disableTrigger,
  enableReport,
  enableTrigger,
  getHookStatus,
  installHooks,
  installPlugin,
  listPlugins,
  PLUGIN_QUERY_KEY,
  setAutoExport,
  uninstallPlugin,
  updateReportConfig,
  updateTriggerConfig,
} from "./api";
import type { HookStatusResponse, PluginRecord } from "./types";
import "./PluginsPage.css";

const AVAILABLE_AGENTS = [
  { reference: "correctness:v1", labelKey: "review.correctness", enabled: true },
  { reference: "security:v1", labelKey: "review.security", enabled: false },
  { reference: "performance:v1", labelKey: "review.performance", enabled: false },
  { reference: "maintainability:v1", labelKey: "review.maintainability", enabled: false },
] as const;

export function PluginsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [gitUrl, setGitUrl] = useState("");
  const [gitRef, setGitRef] = useState("");
  const [installError, setInstallError] = useState<string | null>(null);

  const pluginsQuery = useQuery({
    queryKey: PLUGIN_QUERY_KEY,
    queryFn: listPlugins,
  });

  const invalidatePlugins = () => {
    void queryClient.invalidateQueries({ queryKey: PLUGIN_QUERY_KEY });
  };

  const enableTriggerMutation = useMutation({
    mutationFn: (pluginId: string) => enableTrigger(pluginId),
    onSuccess: invalidatePlugins,
  });

  const disableTriggerMutation = useMutation({
    mutationFn: (pluginId: string) => disableTrigger(pluginId),
    onSuccess: invalidatePlugins,
  });

  const enableReportMutation = useMutation({
    mutationFn: (pluginId: string) => enableReport(pluginId),
    onSuccess: invalidatePlugins,
  });

  const disableReportMutation = useMutation({
    mutationFn: (pluginId: string) => disableReport(pluginId),
    onSuccess: invalidatePlugins,
  });

  const autoExportMutation = useMutation({
    mutationFn: ({ pluginId, enabled }: { pluginId: string; enabled: boolean }) =>
      setAutoExport(pluginId, enabled),
    onSuccess: invalidatePlugins,
  });

  const uninstallMutation = useMutation({
    mutationFn: (pluginId: string) => uninstallPlugin(pluginId),
    onSuccess: invalidatePlugins,
  });

  const installMutation = useMutation({
    mutationFn: () => {
      const ref = gitRef.trim();
      return installPlugin({
        git_url: gitUrl.trim(),
        ...(ref ? { ref } : {}),
      });
    },
    onSuccess: () => {
      setGitUrl("");
      setGitRef("");
      setInstallError(null);
      invalidatePlugins();
    },
    onError: (error: Error) => {
      setInstallError(error.message);
    },
  });

  const plugins = pluginsQuery.data ?? [];

  return (
    <section className="plugins-page">
      <header className="plugins-header">
        <div>
          <h1>{t("plugins.title")}</h1>
          <p className="plugins-subtitle">{t("plugins.subtitle")}</p>
        </div>
      </header>

      <div className="plugins-install">
        <h2 className="plugins-section-title">{t("plugins.installNew")}</h2>
        <div className="install-form">
          <input
            className="install-input install-input--url"
            placeholder={t("plugins.gitUrlPlaceholder")}
            value={gitUrl}
            onChange={(e) => setGitUrl(e.target.value)}
            disabled={installMutation.isPending}
          />
          <input
            className="install-input install-input--ref"
            placeholder={t("plugins.refPlaceholder")}
            value={gitRef}
            onChange={(e) => setGitRef(e.target.value)}
            disabled={installMutation.isPending}
          />
          <button
            className="install-button"
            disabled={!gitUrl.trim() || installMutation.isPending}
            onClick={() => installMutation.mutate()}
          >
            <Download aria-hidden="true" />
            {t("plugins.install")}
          </button>
        </div>
        {installError && <p className="install-error">{installError}</p>}
      </div>

      {pluginsQuery.isLoading && <p className="plugins-loading">{t("common.loading")}</p>}
      {pluginsQuery.error && (
        <p className="plugins-error">
          {t("plugins.loadFailed")}: {(pluginsQuery.error as Error).message}
        </p>
      )}

      <div className="plugin-cards">
        {plugins.map((plugin) => (
          <PluginCard
            key={plugin.plugin_id}
            plugin={plugin}
            onEnableTrigger={(id) => enableTriggerMutation.mutate(id)}
            onDisableTrigger={(id) => disableTriggerMutation.mutate(id)}
            onEnableReport={(id) => enableReportMutation.mutate(id)}
            onDisableReport={(id) => disableReportMutation.mutate(id)}
            onToggleAutoExport={(id, enabled) => autoExportMutation.mutate({ pluginId: id, enabled })}
            onUninstall={(id) => uninstallMutation.mutate(id)}
            onConfigUpdate={invalidatePlugins}
          />
        ))}
      </div>
    </section>
  );
}

type PluginCardProps = {
  plugin: PluginRecord;
  onEnableTrigger: (pluginId: string) => void;
  onDisableTrigger: (pluginId: string) => void;
  onEnableReport: (pluginId: string) => void;
  onDisableReport: (pluginId: string) => void;
  onToggleAutoExport: (pluginId: string, enabled: boolean) => void;
  onUninstall: (pluginId: string) => void;
  onConfigUpdate: () => void;
};

function PluginCard({
  plugin,
  onEnableTrigger,
  onDisableTrigger,
  onEnableReport,
  onDisableReport,
  onToggleAutoExport,
  onUninstall,
  onConfigUpdate,
}: PluginCardProps) {
  const { t } = useI18n();
  const hasTrigger = !!plugin.manifest.capabilities.trigger;
  const hasReport = !!plugin.manifest.capabilities.report;

  return (
    <article className="plugin-card">
      <div className="plugin-card__header">
        <div className="plugin-card__title-row">
          <Package aria-hidden="true" className="plugin-card__icon" />
          <div>
            <h3 className="plugin-card__name">{plugin.manifest.name}</h3>
            <span className="plugin-card__version">v{plugin.manifest.version}</span>
            <span className="plugin-card__badge plugin-card__badge--platform">{plugin.manifest.platform}</span>
            {plugin.is_builtin && <span className="plugin-card__badge plugin-card__badge--builtin">{t("plugins.builtin")}</span>}
          </div>
        </div>
        {!plugin.is_builtin && (
          <button
            className="plugin-uninstall"
            onClick={() => onUninstall(plugin.plugin_id)}
          >
            <Trash2 aria-hidden="true" />
            {t("plugins.uninstall")}
          </button>
        )}
      </div>

      <p className="plugin-card__description">{plugin.manifest.description}</p>

      {hasTrigger && (
        <TriggerCapabilitySection
          plugin={plugin}
          onEnable={(id) => onEnableTrigger(id)}
          onDisable={(id) => onDisableTrigger(id)}
          onConfigUpdate={onConfigUpdate}
        />
      )}

      {hasReport && (
        <ReportCapabilitySection
          plugin={plugin}
          onEnable={(id) => onEnableReport(id)}
          onDisable={(id) => onDisableReport(id)}
          onToggleAutoExport={(id, enabled) => onToggleAutoExport(id, enabled)}
          onConfigUpdate={onConfigUpdate}
        />
      )}

      {plugin.install_path && (
        <div className="plugin-card__install-path">
          <ExternalLink aria-hidden="true" className="plugin-card__path-icon" />
          <code>{plugin.install_path}</code>
        </div>
      )}
    </article>
  );
}

type TriggerCapabilitySectionProps = {
  plugin: PluginRecord;
  onEnable: (pluginId: string) => void;
  onDisable: (pluginId: string) => void;
  onConfigUpdate: () => void;
};

function TriggerCapabilitySection({
  plugin,
  onEnable,
  onDisable,
  onConfigUpdate,
}: TriggerCapabilitySectionProps) {
  const { t } = useI18n();
  const [configDraft, setConfigDraft] = useState<Record<string, unknown>>(plugin.trigger_config);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [hookStatus, setHookStatus] = useState<HookStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const configMutation = useMutation({
    mutationFn: (config: Record<string, unknown>) =>
      updateTriggerConfig(plugin.plugin_id, { config }),
    onSuccess: () => {
      setError(null);
      onConfigUpdate();
      refreshHookStatus();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const hookInstallMutation = useMutation({
    mutationFn: () => installHooks(plugin.plugin_id),
    onSuccess: (status) => {
      setHookStatus(status);
      setError(null);
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const configChanged = JSON.stringify(configDraft) !== JSON.stringify(plugin.trigger_config);

  useEffect(() => {
    setConfigDraft(plugin.trigger_config);
  }, [plugin.plugin_id, plugin.trigger_config]);

  function refreshHookStatus() {
    getHookStatus(plugin.plugin_id).then(setHookStatus).catch(() => setHookStatus(null));
  }

  useEffect(() => {
    if (plugin.trigger_enabled) {
      refreshHookStatus();
    }
  }, [plugin.plugin_id, plugin.trigger_enabled]);

  const triggerType = plugin.manifest.capabilities.trigger?.trigger_type ?? "local-hook";
  const isLocalHook = triggerType === "local-hook";
  const isWebhook = triggerType === "webhook";

  // For local-hook: use hardcoded fields
  // For webhook: use config_schema to dynamically render fields
  const repositoryPaths = (configDraft.repository_paths as string[]) ?? [];
  const events = (configDraft.events as string[]) ?? [];
  const selectedAgents = (configDraft.selected_agents as string[]) ?? [];
  const scopeType = (configDraft.scope_type as string) ?? "uncommitted";
  const baseRef = (configDraft.base_ref as string | null) ?? "";
  const targetRef = (configDraft.target_ref as string | null) ?? "";
  const promptLocale = (configDraft.prompt_locale as string) ?? "en";
  const debounceSeconds = (configDraft.debounce_seconds as number) ?? 0;

  function handleRepositorySelect(path: string) {
    if (!repositoryPaths.includes(path)) {
      setConfigDraft({ ...configDraft, repository_paths: [...repositoryPaths, path] });
    }
    setBrowserOpen(false);
  }

  function handleRemoveRepository(path: string) {
    setConfigDraft({
      ...configDraft,
      repository_paths: repositoryPaths.filter((p) => p !== path),
    });
  }

  function handleEventToggle(event: string, checked: boolean) {
    const newEvents = checked ? [...events, event] : events.filter((e) => e !== event);
    setConfigDraft({ ...configDraft, events: newEvents });
  }

  function handleScopeChange(newScopeType: string) {
    setConfigDraft({ ...configDraft, scope_type: newScopeType });
  }

  function handleAgentToggle(reference: string, checked: boolean) {
    const newAgents = checked
      ? [...selectedAgents, reference]
      : selectedAgents.filter((a) => a !== reference);
    setConfigDraft({ ...configDraft, selected_agents: newAgents });
  }

  const supportedEvents = plugin.manifest.capabilities.trigger?.supported_events ?? [];

  // Common trigger fields rendered with specialized UI for all trigger types
  const COMMON_TRIGGER_FIELDS = new Set(["selected_agents", "prompt_locale", "debounce_seconds"]);

  // For webhook: extract config_schema properties for dynamic rendering
  const webhookConfigSchema = plugin.manifest.capabilities.trigger?.config_schema ?? {};
  const webhookConfigProperties = isWebhook ? extractConfigProperties(webhookConfigSchema) : [];
  // Plugin-specific custom fields (excluding common ones already rendered)
  const webhookCustomProperties = webhookConfigProperties.filter(
    (prop) => !COMMON_TRIGGER_FIELDS.has(prop.key),
  );

  return (
    <div className="capability-section capability-section--trigger">
      <div className="capability-section__header">
        <Webhook aria-hidden="true" className="capability-section__icon" />
        <h4 className="capability-section__title">{t("plugins.triggerCapability")}</h4>
        <button
          className={`capability-toggle ${plugin.trigger_enabled ? "capability-toggle--on" : ""}`}
          onClick={() => (plugin.trigger_enabled ? onDisable(plugin.plugin_id) : onEnable(plugin.plugin_id))}
        >
          <Power aria-hidden="true" />
          {plugin.trigger_enabled ? t("plugins.enabled") : t("plugins.disabled")}
        </button>
      </div>

      {isWebhook && (
        <div className="capability-webhook-info">
          <p className="webhook-url-label">{t("plugins.webhookUrl")}</p>
          <code className="webhook-url">{`POST /api/webhooks/${plugin.manifest.platform}`}</code>
          <p className="webhook-hint">{t("plugins.webhookHint")}</p>
        </div>
      )}

      <div className="capability-config">
        {isLocalHook && (
          <>
            <div className="config-section">
              <label className="config-section__label">{t("plugins.repositoryPaths")}</label>
              <div className="config-repositories">
                {repositoryPaths.map((path) => (
                  <div key={path} className="config-repository">
                    <FolderGit2 aria-hidden="true" />
                    <code>{path}</code>
                    <button
                      className="config-repository__remove"
                      onClick={() => handleRemoveRepository(path)}
                      type="button"
                    >
                      <X aria-hidden="true" />
                    </button>
                  </div>
                ))}
              </div>
              <button
                className="config-add-repo"
                onClick={() => setBrowserOpen(true)}
                type="button"
              >
                <Plus aria-hidden="true" />
                {t("plugins.addRepository")}
              </button>
            </div>

            <div className="config-section">
              <label className="config-section__label">{t("plugins.events")}</label>
              <div className="config-checkboxes">
                {supportedEvents.map((event) => (
                  <label key={event} className="config-checkbox">
                    <input
                      type="checkbox"
                      checked={events.includes(event)}
                      onChange={(e) => handleEventToggle(event, e.target.checked)}
                    />
                    <span>{event}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="config-section">
              <label className="config-section__label">{t("plugins.selectedAgents")}</label>
              <div className="config-checkboxes">
                {AVAILABLE_AGENTS.map((agent) => (
                  <label
                    key={agent.reference}
                    className={`config-checkbox ${!agent.enabled ? "config-checkbox--disabled" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedAgents.includes(agent.reference)}
                      disabled={!agent.enabled}
                      onChange={(e) => handleAgentToggle(agent.reference, e.target.checked)}
                    />
                    <span>{t(agent.labelKey)}</span>
                    {!agent.enabled && (
                      <span className="config-agent-badge">{t("plugins.comingSoon")}</span>
                    )}
                  </label>
                ))}
              </div>
              {selectedAgents.length === 0 && (
                <p className="config-error">{t("plugins.noAgentSelected")}</p>
              )}
            </div>

            <div className="config-section">
              <label className="config-section__label">{t("plugins.scopeType")}</label>
              <div className="config-radios">
                {(["commit", "branch", "uncommitted"] as const).map((scope) => (
                  <label key={scope} className="config-radio">
                    <input
                      type="radio"
                      name={`scope-${plugin.plugin_id}`}
                      checked={scopeType === scope}
                      onChange={() => handleScopeChange(scope)}
                    />
                    <span>{t(`plugins.scope.${scope}`)}</span>
                  </label>
                ))}
              </div>
            </div>

            {scopeType === "branch" && (
              <>
                <div className="config-section">
                  <label className="config-section__label">{t("plugins.baseRef")}</label>
                  <input
                    className="config-input"
                    type="text"
                    value={baseRef}
                    onChange={(e) => setConfigDraft({ ...configDraft, base_ref: e.target.value || null })}
                    placeholder="main"
                  />
                </div>
                <div className="config-section">
                  <label className="config-section__label">{t("plugins.targetRef")}</label>
                  <input
                    className="config-input"
                    type="text"
                    value={targetRef}
                    onChange={(e) => setConfigDraft({ ...configDraft, target_ref: e.target.value || null })}
                    placeholder="feature-branch"
                  />
                </div>
              </>
            )}

            <div className="config-section">
              <label className="config-section__label">{t("plugins.locale")}</label>
              <select
                className="config-select"
                value={promptLocale}
                onChange={(e) => setConfigDraft({ ...configDraft, prompt_locale: e.target.value })}
              >
                <option value="en">English</option>
                <option value="zh-CN">中文</option>
              </select>
            </div>

            <div className="config-section">
              <label className="config-section__label">{t("plugins.debounce")}</label>
              <input
                className="config-input"
                type="number"
                min="0"
                value={debounceSeconds}
                onChange={(e) =>
                  setConfigDraft({ ...configDraft, debounce_seconds: parseInt(e.target.value) || 0 })
                }
              />
            </div>
          </>
        )}

        {isWebhook && (
          <>
            <div className="config-section">
              <label className="config-section__label">{t("plugins.selectedAgents")}</label>
              <div className="config-checkboxes">
                {AVAILABLE_AGENTS.map((agent) => (
                  <label
                    key={agent.reference}
                    className={`config-checkbox ${!agent.enabled ? "config-checkbox--disabled" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedAgents.includes(agent.reference)}
                      disabled={!agent.enabled}
                      onChange={(e) => handleAgentToggle(agent.reference, e.target.checked)}
                    />
                    <span>{t(agent.labelKey)}</span>
                    {!agent.enabled && (
                      <span className="config-agent-badge">{t("plugins.comingSoon")}</span>
                    )}
                  </label>
                ))}
              </div>
              {selectedAgents.length === 0 && (
                <p className="config-error">{t("plugins.noAgentSelected")}</p>
              )}
            </div>

            <div className="config-section">
              <label className="config-section__label">{t("plugins.locale")}</label>
              <select
                className="config-select"
                value={promptLocale}
                onChange={(e) => setConfigDraft({ ...configDraft, prompt_locale: e.target.value })}
              >
                <option value="en">English</option>
                <option value="zh-CN">中文</option>
              </select>
            </div>

            <div className="config-section">
              <label className="config-section__label">{t("plugins.debounce")}</label>
              <input
                className="config-input"
                type="number"
                min="0"
                value={debounceSeconds}
                onChange={(e) =>
                  setConfigDraft({ ...configDraft, debounce_seconds: parseInt(e.target.value) || 0 })
                }
              />
            </div>
          </>
        )}

        {isWebhook && webhookCustomProperties.length > 0 && (
          <div className="config-section">
            {webhookCustomProperties.map((prop) => (
              <ConfigField
                key={prop.key}
                label={prop.label}
                value={configDraft[prop.key]}
                defaultValue={prop.default}
                type={prop.type}
                enumValues={prop.enumValues}
                itemEnumValues={prop.itemEnumValues}
                onChange={(value) => setConfigDraft({ ...configDraft, [prop.key]: value })}
              />
            ))}
          </div>
        )}

        <button
          className="config-save"
          disabled={!configChanged || configMutation.isPending}
          onClick={() => configMutation.mutate(configDraft)}
          type="button"
        >
          <Check aria-hidden="true" />
          {t("plugins.saveConfig")}
        </button>
      </div>

      {isLocalHook && plugin.trigger_enabled && (
        <div className="capability-hooks">
          <div className="capability-hooks__header">
            <h5 className="capability-hooks__title">{t("plugins.hooks")}</h5>
            <button
              aria-label={
                hookStatus?.is_installed
                  ? t("plugins.reinstallHooks")
                  : t("plugins.installHooks")
              }
              className="hook-install-button"
              disabled={
                hookInstallMutation.isPending ||
                configChanged ||
                repositoryPaths.length === 0 ||
                events.length === 0
              }
              onClick={() => hookInstallMutation.mutate()}
              title={
                hookStatus?.is_installed
                  ? t("plugins.reinstallHooks")
                  : t("plugins.installHooks")
              }
              type="button"
            >
              <RefreshCw
                aria-hidden="true"
                className={hookInstallMutation.isPending ? "hook-install-button__spin" : undefined}
              />
              {hookInstallMutation.isPending
                ? t("plugins.installingHooks")
                : hookStatus?.is_installed
                  ? t("plugins.reinstallHooks")
                  : t("plugins.installHooks")}
            </button>
          </div>
          {(hookStatus?.repositories ?? []).map((repositoryStatus) => (
            <div className="hook-status" key={repositoryStatus.repository_path}>
              <span className="hook-status__repo">{repositoryStatus.repository_path}</span>
              <span
                className={`hook-status__state ${
                  repositoryStatus.is_installed
                    ? "hook-status__state--installed"
                    : "hook-status__state--not-installed"
                }`}
              >
                {repositoryStatus.is_installed
                  ? t("plugins.hookInstalled")
                  : t("plugins.hookNotInstalled")}
              </span>
            </div>
          ))}
        </div>
      )}

      {error && <p className="config-error">{error}</p>}

      {isLocalHook && (
        <RepositoryBrowser
          isOpen={browserOpen}
          onClose={() => setBrowserOpen(false)}
          onSelect={handleRepositorySelect}
        />
      )}
    </div>
  );
}

type ReportCapabilitySectionProps = {
  plugin: PluginRecord;
  onEnable: (pluginId: string) => void;
  onDisable: (pluginId: string) => void;
  onToggleAutoExport: (pluginId: string, enabled: boolean) => void;
  onConfigUpdate: () => void;
};

function ReportCapabilitySection({
  plugin,
  onEnable,
  onDisable,
  onToggleAutoExport,
  onConfigUpdate,
}: ReportCapabilitySectionProps) {
  const { t } = useI18n();
  const [configDraft, setConfigDraft] = useState<Record<string, unknown>>(plugin.report_config);

  useEffect(() => {
    setConfigDraft(plugin.report_config);
  }, [plugin.plugin_id, plugin.report_config]);

  const configMutation = useMutation({
    mutationFn: (config: Record<string, unknown>) =>
      updateReportConfig(plugin.plugin_id, { config }),
    onSuccess: () => {
      onConfigUpdate();
    },
  });

  const configChanged = JSON.stringify(configDraft) !== JSON.stringify(plugin.report_config);
  const configSchema = plugin.manifest.capabilities.report?.config_schema ?? {};
  const configProperties = extractConfigProperties(configSchema);

  return (
    <div className="capability-section capability-section--report">
      <div className="capability-section__header">
        <Package aria-hidden="true" className="capability-section__icon" />
        <h4 className="capability-section__title">{t("plugins.reportCapability")}</h4>
        <button
          className={`capability-toggle ${plugin.report_enabled ? "capability-toggle--on" : ""}`}
          onClick={() => (plugin.report_enabled ? onDisable(plugin.plugin_id) : onEnable(plugin.plugin_id))}
        >
          <Power aria-hidden="true" />
          {plugin.report_enabled ? t("plugins.enabled") : t("plugins.disabled")}
        </button>
      </div>

      {configProperties.length > 0 && (
        <div className="capability-config">
          {configProperties.map((prop) => (
            <ConfigField
              key={prop.key}
              label={prop.label}
              value={configDraft[prop.key]}
              defaultValue={prop.default}
              type={prop.type}
              enumValues={prop.enumValues}
              itemEnumValues={prop.itemEnumValues}
              onChange={(value) => setConfigDraft({ ...configDraft, [prop.key]: value })}
            />
          ))}
          <button
            className="config-save"
            disabled={!configChanged || configMutation.isPending}
            onClick={() => configMutation.mutate(configDraft)}
          >
            <Check aria-hidden="true" />
            {t("plugins.saveConfig")}
          </button>
        </div>
      )}

      <div className="capability-auto-export">
        <label className="auto-export-toggle">
          <input
            type="checkbox"
            checked={plugin.report_auto_export}
            onChange={(e) => onToggleAutoExport(plugin.plugin_id, e.target.checked)}
            disabled={!plugin.report_enabled}
          />
          <span>{t("plugins.autoExport")}</span>
        </label>
        <span className="auto-export-hint">{t("plugins.autoExportHint")}</span>
      </div>
    </div>
  );
}

type ConfigProperty = {
  key: string;
  label: string;
  type: string;
  default: unknown;
  enumValues: string[];
  itemEnumValues: string[];
};

function extractConfigProperties(schema: Record<string, unknown>): ConfigProperty[] {
  const rawProperties = schema.properties;
  if (!isRecord(rawProperties)) {
    return [];
  }
  return Object.entries(rawProperties).flatMap(([key, rawProperty]) => {
    if (!isRecord(rawProperty)) {
      return [];
    }
    const items = isRecord(rawProperty.items) ? rawProperty.items : {};
    return [{
      key,
      label: typeof rawProperty.description === "string" ? rawProperty.description : key,
      type: typeof rawProperty.type === "string" ? rawProperty.type : "string",
      default: rawProperty.default,
      enumValues: stringValues(rawProperty.enum),
      itemEnumValues: stringValues(items.enum),
    }];
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringValues(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

type ConfigFieldProps = {
  label: string;
  value: unknown;
  defaultValue: unknown;
  type: string;
  enumValues: string[];
  itemEnumValues: string[];
  onChange: (value: unknown) => void;
};

function ConfigField({
  label,
  value,
  defaultValue,
  type,
  enumValues,
  itemEnumValues,
  onChange,
}: ConfigFieldProps) {
  if (type === "array" && itemEnumValues.length > 0) {
    const current = stringValues(value ?? defaultValue);
    return (
      <div className="config-field config-field--array">
        <label className="config-field__label">{label}</label>
        <div className="config-field__checkboxes">
          {itemEnumValues.map((option) => (
            <label key={option} className="config-field__checkbox">
              <input
                type="checkbox"
                checked={current.includes(option)}
                onChange={(e) => {
                  if (e.target.checked) {
                    onChange([...current, option]);
                  } else {
                    onChange(current.filter((item) => item !== option));
                  }
                }}
              />
              <span>{option}</span>
            </label>
          ))}
        </div>
      </div>
    );
  }

  if (type === "boolean") {
    const checked = typeof value === "boolean"
      ? value
      : typeof defaultValue === "boolean" && defaultValue;
    return (
      <label className="config-field config-field--boolean">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="config-field__label">{label}</span>
      </label>
    );
  }

  if (enumValues.length > 0) {
    const selected = typeof value === "string"
      ? value
      : typeof defaultValue === "string" ? defaultValue : enumValues[0];
    return (
      <div className="config-field">
        <label className="config-field__label">{label}</label>
        <select
          className="config-field__input"
          value={selected}
          onChange={(event) => onChange(event.target.value)}
        >
          {enumValues.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </div>
    );
  }

  if (type === "integer" || type === "number") {
    const numericValue = typeof value === "number"
      ? value
      : typeof defaultValue === "number" ? defaultValue : 0;
    return (
      <div className="config-field">
        <label className="config-field__label">{label}</label>
        <input
          className="config-field__input"
          type="number"
          step={type === "integer" ? 1 : "any"}
          value={numericValue}
          onChange={(event) => {
            if (event.target.value === "") {
              onChange(0);
              return;
            }
            const parsed = type === "integer"
              ? Number.parseInt(event.target.value, 10)
              : Number.parseFloat(event.target.value);
            if (Number.isFinite(parsed)) {
              onChange(parsed);
            }
          }}
        />
      </div>
    );
  }

  const stringValue = typeof value === "string" ? value : String(defaultValue ?? "");
  return (
    <div className="config-field">
      <label className="config-field__label">{label}</label>
      <input
        className="config-field__input"
        type="text"
        value={stringValue}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
