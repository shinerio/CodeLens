"""Final Verifier tools for the simplified two-stage Review DAG.

Three tools drive the Final Verifier stage:

- ``verdict``: accept or deny one or more clusters. Accept uses the cluster's
  canonical candidate fields; deny suppresses the cluster.
- ``merge``: synthesize a single Finding across one or more clusters. All
  Finding fields are required so the model owns the merged attributes.
- ``finalize_verdicts``: validate that every cluster is covered exactly once
  and produce the final validated verdict batch.
"""

from typing import Literal, cast

from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome
from codelens.findings.infrastructure.verdict_codec import (
    ValidatedVerdictBatch,
    VerdictCodec,
)
from codelens.review.domain.ports import FindingValidationWarning
from codelens.review.domain.tool_results import (
    JsonValue,
    ToolDiagnostic,
    ToolResult,
    ToolResultStatus,
)
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding
from codelens.review.infrastructure.location_resolver import SnapshotLocationResolver
from codelens.review.infrastructure.tool_contract import reject_unknown_arguments


class VerdictSubmissionCollector:
    """Accumulate Final Verifier decisions across multiple tool calls.

    The Final Verifier calls ``verdict`` (accept/deny) and ``merge`` multiple
    times to accumulate decisions for clusters. When finished, it calls
    ``finalize_verdicts`` to validate that all clusters are covered exactly
    once and produce the final output.
    """

    def __init__(
        self,
        codec: VerdictCodec,
        location_resolver: SnapshotLocationResolver | None = None,
    ) -> None:
        self._codec = codec
        self._location_resolver = location_resolver
        self._decisions: list[VerdictDecision] = []
        self._covered_cluster_ids: set[str] = set()
        self._finalized: tuple[VerdictDecision, ...] | None = None

    @property
    def is_completed(self) -> bool:
        return self._finalized is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        return ()

    def final_output(self) -> tuple[VerdictDecision, ...]:
        if self._finalized is None:
            raise RuntimeError("Final Verifier has not finalized decisions")
        return self._finalized

    async def verdict(
        self,
        cluster_ids: list[str],
        action: Literal["accept", "deny"],
    ) -> str:
        """Record an accept or deny decision for one or more clusters."""
        if self._finalized is not None:
            return self._completed_result("verdict")

        validated_ids, rejected = self._partition_cluster_ids(cluster_ids)
        if not validated_ids:
            return self._batch_result("verdict", cluster_ids, validated_ids, rejected)
        outcome = VerdictOutcome.ACCEPT if action == "accept" else VerdictOutcome.DENY
        self._decisions.extend(
            VerdictDecision(cluster_ids=(cluster_id,), outcome=outcome)
            for cluster_id in validated_ids
        )
        self._covered_cluster_ids.update(validated_ids)
        return self._batch_result(
            "verdict",
            cluster_ids,
            validated_ids,
            rejected,
            extra={"action": action, "decision_count": len(self._decisions)},
        )

    async def merge(
        self,
        cluster_ids: list[str],
        path: str,
        side: Literal["old", "new"],
        existing_code: str,
        title: str,
        content: str,
        recommendation: str,
        category: str,
        severity: Literal["critical", "high", "medium", "low", "info"],
        primary_dimension: str,
        evidence_strength: Literal["direct", "inferred", "weak"],
    ) -> str:
        """Merge one or more clusters into a single synthesized Finding."""
        if self._finalized is not None:
            return self._completed_result("merge")

        from codelens.findings.domain.candidates import EvidenceStrength
        from codelens.findings.domain.models import FindingSeverity

        if self._location_resolver is None:
            return ToolResult(
                "merge",
                ToolResultStatus.FAILED,
                {},
                (
                    ToolDiagnostic(
                        "location_resolver_unavailable",
                        "Merge location resolution is unavailable.",
                        False,
                    ),
                ),
            ).to_json()
        validated_ids, rejected = self._partition_cluster_ids(cluster_ids)
        if not validated_ids:
            return self._batch_result("merge", cluster_ids, validated_ids, rejected)
        try:
            primary_location, changed_hunk_id = await self._location_resolver.resolve(
                path,
                side,
                existing_code,
            )
        except ValueError as error:
            # 根据实际错误消息提供更具体的诊断，帮助 verifier 理解问题
            error_msg = str(error)
            if "existing_code cannot be resolved" in error_msg:
                # existing_code 无法在 diff 或文件中定位
                diagnostic = ToolDiagnostic(
                    "existing_code_unresolvable",
                    "The existing_code cannot be resolved to a line range. It may contain content from multiple files or lines that don't match the diff.",
                    True,
                    "existing_code",
                )
            elif "existing_code must quote only consecutive" in error_msg:
                # existing_code 包含了 diff 标记或非连续变更行
                diagnostic = ToolDiagnostic(
                    "comment_outside_diff",
                    error_msg,  # 使用原始消息，已经足够清晰
                    True,
                    "existing_code",
                )
            elif "location path is outside" in error_msg:
                # path 不在审查范围内
                diagnostic = ToolDiagnostic(
                    "path_outside_review",
                    "The path is outside the review scope.",
                    True,
                    "path",
                )
            else:
                # 其他位置解析错误
                diagnostic = ToolDiagnostic(
                    "invalid_merge_location",
                    "The merge location is not valid evidence.",
                    True,
                    "path",
                )
            return ToolResult(
                "merge",
                ToolResultStatus.REJECTED,
                {},
                (diagnostic,),
            ).to_json()
        decision = VerdictDecision.merge(
            cluster_ids=validated_ids,
            path=path,
            side=side,
            existing_code=existing_code,
            title=title,
            content=content,
            recommendation=recommendation,
            category=category,
            severity=FindingSeverity(severity),
            primary_dimension=primary_dimension,
            evidence_strength=EvidenceStrength(evidence_strength),
            primary_location=primary_location,
            changed_hunk_id=changed_hunk_id,
        )
        self._decisions.append(decision)
        self._covered_cluster_ids.update(validated_ids)
        return self._batch_result(
            "merge",
            cluster_ids,
            validated_ids,
            rejected,
            extra={"decision_count": len(self._decisions)},
        )

    async def finalize(self) -> str:
        """Validate all accumulated decisions and finalize."""
        if self._finalized is not None:
            return self._completed_result("finalize_verdicts")
        try:
            finalized = self._codec.decode_decisions(self._decisions)
        except ValueError:
            missing = sorted(
                {cluster.cluster_id for cluster in self._codec.clusters} - self._covered_cluster_ids
            )
            return ToolResult(
                "finalize_verdicts",
                ToolResultStatus.NEEDS_ACTION,
                {
                    "missing_cluster_ids": cast(JsonValue, missing),
                    "decision_count": len(self._decisions),
                },
                (
                    ToolDiagnostic(
                        "missing_cluster_verdicts",
                        "Every cluster requires exactly one verdict.",
                        True,
                    ),
                ),
            ).to_json()
        self._finalized = finalized
        return ToolResult(
            "finalize_verdicts",
            ToolResultStatus.SUCCESS,
            {
                "verdict_count": len(finalized),
                "covered_cluster_count": len(self._covered_cluster_ids),
            },
        ).to_json()

    def _partition_cluster_ids(
        self, cluster_ids: list[str]
    ) -> tuple[tuple[str, ...], list[dict[str, JsonValue]]]:
        accepted: list[str] = []
        rejected: list[dict[str, JsonValue]] = []
        pending_covered = set(self._covered_cluster_ids)
        for input_index, cluster_id in enumerate(cluster_ids):
            try:
                validated = self._codec.validate_new_cluster_ids([cluster_id], pending_covered)
            except ValueError as error:
                message = str(error)
                code = (
                    "unknown_cluster"
                    if "unknown cluster" in message
                    else "duplicate_cluster_verdict"
                )
                rejected.append(
                    {"input_index": input_index, "cluster_id": cluster_id, "code": code}
                )
            else:
                accepted.extend(validated)
                pending_covered.update(validated)
        return tuple(accepted), rejected

    def _batch_result(
        self,
        tool: str,
        submitted_ids: list[str],
        accepted_ids: tuple[str, ...],
        rejected: list[dict[str, JsonValue]],
        *,
        extra: dict[str, JsonValue] | None = None,
    ) -> str:
        status = (
            ToolResultStatus.PARTIAL
            if accepted_ids and rejected
            else ToolResultStatus.SUCCESS
            if accepted_ids
            else ToolResultStatus.REJECTED
        )
        data: dict[str, JsonValue] = {
            "submitted_count": len(submitted_ids),
            "accepted_count": len(accepted_ids),
            "rejected_count": len(rejected),
            "accepted_cluster_ids": list(accepted_ids),
            "rejected_clusters": cast(JsonValue, rejected),
        }
        if extra:
            data.update(extra)
        diagnostics = tuple(
            ToolDiagnostic(
                str(item["code"]), "The cluster cannot receive this verdict.", True, "cluster_ids"
            )
            for item in rejected
        )
        return ToolResult(tool, status, data, diagnostics).to_json()

    @staticmethod
    def _completed_result(tool: str) -> str:
        return ToolResult(
            tool,
            ToolResultStatus.REJECTED,
            {},
            (
                ToolDiagnostic(
                    "verdicts_already_finalized",
                    "Final Verifier decisions are already final.",
                    False,
                ),
            ),
        ).to_json()

    def as_verdict_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="verdict", description_override=description)
        async def verdict(
            cluster_ids: list[str],
            action: Literal["accept", "deny"],
        ) -> str:
            """Accept or deny one or more finding clusters."""
            return await collector.verdict(cluster_ids=cluster_ids, action=action)

        return reject_unknown_arguments(verdict)

    def as_merge_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="merge", description_override=description)
        async def merge(
            cluster_ids: list[str],
            path: str,
            side: Literal["old", "new"],
            existing_code: str,
            title: str,
            content: str,
            recommendation: str,
            category: str,
            severity: Literal["critical", "high", "medium", "low", "info"],
            primary_dimension: str,
            evidence_strength: Literal["direct", "inferred", "weak"],
        ) -> str:
            """Merge one or more clusters into a single synthesized Finding."""
            return await collector.merge(
                cluster_ids=cluster_ids,
                path=path,
                side=side,
                existing_code=existing_code,
                title=title,
                content=content,
                recommendation=recommendation,
                category=category,
                severity=severity,
                primary_dimension=primary_dimension,
                evidence_strength=evidence_strength,
            )

        return reject_unknown_arguments(merge)

    def as_finalize_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="finalize_verdicts", description_override=description)
        async def finalize_verdicts() -> str:
            """Validate accumulated verdicts and finalize the Final Verifier stage."""
            return await collector.finalize()

        return reject_unknown_arguments(finalize_verdicts)

    def bindings(
        self,
        verdict_description: str,
        merge_description: str,
        finalize_description: str,
    ) -> tuple[RoleOutputToolBinding, RoleOutputToolBinding, RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("verdict", 2),
                self.as_verdict_tool(verdict_description),
            ),
            RoleOutputToolBinding(
                ToolContractReference("merge", 2),
                self.as_merge_tool(merge_description),
            ),
            RoleOutputToolBinding(
                ToolContractReference("finalize_verdicts", 2),
                self.as_finalize_tool(finalize_description),
                self,
            ),
        )


class VerdictValidator:
    """Validate a persisted Final Verifier Artifact against its frozen input constraints."""

    def __init__(self, codec: VerdictCodec) -> None:
        self._codec = codec

    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]:
        return ()

    async def validate(self, payload: bytes) -> ValidatedVerdictBatch:
        return ValidatedVerdictBatch(self._codec.decode(payload))
