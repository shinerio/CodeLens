"""Terminal, lossless model exchange logging for local prompt diagnostics."""

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from codelens.bootstrap.logging import get_model_output_logging_enabled
from codelens.review.infrastructure.transcripts import TranscriptEntry

_LOGGER = logging.getLogger("codelens.model")
_LOGGER.addHandler(logging.NullHandler())
_LOGGER.propagate = False

_MODEL_LOG_KINDS = frozenset(
    {
        "prompt",
        "model_raw_output",
        "checkpoint_compaction",
        "tool_call",
        "invalid_tool_call",
        "tool_result",
        "model_output",
    }
)


class ModelTranscriptLogWriter:
    """Write complete, already-redacted model exchanges after transcript finalization.

    Stream deltas are deliberately excluded because each provider raw response is recorded once
    after the model run returns. Writes run off the event loop and use the dedicated model logger,
    whose bounded compressed handler is installed by the process bootstrap.
    """

    def __init__(
        self,
        data_directory: Path,
        *,
        default_enabled: bool = True,
    ) -> None:
        self._data_directory = data_directory
        self._default_enabled = default_enabled

    async def write(self, task_id: str, entries: Sequence[TranscriptEntry]) -> None:
        """Write model-relevant terminal transcript entries without truncating their content."""

        enabled = get_model_output_logging_enabled(
            self._data_directory,
            default_enabled=self._default_enabled,
        )
        records = tuple(entry for entry in entries if entry.kind in _MODEL_LOG_KINDS)
        if enabled and records:
            await asyncio.to_thread(self._write, task_id, records)

    @staticmethod
    def _write(task_id: str, entries: Sequence[TranscriptEntry]) -> None:
        for entry in entries:
            _LOGGER.info(
                "model transcript entry",
                extra={
                    "task_id": task_id,
                    "sequence": entry.sequence,
                    "event_kind": entry.kind,
                    "content": entry.content,
                    "metadata": entry.metadata,
                    "redacted": entry.redacted,
                    "created_at": entry.created_at.isoformat(),
                },
            )
