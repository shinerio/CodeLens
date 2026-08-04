import hashlib
import json

from codelens.findings.domain.candidates import CandidateFinding
from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingDisposition,
)
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome


class FindingPublisher:
    """Derive final v2 Findings from Candidate and Final Verifier verdict state."""

    @staticmethod
    def build(
        *,
        task_id: str,
        candidates: tuple[CandidateFinding, ...],
        verdicts: tuple[VerdictDecision, ...],
    ) -> tuple[Finding, ...]:
        by_candidate = {item.candidate_id: item for item in candidates}
        findings: list[Finding] = []
        for verdict in verdicts:
            if verdict.outcome is not VerdictOutcome.ACCEPT:
                continue
            # Use the first cluster to get the canonical candidate
            if not verdict.cluster_ids:
                continue
            first_cluster_id = verdict.cluster_ids[0]
            # Find a candidate that belongs to this cluster
            canonical = None
            for candidate in candidates:
                if hasattr(candidate, "cluster_id") and candidate.cluster_id == first_cluster_id:
                    canonical = candidate
                    break
            if canonical is None:
                continue
            # Collect all candidates from all merged clusters
            sources = []
            for cluster_id in verdict.cluster_ids:
                for candidate in candidates:
                    if hasattr(candidate, "cluster_id") and candidate.cluster_id == cluster_id:
                        sources.append(candidate)
            if not sources:
                continue
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "candidate_fingerprints": sorted(
                            item.fingerprint for item in sources
                        ),
                        "cluster_ids": sorted(verdict.cluster_ids),
                        "content": verdict.content,
                        "recommendation": verdict.recommendation,
                        "severity": (
                            verdict.severity.value
                            if verdict.severity is not None
                            else canonical.severity.value
                        ),
                        "title": verdict.title,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            finding_id = "finding_" + hashlib.sha256(
                f"{task_id}\0{fingerprint}".encode()
            ).hexdigest()
            findings.append(
                Finding(
                    finding_id=finding_id,
                    fingerprint=fingerprint,
                    reviewer_id=canonical.reviewer_reference,
                    category=verdict.category or canonical.category,
                    title=verdict.title or canonical.title,
                    severity=verdict.severity or canonical.severity,
                    disposition=FindingDisposition.BLOCKING,
                    confidence=None,
                    primary_location=canonical.primary_location,
                    related_locations=canonical.related_locations,
                    changed_hunk_id=canonical.changed_hunk_id,
                    change_origin=ChangeOrigin.INTRODUCED,
                    evidence=tuple(
                        Evidence(
                            kind="candidate_excerpt",
                            description="Validated Candidate evidence",
                            artifact_ref=None,
                            excerpt_hash=evidence_hash,
                        )
                        for evidence_hash in canonical.evidence_hashes
                    ),
                    impact=verdict.content or canonical.content,
                    explanation=verdict.content or canonical.content,
                    reproduction=None,
                    recommendation=(
                        verdict.recommendation or canonical.recommendation
                    ),
                    rule_sources=(),
                    primary_dimension=verdict.primary_dimension or canonical.primary_dimension,
                    secondary_dimensions=verdict.secondary_dimensions or canonical.secondary_dimensions,
                    evidence_strength=(
                        verdict.evidence_strength.value
                        if verdict.evidence_strength is not None
                        else canonical.evidence_strength.value
                    ),
                    impact_certainty=(
                        verdict.impact_certainty.value
                        if verdict.impact_certainty is not None
                        else canonical.impact_certainty.value
                    ),
                    reproducibility=(
                        verdict.reproducibility.value
                        if verdict.reproducibility is not None
                        else canonical.reproducibility.value
                    ),
                    source_reviewer_references=tuple(
                        sorted({item.reviewer_reference for item in sources})
                    ),
                )
            )
        return tuple(sorted(findings, key=lambda item: item.finding_id))
