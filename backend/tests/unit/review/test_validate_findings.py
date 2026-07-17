import hashlib
import json
from pathlib import Path

import pytest

from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.review.application.validate_findings import (
    FindingValidationError,
    FindingValidator,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import correctness_agent
from codelens.workspace.domain.models import (
    ChangedHunk,
    ChangeIndex,
    RepositoryFingerprint,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotEntry,
    SnapshotManifest,
    TaskWorktree,
)


def _validator() -> FindingValidator:
    excerpt_hash = hashlib.sha256(b"return False\n").hexdigest()
    worktree = TaskWorktree(
        "worktree-1", "review-1", "a" * 64, Path("/owned"), "b" * 40, "c" * 64
    )
    snapshot = ReviewSnapshot(
        "snapshot-1",
        worktree,
        ReviewTarget("a" * 40, "b" * 40, None),
        RepositoryFingerprint("b" * 40, "d" * 64, "e" * 64),
        SnapshotManifest(
            ("src/state.py",),
            (),
            (),
            entries=(SnapshotEntry("src/state.py", "file", 0o644, 13, "f" * 64, None, "target"),),
        ),
        ChangeIndex((ChangedHunk("hunk-1", "src/state.py", 2, 2, "new", excerpt_hash),)),
    )
    return FindingValidator(
        task_id="review-1",
        node_key="correctness:v1:0:root",
        snapshot=snapshot,
        agent=correctness_agent(),
        codec=AgentOutputCodec("1"),
    )


def _payload(path: str = "src/state.py", hunk_id: str = "hunk-1") -> bytes:
    excerpt_hash = hashlib.sha256(b"return False\n").hexdigest()
    return json.dumps(
        {
            "schema_version": "1",
            "findings": [
                {
                    "reviewer_id": "correctness",
                    "category": "logic",
                    "title": "Inverted result",
                    "severity": "high",
                    "disposition": "blocking",
                    "confidence": 0.95,
                    "primary_location": {
                        "path": path,
                        "start_line": 2,
                        "end_line": 2,
                        "side": "new",
                        "excerpt_hash": excerpt_hash,
                        "is_deleted": False,
                    },
                    "related_locations": [],
                    "changed_hunk_id": hunk_id,
                    "change_origin": "introduced",
                    "evidence": [
                        {
                            "kind": "excerpt",
                            "description": "Changed return is inverted.",
                            "artifact_ref": None,
                            "excerpt_hash": excerpt_hash,
                        }
                    ],
                    "impact": "Callers receive the wrong state.",
                    "explanation": "The changed branch returns the inverse.",
                    "reproduction": None,
                    "recommendation": "Return the intended value.",
                    "suggested_patch": None,
                    "rule_sources": [],
                }
            ],
        }
    ).encode()


async def test_derives_stable_identity_after_path_hunk_and_evidence_validation() -> None:
    first = await _validator().validate(_payload())
    second = await _validator().validate(_payload())

    assert first == second
    assert first.findings[0].finding_id.startswith("finding_")
    assert first.findings[0].changed_hunk_id == "hunk-1"


@pytest.mark.parametrize(
    "payload",
    (_payload("../escape.py"), _payload(hunk_id="hunk-missing")),
)
async def test_rejects_paths_outside_snapshot_and_unknown_hunks(payload: bytes) -> None:
    with pytest.raises(FindingValidationError):
        await _validator().validate(payload)
