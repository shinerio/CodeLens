import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CheckCircle2,
  KeyRound,
  Pencil,
  Plus,
  Plug,
  Power,
  ServerCog,
  SlidersHorizontal,
  Trash2,
  X,
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
  getToolLimits,
  getTriggerIdempotencySettings,
  listModelGateways,
  resetAllSettings,
  testGatewayAvailability,
  testGatewayConnectivity,
  updateRuntimeLogLevel,
  updateInstructionFileSettings,
  updateRecentRepositoryLimit,
  updateReviewCompletionSettings,
  updateModelGateway,
  updateToolLimits,
  updateTriggerIdempotencySettings,
} from "./api";
import type { ToolLimits as ToolLimitsType } from "./types";
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
const TRIGGER_IDEMPOTENCY_SETTINGS_QUERY_KEY = ["trigger-idempotency-settings"] as const;
const DEFAULT_AGENT_TIMEOUT = 1800;
const DEFAULT_MAX_AGENT_TURNS = 100;
const DEFAULT_MAX_TOOL_CALLS = 300;
const DEFAULT_MAX_IDENTICAL_TOOL_RESULTS = 3;
const DEFAULT_TOOL_TIMEOUT_SECONDS = 30;
const DEFAULT_MAX_RETRIES = 10;
const DEFAULT_RETRY_BACKOFF_BASE = 1.0;
const DEFAULT_RETRY_MAX_DELAY = 30.0;
const BYTES_PER_KILOBYTE = 1024;

export function SettingsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [showGatewayModal, setShowGatewayModal] = useState(false);
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
  const [maxRetriesDraft, setMaxRetriesDraft] = useState("10");
  const [retryBackoffBaseDraft, setRetryBackoffBaseDraft] = useState("1");
  const [retryMaxDelayDraft, setRetryMaxDelayDraft] = useState("30");
  const [recentRepositoryLimitDraft, setRecentRepositoryLimitDraft] = useState("10");
  const [rootInstructionLimitDraft, setRootInstructionLimitDraft] = useState("500");
  const [nestedInstructionLimitDraft, setNestedInstructionLimitDraft] = useState("200");
  const [incompleteReviewRetryLimitDraft, setIncompleteReviewRetryLimitDraft] = useState("3");
  const [triggerIdempotencyEnabledDraft, setTriggerIdempotencyEnabledDraft] = useState(false);
  const [toolLimitsDraft, setToolLimitsDraft] = useState<ToolLimitsType | null>(null);
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
  const triggerIdempotencySettingsQuery = useQuery({
    queryKey: TRIGGER_IDEMPOTENCY_SETTINGS_QUERY_KEY,
    queryFn: getTriggerIdempotencySettings,
  });
  const triggerIdempotencySettingsMutation = useMutation({
    mutationFn: updateTriggerIdempotencySettings,
    onSuccess: (settings) => {
      queryClient.setQueryData(TRIGGER_IDEMPOTENCY_SETTINGS_QUERY_KEY, settings);
      setTriggerIdempotencyEnabledDraft(settings.enabled);
    },
  });
  const toolLimitsQuery = useQuery({
    queryKey: ["tool-limits"],
    queryFn: getToolLimits,
  });
  const toolLimitsMutation = useMutation({
    mutationFn: updateToolLimits,
    onSuccess: (limits) => {
      queryClient.setQueryData(["tool-limits"], limits);
      setToolLimitsDraft(limits);
    },
  });
  const resetAllMutation = useMutation({
    mutationFn: resetAllSettings,
    onSuccess: (response) => {
      queryClient.setQueryData(INSTRUCTION_FILE_SETTINGS_QUERY_KEY, response.instruction_files);
      queryClient.setQueryData(REVIEW_COMPLETION_SETTINGS_QUERY_KEY, response.review_completion);
      queryClient.setQueryData(TRIGGER_IDEMPOTENCY_SETTINGS_QUERY_KEY, response.trigger_idempotency);
      queryClient.setQueryData(RECENT_REPOSITORY_SETTINGS_QUERY_KEY, response.recent_repositories);
      queryClient.setQueryData(["tool-limits"], response.tool_limits);
      queryClient.setQueryData(RUNTIME_LOG_LEVEL_QUERY_KEY, response.logging);
      queryClient.setQueryData(MODEL_GATEWAYS_QUERY_KEY, response.model_gateways);
      setToolLimitsDraft(response.tool_limits);
      setRootInstructionLimitDraft(String(response.instruction_files.root_max_lines));
      setNestedInstructionLimitDraft(String(response.instruction_files.nested_max_lines));
      setIncompleteReviewRetryLimitDraft(String(response.review_completion.max_incomplete_review_retries));
      setTriggerIdempotencyEnabledDraft(response.trigger_idempotency.enabled);
      setRecentRepositoryLimitDraft(String(response.recent_repositories.recent_repository_limit));
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
    setMaxRetriesDraft(String(runtimeGateway.max_retries ?? DEFAULT_MAX_RETRIES));
    setRetryBackoffBaseDraft(
      String(runtimeGateway.retry_backoff_base ?? DEFAULT_RETRY_BACKOFF_BASE),
    );
    setRetryMaxDelayDraft(
      String(runtimeGateway.retry_max_delay ?? DEFAULT_RETRY_MAX_DELAY),
    );
  }, [runtimeGateway]);

  useEffect(() => {
    if (reviewCompletionSettingsQuery.data !== undefined) {
      setIncompleteReviewRetryLimitDraft(
        String(reviewCompletionSettingsQuery.data.max_incomplete_review_retries),
      );
    }
  }, [reviewCompletionSettingsQuery.data]);

  useEffect(() => {
    if (triggerIdempotencySettingsQuery.data !== undefined) {
      setTriggerIdempotencyEnabledDraft(triggerIdempotencySettingsQuery.data.enabled);
    }
  }, [triggerIdempotencySettingsQuery.data]);

  useEffect(() => {
    if (toolLimitsQuery.data !== undefined) {
      setToolLimitsDraft(toolLimitsQuery.data);
    }
  }, [toolLimitsQuery.data]);

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
        max_retries: DEFAULT_MAX_RETRIES,
        retry_backoff_base: DEFAULT_RETRY_BACKOFF_BASE,
        retry_max_delay: DEFAULT_RETRY_MAX_DELAY,
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
        max_retries:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)?.max_retries ??
          DEFAULT_MAX_RETRIES,
        retry_backoff_base:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)?.retry_backoff_base ??
          DEFAULT_RETRY_BACKOFF_BASE,
        retry_max_delay:
          gateways.find((gateway) => gateway.gateway_id === editingGatewayId)?.retry_max_delay ??
          DEFAULT_RETRY_MAX_DELAY,
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
  const parsedMaxRetries = Number(maxRetriesDraft);
  const parsedRetryBackoffBase = Number(retryBackoffBaseDraft);
  const parsedRetryMaxDelay = Number(retryMaxDelayDraft);
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
    parsedToolTimeoutSeconds <= 300 &&
    Number.isInteger(parsedMaxRetries) &&
    parsedMaxRetries >= 0 &&
    parsedMaxRetries <= 10 &&
    parsedRetryBackoffBase >= 0.1 &&
    parsedRetryBackoffBase <= 60 &&
    parsedRetryMaxDelay >= 1 &&
    parsedRetryMaxDelay <= 300;
  const areExecutionLimitsUnchanged =
    runtimeGateway !== undefined &&
    parsedAgentTimeout === (runtimeGateway.agent_timeout ?? DEFAULT_AGENT_TIMEOUT) &&
    parsedMaxAgentTurns ===
      (runtimeGateway.max_agent_turns ?? DEFAULT_MAX_AGENT_TURNS) &&
    parsedMaxToolCalls === (runtimeGateway.max_tool_calls ?? DEFAULT_MAX_TOOL_CALLS) &&
    parsedMaxIdenticalToolResults ===
      (runtimeGateway.max_identical_tool_results ?? DEFAULT_MAX_IDENTICAL_TOOL_RESULTS) &&
    parsedToolTimeoutSeconds ===
      (runtimeGateway.tool_timeout_seconds ?? DEFAULT_TOOL_TIMEOUT_SECONDS) &&
    parsedMaxRetries === (runtimeGateway.max_retries ?? DEFAULT_MAX_RETRIES) &&
    parsedRetryBackoffBase ===
      (runtimeGateway.retry_backoff_base ?? DEFAULT_RETRY_BACKOFF_BASE) &&
    parsedRetryMaxDelay === (runtimeGateway.retry_max_delay ?? DEFAULT_RETRY_MAX_DELAY);

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
        max_retries: parsedMaxRetries,
        retry_backoff_base: parsedRetryBackoffBase,
        retry_max_delay: parsedRetryMaxDelay,
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
  const isTriggerIdempotencyUnchanged =
    triggerIdempotencyEnabledDraft === triggerIdempotencySettingsQuery.data?.enabled;
  const areReviewSettingsValid =
    isRecentRepositoryLimitValid &&
    areInstructionLimitsValid &&
    isIncompleteReviewRetryLimitValid;
  const areReviewSettingsUnchanged =
    isRecentRepositoryLimitUnchanged &&
    areInstructionLimitsUnchanged &&
    isIncompleteReviewRetryLimitUnchanged &&
    isTriggerIdempotencyUnchanged;
  const areReviewSettingsPending =
    recentRepositorySettingsQuery.isPending ||
    instructionFileSettingsQuery.isPending ||
    reviewCompletionSettingsQuery.isPending ||
    triggerIdempotencySettingsQuery.isPending ||
    recentRepositorySettingsMutation.isPending ||
    instructionFileSettingsMutation.isPending ||
    reviewCompletionSettingsMutation.isPending ||
    triggerIdempotencySettingsMutation.isPending;
  const areToolLimitsValid =
    toolLimitsDraft !== null &&
    toolLimitsDraft.context_compaction_target_bytes <
      toolLimitsDraft.context_compaction_trigger_bytes;
  const areToolLimitsUnchanged =
    toolLimitsDraft === null ||
    JSON.stringify(toolLimitsDraft) === JSON.stringify(toolLimitsQuery.data);
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
    setShowGatewayModal(true);
  }

  function handleSaveReviewSettings() {
    if (!isRecentRepositoryLimitUnchanged) {
      recentRepositorySettingsMutation.mutate(parsedRecentRepositoryLimit);
    }
    if (!areInstructionLimitsUnchanged) {
      instructionFileSettingsMutation.mutate({
        root_max_lines: parsedRootInstructionLimit,
        nested_max_lines: parsedNestedInstructionLimit,
      });
    }
    if (!isIncompleteReviewRetryLimitUnchanged) {
      reviewCompletionSettingsMutation.mutate({
        max_incomplete_review_retries: parsedIncompleteReviewRetryLimit,
      });
    }
    if (!isTriggerIdempotencyUnchanged) {
      triggerIdempotencySettingsMutation.mutate({
        enabled: triggerIdempotencyEnabledDraft,
      });
    }
  }

  function handleAddGateway() {
    setEditingGatewayId(null);
    setName("");
    setApiKey("");
    setModel("");
    setBaseUrl("");
    setVendor("openai");
    setApiType("chat_completions");
    setMaxTokens(65536);
    setThinkingLevel("disabled");
    setShowGatewayModal(true);
  }

  function closeModal() {
    setShowGatewayModal(false);
    setEditingGatewayId(null);
    setName("");
    setApiKey("");
    setModel("");
    setBaseUrl("");
    setVendor("openai");
    setApiType("chat_completions");
    setMaxTokens(65536);
    setThinkingLevel("disabled");
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

  return (
    <section className="settings-page">
      <header className="settings-page__header">
        <div>
          <p className="settings-page__eyebrow">{t("settings.eyebrow")}</p>
          <h1>{t("settings.title")}</h1>
          <p>{t("settings.subtitle")}</p>
        </div>
        <a className="settings-profile-link" href="/settings/review-profiles">{t("settings.reviewProfiles")}</a>
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
              <button
                className="gateway-card gateway-card--add"
                type="button"
                onClick={handleAddGateway}
              >
                <Plus aria-hidden="true" />
                <span>{t("settings.addGateway")}</span>
              </button>
            </div>
          </section>

          {/* Agent Execution Panel */}
          <section className="settings-panel">
            <header className="settings-panel__header">
              <Zap className="settings-panel__icon" aria-hidden="true" />
              <h2 className="settings-panel__title">{t("settings.executionLimits")}</h2>
            </header>
            <div className="settings-panel__grid">
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
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.maxRetries")}</span>
                <input
                  aria-label={t("settings.maxRetries")}
                  disabled={runtimeGateway === undefined}
                  min={0}
                  max={10}
                  step={1}
                  type="number"
                  value={maxRetriesDraft}
                  onChange={(event) => setMaxRetriesDraft(event.currentTarget.value)}
                />
                <small>{t("settings.maxRetriesHint")}</small>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.retryBackoffBase")}</span>
                <input
                  aria-label={t("settings.retryBackoffBase")}
                  disabled={runtimeGateway === undefined}
                  min={0.1}
                  max={60}
                  step={0.1}
                  type="number"
                  value={retryBackoffBaseDraft}
                  onChange={(event) => setRetryBackoffBaseDraft(event.currentTarget.value)}
                />
                <small>{t("settings.retryBackoffBaseHint")}</small>
              </label>
              <label className="settings-field">
                <span className="settings-field__label">{t("settings.retryMaxDelay")}</span>
                <input
                  aria-label={t("settings.retryMaxDelay")}
                  disabled={runtimeGateway === undefined}
                  min={1}
                  max={300}
                  step={1}
                  type="number"
                  value={retryMaxDelayDraft}
                  onChange={(event) => setRetryMaxDelayDraft(event.currentTarget.value)}
                />
                <small>{t("settings.retryMaxDelayHint")}</small>
              </label>
            </div>
            <div className="settings-panel__actions">
              <button
                className="settings-panel__save-button"
                disabled={executionLimitsMutation.isPending || runtimeGateway === undefined || !areExecutionLimitsValid || areExecutionLimitsUnchanged}
                type="button"
                onClick={() => executionLimitsMutation.mutate()}
              >
                <Check aria-hidden="true" />
                {t("settings.saveExecutionLimits")}
              </button>
            </div>
          </section>

          {/* Review Settings Panel */}
          <section className="settings-panel">
            <header className="settings-panel__header">
              <SlidersHorizontal className="settings-panel__icon" aria-hidden="true" />
              <h2 className="settings-panel__title">{t("settings.reviewSettings")}</h2>
            </header>
            <div className="settings-panel__grid">
              <div className="settings-field">
                <label className="settings-field__label" htmlFor="recent-repository-limit">
                  {t("settings.recentRepositoryLimit")}
                </label>
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
                <small>{t("settings.recentRepositoryLimitHint")}</small>
              </div>

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

              <div className="settings-field">
                <label className="settings-field__label" htmlFor="incomplete-review-retry-limit">
                  {t("settings.incompleteReviewRetryLimit")}
                </label>
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
                <small>{t("settings.incompleteReviewRetryLimitHint")}</small>
              </div>

              <label className="settings-field settings-field--checkbox">
                <span>
                  <input
                    aria-label={t("settings.triggerIdempotency")}
                    type="checkbox"
                    checked={triggerIdempotencyEnabledDraft}
                    onChange={(event) =>
                      setTriggerIdempotencyEnabledDraft(event.currentTarget.checked)
                    }
                    disabled={triggerIdempotencySettingsQuery.isPending}
                  />
                  {t("settings.triggerIdempotency")}
                </span>
                <small>{t("settings.triggerIdempotencyHint")}</small>
              </label>
            </div>
            <div className="settings-panel__actions">
              <button
                className="settings-panel__save-button"
                disabled={
                  areReviewSettingsPending ||
                  !areReviewSettingsValid ||
                  areReviewSettingsUnchanged
                }
                type="button"
                onClick={handleSaveReviewSettings}
              >
                <Check aria-hidden="true" />
                {t("settings.saveReviewSettings")}
              </button>
            </div>
          </section>

          {/* Tool Limits Panel */}
          {toolLimitsDraft !== null && (
            <section className="settings-panel">
              <header className="settings-panel__header">
                <SlidersHorizontal className="settings-panel__icon" aria-hidden="true" />
                <h2 className="settings-panel__title">{t("settings.toolLimits")}</h2>
              </header>
              <small style={{ display: "block", marginBottom: "16px", color: "#aeb9b0" }}>
                {t("settings.toolLimitsHint")}
              </small>
              <div className="settings-panel__grid">
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxResults")}</span>
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    value={toolLimitsDraft.max_results}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, max_results: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxReadBytes")}</span>
                  <input
                    type="number"
                    min={1}
                    max={10240}
                    value={toolLimitsDraft.max_read_bytes / BYTES_PER_KILOBYTE}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, max_read_bytes: Number(e.currentTarget.value) * BYTES_PER_KILOBYTE })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxScanBytes")}</span>
                  <input
                    type="number"
                    min={1}
                    max={102400}
                    value={toolLimitsDraft.max_scan_bytes / BYTES_PER_KILOBYTE}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, max_scan_bytes: Number(e.currentTarget.value) * BYTES_PER_KILOBYTE })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxSourceBytes")}</span>
                  <input
                    type="number"
                    min={1}
                    max={102400}
                    value={toolLimitsDraft.max_source_bytes / BYTES_PER_KILOBYTE}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, max_source_bytes: Number(e.currentTarget.value) * BYTES_PER_KILOBYTE })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxLines")}</span>
                  <input
                    type="number"
                    min={1}
                    max={100000}
                    value={toolLimitsDraft.max_lines}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, max_lines: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxPathChars")}</span>
                  <input
                    type="number"
                    min={100}
                    max={10000}
                    value={toolLimitsDraft.max_path_chars}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, max_path_chars: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxPatternChars")}</span>
                  <input
                    type="number"
                    min={10}
                    max={10000}
                    value={toolLimitsDraft.max_pattern_chars}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, max_pattern_chars: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.regexTimeoutSeconds")}</span>
                  <input
                    type="number"
                    min={1}
                    max={300}
                    step={0.1}
                    value={toolLimitsDraft.regex_timeout_seconds}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, regex_timeout_seconds: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.commentBatchSize")}</span>
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    value={toolLimitsDraft.comment_batch_size}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, comment_batch_size: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.shortTextMax")}</span>
                  <input
                    type="number"
                    min={10}
                    max={10000}
                    value={toolLimitsDraft.short_text_max}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, short_text_max: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.longTextMax")}</span>
                  <input
                    type="number"
                    min={100}
                    max={100000}
                    value={toolLimitsDraft.long_text_max}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, long_text_max: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.taskSummaryMax")}</span>
                  <input
                    type="number"
                    min={100}
                    max={100000}
                    value={toolLimitsDraft.task_summary_max}
                    onChange={(e) =>
                      setToolLimitsDraft({ ...toolLimitsDraft, task_summary_max: Number(e.currentTarget.value) })
                    }
                  />
                </label>
                <label className="settings-field settings-field--toggle">
                  <span className="settings-field__label">{t("settings.contextCompactionEnabled")}</span>
                  <input
                    aria-label={t("settings.contextCompactionEnabled")}
                    className="settings-field__checkbox"
                    type="checkbox"
                    checked={toolLimitsDraft.context_compaction_enabled}
                    onChange={(event) =>
                      setToolLimitsDraft({
                        ...toolLimitsDraft,
                        context_compaction_enabled: event.currentTarget.checked,
                      })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.contextCompactionTriggerBytes")}</span>
                  <input
                    type="number"
                    min={1}
                    max={102400}
                    value={toolLimitsDraft.context_compaction_trigger_bytes / BYTES_PER_KILOBYTE}
                    onChange={(event) =>
                      setToolLimitsDraft({
                        ...toolLimitsDraft,
                        context_compaction_trigger_bytes:
                          Number(event.currentTarget.value) * BYTES_PER_KILOBYTE,
                      })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.contextCompactionTargetBytes")}</span>
                  <input
                    type="number"
                    min={1}
                    max={102400}
                    value={toolLimitsDraft.context_compaction_target_bytes / BYTES_PER_KILOBYTE}
                    onChange={(event) =>
                      setToolLimitsDraft({
                        ...toolLimitsDraft,
                        context_compaction_target_bytes:
                          Number(event.currentTarget.value) * BYTES_PER_KILOBYTE,
                      })
                    }
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.contextCompactionKeepRecent")}</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={toolLimitsDraft.context_compaction_keep_recent_evidence_results}
                    onChange={(event) =>
                      setToolLimitsDraft({
                        ...toolLimitsDraft,
                        context_compaction_keep_recent_evidence_results: Number(
                          event.currentTarget.value,
                        ),
                      })
                    }
                  />
                </label>
              </div>
              <div className="settings-panel__actions">
                <button
                  className="settings-panel__save-button"
                  disabled={
                    toolLimitsMutation.isPending ||
                    !areToolLimitsValid ||
                    areToolLimitsUnchanged
                  }
                  type="button"
                  onClick={() => {
                    if (toolLimitsDraft !== null) {
                      toolLimitsMutation.mutate(toolLimitsDraft);
                    }
                  }}
                >
                  <Check aria-hidden="true" />
                  {t("settings.saveToolLimits")}
                </button>
              </div>
            </section>
          )}

          {/* System Settings Panel */}
          <section className="settings-panel">
            <header className="settings-panel__header">
              <SlidersHorizontal className="settings-panel__icon" aria-hidden="true" />
              <h2 className="settings-panel__title">{t("settings.systemSettings")}</h2>
            </header>
            <div className="settings-panel__grid">
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
            </div>
            <div className="settings-panel__actions">
              <button
                className="settings-panel__save-button"
                disabled={resetAllMutation.isPending}
                type="button"
                onClick={() => {
                  if (window.confirm(t("settings.resetAllConfirm"))) {
                    resetAllMutation.mutate();
                  }
                }}
              >
                {t("settings.resetAll")}
              </button>
            </div>
          </section>
        </main>
      </div>

      {showGatewayModal && (
        <div className="gateway-modal-overlay" onClick={closeModal}>
          <div className="gateway-modal" onClick={(e) => e.stopPropagation()}>
            <header className="gateway-modal__header">
              <h2>{isEditing ? t("settings.updateGateway") : t("settings.addGateway")}</h2>
              <button onClick={closeModal} type="button"><X aria-hidden="true" /></button>
            </header>
            <form onSubmit={handleSubmit}>
              <div className="gateway-modal__fields">
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.provider")}</span>
                  <select
                    value={vendor}
                    onChange={(event) => setVendor(event.currentTarget.value as ModelProviderVendor)}
                  >
                    <option value="openai">OpenAI</option>
                    <option value="deepseek">DeepSeek</option>
                    <option value="zhipu">智谱 AI</option>
                  </select>
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.gatewayName")}</span>
                  <input
                    aria-label={t("settings.gatewayName")}
                    onChange={(event) => setName(event.currentTarget.value)}
                    placeholder={t("settings.gatewayNamePlaceholder")}
                    required
                    type="text"
                    value={name}
                  />
                </label>
                <label className="settings-field settings-field--secret">
                  <span className="settings-field__label">
                    <KeyRound aria-hidden="true" />
                    {t("settings.apiKey")}
                  </span>
                  <input
                    aria-label={t("settings.apiKey")}
                    autoComplete="off"
                    onChange={(event) => setApiKey(event.currentTarget.value)}
                    placeholder={isEditing ? t("settings.apiKeyPreserved") : "sk-..."}
                    required={!isEditing}
                    spellCheck={false}
                    type="password"
                    value={apiKey}
                  />
                  <small>{isEditing ? t("settings.apiKeyEditHint") : t("settings.apiKeyNote")}</small>
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.baseUrl")}</span>
                  <input
                    aria-label={t("settings.baseUrl")}
                    onChange={(event) => setBaseUrl(event.currentTarget.value)}
                    placeholder="https://api.openai.com/v1"
                    required
                    type="url"
                    value={baseUrl}
                  />
                  <small>{t("settings.baseUrlNote")}</small>
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.model")}</span>
                  <input
                    aria-label={t("settings.model")}
                    onChange={(event) => setModel(event.currentTarget.value)}
                    placeholder="gpt-4o"
                    required
                    type="text"
                    value={model}
                  />
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.apiType")}</span>
                  <select
                    value={apiType}
                    onChange={(event) => setApiType(event.currentTarget.value as GatewayApiType)}
                  >
                    <option value="chat_completions">Chat Completions</option>
                    <option value="responses">Responses</option>
                  </select>
                  <small>{t("settings.apiTypeNote")}</small>
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.maxTokens")}</span>
                  <input
                    aria-label={t("settings.maxTokens")}
                    min={1024}
                    onChange={(event) => setMaxTokens(Number(event.currentTarget.value))}
                    required
                    step={1024}
                    type="number"
                    value={maxTokens}
                  />
                  <small>{t("settings.maxTokensNote")}</small>
                </label>
                <label className="settings-field">
                  <span className="settings-field__label">{t("settings.thinkingLevel")}</span>
                  <select
                    value={thinkingLevel}
                    onChange={(event) => setThinkingLevel(event.currentTarget.value as ThinkingLevel)}
                  >
                    <option value="disabled">{t("settings.thinkingDisabled")}</option>
                    <option value="low">{t("settings.thinkingLow")}</option>
                    <option value="medium">{t("settings.thinkingMedium")}</option>
                    <option value="high">{t("settings.thinkingHigh")}</option>
                  </select>
                  <small>{t("settings.thinkingLevelNote")}</small>
                </label>
              </div>
              <footer className="gateway-modal__footer">
                <span>
                  {saveMutation.isPending
                    ? t("common.saving")
                    : isEditing
                      ? t("settings.editingGateway")
                      : t("settings.newGateway")}
                </span>
                <button
                  className="gateway-modal__cancel"
                  disabled={saveMutation.isPending}
                  onClick={closeModal}
                  type="button"
                >
                  {t("common.cancel")}
                </button>
                <button disabled={isSaveDisabled} type="submit">
                  {isEditing ? t("common.save") : t("settings.addGateway")}
                </button>
              </footer>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
