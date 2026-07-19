import json
import logging
from pathlib import Path

from codelens.bootstrap.logging import (
    configure_process_logging,
    get_runtime_log_level,
    set_runtime_log_level,
)


def test_configure_process_logging_writes_structured_events_to_the_log_directory(
    tmp_path: Path,
) -> None:
    log_path = configure_process_logging("api", log_directory=tmp_path)

    logging.getLogger("codelens.test").info("review creation failed", extra={"task_id": "task-1"})

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload == {
        "level": "INFO",
        "logger": "codelens.test",
        "message": "review creation failed",
        "process": "api",
        "task_id": "task-1",
        "timestamp": payload["timestamp"],
    }


def test_runtime_log_level_is_persisted_for_other_processes(tmp_path: Path) -> None:
    assert get_runtime_log_level(tmp_path) == "info"

    set_runtime_log_level(tmp_path, "debug")

    assert get_runtime_log_level(tmp_path) == "debug"
    assert json.loads((tmp_path / "logging.json").read_text(encoding="utf-8")) == {
        "level": "debug"
    }
