from pathlib import Path

import pytest
from pydantic import ValidationError

from codelens.bootstrap.settings import Settings


def test_local_settings_allow_empty_repository_roots(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, host="127.0.0.1")

    assert settings.repository_roots == ()


def test_unauthenticated_remote_bind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(data_dir=tmp_path, host="0.0.0.0", repository_roots=(tmp_path,))


def test_local_bind_normalizes_repository_roots(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()

    settings = Settings(data_dir=tmp_path, host="127.0.0.1", repository_roots=(root,))

    assert settings.repository_roots == (root.resolve(),)


def test_multiple_workers_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="one Worker"):
        Settings(data_dir=tmp_path, max_workers=2)
