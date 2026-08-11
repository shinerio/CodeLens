import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import StringConstraints

from codelens.bootstrap.logging import get_runtime_log_level, set_runtime_log_level
from codelens.interface.http.dependencies import HttpComponents, HttpProblem, get_components
from codelens.interface.http.dto import (
    ActivateModelGatewayRequest,
    CreateModelGatewayRequest,
    FileExclusionSettingsResponse,
    GatewayAvailabilityTestResponse,
    GatewayConnectivityTestResponse,
    InstructionFileSettingsResponse,
    ModelGatewayCatalogResponse,
    ModelGatewayResponse,
    RecentRepositorySettingsResponse,
    ResetAllSettingsResponse,
    ReviewCompletionSettingsResponse,
    RuntimeLogLevelResponse,
    ToolLimitsResponse,
    TriggerIdempotencySettingsResponse,
    UpdateFileExclusionSettingsRequest,
    UpdateInstructionFileSettingsRequest,
    UpdateModelGatewayRequest,
    UpdateRecentRepositorySettingsRequest,
    UpdateReviewCompletionSettingsRequest,
    UpdateRuntimeLogLevelRequest,
    UpdateToolLimitsRequest,
    UpdateTriggerIdempotencySettingsRequest,
)
from codelens.review.domain.tool_limits import ToolLimits
from codelens.reviewer_catalog.application.provider_settings import ModelGatewayCatalogView
from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy

router = APIRouter(prefix="/api/settings", tags=["settings"])
_LOGGER = logging.getLogger("codelens.settings")

GatewayId = Annotated[
    str,
    StringConstraints(pattern=r"^gateway_[A-Za-z0-9_-]{3,64}$", max_length=72),
]


def _instruction_settings_response(
    root_max_lines: int,
    nested_max_lines: int,
) -> InstructionFileSettingsResponse:
    return InstructionFileSettingsResponse(
        root_max_lines=root_max_lines,
        nested_max_lines=nested_max_lines,
    )


def _catalog_response(view: ModelGatewayCatalogView) -> ModelGatewayCatalogResponse:
    return ModelGatewayCatalogResponse(
        active_gateway_id=view.active_gateway_id,
        gateways=[
            ModelGatewayResponse(
                gateway_id=gateway.gateway_id,
                name=gateway.name,
                model=gateway.model,
                base_url=gateway.base_url,
                vendor=gateway.vendor,
                is_active=gateway.is_active,
                api_type=gateway.api_type,
                max_tokens=gateway.max_tokens,
                thinking_level=gateway.thinking_level,
                agent_timeout=gateway.agent_timeout,
                max_agent_turns=gateway.max_agent_turns,
                max_tool_calls=gateway.max_tool_calls,
                max_identical_tool_results=gateway.max_identical_tool_results,
                tool_timeout_seconds=gateway.tool_timeout_seconds,
                max_retries=gateway.max_retries,
                retry_backoff_base=gateway.retry_backoff_base,
                retry_max_delay=gateway.retry_max_delay,
            )
            for gateway in view.gateways
        ],
    )


def _tool_limits_response(limits: ToolLimits) -> ToolLimitsResponse:
    return ToolLimitsResponse(
        max_results=limits.max_results,
        max_read_bytes=limits.max_read_bytes,
        max_scan_bytes=limits.max_scan_bytes,
        max_source_bytes=limits.max_source_bytes,
        max_lines=limits.max_lines,
        max_path_chars=limits.max_path_chars,
        max_pattern_chars=limits.max_pattern_chars,
        regex_timeout_seconds=limits.regex_timeout_seconds,
        comment_batch_size=limits.comment_batch_size,
        short_text_max=limits.short_text_max,
        long_text_max=limits.long_text_max,
        task_summary_max=limits.task_summary_max,
        context_compaction_enabled=limits.context_compaction_enabled,
        context_compaction_trigger_bytes=limits.context_compaction_trigger_bytes,
        context_compaction_target_bytes=limits.context_compaction_target_bytes,
        context_compaction_keep_recent_evidence_results=(
            limits.context_compaction_keep_recent_evidence_results
        ),
    )


def _file_exclusion_response(
    policy: ReviewFileExclusionPolicy,
) -> FileExclusionSettingsResponse:
    return FileExclusionSettingsResponse(
        suffixes=list(policy.suffixes),
        path_regexes=list(policy.path_regexes),
        exclude_binary=policy.exclude_binary,
    )


@router.get("/file-exclusions", response_model=FileExclusionSettingsResponse)
async def get_file_exclusions(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> FileExclusionSettingsResponse:
    """Return the file exclusion policy used for newly created Reviews."""

    return _file_exclusion_response(await components.file_exclusion_settings.get())


@router.put("/file-exclusions", response_model=FileExclusionSettingsResponse)
async def update_file_exclusions(
    request: UpdateFileExclusionSettingsRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> FileExclusionSettingsResponse:
    """Validate and atomically update the file exclusion policy."""

    try:
        policy = await components.file_exclusion_settings.update(
            suffixes=None if request.suffixes is None else tuple(request.suffixes),
            path_regexes=(None if request.path_regexes is None else tuple(request.path_regexes)),
            exclude_binary=request.exclude_binary,
        )
    except ValueError as error:
        raise HttpProblem(422, "invalid_file_exclusion_policy", str(error)) from error
    return _file_exclusion_response(policy)


@router.get("/logging", response_model=RuntimeLogLevelResponse)
async def get_runtime_log_level_setting(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> RuntimeLogLevelResponse:
    """Return the persisted runtime log threshold without exposing log contents."""

    level = await asyncio.to_thread(get_runtime_log_level, components.settings.data_dir)
    return RuntimeLogLevelResponse(level=level)


@router.put("/logging", response_model=RuntimeLogLevelResponse)
async def update_runtime_log_level_setting(
    request: UpdateRuntimeLogLevelRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> RuntimeLogLevelResponse:
    """Persist a shared threshold used by every process on its next log event."""

    await asyncio.to_thread(set_runtime_log_level, components.settings.data_dir, request.level)
    _LOGGER.info("Runtime log level updated", extra={"log_level": request.level})
    return RuntimeLogLevelResponse(level=request.level)


@router.get("/repositories", response_model=RecentRepositorySettingsResponse)
async def get_recent_repository_settings(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> RecentRepositorySettingsResponse:
    """Return the persisted recent repository LRU capacity."""

    limit = await components.get_recent_repository_settings.handle()
    return RecentRepositorySettingsResponse(recent_repository_limit=limit)


@router.put("/repositories", response_model=RecentRepositorySettingsResponse)
async def update_recent_repository_settings(
    request: UpdateRecentRepositorySettingsRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> RecentRepositorySettingsResponse:
    """Persist the recent repository capacity and prune overflow immediately."""

    limit = await components.update_recent_repository_settings.handle(
        request.recent_repository_limit
    )
    return RecentRepositorySettingsResponse(recent_repository_limit=limit)


@router.get("/instruction-files", response_model=InstructionFileSettingsResponse)
async def get_instruction_file_settings(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> InstructionFileSettingsResponse:
    """Return the line limits used for repository instruction files."""

    limits = await components.instruction_settings.get()
    return _instruction_settings_response(limits.root_max_lines, limits.nested_max_lines)


@router.put("/instruction-files", response_model=InstructionFileSettingsResponse)
async def update_instruction_file_settings(
    request: UpdateInstructionFileSettingsRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> InstructionFileSettingsResponse:
    """Persist replacement instruction limits for subsequent Review snapshots."""

    limits = await components.instruction_settings.update(
        root_max_lines=request.root_max_lines,
        nested_max_lines=request.nested_max_lines,
    )
    return _instruction_settings_response(limits.root_max_lines, limits.nested_max_lines)


@router.get("/review-completion", response_model=ReviewCompletionSettingsResponse)
async def get_review_completion_settings(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewCompletionSettingsResponse:
    """Return the retry limit used for incomplete Agent completion attempts."""

    settings = await components.review_completion_settings.get()
    return ReviewCompletionSettingsResponse(
        max_incomplete_review_retries=settings.max_incomplete_review_retries
    )


@router.put("/review-completion", response_model=ReviewCompletionSettingsResponse)
async def update_review_completion_settings(
    request: UpdateReviewCompletionSettingsRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewCompletionSettingsResponse:
    """Persist the retry limit adopted by subsequent Agent runs."""

    settings = await components.review_completion_settings.update(
        max_incomplete_review_retries=request.max_incomplete_review_retries
    )
    return ReviewCompletionSettingsResponse(
        max_incomplete_review_retries=settings.max_incomplete_review_retries
    )


@router.get("/trigger-idempotency", response_model=TriggerIdempotencySettingsResponse)
async def get_trigger_idempotency_settings(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> TriggerIdempotencySettingsResponse:
    """Return the trigger idempotency toggle state."""

    settings = await components.trigger_idempotency_settings.get()
    return TriggerIdempotencySettingsResponse(enabled=settings.enabled)


@router.put("/trigger-idempotency", response_model=TriggerIdempotencySettingsResponse)
async def update_trigger_idempotency_settings(
    request: UpdateTriggerIdempotencySettingsRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> TriggerIdempotencySettingsResponse:
    """Persist the trigger idempotency toggle."""

    settings = await components.trigger_idempotency_settings.update(enabled=request.enabled)
    return TriggerIdempotencySettingsResponse(enabled=settings.enabled)


@router.get("/model-gateways", response_model=ModelGatewayCatalogResponse)
async def list_model_gateways(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Return every gateway without serializing any stored API key."""

    return _catalog_response(await components.model_gateways.list())


@router.post(
    "/model-gateways",
    response_model=ModelGatewayCatalogResponse,
    status_code=201,
)
async def create_model_gateway(
    request: CreateModelGatewayRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Persist a new named gateway; the first gateway becomes active."""

    return _catalog_response(
        await components.model_gateways.create(
            name=request.name,
            api_key=request.api_key.get_secret_value(),
            model=request.model,
            base_url=str(request.base_url).rstrip("/"),
            vendor=request.vendor,
            api_type=request.api_type,
            max_tokens=request.max_tokens,
            thinking_level=request.thinking_level,
            agent_timeout=request.agent_timeout,
            max_agent_turns=request.max_agent_turns,
            max_tool_calls=request.max_tool_calls,
            max_identical_tool_results=request.max_identical_tool_results,
            tool_timeout_seconds=request.tool_timeout_seconds,
            max_retries=request.max_retries,
            retry_backoff_base=request.retry_backoff_base,
            retry_max_delay=request.retry_max_delay,
        )
    )


@router.put("/model-gateways/{gateway_id}", response_model=ModelGatewayCatalogResponse)
async def update_model_gateway(
    gateway_id: GatewayId,
    request: UpdateModelGatewayRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Update one gateway and retain its key when no replacement is supplied."""

    return _catalog_response(
        await components.model_gateways.update(
            gateway_id,
            name=request.name,
            api_key=(request.api_key.get_secret_value() if request.api_key is not None else None),
            model=request.model,
            base_url=str(request.base_url).rstrip("/"),
            vendor=request.vendor,
            api_type=request.api_type,
            max_tokens=request.max_tokens,
            thinking_level=request.thinking_level,
            agent_timeout=request.agent_timeout,
            max_agent_turns=request.max_agent_turns,
            max_tool_calls=request.max_tool_calls,
            max_identical_tool_results=request.max_identical_tool_results,
            tool_timeout_seconds=request.tool_timeout_seconds,
            max_retries=request.max_retries,
            retry_backoff_base=request.retry_backoff_base,
            retry_max_delay=request.retry_max_delay,
        )
    )


@router.delete("/model-gateways/{gateway_id}", response_model=ModelGatewayCatalogResponse)
async def delete_model_gateway(
    gateway_id: GatewayId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Delete one gateway and select a deterministic fallback when required."""

    return _catalog_response(await components.model_gateways.delete(gateway_id))


@router.put("/active-model-gateway", response_model=ModelGatewayCatalogResponse)
async def activate_model_gateway(
    request: ActivateModelGatewayRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ModelGatewayCatalogResponse:
    """Switch the active runtime gateway without restarting API or Worker."""

    return _catalog_response(await components.model_gateways.activate(request.gateway_id))


@router.post(
    "/model-gateways/{gateway_id}/test-connectivity",
    response_model=GatewayConnectivityTestResponse,
)
async def test_gateway_connectivity(
    gateway_id: GatewayId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> GatewayConnectivityTestResponse:
    """Probe TCP reachability of the gateway base URL without exposing credentials."""

    result = await components.model_gateways.test_connectivity(gateway_id)
    return GatewayConnectivityTestResponse(
        ok=result.ok,
        latency_ms=result.latency_ms,
        detail=result.detail,
    )


@router.post(
    "/model-gateways/{gateway_id}/test-availability",
    response_model=GatewayAvailabilityTestResponse,
)
async def test_gateway_availability(
    gateway_id: GatewayId,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> GatewayAvailabilityTestResponse:
    """Send a minimal ping to verify the LLM behind the gateway can respond."""

    result = await components.model_gateways.test_availability(gateway_id)
    return GatewayAvailabilityTestResponse(
        ok=result.ok,
        latency_ms=result.latency_ms,
        detail=result.detail,
    )


@router.get("/tool-limits", response_model=ToolLimitsResponse)
async def get_tool_limits(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ToolLimitsResponse:
    """Return the configured tool-level limits for Review Agent evidence operations."""

    limits = await components.tool_limits.get()
    return _tool_limits_response(limits)


@router.put("/tool-limits", response_model=ToolLimitsResponse)
async def update_tool_limits(
    request: UpdateToolLimitsRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ToolLimitsResponse:
    """Persist replacement tool limits for subsequent Agent runs."""

    try:
        limits = await components.tool_limits.update(
            max_results=request.max_results,
            max_read_bytes=request.max_read_bytes,
            max_scan_bytes=request.max_scan_bytes,
            max_source_bytes=request.max_source_bytes,
            max_lines=request.max_lines,
            max_path_chars=request.max_path_chars,
            max_pattern_chars=request.max_pattern_chars,
            regex_timeout_seconds=request.regex_timeout_seconds,
            comment_batch_size=request.comment_batch_size,
            short_text_max=request.short_text_max,
            long_text_max=request.long_text_max,
            task_summary_max=request.task_summary_max,
            context_compaction_enabled=request.context_compaction_enabled,
            context_compaction_trigger_bytes=request.context_compaction_trigger_bytes,
            context_compaction_target_bytes=request.context_compaction_target_bytes,
            context_compaction_keep_recent_evidence_results=(
                request.context_compaction_keep_recent_evidence_results
            ),
        )
    except ValueError as error:
        raise HttpProblem(422, "invalid_tool_limits", str(error)) from error
    return _tool_limits_response(limits)


@router.post("/reset-all", response_model=ResetAllSettingsResponse)
async def reset_all_settings(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ResetAllSettingsResponse:
    """Reset all user-configurable settings to their product defaults.

    Does not affect gateway identities, credentials, reviewer prompt customizations,
    or trigger plugin configurations.
    """

    from codelens.bootstrap.logging import get_runtime_log_level, set_runtime_log_level
    from codelens.instruction_policy.domain.models import (
        DEFAULT_NESTED_INSTRUCTION_MAX_LINES,
        DEFAULT_ROOT_INSTRUCTION_MAX_LINES,
    )
    from codelens.review.application.settings import DEFAULT_MAX_INCOMPLETE_REVIEW_RETRIES
    from codelens.review.domain.ports import DEFAULT_RECENT_REPOSITORY_LIMIT
    from codelens.review.domain.tool_limits import ToolLimits
    from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy

    # Reset instruction file limits
    instruction_limits = await components.instruction_settings.update(
        root_max_lines=DEFAULT_ROOT_INSTRUCTION_MAX_LINES,
        nested_max_lines=DEFAULT_NESTED_INSTRUCTION_MAX_LINES,
    )

    default_file_exclusions = ReviewFileExclusionPolicy()
    file_exclusions = await components.file_exclusion_settings.update(
        suffixes=default_file_exclusions.suffixes,
        path_regexes=default_file_exclusions.path_regexes,
        exclude_binary=default_file_exclusions.exclude_binary,
    )

    # Reset review completion settings
    review_completion = await components.review_completion_settings.update(
        max_incomplete_review_retries=DEFAULT_MAX_INCOMPLETE_REVIEW_RETRIES
    )

    # Reset trigger idempotency settings
    trigger_idempotency = await components.trigger_idempotency_settings.update(enabled=False)

    # Reset recent repository limit
    recent_repo_limit = await components.update_recent_repository_settings.handle(
        DEFAULT_RECENT_REPOSITORY_LIMIT
    )

    # Reset tool limits
    default_tool_limits = ToolLimits()
    tool_limits = await components.tool_limits.update(
        max_results=default_tool_limits.max_results,
        max_read_bytes=default_tool_limits.max_read_bytes,
        max_scan_bytes=default_tool_limits.max_scan_bytes,
        max_source_bytes=default_tool_limits.max_source_bytes,
        max_lines=default_tool_limits.max_lines,
        max_path_chars=default_tool_limits.max_path_chars,
        max_pattern_chars=default_tool_limits.max_pattern_chars,
        regex_timeout_seconds=default_tool_limits.regex_timeout_seconds,
        comment_batch_size=default_tool_limits.comment_batch_size,
        short_text_max=default_tool_limits.short_text_max,
        long_text_max=default_tool_limits.long_text_max,
        task_summary_max=default_tool_limits.task_summary_max,
        context_compaction_enabled=default_tool_limits.context_compaction_enabled,
        context_compaction_trigger_bytes=default_tool_limits.context_compaction_trigger_bytes,
        context_compaction_target_bytes=default_tool_limits.context_compaction_target_bytes,
        context_compaction_keep_recent_evidence_results=(
            default_tool_limits.context_compaction_keep_recent_evidence_results
        ),
    )

    # Reset log level
    await asyncio.to_thread(set_runtime_log_level, components.settings.data_dir, "info")
    log_level = await asyncio.to_thread(get_runtime_log_level, components.settings.data_dir)

    # Reset active gateway execution limits (if a gateway is active)
    gateway_catalog = await components.model_gateways.list()
    if gateway_catalog.active_gateway_id:
        active_gw = next(
            (gw for gw in gateway_catalog.gateways if gw.is_active),
            None,
        )
        if active_gw:
            gateway_catalog = await components.model_gateways.update(
                gateway_id=active_gw.gateway_id,
                name=active_gw.name,
                api_key=None,  # retain existing key
                model=active_gw.model,
                base_url=active_gw.base_url,
                vendor=active_gw.vendor,
                api_type=active_gw.api_type,
                max_tokens=active_gw.max_tokens,
                thinking_level=active_gw.thinking_level,
                agent_timeout=1800,
                max_agent_turns=100,
                max_tool_calls=300,
                max_identical_tool_results=3,
                tool_timeout_seconds=30,
                max_retries=10,
                retry_backoff_base=1.0,
                retry_max_delay=30.0,
            )

    return ResetAllSettingsResponse(
        instruction_files=_instruction_settings_response(
            instruction_limits.root_max_lines,
            instruction_limits.nested_max_lines,
        ),
        file_exclusions=_file_exclusion_response(file_exclusions),
        review_completion=ReviewCompletionSettingsResponse(
            max_incomplete_review_retries=review_completion.max_incomplete_review_retries,
        ),
        trigger_idempotency=TriggerIdempotencySettingsResponse(
            enabled=trigger_idempotency.enabled,
        ),
        recent_repositories=RecentRepositorySettingsResponse(
            recent_repository_limit=recent_repo_limit,
        ),
        tool_limits=_tool_limits_response(tool_limits),
        logging=RuntimeLogLevelResponse(level=log_level),
        model_gateways=_catalog_response(gateway_catalog),
    )
