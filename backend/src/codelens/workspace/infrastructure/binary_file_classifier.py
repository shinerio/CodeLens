"""Classify binary Review changes using frozen current and base content."""

import asyncio
from pathlib import Path

from codelens.workspace.domain.models import ReviewFileChange
from codelens.workspace.infrastructure.git_cli import GitCli

_PROBE_BYTES = 64 * 1024


def _is_binary(content: bytes) -> bool:
    """Use Git-compatible NUL detection plus strict text decoding."""

    if b"\0" in content:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


class BinaryFileClassifier:
    """Report a change as binary when either relevant frozen side is binary."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def classify_current(self, repository: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
        """Classify current context files without invoking Git once per path."""

        attribute_binary = await self._attribute_binary_paths(repository, paths)
        probes = await asyncio.gather(
            *(asyncio.to_thread(self._read_current, repository, path) for path in paths)
        )
        return tuple(
            path
            for path, content in zip(paths, probes, strict=True)
            if path in attribute_binary or _is_binary(content)
        )

    async def classify(
        self,
        repository: Path,
        base_oid: str,
        changes: tuple[ReviewFileChange, ...],
    ) -> tuple[str, ...]:
        attribute_binary = await self._attribute_binary_paths(
            repository, tuple(change.path for change in changes)
        )
        binary: list[str] = []
        for change in changes:
            if change.path in attribute_binary or await self._git_reports_binary(
                repository, base_oid, change.path
            ):
                binary.append(change.path)
                continue
            if change.change_type != "deleted":
                content = await asyncio.to_thread(self._read_current, repository, change.path)
            else:
                content = b""
            if _is_binary(content):
                binary.append(change.path)
        return tuple(sorted(binary))

    async def _attribute_binary_paths(
        self,
        repository: Path,
        paths: tuple[str, ...],
    ) -> frozenset[str]:
        if not paths:
            return frozenset()
        result = await self._git.run(
            repository,
            "check-attr",
            "-z",
            "--stdin",
            "binary",
            "diff",
            stdin=b"\0".join(path.encode("utf-8") for path in paths) + b"\0",
        )
        fields = result.stdout.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) % 3 != 0:
            raise ValueError("unexpected git check-attr -z output")
        binary: set[str] = set()
        for offset in range(0, len(fields), 3):
            path, attribute, value = (
                field.decode("utf-8", errors="strict") for field in fields[offset : offset + 3]
            )
            if (attribute == "binary" and value == "set") or (
                attribute == "diff" and value in {"unset", "binary"}
            ):
                binary.add(path)
        return frozenset(binary)

    @staticmethod
    def _read_current(repository: Path, path: str) -> bytes:
        absolute = repository / path
        if absolute.is_symlink() or absolute.is_dir():
            return b""
        try:
            with absolute.open("rb") as source:
                return source.read(_PROBE_BYTES)
        except FileNotFoundError:
            return b""

    async def _git_reports_binary(self, repository: Path, base_oid: str, path: str) -> bool:
        result = await self._git.run(
            repository,
            "diff",
            "--numstat",
            base_oid,
            "--",
            path,
        )
        return any(line.startswith(b"-\t-\t") for line in result.stdout.splitlines())
