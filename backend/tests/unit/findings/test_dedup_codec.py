"""Tests for DedupCodec: coverage validation, unknown id rejection, duplicate coverage rejection."""

import json

import pytest

from codelens.findings.domain.dedup import DedupDecision, DedupDecisionSource, DedupOutcome
from codelens.findings.infrastructure.dedup_codec import (
    DedupCodec,
    DedupCodecError,
    ValidatedDedupBatch,
)

_IDS = frozenset({"verdict_aaa", "verdict_bbb"})


def _decision(
    verdict_decision_id: str = "verdict_aaa",
    outcome: DedupOutcome = DedupOutcome.ACCEPT,
) -> DedupDecision:
    return DedupDecision(
        verdict_decision_id=verdict_decision_id,
        outcome=outcome,
        decision_source=DedupDecisionSource.LLM,
    )


def _submission(
    decisions: list[dict[str, str]],
) -> bytes:
    return json.dumps(
        {"schema_version": "1", "decisions": decisions}
    ).encode()


class TestDedupCodecDecode:
    def test_valid_full_coverage_decodes(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        payload = _submission(
            [
                {"verdict_decision_id": "verdict_aaa", "outcome": "accept"},
                {"verdict_decision_id": "verdict_bbb", "outcome": "deny"},
            ]
        )
        decisions = codec.decode(payload)
        assert len(decisions) == 2
        assert decisions[0].verdict_decision_id == "verdict_aaa"
        assert decisions[0].outcome == DedupOutcome.ACCEPT
        assert decisions[0].decision_source == DedupDecisionSource.LLM
        assert decisions[1].verdict_decision_id == "verdict_bbb"
        assert decisions[1].outcome == DedupOutcome.DENY

    def test_unknown_verdict_id_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        payload = _submission(
            [
                {"verdict_decision_id": "verdict_unknown", "outcome": "accept"},
                {"verdict_decision_id": "verdict_bbb", "outcome": "deny"},
            ]
        )
        with pytest.raises(DedupCodecError, match="unknown verdict"):
            codec.decode(payload)

    def test_duplicate_coverage_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        payload = _submission(
            [
                {"verdict_decision_id": "verdict_aaa", "outcome": "accept"},
                {"verdict_decision_id": "verdict_aaa", "outcome": "deny"},
            ]
        )
        with pytest.raises(DedupCodecError, match="multiple dedup decisions"):
            codec.decode(payload)

    def test_missing_coverage_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        payload = _submission(
            [
                {"verdict_decision_id": "verdict_aaa", "outcome": "accept"},
            ]
        )
        with pytest.raises(DedupCodecError, match="does not cover"):
            codec.decode(payload)

    def test_invalid_schema_version_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        payload = json.dumps(
            {"schema_version": "2", "decisions": []}
        ).encode()
        with pytest.raises(DedupCodecError, match="schema is invalid"):
            codec.decode(payload)

    def test_invalid_outcome_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        payload = _submission(
            [
                {"verdict_decision_id": "verdict_aaa", "outcome": "maybe"},
                {"verdict_decision_id": "verdict_bbb", "outcome": "deny"},
            ]
        )
        with pytest.raises(DedupCodecError, match="schema is invalid"):
            codec.decode(payload)


class TestDedupCodecDecodeDecisions:
    def test_valid_decisions_pass_through(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        decisions = (
            _decision("verdict_aaa", DedupOutcome.ACCEPT),
            _decision("verdict_bbb", DedupOutcome.DENY),
        )
        result = codec.decode_decisions(decisions)
        assert result == decisions

    def test_incomplete_coverage_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        decisions = (_decision("verdict_aaa", DedupOutcome.ACCEPT),)
        with pytest.raises(DedupCodecError, match="does not cover"):
            codec.decode_decisions(decisions)


class TestDedupCodecValidateNewIds:
    def test_valid_new_ids_returned(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        result = codec.validate_new_ids(["verdict_aaa"], set())
        assert result == ("verdict_aaa",)

    def test_unknown_id_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        with pytest.raises(DedupCodecError, match="unknown verdict"):
            codec.validate_new_ids(["verdict_unknown"], set())

    def test_already_covered_id_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        with pytest.raises(DedupCodecError, match="already has a dedup decision"):
            codec.validate_new_ids(["verdict_aaa"], {"verdict_aaa"})

    def test_duplicate_ids_in_one_batch_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        with pytest.raises(DedupCodecError, match="duplicate"):
            codec.validate_new_ids(["verdict_aaa", "verdict_aaa"], set())

    def test_empty_ids_rejected(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        with pytest.raises(DedupCodecError, match="at least one"):
            codec.validate_new_ids([], set())


class TestDedupCodecCanonicalBytes:
    def test_round_trip(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        decisions = (
            _decision("verdict_aaa", DedupOutcome.ACCEPT),
            _decision("verdict_bbb", DedupOutcome.DENY),
        )
        canonical = codec.canonical_bytes(decisions)
        decoded = codec.decode(canonical)
        assert decoded == decisions

    def test_deterministic_ordering(self) -> None:
        codec = DedupCodec(expected_ids=_IDS)
        decisions = (
            _decision("verdict_bbb", DedupOutcome.DENY),
            _decision("verdict_aaa", DedupOutcome.ACCEPT),
        )
        canonical = codec.canonical_bytes(decisions)
        payload = json.loads(canonical)
        assert payload["decisions"][0]["verdict_decision_id"] == "verdict_bbb"
        assert payload["decisions"][1]["verdict_decision_id"] == "verdict_aaa"


class TestValidatedDedupBatch:
    def test_carries_decisions(self) -> None:
        decisions = (_decision(),)
        batch = ValidatedDedupBatch(decisions)
        assert batch.decisions == decisions
