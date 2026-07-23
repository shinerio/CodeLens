"""Bounded, credential-safe execution transcripts for one Review task."""

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
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


class LiveTranscriptCache:
    """Own API-process transient transcripts received from the local Worker.

    This cache is intentionally non-durable.  It exists solely to serve a running
    review without repeatedly rewriting or reading its Artifact file.  The Worker
    sends complete snapshots over a local Unix socket and flushes the final snapshot
    to ``ExecutionTranscriptStore`` before marking the review terminal.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[TranscriptEntry, ...]] = {}
        self._lock = asyncio.Lock()

    async def replace(self, task_id: str, entries: Sequence[TranscriptEntry]) -> None:
        async with self._lock:
            self._entries[task_id] = tuple(entries)

    async def get(self, task_id: str) -> tuple[TranscriptEntry, ...] | None:
        async with self._lock:
            return self._entries.get(task_id)

    async def remove(self, task_id: str) -> None:
        async with self._lock:
            self._entries.pop(task_id, None)


class UnixTranscriptRelayServer:
    """Receive local Worker snapshots without making API and Worker co-dependent.

    Every connection transports exactly one validated JSON snapshot.  Bad or partial
    messages are discarded; a Worker cannot be blocked by an unavailable API relay.
    """

    def __init__(self, socket_path: Path, cache: LiveTranscriptCache) -> None:
        self._socket_path = socket_path
        self._cache = cache
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        socket_path = _unix_socket_path(self._socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(socket_path))

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        _unix_socket_path(self._socket_path).unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            payload = json.loads((await reader.read(8 * 1024 * 1024)).decode("utf-8"))
            task_id = payload["task_id"]
            if not isinstance(task_id, str) or not re.fullmatch(
                r"review[-_][a-zA-Z0-9-]{1,120}", task_id
            ):
                return
            if payload.get("completed") is True:
                await self._cache.remove(task_id)
            else:
                entries = tuple(TranscriptEntry.model_validate(item) for item in payload["entries"])
                await self._cache.replace(task_id, entries)
            writer.write(b"1")
            await writer.drain()
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return
        finally:
            writer.close()
            await writer.wait_closed()


class DeferredTranscriptStore:
    """Keep one Worker's transcript in memory and persist it only when finalized.

    Snapshots are relayed best-effort to the API once per ``publish_interval``.  Relay
    failure is intentionally ignored: the review workflow must remain independent of
    API startup order.  ``finalize`` performs the single durable Artifact write.
    """

    def __init__(
        self,
        durable_store: ExecutionTranscriptStore,
        socket_path: Path,
        *,
        publish_interval: float = 1.0,
    ) -> None:
        self._durable_store = durable_store
        self._socket_path = socket_path
        self._publish_interval = publish_interval
        self._entries: dict[str, list[TranscriptEntry]] = {}
        self._last_published: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._scheduled_flushes: dict[str, asyncio.Task[None]] = {}

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
        if not entries:
            return
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
            if time.monotonic() - self._last_published.get(task_id, 0.0) >= self._publish_interval:
                await self._publish(task_id, collected)
                self._last_published[task_id] = time.monotonic()
            elif task_id not in self._scheduled_flushes:
                delay = self._publish_interval - (
                    time.monotonic() - self._last_published[task_id]
                )
                self._scheduled_flushes[task_id] = asyncio.create_task(
                    self._flush_after_delay(task_id, delay)
                )

    async def finalize(self, task_id: str) -> None:
        """Write the complete transcript once and remove its transient API copy."""

        lock = self._locks.setdefault(task_id, asyncio.Lock())
        scheduled_flush = self._scheduled_flushes.pop(task_id, None)
        if scheduled_flush is not None:
            scheduled_flush.cancel()
        async with lock:
            entries = self._entries.pop(task_id, [])
            if entries:
                await self._durable_store.replace(task_id, entries)
            await self._send({"task_id": task_id, "completed": True})
            self._last_published.pop(task_id, None)

    async def _flush_after_delay(self, task_id: str, delay: float) -> None:
        """Publish a quiet stream's accumulated entries at the one-second deadline."""

        try:
            await asyncio.sleep(max(delay, 0.0))
            lock = self._locks.setdefault(task_id, asyncio.Lock())
            async with lock:
                entries = self._entries.get(task_id)
                if entries:
                    await self._publish(task_id, entries)
                    self._last_published[task_id] = time.monotonic()
        finally:
            self._scheduled_flushes.pop(task_id, None)

    async def _publish(self, task_id: str, entries: Sequence[TranscriptEntry]) -> None:
        await self._send(
            {"task_id": task_id, "entries": [item.model_dump(mode="json") for item in entries]}
        )

    async def _send(self, payload: dict[str, object]) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(_unix_socket_path(self._socket_path))), timeout=0.2
            )
            writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=0.2)
            if writer.can_write_eof():
                writer.write_eof()
                await asyncio.wait_for(writer.drain(), timeout=0.2)
            await asyncio.wait_for(reader.readexactly(1), timeout=0.2)
            writer.close()
            await writer.wait_closed()
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            return


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


class UnixWorkerTranscriptQueryServer:
    """Expose only a Worker's active in-memory transcript through a local socket."""

    def __init__(self, socket_path: Path, transcripts: WorkerTranscriptStore) -> None:
        self._socket_path = socket_path
        self._transcripts = transcripts
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        socket_path = _unix_socket_path(self._socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(socket_path))

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        _unix_socket_path(self._socket_path).unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            payload = json.loads((await reader.read(4096)).decode("utf-8"))
            task_id = payload.get("task_id")
            if isinstance(task_id, str):
                entries = await self._transcripts.list(task_id)
                response = json.dumps([entry.model_dump(mode="json") for entry in entries]).encode()
                writer.write(response)
                await writer.drain()
        except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError):
            return
        finally:
            writer.close()
            await writer.wait_closed()


class UnixWorkerTranscriptQueryClient:
    """Query the independently started Worker without making API requests block on it."""

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

    async def list(self, task_id: str) -> tuple[TranscriptEntry, ...]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(_unix_socket_path(self._socket_path))), timeout=0.2
            )
            writer.write(json.dumps({"task_id": task_id}).encode())
            writer.write_eof()
            await asyncio.wait_for(writer.drain(), timeout=0.2)
            raw = await asyncio.wait_for(reader.read(8 * 1024 * 1024), timeout=0.2)
            writer.close()
            await writer.wait_closed()
            return tuple(TranscriptEntry.model_validate(item) for item in json.loads(raw))
        except (
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            json.JSONDecodeError,
            ValueError,
        ):
            return ()


def _redact(content: str) -> tuple[str, bool]:
    """Remove credential-like values while retaining enough text for diagnosis."""

    safe, count = _SECRET_PATTERN.subn("[REDACTED_CREDENTIAL]", content)
    return safe, count > 0


def _unix_socket_path(requested_path: Path) -> Path:
    """Keep local socket paths below the platform AF_UNIX pathname limit."""

    normalized = requested_path.expanduser().resolve()
    if len(str(normalized).encode()) <= 90:
        return normalized
    digest = hashlib.sha256(str(normalized).encode()).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / f"codelens-{digest}.sock"
