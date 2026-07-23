"""Process-local structured logging for the API, Worker, and supervisor."""

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

type ProcessName = Literal["api", "worker", "supervisor"]
type LogLevel = Literal["debug", "info", "warning", "error"]

_LOG_LEVELS: dict[LogLevel, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_STANDARD_RECORD_ATTRIBUTES = frozenset(
    set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"},
)


class _JsonLogFormatter(logging.Formatter):
    """Serialize safe structured log fields without adding source or secret payloads."""

    def __init__(self, process_name: ProcessName) -> None:
        super().__init__()
        self._process_name = process_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "process": self._process_name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRIBUTES and not key.startswith("_"):
                payload[key] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _CodeLensFileHandler(RotatingFileHandler):
    """Identify handlers owned by CodeLens without touching application handlers."""

    codelens_log_path: Path


def get_runtime_log_level(data_directory: Path) -> LogLevel:
    """Read the shared log level, defaulting safely when its config is absent or invalid."""

    try:
        value = json.loads((data_directory / "logging.json").read_text(encoding="utf-8"))
        level = value.get("level")
        if level in _LOG_LEVELS:
            return level
    except (OSError, json.JSONDecodeError):
        pass
    return "info"


def set_runtime_log_level(data_directory: Path, level: LogLevel) -> None:
    """Atomically persist a level for independently running processes to observe."""

    data_directory.mkdir(parents=True, exist_ok=True)
    target = data_directory / "logging.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"level": level}), encoding="utf-8")
    os.replace(temporary, target)


class _RuntimeLevelFilter(logging.Filter):
    """Refresh the shared level for every emitted record without process restarts."""

    def __init__(self, data_directory: Path) -> None:
        super().__init__()
        self._data_directory = data_directory

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= _LOG_LEVELS[get_runtime_log_level(self._data_directory)]


def _file_handler(
    log_path: Path,
    process_name: ProcessName,
    data_directory: Path,
) -> _CodeLensFileHandler:
    """Create one bounded handler without sharing lifecycle with another logger."""

    handler = _CodeLensFileHandler(
        log_path,
        encoding="utf-8",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(_JsonLogFormatter(process_name))
    handler.addFilter(_RuntimeLevelFilter(data_directory))
    handler.codelens_log_path = log_path
    return handler


def configure_process_logging(
    process_name: ProcessName,
    *,
    log_directory: Path | None = None,
    data_directory: Path | None = None,
) -> Path:
    """Configure bounded JSON logs in ``logs/`` relative to the launch directory.

    The handler is process-local and replaces only prior CodeLens file handlers, so API,
    Worker, and supervisor logs remain isolated while repeated test or startup setup is safe.
    """

    directory = (log_directory or Path.cwd() / "logs").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / f"{process_name}.log"
    level_directory = (data_directory or Path.cwd() / "data").resolve()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for existing_handler in tuple(root_logger.handlers):
        if isinstance(existing_handler, _CodeLensFileHandler):
            root_logger.removeHandler(existing_handler)
            existing_handler.close()
    root_logger.addHandler(_file_handler(log_path, process_name, level_directory))

    application_logger = logging.getLogger("codelens")
    for existing_handler in tuple(application_logger.handlers):
        if isinstance(existing_handler, _CodeLensFileHandler):
            application_logger.removeHandler(existing_handler)
            existing_handler.close()
    # Third-party runtimes can replace root handlers during import. Keep CodeLens
    # task failures on an independently owned logger so their tracebacks survive.
    application_logger.addHandler(_file_handler(log_path, process_name, level_directory))
    application_logger.propagate = False

    for logger_name in ("codelens", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.disabled = False
        logger.setLevel(logging.INFO)
        if logger_name != "codelens":
            logger.propagate = True

    # Alembic's fileConfig(disable_existing_loggers=True) disables all codelens.*
    # child loggers created before migration. Re-enable them so scheduler and
    # executor tracebacks survive the migration round-trip.
    for name, obj in logging.Logger.manager.loggerDict.items():
        if name.startswith("codelens.") and isinstance(obj, logging.Logger):
            obj.disabled = False
    return log_path
