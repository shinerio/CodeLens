import hashlib
import json
import re
import unicodedata

from codelens.findings.domain.candidates import CandidateFinding
from codelens.findings.domain.clusters import FindingCluster


def _normalized_root_cause(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


class CandidateClusterer:
    """Partition validated Candidates by deterministic evidence and root-cause facts."""

    def cluster(
        self, candidates: tuple[CandidateFinding, ...]
    ) -> tuple[FindingCluster, ...]:
        groups: dict[str, list[CandidateFinding]] = {}
        for candidate in candidates:
            key_payload = {
                "category": _normalized_root_cause(candidate.category),
                "evidence_hashes": sorted(candidate.evidence_hashes),
                "impact_class": candidate.impact_certainty.value,
                "location": {
                    "end_line": candidate.primary_location.end_line,
                    "path": candidate.primary_location.path,
                    "side": candidate.primary_location.side,
                    "start_line": candidate.primary_location.start_line,
                },
                "snapshot_id": candidate.snapshot_id,
                "title": _normalized_root_cause(candidate.title),
            }
            key = json.dumps(key_payload, sort_keys=True, separators=(",", ":"))
            groups.setdefault(key, []).append(candidate)
        clusters = tuple(
            self._build_cluster(
                "cluster_" + hashlib.sha256(key.encode()).hexdigest(),
                tuple(sorted(members, key=lambda item: item.candidate_id)),
            )
            for key, members in groups.items()
        )
        return tuple(sorted(clusters, key=lambda cluster: cluster.cluster_id))

    @staticmethod
    def _build_cluster(
        cluster_id: str, members: tuple[CandidateFinding, ...]
    ) -> FindingCluster:
        canonical = members[0]
        return FindingCluster(
            cluster_id=cluster_id,
            candidate_ids=tuple(member.candidate_id for member in members),
            canonical_candidate_id=canonical.candidate_id,
            title=canonical.title,
            category=canonical.category,
            severity=canonical.severity,
            content=canonical.content,
            recommendation=canonical.recommendation,
            primary_dimension=canonical.primary_dimension,
            secondary_dimensions=canonical.secondary_dimensions,
            evidence_strength=canonical.evidence_strength,
            impact_certainty=canonical.impact_certainty,
            reproducibility=canonical.reproducibility,
        )
