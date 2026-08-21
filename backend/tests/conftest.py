from pathlib import Path

import pytest

pytest_plugins = ["tests.fixtures.git_repository"]


@pytest.fixture(autouse=True)
def _isolate_repository_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from loading the developer's local ``conf/runtime-settings.toml``.

    Tests construct ``Settings`` with only ``data_dir`` overridden, which causes the
    ``model_validator`` to fall through to the committed config file at
    ``conf/runtime-settings.toml``. That file carries developer-specific
    ``repository.roots`` (e.g. ``["/root/code"]``), which makes contract tests fail
    because temporary repositories under ``/tmp`` fall outside the trust boundary.

    We monkeypatch ``_load_repository_roots`` to return ``()`` when loading the
    default config path, while preserving the original behavior for tests that
    explicitly pass ``repository_settings_config``.
    """

    from codelens.bootstrap import settings as settings_module

    original_load = settings_module._load_repository_roots
    project_root = Path(settings_module.__file__).resolve().parent.parent.parent.parent.parent
    default_config = project_root / "conf" / "runtime-settings.toml"

    def patched_load(config_path: Path) -> tuple:
        if config_path.resolve() == default_config.resolve():
            return ()
        return original_load(config_path)

    monkeypatch.setattr(settings_module, "_load_repository_roots", patched_load)
