"""Deduplicator tools for the post-verifier dedup gate.

Two tools drive the Deduplicator stage:

- ``deduplicate``: accept or deny one or more survived findings by their
  ``verdict_decision_id``. Accept publishes the finding; deny suppresses it
  as a duplicate of an existing finding.
- ``deduplicate_done``: validate that every survived finding is covered
  exactly once and produce the final validated dedup batch.
"""

from typing import Literal, cast

from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.findings.domain.dedup import (
    DedupDecision,
    DedupDecisionSource,
    DedupOutcome,
)
from codelens.findings.infrastructure.dedup_codec import (
    DedupCodec,
    ValidatedDedupBatch,
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


class DeduplicationCollector:
    """Accumulate Deduplicator decisions across multiple tool calls.

    The Deduplicator calls ``deduplicate`` (accept/deny) multiple times to
    accumulate decisions for survived findings. When finished, it calls
    ``deduplicate_done`` to validate that all survived findings are covered
    exactly once and produce the final output.
    """

    def __init__(self, codec: DedupCodec) -> None:
        self._codec = codec
        self._decisions: list[DedupDecision] = []
        self._covered_ids: set[str] = set()
        self._finalized: tuple[DedupDecision, ...] | None = None

    @property
    def is_completed(self) -> bool:
        return self._finalized is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        return ()

    def final_output(self) -> tuple[DedupDecision, ...]:
        if self._finalized is None:
            raise RuntimeError("Deduplicator has not finalized decisions")
        return self._finalized

    async def deduplicate(
        self,
        verdict_decision_ids: list[str],
        outcome: Literal["accept", "deny"],
    ) -> str:
        """Record an accept or deny decision for one or more survived findings."""
        if self._finalized is not None:
            return self._completed_result("deduplicate")

        validated_ids, rejected = self._partition_verdict_decision_ids(verdict_decision_ids)
        if not validated_ids:
            return self._batch_result("deduplicate", verdict_decision_ids, validated_ids, rejected)
        dedup_outcome = DedupOutcome.ACCEPT if outcome == "accept" else DedupOutcome.DENY
        self._decisions.extend(
            DedupDecision(
                verdict_decision_id=verdict_decision_id,
                outcome=dedup_outcome,
                decision_source=DedupDecisionSource.LLM,
            )
            for verdict_decision_id in validated_ids
        )
        self._covered_ids.update(validated_ids)
        return self._batch_result(
            "deduplicate",
            verdict_decision_ids,
            validated_ids,
            rejected,
            extra={"outcome": outcome, "decision_count": len(self._decisions)},
        )

    async def finalize(self) -> str:
        """Validate all accumulated decisions and finalize."""
        if self._finalized is not None:
            return self._completed_result("deduplicate_done")
        try:
            finalized = self._codec.decode_decisions(self._decisions)
        except ValueError:
            missing = sorted(self._codec.expected_ids - self._covered_ids)
            return ToolResult(
                "deduplicate_done",
                ToolResultStatus.NEEDS_ACTION,
                {
                    "missing_verdict_decision_ids": cast(JsonValue, missing),
                    "decision_count": len(self._decisions),
                },
                (
                    ToolDiagnostic(
                        "missing_dedup_decisions",
                        "Every survived finding requires exactly one dedup decision.",
                        True,
                    ),
                ),
            ).to_json()
        self._finalized = finalized
        return ToolResult(
            "deduplicate_done",
            ToolResultStatus.SUCCESS,
            {
                "dedup_count": len(finalized),
                "covered_finding_count": len(self._covered_ids),
            },
        ).to_json()

    def _partition_verdict_decision_ids(
        self, verdict_decision_ids: list[str]
    ) -> tuple[tuple[str, ...], list[dict[str, JsonValue]]]:
        accepted: list[str] = []
        rejected: list[dict[str, JsonValue]] = []
        pending_covered = set(self._covered_ids)
        for input_index, verdict_decision_id in enumerate(verdict_decision_ids):
            try:
                validated = self._codec.validate_new_ids(
                    [verdict_decision_id], pending_covered
                )
            except ValueError as error:
                message = str(error)
                code = (
                    "unknown_verdict"
                    if "unknown verdict" in message
                    else "duplicate_verdict_dedup"
                )
                rejected.append(
                    {
                        "input_index": input_index,
                        "verdict_decision_id": verdict_decision_id,
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
            "accepted_verdict_decision_ids": list(accepted_ids),
            "rejected_decisions": cast(JsonValue, rejected),
        }
        if extra:
            data.update(extra)
        diagnostics = tuple(
            ToolDiagnostic(
                str(item["code"]),
                "The survived finding cannot receive this dedup decision.",
                True,
                "verdict_decision_ids",
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
                    "dedup_already_finalized",
                    "Deduplicator decisions are already final.",
                    False,
                ),
            ),
        ).to_json()

    def as_deduplicate_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="deduplicate", description_override=description)
        async def deduplicate(
            verdict_decision_ids: list[str],
            outcome: Literal["accept", "deny"],
        ) -> str:
            """Accept or deny one or more survived findings."""
            return await collector.deduplicate(
                verdict_decision_ids=verdict_decision_ids,
                outcome=outcome,
            )

        return reject_unknown_arguments(deduplicate)

    def as_deduplicate_done_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="deduplicate_done", description_override=description)
        async def deduplicate_done() -> str:
            """Validate accumulated dedup decisions and finalize the Deduplicator stage."""
            return await collector.finalize()

        return reject_unknown_arguments(deduplicate_done)

    def bindings(
        self,
        deduplicate_description: str,
        done_description: str,
    ) -> tuple[RoleOutputToolBinding, RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("deduplicate", 2),
                self.as_deduplicate_tool(deduplicate_description),
            ),
            RoleOutputToolBinding(
                ToolContractReference("deduplicate_done", 2),
                self.as_deduplicate_done_tool(done_description),
                self,
            ),
        )


class DedupValidator:
    """Validate a persisted Deduplicator Artifact against its frozen input constraints."""

    def __init__(self, codec: DedupCodec) -> None:
        self._codec = codec

    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]:
        return ()

    async def validate(self, payload: bytes) -> ValidatedDedupBatch:
        return ValidatedDedupBatch(self._codec.decode(payload))
