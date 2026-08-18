"""Process-local structured logging for the API, Worker, and supervisor."""

import gzip
import json
import logging
import os
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

type ProcessName = Literal["api", "worker", "supervisor", "unified"]
type LogLevel = Literal["debug", "info", "warning", "error"]

_LOG_LEVELS: dict[LogLevel, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}
_UNIFIED_WORKER_LOGGERS = (
    "codelens.worker",
    "codelens.review.infrastructure.openai_runtime",
)

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
            "level": record.levelname.lower(),
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


class _CodeLensModelFileHandler(RotatingFileHandler):
    """Identify the dedicated compressed model transcript handler."""

    codelens_log_path: Path


def _gzip_rotator(source: str, destination: str) -> None:
    """Compress one completed model log segment and remove its source file."""

    with open(source, "rb") as source_handle, gzip.open(destination, "wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle)
    os.chmod(destination, 0o600)
    os.remove(source)


def _read_logging_settings(data_directory: Path) -> dict[str, object] | None:
    try:
        value = json.loads((data_directory / "logging.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def get_runtime_log_level(
    data_directory: Path,
    default_level: LogLevel = "info",
) -> LogLevel:
    """Read the shared log level, defaulting safely when its config is absent or invalid."""

    value = _read_logging_settings(data_directory)
    level = value.get("level") if value is not None else None
    return level if level in _LOG_LEVELS else default_level


def get_model_output_logging_enabled(
    data_directory: Path,
    default_enabled: bool = True,
) -> bool:
    """Read the operator opt-in for writing complete model output transcripts."""

    value = _read_logging_settings(data_directory)
    enabled = value.get("model_output_enabled") if value is not None else None
    return enabled if isinstance(enabled, bool) else default_enabled


def set_runtime_log_level(data_directory: Path, level: LogLevel) -> None:
    """Atomically persist a level for independently running processes to observe."""

    update_runtime_logging(data_directory, level=level)


def set_model_output_logging_enabled(
    data_directory: Path,
    enabled: bool,
) -> None:
    """Atomically persist the model output logging opt-in."""

    update_runtime_logging(data_directory, model_output_enabled=enabled)


def update_runtime_logging(
    data_directory: Path,
    *,
    level: LogLevel | None = None,
    model_output_enabled: bool | None = None,
) -> None:
    """Atomically merge runtime logging settings for independently running processes."""

    data_directory.mkdir(parents=True, exist_ok=True)
    target = data_directory / "logging.json"
    current = _read_logging_settings(data_directory) or {}
    current["level"] = level if level is not None else current.get("level", "info")
    current["model_output_enabled"] = (
        model_output_enabled
        if model_output_enabled is not None
        else current.get("model_output_enabled", True)
    )
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(current), encoding="utf-8")
    os.replace(temporary, target)


class _RuntimeLevelFilter(logging.Filter):
    """Refresh the shared level for every emitted record without process restarts."""

    def __init__(self, data_directory: Path, default_level: LogLevel) -> None:
        super().__init__()
        self._data_directory = data_directory
        self._default_level = default_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= _LOG_LEVELS[
            get_runtime_log_level(self._data_directory, self._default_level)
        ]


def _file_handler(
    log_path: Path,
    process_name: ProcessName,
    data_directory: Path,
    default_level: LogLevel,
) -> _CodeLensFileHandler:
    """Create one bounded handler without sharing lifecycle with another logger."""

    handler = _CodeLensFileHandler(
        log_path,
        encoding="utf-8",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(_JsonLogFormatter(process_name))
    handler.addFilter(_RuntimeLevelFilter(data_directory, default_level))
    handler.codelens_log_path = log_path
    return handler


def configure_process_logging(
    process_name: ProcessName,
    *,
    log_directory: Path | None = None,
    data_directory: Path | None = None,
    default_level: LogLevel = "info",
    model_log_max_bytes: int = 10 * 1024 * 1024,
) -> Path:
    """Configure bounded JSON logs in ``logs/`` relative to the launch directory.

    The handler is process-local and replaces prior CodeLens file handlers plus inherited
    root console handlers. The launcher owns process stdout, so allowing runtime records to
    propagate there would duplicate API or Worker events in ``supervisor.log``.
    """

    project_root = Path(__file__).resolve().parents[4]
    directory = (log_directory or project_root / "logs").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    runtime_process: ProcessName = "api" if process_name == "unified" else process_name
    log_path = directory / f"{runtime_process}.log"
    level_directory = (data_directory or Path.cwd() / "data").resolve()

    root_logger = logging.getLogger()
    # Runtime handlers own the configurable threshold. Logger-level INFO gates
    # would otherwise discard DEBUG records before the persisted filter sees them.
    root_logger.setLevel(logging.DEBUG)
    for existing_handler in tuple(root_logger.handlers):
        is_inherited_console = isinstance(
            existing_handler, logging.StreamHandler
        ) and not isinstance(existing_handler, logging.FileHandler)
        if isinstance(existing_handler, _CodeLensFileHandler) or is_inherited_console:
            root_logger.removeHandler(existing_handler)
            existing_handler.close()
    runtime_handler = _file_handler(
        log_path, runtime_process, level_directory, default_level
    )
    root_logger.addHandler(runtime_handler)

    model_log_path = directory / "model.log"
    model_logger = logging.getLogger("codelens.model")
    for existing_handler in tuple(model_logger.handlers):
        if isinstance(existing_handler, _CodeLensModelFileHandler):
            model_logger.removeHandler(existing_handler)
            existing_handler.close()
    model_handler = _CodeLensModelFileHandler(
        model_log_path,
        encoding="utf-8",
        maxBytes=model_log_max_bytes,
        backupCount=1,
    )
    os.chmod(model_log_path, 0o600)
    model_handler.namer = lambda path: f"{path}.gz"
    model_handler.rotator = _gzip_rotator
    model_handler.setFormatter(_JsonLogFormatter(process_name))
    model_handler.codelens_log_path = model_log_path
    model_logger.addHandler(model_handler)
    model_logger.disabled = False
    model_logger.setLevel(logging.INFO)
    model_logger.propagate = False

    application_logger = logging.getLogger("codelens")
    for existing_handler in tuple(application_logger.handlers):
        if isinstance(existing_handler, _CodeLensFileHandler):
            application_logger.removeHandler(existing_handler)
            existing_handler.close()
    # Third-party runtimes can replace root handlers during import. Keep CodeLens
    # task failures on an independently owned logger so their tracebacks survive.
    application_logger.addHandler(runtime_handler)
    application_logger.propagate = False

    worker_handler = (
        _file_handler(
            directory / "worker.log", "worker", level_directory, default_level
        )
        if process_name == "unified"
        else None
    )
    for logger_name in _UNIFIED_WORKER_LOGGERS:
        worker_logger = logging.getLogger(logger_name)
        for existing_handler in tuple(worker_logger.handlers):
            if isinstance(existing_handler, _CodeLensFileHandler):
                worker_logger.removeHandler(existing_handler)
                existing_handler.close()
        worker_logger.propagate = True
        if worker_handler is not None:
            worker_logger.addHandler(worker_handler)
            worker_logger.propagate = False
        worker_logger.disabled = False
        worker_logger.setLevel(logging.DEBUG)

    for logger_name in ("codelens", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.disabled = False
        logger.setLevel(logging.DEBUG)
        if logger_name != "codelens":
            logger.propagate = True

    # Alembic's fileConfig(disable_existing_loggers=True) disables all codelens.*
    # child loggers created before migration. Re-enable them so scheduler and
    # executor tracebacks survive the migration round-trip.
    for name, obj in logging.Logger.manager.loggerDict.items():
        if name.startswith("codelens.") and isinstance(obj, logging.Logger):
            obj.disabled = False
    return log_path
