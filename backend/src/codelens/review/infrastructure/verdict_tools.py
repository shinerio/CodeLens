"""Final Verifier tools for the simplified two-stage Review DAG.

Three tools drive the Final Verifier stage:

- ``verdict``: accept or deny one or more clusters. Accept uses the cluster's
  canonical candidate fields; deny suppresses the cluster.
- ``merge``: synthesize a single Finding across one or more clusters. All
  Finding fields are required so the model owns the merged attributes.
- ``finalize_verdicts``: validate that every cluster is covered exactly once
  and produce the final validated verdict batch.
"""

from typing import Literal

from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome
from codelens.findings.infrastructure.verdict_codec import (
    ValidatedVerdictBatch,
    VerdictCodec,
)
from codelens.review.domain.ports import FindingValidationWarning
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding


class VerdictSubmissionCollector:
    """Accumulate Final Verifier decisions across multiple tool calls.

    The Final Verifier calls ``verdict`` (accept/deny) and ``merge`` multiple
    times to accumulate decisions for clusters. When finished, it calls
    ``finalize_verdicts`` to validate that all clusters are covered exactly
    once and produce the final output.
    """

    def __init__(self, codec: VerdictCodec) -> None:
        self._codec = codec
        self._decisions: list[VerdictDecision] = []
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
            raise ValueError("Final Verifier has already finalized decisions")

        outcome = VerdictOutcome.ACCEPT if action == "accept" else VerdictOutcome.DENY
        decision = VerdictDecision(
            cluster_ids=tuple(cluster_ids),
            outcome=outcome,
        )
        self._decisions.append(decision)
        verb = "accepted" if action == "accept" else "denied"
        return f"{len(cluster_ids)} cluster(s) {verb}. Total decisions: {len(self._decisions)}"

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
            raise ValueError("Final Verifier has already finalized decisions")

        from codelens.findings.domain.candidates import EvidenceStrength
        from codelens.findings.domain.models import FindingSeverity

        decision = VerdictDecision.merge(
            cluster_ids=tuple(cluster_ids),
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
        )
        self._decisions.append(decision)
        count = len(self._decisions)
        return f"Merged {len(cluster_ids)} cluster(s) into one Finding. Total decisions: {count}"

    async def finalize(self) -> str:
        """Validate all accumulated decisions and finalize."""
        if self._finalized is not None:
            raise ValueError("Final Verifier has already finalized decisions")

        self._finalized = self._codec.decode_decisions(self._decisions)
        return f"Final Verifier completed: {len(self._finalized)} verdict(s)"

    def as_verdict_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="verdict", description_override=description)
        async def verdict(
            cluster_ids: list[str],
            action: Literal["accept", "deny"],
        ) -> str:
            """Accept or deny one or more finding clusters."""
            return await collector.verdict(cluster_ids=cluster_ids, action=action)

        return verdict

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

        return merge

    def as_finalize_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(
            name_override="finalize_verdicts", description_override=description
        )
        async def finalize_verdicts() -> str:
            """Validate accumulated verdicts and finalize the Final Verifier stage."""
            return await collector.finalize()

        return finalize_verdicts

    def bindings(
        self,
        verdict_description: str,
        merge_description: str,
        finalize_description: str,
    ) -> tuple[RoleOutputToolBinding, RoleOutputToolBinding, RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("verdict", 1),
                self.as_verdict_tool(verdict_description),
            ),
            RoleOutputToolBinding(
                ToolContractReference("merge", 1),
                self.as_merge_tool(merge_description),
            ),
            RoleOutputToolBinding(
                ToolContractReference("finalize_verdicts", 1),
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
