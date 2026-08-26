"""Lossless, credential-safe execution transcripts for one Review task."""

import asyncio
import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from codelens.review.domain.tool_invocation import classify_tool_result, outcome_metadata

TranscriptKind = Literal[
    "lifecycle",
    "prompt",
    "model_output",
    "tool_call",
    "invalid_tool_call",
    "invalid_tool_result",
    "tool_result",
    "skill_loaded",
    "model_started",
    "model_reasoning_delta",
    "model_reasoning_completed",
    "model_output_delta",
    "model_output_completed",
    "model_completed",
    "model_raw_output",
    "checkpoint_compaction",
]
_LOGGER = logging.getLogger("codelens.worker.transcripts")

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


class ToolRejectionEventPort(Protocol):
    """Persist bounded rejected-tool diagnostics without arguments or result content."""

    async def append(self, task_id: str, event_type: str, payload: dict[str, object]) -> None: ...


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

    def evict_locks(self, active_task_ids: set[str]) -> int:
        """Remove per-task locks for tasks no longer active; returns count removed.

        The durable store has no lifecycle hook to drop locks after a task
        finalises (``_write`` is called by many paths), so locks accumulate
        across the Worker process lifetime. This method is invoked by the memory
        guard to prune locks for tasks that are no longer active.
        """

        stale = [tid for tid in self._locks if tid not in active_task_ids]
        for tid in stale:
            self._locks.pop(tid, None)
        return len(stale)

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
    """Keep active transcripts in memory and persist them at task completion.

    Each model call produces one ``model_output_delta`` (or ``model_reasoning_delta``)
    entry with the full text — token-level chunking is handled upstream in
    ``openai_runtime._visible_event``. This store simply appends entries in arrival
    order and flushes them to the durable store on ``finalize()``.
    """

    def __init__(
        self,
        durable_store: ExecutionTranscriptStore,
        model_log: ModelTranscriptLogPort | None = None,
        rejection_events: ToolRejectionEventPort | None = None,
    ) -> None:
        self._durable_store = durable_store
        self._model_log = model_log
        self._rejection_events = rejection_events
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
        self,
        task_id: str,
        entries: Sequence[tuple[TranscriptKind, str, Mapping[str, str] | None]],
    ) -> None:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            collected = self._entries.setdefault(task_id, [])
            for kind, content, metadata in entries:
                safe_content, redacted = _redact(content)
                safe_metadata = dict(metadata or {})
                if kind == "tool_result" and "tool_outcome" not in safe_metadata:
                    safe_metadata.update(outcome_metadata(classify_tool_result(safe_content)))
                collected.append(
                    _transcript_entry(
                        sequence=len(collected) + 1,
                        kind=kind,
                        content=safe_content,
                        redacted=redacted,
                        metadata=safe_metadata,
                    )
                )
                if kind == "tool_result" and safe_metadata.get("tool_outcome") == "rejected":
                    await self._record_tool_rejection(task_id, safe_metadata)

    async def _record_tool_rejection(
        self, task_id: str, metadata: Mapping[str, str]
    ) -> None:
        """Write one bounded rejection fact to logs and the durable event outbox."""

        payload: dict[str, object] = {
            "agent": metadata.get("agent", "unknown")[:128],
            "tool_name": metadata.get("tool_name", "unknown")[:128],
            "tool_call_id": metadata.get("tool_call_id", "")[:128],
            "reason_code": metadata.get(
                "tool_rejection_reason_code", "tool_result_rejected"
            )[:128],
            "reason": metadata.get(
                "tool_rejection_reason", "Tool invocation was rejected."
            )[:500],
        }
        _LOGGER.warning(
            "Model-visible tool invocation rejected",
            extra={"task_id": task_id, **payload},
        )
        if self._rejection_events is not None:
            await self._rejection_events.append(
                task_id,
                "agent_tool_call.rejected.v2",
                payload,
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
                if self._model_log is not None:
                    await self._model_log.write(task_id, entries)
        # Drop the per-task lock so finalised task IDs do not accumulate forever.
        self._locks.pop(task_id, None)

    async def evict_inactive(self, active_task_ids: set[str]) -> int:
        """Flush and drop in-memory transcripts for tasks no longer active.

        Used by the memory guard to reclaim memory held by transcripts whose
        tasks crashed or were cancelled without reaching ``finalize``. Entries
        are persisted to the durable store (best-effort) before being dropped so
        diagnostic data is not lost. Returns the number of task IDs evicted.
        """

        evicted = 0
        for task_id in list(self._entries.keys()):
            if task_id in active_task_ids:
                continue
            entries = self._entries.pop(task_id, [])
            if entries:
                try:
                    await self._durable_store.replace(task_id, entries)
                except Exception:
                    _LOGGER.exception(
                        "Failed to flush evicted transcript to durable store",
                        extra={"task_id": task_id},
                    )
            self._locks.pop(task_id, None)
            evicted += 1
        return evicted


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
