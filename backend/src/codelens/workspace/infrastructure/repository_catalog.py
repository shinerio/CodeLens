from pathlib import Path

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.ports import (
    RepositoryBranch,
    RepositoryCatalog,
    RepositoryCommit,
)
from codelens.workspace.infrastructure.git_cli import GitCli


class GitRepositoryCatalogAdapter:
    """List branch and commit choices through bounded read-only Git commands."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def list_catalog(
        self,
        repository: Path,
        *,
        target_ref: str | None,
        commit_offset: int,
        commit_limit: int,
    ) -> RepositoryCatalog:
        """Return every branch and one selected-branch history page without its tip."""

        branch_result = await self._git.run(
            repository,
            "for-each-ref",
            "--format=%(refname)%00%(objectname)%00%(HEAD)",
            "refs/heads",
            "refs/remotes",
        )
        branches = self._parse_branches(branch_result.stdout)
        target_branch = self._select_target_branch(branches, target_ref)
        if target_branch is None:
            return RepositoryCatalog(
                branches=branches,
                commits=(),
                next_commit_offset=None,
            )
        commit_result = await self._git.run(
            repository,
            "log",
            f"--skip={commit_offset + 1}",
            f"--max-count={commit_limit + 1}",
            "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e",
            target_branch.oid,
        )
        commits = self._parse_commits(commit_result.stdout)
        has_more = len(commits) > commit_limit
        return RepositoryCatalog(
            branches=branches,
            commits=commits[:commit_limit],
            next_commit_offset=commit_offset + commit_limit if has_more else None,
        )

    @staticmethod
    def _select_target_branch(
        branches: tuple[RepositoryBranch, ...],
        target_ref: str | None,
    ) -> RepositoryBranch | None:
        if target_ref is not None:
            for branch in branches:
                if branch.name == target_ref:
                    return branch
            raise InvalidRepositoryError("selected target is not a repository branch")
        return next(
            (branch for branch in branches if branch.is_current),
            branches[0] if branches else None,
        )

    @staticmethod
    def _parse_branches(payload: bytes) -> tuple[RepositoryBranch, ...]:
        branches: list[RepositoryBranch] = []
        try:
            lines = payload.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError:
            raise InvalidRepositoryError("branch metadata is not valid UTF-8") from None
        for line in lines:
            fields = line.split("\x00")
            if len(fields) != 3:
                raise InvalidRepositoryError("branch metadata is malformed")
            full_name, oid, current_marker = fields
            if full_name.startswith("refs/heads/"):
                name = full_name.removeprefix("refs/heads/")
                is_remote = False
            elif full_name.startswith("refs/remotes/"):
                name = full_name.removeprefix("refs/remotes/")
                is_remote = True
            else:
                continue
            if is_remote and name.endswith("/HEAD"):
                continue
            branches.append(
                RepositoryBranch(
                    name=name,
                    oid=oid,
                    is_current=current_marker.strip() == "*",
                    is_remote=is_remote,
                )
            )
        return tuple(
            sorted(
                branches,
                key=lambda branch: (
                    not branch.is_current,
                    branch.is_remote,
                    branch.name.casefold(),
                ),
            )
        )

    @staticmethod
    def _parse_commits(payload: bytes) -> tuple[RepositoryCommit, ...]:
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise InvalidRepositoryError("commit metadata is not valid UTF-8") from None
        commits: list[RepositoryCommit] = []
        for record in text.split("\x1e"):
            normalized = record.strip("\r\n")
            if not normalized:
                continue
            fields = normalized.split("\x1f")
            if len(fields) != 5:
                raise InvalidRepositoryError("commit metadata is malformed")
            oid, short_oid, author, committed_at, message = fields
            commits.append(
                RepositoryCommit(
                    oid=oid,
                    short_oid=short_oid,
                    author=author,
                    message=message,
                    committed_at=committed_at,
                )
            )
        return tuple(commits)
