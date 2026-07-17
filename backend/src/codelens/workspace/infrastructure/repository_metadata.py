import asyncio
import hashlib
from pathlib import Path

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.ports import RepositoryInfo
from codelens.workspace.infrastructure.git_cli import GitCli


def _resolve_git_path(repository: Path, raw_path: bytes) -> Path:
    candidate = Path(raw_path.decode("utf-8", errors="strict").strip())
    return (candidate if candidate.is_absolute() else repository / candidate).resolve()


class GitRepositoryMetadataAdapter:
    """Read repository metadata through the bounded Git CLI adapter."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def inspect(self, repository: Path) -> RepositoryInfo:
        """Validate an exact Git root and return its current immutable metadata."""

        top = await self._git.run(repository, "rev-parse", "--show-toplevel")
        top_path = await asyncio.to_thread(_resolve_git_path, repository, top.stdout)
        if top_path != repository:
            raise InvalidRepositoryError("path must be a Git repository root")

        common_dir_result = await self._git.run(repository, "rev-parse", "--git-common-dir")
        common_dir = await asyncio.to_thread(
            _resolve_git_path,
            repository,
            common_dir_result.stdout,
        )
        realpath_hash = hashlib.sha256(str(repository).encode()).hexdigest()
        common_dir_hash = hashlib.sha256(str(common_dir).encode()).hexdigest()
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
            repository_id=f"repository_{common_dir_hash}",
            repository_realpath_hash=realpath_hash,
            git_common_dir_hash=common_dir_hash,
            head_sha=head.stdout.decode("ascii", errors="strict").strip(),
            current_branch=branch.stdout.decode("utf-8", errors="strict").strip() or None,
            is_dirty=bool(status.stdout),
        )
