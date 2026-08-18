"""Verify the tiktoken-backed token counter adapter returns expected counts."""

from codelens.review.domain.canonical_json import canonical_json
from codelens.review.infrastructure.token_counter import TiktokenCounterAdapter


def test_tiktoken_counter_known_string() -> None:
    """``TiktokenCounterAdapter`` returns expected counts for known strings."""

    counter = TiktokenCounterAdapter()

    assert counter.count("hello world") == 2


def test_tiktoken_counter_chinese_text() -> None:
    """Chinese characters encode to a bounded range of tokens under cl100k_base."""

    counter = TiktokenCounterAdapter()

    assert 4 <= counter.count("你好世界") <= 6


def test_tiktoken_counter_count_json_matches_canonical_serialization() -> None:
    """``count_json`` serializes via ``canonical_json`` before counting tokens."""

    counter = TiktokenCounterAdapter()
    value = {"b": 1, "a": "two"}

    assert counter.count_json(value) == counter.count(canonical_json(value))


def test_tiktoken_counter_empty_string_returns_zero() -> None:
    """An empty string encodes to zero tokens."""

    counter = TiktokenCounterAdapter()

    assert counter.count("") == 0
