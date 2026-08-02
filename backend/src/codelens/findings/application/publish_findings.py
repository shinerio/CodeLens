import hashlib
import json

from codelens.findings.domain.candidates import CandidateFinding
from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingDisposition,
)
from codelens.findings.domain.resolution import (
    ResolutionDecision,
    ResolutionOutcome,
    VerificationDecision,
    VerificationOutcome,
)


class FindingPublisher:
    """Derive final v2 Findings only from resolved Candidate and Verifier audit state."""

    @staticmethod
    def build(
        *,
        task_id: str,
        candidates: tuple[CandidateFinding, ...],
        resolutions: tuple[ResolutionDecision, ...],
        verifications: tuple[VerificationDecision, ...] = (),
    ) -> tuple[Finding, ...]:
        by_candidate = {item.candidate_id: item for item in candidates}
        verification_by_cluster = {item.target_id: item for item in verifications}
        findings: list[Finding] = []
        for resolution in resolutions:
            is_publishable = resolution.outcome is ResolutionOutcome.PUBLISH
            if resolution.outcome is ResolutionOutcome.VERIFY:
                verification = verification_by_cluster.get(resolution.cluster_id)
                is_publishable = (
                    verification is not None
                    and verification.outcome is VerificationOutcome.CONFIRMED
                )
            if not is_publishable or resolution.canonical_candidate_id is None:
                continue
            try:
                canonical = by_candidate[resolution.canonical_candidate_id]
                sources = tuple(
                    by_candidate[candidate_id]
                    for candidate_id in resolution.merged_candidate_ids
                )
            except KeyError as error:
                raise ValueError(
                    "Resolution publication references an unknown Candidate"
                ) from error
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "candidate_fingerprints": sorted(
                            item.fingerprint for item in sources
                        ),
                        "cluster_id": resolution.cluster_id,
                        "content": resolution.content,
                        "recommendation": resolution.recommendation,
                        "severity": (
                            resolution.severity.value
                            if resolution.severity is not None
                            else canonical.severity.value
                        ),
                        "title": resolution.title,
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
                    category=canonical.category,
                    title=resolution.title or canonical.title,
                    severity=resolution.severity or canonical.severity,
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
                    impact=resolution.content or canonical.content,
                    explanation=resolution.content or canonical.content,
                    reproduction=None,
                    recommendation=(
                        resolution.recommendation or canonical.recommendation
                    ),
                    rule_sources=(),
                    primary_dimension=canonical.primary_dimension,
                    secondary_dimensions=canonical.secondary_dimensions,
                    evidence_strength=canonical.evidence_strength.value,
                    impact_certainty=canonical.impact_certainty.value,
                    reproducibility=canonical.reproducibility.value,
                    source_reviewer_references=tuple(
                        sorted({item.reviewer_reference for item in sources})
                    ),
                )
            )
        return tuple(sorted(findings, key=lambda item: item.finding_id))
