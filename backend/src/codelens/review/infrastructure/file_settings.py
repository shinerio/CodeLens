"""Atomic JSON persistence for Review completion and trigger idempotency settings."""

import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict, cast

from codelens.review.application.settings import (
    ReviewCompletionSettings,
    TriggerIdempotencySettings,
)


class _ReviewCompletionSettingsPayload(TypedDict):
    max_incomplete_review_retries: int


class _TriggerIdempotencySettingsPayload(TypedDict):
    enabled: bool


class FilesystemReviewCompletionSettingsStore:
    """Store Review completion settings in the local CodeLens data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "review-completion.json"

    def get_settings(self) -> ReviewCompletionSettings:
        """Load persisted settings, using product defaults before first save."""

        if not self._path.exists():
            return ReviewCompletionSettings()
        raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("review completion settings are invalid")
        payload = cast(dict[object, object], raw)
        value = payload.get("max_incomplete_review_retries")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("review completion settings are invalid")
        return ReviewCompletionSettings(max_incomplete_review_retries=value)

    def save_settings(self, settings: ReviewCompletionSettings) -> None:
        """Write a complete settings document and atomically replace the old one."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".review-completion-",
            suffix=".tmp",
        )
        temporary = Path(name)
        payload: _ReviewCompletionSettingsPayload = {
            "max_incomplete_review_retries": settings.max_incomplete_review_retries
        }
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)


class FilesystemTriggerIdempotencySettingsStore:
    """Store trigger idempotency settings in the local CodeLens data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "trigger-idempotency.json"

    def get_settings(self) -> TriggerIdempotencySettings:
        """Load persisted settings, using product defaults before first save."""

        if not self._path.exists():
            return TriggerIdempotencySettings()
        raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("trigger idempotency settings are invalid")
        payload = cast(dict[object, object], raw)
        value = payload.get("enabled")
        if not isinstance(value, bool):
            raise ValueError("trigger idempotency settings are invalid")
        return TriggerIdempotencySettings(enabled=value)

    def save_settings(self, settings: TriggerIdempotencySettings) -> None:
        """Write a complete settings document and atomically replace the old one."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".trigger-idempotency-",
            suffix=".tmp",
        )
        temporary = Path(name)
        payload: _TriggerIdempotencySettingsPayload = {"enabled": settings.enabled}
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
