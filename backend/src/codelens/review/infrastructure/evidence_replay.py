"""Run-scoped allowances for exact rereads of compacted evidence tool results."""

from __future__ import annotations

import json
from collections import Counter
from threading import Lock

EVIDENCE_TOOL_NAMES = frozenset({"find_files", "grep", "read_file", "get_diff"})


def canonicalize_tool_arguments(arguments: str) -> str:
    """Canonicalize JSON arguments identically for compaction and loop detection."""

    try:
        parsed: object = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    return json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class CompactedEvidenceReplayRegistry:
    """Atomically register and consume one allowance per newly compacted call ID."""

    def __init__(self) -> None:
        self._allowances: Counter[tuple[str, str]] = Counter()
        self._registered_call_ids: set[str] = set()
        self._registered_count = 0
        self._consumed_count = 0
        self._lock = Lock()

    def register(self, call_id: str, tool_name: str, arguments: str) -> bool:
        """Register one allowance once for an original evidence call ID."""

        if tool_name not in EVIDENCE_TOOL_NAMES:
            return False
        key = (tool_name, canonicalize_tool_arguments(arguments))
        with self._lock:
            if call_id in self._registered_call_ids:
                return False
            self._registered_call_ids.add(call_id)
            self._allowances[key] += 1
            self._registered_count += 1
            return True

    def consume(self, tool_name: str, arguments: str) -> bool:
        """Consume one exact evidence replay allowance if available."""

        if tool_name not in EVIDENCE_TOOL_NAMES:
            return False
        key = (tool_name, canonicalize_tool_arguments(arguments))
        with self._lock:
            if self._allowances[key] <= 0:
                return False
            self._allowances[key] -= 1
            self._consumed_count += 1
            return True

    @property
    def registered_count(self) -> int:
        """Return the number of unique original calls that registered allowances."""

        with self._lock:
            return self._registered_count

    @property
    def consumed_count(self) -> int:
        """Return the number of exact rereads that consumed allowances."""

        with self._lock:
            return self._consumed_count


class ToolLoopResetSignal:
    """Increment a generation counter after each successful context compaction.

    The ToolExecutionLimiter observes the generation and resets its duplicate-call
    counters when it changes, so that re-reading evidence after compaction is not
    flagged as a no-progress loop. Accessed only within one agent run's event loop.
    """

    def __init__(self) -> None:
        self._generation = 0

    def trigger(self) -> None:
        """Advance the generation after one successful checkpoint compaction."""

        self._generation += 1

    @property
    def generation(self) -> int:
        """Return the current generation counter."""

        return self._generation
