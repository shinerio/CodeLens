# ADR 0005: Live Transcript Relay and Terminal Persistence

## Status

Accepted

## Context

Polling a growing Artifact file during model streaming caused repeated disk reads and writes. The API and Worker are intentionally independent processes, so an API route cannot directly inspect Worker memory.

## Decision

The Worker keeps each active task transcript in memory and exposes validated, credential-redacted snapshots through a local Unix Socket query interface. The API queries that interface only for running tasks; the query is bounded and failure does not affect model execution or require Worker-first startup.

Once a review reaches a terminal state, the Worker atomically writes the complete transcript to the existing task Artifact and immediately removes its in-memory copy. Subsequent HTTP reads use the durable Artifact. A Worker restart can lose only the transient display copy; review state, artifacts and final transcripts remain governed by the normal durable workflow.

## Consequences

The API and Worker remain separately startable and do not share a Python object or database polling protocol. The deployment now requires a local filesystem that supports Unix-domain sockets. Transcripts remain credential-redacted before both relay and persistence, and no transcript content is sent to logs or the event outbox.
