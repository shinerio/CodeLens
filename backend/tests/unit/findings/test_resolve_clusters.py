"""Test cluster preparation in the new resolve_clusters architecture."""

import pytest

from codelens.findings.application.cluster_candidates import CandidateClusterer
from codelens.findings.application.resolve_clusters import (
    ClusterService,
)
from codelens.findings.domain.candidates import (
    CandidateFinding,
    EvidenceStrength,
    ImpactCertainty,
    Reproducibility,
)
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.models import FindingSeverity, SourceLocation


def candidate(
    candidate_id: str,
    *,
    category: str = "authentication",
    title: str = "Missing signature check",
    evidence_hashes: tuple[str, ...] = ("a" * 64,),
    impact_certainty: ImpactCertainty = ImpactCertainty.CONFIRMED,
    path: str = "src/webhook.py",
) -> CandidateFinding:
    return CandidateFinding(
        task_id="review-1",
        candidate_id=candidate_id,
        run_id="run-private",
        snapshot_id="snapshot-1",
        reviewer_reference="security:v1",
        category=category,
        title=title,
        severity=FindingSeverity.HIGH,
        primary_dimension="security",
        secondary_dimensions=(),
        evidence_strength=EvidenceStrength.DIRECT,
        impact_certainty=impact_certainty,
        reproducibility=Reproducibility.DETERMINISTIC,
        primary_location=SourceLocation(path, 5, 5, "new", "a" * 64, False),
        related_locations=(),
        changed_hunk_id="hunk-1",
        existing_code_hash="a" * 64,
        evidence_hashes=evidence_hashes,
        content="The changed path accepts unsigned requests.",
        recommendation="Verify the signature before parsing.",
        fingerprint="b" * 64,
    )


class FakeClusterStore:
    """In-memory ClusterStorePort fake for assertions."""

    def __init__(self) -> None:
        self.saved: list[
            tuple[str, str, tuple[FindingCluster, ...]]
        ] = []

    async def save_clusters(
        self,
        task_id: str,
        snapshot_id: str,
        clusters: tuple[FindingCluster, ...],
    ) -> None:
        self.saved.append((task_id, snapshot_id, clusters))


def test_clusterer_groups_candidates_with_identical_evidence() -> None:
    first = candidate("candidate-a")
    second = candidate("candidate-b")

    clusters = CandidateClusterer().cluster((first, second))

    assert len(clusters) == 1
    cluster = clusters[0]
    assert isinstance(cluster, FindingCluster)
    assert set(cluster.candidate_ids) == {"candidate-a", "candidate-b"}
    # canonical is the lexicographically-first candidate_id
    assert cluster.canonical_candidate_id == "candidate-a"
    # canonical fields are copied from the canonical candidate
    assert cluster.title == first.title
    assert cluster.category == first.category
    assert cluster.severity == first.severity
    assert cluster.content == first.content
    assert cluster.recommendation == first.recommendation
    assert cluster.primary_dimension == first.primary_dimension
    assert cluster.secondary_dimensions == first.secondary_dimensions
    assert cluster.evidence_strength == first.evidence_strength
    assert cluster.impact_certainty == first.impact_certainty
    assert cluster.reproducibility == first.reproducibility


def test_clusterer_splits_when_evidence_differs() -> None:
    same_evidence = candidate("candidate-a")
    differing_evidence = candidate(
        "candidate-b", evidence_hashes=("z" * 64,)
    )

    clusters = CandidateClusterer().cluster((same_evidence, differing_evidence))

    assert len(clusters) == 2
    # each cluster contains exactly one candidate
    assert {cluster.candidate_ids[0] for cluster in clusters} == {
        "candidate-a",
        "candidate-b",
    }
    # canonical_candidate_id equals the only member
    for cluster in clusters:
        assert cluster.canonical_candidate_id == cluster.candidate_ids[0]


def test_clusterer_splits_when_location_or_category_or_title_differ() -> None:
    base = candidate("candidate-a")
    other_path = candidate("candidate-b", path="src/other.py")
    other_category = candidate("candidate-c", category="crypto")
    other_title = candidate("candidate-d", title="Unsigned payload")

    clusters = CandidateClusterer().cluster((base, other_path, other_category, other_title))

    assert len(clusters) == 4
    cluster_ids_sorted = sorted(cluster.cluster_id for cluster in clusters)
    assert [c.cluster_id for c in clusters] == cluster_ids_sorted


def test_clusterer_returns_empty_for_empty_input() -> None:
    assert CandidateClusterer().cluster(()) == ()


@pytest.mark.asyncio
async def test_cluster_service_prepare_persists_clusters_via_store() -> None:
    store = FakeClusterStore()
    service = ClusterService(store=store)
    first = candidate("candidate-a")
    second = candidate("candidate-b")

    result = await service.prepare(
        task_id="review-1",
        snapshot_id="snapshot-1",
        candidates=(first, second),
    )

    assert len(store.saved) == 1
    saved_task_id, saved_snapshot_id, saved_clusters = store.saved[0]
    assert saved_task_id == "review-1"
    assert saved_snapshot_id == "snapshot-1"
    assert saved_clusters == result
    assert len(result) == 1
    assert set(result[0].candidate_ids) == {"candidate-a", "candidate-b"}
    assert result[0].canonical_candidate_id == "candidate-a"


@pytest.mark.asyncio
async def test_cluster_service_prepare_persists_empty_when_no_candidates() -> None:
    store = FakeClusterStore()
    service = ClusterService(store=store)

    result = await service.prepare(
        task_id="review-empty",
        snapshot_id="snapshot-empty",
        candidates=(),
    )

    assert result == ()
    assert store.saved == [("review-empty", "snapshot-empty", ())]
