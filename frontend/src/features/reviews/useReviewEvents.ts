import { useEffect, useRef, useState } from "react";

type ReviewStatus =
  | "loading"
  | "created"
  | "provisioning_worktree"
  | "snapshotting"
  | "preparing"
  | "reviewing"
  | "validating"
  | "synthesizing"
  | "completed"
  | "partial"
  | "failed"
  | "canceled";

export type ReviewStreamEvent = {
  id: string;
  type: string;
  payload: Record<string, unknown>;
};

export type ConnectionState = "connecting" | "open" | "closed";

const STATUS_BY_EVENT: Record<string, ReviewStatus> = {
  "review.created": "created",
  "review.provisioning_worktree": "provisioning_worktree",
  "review.snapshotting": "snapshotting",
  "review.preparing": "preparing",
  "review.reviewing": "reviewing",
  "review.validating": "validating",
  "review.synthesizing": "synthesizing",
  "review.completed": "completed",
  "review.partial": "partial",
  "review.failed": "failed",
  "review.canceled": "canceled",
};

const TERMINAL_STATUSES = new Set<ReviewStatus>([
  "completed",
  "partial",
  "failed",
  "canceled",
]);

export function useReviewEvents(taskId: string | undefined) {
  const [status, setStatus] = useState<ReviewStatus>("loading");
  const [events, setEvents] = useState<ReviewStreamEvent[]>([]);
  const [connectionState, setConnectionState] = useState<ConnectionState>("closed");
  const lastEventIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (taskId === undefined) {
      setStatus("loading");
      setEvents([]);
      setConnectionState("closed");
      lastEventIdRef.current = null;
      return;
    }

    const source = new EventSource(`/api/reviews/${taskId}/events`);
    setConnectionState("connecting");
    setConnectionState("open");
    const eventTypes = Object.keys(STATUS_BY_EVENT);
    const listeners = eventTypes.map((type) => {
      const listener = (event: MessageEvent<string>) => {
        const payload = JSON.parse(event.data) as Record<string, unknown>;
        const eventId = event.lastEventId || lastEventIdRef.current || "0";
        lastEventIdRef.current = event.lastEventId || lastEventIdRef.current;
        setEvents((current) => [...current, { id: eventId, type, payload }]);
        const nextStatus = STATUS_BY_EVENT[type];
        if (nextStatus !== undefined) {
          setStatus(nextStatus);
          if (TERMINAL_STATUSES.has(nextStatus)) {
            source.close();
            setConnectionState("closed");
          }
        }
      };
      source.addEventListener(type, listener);
      return { listener, type };
    });

    return () => {
      for (const { listener, type } of listeners) {
        source.removeEventListener(type, listener);
      }
      source.close();
      setConnectionState("closed");
    };
  }, [taskId]);

  return { status, events, connectionState };
}
