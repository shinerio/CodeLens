from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codelens.findings.infrastructure.agent_output_codec import AgentOutputCodec
from codelens.findings.infrastructure.model_output import FindingBatchSchema
from codelens.review.domain.ports import UnvalidatedAgentOutput
from codelens.workspace.domain.models import TaskWorktree
from codelens.workspace.infrastructure.change_index import GitChangeIndexBuilder
from codelens.workspace.infrastructure.git_cli import GitCli

_PLACEHOLDER_HUNK_ID = "__HUNK_ID__"
_PLACEHOLDER_EXCERPT_HASH = "__EXCERPT_HASH__"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE_ROOT = (
    _REPO_ROOT / "backend" / "tests" / "evals" / "fixtures" / "correctness" / "simple_branch"
)


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
    await _run_git("-C", str(repository), "add", "src/state.py", "REVIEW.md")
    await _run_git("-C", str(repository), "commit", "-m", "initial")
    base_oid = (
        await _run_git("-C", str(repository), "rev-parse", "HEAD")
    ).stdout.decode("utf-8").strip()
    await _copy_tree(_FIXTURE_ROOT / "changed", repository)
    changed_oid = (
        await _run_git("-C", str(repository), "rev-parse", "HEAD")
    ).stdout.decode("utf-8").strip()

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
    )
    matching_hunks = [
        hunk
        for hunk in change_index.hunks
        if hunk.path == "src/state.py" and hunk.start_line == 7
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


class FixtureRuntime:
    def __init__(
        self,
        batch: FindingBatchSchema,
        *,
        model_name: str = "fixture-model",
        delay_seconds: float = 0.15,
    ) -> None:
        self._batch = batch
        self._codec = AgentOutputCodec("1")
        self.calls = 0
        self.model_name = model_name
        self.delay_seconds = delay_seconds

    async def invoke(self, _agent: object, _payload: bytes) -> UnvalidatedAgentOutput:
        self.calls += 1
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        return UnvalidatedAgentOutput(
            canonical_bytes=self._codec.encode(self._batch),
            response_ids=("fixture-response",),
            model_name=self.model_name,
            input_tokens=0,
            output_tokens=0,
            diagnostics=(),
        )
