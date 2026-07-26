import hashlib
import json
from pathlib import Path

import pytest

from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.review.application.validate_findings import (
    FindingValidationError,
    FindingValidator,
)
from codelens.review.domain.ports import SnapshotRead
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


class _ExcerptReader:
    async def read(
        self,
        _snapshot: ReviewSnapshot,
        _path: str,
        _start_line: int,
        _end_line: int,
        _side: str,
        _max_bytes: int,
    ) -> SnapshotRead:
        payload = b"return False\n"
        return SnapshotRead(payload, hashlib.sha256(payload).hexdigest(), False)


def _validator(*, excerpt_reader: _ExcerptReader | None = None) -> FindingValidator:
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
        excerpt_reader=excerpt_reader,
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


async def test_accepts_a_primary_excerpt_hash_for_a_subrange_of_a_changed_hunk() -> None:
    batch = await _validator(excerpt_reader=_ExcerptReader()).validate(_payload())

    assert batch.findings[0].primary_location.excerpt_hash == hashlib.sha256(
        b"return False\n"
    ).hexdigest()


async def test_deduplicates_repeated_findings_in_stable_order() -> None:
    payload = json.loads(_payload())
    payload["findings"].append(payload["findings"][0])

    validator = _validator()
    batch = await validator.validate(json.dumps(payload).encode())

    assert len(batch.findings) == 1
    assert batch.findings[0].title == "Inverted result"
    assert len(validator.warnings) == 1
    assert validator.warnings[0].reason_code == "duplicate"


@pytest.mark.parametrize(
    "payload",
    (_payload("../escape.py"), _payload(hunk_id="hunk-missing")),
)
async def test_skips_invalid_candidates_and_returns_an_empty_trusted_batch(payload: bytes) -> None:
    validator = _validator()

    batch = await validator.validate(payload)

    assert batch.findings == ()
    assert len(validator.warnings) == 1
    assert validator.warnings[0].candidate_index == 0
    assert validator.warnings[0].reason_code == "invalid"


async def test_keeps_valid_candidates_when_another_candidate_is_invalid() -> None:
    payload = json.loads(_payload())
    invalid = json.loads(json.dumps(payload["findings"][0]))
    invalid["primary_location"]["path"] = "../escape.py"
    payload["findings"].insert(0, invalid)
    validator = _validator()

    batch = await validator.validate(json.dumps(payload).encode())

    assert [finding.title for finding in batch.findings] == ["Inverted result"]
    assert len(validator.warnings) == 1
    assert validator.warnings[0].candidate_index == 0


async def test_rejects_an_unparseable_output_envelope() -> None:
    with pytest.raises(FindingValidationError):
        await _validator().validate(b"not-json")
