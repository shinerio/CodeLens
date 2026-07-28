import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, ExternalLink, Package, Power, Trash2 } from "lucide-react";
import { useState } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import {
  disablePlugin,
  enablePlugin,
  installPlugin,
  listPlugins,
  PLUGIN_QUERY_KEY,
  setAutoExport,
  uninstallPlugin,
  updatePluginConfig,
} from "./api";
import type { PluginRecord } from "./types";
import "./ReportPluginsPage.css";

export function ReportPluginsPage() {
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

  const enableMutation = useMutation({
    mutationFn: (pluginId: string) => enablePlugin(pluginId),
    onSuccess: invalidatePlugins,
  });

  const disableMutation = useMutation({
    mutationFn: (pluginId: string) => disablePlugin(pluginId),
    onSuccess: invalidatePlugins,
  });

  const autoExportMutation = useMutation({
    mutationFn: ({ pluginId, enabled }: { pluginId: string; enabled: boolean }) =>
      setAutoExport(pluginId, { enabled }),
    onSuccess: invalidatePlugins,
  });

  const uninstallMutation = useMutation({
    mutationFn: (pluginId: string) => uninstallPlugin(pluginId),
    onSuccess: invalidatePlugins,
  });

  const installMutation = useMutation({
    mutationFn: () => installPlugin({ git_url: gitUrl, ref: gitRef || null }),
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
    <section className="report-plugins-page">
      <header className="report-plugins-header">
        <div>
          <h1>{t("reportPlugins.title")}</h1>
          <p className="report-plugins-subtitle">{t("reportPlugins.subtitle")}</p>
        </div>
      </header>

      <div className="report-plugins-install">
        <h2 className="report-plugins-section-title">{t("reportPlugins.installNew")}</h2>
        <div className="install-form">
          <input
            className="install-input install-input--url"
            placeholder={t("reportPlugins.gitUrlPlaceholder")}
            value={gitUrl}
            onChange={(e) => setGitUrl(e.target.value)}
            disabled={installMutation.isPending}
          />
          <input
            className="install-input install-input--ref"
            placeholder={t("reportPlugins.refPlaceholder")}
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
            {t("reportPlugins.install")}
          </button>
        </div>
        {installError && <p className="install-error">{installError}</p>}
      </div>

      {pluginsQuery.isLoading && <p className="report-plugins-loading">{t("common.loading")}</p>}
      {pluginsQuery.error && (
        <p className="report-plugins-error">
          {t("reportPlugins.loadFailed")}: {(pluginsQuery.error as Error).message}
        </p>
      )}

      <div className="plugin-cards">
        {plugins.map((plugin) => (
          <PluginCard
            key={plugin.plugin_id}
            plugin={plugin}
            onEnable={(id) => enableMutation.mutate(id)}
            onDisable={(id) => disableMutation.mutate(id)}
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
  onEnable: (pluginId: string) => void;
  onDisable: (pluginId: string) => void;
  onToggleAutoExport: (pluginId: string, enabled: boolean) => void;
  onUninstall: (pluginId: string) => void;
  onConfigUpdate: () => void;
};

function PluginCard({
  plugin,
  onEnable,
  onDisable,
  onToggleAutoExport,
  onUninstall,
  onConfigUpdate,
}: PluginCardProps) {
  const { t } = useI18n();
  const [configDraft, setConfigDraft] = useState<Record<string, unknown>>(plugin.config);

  const configMutation = useMutation({
    mutationFn: (request: { config: Record<string, unknown> }) =>
      updatePluginConfig(plugin.plugin_id, request),
    onSuccess: () => {
      onConfigUpdate();
    },
  });

  const configProperties = extractConfigProperties(plugin.manifest.config_schema);
  const configChanged = JSON.stringify(configDraft) !== JSON.stringify(plugin.config);

  return (
    <article className={`plugin-card ${plugin.is_enabled ? "plugin-card--enabled" : ""}`}>
      <div className="plugin-card__header">
        <div className="plugin-card__title-row">
          <Package aria-hidden="true" className="plugin-card__icon" />
          <div>
            <h3 className="plugin-card__name">{plugin.manifest.name}</h3>
            <span className="plugin-card__version">v{plugin.manifest.version}</span>
            {plugin.is_builtin && <span className="plugin-card__badge plugin-card__badge--builtin">{t("reportPlugins.builtin")}</span>}
          </div>
        </div>
        <div className="plugin-card__actions">
          <button
            className={`plugin-toggle ${plugin.is_enabled ? "plugin-toggle--on" : ""}`}
            disabled={configMutation.isPending}
            onClick={() => (plugin.is_enabled ? onDisable(plugin.plugin_id) : onEnable(plugin.plugin_id))}
          >
            <Power aria-hidden="true" />
            {plugin.is_enabled ? t("reportPlugins.enabled") : t("reportPlugins.disabled")}
          </button>
          {!plugin.is_builtin && (
            <button
              className="plugin-uninstall"
              disabled={configMutation.isPending}
              onClick={() => onUninstall(plugin.plugin_id)}
            >
              <Trash2 aria-hidden="true" />
              {t("reportPlugins.uninstall")}
            </button>
          )}
        </div>
      </div>

      <p className="plugin-card__description">{plugin.manifest.description}</p>

      {configProperties.length > 0 && (
        <div className="plugin-config">
          <h4 className="plugin-config__title">{t("reportPlugins.configuration")}</h4>
          {configProperties.map((prop) => (
            <ConfigField
              key={prop.key}
              label={prop.label}
              value={configDraft[prop.key]}
              defaultValue={prop.default}
              type={prop.type}
              onChange={(value) => setConfigDraft({ ...configDraft, [prop.key]: value })}
            />
          ))}
          <button
            className="plugin-config__save"
            disabled={!configChanged || configMutation.isPending}
            onClick={() => configMutation.mutate({ config: configDraft })}
          >
            <Check aria-hidden="true" />
            {t("reportPlugins.saveConfig")}
          </button>
        </div>
      )}

      <div className="plugin-card__auto-export">
        <label className="auto-export-toggle">
          <input
            type="checkbox"
            checked={plugin.auto_export}
            onChange={(e) => onToggleAutoExport(plugin.plugin_id, e.target.checked)}
            disabled={!plugin.is_enabled}
          />
          <span>{t("reportPlugins.autoExport")}</span>
        </label>
        <span className="auto-export-hint">{t("reportPlugins.autoExportHint")}</span>
      </div>

      {plugin.install_path && (
        <div className="plugin-card__install-path">
          <ExternalLink aria-hidden="true" className="plugin-card__path-icon" />
          <code>{plugin.install_path}</code>
        </div>
      )}
    </article>
  );
}

type ConfigProperty = {
  key: string;
  label: string;
  type: string;
  default: unknown;
};

function extractConfigProperties(schema: Record<string, unknown>): ConfigProperty[] {
  const properties = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  return Object.entries(properties).map(([key, prop]) => ({
    key,
    label: typeof prop.description === "string" ? prop.description : key,
    type: typeof prop.type === "string" ? prop.type : "string",
    default: prop.default,
  }));
}

type ConfigFieldProps = {
  label: string;
  value: unknown;
  defaultValue: unknown;
  type: string;
  onChange: (value: unknown) => void;
};

function ConfigField({ label, value, defaultValue, type, onChange }: ConfigFieldProps) {
  if (type === "array" && Array.isArray(defaultValue)) {
    const options = defaultValue as string[];
    const current = Array.isArray(value) ? value : options;
    return (
      <div className="config-field config-field--array">
        <label className="config-field__label">{label}</label>
        <div className="config-field__checkboxes">
          {options.map((option) => (
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

  const stringValue = typeof value === "string" ? value : String(defaultValue ?? "");
  return (
    <div className="config-field">
      <label className="config-field__label">{label}</label>
      <input
        className="config-field__input"
        type={type === "number" ? "number" : "text"}
        value={stringValue}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
