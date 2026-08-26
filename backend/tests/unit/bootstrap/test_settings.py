from pathlib import Path

import pytest
from pydantic import ValidationError

from codelens.bootstrap.settings import Settings


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


def test_default_memory_limit_is_2gb(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    assert settings.memory_limit_mb == 2048
    assert settings.memory_check_interval_seconds == 5.0
    assert settings.memory_cleanup_threshold_ratio == 0.85
    assert settings.memory_reject_threshold_ratio == 0.95


def test_memory_limit_below_512mb_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="at least 512"):
        Settings(data_dir=tmp_path, memory_limit_mb=256)


def test_memory_thresholds_must_be_ordered(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="thresholds"):
        Settings(
            data_dir=tmp_path,
            memory_cleanup_threshold_ratio=0.95,
            memory_reject_threshold_ratio=0.85,
        )
