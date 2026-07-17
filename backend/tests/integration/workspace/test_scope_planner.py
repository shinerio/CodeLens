import asyncio
from pathlib import Path

import pytest

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.domain.models import (
    BranchScope,
    CommitScope,
    FullRepositoryScope,
    UncommittedScope,
)
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_workspace import GitWorkspaceAdapter


async def _commit_file(
    git: GitCli,
    repository: Path,
    path: str,
    content: str,
    message: str,
) -> str:
    absolute = repository / path
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(content, encoding="utf-8")
    await git.run(repository, "add", path)
    await git.run(repository, "commit", "-m", message)
    result = await git.run(repository, "rev-parse", "HEAD")
    return result.stdout.decode("ascii").strip()


async def _create_feature_branches(git: GitCli, repository: Path) -> tuple[str, str, str]:
    main = (await git.run(repository, "rev-parse", "main")).stdout.decode("ascii").strip()
    await git.run(repository, "checkout", "-b", "feature-a")
    feature_a = await _commit_file(
        git,
        repository,
        "src/feature_a.py",
        "FEATURE = 'a'\n",
        "add feature a",
    )
    await git.run(repository, "checkout", "main")
    await git.run(repository, "checkout", "-b", "feature-b")
    feature_b = await _commit_file(
        git,
        repository,
        "src/feature_b.py",
        "FEATURE = 'b'\n",
        "add feature b",
    )
    await git.run(repository, "checkout", "main")
    return main, feature_a, feature_b


def _planner(git: GitCli) -> ScopePlanner:
    return ScopePlanner(GitWorkspaceAdapter(git))


def _create_escaping_symlink(repository: Path) -> None:
    source = repository / "src"
    source.mkdir(exist_ok=True)
    (source / "leak.py").symlink_to("../../outside.py")


async def test_branch_scope_uses_merge_base_and_pins_target_oid(git_repository: Path) -> None:
    git = GitCli()
    main_oid, feature_a_oid, feature_b_oid = await _create_feature_branches(git, git_repository)
    (git_repository / "dirty-only-on-main.py").write_text("DIRTY = True\n", encoding="utf-8")

    plan = await _planner(git).plan(
        git_repository,
        BranchScope(base_ref="main", target_ref="feature-a"),
    )
    await git.run(git_repository, "branch", "-f", "feature-a", feature_b_oid)

    assert plan.base_oid == main_oid
    assert plan.head_oid == feature_a_oid
    assert plan.target_paths == ("src/feature_a.py",)
    assert "dirty-only-on-main.py" not in plan.target_paths


async def test_commit_scope_warns_for_non_ancestor_baseline(git_repository: Path) -> None:
    git = GitCli()
    _, feature_a_oid, feature_b_oid = await _create_feature_branches(git, git_repository)

    plan = await _planner(git).plan(
        git_repository,
        CommitScope(base_commit=feature_a_oid, target_ref="feature-b"),
    )

    assert plan.base_oid == feature_a_oid
    assert plan.head_oid == feature_b_oid
    assert plan.warnings == ("base commit is not an ancestor of target; using direct diff",)
    assert plan.target_paths == ("src/feature_a.py", "src/feature_b.py")


async def test_uncommitted_scope_collects_tracked_and_allowed_untracked_paths(
    git_repository: Path,
) -> None:
    git = GitCli()
    head_oid = (await git.run(git_repository, "rev-parse", "HEAD")).stdout.decode("ascii").strip()
    (git_repository / "README.md").write_text("# changed\n", encoding="utf-8")
    (git_repository / ".gitignore").write_text("ignored.tmp\n", encoding="utf-8")
    (git_repository / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
    (git_repository / "ignored.tmp").write_text("ignore me\n", encoding="utf-8")

    plan = await _planner(git).plan(git_repository, UncommittedScope())

    assert plan.base_oid == head_oid
    assert plan.head_oid == head_oid
    assert plan.capture_workspace_overlay
    assert plan.target_paths == (".gitignore", "README.md", "allowed.py")


async def test_full_scope_uses_only_selected_target_tree(git_repository: Path) -> None:
    git = GitCli()
    _, feature_a_oid, _ = await _create_feature_branches(git, git_repository)
    (git_repository / "dirty-only-on-main.py").write_text("DIRTY = True\n", encoding="utf-8")

    plan = await _planner(git).plan(
        git_repository,
        FullRepositoryScope(target_ref="feature-a"),
    )

    assert plan.base_oid == feature_a_oid
    assert plan.head_oid == feature_a_oid
    assert plan.target_paths == ("README.md", "src/feature_a.py")
    assert not plan.capture_workspace_overlay


async def test_workspace_overlay_requires_current_checkout_head(git_repository: Path) -> None:
    git = GitCli()
    await _create_feature_branches(git, git_repository)

    with pytest.raises(InvalidRepositoryError, match="current HEAD"):
        await _planner(git).plan(
            git_repository,
            BranchScope(
                base_ref="main",
                target_ref="feature-a",
                include_workspace_changes=True,
            ),
        )


async def test_rejects_option_like_refs_before_git_invocation(git_repository: Path) -> None:
    with pytest.raises(InvalidRepositoryError, match="invalid Git ref"):
        await _planner(GitCli()).plan(
            git_repository,
            BranchScope(base_ref="main", target_ref="--upload-pack=evil"),
        )


async def test_full_scope_rejects_symlink_that_escapes_repository(git_repository: Path) -> None:
    git = GitCli()
    await asyncio.to_thread(_create_escaping_symlink, git_repository)
    await git.run(git_repository, "add", "src/leak.py")
    await git.run(git_repository, "commit", "-m", "add escaping symlink")

    with pytest.raises(InvalidRepositoryError, match="symlink target escapes"):
        await _planner(git).plan(git_repository, FullRepositoryScope())
