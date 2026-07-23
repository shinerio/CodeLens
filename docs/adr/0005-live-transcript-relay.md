# ADR 0005: Live Transcript Relay and Terminal Persistence

## Status

Accepted

## Context

Polling a growing Artifact file during model streaming caused repeated disk reads and writes. The API and Worker are intentionally independent processes, so an API route cannot directly inspect Worker memory.

## Decision

The Worker keeps each active task transcript in memory and sends validated, credential-redacted complete snapshots to the API over a local Unix Socket at a bounded cadence. The API owns an ephemeral cache and serves it while a task is running. Relay transport is best-effort and cannot block model execution or require API-first startup.

Once a review reaches a terminal state, the Worker atomically writes the complete transcript to the existing task Artifact and tells the API to discard its transient entry. Subsequent HTTP reads use the durable Artifact. A Worker or API restart can lose only the transient display copy; review state, artifacts and final transcripts remain governed by the normal durable workflow.

## Consequences

The API and Worker remain separately startable and do not share a Python object or database polling protocol. The deployment now requires a local filesystem that supports Unix-domain sockets. Transcripts remain credential-redacted before both relay and persistence, and no transcript content is sent to logs or the event outbox.
