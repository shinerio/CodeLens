"""Final Verifier tools for the simplified two-stage Review DAG."""

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

    The Final Verifier calls submit_verdict multiple times to accumulate
    accept/deny decisions for clusters. When finished, it calls finalize_verdicts
    to validate that all clusters are covered and produce the final output.
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

    async def submit(
        self,
        cluster_ids: list[str],
        outcome: Literal["accept", "deny"],
        path: str | None = None,
        side: Literal["old", "new"] | None = None,
        existing_code: str | None = None,
        title: str | None = None,
        content: str | None = None,
        recommendation: str | None = None,
        category: str | None = None,
        severity: Literal["critical", "high", "medium", "low", "info"] | None = None,
        primary_dimension: str | None = None,
        secondary_dimensions: list[str] | None = None,
        evidence_strength: Literal["direct", "inferred", "weak"] | None = None,
        impact_certainty: Literal["confirmed", "plausible", "unclear"] | None = None,
        reproducibility: Literal["deterministic", "conditional", "unknown"] | None = None,
    ) -> str:
        """Accumulate one verdict decision."""
        if self._finalized is not None:
            raise ValueError("Final Verifier has already finalized decisions")

        from codelens.findings.domain.candidates import (
            EvidenceStrength,
            ImpactCertainty,
            Reproducibility,
        )
        from codelens.findings.domain.models import FindingSeverity

        decision = VerdictDecision(
            cluster_ids=tuple(cluster_ids),
            outcome=VerdictOutcome(outcome),
            path=path,
            side=side,
            existing_code=existing_code,
            title=title,
            content=content,
            recommendation=recommendation,
            category=category,
            severity=FindingSeverity(severity) if severity else None,
            primary_dimension=primary_dimension,
            secondary_dimensions=(
                tuple(secondary_dimensions) if secondary_dimensions else None
            ),
            evidence_strength=(
                EvidenceStrength(evidence_strength) if evidence_strength else None
            ),
            impact_certainty=(
                ImpactCertainty(impact_certainty) if impact_certainty else None
            ),
            reproducibility=(
                Reproducibility(reproducibility) if reproducibility else None
            ),
        )
        self._decisions.append(decision)
        return f"Verdict accepted for {len(cluster_ids)} cluster(s). Total: {len(self._decisions)}"

    async def finalize(self) -> str:
        """Validate all accumulated decisions and finalize."""
        if self._finalized is not None:
            raise ValueError("Final Verifier has already finalized decisions")

        # Validate through codec (checks cluster coverage)
        self._finalized = self._codec.decode_decisions(self._decisions)
        return f"Final Verifier completed: {len(self._finalized)} verdict(s)"

    def as_submit_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="submit_verdict", description_override=description)
        async def submit_verdict(
            cluster_ids: list[str],
            outcome: Literal["accept", "deny"],
            path: str | None = None,
            side: Literal["old", "new"] | None = None,
            existing_code: str | None = None,
            title: str | None = None,
            content: str | None = None,
            recommendation: str | None = None,
            category: str | None = None,
            severity: Literal["critical", "high", "medium", "low", "info"] | None = None,
            primary_dimension: str | None = None,
            secondary_dimensions: list[str] | None = None,
            evidence_strength: Literal["direct", "inferred", "weak"] | None = None,
            impact_certainty: Literal["confirmed", "plausible", "unclear"] | None = None,
            reproducibility: Literal["deterministic", "conditional", "unknown"] | None = None,
        ) -> str:
            """Submit one verdict decision for one or more clusters."""
            return await collector.submit(
                cluster_ids=cluster_ids,
                outcome=outcome,
                path=path,
                side=side,
                existing_code=existing_code,
                title=title,
                content=content,
                recommendation=recommendation,
                category=category,
                severity=severity,
                primary_dimension=primary_dimension,
                secondary_dimensions=secondary_dimensions,
                evidence_strength=evidence_strength,
                impact_certainty=impact_certainty,
                reproducibility=reproducibility,
            )

        return submit_verdict

    def as_finalize_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="finalize_verdicts", description_override=description)
        async def finalize_verdicts() -> str:
            """Validate accumulated verdicts and finalize the Final Verifier stage."""
            return await collector.finalize()

        return finalize_verdicts

    def bindings(
        self, submit_description: str, finalize_description: str
    ) -> tuple[RoleOutputToolBinding, RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("submit_verdict", 1),
                self.as_submit_tool(submit_description),
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
