"""Remediator tools for the post-dedup fix-detection gate.

Two tools drive the Remediator stage:

- ``resolved_review``: mark one or more pending existing findings as
  ``resolved``, ``unresolved``, or ``unclear``. ``resolved`` means the current
  code changes have fixed the issue; ``unresolved`` means it still exists;
  ``unclear`` means the evidence is insufficient to decide.
- ``remediation_done``: validate that every pending finding is covered
  exactly once and produce the final validated remediation batch.
"""

from typing import Literal, cast

from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.findings.domain.remediation import (
    RemediationDecision,
    RemediationDecisionSource,
    RemediationOutcome,
)
from codelens.findings.infrastructure.remediation_codec import (
    RemediationCodec,
    ValidatedRemediationBatch,
)
from codelens.review.domain.ports import FindingValidationWarning
from codelens.review.domain.tool_results import (
    JsonValue,
    ToolDiagnostic,
    ToolResult,
    ToolResultStatus,
)
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding
from codelens.review.infrastructure.tool_contract import reject_unknown_arguments


class RemediationCollector:
    """Accumulate Remediator decisions across multiple tool calls.

    The Remediator calls ``resolved_review`` (mark resolved/unresolved/unclear)
    multiple times to accumulate decisions for pending existing findings. When
    finished, it calls ``remediation_done`` to validate that all pending
    findings are covered exactly once and produce the final output.
    """

    def __init__(self, codec: RemediationCodec) -> None:
        self._codec = codec
        self._decisions: list[RemediationDecision] = []
        self._covered_refs: set[str] = set()
        self._finalized: tuple[RemediationDecision, ...] | None = None

    @property
    def is_completed(self) -> bool:
        return self._finalized is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        return ()

    def final_output(self) -> tuple[RemediationDecision, ...]:
        if self._finalized is None:
            raise RuntimeError("Remediator has not finalized decisions")
        return self._finalized

    async def resolved_review(
        self,
        remediation_refs: list[str],
        outcome: Literal["resolved", "unresolved", "unclear"],
        evidence_summary: str,
    ) -> str:
        """Record a remediation outcome for one or more pending existing findings."""
        if self._finalized is not None:
            return self._completed_result("resolved_review")

        validated_refs, rejected = self._partition_remediation_refs(remediation_refs)
        if not validated_refs:
            return self._batch_result(
                "resolved_review", remediation_refs, validated_refs, rejected
            )
        remediation_outcome = RemediationOutcome(outcome)
        self._decisions.extend(
            RemediationDecision(
                source_id=ref.partition(":")[0],
                finding_id=ref.partition(":")[2],
                outcome=remediation_outcome,
                evidence_summary=evidence_summary,
                decision_source=RemediationDecisionSource.LLM,
            )
            for ref in validated_refs
        )
        self._covered_refs.update(validated_refs)
        return self._batch_result(
            "resolved_review",
            remediation_refs,
            validated_refs,
            rejected,
            extra={"outcome": outcome, "decision_count": len(self._decisions)},
        )

    async def finalize(self) -> str:
        """Validate all accumulated decisions and finalize."""
        if self._finalized is not None:
            return self._completed_result("remediation_done")
        try:
            finalized = self._codec.decode_decisions(self._decisions)
        except ValueError:
            missing = sorted(self._codec.expected_refs - self._covered_refs)
            return ToolResult(
                "remediation_done",
                ToolResultStatus.NEEDS_ACTION,
                {
                    "missing_remediation_refs": cast(JsonValue, missing),
                    "decision_count": len(self._decisions),
                },
                (
                    ToolDiagnostic(
                        "missing_remediation_decisions",
                        "Every pending finding requires exactly one remediation decision.",
                        True,
                    ),
                ),
            ).to_json()
        self._finalized = finalized
        return ToolResult(
            "remediation_done",
            ToolResultStatus.SUCCESS,
            {
                "remediation_count": len(finalized),
                "covered_finding_count": len(self._covered_refs),
            },
        ).to_json()

    def _partition_remediation_refs(
        self, remediation_refs: list[str]
    ) -> tuple[tuple[str, ...], list[dict[str, JsonValue]]]:
        accepted: list[str] = []
        rejected: list[dict[str, JsonValue]] = []
        pending_covered = set(self._covered_refs)
        for input_index, remediation_ref in enumerate(remediation_refs):
            try:
                validated = self._codec.validate_new_refs(
                    [remediation_ref], pending_covered
                )
            except ValueError as error:
                message = str(error)
                code = (
                    "unknown_ref"
                    if "unknown pending finding" in message
                    else "duplicate_ref_remediation"
                )
                rejected.append(
                    {
                        "input_index": input_index,
                        "remediation_ref": remediation_ref,
                        "code": code,
                    }
                )
            else:
                accepted.extend(validated)
                pending_covered.update(validated)
        return tuple(accepted), rejected

    def _batch_result(
        self,
        tool: str,
        submitted_refs: list[str],
        accepted_refs: tuple[str, ...],
        rejected: list[dict[str, JsonValue]],
        *,
        extra: dict[str, JsonValue] | None = None,
    ) -> str:
        status = (
            ToolResultStatus.PARTIAL
            if accepted_refs and rejected
            else ToolResultStatus.SUCCESS
            if accepted_refs
            else ToolResultStatus.REJECTED
        )
        data: dict[str, JsonValue] = {
            "submitted_count": len(submitted_refs),
            "accepted_count": len(accepted_refs),
            "rejected_count": len(rejected),
            "accepted_remediation_refs": list(accepted_refs),
            "rejected_decisions": cast(JsonValue, rejected),
        }
        if extra:
            data.update(extra)
        diagnostics = tuple(
            ToolDiagnostic(
                str(item["code"]),
                "The pending finding cannot receive this remediation decision.",
                True,
                "remediation_refs",
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
                    "remediation_already_finalized",
                    "Remediator decisions are already final.",
                    False,
                ),
            ),
        ).to_json()

    def as_resolved_review_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="resolved_review", description_override=description)
        async def resolved_review(
            remediation_refs: list[str],
            outcome: Literal["resolved", "unresolved", "unclear"],
            evidence_summary: str,
        ) -> str:
            """Mark one or more pending existing findings as resolved, unresolved, or unclear."""
            return await collector.resolved_review(
                remediation_refs=remediation_refs,
                outcome=outcome,
                evidence_summary=evidence_summary,
            )

        return reject_unknown_arguments(resolved_review)

    def as_remediation_done_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="remediation_done", description_override=description)
        async def remediation_done() -> str:
            """Validate accumulated remediation decisions and finalize the Remediator stage."""
            return await collector.finalize()

        return reject_unknown_arguments(remediation_done)

    def bindings(
        self,
        resolved_review_description: str,
        done_description: str,
    ) -> tuple[RoleOutputToolBinding, RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("resolved_review", 2),
                self.as_resolved_review_tool(resolved_review_description),
            ),
            RoleOutputToolBinding(
                ToolContractReference("remediation_done", 2),
                self.as_remediation_done_tool(done_description),
                self,
            ),
        )


class RemediationValidator:
    """Validate a persisted Remediator Artifact against its frozen input constraints."""

    def __init__(self, codec: RemediationCodec) -> None:
        self._codec = codec

    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]:
        return ()

    async def validate(self, payload: bytes) -> ValidatedRemediationBatch:
        return ValidatedRemediationBatch(self._codec.decode(payload))
