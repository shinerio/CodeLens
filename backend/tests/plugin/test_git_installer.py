import json
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


def _write_manifest(directory: Path, **overrides: object) -> None:
    payload = {
        "plugin_id": "example-plugin",
        "name": "Example",
        "version": "1.4.2",
        "platform": "local",
        "capabilities": {"report": {"entry_point": "sink:Sink"}},
        **overrides,
    }
    (directory / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_without_plugin_api_version_is_legacy_v1(tmp_path: Path) -> None:
    _write_manifest(tmp_path)

    manifest = GitPluginInstaller(cast(GitCli, object()), tmp_path)._read_manifest(tmp_path)

    assert manifest.plugin_api_version.value == "1"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"version": "2.0.0", "plugin_api_version": "2"}, "min_codelens_version"),
        (
            {
                "version": "1.9.0",
                "plugin_api_version": "2",
                "min_codelens_version": "0.2.0",
            },
            "plugin version",
        ),
    ],
)
def test_v2_manifest_compatibility_rules(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    _write_manifest(tmp_path, **overrides)
    installer = GitPluginInstaller(cast(GitCli, object()), tmp_path)

    with pytest.raises(PluginInstallError, match=message):
        installer._validate_manifest(installer._read_manifest(tmp_path))


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
