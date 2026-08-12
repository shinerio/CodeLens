from pathlib import Path

import pytest
from pydantic import ValidationError

from codelens.bootstrap.settings import Settings


def test_local_settings_allow_empty_repository_roots(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, host="127.0.0.1")

    assert settings.repository_roots == ()


def test_file_exclusion_config_defaults_to_project_conf_directory() -> None:
    settings = Settings()

    assert settings.file_exclusion_config.name == "file-exclusions.toml"
    assert settings.file_exclusion_config.parent.name == "conf"


def test_unauthenticated_remote_bind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(data_dir=tmp_path, host="192.0.2.1", repository_roots=(tmp_path,))


def test_non_loopback_wildcard_bind_allows_empty_repository_roots(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, host="0.0.0.0")

    assert settings.repository_roots == ()

    settings_with_roots = Settings(data_dir=tmp_path, host="0.0.0.0", repository_roots=(tmp_path,))

    assert settings_with_roots.repository_roots == (tmp_path.resolve(),)


def test_local_bind_normalizes_repository_roots(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()

    settings = Settings(data_dir=tmp_path, host="127.0.0.1", repository_roots=(root,))

    assert settings.repository_roots == (root.resolve(),)


def test_multiple_workers_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="one Worker"):
        Settings(data_dir=tmp_path, max_workers=2)
