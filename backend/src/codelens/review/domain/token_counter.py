"""Token counting port for cache-stable context compaction.

Large language model context windows are measured in tokens, not bytes. The
``TokenCounterPort`` abstracts the tokenizer so the Domain layer can describe
compaction triggers in token units while the Infrastructure layer supplies a
concrete implementation (e.g. tiktoken).
"""

from __future__ import annotations

from typing import Protocol

from codelens.review.domain.canonical_json import canonical_json


class TokenCounterPort(Protocol):
    """Count tokens for plain text and structured JSON payloads.

    Implementations must be safe to call synchronously from within an event loop
    and must return a non-negative integer for any input. The chosen tokenizer
    is an approximation for non-OpenAI models; callers should treat the result
    as a planning signal rather than an exact billable figure.
    """

    def count(self, text: str) -> int:
        """Return the token count for a plain-text payload."""
        ...

    def count_json(self, value: object) -> int:
        """Return the token count for ``value`` serialized as canonical JSON."""
        ...


def count_json_via(counter: TokenCounterPort, value: object) -> int:
    """Default helper binding ``count_json`` to ``canonical_json`` + ``count``.

    Concrete adapters can override ``count_json`` directly, but this helper
    lets test doubles and any future adapter share one serialization path.
    """

    return counter.count(canonical_json(value))
