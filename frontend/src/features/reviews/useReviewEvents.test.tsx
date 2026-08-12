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
    "review.planning",
    "agent_run.started",
    "agent.succeeded",
    "finding.published",
    "agent_tool_call.rejected",
    "review.verdict_completed",
  ];

  act(() => {
    types.forEach((type, index) => FakeEventSource.latest?.emit(type, {}, String(index + 1)));
  });

  expect(result.current.events.map((event) => event.type)).toEqual(types);
});
