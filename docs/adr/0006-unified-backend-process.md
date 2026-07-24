# ADR 0006: Unified Backend Process with In-Memory Event Bus

## Status

Accepted

## Context

The original architecture ran API and Worker as two independent OS processes, communicating through SQLite polling and Unix Socket IPC. This created an unstable event streaming chain to the frontend:

1. SSE endpoint polled SQLite every 0.1s for new events
2. Transcripts were relayed through Unix Socket fire-and-forget mechanism
3. Frontend polled the transcript REST endpoint every 1s

This three-layer relay chain caused data loss and output interruption, particularly during high-frequency model streaming events.

## Decision

Merge API and Worker into a single unified backend process with shared in-memory infrastructure:

1. **In-Memory Event Bus**: Replace SQLite polling with an `InMemoryEventBus` that publishes events to per-task subscriber queues immediately after database commit. SSE endpoints subscribe to the bus for real-time delivery, with database replay for catch-up on reconnection.

2. **Shared Transcript Store**: Replace Unix Socket IPC with direct `WorkerTranscriptStore` references. Transcripts remain in memory during execution and are persisted to task artifacts only after review completion.

3. **Unified Process**: API and Worker run in the same asyncio event loop, sharing the database connection pool, event bus, and transcript store. The `start` command launches the unified backend; `api` and `worker` commands are deprecated but retained for backward compatibility.

## Consequences

**Positive:**
- Event streaming is now stable and real-time with no polling delay
- Simplified architecture eliminates Unix Socket complexity and IPC failure modes
- Single process reduces resource overhead and simplifies deployment
- Transcripts are immediately available to API without socket relay latency

**Negative:**
- API and Worker can no longer be started independently (though deprecated commands remain)
- Process failure affects both API and Worker simultaneously (mitigated by existing restart recovery)

**Neutral:**
- HTTP/SSE contracts remain unchanged; frontend requires no modifications
- Database schema and event outbox remain the source of truth for durability
- Credential redaction and security boundaries are preserved
