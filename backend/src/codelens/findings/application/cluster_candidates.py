import hashlib
import json
import re
import unicodedata

from codelens.findings.domain.candidates import CandidateFinding
from codelens.findings.domain.resolution import FindingCluster


def _normalized_root_cause(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


class CandidateClusterer:
    """Partition validated Candidates by deterministic evidence and root-cause facts."""

    def cluster(
        self, candidates: tuple[CandidateFinding, ...]
    ) -> tuple[FindingCluster, ...]:
        groups: dict[str, list[str]] = {}
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
            groups.setdefault(key, []).append(candidate.candidate_id)
        clusters = tuple(
            FindingCluster(
                "cluster_" + hashlib.sha256(key.encode()).hexdigest(),
                tuple(sorted(candidate_ids)),
            )
            for key, candidate_ids in groups.items()
        )
        return tuple(sorted(clusters, key=lambda cluster: cluster.cluster_id))
