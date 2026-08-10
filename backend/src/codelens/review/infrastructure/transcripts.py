"""Lossless, credential-safe execution transcripts for one Review task."""

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

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
StreamingTranscriptKind = Literal["model_reasoning_delta", "model_output_delta"]
_STREAMING_TRANSCRIPT_KINDS: frozenset[str] = frozenset(
    {"model_reasoning_delta", "model_output_delta"}
)

_SECRET_PATTERN = re.compile(
    r"(?i)(?P<prefix>"
    r"[\"']?authorization[\"']?\s*:\s*[\"']?\s*(?:(?:bearer|basic)\s+)?|"
    r"[\"']?(?:api[_-]?key|bearer|cookie|token)[\"']?\s*[:=]\s*[\"']?"
    r")(?P<secret>[^\s,\"'}]+)"
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


class ModelTranscriptLogPort(Protocol):
    """Write complete sanitized model exchanges when a task transcript becomes terminal."""

    async def write(self, task_id: str, entries: Sequence[TranscriptEntry]) -> None:
        """Persist model exchanges outside the streaming event path."""

        raise NotImplementedError


class ExecutionTranscriptStore:
    """Append and read complete transcript entries while removing credential values.

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
        """Sanitize and append one complete entry without truncation."""

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
    """Keep active transcripts in memory and persist logical model events at completion.

    Provider token deltas are merged per Agent, stream kind, and message identity. Events
    from concurrent Agents do not split an active response; the next non-stream event from
    that same Agent closes its response boundary. This preserves complete visible content
    without persisting one transcript entry per provider token.
    """

    def __init__(
        self,
        durable_store: ExecutionTranscriptStore,
        model_log: ModelTranscriptLogPort | None = None,
    ) -> None:
        self._durable_store = durable_store
        self._model_log = model_log
        self._entries: dict[str, list[TranscriptEntry]] = {}
        self._active_streams: dict[
            str,
            dict[
                tuple[str, StreamingTranscriptKind],
                tuple[str | None, int],
            ],
        ] = {}
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
        self,
        task_id: str,
        entries: Sequence[tuple[TranscriptKind, str, Mapping[str, str] | None]],
    ) -> None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            collected = self._entries.setdefault(task_id, [])
            active_streams = self._active_streams.setdefault(task_id, {})
            for kind, content, metadata in entries:
                safe_content, redacted = _redact(content)
                safe_metadata = dict(metadata or {})
                agent = safe_metadata.get("agent", "<global>")
                if kind in _STREAMING_TRANSCRIPT_KINDS:
                    streaming_kind = _streaming_kind(kind)
                    stream_key = (agent, streaming_kind)
                    message_id = safe_metadata.get("message_id")
                    active_stream = active_streams.get(stream_key)
                    if active_stream is not None and active_stream[0] == message_id:
                        entry_index = active_stream[1]
                        previous = collected[entry_index]
                        collected[entry_index] = previous.model_copy(
                            update={
                                "content": previous.content + safe_content,
                                "redacted": previous.redacted or redacted,
                            }
                        )
                        continue
                    collected.append(
                        _transcript_entry(
                            sequence=len(collected) + 1,
                            kind=kind,
                            content=safe_content,
                            redacted=redacted,
                            metadata=safe_metadata,
                        )
                    )
                    active_streams[stream_key] = (message_id, len(collected) - 1)
                    continue

                for stream_key in tuple(active_streams):
                    if stream_key[0] == agent:
                        del active_streams[stream_key]
                collected.append(
                    _transcript_entry(
                        sequence=len(collected) + 1,
                        kind=kind,
                        content=safe_content,
                        redacted=redacted,
                        metadata=safe_metadata,
                    )
                )

    async def list(self, task_id: str) -> tuple[TranscriptEntry, ...]:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            return tuple(self._entries.get(task_id, ()))

    async def finalize(self, task_id: str) -> None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            entries = self._entries.pop(task_id, [])
            self._active_streams.pop(task_id, None)
            if entries:
                await self._durable_store.replace(task_id, entries)
                if self._model_log is not None:
                    await self._model_log.write(task_id, entries)


def _streaming_kind(kind: TranscriptKind) -> StreamingTranscriptKind:
    if kind == "model_reasoning_delta" or kind == "model_output_delta":
        return kind
    raise ValueError(f"Transcript kind is not a streaming delta: {kind}")


def _transcript_entry(
    *,
    sequence: int,
    kind: TranscriptKind,
    content: str,
    redacted: bool,
    metadata: dict[str, str],
) -> TranscriptEntry:
    return TranscriptEntry(
        sequence=sequence,
        kind=kind,
        content=content,
        created_at=datetime.now(UTC),
        redacted=redacted,
        truncated=False,
        metadata=metadata,
    )


def _redact(content: str) -> tuple[str, bool]:
    """Remove credential-like values while retaining enough text for diagnosis."""

    safe, count = _SECRET_PATTERN.subn(
        lambda match: f"{match.group('prefix')}[REDACTED_CREDENTIAL]",
        content,
    )
    return safe, count > 0
