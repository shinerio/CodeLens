import asyncio
import hashlib
import re
from pathlib import Path
from typing import Literal

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.models import ChangedHunk, ChangeIndex, TaskWorktree
from codelens.workspace.infrastructure.git_cli import GitCli

_HUNK_HEADER = re.compile(
    r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


def _read_lines(path: Path) -> tuple[str, ...]:
    try:
        return tuple(path.read_text(encoding="utf-8").splitlines(keepends=True))
    except (FileNotFoundError, IsADirectoryError):
        return ()


class GitChangeIndexBuilder:
    """Build deterministic changed-hunk identities from a pinned base and worktree."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def build(self, worktree: TaskWorktree, base_oid: str) -> ChangeIndex:
        """Index new- or old-side hunk locations with excerpt hashes."""

        result = await self._git.run(
            worktree.root,
            "diff",
            "--unified=0",
            "--no-ext-diff",
            "--no-textconv",
            base_oid,
            "--",
        )
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        path: str | None = None
        hunks: list[ChangedHunk] = []
        for line in lines:
            if line.startswith("+++ "):
                raw_path = line[4:]
                path = raw_path[2:] if raw_path.startswith("b/") else raw_path
                if path == "/dev/null":
                    path = None
            elif line.startswith("@@ "):
                if path is None:
                    continue
                match = _HUNK_HEADER.match(line)
                if match is None:
                    raise InvalidRepositoryError("unexpected unified diff hunk header")
                old_start, old_count_raw, new_start, new_count_raw = match.groups()
                old_count = int(old_count_raw or "1")
                new_count = int(new_count_raw or "1")
                if new_count > 0:
                    side: Literal["old", "new"] = "new"
                    start_line = int(new_start)
                    end_line = start_line + new_count - 1
                    file_lines = await asyncio.to_thread(_read_lines, worktree.root / path)
                    excerpt = "".join(file_lines[start_line - 1 : end_line]).encode("utf-8")
                else:
                    side = "old"
                    start_line = int(old_start)
                    end_line = start_line + max(old_count, 1) - 1
                    excerpt = line.encode("utf-8")
                excerpt_hash = hashlib.sha256(excerpt).hexdigest()
                identity = f"{path}\0{side}\0{start_line}\0{end_line}\0{excerpt_hash}"
                hunks.append(
                    ChangedHunk(
                        hunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                        path=path,
                        start_line=start_line,
                        end_line=end_line,
                        side=side,
                        excerpt_hash=excerpt_hash,
                    )
                )
        return ChangeIndex(tuple(hunks))
