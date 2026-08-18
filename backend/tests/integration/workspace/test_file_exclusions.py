from pathlib import Path

from codelens.workspace.domain.models import ReviewFileChange
from codelens.workspace.domain.review_file_scope import (
    ReviewFileExclusionPolicy,
    ReviewFileExclusionReason,
    ReviewFileScopeResolver,
)
from codelens.workspace.infrastructure.binary_file_classifier import BinaryFileClassifier
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_ignore import GitIgnoreResolver


async def _git(repository: Path, *args: str) -> bytes:
    result = await GitCli().run(repository, *args)
    assert result.returncode == 0
    return result.stdout


async def test_real_git_facts_feed_one_review_file_scope_resolver(tmp_path: Path) -> None:
    """Cover native ignore, attributes, both binary sides, symlinks, and user rules."""

    await _git(tmp_path, "init")
    await _git(tmp_path, "config", "user.email", "scope@example.test")
    await _git(tmp_path, "config", "user.name", "Scope Test")
    await _git(tmp_path, "config", "commit.gpgSign", "false")
    (tmp_path / "tracked-ignore.txt").write_text("base\n")
    (tmp_path / "deleted.bin").write_bytes(b"base\0deleted")
    (tmp_path / "old.bin").write_bytes(b"base\0rename")
    (tmp_path / ".gitignore").write_text("ignored-untracked.txt\ntracked-ignore.txt\n")
    (tmp_path / ".gitattributes").write_text("*.forced binary\n")
    await _git(tmp_path, "add", ".")
    await _git(tmp_path, "add", "-f", "tracked-ignore.txt")
    await _git(tmp_path, "commit", "-m", "base")
    base_oid = (await _git(tmp_path, "rev-parse", "HEAD")).decode().strip()

    (tmp_path / "tracked-ignore.txt").write_text("changed\n")
    (tmp_path / "ignored-untracked.txt").write_text("ignored\n")
    (tmp_path / "added.bin").write_bytes(b"new\0binary")
    (tmp_path / "marked.forced").write_text("text marked binary\n")
    (tmp_path / "deleted.bin").unlink()
    await _git(tmp_path, "mv", "old.bin", "renamed.bin")
    (tmp_path / "link.txt").symlink_to("tracked-ignore.txt")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "code.py").write_text("generated = True\n")
    (tmp_path / "bundle.min.js").write_text("const generated = true;\n")

    changes = (
        ReviewFileChange("tracked-ignore.txt", "modified"),
        ReviewFileChange("ignored-untracked.txt", "added"),
        ReviewFileChange("added.bin", "added"),
        ReviewFileChange("marked.forced", "added"),
        ReviewFileChange("deleted.bin", "deleted"),
        ReviewFileChange("renamed.bin", "renamed", old_path="old.bin"),
        ReviewFileChange("link.txt", "added"),
        ReviewFileChange("generated/code.py", "added"),
        ReviewFileChange("bundle.min.js", "added"),
    )
    paths = tuple(change.path for change in changes)
    ignored = await GitIgnoreResolver(GitCli()).resolve(tmp_path, paths)
    binary = await BinaryFileClassifier(GitCli()).classify(tmp_path, base_oid, changes)
    scope = ReviewFileScopeResolver().resolve(
        candidate_review_paths=paths,
        candidate_context_paths=paths,
        policy=ReviewFileExclusionPolicy(
            suffixes=(".MIN.JS",),
            path_regexes=(r"^generated/",),
        ),
        git_ignored_paths=tuple(item.path for item in ignored.excluded),
        binary_paths=binary,
    )

    assert "tracked-ignore.txt" in ignored.included
    assert tuple(item.path for item in ignored.excluded) == ("ignored-untracked.txt",)
    assert set(binary) == {"added.bin", "deleted.bin", "marked.forced", "renamed.bin"}
    assert scope.review_paths == ("link.txt", "tracked-ignore.txt")
    reasons = {item.path: item.reasons for item in scope.exclusions}
    assert reasons["ignored-untracked.txt"] == (ReviewFileExclusionReason.GITIGNORE,)
    assert reasons["bundle.min.js"] == (ReviewFileExclusionReason.USER_SUFFIX,)
    assert reasons["generated/code.py"] == (ReviewFileExclusionReason.USER_REGEX,)
    assert reasons["marked.forced"] == (ReviewFileExclusionReason.BINARY,)
