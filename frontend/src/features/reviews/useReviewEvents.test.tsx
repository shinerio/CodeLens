import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { FakeEventSource } from "../../test/FakeEventSource";
import { useReviewEvents } from "./useReviewEvents";

beforeEach(() => {
  FakeEventSource.latest = undefined;
  vi.stubGlobal("EventSource", FakeEventSource);
});

it("receives persisted lifecycle, agent, finding, rejection, and verdict event types", () => {
  const { result } = renderHook(() => useReviewEvents("review-1"));
  const types = [
    "review.planning.v2",
    "agent_run.started.v2",
    "agent.succeeded.v2",
    "finding.published.v2",
    "agent_tool_call.rejected.v2",
    "review.verdict_completed.v2",
  ];

  act(() => {
    types.forEach((type, index) => FakeEventSource.latest?.emit(type, {}, String(index + 1)));
  });

  expect(result.current.events.map((event) => event.type)).toEqual(types);
});
