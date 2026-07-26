import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from codelens.instruction_policy.domain.models import (
    InstructionChain,
    InstructionDocument,
    ResolvedInstructionSet,
)
from codelens.review.application.context_builder import (
    ContextBuilder,
    ContextContainmentError,
    ContextIntegrityError,
    ReviewScopeLimits,
)
from codelens.review.application.review_scope import ReviewScopeLimitError
from codelens.workspace.domain.models import (
    ChangedHunk,
    ChangeIndex,
    RepositoryFingerprint,
    ReviewFileChange,
    ReviewSnapshot,
    ReviewTarget,
    SnapshotEntry,
    SnapshotManifest,
    TaskWorktree,
)


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _snapshot() -> tuple[ReviewSnapshot, ResolvedInstructionSet]:
    target_paths = (
        "src/renamed.py",
        "src/original.py",
        "src/deleted.py",
        "src/changed.py",
        "src/added.py",
    )
    root_rule = "Follow the repository rules.\n"
    root_document = InstructionDocument(
        "AGENTS.md",
        root_rule,
        _hash(root_rule.encode()),
        "agents",
        "",
        0,
    )
    entries = tuple(
        SnapshotEntry(
            path,
            "deleted" if path in {"src/original.py", "src/deleted.py"} else "file",
            0o644,
            0,
            _hash(b""),
            None,
            "target",
        )
        for path in target_paths
    ) + (
        SnapshotEntry(
            "AGENTS.md",
            "file",
            0o644,
            len(root_rule.encode()),
            _hash(root_rule.encode()),
            None,
            "instruction",
        ),
    )
    snapshot = ReviewSnapshot(
        snapshot_id="snapshot-internal",
        worktree=TaskWorktree(
            "worktree-1",
            "review-1",
            "a" * 64,
            Path("/private/internal-worktree"),
            "b" * 40,
            "c" * 64,
        ),
        target=ReviewTarget("d" * 40, "b" * 40, None),
        fingerprint=RepositoryFingerprint("b" * 40, "e" * 64, "f" * 64),
        manifest=SnapshotManifest(
            target_paths=target_paths,
            context_paths=(),
            instruction_paths=("AGENTS.md",),
            excluded_paths=(),
            entries=entries,
        ),
        change_index=ChangeIndex(
            hunks=(
                ChangedHunk("h4", "src/changed.py", 20, 22, "new", "1" * 64),
                ChangedHunk("h1", "src/added.py", 1, 3, "new", "2" * 64),
                ChangedHunk("h3", "src/changed.py", 5, 6, "new", "3" * 64),
                ChangedHunk("h2", "src/deleted.py", 1, 2, "old", "4" * 64),
                ChangedHunk("h5", "src/renamed.py", 7, 8, "new", "5" * 64),
            ),
            files=(
                ReviewFileChange("src/renamed.py", "renamed", old_path="src/original.py"),
                ReviewFileChange("src/deleted.py", "deleted"),
                ReviewFileChange("src/changed.py", "modified"),
                ReviewFileChange("src/added.py", "added"),
            ),
        ),
    )
    instructions = ResolvedInstructionSet(
        documents=(
            root_document,
            InstructionDocument(
                "unrelated/REVIEW.md",
                "Inactive rule body.\n",
                _hash(b"Inactive rule body.\n"),
                "review",
                "unrelated",
                100,
            ),
        ),
        chains=tuple(InstructionChain(path, ("AGENTS.md",)) for path in target_paths)
        + (InstructionChain("unrelated/file.py", ("unrelated/REVIEW.md",)),),
        excludes=(),
        warnings=(),
    )
    return snapshot, instructions


def test_serializes_complete_review_files_and_active_repository_instructions() -> None:
    snapshot, instructions = _snapshot()

    agent_input = ContextBuilder().build(snapshot, instructions)

    assert json.loads(agent_input.canonical_bytes()) == {
        "repository_instructions": [
            {
                "applies_to": [
                    "src/added.py",
                    "src/changed.py",
                    "src/deleted.py",
                    "src/renamed.py",
                ],
                "content": "Follow the repository rules.\n",
                "path": "AGENTS.md",
            }
        ],
        "review_files": [
            {
                "change_type": "added",
                "new_ranges": [{"end_line": 3, "start_line": 1}],
                "old_ranges": [],
                "path": "src/added.py",
            },
            {
                "change_type": "modified",
                "new_ranges": [
                    {"end_line": 6, "start_line": 5},
                    {"end_line": 22, "start_line": 20},
                ],
                "old_ranges": [],
                "path": "src/changed.py",
            },
            {
                "change_type": "deleted",
                "new_ranges": [],
                "old_ranges": [{"end_line": 2, "start_line": 1}],
                "path": "src/deleted.py",
            },
            {
                "change_type": "renamed",
                "new_ranges": [{"end_line": 8, "start_line": 7}],
                "old_path": "src/original.py",
                "old_ranges": [],
                "path": "src/renamed.py",
            },
        ]
    }
    serialized = agent_input.canonical_bytes()
    for forbidden in (
        b"snapshot_id",
        b"output_locale",
        b"prompt_locale",
        b"hunk_id",
        b"content_hash",
        b"excerpt_hash",
        b"Inactive rule body",
        b"plan",
        b"changed_hunks",
        b"context",
    ):
        assert forbidden not in serialized


def test_serializes_each_repository_instruction_once_with_its_exact_targets() -> None:
    snapshot, instructions = _snapshot()
    file_rule = "Check added-file migrations.\n"
    rule_path = "src/added.py.review.md"
    snapshot = replace(
        snapshot,
        manifest=replace(
            snapshot.manifest,
            instruction_paths=("AGENTS.md", rule_path),
            entries=(
                *snapshot.manifest.entries,
                SnapshotEntry(
                    rule_path,
                    "file",
                    0o644,
                    len(file_rule.encode()),
                    _hash(file_rule.encode()),
                    None,
                    "instruction",
                ),
            ),
        ),
    )
    instructions = replace(
        instructions,
        documents=(
            instructions.documents[0],
            InstructionDocument(
                rule_path,
                file_rule,
                _hash(file_rule.encode()),
                "file_review",
                "src/added.py",
                4,
            ),
            instructions.documents[1],
        ),
        chains=tuple(
            replace(chain, rule_paths=(*chain.rule_paths, rule_path))
            if chain.target_path == "src/added.py"
            else chain
            for chain in instructions.chains
        ),
    )

    payload = json.loads(ContextBuilder().build(snapshot, instructions).canonical_bytes())

    assert payload["repository_instructions"] == [
        {
            "applies_to": [
                "src/added.py",
                "src/changed.py",
                "src/deleted.py",
                "src/renamed.py",
            ],
            "content": "Follow the repository rules.\n",
            "path": "AGENTS.md",
        },
        {
            "applies_to": ["src/added.py"],
            "content": file_rule,
            "path": rule_path,
        },
    ]


def test_same_snapshot_produces_identical_bytes_regardless_of_metadata_order() -> None:
    snapshot, instructions = _snapshot()
    reordered = replace(
        snapshot,
        change_index=replace(
            snapshot.change_index,
            hunks=tuple(reversed(snapshot.change_index.hunks)),
            files=tuple(reversed(snapshot.change_index.files)),
        ),
    )

    assert ContextBuilder().build(reordered, instructions).canonical_bytes() == (
        ContextBuilder().build(snapshot, instructions).canonical_bytes()
    )


def test_fails_instead_of_truncating_review_files_or_ranges() -> None:
    snapshot, instructions = _snapshot()

    with pytest.raises(ReviewScopeLimitError, match="file limit"):
        ContextBuilder().build(
            snapshot,
            instructions,
            ReviewScopeLimits(max_review_files=3),
        )
    with pytest.raises(ReviewScopeLimitError, match="range limit"):
        ContextBuilder().build(
            snapshot,
            instructions,
            ReviewScopeLimits(max_review_ranges=3),
        )


def test_rejects_active_instruction_metadata_that_is_not_exactly_frozen() -> None:
    snapshot, instructions = _snapshot()
    missing_rule_snapshot = replace(
        snapshot,
        manifest=replace(snapshot.manifest, instruction_paths=()),
    )

    with pytest.raises(ContextContainmentError, match="active repository instruction chains"):
        ContextBuilder().build(missing_rule_snapshot, instructions)

    stale = replace(
        instructions,
        documents=(replace(instructions.documents[0], content="Changed after freeze."),),
    )
    with pytest.raises(ContextIntegrityError, match="instruction content"):
        ContextBuilder().build(snapshot, stale)


def test_rejects_new_side_hunk_outside_snapshot_targets() -> None:
    snapshot, instructions = _snapshot()
    unsafe = replace(
        snapshot,
        change_index=replace(
            snapshot.change_index,
            hunks=(ChangedHunk("bad", "../outside.py", 1, 1, "new", "a" * 64),),
        ),
    )

    with pytest.raises(ContextContainmentError, match="changed hunk"):
        ContextBuilder().build(unsafe, instructions)
