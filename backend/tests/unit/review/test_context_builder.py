import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from codelens.instruction_policy.domain.models import (
    InstructionDocument,
    ResolvedInstructionSet,
)
from codelens.review.application.context_builder import (
    CandidateSummary,
    ContextBudget,
    ContextBudgetError,
    ContextBuilder,
    ContextContainmentError,
    ContextIntegrityError,
    SnapshotRead,
)
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

_INSTRUCTION_CONTENT = "Review changed behavior and cite evidence."


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _snapshot(
    context_paths: tuple[str, ...],
    context_hashes: dict[str, str] | None = None,
) -> ReviewSnapshot:
    changed = b"return ready\n"
    hashes = context_hashes or {}
    return ReviewSnapshot(
        snapshot_id="snapshot-1",
        worktree=TaskWorktree(
            worktree_id="worktree-1",
            task_id="review-1",
            repository_common_dir_hash="a" * 64,
            root=Path("/private/source/repository"),
            head_oid="b" * 40,
            ownership_token_hash="c" * 64,
        ),
        target=ReviewTarget("d" * 40, "b" * 40, None),
        fingerprint=RepositoryFingerprint("b" * 40, "e" * 64, "f" * 64),
        manifest=SnapshotManifest(
            target_paths=("src/changed.py",),
            context_paths=context_paths,
            instruction_paths=("AGENTS.md",),
            excluded_paths=(),
            entries=(
                SnapshotEntry(
                    "src/changed.py",
                    "file",
                    0o100644,
                    len(changed),
                    _hash(changed),
                    None,
                    "target",
                ),
                SnapshotEntry(
                    "AGENTS.md",
                    "file",
                    0o100644,
                    len(_INSTRUCTION_CONTENT.encode()),
                    _hash(_INSTRUCTION_CONTENT.encode()),
                    None,
                    "instruction",
                ),
                *(
                    SnapshotEntry(
                        path,
                        "deleted" if path == "src/deleted.py" else "file",
                        0o100644,
                        0,
                        hashes[path],
                        None,
                        "context",
                    )
                    for path in context_paths
                ),
            ),
        ),
        change_index=ChangeIndex(
            (
                ChangedHunk(
                    "hunk-1",
                    "src/changed.py",
                    4,
                    4,
                    "new",
                    _hash(changed),
                ),
            )
        ),
    )


class FakeContextProvider:
    def __init__(self, candidates: tuple[CandidateSummary, ...]) -> None:
        self._candidates = candidates

    async def summarize(self, _snapshot: ReviewSnapshot) -> tuple[CandidateSummary, ...]:
        return self._candidates


class RecordingReader:
    def __init__(self, bodies: dict[tuple[str, str], bytes]) -> None:
        self._bodies = bodies
        self.paths_read: list[str] = []

    async def read(
        self,
        _snapshot: ReviewSnapshot,
        path: str,
        start_line: int,
        end_line: int,
        side: str,
        max_bytes: int,
    ) -> SnapshotRead:
        del start_line, end_line
        self.paths_read.append(path)
        payload = self._bodies[(path, side)]
        return SnapshotRead(
            content=payload[:max_bytes],
            content_hash=_hash(payload),
            truncated=len(payload) > max_bytes,
        )


def _instructions() -> ResolvedInstructionSet:
    return ResolvedInstructionSet(
        documents=(
            InstructionDocument(
                "AGENTS.md",
                _INSTRUCTION_CONTENT,
                _hash(_INSTRUCTION_CONTENT.encode()),
            ),
        ),
        excludes=(),
        warnings=(),
    )


async def test_plans_ten_candidates_then_reads_only_the_two_that_fit() -> None:
    context_paths = tuple(f"src/context_{index}.py" for index in range(10))
    bodies = {
        ("src/changed.py", "new"): b"return ready\n",
        **{
            (path, "new"): f"value_{index} = {index}\n".encode()
            for index, path in enumerate(context_paths)
        },
    }
    candidates = tuple(
        CandidateSummary(
            path=path,
            start_line=1,
            end_line=1,
            side="new",
            estimated_tokens=50,
            priority=100 - index,
            reason="direct caller",
            trust_label="repository_context",
            content_hash=_hash(bodies[(path, "new")]),
        )
        for index, path in enumerate(context_paths)
    )
    reader = RecordingReader(bodies)
    builder = ContextBuilder(FakeContextProvider(candidates), reader)

    agent_input = await builder.build(
        _snapshot(
            context_paths,
            {path: _hash(bodies[(path, "new")]) for path in context_paths},
        ),
        _instructions(),
        ContextBudget(
            total_tokens=240,
            platform_policy_tokens=30,
            instruction_tokens=20,
            output_schema_tokens=30,
            changed_hunk_tokens=20,
            max_excerpt_bytes=256,
            max_line_chars=120,
        ),
    )

    assert reader.paths_read == [
        "src/changed.py",
        "src/context_0.py",
        "src/context_1.py",
    ]
    assert [excerpt.path for excerpt in agent_input.instructions] == ["AGENTS.md"]
    assert [excerpt.path for excerpt in agent_input.changed_hunks] == ["src/changed.py"]
    assert [excerpt.path for excerpt in agent_input.context] == [
        "src/context_0.py",
        "src/context_1.py",
    ]
    decisions = {decision.path: decision for decision in agent_input.plan.decisions}
    assert decisions["src/context_0.py"].status == "included"
    assert decisions["src/context_1.py"].status == "included"
    assert decisions["src/context_2.py"].status == "omitted"
    assert decisions["src/context_2.py"].reason == "token_budget"
    assert decisions["src/context_2.py"].estimated_tokens == 50
    assert agent_input.plan.considered_paths == context_paths
    assert agent_input.plan.included_paths == context_paths[:2]
    assert agent_input.plan.omitted_paths == context_paths[2:]
    assert agent_input.plan.used_tokens <= agent_input.plan.total_tokens
    assert b"/private/source/repository" not in agent_input.canonical_bytes()


async def test_binary_deleted_oversized_unicode_and_long_lines_are_bounded() -> None:
    context_paths = (
        "src/binary.bin",
        "src/deleted.py",
        "src/huge.py",
        "src/unicode.py",
        "src/long.py",
    )
    unicode_body = "你好🙂\n".encode()
    long_body = ("x" * 500 + "\n").encode()
    bodies = {
        ("src/changed.py", "new"): b"return ready\n",
        ("src/binary.bin", "new"): b"code\x00binary",
        ("src/unicode.py", "new"): unicode_body,
        ("src/long.py", "new"): long_body,
    }
    candidates = (
        CandidateSummary(
            "src/deleted.py", 1, 2, "new", 10, 100, "deleted", "repository_context", "a" * 64, True
        ),
        CandidateSummary(
            "src/huge.py", 1, 10000, "new", 500, 90, "large", "repository_context", "b" * 64
        ),
        CandidateSummary(
            "src/binary.bin",
            1,
            1,
            "new",
            10,
            80,
            "binary neighbor",
            "repository_context",
            _hash(bodies[("src/binary.bin", "new")]),
        ),
        CandidateSummary(
            "src/unicode.py",
            1,
            1,
            "new",
            10,
            70,
            "unicode neighbor",
            "repository_context",
            _hash(unicode_body),
        ),
        CandidateSummary(
            "src/long.py",
            1,
            1,
            "new",
            10,
            60,
            "long neighbor",
            "repository_context",
            _hash(long_body),
        ),
    )
    reader = RecordingReader(bodies)
    builder = ContextBuilder(FakeContextProvider(candidates), reader)

    agent_input = await builder.build(
        _snapshot(
            context_paths,
            {candidate.path: candidate.content_hash for candidate in candidates},
        ),
        _instructions(),
        ContextBudget(
            total_tokens=210,
            platform_policy_tokens=30,
            instruction_tokens=20,
            output_schema_tokens=30,
            changed_hunk_tokens=20,
            max_excerpt_bytes=128,
            max_line_chars=32,
        ),
    )

    assert "src/deleted.py" not in reader.paths_read
    assert "src/huge.py" not in reader.paths_read
    assert [excerpt.path for excerpt in agent_input.context] == [
        "src/unicode.py",
        "src/long.py",
    ]
    assert agent_input.context[0].content == "你好🙂\n"
    assert len(agent_input.context[1].content.splitlines()[0]) == 32
    decisions = {decision.path: decision for decision in agent_input.plan.decisions}
    assert decisions["src/deleted.py"].reason == "deleted"
    assert decisions["src/huge.py"].reason == "token_budget"
    assert decisions["src/binary.bin"].reason == "binary"
    assert decisions["src/long.py"].status == "truncated"
    assert agent_input.plan.truncated_paths == ("src/long.py",)


async def test_rejects_provider_paths_outside_the_snapshot_before_reading() -> None:
    candidate = CandidateSummary(
        "../private.env",
        1,
        1,
        "new",
        10,
        1,
        "untrusted",
        "repository_context",
        "a" * 64,
    )
    reader = RecordingReader({("src/changed.py", "new"): b"return ready\n"})
    builder = ContextBuilder(FakeContextProvider((candidate,)), reader)

    with pytest.raises(ContextContainmentError):
        await builder.build(
            _snapshot(()),
            _instructions(),
            ContextBudget(240, 30, 20, 30, 20, 128, 80),
        )

    assert reader.paths_read == []


async def test_rejects_instructions_that_exceed_their_reservation_before_reading() -> None:
    oversized_content = "mandatory-rule " * 200
    instructions = ResolvedInstructionSet(
        documents=(
            InstructionDocument(
                "AGENTS.md",
                oversized_content,
                _hash(oversized_content.encode()),
            ),
        ),
        excludes=(),
        warnings=(),
    )
    reader = RecordingReader({("src/changed.py", "new"): b"return ready\n"})
    builder = ContextBuilder(FakeContextProvider(()), reader)

    with pytest.raises(ContextBudgetError, match="instructions"):
        await builder.build(
            _snapshot(()),
            instructions,
            ContextBudget(240, 30, 20, 30, 20, 128, 80),
        )

    assert reader.paths_read == []


async def test_rejects_uncontained_changed_hunk_before_reading() -> None:
    malicious_snapshot = replace(
        _snapshot(()),
        change_index=ChangeIndex(
            (
                ChangedHunk(
                    "hunk-malicious",
                    "../outside.py",
                    1,
                    1,
                    "new",
                    "a" * 64,
                ),
            )
        ),
    )
    reader = RecordingReader({("src/changed.py", "new"): b"return ready\n"})
    builder = ContextBuilder(FakeContextProvider(()), reader)

    with pytest.raises(ContextContainmentError, match="hunk"):
        await builder.build(
            malicious_snapshot,
            _instructions(),
            ContextBudget(240, 30, 20, 30, 20, 128, 80),
        )

    assert reader.paths_read == []


async def test_rejects_stale_instruction_content_before_reading() -> None:
    stale = ResolvedInstructionSet(
        documents=(
            InstructionDocument(
                "AGENTS.md",
                "Changed after the Snapshot was frozen.",
                _hash(_INSTRUCTION_CONTENT.encode()),
            ),
        ),
        excludes=(),
        warnings=(),
    )
    reader = RecordingReader({("src/changed.py", "new"): b"return ready\n"})
    builder = ContextBuilder(FakeContextProvider(()), reader)

    with pytest.raises(ContextIntegrityError, match="instruction"):
        await builder.build(
            _snapshot(()),
            stale,
            ContextBudget(240, 30, 20, 30, 20, 128, 80),
        )

    assert reader.paths_read == []
