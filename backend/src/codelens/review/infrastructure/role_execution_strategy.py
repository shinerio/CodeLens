"""Role-specific execution strategies for the OpenAI Agent runtime.

Each strategy owns one ``AgentRole``'s output tool bindings, instruction
assembly, nudge configuration, and canonical output serialization.  The
registry follows the same pattern as ``ModelProviderAdapterRegistry``:
Protocol + concrete classes + dict lookup + DI-friendly constructor.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from codelens.findings.application.validate_candidates import CandidateBatchCodec
from codelens.findings.domain.candidates import CandidateFindingBatch
from codelens.findings.domain.dedup import DedupDecision
from codelens.findings.domain.models import FindingSeverity
from codelens.findings.domain.remediation import RemediationDecision
from codelens.findings.domain.verdict import VerdictDecision
from codelens.findings.infrastructure.dedup_codec import DedupCodec
from codelens.findings.infrastructure.remediation_codec import RemediationCodec
from codelens.findings.infrastructure.verdict_codec import VerdictCodec
from codelens.review.application.i18n_prompt_loader import LocalizedSystemPrompts
from codelens.review.application.planning import PlannerSelection
from codelens.review.domain.errors import PermanentAgentOutputError
from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.capability_tools import (
    RoleOutputToolBinding,
)
from codelens.review.infrastructure.dedup_tools import DeduplicationCollector
from codelens.review.infrastructure.location_resolver import SnapshotLocationResolver
from codelens.review.infrastructure.planner_output import PlannerOutputCodec
from codelens.review.infrastructure.planning_tools import ReviewPlanSubmissionCollector
from codelens.review.infrastructure.remediation_tools import RemediationCollector
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.review.infrastructure.verdict_tools import VerdictSubmissionCollector
from codelens.reviewer_catalog.domain.models import AgentRole, AgentVersion
from codelens.reviewer_catalog.domain.provider_config import ModelProviderConfig
from codelens.workspace.domain.models import ReviewSnapshot
from codelens.workspace.infrastructure.git_cli import GitCli


@dataclass(frozen=True)
class NudgeConfig:
    """Role-specific nudge templates injected into ``ToolExecutionLimits``."""

    no_progress_rounds_threshold: int | None = None
    no_progress_nudge_template: str | None = None
    all_files_reviewed_nudge_template: str | None = None


@dataclass(frozen=True)
class RoleOutputSetup:
    """Role-specific output tool bindings and canonical byte serializer.

    ``serialize_output`` receives the value returned by
    ``RuntimeToolContext.final_output()`` and must raise ``ValueError`` when
    the type does not match the role's contract.  The caller wraps that
    error in a ``PermanentAgentOutputError``.
    """

    bindings: tuple[RoleOutputToolBinding, ...]
    serialize_output: Callable[[object], bytes]


class RoleExecutionStrategy(Protocol):
    """Own one Agent role's output tools, instructions, and result serialization."""

    @property
    def role(self) -> AgentRole:
        raise NotImplementedError

    def output_tool_bindings(
        self,
        prompts: LocalizedSystemPrompts,
        role_context: dict[str, object] | None,
        snapshot: ReviewSnapshot,
        git: GitCli,
        tool_limits: ToolLimits,
    ) -> RoleOutputSetup:
        """Build role-specific output tools (codec + collector + bindings)."""
        raise NotImplementedError

    def instruction_sections(
        self,
        prompts: LocalizedSystemPrompts,
        repository_instructions: str,
        agent: AgentVersion,
        skill_sections: tuple[str, ...],
    ) -> list[str]:
        """Assemble role-specific instruction sections."""
        raise NotImplementedError

    def nudge_config(
        self,
        prompts: LocalizedSystemPrompts,
        provider_config: ModelProviderConfig,
    ) -> NudgeConfig:
        """Return role-specific nudge configuration."""
        raise NotImplementedError

    @property
    def requires_completion_nudge(self) -> bool:
        """Whether to send a completion nudge when task_done is not called."""
        raise NotImplementedError

    def validate_output_contract(self, agent: AgentVersion) -> None:
        """Validate role-specific output contract requirements."""
        raise NotImplementedError


class ReviewerStrategy:
    """Reviewer role: comment:v2 + task_done:v2 output."""

    @property
    def role(self) -> AgentRole:
        return AgentRole.REVIEWER

    def output_tool_bindings(
        self,
        prompts: LocalizedSystemPrompts,
        role_context: dict[str, object] | None,
        snapshot: ReviewSnapshot,
        git: GitCli,
        tool_limits: ToolLimits,
    ) -> RoleOutputSetup:
        """Return empty bindings; the comment tool is assembled by CapabilityToolAssembler."""

        del role_context, snapshot, git, tool_limits  # Reviewer has no role-specific output tools.

        def serialize_output(final_output: object) -> bytes:
            if not isinstance(final_output, CandidateFindingBatch):
                raise ValueError("Comment v2 output state has the wrong value")
            return CandidateBatchCodec().encode(final_output)

        return RoleOutputSetup(bindings=(), serialize_output=serialize_output)

    def instruction_sections(
        self,
        prompts: LocalizedSystemPrompts,
        repository_instructions: str,
        agent: AgentVersion,
        skill_sections: tuple[str, ...],
    ) -> list[str]:
        return [
            prompts.review_policy,
            repository_instructions,
            prompts.review_workflow,
            f"# Agent Policy\n{agent.prompt_template}",
            *skill_sections,
        ]

    def nudge_config(
        self,
        prompts: LocalizedSystemPrompts,
        provider_config: ModelProviderConfig,
    ) -> NudgeConfig:
        return NudgeConfig(
            no_progress_rounds_threshold=provider_config.no_progress_rounds_threshold,
            no_progress_nudge_template=prompts.no_progress_nudge,
            all_files_reviewed_nudge_template=prompts.all_files_reviewed_nudge,
        )

    @property
    def requires_completion_nudge(self) -> bool:
        return True

    def validate_output_contract(self, agent: AgentVersion) -> None:
        if agent.output_contract_version != "2":
            raise PermanentAgentOutputError("Agent output contract is unsupported")


class PlannerStrategy:
    """Planner role: finalize_plan:v2 output."""

    @property
    def role(self) -> AgentRole:
        return AgentRole.PLANNER

    def output_tool_bindings(
        self,
        prompts: LocalizedSystemPrompts,
        role_context: dict[str, object] | None,
        snapshot: ReviewSnapshot,
        git: GitCli,
        tool_limits: ToolLimits,
    ) -> RoleOutputSetup:
        del snapshot, git, tool_limits  # Planner has no filesystem evidence tools.

        codec = _planner_codec(role_context)
        collector = ReviewPlanSubmissionCollector(codec)
        finalize_description = prompts.tools["finalize_plan"].description
        bindings = collector.bindings(finalize_description)

        def serialize_output(final_output: object) -> bytes:
            if not isinstance(final_output, PlannerSelection):
                raise ValueError("Planner output state has the wrong value")
            return codec.canonical_bytes(final_output)

        return RoleOutputSetup(bindings=bindings, serialize_output=serialize_output)

    def instruction_sections(
        self,
        prompts: LocalizedSystemPrompts,
        repository_instructions: str,
        agent: AgentVersion,
        skill_sections: tuple[str, ...],
    ) -> list[str]:
        return [
            prompts.review_policy,
            repository_instructions,
            f"# Agent Policy\n{agent.prompt_template}",
            *skill_sections,
        ]

    def nudge_config(
        self,
        prompts: LocalizedSystemPrompts,
        provider_config: ModelProviderConfig,
    ) -> NudgeConfig:
        del prompts, provider_config
        return NudgeConfig()

    @property
    def requires_completion_nudge(self) -> bool:
        return False

    def validate_output_contract(self, agent: AgentVersion) -> None:
        del agent


class VerifierStrategy:
    """Verifier role: verdict:v2 + merge:v2 + finalize_verdicts:v2 output."""

    @property
    def role(self) -> AgentRole:
        return AgentRole.VERIFIER

    def output_tool_bindings(
        self,
        prompts: LocalizedSystemPrompts,
        role_context: dict[str, object] | None,
        snapshot: ReviewSnapshot,
        git: GitCli,
        tool_limits: ToolLimits,
    ) -> RoleOutputSetup:
        codec = _verdict_codec(role_context)
        evidence = FilesystemReviewTools(
            snapshot,
            git,
            max_tool_calls=None,
            tool_limits=tool_limits,
        )
        collector = VerdictSubmissionCollector(
            codec,
            SnapshotLocationResolver(snapshot, evidence),
        )
        verdict_description = prompts.tools["verdict"].description
        merge_description = prompts.tools["merge"].description
        finalize_description = prompts.tools["finalize_verdicts"].description
        bindings = collector.bindings(verdict_description, merge_description, finalize_description)

        def serialize_output(final_output: object) -> bytes:
            if not isinstance(final_output, tuple) or not all(
                isinstance(item, VerdictDecision) for item in final_output
            ):
                raise ValueError("Verdict output state has the wrong value")
            return codec.canonical_bytes(final_output)

        return RoleOutputSetup(bindings=bindings, serialize_output=serialize_output)

    def instruction_sections(
        self,
        prompts: LocalizedSystemPrompts,
        repository_instructions: str,
        agent: AgentVersion,
        skill_sections: tuple[str, ...],
    ) -> list[str]:
        return [
            prompts.review_policy,
            repository_instructions,
            f"# Agent Policy\n{agent.prompt_template}",
            *skill_sections,
        ]

    def nudge_config(
        self,
        prompts: LocalizedSystemPrompts,
        provider_config: ModelProviderConfig,
    ) -> NudgeConfig:
        del prompts, provider_config
        return NudgeConfig()

    @property
    def requires_completion_nudge(self) -> bool:
        return False

    def validate_output_contract(self, agent: AgentVersion) -> None:
        del agent


class DeduplicatorStrategy:
    """Deduplicator role: deduplicate:v1 + deduplicate_done:v1 output."""

    @property
    def role(self) -> AgentRole:
        return AgentRole.DEDUPLICATOR

    def output_tool_bindings(
        self,
        prompts: LocalizedSystemPrompts,
        role_context: dict[str, object] | None,
        snapshot: ReviewSnapshot,
        git: GitCli,
        tool_limits: ToolLimits,
    ) -> RoleOutputSetup:
        del snapshot, git, tool_limits  # Deduplicator is codeless; no filesystem tools.

        codec = _dedup_codec(role_context)
        collector = DeduplicationCollector(codec)
        dedup_description = prompts.tools["deduplicate"].description
        done_description = prompts.tools["deduplicate_done"].description
        bindings = collector.bindings(dedup_description, done_description)

        def serialize_output(final_output: object) -> bytes:
            if not isinstance(final_output, tuple) or not all(
                isinstance(item, DedupDecision) for item in final_output
            ):
                raise ValueError("Dedup output state has the wrong value")
            return codec.canonical_bytes(final_output)

        return RoleOutputSetup(bindings=bindings, serialize_output=serialize_output)

    def instruction_sections(
        self,
        prompts: LocalizedSystemPrompts,
        repository_instructions: str,
        agent: AgentVersion,
        skill_sections: tuple[str, ...],
    ) -> list[str]:
        """Skip review_policy and repository_instructions (codeless agent)."""
        del prompts, repository_instructions
        return [
            f"# Agent Policy\n{agent.prompt_template}",
            *skill_sections,
        ]

    def nudge_config(
        self,
        prompts: LocalizedSystemPrompts,
        provider_config: ModelProviderConfig,
    ) -> NudgeConfig:
        del prompts, provider_config
        return NudgeConfig()

    @property
    def requires_completion_nudge(self) -> bool:
        return False

    def validate_output_contract(self, agent: AgentVersion) -> None:
        del agent


class RemediatorStrategy:
    """Remediator role: resolved_review:v1 + remediation_done:v1 output."""

    @property
    def role(self) -> AgentRole:
        return AgentRole.REMEDIATOR

    def output_tool_bindings(
        self,
        prompts: LocalizedSystemPrompts,
        role_context: dict[str, object] | None,
        snapshot: ReviewSnapshot,
        git: GitCli,
        tool_limits: ToolLimits,
    ) -> RoleOutputSetup:
        del snapshot, git, tool_limits  # Remediator is codeless; no filesystem tools.

        codec = _remediation_codec(role_context)
        collector = RemediationCollector(codec)
        resolved_review_description = prompts.tools["resolved_review"].description
        done_description = prompts.tools["remediation_done"].description
        bindings = collector.bindings(resolved_review_description, done_description)

        def serialize_output(final_output: object) -> bytes:
            if not isinstance(final_output, tuple) or not all(
                isinstance(item, RemediationDecision) for item in final_output
            ):
                raise ValueError("Remediation output state has the wrong value")
            return codec.canonical_bytes(final_output)

        return RoleOutputSetup(bindings=bindings, serialize_output=serialize_output)

    def instruction_sections(
        self,
        prompts: LocalizedSystemPrompts,
        repository_instructions: str,
        agent: AgentVersion,
        skill_sections: tuple[str, ...],
    ) -> list[str]:
        return [
            prompts.review_policy,
            repository_instructions,
            f"# Agent Policy\n{agent.prompt_template}",
            *skill_sections,
        ]

    def nudge_config(
        self,
        prompts: LocalizedSystemPrompts,
        provider_config: ModelProviderConfig,
    ) -> NudgeConfig:
        del prompts, provider_config
        return NudgeConfig()

    @property
    def requires_completion_nudge(self) -> bool:
        return False

    def validate_output_contract(self, agent: AgentVersion) -> None:
        del agent


class RoleExecutionStrategyRegistry:
    """Map ``AgentRole`` to its execution strategy.

    Follows the same pattern as ``ModelProviderAdapterRegistry``: default
    strategies in ``__init__``, no global state, accepts an explicit
    tuple for dependency injection in tests.
    """

    def __init__(
        self,
        strategies: tuple[RoleExecutionStrategy, ...] | None = None,
    ) -> None:
        resolved = strategies or (
            ReviewerStrategy(),
            PlannerStrategy(),
            VerifierStrategy(),
            DeduplicatorStrategy(),
            RemediatorStrategy(),
        )
        self._strategies = {strategy.role: strategy for strategy in resolved}

    def resolve(self, role: AgentRole) -> RoleExecutionStrategy:
        try:
            return self._strategies[role]
        except KeyError as error:
            raise ValueError(f"No execution strategy for role {role}") from error


# ---------------------------------------------------------------------------
# Codec factory functions (moved from openai_runtime.py)
# ---------------------------------------------------------------------------


def _planner_codec(role_context: dict[str, object] | None) -> PlannerOutputCodec:
    """Build the Planner validator only from bounded frozen input metadata."""

    required = {
        "eligible_reviewer_references",
        "unavailable_reviewer_references",
    }
    # The Worker attaches this trusted identity after freezing Planner input. It is
    # stripped from model-visible context and validated separately by _host_run_id.
    allowed = required | {"change_risk_summary", "reviewer_catalog", "_host_run_id"}
    if (
        role_context is None
        or not required.issubset(role_context)
        or not set(role_context).issubset(allowed)
    ):
        raise PermanentAgentOutputError("Planner role context has an invalid shape")

    def string_tuple(name: str) -> tuple[str, ...]:
        value = role_context[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PermanentAgentOutputError("Planner role context has an invalid value")
        return tuple(value)

    return PlannerOutputCodec(
        eligible_reviewer_references=string_tuple("eligible_reviewer_references"),
        unavailable_reviewer_references=string_tuple("unavailable_reviewer_references"),
    )


def _verdict_codec(role_context: dict[str, object] | None) -> VerdictCodec:
    """Rebuild Verdict constraints from the frozen cluster projection."""

    context = role_context.get("verdict_context") if role_context is not None else None
    if not isinstance(context, dict) or set(context) != {
        "clusters",
        "schema_version",
    }:
        raise PermanentAgentOutputError("Verdict role context has an invalid shape")
    raw_clusters = context["clusters"]
    if context["schema_version"] != "2" or not isinstance(raw_clusters, list):
        raise PermanentAgentOutputError("Verdict role context has an invalid value")
    try:
        from codelens.findings.domain.candidates import EvidenceStrength
        from codelens.findings.domain.clusters import FindingCluster

        clusters = tuple(
            FindingCluster(
                cluster_id=item["cluster_id"],
                candidate_ids=tuple(item["candidate_ids"]),
                canonical_candidate_id=item["canonical_candidate_id"],
                title=item["title"],
                category=item["category"],
                severity=FindingSeverity(item["severity"]),
                content=item["content"],
                recommendation=item["recommendation"],
                primary_dimension=item["primary_dimension"],
                evidence_strength=EvidenceStrength(item["evidence_strength"]),
            )
            for item in raw_clusters
            if isinstance(item, dict)
        )
        if len(clusters) != len(raw_clusters):
            raise ValueError("Verdict projection contains non-object values")
        return VerdictCodec(clusters=clusters)
    except (KeyError, TypeError, ValueError) as error:
        raise PermanentAgentOutputError("Verdict role context has an invalid value") from error


def _dedup_codec(role_context: dict[str, object] | None) -> DedupCodec:
    """Rebuild Dedup constraints from the frozen survived-finding projection."""

    context = role_context.get("dedup_context") if role_context is not None else None
    if not isinstance(context, dict) or set(context) != {
        "survived_findings",
        "schema_version",
    }:
        raise PermanentAgentOutputError("Dedup role context has an invalid shape")
    raw_findings = context["survived_findings"]
    if context["schema_version"] != "1" or not isinstance(raw_findings, list):
        raise PermanentAgentOutputError("Dedup role context has an invalid value")
    try:
        expected_ids = frozenset(
            str(item["verdict_decision_id"])
            for item in raw_findings
            if isinstance(item, dict)
        )
        if len(expected_ids) != len(raw_findings):
            raise ValueError("Dedup projection contains duplicate or non-object values")
        return DedupCodec(expected_ids=expected_ids)
    except (KeyError, TypeError, ValueError) as error:
        raise PermanentAgentOutputError("Dedup role context has an invalid value") from error


def _remediation_codec(role_context: dict[str, object] | None) -> RemediationCodec:
    """Rebuild Remediation constraints from the frozen pending-finding projection."""

    context = role_context.get("remediation_context") if role_context is not None else None
    if not isinstance(context, dict) or set(context) != {
        "pending_findings",
        "schema_version",
    }:
        raise PermanentAgentOutputError("Remediation role context has an invalid shape")
    raw_findings = context["pending_findings"]
    if context["schema_version"] != "1" or not isinstance(raw_findings, list):
        raise PermanentAgentOutputError("Remediation role context has an invalid value")
    try:
        expected_refs = frozenset(
            str(item["remediation_ref"])
            for item in raw_findings
            if isinstance(item, dict)
        )
        if len(expected_refs) != len(raw_findings):
            raise ValueError("Remediation projection contains duplicate or non-object values")
        return RemediationCodec(expected_refs=expected_refs)
    except (KeyError, TypeError, ValueError) as error:
        raise PermanentAgentOutputError("Remediation role context has an invalid value") from error
