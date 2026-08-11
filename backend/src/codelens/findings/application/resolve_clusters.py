"""Cluster Candidate audit state and derive deterministic verdicts.

The Final Verifier consumes the resulting ``FindingCluster`` objects via the
verdict/merge tools. This module no longer carries a separate resolution or
verification step — the verdict stage owns both.
"""

from typing import Protocol

from codelens.findings.application.cluster_candidates import CandidateClusterer
from codelens.findings.domain.candidates import CandidateFinding
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.verdict import VerdictDecision


def direct_verdicts(clusters: tuple[FindingCluster, ...]) -> tuple[VerdictDecision, ...]:
    """Accept direct-evidence clusters and retain all others as denied audit decisions."""

    return tuple(
        (
            VerdictDecision.accept(cluster_ids=(cluster.cluster_id,))
            if cluster.evidence_strength.value == "direct"
            else VerdictDecision.deny(cluster_ids=(cluster.cluster_id,))
        )
        for cluster in clusters
    )


class ClusterStorePort(Protocol):
    """Persist deterministic clusters for the Final Verifier."""

    async def save_clusters(
        self,
        task_id: str,
        snapshot_id: str,
        clusters: tuple[FindingCluster, ...],
    ) -> None: ...


class ClusterService:
    """Cluster Candidate audit state and persist the resulting clusters."""

    def __init__(
        self,
        store: ClusterStorePort,
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
