import hashlib
import json

from codelens.findings.domain.candidates import CandidateFinding
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingDisposition,
)
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome


class FindingPublisher:
    """Derive final v2 Findings from Candidate, cluster, and Final Verifier state.

    ACCEPT verdicts publish a Finding using the cluster's canonical candidate
    fields. MERGE verdicts publish a Finding using the model-synthesized merge
    fields. DENY verdicts are suppressed.
    """

    @staticmethod
    def build(
        *,
        task_id: str,
        candidates: tuple[CandidateFinding, ...],
        verdicts: tuple[VerdictDecision, ...],
        clusters: tuple[FindingCluster, ...],
    ) -> tuple[Finding, ...]:
        by_candidate = {item.candidate_id: item for item in candidates}
        cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
        candidates_by_cluster: dict[str, list[CandidateFinding]] = {}
        for cluster in clusters:
            members = [
                by_candidate[candidate_id]
                for candidate_id in cluster.candidate_ids
                if candidate_id in by_candidate
            ]
            candidates_by_cluster[cluster.cluster_id] = members

        findings: list[Finding] = []
        for verdict in verdicts:
            if not verdict.is_publishable:
                continue
            sources: list[CandidateFinding] = []
            for cluster_id in verdict.cluster_ids:
                sources.extend(candidates_by_cluster.get(cluster_id, []))
            if not sources:
                continue
            primary_cluster = cluster_by_id.get(verdict.cluster_ids[0])
            if primary_cluster is None:
                continue
            canonical = by_candidate.get(primary_cluster.canonical_candidate_id)
            if canonical is None:
                canonical = sources[0]

            if verdict.outcome is VerdictOutcome.MERGE:
                # __post_init__ guarantees all merge fields are non-None
                # when outcome is MERGE, so narrowing via assert is safe.
                assert verdict.title is not None
                assert verdict.category is not None
                assert verdict.severity is not None
                assert verdict.content is not None
                assert verdict.recommendation is not None
                assert verdict.primary_dimension is not None
                assert verdict.evidence_strength is not None
                title = verdict.title
                category = verdict.category
                severity = verdict.severity
                content = verdict.content
                recommendation = verdict.recommendation
                primary_dimension = verdict.primary_dimension
                evidence_strength = verdict.evidence_strength
            else:
                title = primary_cluster.title
                category = primary_cluster.category
                severity = primary_cluster.severity
                content = primary_cluster.content
                recommendation = primary_cluster.recommendation
                primary_dimension = primary_cluster.primary_dimension
                evidence_strength = primary_cluster.evidence_strength

            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "candidate_fingerprints": sorted(
                            item.fingerprint for item in sources
                        ),
                        "cluster_ids": sorted(verdict.cluster_ids),
                        "content": content,
                        "recommendation": recommendation,
                        "severity": severity.value,
                        "title": title,
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
                    category=category,
                    title=title,
                    severity=severity,
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
                    impact=content,
                    explanation=content,
                    reproduction=None,
                    recommendation=recommendation,
                    rule_sources=(),
                    primary_dimension=primary_dimension,
                    evidence_strength=evidence_strength.value,
                    source_reviewer_references=tuple(
                        sorted({item.reviewer_reference for item in sources})
                    ),
                )
            )
        return tuple(sorted(findings, key=lambda item: item.finding_id))
