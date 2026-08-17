"""tiktoken-based token counter adapter for context compaction.

Uses the ``cl100k_base`` encoding, which is exact for OpenAI models (GPT-4,
GPT-4o) and an approximation (within ~10%) for non-OpenAI models such as
GLM, DeepSeek and Qwen. This is a significant improvement over byte-based
counting, which can be off by 50% or more for mixed-language content.

``tiktoken`` encode is a pure-CPU synchronous call that completes in well
under 10 ms for typical conversation tails, so it is safe to invoke inside
the event loop without offloading to a thread.
"""

from __future__ import annotations

import tiktoken

from codelens.review.domain.canonical_json import canonical_json


class TiktokenCounterAdapter:
    """Concrete ``TokenCounterPort`` backed by the local tiktoken encoder."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        return len(self._encoding.encode_ordinary(text))

    def count_json(self, value: object) -> int:
        return self.count(canonical_json(value))
