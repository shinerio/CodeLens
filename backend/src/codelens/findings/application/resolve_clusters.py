import hashlib
import json
from collections.abc import Sequence
from typing import Protocol

from codelens.findings.application.cluster_candidates import CandidateClusterer
from codelens.findings.domain.candidates import (
    CandidateFinding,
    EvidenceStrength,
    ImpactCertainty,
)
from codelens.findings.domain.resolution import FindingCluster, ResolutionDecision
from codelens.findings.infrastructure.resolver_output import ResolverOutputCodec


class ResolutionStorePort(Protocol):
    """Persist deterministic clusters and immutable Resolver decisions."""

    async def save_clusters(
        self,
        task_id: str,
        snapshot_id: str,
        clusters: tuple[FindingCluster, ...],
    ) -> None: ...

    async def save_decisions(
        self, task_id: str, decisions: tuple[ResolutionDecision, ...]
    ) -> None: ...


def validate_resolution(
    cluster: FindingCluster,
    candidates: tuple[CandidateFinding, ...],
    decision: ResolutionDecision,
) -> ResolutionDecision:
    """Apply the Resolver codec's no-invention policy to one typed decision."""

    return ResolverOutputCodec((cluster,), candidates).validate((decision,))[0]


class ResolutionService:
    """Cluster Candidate audit state and commit constrained Resolver decisions."""

    def __init__(
        self,
        store: ResolutionStorePort,
        clusterer: CandidateClusterer | None = None,
    ) -> None:
        self._store = store
        self._clusterer = clusterer or CandidateClusterer()

    async def prepare(
        self,
        *,
        task_id: str,
        snapshot_id: str,
        candidates: tuple[CandidateFinding, ...],
    ) -> tuple[FindingCluster, ...]:
        clusters = self._clusterer.cluster(candidates)
        await self._store.save_clusters(task_id, snapshot_id, clusters)
        return clusters

    async def commit(
        self,
        *,
        task_id: str,
        clusters: tuple[FindingCluster, ...],
        candidates: tuple[CandidateFinding, ...],
        resolver_output: object,
    ) -> tuple[ResolutionDecision, ...]:
        decisions = ResolverOutputCodec(clusters, candidates).decode(resolver_output)
        await self._store.save_decisions(task_id, decisions)
        return decisions

    @staticmethod
    def resolver_input_payload(
        *,
        plan_hash: str,
        clusters: tuple[FindingCluster, ...],
        candidates: tuple[CandidateFinding, ...],
    ) -> bytes:
        """Shuffle Candidate presentation deterministically without execution ordering."""

        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        ordered_ids = sorted(
            by_id,
            key=lambda candidate_id: hashlib.sha256(
                f"{plan_hash}\0{candidate_id}".encode()
            ).hexdigest(),
        )
        payload = {
            "clusters": [
                {
                    "candidate_ids": list(cluster.candidate_ids),
                    "cluster_id": cluster.cluster_id,
                }
                for cluster in clusters
            ],
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "category": by_id[candidate_id].category,
                    "content": by_id[candidate_id].content,
                    "evidence_hashes": list(by_id[candidate_id].evidence_hashes),
                    "evidence_strength": by_id[candidate_id].evidence_strength.value,
                    "impact_certainty": by_id[candidate_id].impact_certainty.value,
                    "location": {
                        "end_line": by_id[candidate_id].primary_location.end_line,
                        "path": by_id[candidate_id].primary_location.path,
                        "side": by_id[candidate_id].primary_location.side,
                        "start_line": by_id[candidate_id].primary_location.start_line,
                    },
                    "recommendation": by_id[candidate_id].recommendation,
                    "severity": by_id[candidate_id].severity.value,
                    "title": by_id[candidate_id].title,
                }
                for candidate_id in ordered_ids
            ],
            "schema_version": "1",
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def direct_decisions(
        candidates: Sequence[CandidateFinding],
        *,
        clusters: tuple[FindingCluster, ...] | None = None,
    ) -> tuple[ResolutionDecision, ...]:
        """Publish direct confirmed/plausible evidence and audit-suppress the remainder."""

        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        direct_clusters = clusters or tuple(
            FindingCluster(
                "cluster_direct_"
                + hashlib.sha256(candidate.candidate_id.encode()).hexdigest(),
                (candidate.candidate_id,),
            )
            for candidate in candidates
        )
        decisions: list[ResolutionDecision] = []
        for cluster in direct_clusters:
            members = tuple(by_id[candidate_id] for candidate_id in cluster.candidate_ids)
            publishable = tuple(
                candidate
                for candidate in members
                if candidate.evidence_strength is EvidenceStrength.DIRECT
                and candidate.impact_certainty
                in {ImpactCertainty.CONFIRMED, ImpactCertainty.PLAUSIBLE}
            )
            if publishable:
                candidate = publishable[0]
                decisions.append(
                    ResolutionDecision.publish(
                        cluster=cluster,
                        canonical_candidate_id=candidate.candidate_id,
                        merged_candidate_ids=tuple(
                            item.candidate_id for item in publishable
                        ),
                        severity=candidate.severity,
                        title=candidate.title,
                        content=candidate.content,
                        recommendation=candidate.recommendation,
                        reason_code="direct-publication-policy",
                    )
                )
            else:
                decisions.append(
                    ResolutionDecision.suppress(
                        cluster=cluster,
                        reason_code="direct-publication-evidence-insufficient",
                    )
                )
        return tuple(decisions)
