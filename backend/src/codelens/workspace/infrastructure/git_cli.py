import asyncio
import base64
import json
import tempfile
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
        max_output_bytes: int = 2 * 1024 * 1024,
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

        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repository),
                *args,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise InvalidRepositoryError("git executable is not installed or not on PATH") from None
        except PermissionError:
            raise InvalidRepositoryError("git executable cannot be executed") from None
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

    async def verify_available(self, repository: Path) -> None:
        """Fail startup unless a runnable Git executable is available on the process PATH."""

        result = await self.run(repository, "--version")
        if not result.stdout.startswith(b"git version "):
            raise InvalidRepositoryError("git executable returned an invalid version response")

    async def read_revision(self, repository: Path, revision: str, path: str) -> bytes:
        """Read one normalized repository-relative path from a pinned Git revision."""

        self._validate_revision_path(path)
        return (await self.run(repository, "show", f"{revision}:{path}")).stdout

    async def read_revision_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
    ) -> bytes | None:
        """Read a pinned path or return None when that revision has no such path."""

        self._validate_revision_path(path)
        result = await self.run(
            repository,
            "show",
            f"{revision}:{path}",
            ok_codes=(0, 128),
        )
        return result.stdout if result.returncode == 0 else None

    async def resolve_old_path_optional(
        self,
        repository: Path,
        base_revision: str,
        target_revision: str,
        path: str,
    ) -> str | None:
        """Return the pre-rename path at base revision for a target-side path."""

        self._validate_revision_path(path)
        result = await self.run(
            repository,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            base_revision,
            target_revision,
        )
        fields = tuple(field for field in result.stdout.split(b"\0") if field)
        index = 0
        while index < len(fields):
            status = fields[index]
            field_count = 3 if status.startswith(b"R") else 2
            if len(fields) < index + field_count:
                raise InvalidRepositoryError("rename lookup returned truncated output")
            if field_count == 3:
                old_path = fields[index + 1]
                new_path = fields[index + 2]
                if new_path.decode("utf-8", errors="strict") == path:
                    return old_path.decode("utf-8", errors="strict")
            index += field_count
        return None

    async def read_overlay_optional(
        self,
        repository: Path,
        revision: str,
        path: str,
        payload: bytes,
    ) -> bytes | None:
        """Read one file after applying its trusted, hash-verified overlay entry."""

        self._validate_revision_path(path)
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("overlay Artifact is not valid JSON") from None
        if not isinstance(decoded, dict) or decoded.get("schema_version") != 2:
            raise ValueError("unsupported overlay Artifact schema")
        entries = decoded.get("entries")
        if not isinstance(entries, list):
            raise ValueError("invalid overlay Artifact entries")
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("path") != path:
                continue
            if entry.get("kind") not in {"file", "symlink"}:
                raise ValueError("unknown overlay entry kind")
            content = entry.get("content")
            if not isinstance(content, str):
                raise ValueError("invalid overlay entry content")
            try:
                return base64.b64decode(content, validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("invalid overlay entry content") from error
        encoded_patch = decoded.get("tracked_patch")
        if not isinstance(encoded_patch, str):
            raise ValueError("invalid overlay tracked patch")
        try:
            patch = base64.b64decode(encoded_patch, validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("invalid overlay tracked patch") from error
        if not patch:
            return await self.read_revision_optional(repository, revision, path)

        with tempfile.TemporaryDirectory(prefix="codelens-overlay-preview-") as directory:
            root = Path(directory)
            await self.run(root, "init")
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            preimage = await self.read_revision_optional(repository, revision, path)
            if preimage is not None:
                destination.write_bytes(preimage)
            result = await self.run(
                root,
                "apply",
                "--binary",
                "--whitespace=nowarn",
                f"--include={path}",
                "-",
                stdin=patch,
                ok_codes=(0, 128),
            )
            if result.returncode == 128:
                raise ValueError("overlay tracked patch does not match its pinned preimage")
            if result.returncode != 0:
                raise ValueError("overlay tracked patch is invalid")
            return destination.read_bytes() if destination.exists() else None

    @staticmethod
    def _validate_revision_path(path: str) -> None:
        """Reject paths that could escape the repository-relative Git object lookup."""

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

    async def clone(
        self,
        url: str,
        destination: Path,
        *,
        ref: str | None = None,
        depth: int = 1,
    ) -> None:
        """Clone a remote repository into a fresh local destination.

        Uses ``--depth`` for a shallow clone and an optional ``--branch`` to
        select a specific ref. The destination's parent must exist.
        """

        if not url or "\0" in url:
            raise InvalidRepositoryError("clone URL is invalid")
        if depth < 1:
            raise InvalidRepositoryError("clone depth must be positive")
        args: list[str] = ["clone", "--depth", str(depth)]
        if ref:
            args.extend(["--branch", ref])
        args.extend([url, str(destination)])
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise InvalidRepositoryError("git executable is not installed or not on PATH") from None
        except PermissionError:
            raise InvalidRepositoryError("git executable cannot be executed") from None
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(None),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise InvalidRepositoryError("git clone timed out") from None
        returncode = process.returncode
        if returncode is None:
            raise InvalidRepositoryError("git clone did not terminate")
        if len(stdout) + len(stderr) > self._max_output_bytes:
            raise InvalidRepositoryError("git clone exceeded the configured output limit")
        if returncode != 0:
            message = stderr[:4096].decode("utf-8", errors="replace").strip()
            raise InvalidRepositoryError(message or "git clone failed")
