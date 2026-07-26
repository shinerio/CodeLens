from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.findings.infrastructure.model_output import FindingBatchSchema
from codelens.review.domain.ports import (
    AgentResponseDiagnostic,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    UnvalidatedAgentOutput,
)
from codelens.review.infrastructure.comment_collector import (
    ReviewCommentCollector,
    ReviewCommentSubmission,
    ReviewCompletionSubmission,
)
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.reviewer_catalog.domain.models import AgentVersion
from codelens.workspace.domain.models import ReviewSnapshot, TaskWorktree
from codelens.workspace.infrastructure.change_index import GitChangeIndexBuilder
from codelens.workspace.infrastructure.git_cli import GitCli

_PLACEHOLDER_HUNK_ID = "__HUNK_ID__"
_PLACEHOLDER_EXCERPT_HASH = "__EXCERPT_HASH__"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE_ROOT = (
    _REPO_ROOT / "backend" / "tests" / "evals" / "fixtures" / "correctness" / "simple_branch"
)
_DELETED_PATHS = ("src/permissions.py",)


@dataclass(frozen=True)
class CorrectnessFixture:
    repository: Path
    base_oid: str
    changed_oid: str
    batch: FindingBatchSchema


async def _run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return await asyncio.to_thread(
        subprocess.run,
        ["git", "-c", "commit.gpgsign=false", *arguments],
        check=True,
        capture_output=True,
        timeout=30.0,
    )


async def _copy_tree(source: Path, destination: Path) -> None:
    await asyncio.to_thread(shutil.copytree, source, destination, dirs_exist_ok=True)


async def _copy_file(source: Path, destination: Path) -> None:
    await asyncio.to_thread(shutil.copy2, source, destination)


def _replace_placeholders(value: object, *, hunk_id: str, excerpt_hash: str) -> object:
    if isinstance(value, str):
        if value == _PLACEHOLDER_HUNK_ID:
            return hunk_id
        if value == _PLACEHOLDER_EXCERPT_HASH:
            return excerpt_hash
        return value
    if isinstance(value, list):
        return [
            _replace_placeholders(item, hunk_id=hunk_id, excerpt_hash=excerpt_hash)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_placeholders(item, hunk_id=hunk_id, excerpt_hash=excerpt_hash)
            for key, item in value.items()
        }
    return value


async def prepare_simple_branch_repository(workspace: Path) -> CorrectnessFixture:
    repository = workspace / "simple-branch"
    if repository.exists():
        await asyncio.to_thread(shutil.rmtree, repository)
    repository.mkdir(parents=True)
    await _run_git("init", "-b", "main", str(repository))
    await _run_git("-C", str(repository), "config", "user.email", "test@example.com")
    await _run_git("-C", str(repository), "config", "user.name", "Test User")
    await _copy_tree(_FIXTURE_ROOT / "initial", repository)
    await _copy_file(_FIXTURE_ROOT / "REVIEW.md", repository / "REVIEW.md")
    await _run_git("-C", str(repository), "add", "src", "REVIEW.md")
    await _run_git("-C", str(repository), "commit", "-m", "initial")
    base_oid = (
        await _run_git("-C", str(repository), "rev-parse", "HEAD")
    ).stdout.decode("utf-8").strip()
    await _copy_tree(_FIXTURE_ROOT / "changed", repository)
    for relative_path in _DELETED_PATHS:
        (repository / relative_path).unlink()
    await _run_git("-C", str(repository), "switch", "-c", "fixture-change")
    await _run_git("-C", str(repository), "add", "-A")
    await _run_git("-C", str(repository), "commit", "-m", "introduce review defects")
    changed_oid = (await _run_git("-C", str(repository), "rev-parse", "HEAD")).stdout.decode(
        "utf-8"
    ).strip()

    batch = await load_simple_branch_batch(repository, base_oid=base_oid)
    return CorrectnessFixture(
        repository=repository,
        base_oid=base_oid,
        changed_oid=changed_oid,
        batch=batch,
    )


async def load_simple_branch_batch(repository: Path, *, base_oid: str) -> FindingBatchSchema:
    git = GitCli()
    change_index = await GitChangeIndexBuilder(git).build(
        TaskWorktree(
            worktree_id="fixture-worktree",
            task_id="fixture-task",
            repository_common_dir_hash="d" * 64,
            root=repository,
            head_oid=base_oid,
            ownership_token_hash="e" * 64,
        ),
        base_oid,
        ("src/state.py",),
        "branch",
    )
    matching_hunks = [
        hunk
        for hunk in change_index.hunks
        if hunk.path == "src/state.py" and hunk.start_line == 7 and hunk.side == "new"
    ]
    if len(matching_hunks) != 1:
        raise AssertionError(
            f"expected one matching hunk for src/state.py:7, got {len(matching_hunks)}"
        )
    hunk = matching_hunks[0]

    payload = json.loads((_FIXTURE_ROOT / "golden.json").read_text(encoding="utf-8"))
    payload = _replace_placeholders(
        payload,
        hunk_id=hunk.hunk_id,
        excerpt_hash=hunk.excerpt_hash,
    )
    return FindingBatchSchema.model_validate(payload)


def load_simple_branch_comments() -> tuple[ReviewCommentSubmission, ...]:
    """Load deterministic model comments while leaving locations to production resolution."""

    payload = json.loads((_FIXTURE_ROOT / "comments.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("correctness fixture comments must be a JSON array")
    return tuple(ReviewCommentSubmission.model_validate(item) for item in payload)


class FixtureRuntime:
    """Run deterministic model intent through the production comment collection boundary."""

    def __init__(
        self,
        comments: tuple[ReviewCommentSubmission, ...],
        *,
        model_name: str = "fixture-model",
        delay_seconds: float = 0.15,
        repeat_first_comment: bool = False,
    ) -> None:
        self._comments = comments + comments[:1] if repeat_first_comment else comments
        self._codec = AgentOutputCodec("1")
        self.calls = 0
        self.model_name = model_name
        self.delay_seconds = delay_seconds

    async def invoke(
        self,
        agent: AgentVersion,
        _payload: bytes,
        snapshot: ReviewSnapshot,
        _prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        return await self._collect(agent, snapshot)

    async def _collect(
        self,
        agent: AgentVersion,
        snapshot: ReviewSnapshot,
    ) -> UnvalidatedAgentOutput:
        self.calls += 1
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        collector = ReviewCommentCollector(
            snapshot=snapshot,
            reviewer_id=agent.agent_id,
            confidence_floor=agent.confidence_floor,
            tools=FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=None),
        )
        await collector.submit_many(list(self._comments))
        collector.complete(
            ReviewCompletionSubmission(
                summary="Deterministic fixture reviewed all three changed files.",
                reviewed_changed_files=3,
            )
        )
        return UnvalidatedAgentOutput(
            canonical_bytes=self._codec.encode(collector.finding_batch()),
            response_ids=("fixture-response-1", "fixture-response-2"),
            model_name=self.model_name,
            input_tokens=640,
            output_tokens=180,
            diagnostics=(
                AgentResponseDiagnostic("fixture-response-1", "fixture-request-1", 400, 60, 1),
                AgentResponseDiagnostic("fixture-response-2", "fixture-request-2", 240, 120, 2),
            ),
        )

    async def invoke_stream(
        self,
        agent: AgentVersion,
        payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
        sink: AgentRuntimeEventSink,
    ) -> UnvalidatedAgentOutput:
        """Emit stable LLM and tool events around production comment resolution."""

        await sink(AgentRuntimeEvent("model_started", "", {}))
        comment_call_id = "fixture-comment-call"
        await sink(
            AgentRuntimeEvent(
                "tool_call",
                json.dumps(
                    {"comments": [item.model_dump(mode="json") for item in self._comments]},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                {"tool_call_id": comment_call_id, "tool_name": "comment"},
            )
        )
        output = await self._collect(agent, snapshot)
        await sink(
            AgentRuntimeEvent(
                "tool_result",
                json.dumps({"accepted": True, "accepted_count": len(self._comments)}),
                {"tool_call_id": comment_call_id},
            )
        )
        completion_call_id = "fixture-task-done-call"
        await sink(
            AgentRuntimeEvent(
                "tool_call",
                json.dumps({"reviewed_changed_files": 3}),
                {"tool_call_id": completion_call_id, "tool_name": "task_done"},
            )
        )
        await sink(
            AgentRuntimeEvent(
                "tool_result",
                json.dumps({"accepted": True, "reviewed_changed_files": 3}),
                {"tool_call_id": completion_call_id},
            )
        )
        await sink(AgentRuntimeEvent("model_completed", "", {}))
        return output
