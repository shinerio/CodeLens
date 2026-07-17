from pathlib import Path

from codelens.workspace.domain.models import ExcludedPath, IgnoreResolution
from codelens.workspace.infrastructure.git_cli import GitCli


class GitIgnoreResolver:
    """Apply Git's current ignore rules to tracked and untracked candidates."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def resolve(self, repository: Path, paths: tuple[str, ...]) -> IgnoreResolution:
        """Return deterministic include/exclude partitions with rule provenance."""

        normalized = tuple(sorted(dict.fromkeys(path.replace("\\", "/") for path in paths)))
        if any("\0" in path for path in normalized):
            raise ValueError("review paths cannot contain NUL bytes")
        if not normalized:
            return IgnoreResolution((), ())

        stdin = b"\0".join(path.encode("utf-8") for path in normalized) + b"\0"
        result = await self._git.run(
            repository,
            "check-ignore",
            "--no-index",
            "-v",
            "-z",
            "--stdin",
            stdin=stdin,
            ok_codes=(0, 1),
        )
        fields = result.stdout.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) % 4 != 0:
            raise ValueError("unexpected git check-ignore -z output")

        matches: dict[str, ExcludedPath] = {}
        for offset in range(0, len(fields), 4):
            source, line, pattern, path = (
                field.decode("utf-8", errors="strict") for field in fields[offset : offset + 4]
            )
            if pattern.startswith("!"):
                matches.pop(path, None)
                continue
            matches[path] = ExcludedPath(
                path=path,
                reason="gitignore",
                source=f"{source}:{line}:{pattern}",
            )

        return IgnoreResolution(
            included=tuple(path for path in normalized if path not in matches),
            excluded=tuple(matches[path] for path in normalized if path in matches),
        )
