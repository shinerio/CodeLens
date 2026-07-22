"""Bounded, credential-safe execution transcripts for one Review task."""

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

TranscriptKind = Literal[
    "lifecycle",
    "prompt",
    "model_output",
    "tool_call",
    "tool_result",
    "skill_loaded",
    "model_started",
    "model_reasoning_delta",
    "model_reasoning_completed",
    "model_output_delta",
    "model_output_completed",
    "model_completed",
]

_SECRET_PATTERN = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer\s+|(?:api[_-]?key|bearer|cookie|token)\s*[:=]\s*)[^\s,\"}]+"
)


class TranscriptEntry(BaseModel):
    """One safe-to-display execution message in chronological task order."""

    sequence: int = Field(ge=1)
    kind: TranscriptKind
    content: str
    created_at: datetime
    redacted: bool
    truncated: bool
    metadata: dict[str, str] = Field(default_factory=dict)


class ExecutionTranscriptStore:
    """Append and read transcript entries without placing model content in logs/events.

    Files are task-scoped and atomically replaced through unique, same-directory temporary
    files, so stale interrupted writes cannot block a Worker. Credential-like substrings are
    removed before writing. Entries are deliberately lossless: console collapse is
    presentation-only.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._locks: dict[str, asyncio.Lock] = {}

    async def append(
        self,
        task_id: str,
        kind: TranscriptKind,
        content: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Sanitize and append an entry without exposing content through logging."""

        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            entries = await self.list(task_id)
            safe_content, redacted = _redact(content)
            entry = TranscriptEntry(
                sequence=len(entries) + 1,
                kind=kind,
                content=safe_content,
                created_at=datetime.now(UTC),
                redacted=redacted,
                truncated=False,
                metadata=dict(metadata or {}),
            )
            await asyncio.to_thread(self._write, task_id, [*entries, entry])

    async def list(self, task_id: str) -> tuple[TranscriptEntry, ...]:
        """Return validated transcript entries, or an empty transcript for older tasks."""

        return await asyncio.to_thread(self._read, task_id)

    def _path(self, task_id: str) -> Path:
        if not re.fullmatch(r"review[-_][a-zA-Z0-9-]{1,120}", task_id):
            raise ValueError("invalid transcript task ID")
        return self._root / f"{task_id}.json"

    def _read(self, task_id: str) -> tuple[TranscriptEntry, ...]:
        path = self._path(task_id)
        if not path.exists():
            return ()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return tuple(TranscriptEntry.model_validate(item) for item in payload)

    def _write(self, task_id: str, entries: Sequence[TranscriptEntry]) -> None:
        path = self._path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump([entry.model_dump(mode="json") for entry in entries], handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise


def _redact(content: str) -> tuple[str, bool]:
    """Remove credential-like values while retaining enough text for diagnosis."""

    safe, count = _SECRET_PATTERN.subn("[REDACTED_CREDENTIAL]", content)
    return safe, count > 0
