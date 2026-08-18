from pathlib import Path

import pytest
from pydantic import ValidationError

from codelens.bootstrap.settings import Settings


def test_default_settings_bind_lan_and_allow_all_host_roots() -> None:
    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.repository_roots == ()


def test_runtime_settings_load_repository_roots_from_conf_file(tmp_path: Path) -> None:
    config = tmp_path / "runtime-settings.toml"
    config.write_text('[repository]\nroots = ["repositories/example"]\n', encoding="utf-8")

    settings = Settings(repository_settings_config=config)

    assert settings.repository_roots == ((tmp_path.parent / "repositories/example").resolve(),)


def test_empty_runtime_repository_roots_remain_allowed(tmp_path: Path) -> None:
    config = tmp_path / "runtime-settings.toml"
    config.write_text("[repository]\nroots = []\n", encoding="utf-8")

    settings = Settings(repository_settings_config=config)

    assert settings.repository_roots == ()


def test_invalid_runtime_repository_roots_are_rejected(tmp_path: Path) -> None:
    config = tmp_path / "runtime-settings.toml"
    config.write_text('[repository]\nroots = [""]\n', encoding="utf-8")

    with pytest.raises(ValidationError, match="empty paths"):
        Settings(repository_settings_config=config)


def test_repository_roots_normalize_to_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()

    settings = Settings(
        repository_settings_config=tmp_path / "missing.toml",
        repository_roots=(root,),
    )

    assert settings.repository_roots == (root.resolve(),)


def test_multiple_workers_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="one Worker"):
        Settings(data_dir=tmp_path, max_workers=2)
