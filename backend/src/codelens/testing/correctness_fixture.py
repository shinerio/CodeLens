"""Deterministic v2 Review fixture used by local end-to-end verification."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codelens.capabilities.domain.models import FrozenAgentExecutionSpec
from codelens.findings.application.validate_candidates import CandidateBatchCodec
from codelens.findings.infrastructure.comment_output import CommentFindingSchema
from codelens.review.domain.ports import (
    AgentResponseDiagnostic,
    AgentRuntimeEvent,
    AgentRuntimeEventSink,
    UnvalidatedAgentOutput,
)
from codelens.review.infrastructure.comment_collector import ReviewCommentCollector
from codelens.review.infrastructure.snapshot_tools import FilesystemReviewTools
from codelens.reviewer_catalog.domain.models import AgentRole
from codelens.workspace.domain.models import ReviewSnapshot
from codelens.workspace.infrastructure.git_cli import GitCli

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE_ROOT = (
    _REPO_ROOT / "backend" / "tests" / "evals" / "fixtures" / "correctness" / "simple_branch"
)
_DELETED_PATHS = ("src/permissions.py",)


@dataclass(frozen=True)
class CorrectnessFixture:
    """Identify one deterministic branch comparison repository."""

    repository: Path
    base_oid: str
    changed_oid: str


async def _run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return await asyncio.to_thread(
        subprocess.run,
        ["git", "-c", "commit.gpgsign=false", *arguments],
        check=True,
        capture_output=True,
        timeout=30.0,
    )


async def prepare_simple_branch_repository(workspace: Path) -> CorrectnessFixture:
    """Create a real Git repository containing added, deleted, and modified defects."""

    repository = workspace / "simple-branch"
    if repository.exists():
        await asyncio.to_thread(shutil.rmtree, repository)
    repository.mkdir(parents=True)
    await _run_git("init", "-b", "main", str(repository))
    await _run_git("-C", str(repository), "config", "user.email", "test@example.com")
    await _run_git("-C", str(repository), "config", "user.name", "Test User")
    await asyncio.to_thread(
        shutil.copytree,
        _FIXTURE_ROOT / "initial",
        repository,
        dirs_exist_ok=True,
    )
    await asyncio.to_thread(shutil.copy2, _FIXTURE_ROOT / "REVIEW.md", repository / "REVIEW.md")
    await _run_git("-C", str(repository), "add", "src", "REVIEW.md")
    await _run_git("-C", str(repository), "commit", "-m", "initial")
    base_oid = (await _run_git("-C", str(repository), "rev-parse", "HEAD")).stdout.decode().strip()
    await asyncio.to_thread(
        shutil.copytree,
        _FIXTURE_ROOT / "changed",
        repository,
        dirs_exist_ok=True,
    )
    for relative_path in _DELETED_PATHS:
        (repository / relative_path).unlink()
    await _run_git("-C", str(repository), "switch", "-c", "fixture-change")
    await _run_git("-C", str(repository), "add", "-A")
    await _run_git("-C", str(repository), "commit", "-m", "introduce review defects")
    changed_oid = (
        (await _run_git("-C", str(repository), "rev-parse", "HEAD")).stdout.decode().strip()
    )
    return CorrectnessFixture(repository, base_oid, changed_oid)


def load_simple_branch_comments() -> tuple[CommentFindingSchema, ...]:
    """Load strict Comment v2 submissions for the deterministic fixture."""

    payload = json.loads((_FIXTURE_ROOT / "comments.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("correctness fixture comments must be a JSON array")
    return tuple(CommentFindingSchema.model_validate(item) for item in payload)


class FixtureRuntime:
    """Resolve deterministic reviewer comments and accept every resulting cluster."""

    def __init__(
        self,
        comments: tuple[CommentFindingSchema, ...],
        *,
        model_name: str = "fixture-model",
        delay_seconds: float = 0.15,
    ) -> None:
        self._comments = comments
        self.calls = 0
        self.model_name = model_name
        self.delay_seconds = delay_seconds

    async def invoke(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        payload: bytes,
        snapshot: ReviewSnapshot,
        _prompt_locale: str,
    ) -> UnvalidatedAgentOutput:
        """Return one canonical v2 Reviewer or Final Verifier Artifact."""

        self.calls += 1
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        envelope = json.loads(payload)
        role_context = envelope.get("role_context", {})
        if execution_spec.agent.role is AgentRole.VERIFIER:
            clusters = role_context.get("verdict_context", {}).get("clusters", [])
            canonical = json.dumps(
                {
                    "schema_version": "2",
                    "decisions": [
                        {"cluster_ids": [cluster["cluster_id"]], "outcome": "accept"}
                        for cluster in clusters
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        else:
            run_id = role_context.get("_host_run_id")
            if not isinstance(run_id, str):
                raise ValueError("fixture Reviewer input lacks a stable run ID")
            tools = FilesystemReviewTools(snapshot, GitCli(), max_tool_calls=None)
            collector = ReviewCommentCollector(
                task_id=snapshot.worktree.task_id,
                run_id=run_id,
                snapshot=snapshot,
                reviewer_reference=execution_spec.agent.reference,
                reviewer_dimensions=execution_spec.agent.dimensions,
                tools=tools,
            )
            await collector.submit_many(list(self._comments))
            for path in tools.review_file_paths:
                await tools.get_diff(path)
            collector.complete("Deterministic fixture reviewed the complete frozen scope.")
            canonical = CandidateBatchCodec().encode(collector.candidate_batch())
        return UnvalidatedAgentOutput(
            canonical,
            ("fixture-response",),
            self.model_name,
            640,
            180,
            (AgentResponseDiagnostic("fixture-response", "fixture-request", 640, 180, 1),),
        )

    async def invoke_stream(
        self,
        execution_spec: FrozenAgentExecutionSpec,
        payload: bytes,
        snapshot: ReviewSnapshot,
        prompt_locale: str,
        sink: AgentRuntimeEventSink,
    ) -> UnvalidatedAgentOutput:
        """Emit bounded observable events around the deterministic v2 Artifact."""

        await sink(AgentRuntimeEvent("model_started", "", {}))
        tool_name = "verdict" if execution_spec.agent.role is AgentRole.VERIFIER else "comment"
        call_id = f"fixture-{tool_name}-call"
        call_content = (
            json.dumps({"fixture": True})
            if execution_spec.agent.role is AgentRole.VERIFIER
            else json.dumps(
                {"comments": [comment.model_dump(mode="json") for comment in self._comments]}
            )
        )
        await sink(
            AgentRuntimeEvent(
                "tool_call",
                call_content,
                {"tool_call_id": call_id, "tool_name": tool_name},
            )
        )
        output = await self.invoke(execution_spec, payload, snapshot, prompt_locale)
        await sink(
            AgentRuntimeEvent(
                "tool_result",
                json.dumps({"accepted": True}),
                {"tool_call_id": call_id},
            )
        )
        await sink(AgentRuntimeEvent("model_completed", "", {}))
        return output
