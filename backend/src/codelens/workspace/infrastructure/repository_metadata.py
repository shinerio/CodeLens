import asyncio
from pathlib import Path

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.ports import RepositoryInfo
from codelens.workspace.infrastructure.git_cli import GitCli


def _resolve_git_path(raw_path: bytes) -> Path:
    return Path(raw_path.decode("utf-8", errors="strict").strip()).resolve()


class GitRepositoryMetadataAdapter:
    """Read repository metadata through the bounded Git CLI adapter."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def inspect(self, repository: Path) -> RepositoryInfo:
        """Validate an exact Git root and return its current immutable metadata."""

        top = await self._git.run(repository, "rev-parse", "--show-toplevel")
        top_path = await asyncio.to_thread(_resolve_git_path, top.stdout)
        if top_path != repository:
            raise InvalidRepositoryError("path must be a Git repository root")

        head = await self._git.run(repository, "rev-parse", "HEAD")
        branch = await self._git.run(
            repository,
            "symbolic-ref",
            "--short",
            "-q",
            "HEAD",
            ok_codes=(0, 1),
        )
        status = await self._git.run(repository, "status", "--porcelain=v1", "-z")
        return RepositoryInfo(
            path=repository,
            head_sha=head.stdout.decode("ascii", errors="strict").strip(),
            current_branch=branch.stdout.decode("utf-8", errors="strict").strip() or None,
            is_dirty=bool(status.stdout),
        )
