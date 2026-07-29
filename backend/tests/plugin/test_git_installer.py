from pathlib import Path
from typing import cast

import pytest

from codelens.plugin.domain.models import (
    PluginInstallError,
    PluginManifest,
    ReportCapability,
)
from codelens.plugin.infrastructure.git_installer import GitPluginInstaller
from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.infrastructure.git_cli import GitCli


def test_external_plugin_cannot_use_the_builtin_plugin_id(tmp_path: Path) -> None:
    installer = GitPluginInstaller(cast(GitCli, object()), tmp_path)
    manifest = PluginManifest(
        plugin_id="local",
        name="Impersonated local plugin",
        version="1.0.0",
        description="",
        author="test",
        platform="local",
        capabilities={
            "report": ReportCapability(entry_point="sink:Sink")
        },
    )

    with pytest.raises(PluginInstallError, match="reserved"):
        installer._validate_manifest(manifest)


class FailingGit:
    async def clone(
        self,
        url: str,
        destination: Path,
        *,
        ref: str | None = None,
        depth: int = 1,
    ) -> None:
        del url, destination, ref, depth
        raise InvalidRepositoryError(
            "authentication failed for https://user:secret@example.invalid/plugin.git"
        )


async def test_clone_errors_do_not_expose_git_credentials(tmp_path: Path) -> None:
    installer = GitPluginInstaller(cast(GitCli, FailingGit()), tmp_path)

    with pytest.raises(PluginInstallError) as captured:
        await installer.install("https://user:secret@example.invalid/plugin.git")

    assert "secret" not in str(captured.value)
    assert str(captured.value) == "Git repository could not be cloned"
