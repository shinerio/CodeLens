# ADR 0003: Review Execution Transcript

## Status

Accepted

## Context

Local operators need to inspect a Review's actual execution, including rendered model input, model output, tool calls, and Skill lifecycle. The former policy prohibited complete model payloads everywhere, which prevented diagnosis of failed reviews.

## Decision

Each Review may persist an ordered execution transcript as task-scoped Artifact content. Entries use a stable typed schema for Prompt, ModelOutput, ToolCall, ToolResult, SkillLoaded, and lifecycle events. The HTTP API returns only this validated transcript; the frontend renders it as a conversation timeline.

Credentials remain prohibited. Before persistence, the recorder must redact API keys, bearer tokens, cookies, authorization headers, and provider configuration. Every entry records whether content was redacted or truncated. Transcript records are not emitted to process logs or SSE event payloads.

## Consequences

The Artifact Store becomes responsible for bounded transcript retention and task deletion cleanup. Review detail APIs and UI gain an audit view. New model/tool integrations must emit typed transcript entries through the recorder port rather than exposing vendor objects or raw provider diagnostics.
