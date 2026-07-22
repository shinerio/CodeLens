import asyncio
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from codelens.shared.domain.errors import InvalidRepositoryError


@dataclass(frozen=True)
class CommandResult:
    """Contain bounded Git output and an explicitly accepted exit status."""

    returncode: int
    stdout: bytes
    stderr: bytes


class GitCli:
    """Execute Git with argument arrays, timeouts, and bounded input/output.

    The adapter never invokes a shell. Callers must enumerate every accepted exit
    code, while timeout and output-limit failures are mapped to a stable internal
    error without exposing unbounded Git diagnostics.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 1024 * 1024,
        max_input_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Git timeout must be positive")
        if max_output_bytes <= 0 or max_input_bytes <= 0:
            raise ValueError("Git input and output limits must be positive")
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._max_input_bytes = max_input_bytes

    async def run(
        self,
        repository: Path,
        *args: str,
        stdin: bytes | None = None,
        ok_codes: tuple[int, ...] = (0,),
    ) -> CommandResult:
        """Run one bounded Git command in a repository without shell expansion."""

        if not ok_codes:
            raise ValueError("at least one allowed Git exit code is required")
        if stdin is not None and len(stdin) > self._max_input_bytes:
            raise InvalidRepositoryError("git input exceeded the configured limit")

        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise InvalidRepositoryError("git command timed out") from None

        returncode = process.returncode
        if returncode is None:
            raise InvalidRepositoryError("git command did not terminate")
        if len(stdout) + len(stderr) > self._max_output_bytes:
            raise InvalidRepositoryError("git command exceeded the configured output limit")
        if returncode not in ok_codes:
            message = stderr[:4096].decode("utf-8", errors="replace").strip()
            raise InvalidRepositoryError(message or "git command failed")
        return CommandResult(returncode, stdout, stderr)

    async def read_revision(self, repository: Path, revision: str, path: str) -> bytes:
        """Read one normalized repository-relative path from a pinned Git revision."""

        candidate = PurePosixPath(path)
        if (
            not path
            or "\0" in path
            or "\\" in path
            or candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.as_posix() != path
        ):
            raise InvalidRepositoryError("revision path is unsafe")
        return (await self.run(repository, "show", f"{revision}:{path}")).stdout
