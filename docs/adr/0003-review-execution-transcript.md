# ADR 0003: Review Execution Transcript

## Status

Accepted

## Context

Local operators need to inspect a Review's actual execution, including rendered model input, model output, tool calls, and Skill lifecycle. The former policy prohibited complete model payloads everywhere, which prevented diagnosis of failed reviews.

## Decision

Each Review persists an ordered, lossless execution transcript as task-scoped Artifact content. Entries use a stable typed schema for Prompt, ModelStarted, ModelOutputDelta, ModelCompleted, ToolCall, ToolResult, SkillLoaded, and lifecycle events. Model-visible output is appended as it arrives, so a reconnect can restore the exact emitted text. The HTTP API returns the validated transcript and the frontend renders it as a collapsible conversation console.

Credentials remain prohibited. Before persistence, the recorder must redact API keys, bearer tokens, cookies, authorization headers, and provider configuration. Every entry records whether content was redacted; content is not truncated. A task-level storage quota must fail explicitly rather than discarding content. Transcript records are not emitted to process logs; live delivery is a resumable view of the durable ordered transcript.

## Consequences

The Artifact Store becomes responsible for task-level quota enforcement, transcript retention, and task deletion cleanup. Review detail APIs and UI gain a full execution console. New model/tool integrations must emit typed transcript entries through the recorder port rather than exposing vendor objects or raw provider diagnostics.
