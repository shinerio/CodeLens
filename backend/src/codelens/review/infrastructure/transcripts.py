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
    "model_raw_output",
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

        await self.append_many(task_id, ((kind, content, metadata),))

    async def append_many(
        self,
        task_id: str,
        entries_to_append: Sequence[tuple[TranscriptKind, str, Mapping[str, str] | None]],
    ) -> None:
        """Atomically append one completed model transcript batch with a single file rewrite."""

        if not entries_to_append:
            return
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            entries = await self.list(task_id)
            new_entries = tuple(
                TranscriptEntry(
                    sequence=len(entries) + index,
                    kind=kind,
                    content=safe_content,
                    created_at=datetime.now(UTC),
                    redacted=redacted,
                    truncated=False,
                    metadata=dict(metadata or {}),
                )
                for index, (kind, content, metadata) in enumerate(entries_to_append, start=1)
                for safe_content, redacted in (_redact(content),)
            )
            await asyncio.to_thread(self._write, task_id, [*entries, *new_entries])

    async def list(self, task_id: str) -> tuple[TranscriptEntry, ...]:
        """Return validated transcript entries, or an empty transcript for older tasks."""

        return await asyncio.to_thread(self._read, task_id)

    async def replace(self, task_id: str, entries: Sequence[TranscriptEntry]) -> None:
        """Persist a complete already-sanitized transcript with one atomic replacement."""

        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            await asyncio.to_thread(self._write, task_id, entries)

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


class WorkerTranscriptStore:
    """Keep active Review transcripts in Worker memory and persist only at completion."""

    def __init__(self, durable_store: ExecutionTranscriptStore) -> None:
        self._durable_store = durable_store
        self._entries: dict[str, list[TranscriptEntry]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def append(
        self,
        task_id: str,
        kind: TranscriptKind,
        content: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        await self.append_many(task_id, ((kind, content, metadata),))

    async def append_many(
        self, task_id: str, entries: Sequence[tuple[TranscriptKind, str, Mapping[str, str] | None]],
    ) -> None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            collected = self._entries.setdefault(task_id, [])
            collected.extend(
                TranscriptEntry(
                    sequence=len(collected) + index,
                    kind=kind,
                    content=safe_content,
                    created_at=datetime.now(UTC),
                    redacted=redacted,
                    truncated=False,
                    metadata=dict(metadata or {}),
                )
                for index, (kind, content, metadata) in enumerate(entries, start=1)
                for safe_content, redacted in (_redact(content),)
            )

    async def list(self, task_id: str) -> tuple[TranscriptEntry, ...]:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            return tuple(self._entries.get(task_id, ()))

    async def finalize(self, task_id: str) -> None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            entries = self._entries.pop(task_id, [])
            if entries:
                await self._durable_store.replace(task_id, entries)


def _redact(content: str) -> tuple[str, bool]:
    """Remove credential-like values while retaining enough text for diagnosis."""

    safe, count = _SECRET_PATTERN.subn("[REDACTED_CREDENTIAL]", content)
    return safe, count > 0


