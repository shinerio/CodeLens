from pathlib import Path

import pytest

from codelens.bootstrap.settings import Settings
from codelens.workspace.application.file_exclusion_settings import (
    FileExclusionPolicyService,
)
from codelens.workspace.infrastructure.file_exclusion_settings import (
    FilesystemFileExclusionPolicySource,
)


def test_file_exclusion_config_loads_and_normalizes_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "file-exclusions.toml"
    config_path.write_text(
        """
exclude_binary = true
suffixes = [".LOG", ".log", "~"]
path_regexes = ['(?:^|/)__pycache__(?:/|$)']
""".strip(),
        encoding="utf-8",
    )

    policy = FilesystemFileExclusionPolicySource(config_path).get_policy()

    assert policy.exclude_binary is True
    assert policy.suffixes == (".log", "~")
    assert policy.path_regexes == (r"(?:^|/)__pycache__(?:/|$)",)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('exclude_binary = true\nsuffixes = []\npath_regexes = []\nextra = 1', "unknown"),
        ('exclude_binary = "yes"\nsuffixes = []\npath_regexes = []', "invalid"),
        ('exclude_binary = true\nsuffixes = ".log"\npath_regexes = []', "invalid"),
        ('exclude_binary = true\nsuffixes = []\npath_regexes = ["["]', "invalid"),
    ],
)
def test_file_exclusion_config_rejects_invalid_documents(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    config_path = tmp_path / "file-exclusions.toml"
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        FilesystemFileExclusionPolicySource(config_path).get_policy()


def test_file_exclusion_config_requires_the_configured_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        FilesystemFileExclusionPolicySource(tmp_path / "missing.toml").get_policy()


def test_repository_default_file_exclusion_config_is_valid() -> None:
    policy = FilesystemFileExclusionPolicySource(Settings().file_exclusion_config).get_policy()

    assert policy.exclude_binary is True
    assert {".log", ".min.js", ".js.map", "~"}.issubset(policy.suffixes)
    assert any("__pycache__" in pattern for pattern in policy.path_regexes)
    assert any("node_modules" in pattern for pattern in policy.path_regexes)


async def test_file_exclusion_service_reloads_configuration_for_each_review(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "file-exclusions.toml"
    config_path.write_text(
        "exclude_binary = true\nsuffixes = [\".log\"]\npath_regexes = []",
        encoding="utf-8",
    )
    service = FileExclusionPolicyService(
        FilesystemFileExclusionPolicySource(config_path)
    )

    initial = await service.get()
    config_path.write_text(
        "exclude_binary = true\nsuffixes = [\".trace\"]\npath_regexes = []",
        encoding="utf-8",
    )
    updated = await service.get()

    assert initial.suffixes == (".log",)
    assert updated.suffixes == (".trace",)
