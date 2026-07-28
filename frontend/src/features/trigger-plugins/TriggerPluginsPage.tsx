import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FolderGit2, Plus, Power, Webhook, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import { RepositoryBrowser } from "../repositories/RepositoryBrowser";
import {
  disableTriggerPlugin,
  enableTriggerPlugin,
  getHookStatus,
  listTriggerPlugins,
  TRIGGER_PLUGIN_QUERY_KEY,
  updateTriggerConfig,
} from "./api";
import type { HookStatusResponse, TriggerConfig, TriggerRecord } from "./types";
import "./TriggerPluginsPage.css";

const AVAILABLE_AGENTS = [
  { reference: "correctness:v1", labelKey: "review.correctness", enabled: true },
  { reference: "security:v1", labelKey: "review.security", enabled: false },
  { reference: "performance:v1", labelKey: "review.performance", enabled: false },
  { reference: "maintainability:v1", labelKey: "review.maintainability", enabled: false },
] as const;

export function TriggerPluginsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();

  const pluginsQuery = useQuery({
    queryKey: TRIGGER_PLUGIN_QUERY_KEY,
    queryFn: listTriggerPlugins,
  });

  const invalidatePlugins = () => {
    void queryClient.invalidateQueries({ queryKey: TRIGGER_PLUGIN_QUERY_KEY });
  };

  const enableMutation = useMutation({
    mutationFn: (pluginId: string) => enableTriggerPlugin(pluginId),
    onSuccess: invalidatePlugins,
  });

  const disableMutation = useMutation({
    mutationFn: (pluginId: string) => disableTriggerPlugin(pluginId),
    onSuccess: invalidatePlugins,
  });

  const plugins = pluginsQuery.data ?? [];

  return (
    <section className="trigger-plugins-page">
      <header className="trigger-plugins-header">
        <div>
          <h1>{t("triggerPlugins.title")}</h1>
          <p className="trigger-plugins-subtitle">{t("triggerPlugins.subtitle")}</p>
        </div>
      </header>

      {pluginsQuery.isLoading && <p className="trigger-plugins-loading">{t("common.loading")}</p>}
      {pluginsQuery.error && (
        <p className="trigger-plugins-error">
          {t("triggerPlugins.loadFailed")}: {(pluginsQuery.error as Error).message}
        </p>
      )}

      <div className="trigger-plugin-cards">
        {plugins.map((plugin) => (
          <TriggerPluginCard
            key={plugin.plugin_id}
            plugin={plugin}
            onEnable={(id) => enableMutation.mutate(id)}
            onDisable={(id) => disableMutation.mutate(id)}
            onConfigUpdate={invalidatePlugins}
          />
        ))}
      </div>
    </section>
  );
}

type TriggerPluginCardProps = {
  plugin: TriggerRecord;
  onEnable: (pluginId: string) => void;
  onDisable: (pluginId: string) => void;
  onConfigUpdate: () => void;
};

function TriggerPluginCard({
  plugin,
  onEnable,
  onDisable,
  onConfigUpdate,
}: TriggerPluginCardProps) {
  const { t } = useI18n();
  const [configDraft, setConfigDraft] = useState<TriggerConfig>(plugin.config);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [hookStatus, setHookStatus] = useState<HookStatusResponse[]>([]);
  const [error, setError] = useState<string | null>(null);

  const configMutation = useMutation({
    mutationFn: (config: Partial<TriggerConfig>) =>
      updateTriggerConfig(plugin.plugin_id, config),
    onSuccess: () => {
      setError(null);
      onConfigUpdate();
      refreshHookStatus();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const configChanged = JSON.stringify(configDraft) !== JSON.stringify(plugin.config);

  function refreshHookStatus() {
    if (configDraft.repository_paths.length > 0) {
      getHookStatus(plugin.plugin_id, configDraft.repository_paths).then(setHookStatus);
    }
  }

  // Auto-fetch hook status on mount
  useEffect(() => {
    if (plugin.is_enabled && plugin.config.repository_paths.length > 0) {
      refreshHookStatus();
    }
  }, [plugin.plugin_id, plugin.is_enabled, plugin.config.repository_paths]);

  function handleRepositorySelect(path: string) {
    if (!configDraft.repository_paths.includes(path)) {
      setConfigDraft({
        ...configDraft,
        repository_paths: [...configDraft.repository_paths, path],
      });
    }
    setBrowserOpen(false);
  }

  function handleRemoveRepository(path: string) {
    setConfigDraft({
      ...configDraft,
      repository_paths: configDraft.repository_paths.filter((p) => p !== path),
    });
  }

  function handleEventToggle(event: string, checked: boolean) {
    const events = checked
      ? [...configDraft.events, event]
      : configDraft.events.filter((e) => e !== event);
    setConfigDraft({ ...configDraft, events });
  }

  function handleScopeChange(scopeType: string) {
    setConfigDraft({ ...configDraft, scope_type: scopeType });
  }

  function handleAgentToggle(reference: string, checked: boolean) {
    const agents = checked
      ? [...configDraft.selected_agents, reference]
      : configDraft.selected_agents.filter((a) => a !== reference);
    setConfigDraft({ ...configDraft, selected_agents: agents });
  }

  return (
    <article className={`trigger-plugin-card ${plugin.is_enabled ? "trigger-plugin-card--enabled" : ""}`}>
      <div className="trigger-plugin-card__header">
        <div className="trigger-plugin-card__title-row">
          <Webhook aria-hidden="true" className="trigger-plugin-card__icon" />
          <div>
            <h3 className="trigger-plugin-card__name">{plugin.manifest.name}</h3>
            <span className="trigger-plugin-card__version">v{plugin.manifest.version}</span>
            {plugin.is_builtin && (
              <span className="trigger-plugin-card__badge trigger-plugin-card__badge--builtin">
                {t("triggerPlugins.builtin")}
              </span>
            )}
          </div>
        </div>
        <div className="trigger-plugin-card__actions">
          <button
            className={`trigger-plugin-toggle ${plugin.is_enabled ? "trigger-plugin-toggle--on" : ""}`}
            onClick={() => (plugin.is_enabled ? onDisable(plugin.plugin_id) : onEnable(plugin.plugin_id))}
          >
            <Power aria-hidden="true" />
            {plugin.is_enabled ? t("triggerPlugins.enabled") : t("triggerPlugins.disabled")}
          </button>
        </div>
      </div>

      <p className="trigger-plugin-card__description">{plugin.manifest.description}</p>

      <div className="trigger-plugin-config">
        <h4 className="trigger-plugin-config__title">{t("triggerPlugins.configuration")}</h4>

        <div className="trigger-config-section">
          <label className="trigger-config-section__label">{t("triggerPlugins.repositoryPaths")}</label>
          <div className="trigger-config-repositories">
            {configDraft.repository_paths.map((path) => (
              <div key={path} className="trigger-config-repository">
                <FolderGit2 aria-hidden="true" />
                <code>{path}</code>
                <button
                  className="trigger-config-repository__remove"
                  onClick={() => handleRemoveRepository(path)}
                  type="button"
                >
                  <X aria-hidden="true" />
                  {t("triggerPlugins.removeRepository")}
                </button>
              </div>
            ))}
          </div>
          <button
            className="trigger-config-add-repo"
            onClick={() => setBrowserOpen(true)}
            type="button"
          >
            <Plus aria-hidden="true" />
            {t("triggerPlugins.addRepository")}
          </button>
        </div>

        <div className="trigger-config-section">
          <label className="trigger-config-section__label">{t("triggerPlugins.events")}</label>
          <div className="trigger-config-checkboxes">
            {plugin.manifest.supported_events.map((event) => (
              <label key={event} className="trigger-config-checkbox">
                <input
                  type="checkbox"
                  checked={configDraft.events.includes(event)}
                  onChange={(e) => handleEventToggle(event, e.target.checked)}
                />
                <span>{event}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="trigger-config-section">
          <label className="trigger-config-section__label">{t("triggerPlugins.selectedAgents")}</label>
          <div className="trigger-config-checkboxes">
            {AVAILABLE_AGENTS.map((agent) => (
              <label
                key={agent.reference}
                className={`trigger-config-checkbox ${!agent.enabled ? "trigger-config-checkbox--disabled" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={configDraft.selected_agents.includes(agent.reference)}
                  disabled={!agent.enabled}
                  onChange={(e) => handleAgentToggle(agent.reference, e.target.checked)}
                />
                <span>{t(agent.labelKey)}</span>
                {!agent.enabled && (
                  <span className="trigger-config-agent-badge">{t("triggerPlugins.comingSoon")}</span>
                )}
              </label>
            ))}
          </div>
          {configDraft.selected_agents.length === 0 && (
            <p className="trigger-plugin-error">{t("triggerPlugins.noAgentSelected")}</p>
          )}
        </div>

        <div className="trigger-config-section">
          <label className="trigger-config-section__label">{t("triggerPlugins.scopeType")}</label>
          <div className="trigger-config-radios">
            {(["commit", "branch", "uncommitted"] as const).map((scope) => (
              <label key={scope} className="trigger-config-radio">
                <input
                  type="radio"
                  name={`scope-${plugin.plugin_id}`}
                  checked={configDraft.scope_type === scope}
                  onChange={() => handleScopeChange(scope)}
                />
                <span>{t(`triggerPlugins.scope.${scope}`)}</span>
              </label>
            ))}
          </div>
        </div>

        {configDraft.scope_type === "branch" && (
          <>
            <div className="trigger-config-section">
              <label className="trigger-config-section__label">{t("triggerPlugins.baseRef")}</label>
              <input
                className="trigger-config-input"
                type="text"
                value={configDraft.base_ref ?? ""}
                onChange={(e) => setConfigDraft({ ...configDraft, base_ref: e.target.value || null })}
                placeholder="main"
              />
            </div>
            <div className="trigger-config-section">
              <label className="trigger-config-section__label">{t("triggerPlugins.targetRef")}</label>
              <input
                className="trigger-config-input"
                type="text"
                value={configDraft.target_ref ?? ""}
                onChange={(e) => setConfigDraft({ ...configDraft, target_ref: e.target.value || null })}
                placeholder="feature-branch"
              />
            </div>
          </>
        )}

        <div className="trigger-config-section">
          <label className="trigger-config-section__label">{t("triggerPlugins.locale")}</label>
          <select
            className="trigger-config-select"
            value={configDraft.prompt_locale}
            onChange={(e) => setConfigDraft({ ...configDraft, prompt_locale: e.target.value })}
          >
            <option value="en">English</option>
            <option value="zh-CN">中文</option>
          </select>
        </div>

        <div className="trigger-config-section">
          <label className="trigger-config-section__label">{t("triggerPlugins.debounce")}</label>
          <input
            className="trigger-config-input"
            type="number"
            min="0"
            value={configDraft.debounce_seconds}
            onChange={(e) =>
              setConfigDraft({ ...configDraft, debounce_seconds: parseInt(e.target.value) || 0 })
            }
          />
        </div>

        <button
          className="trigger-plugin-config__save"
          disabled={!configChanged || configMutation.isPending}
          onClick={() => {
            const { extra, ...updateRequest } = configDraft;
            configMutation.mutate(updateRequest);
          }}
          type="button"
        >
          <Check aria-hidden="true" />
          {t("triggerPlugins.saveConfig")}
        </button>
      </div>

      {plugin.is_enabled && configDraft.repository_paths.length > 0 && (
        <div className="trigger-plugin-hooks">
          <h4 className="trigger-plugin-hooks__title">{t("triggerPlugins.hooks")}</h4>
          <div className="trigger-hook-status">
            {hookStatus.map((status) => (
              <div key={status.repository_path} className="trigger-hook-status__item">
                <span className="trigger-hook-status__repo">{status.repository_path}</span>
                <span
                  className={`trigger-hook-status__state ${
                    Object.values(status.hooks).some(Boolean)
                      ? "trigger-hook-status__state--installed"
                      : "trigger-hook-status__state--not-installed"
                  }`}
                >
                  {Object.values(status.hooks).some(Boolean)
                    ? t("triggerPlugins.hookInstalled")
                    : t("triggerPlugins.hookNotInstalled")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {error && <p className="trigger-plugin-error">{error}</p>}

      <RepositoryBrowser
        isOpen={browserOpen}
        onClose={() => setBrowserOpen(false)}
        onSelect={handleRepositorySelect}
      />
    </article>
  );
}
