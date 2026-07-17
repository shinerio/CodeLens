import subprocess
from pathlib import Path

import pytest

_GIT_TIMEOUT_SECONDS = 10.0
_GIT_OUTPUT_LIMIT_BYTES = 64 * 1024


def _run_git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *arguments],
        check=False,
        capture_output=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if len(result.stdout) > _GIT_OUTPUT_LIMIT_BYTES or len(result.stderr) > _GIT_OUTPUT_LIMIT_BYTES:
        raise RuntimeError("Git fixture output exceeded its safety limit")
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _run_git("init", "-b", "main", str(repository))
    _run_git("-C", str(repository), "config", "user.email", "test@example.com")
    _run_git("-C", str(repository), "config", "user.name", "Test User")
    (repository / "README.md").write_text("# fixture\n", encoding="utf-8")
    _run_git("-C", str(repository), "add", "README.md")
    _run_git("-C", str(repository), "commit", "-m", "initial")
    return repository
