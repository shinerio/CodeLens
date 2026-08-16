import base64
import json
from pathlib import Path

from codelens.workspace.infrastructure.git_cli import GitCli


async def _commit(repository: Path, git: GitCli, message: str) -> None:
    await git.run(repository, "add", "--all")
    await git.run(repository, "commit", "-m", message)


def _overlay_payload(patch: bytes = b"", entries: tuple[dict[str, object], ...] = ()) -> bytes:
    return json.dumps(
        {
            "schema_version": 2,
            "tracked_patch": base64.b64encode(patch).decode("ascii"),
            "entries": entries,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


async def test_git_reader_applies_verified_tracked_overlay_to_pinned_target(tmp_path: Path) -> None:
    git = GitCli()
    repository = tmp_path / "repository"
    repository.mkdir()
    await git.run(repository, "init")
    await git.run(repository, "config", "user.email", "test@example.com")
    await git.run(repository, "config", "user.name", "Test")
    source = repository / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("one\ntwo\n")
    await _commit(repository, git, "base")
    head = (await git.run(repository, "rev-parse", "HEAD")).stdout.decode().strip()
    source.write_text("one\noverlay two\n")
    patch = (await git.run(repository, "diff", "--binary", "HEAD", "--")).stdout

    content = await git.read_overlay_optional(
        repository, head, "src/example.py", _overlay_payload(patch)
    )

    assert content == b"one\noverlay two\n"


async def test_git_reader_reads_overlay_entry_and_tracked_deletion(tmp_path: Path) -> None:
    git = GitCli()
    repository = tmp_path / "repository"
    repository.mkdir()
    await git.run(repository, "init")
    await git.run(repository, "config", "user.email", "test@example.com")
    await git.run(repository, "config", "user.name", "Test")
    source = repository / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("removed\n")
    await _commit(repository, git, "base")
    head = (await git.run(repository, "rev-parse", "HEAD")).stdout.decode().strip()
    source.unlink()
    patch = (await git.run(repository, "diff", "--binary", "HEAD", "--")).stdout
    entry = {
        "path": "src/untracked.py",
        "mode": 0o644,
        "kind": "file",
        "content": base64.b64encode(b"untrusted input\n").decode("ascii"),
    }

    deleted = await git.read_overlay_optional(
        repository, head, "src/example.py", _overlay_payload(patch)
    )
    untracked = await git.read_overlay_optional(
        repository, head, "src/untracked.py", _overlay_payload(patch, (entry,))
    )

    assert deleted is None
    assert untracked == b"untrusted input\n"


async def test_git_reader_resolves_rename_old_path(tmp_path: Path) -> None:
    git = GitCli()
    repository = tmp_path / "repository"
    repository.mkdir()
    await git.run(repository, "init")
    await git.run(repository, "config", "user.email", "test@example.com")
    await git.run(repository, "config", "user.name", "Test")
    old = repository / "src" / "old.py"
    old.parent.mkdir()
    old.write_text("old\n")
    await _commit(repository, git, "base")
    base = (await git.run(repository, "rev-parse", "HEAD")).stdout.decode().strip()
    new = repository / "src" / "new.py"
    new.write_text("old\n")
    old.unlink()
    await _commit(repository, git, "rename")
    head = (await git.run(repository, "rev-parse", "HEAD")).stdout.decode().strip()

    assert await git.resolve_old_path_optional(repository, base, head, "src/new.py") == "src/old.py"
