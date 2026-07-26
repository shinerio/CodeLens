import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CheckCircle2,
  KeyRound,
  Network,
  Pencil,
  Plus,
  Plug,
  Power,
  ServerCog,
  SlidersHorizontal,
  Trash2,
  XCircle,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import {
  activateModelGateway,
  createModelGateway,
  deleteModelGateway,
  getInstructionFileSettings,
  getRecentRepositorySettings,
  getReviewCompletionSettings,
  getRuntimeLogLevel,
  listModelGateways,
  testGatewayAvailability,
  testGatewayConnectivity,
  updateRuntimeLogLevel,
  updateInstructionFileSettings,
  updateRecentRepositoryLimit,
  updateReviewCompletionSettings,
  updateModelGateway,
} from "./api";
import type {
  GatewayApiType,
  GatewayTestResult,
  ModelGateway,
  ModelGatewayCatalog,
  ModelProviderVendor,
  RuntimeLogLevel,
  ThinkingLevel,
} from "./types";
import "./SettingsPage.css";

export const MODEL_GATEWAYS_QUERY_KEY = ["model-gateways"] as const;
const RUNTIME_LOG_LEVEL_QUERY_KEY = ["runtime-log-level"] as const;
const RECENT_REPOSITORY_SETTINGS_QUERY_KEY = ["recent-repository-settings"] as const;
const INSTRUCTION_FILE_SETTINGS_QUERY_KEY = ["instruction-file-settings"] as const;
const REVIEW_COMPLETION_SETTINGS_QUERY_KEY = ["review-completion-settings"] as const;
const DEFAULT_AGENT_TIMEOUT = 1800;
const DEFAULT_MAX_AGENT_TURNS = 100;
const DEFAULT_MAX_TOOL_CALLS = 300;
const DEFAULT_MAX_IDENTICAL_TOOL_RESULTS = 3;
const DEFAULT_TOOL_TIMEOUT_SECONDS = 30;

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
  const [vendor, setVendor] = useState<ModelProviderVendor>("openai");
  const [apiType, setApiType] = useState<GatewayApiType>("chat_completions");
  const [maxTokens, setMaxTokens] = useState(65536);
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>("disabled");
  const [runtimeGatewayId, setRuntimeGatewayId] = useState("");
  const [agentTimeoutDraft, setAgentTimeoutDraft] = useState("1800");
  const [maxAgentTurnsDraft, setMaxAgentTurnsDraft] = useState("100");
  const [maxToolCallsDraft, setMaxToolCallsDraft] = useState("300");
  const [maxIdenticalToolResultsDraft, setMaxIdenticalToolResultsDraft] = useState("3");
  const [toolTimeoutSecondsDraft, setToolTimeoutSecondsDraft] = useState("30");
  const [recentRepositoryLimitDraft, setRecentRepositoryLimitDraft] = useState("10");
  const [rootInstructionLimitDraft, setRootInstructionLimitDraft] = useState("500");
  const [nestedInstructionLimitDraft, setNestedInstructionLimitDraft] = useState("200");
  const [incompleteReviewRetryLimitDraft, setIncompleteReviewRetryLimitDraft] = useState("3");
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
  const recentRepositorySettingsQuery = useQuery({
    queryKey: RECENT_REPOSITORY_SETTINGS_QUERY_KEY,
    queryFn: getRecentRepositorySettings,
  });
  const recentRepositorySettingsMutation = useMutation({
    mutationFn: updateRecentRepositoryLimit,
    onSuccess: (settings) => {
      queryClient.setQueryData(RECENT_REPOSITORY_SETTINGS_QUERY_KEY, settings);
      setRecentRepositoryLimitDraft(String(settings.recent_repository_limit));
      void queryClient.invalidateQueries({ queryKey: ["recent-repositories"] });
    },
  });
  const instructionFileSettingsQuery = useQuery({
    queryKey: INSTRUCTION_FILE_SETTINGS_QUERY_KEY,
    queryFn: getInstructionFileSettings,
  });
  const instructionFileSettingsMutation = useMutation({
    mutationFn: updateInstructionFileSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData(INSTRUCTION_FILE_SETTINGS_QUERY_KEY, settings);
      setRootInstructionLimitDraft(String(settings.root_max_lines));
      setNestedInstructionLimitDraft(String(settings.nested_max_lines));
    },
  });
  const gateways = useMemo(
    () => gatewayQuery.data?.gateways ?? [],
    [gatewayQuery.data?.gateways],
  );
  const runtimeGateway = useMemo(
    () =>
      gateways.find((gateway) => gateway.gateway_id === runtimeGatewayId) ??
      gateways.find((gateway) => gateway.is_active) ??
      gateways[0],
    [gateways, runtimeGatewayId],
  );
  const reviewCompletionSettingsQuery = useQuery({
    queryKey: REVIEW_COMPLETION_SETTINGS_QUERY_KEY,
    queryFn: getReviewCompletionSettings,
  });
  const reviewCompletionSettingsMutation = useMutation({
    mutationFn: updateReviewCompletionSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData(REVIEW_COMPLETION_SETTINGS_QUERY_KEY, settings);
      setIncompleteReviewRetryLimitDraft(String(settings.max_incomplete_review_retries));
    },
  });

  useEffect(() => {
    if (recentRepositorySettingsQuery.data !== undefined) {
      setRecentRepositoryLimitDraft(
        String(recentRepositorySettingsQuery.data.recent_repository_limit),
      );
    }
  }, [recentRepositorySettingsQuery.data]);

  useEffect(() => {
    if (instructionFileSettingsQuery.data !== undefined) {
      setRootInstructionLimitDraft(String(instructionFileSettingsQuery.data.root_max_lines));
      setNestedInstructionLimitDraft(String(instructionFileSettingsQuery.data.nested_max_lines));
    }
  }, [instructionFileSettingsQuery.data]);

  useEffect(() => {
    if (runtimeGateway === undefined) {
      return;
    }
    setRuntimeGatewayId(runtimeGateway.gateway_id);
    setAgentTimeoutDraft(String(runtimeGateway.agent_timeout ?? DEFAULT_AGENT_TIMEOUT));
    setMaxAgentTurnsDraft(
      String(runtimeGateway.max_agent_turns ?? DEFAULT_MAX_AGENT_TURNS),
    );
    setMaxToolCallsDraft(String(runtimeGateway.max_tool_calls ?? DEFAULT_MAX_TOOL_CALLS));
    setMaxIdenticalToolResultsDraft(
      String(
        runtimeGateway.max_identical_tool_results ?? DEFAULT_MAX_IDENTICAL_TOOL_RESULTS,
      ),
    );
    setToolTimeoutSecondsDraft(
      String(runtimeGateway.tool_timeout_seconds ?? DEFAULT_TOOL_TIMEOUT_SECONDS),
    );
  }, [runtimeGateway]);

  useEffect(() => {
    if (reviewCompletionSettingsQuery.data !== undefined) {
      setIncompleteReviewRetryLimitDraft(
        String(reviewCompletionSettingsQuery.data.max_incomplete_review_retries),
      );
    }
  }, [reviewCompletionSettingsQuery.data]);

  const updateCatalog = (catalog: ModelGatewayCatalog) => {
    queryClient.setQueryData(MODEL_GATEWAYS_QUERY_KEY, catalog);
  };
  const clearForm = () => {
    setEditingGatewayId(null);
    setName("");
    setApiKey("");
    setModel("");
    setBaseUrl("");
    setVendor("openai");
    setApiType("chat_completions");
    setMaxTokens(65536);
    setThinkingLevel("disabled");
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const common = {
        name: name.trim(),
        model: model.trim(),
        base_url: baseUrl.trim(),
        vendor,
        api_type: apiType,
        max_tokens: maxTokens,
        thinking_level: thinkingLevel,
        agent_timeout: DEFAULT_AGENT_TIMEOUT,
        max_agent_turns: DEFAULT_MAX_AGENT_TURNS,
        max_tool_calls: DEFAULT_MAX_TOOL_CALLS,
        max_identical_tool_results: DEFAULT_MAX_IDENTICAL_TOOL_RESULTS,
        tool_timeout_seconds: DEFAULT_TOOL_TIMEOUT_SECONDS,
      };
      if (editingGatewayId === null) {
        return createModelGateway({ ...common, api_key: apiKey });
      }
      return updateModelGateway(editingGatewayId, {
        ...common,
        agent_timeout:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)?.agent_timeout ??
          DEFAULT_AGENT_TIMEOUT,
        max_agent_turns:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)?.max_agent_turns ??
          DEFAULT_MAX_AGENT_TURNS,
        max_tool_calls:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)?.max_tool_calls ??
          DEFAULT_MAX_TOOL_CALLS,
        max_identical_tool_results:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)
            ?.max_identical_tool_results ?? DEFAULT_MAX_IDENTICAL_TOOL_RESULTS,
        tool_timeout_seconds:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)
            ?.tool_timeout_seconds ?? DEFAULT_TOOL_TIMEOUT_SECONDS,
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
  const [connectivityResults, setConnectivityResults] = useState<
    Record<string, GatewayTestResult>
  >({});
  const [availabilityResults, setAvailabilityResults] = useState<
    Record<string, GatewayTestResult>
  >({});
  const connectivityMutation = useMutation({
    mutationFn: testGatewayConnectivity,
    onSuccess: (result, gatewayId) => {
      setConnectivityResults((prev) => ({ ...prev, [gatewayId]: result }));
    },
  });
  const availabilityMutation = useMutation({
    mutationFn: testGatewayAvailability,
    onSuccess: (result, gatewayId) => {
      setAvailabilityResults((prev) => ({ ...prev, [gatewayId]: result }));
    },
  });

  const isEditing = editingGatewayId !== null;
  const isSaveDisabled =
    name.trim() === "" ||
    model.trim() === "" ||
    baseUrl.trim() === "" ||
    (!isEditing && apiKey.trim() === "") ||
    saveMutation.isPending;
  const parsedRecentRepositoryLimit = Number(recentRepositoryLimitDraft);
  const isRecentRepositoryLimitValid =
    Number.isInteger(parsedRecentRepositoryLimit) &&
    parsedRecentRepositoryLimit >= 1 &&
    parsedRecentRepositoryLimit <= 20;
  const isRecentRepositoryLimitUnchanged =
    parsedRecentRepositoryLimit ===
    recentRepositorySettingsQuery.data?.recent_repository_limit;
  const parsedRootInstructionLimit = Number(rootInstructionLimitDraft);
  const parsedNestedInstructionLimit = Number(nestedInstructionLimitDraft);
  const areInstructionLimitsValid =
    Number.isInteger(parsedRootInstructionLimit) &&
    parsedRootInstructionLimit >= 1 &&
    parsedRootInstructionLimit <= 10_000 &&
    Number.isInteger(parsedNestedInstructionLimit) &&
    parsedNestedInstructionLimit >= 1 &&
    parsedNestedInstructionLimit <= 10_000 &&
    parsedRootInstructionLimit >= parsedNestedInstructionLimit;
  const areInstructionLimitsUnchanged =
    parsedRootInstructionLimit === instructionFileSettingsQuery.data?.root_max_lines &&
    parsedNestedInstructionLimit === instructionFileSettingsQuery.data?.nested_max_lines;
  const parsedAgentTimeout = Number(agentTimeoutDraft);
  const parsedMaxAgentTurns = Number(maxAgentTurnsDraft);
  const parsedMaxToolCalls = Number(maxToolCallsDraft);
  const parsedMaxIdenticalToolResults = Number(maxIdenticalToolResultsDraft);
  const parsedToolTimeoutSeconds = Number(toolTimeoutSecondsDraft);
  const areExecutionLimitsValid =
    Number.isInteger(parsedAgentTimeout) &&
    parsedAgentTimeout >= 60 &&
    parsedAgentTimeout <= 7200 &&
    Number.isInteger(parsedMaxAgentTurns) &&
    parsedMaxAgentTurns >= 1 &&
    parsedMaxAgentTurns <= 500 &&
    Number.isInteger(parsedMaxToolCalls) &&
    parsedMaxToolCalls >= 1 &&
    parsedMaxToolCalls <= 5000 &&
    Number.isInteger(parsedMaxIdenticalToolResults) &&
    parsedMaxIdenticalToolResults >= 2 &&
    parsedMaxIdenticalToolResults <= 20 &&
    Number.isInteger(parsedToolTimeoutSeconds) &&
    parsedToolTimeoutSeconds >= 1 &&
    parsedToolTimeoutSeconds <= 300;
  const areExecutionLimitsUnchanged =
    runtimeGateway !== undefined &&
    parsedAgentTimeout === (runtimeGateway.agent_timeout ?? DEFAULT_AGENT_TIMEOUT) &&
    parsedMaxAgentTurns ===
      (runtimeGateway.max_agent_turns ?? DEFAULT_MAX_AGENT_TURNS) &&
    parsedMaxToolCalls === (runtimeGateway.max_tool_calls ?? DEFAULT_MAX_TOOL_CALLS) &&
    parsedMaxIdenticalToolResults ===
      (runtimeGateway.max_identical_tool_results ?? DEFAULT_MAX_IDENTICAL_TOOL_RESULTS) &&
    parsedToolTimeoutSeconds ===
      (runtimeGateway.tool_timeout_seconds ?? DEFAULT_TOOL_TIMEOUT_SECONDS);

  const executionLimitsMutation = useMutation({
    mutationFn: async () => {
      if (runtimeGateway === undefined) {
        throw new Error("No model gateway is selected.");
      }
      return updateModelGateway(runtimeGateway.gateway_id, {
        name: runtimeGateway.name,
        model: runtimeGateway.model,
        base_url: runtimeGateway.base_url,
        vendor: runtimeGateway.vendor ?? "openai",
        api_type: runtimeGateway.api_type ?? "chat_completions",
        max_tokens: runtimeGateway.max_tokens ?? 65536,
        thinking_level: runtimeGateway.thinking_level ?? "disabled",
        agent_timeout: parsedAgentTimeout,
        max_agent_turns: parsedMaxAgentTurns,
        max_tool_calls: parsedMaxToolCalls,
        max_identical_tool_results: parsedMaxIdenticalToolResults,
        tool_timeout_seconds: parsedToolTimeoutSeconds,
      });
    },
    onSuccess: updateCatalog,
  });
  const parsedIncompleteReviewRetryLimit = Number(incompleteReviewRetryLimitDraft);
  const isIncompleteReviewRetryLimitValid =
    Number.isInteger(parsedIncompleteReviewRetryLimit) &&
    parsedIncompleteReviewRetryLimit >= 0 &&
    parsedIncompleteReviewRetryLimit <= 20;
  const isIncompleteReviewRetryLimitUnchanged =
    parsedIncompleteReviewRetryLimit ===
    reviewCompletionSettingsQuery.data?.max_incomplete_review_retries;

  function handleEdit(gateway: ModelGateway) {
    setEditingGatewayId(gateway.gateway_id);
    setName(gateway.name);
    setApiKey("");
    setModel(gateway.model);
    setBaseUrl(gateway.base_url);
    setVendor(gateway.vendor);
    setApiType(gateway.api_type);
    setMaxTokens(gateway.max_tokens);
    setThinkingLevel(gateway.thinking_level);
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
    saveMutation.error ??
    executionLimitsMutation.error ??
    activateMutation.error ??
    deleteMutation.error ??
    connectivityMutation.error ??
    availabilityMutation.error ??
    gatewayQuery.error ??
    logLevelQuery.error ??
    logLevelMutation.error ??
    recentRepositorySettingsQuery.error ??
    recentRepositorySettingsMutation.error ??
    instructionFileSettingsQuery.error ??
    instructionFileSettingsMutation.error ??
    reviewCompletionSettingsQuery.error ??
    reviewCompletionSettingsMutation.error;

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
        <main className="settings-main">
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
                  {(connectivityResults[gateway.gateway_id] ||
                    availabilityResults[gateway.gateway_id] ||
                    connectivityMutation.isPending ||
                    availabilityMutation.isPending) &&
                  (connectivityMutation.variables === gateway.gateway_id ||
                    availabilityMutation.variables === gateway.gateway_id ||
                    connectivityResults[gateway.gateway_id] ||
                    availabilityResults[gateway.gateway_id]) ? (
                    <div className="gateway-card__test-bar">
                      {connectivityMutation.isPending &&
                      connectivityMutation.variables === gateway.gateway_id ? (
                        <span className="gateway-card__test-result gateway-card__test-result--pending">
                          {t("settings.testing")}
                        </span>
                      ) : connectivityResults[gateway.gateway_id] ? (
                        <span
                          className={
                            connectivityResults[gateway.gateway_id].ok
                              ? "gateway-card__test-result gateway-card__test-result--ok"
                              : "gateway-card__test-result gateway-card__test-result--fail"
                          }
                        >
                          {connectivityResults[gateway.gateway_id].ok ? (
                            <CheckCircle2 aria-hidden="true" />
                          ) : (
                            <XCircle aria-hidden="true" />
                          )}
                          {connectivityResults[gateway.gateway_id].ok
                            ? t("settings.connectivityOk", {
                                latency_ms: connectivityResults[gateway.gateway_id].latency_ms ?? 0,
                              })
                            : t("settings.connectivityFail")}
                        </span>
                      ) : null}
                      {availabilityMutation.isPending &&
                      availabilityMutation.variables === gateway.gateway_id ? (
                        <span className="gateway-card__test-result gateway-card__test-result--pending">
                          {t("settings.testing")}
                        </span>
                      ) : availabilityResults[gateway.gateway_id] ? (
                        <span
                          className={
                            availabilityResults[gateway.gateway_id].ok
                              ? "gateway-card__test-result gateway-card__test-result--ok"
                              : "gateway-card__test-result gateway-card__test-result--fail"
                          }
                        >
                          {availabilityResults[gateway.gateway_id].ok ? (
                            <CheckCircle2 aria-hidden="true" />
                          ) : (
                            <XCircle aria-hidden="true" />
                          )}
                          {availabilityResults[gateway.gateway_id].ok
                            ? t("settings.availabilityOk", {
                                latency_ms: availabilityResults[gateway.gateway_id].latency_ms ?? 0,
                              })
                            : t("settings.availabilityFail")}
                        </span>
                      ) : null}
                    </div>
                  ) : null}
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
                      className="gateway-card__test-btn"
                      aria-label={`${t("settings.testConnectivity")} ${gateway.name}`}
                      title={t("settings.testConnectivity")}
                      type="button"
                      disabled={connectivityMutation.isPending}
                      onClick={() => connectivityMutation.mutate(gateway.gateway_id)}
                    >
                      <Plug aria-hidden="true" />
                    </button>
                    <button
                      className="gateway-card__test-btn"
                      aria-label={`${t("settings.testAvailability")} ${gateway.name}`}
                      title={t("settings.testAvailability")}
                      type="button"
                      disabled={availabilityMutation.isPending}
                      onClick={() => availabilityMutation.mutate(gateway.gateway_id)}
                    >
                      <Zap aria-hidden="true" />
                    </button>
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
                  <ServerCog aria-hidden="true" /> Provider
                </span>
                <select value={vendor} onChange={(event) => {
                  const next = event.currentTarget.value as ModelProviderVendor;
                  setVendor(next);
                  if (next === "deepseek" || next === "zhipu") setApiType("chat_completions");
                }}>
                  <option value="openai">OpenAI-compatible</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="zhipu">Zhipu (GLM)</option>
                </select>
                <small>{vendor === "deepseek" ? "Uses DeepSeek thinking and Chat Completions semantics." : vendor === "zhipu" ? "Uses GLM thinking and Chat Completions semantics." : "Uses OpenAI SDK request semantics."}</small>
              </label>
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
              <label className="settings-field">
                <span className="settings-field__label">
                  <ServerCog aria-hidden="true" /> {t("settings.apiType")}
                </span>
                <select
                  value={apiType}
                  disabled={vendor === "deepseek" || vendor === "zhipu"}
                  onChange={(event) => setApiType(event.currentTarget.value as GatewayApiType)}
                >
                  <option value="chat_completions">Chat Completions</option>
                  <option value="responses">Responses</option>
                </select>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">
                  <ServerCog aria-hidden="true" /> {t("settings.maxTokens")}
                </span>
                <input
                  type="number"
                  min={256}
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(Number(event.currentTarget.value) || 4096)}
                />
                <small>{t("settings.maxTokensHint")}</small>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">
                  <ServerCog aria-hidden="true" /> {t("settings.thinkingLevel")}
                </span>
                <select
                  value={thinkingLevel}
                  onChange={(event) => setThinkingLevel(event.currentTarget.value as ThinkingLevel)}
                >
                  <option value="disabled">{t("settings.thinkingDisabled")}</option>
                  <option value="low">{t("settings.thinkingLow")}</option>
                  <option value="medium">{t("settings.thinkingMedium")}</option>
                  <option value="high">{t("settings.thinkingHigh")}</option>
                </select>
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
        </main>

        <aside className="runtime-rail">
          <div className="local-preferences">
            <p className="local-preferences__heading">
              <SlidersHorizontal aria-hidden="true" /> {t("settings.localPreferences")}
            </p>
            <div className="local-preferences__execution-fields">
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.executionModel")}</span>
                <select
                  aria-label={t("settings.executionModel")}
                  disabled={gateways.length === 0}
                  value={runtimeGateway?.gateway_id ?? ""}
                  onChange={(event) => setRuntimeGatewayId(event.currentTarget.value)}
                >
                  {gateways.map((gateway) => (
                    <option key={gateway.gateway_id} value={gateway.gateway_id}>
                      {gateway.name} · {gateway.model}
                    </option>
                  ))}
                </select>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.agentTimeout")}</span>
                <input
                  aria-label={t("settings.agentTimeout")}
                  disabled={runtimeGateway === undefined}
                  min={60}
                  max={7200}
                  type="number"
                  value={agentTimeoutDraft}
                  onChange={(event) => setAgentTimeoutDraft(event.currentTarget.value)}
                />
                <small>{t("settings.agentTimeoutHint")}</small>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.maxAgentTurns")}</span>
                <input
                  aria-label={t("settings.maxAgentTurns")}
                  disabled={runtimeGateway === undefined}
                  min={1}
                  max={500}
                  type="number"
                  value={maxAgentTurnsDraft}
                  onChange={(event) => setMaxAgentTurnsDraft(event.currentTarget.value)}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.maxToolCalls")}</span>
                <input
                  aria-label={t("settings.maxToolCalls")}
                  disabled={runtimeGateway === undefined}
                  min={1}
                  max={5000}
                  type="number"
                  value={maxToolCallsDraft}
                  onChange={(event) => setMaxToolCallsDraft(event.currentTarget.value)}
                />
              </label>
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.maxIdenticalToolResults")}</span>
                <input
                  aria-label={t("settings.maxIdenticalToolResults")}
                  disabled={runtimeGateway === undefined}
                  min={2}
                  max={20}
                  type="number"
                  value={maxIdenticalToolResultsDraft}
                  onChange={(event) =>
                    setMaxIdenticalToolResultsDraft(event.currentTarget.value)
                  }
                />
                <small>{t("settings.maxIdenticalToolResultsHint")}</small>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.toolTimeoutSeconds")}</span>
                <input
                  aria-label={t("settings.toolTimeoutSeconds")}
                  disabled={runtimeGateway === undefined}
                  min={1}
                  max={300}
                  type="number"
                  value={toolTimeoutSecondsDraft}
                  onChange={(event) => setToolTimeoutSecondsDraft(event.currentTarget.value)}
                />
              </label>
              <button
                className="local-preferences__save-limits"
                disabled={executionLimitsMutation.isPending || runtimeGateway === undefined || !areExecutionLimitsValid || areExecutionLimitsUnchanged}
                type="button"
                onClick={() => executionLimitsMutation.mutate()}
              >
                <Check aria-hidden="true" />
                {t("settings.saveExecutionLimits")}
              </button>
            </div>
            <label className="settings-field">
              <span className="settings-field__label">{t("settings.runtimeLogLevel")}</span>
              <select
                aria-label={t("settings.runtimeLogLevel")}
                disabled={logLevelQuery.isPending || logLevelMutation.isPending}
                value={logLevelQuery.data?.level ?? "info"}
                onChange={(event) =>
                  logLevelMutation.mutate(event.currentTarget.value as RuntimeLogLevel)
                }
              >
                <option value="debug">{t("settings.logDebug")}</option>
                <option value="info">{t("settings.logInfo")}</option>
                <option value="warning">{t("settings.logWarning")}</option>
                <option value="error">{t("settings.logError")}</option>
              </select>
            </label>

            <div className="settings-field">
              <label className="settings-field__label" htmlFor="recent-repository-limit">
                {t("settings.recentRepositoryLimit")}
              </label>
              <div className="local-preferences__number-control">
                <input
                  aria-label={t("settings.recentRepositoryLimit")}
                  disabled={recentRepositorySettingsQuery.isPending}
                  id="recent-repository-limit"
                  inputMode="numeric"
                  max={20}
                  min={1}
                  step={1}
                  type="number"
                  value={recentRepositoryLimitDraft}
                  onChange={(event) => setRecentRepositoryLimitDraft(event.currentTarget.value)}
                />
                <button
                  aria-label={t("settings.saveRecentRepositoryLimit")}
                  disabled={
                    recentRepositorySettingsMutation.isPending ||
                    !isRecentRepositoryLimitValid ||
                    isRecentRepositoryLimitUnchanged
                  }
                  title={t("settings.saveRecentRepositoryLimit")}
                  type="button"
                  onClick={() =>
                    recentRepositorySettingsMutation.mutate(parsedRecentRepositoryLimit)
                  }
                >
                  <Check aria-hidden="true" />
                </button>
              </div>
              <small>{t("settings.recentRepositoryLimitHint")}</small>
            </div>

            <div className="local-preferences__instruction-fields">
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.rootInstructionLimit")}</span>
                <input
                  aria-label={t("settings.rootInstructionLimit")}
                  disabled={instructionFileSettingsQuery.isPending}
                  inputMode="numeric"
                  max={10_000}
                  min={1}
                  step={1}
                  type="number"
                  value={rootInstructionLimitDraft}
                  onChange={(event) => setRootInstructionLimitDraft(event.currentTarget.value)}
                />
                <small>{t("settings.recommendedLines", { count: 500 })}</small>
              </label>

              <label className="settings-field">
                <span className="settings-field__label">{t("settings.nestedInstructionLimit")}</span>
                <input
                  aria-label={t("settings.nestedInstructionLimit")}
                  disabled={instructionFileSettingsQuery.isPending}
                  inputMode="numeric"
                  max={10_000}
                  min={1}
                  step={1}
                  type="number"
                  value={nestedInstructionLimitDraft}
                  onChange={(event) => setNestedInstructionLimitDraft(event.currentTarget.value)}
                />
                <small>{t("settings.recommendedLines", { count: 200 })}</small>
              </label>

              <small className="local-preferences__instruction-hint">
                {t("settings.instructionLimitHint")}
              </small>
              <button
                className="local-preferences__save-limits"
                disabled={
                  instructionFileSettingsMutation.isPending ||
                  !areInstructionLimitsValid ||
                  areInstructionLimitsUnchanged
                }
                type="button"
                onClick={() =>
                  instructionFileSettingsMutation.mutate({
                    root_max_lines: parsedRootInstructionLimit,
                    nested_max_lines: parsedNestedInstructionLimit,
                  })
                }
              >
                <Check aria-hidden="true" />
                {t("settings.saveInstructionLimits")}
              </button>
            </div>

            <div className="settings-field">
              <label className="settings-field__label" htmlFor="incomplete-review-retry-limit">
                {t("settings.incompleteReviewRetryLimit")}
              </label>
              <div className="local-preferences__number-control">
                <input
                  aria-label={t("settings.incompleteReviewRetryLimit")}
                  disabled={reviewCompletionSettingsQuery.isPending}
                  id="incomplete-review-retry-limit"
                  inputMode="numeric"
                  max={20}
                  min={0}
                  step={1}
                  type="number"
                  value={incompleteReviewRetryLimitDraft}
                  onChange={(event) =>
                    setIncompleteReviewRetryLimitDraft(event.currentTarget.value)
                  }
                />
                <button
                  aria-label={t("settings.saveIncompleteReviewRetryLimit")}
                  disabled={
                    reviewCompletionSettingsMutation.isPending ||
                    !isIncompleteReviewRetryLimitValid ||
                    isIncompleteReviewRetryLimitUnchanged
                  }
                  title={t("settings.saveIncompleteReviewRetryLimit")}
                  type="button"
                  onClick={() =>
                    reviewCompletionSettingsMutation.mutate({
                      max_incomplete_review_retries: parsedIncompleteReviewRetryLimit,
                    })
                  }
                >
                  <Check aria-hidden="true" />
                </button>
              </div>
              <small>{t("settings.incompleteReviewRetryLimitHint")}</small>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}
