import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  KeyRound,
  Network,
  Pencil,
  Plus,
  Power,
  ServerCog,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useState, type FormEvent } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import {
  activateModelGateway,
  createModelGateway,
  deleteModelGateway,
  getRuntimeLogLevel,
  listModelGateways,
  updateRuntimeLogLevel,
  updateModelGateway,
} from "./api";
import type { ModelGateway, ModelGatewayCatalog, RuntimeLogLevel } from "./types";
import "./SettingsPage.css";

export const MODEL_GATEWAYS_QUERY_KEY = ["model-gateways"] as const;
const RUNTIME_LOG_LEVEL_QUERY_KEY = ["runtime-log-level"] as const;

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Unable to save the gateway.";
}

export function SettingsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [editingGatewayId, setEditingGatewayId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const gatewayQuery = useQuery({
    queryKey: MODEL_GATEWAYS_QUERY_KEY,
    queryFn: listModelGateways,
  });
  const logLevelQuery = useQuery({
    queryKey: RUNTIME_LOG_LEVEL_QUERY_KEY,
    queryFn: getRuntimeLogLevel,
  });
  const logLevelMutation = useMutation({
    mutationFn: updateRuntimeLogLevel,
    onSuccess: (settings) => {
      queryClient.setQueryData(RUNTIME_LOG_LEVEL_QUERY_KEY, settings);
    },
  });

  const updateCatalog = (catalog: ModelGatewayCatalog) => {
    queryClient.setQueryData(MODEL_GATEWAYS_QUERY_KEY, catalog);
  };
  const clearForm = () => {
    setEditingGatewayId(null);
    setName("");
    setApiKey("");
    setModel("");
    setBaseUrl("");
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const common = {
        name: name.trim(),
        model: model.trim(),
        base_url: baseUrl.trim(),
      };
      if (editingGatewayId === null) {
        return createModelGateway({ ...common, api_key: apiKey });
      }
      return updateModelGateway(editingGatewayId, {
        ...common,
        ...(apiKey.trim() === "" ? {} : { api_key: apiKey }),
      });
    },
    onSuccess: (catalog) => {
      updateCatalog(catalog);
      clearForm();
    },
  });
  const activateMutation = useMutation({
    mutationFn: activateModelGateway,
    onSuccess: updateCatalog,
  });
  const deleteMutation = useMutation({
    mutationFn: deleteModelGateway,
    onSuccess: (catalog, deletedGatewayId) => {
      updateCatalog(catalog);
      if (editingGatewayId === deletedGatewayId) {
        clearForm();
      }
    },
  });

  const gateways = gatewayQuery.data?.gateways ?? [];
  const isEditing = editingGatewayId !== null;
  const isSaveDisabled =
    name.trim() === "" ||
    model.trim() === "" ||
    baseUrl.trim() === "" ||
    (!isEditing && apiKey.trim() === "") ||
    saveMutation.isPending;

  function handleEdit(gateway: ModelGateway) {
    setEditingGatewayId(gateway.gateway_id);
    setName(gateway.name);
    setApiKey("");
    setModel(gateway.model);
    setBaseUrl(gateway.base_url);
  }

  function handleDelete(gateway: ModelGateway) {
    if (window.confirm(t("settings.deleteConfirm", { name: gateway.name }))) {
      deleteMutation.mutate(gateway.gateway_id);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isSaveDisabled) {
      saveMutation.mutate();
    }
  }

  const mutationError =
    saveMutation.error ?? activateMutation.error ?? deleteMutation.error ?? gatewayQuery.error ?? logLevelQuery.error ?? logLevelMutation.error;

  return (
    <section className="settings-page">
      <header className="settings-page__header">
        <div>
          <p className="settings-page__eyebrow">{t("settings.eyebrow")}</p>
          <h1>{t("settings.title")}</h1>
          <p>{t("settings.subtitle")}</p>
        </div>
        <div
          className={gateways.length > 0 ? "provider-state provider-state--ready" : "provider-state"}
          aria-live="polite"
        >
          <span className="provider-state__light" aria-hidden="true" />
          <span>
            <small>{t("settings.connectionState")}</small>
            <strong>
              {gateways.length > 0
                ? t("settings.configuredCount", { count: gateways.length })
                : t("settings.notConfigured")}
            </strong>
          </span>
        </div>
      </header>

      <div className="settings-page__layout">
        <div className="gateway-workbench">
          <section className="gateway-inventory">
            <header className="gateway-section-heading">
              <div>
                <p>{t("settings.inventoryStep")}</p>
                <h2>{t("settings.gatewayInventory")}</h2>
                <span>{t("settings.gatewayInventoryNote")}</span>
              </div>
              <span className="gateway-count">{String(gateways.length).padStart(2, "0")}</span>
            </header>

            {gatewayQuery.isPending ? <p className="gateway-empty">{t("common.loading")}</p> : null}
            {!gatewayQuery.isPending && gateways.length === 0 ? (
              <div className="gateway-empty">
                <ServerCog aria-hidden="true" />
                <strong>{t("settings.noGateways")}</strong>
                <span>{t("settings.noGatewaysNote")}</span>
              </div>
            ) : null}

            <div className="gateway-card-grid">
              {gateways.map((gateway) => (
                <article
                  className={gateway.is_active ? "gateway-card gateway-card--active" : "gateway-card"}
                  data-testid={`gateway-${gateway.gateway_id}`}
                  key={gateway.gateway_id}
                >
                  <header>
                    <span className="gateway-card__state">
                      <span aria-hidden="true" />
                      {gateway.is_active
                        ? t("settings.activeGateway")
                        : t("settings.inactiveGateway")}
                    </span>
                    <span className="gateway-card__index">
                      {String(gateways.indexOf(gateway) + 1).padStart(2, "0")}
                    </span>
                  </header>
                  <h3>{gateway.name}</h3>
                  <dl>
                    <div>
                      <dt>{t("settings.model")}</dt>
                      <dd>{gateway.model}</dd>
                    </div>
                    <div>
                      <dt>{t("settings.baseUrl")}</dt>
                      <dd>{gateway.base_url}</dd>
                    </div>
                  </dl>
                  <footer>
                    {!gateway.is_active ? (
                      <button
                        className="gateway-card__activate"
                        disabled={activateMutation.isPending}
                        type="button"
                        onClick={() => activateMutation.mutate(gateway.gateway_id)}
                      >
                        <Power aria-hidden="true" /> {t("settings.activate")}
                      </button>
                    ) : (
                      <span className="gateway-card__online">
                        <Check aria-hidden="true" /> {t("settings.online")}
                      </span>
                    )}
                    <button
                      aria-label={`${t("settings.editGateway")} ${gateway.name}`}
                      title={t("common.edit")}
                      type="button"
                      onClick={() => handleEdit(gateway)}
                    >
                      <Pencil aria-hidden="true" />
                    </button>
                    <button
                      aria-label={`${t("settings.deleteGateway")} ${gateway.name}`}
                      title={t("common.delete")}
                      type="button"
                      onClick={() => handleDelete(gateway)}
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  </footer>
                </article>
              ))}
            </div>
          </section>

          <form className="gateway-form" onSubmit={handleSubmit}>
            <div className="gateway-section-heading gateway-section-heading--form">
              <div>
                <p>{t("settings.configurationStep")}</p>
                <h2>{isEditing ? t("settings.updateGateway") : t("settings.addGateway")}</h2>
              </div>
              <Plus aria-hidden="true" />
            </div>
            <div className="gateway-form__fields">
              <label className="settings-field">
                <span className="settings-field__label">
                  <ServerCog aria-hidden="true" /> {t("settings.gatewayName")}
                </span>
                <input value={name} onChange={(event) => setName(event.currentTarget.value)} />
              </label>
              <label className="settings-field settings-field--secret">
                <span className="settings-field__label">
                  <KeyRound aria-hidden="true" /> {t("settings.apiKey")}
                </span>
                <input
                  aria-label={t("settings.apiKey")}
                  autoComplete="new-password"
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.currentTarget.value)}
                />
                <small>{isEditing ? t("settings.rotateKey") : t("settings.firstKey")}</small>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">
                  <Network aria-hidden="true" /> {t("settings.baseUrl")}
                </span>
                <input
                  inputMode="url"
                  placeholder="https://api.openai.com/v1"
                  type="url"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.currentTarget.value)}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field__label">
                  <ServerCog aria-hidden="true" /> {t("settings.model")}
                </span>
                <input value={model} onChange={(event) => setModel(event.currentTarget.value)} />
              </label>
            </div>

            {mutationError !== null ? (
              <div className="settings-alert" role="alert">
                {errorMessage(mutationError)}
              </div>
            ) : null}

            <footer className="gateway-form__footer">
              {isEditing ? (
                <button className="gateway-form__cancel" type="button" onClick={clearForm}>
                  {t("common.cancel")}
                </button>
              ) : (
                <span>{saveMutation.isSuccess ? t("settings.saved") : t("settings.secretWriteOnly")}</span>
              )}
              <button disabled={isSaveDisabled} type="submit">
                {isEditing ? t("common.save") : t("settings.addGateway")}
              </button>
            </footer>
          </form>
        </div>

        <aside className="security-rail">
          <label className="settings-field">
            <span className="settings-field__label">{t("settings.runtimeLogLevel")}</span>
            <select
              aria-label={t("settings.runtimeLogLevel")}
              disabled={logLevelQuery.isPending || logLevelMutation.isPending}
              value={logLevelQuery.data?.level ?? "info"}
              onChange={(event) => logLevelMutation.mutate(event.currentTarget.value as RuntimeLogLevel)}
            >
              <option value="debug">{t("settings.logDebug")}</option>
              <option value="info">{t("settings.logInfo")}</option>
              <option value="warning">{t("settings.logWarning")}</option>
              <option value="error">{t("settings.logError")}</option>
            </select>
          </label>
          <div className="security-rail__icon">
            <ShieldCheck aria-hidden="true" />
          </div>
          <p className="security-rail__label">{t("settings.securityBoundary")}</p>
          <h2>{t("settings.credentialHandling")}</h2>
          <ul>
            <li>{t("settings.security1")}</li>
            <li>{t("settings.security2")}</li>
            <li>{t("settings.security3")}</li>
            <li>{t("settings.security4")}</li>
          </ul>
          <p className="security-rail__warning">{t("settings.httpWarning")}</p>
        </aside>
      </div>
    </section>
  );
}
