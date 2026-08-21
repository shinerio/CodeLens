"""Tests for DeduplicationCollector and DedupValidator."""

import json

import pytest

from codelens.findings.domain.dedup import DedupDecisionSource, DedupOutcome
from codelens.findings.infrastructure.dedup_codec import DedupCodec, ValidatedDedupBatch
from codelens.review.infrastructure.dedup_tools import (
    DeduplicationCollector,
    DedupValidator,
)

_IDS = frozenset({"verdict_aaa", "verdict_bbb"})


def _codec() -> DedupCodec:
    return DedupCodec(expected_ids=_IDS)


class TestDeduplicationCollectorBatch:
    async def test_batch_accept_accumulates(self) -> None:
        collector = DeduplicationCollector(_codec())
        result = json.loads(await collector.deduplicate(["verdict_aaa"], "accept"))
        assert result["status"] == "success"
        assert result["data"]["accepted_count"] == 1
        assert not collector.is_completed

    async def test_batch_deny_accumulates(self) -> None:
        collector = DeduplicationCollector(_codec())
        result = json.loads(await collector.deduplicate(["verdict_aaa"], "deny"))
        assert result["status"] == "success"
        assert result["data"]["accepted_count"] == 1

    async def test_multiple_batches_accumulate(self) -> None:
        collector = DeduplicationCollector(_codec())
        await collector.deduplicate(["verdict_aaa"], "accept")
        result = json.loads(await collector.deduplicate(["verdict_bbb"], "deny"))
        assert result["data"]["decision_count"] == 2

    async def test_unknown_verdict_rejected(self) -> None:
        collector = DeduplicationCollector(_codec())
        result = json.loads(await collector.deduplicate(["verdict_unknown"], "accept"))
        assert result["status"] == "rejected"
        assert result["data"]["rejected_count"] == 1

    async def test_duplicate_verdict_rejected(self) -> None:
        collector = DeduplicationCollector(_codec())
        await collector.deduplicate(["verdict_aaa"], "accept")
        result = json.loads(await collector.deduplicate(["verdict_aaa"], "deny"))
        assert result["status"] == "rejected"
        assert result["data"]["rejected_count"] == 1

    async def test_partial_batch_when_mixed(self) -> None:
        collector = DeduplicationCollector(_codec())
        result = json.loads(
            await collector.deduplicate(["verdict_aaa", "verdict_unknown"], "accept")
        )
        assert result["status"] == "partial"
        assert result["data"]["accepted_count"] == 1
        assert result["data"]["rejected_count"] == 1


class TestDeduplicationCollectorFinalize:
    async def test_finalize_success_when_complete(self) -> None:
        collector = DeduplicationCollector(_codec())
        await collector.deduplicate(["verdict_aaa"], "accept")
        await collector.deduplicate(["verdict_bbb"], "deny")
        result = json.loads(await collector.finalize())
        assert result["status"] == "success"
        assert result["data"]["dedup_count"] == 2
        assert collector.is_completed

    async def test_finalize_needs_action_when_incomplete(self) -> None:
        collector = DeduplicationCollector(_codec())
        await collector.deduplicate(["verdict_aaa"], "accept")
        result = json.loads(await collector.finalize())
        assert result["status"] == "needs_action"
        assert "verdict_bbb" in result["data"]["missing_verdict_decision_ids"]
        assert not collector.is_completed

    async def test_finalize_after_finalize_rejected(self) -> None:
        collector = DeduplicationCollector(_codec())
        await collector.deduplicate(["verdict_aaa"], "accept")
        await collector.deduplicate(["verdict_bbb"], "deny")
        await collector.finalize()
        result = json.loads(await collector.finalize())
        assert result["status"] == "rejected"

    async def test_deduplicate_after_finalize_rejected(self) -> None:
        collector = DeduplicationCollector(_codec())
        await collector.deduplicate(["verdict_aaa"], "accept")
        await collector.deduplicate(["verdict_bbb"], "deny")
        await collector.finalize()
        result = json.loads(await collector.deduplicate(["verdict_aaa"], "accept"))
        assert result["status"] == "rejected"

    async def test_final_output_returns_decisions(self) -> None:
        collector = DeduplicationCollector(_codec())
        await collector.deduplicate(["verdict_aaa"], "accept")
        await collector.deduplicate(["verdict_bbb"], "deny")
        await collector.finalize()
        decisions = collector.final_output()
        assert len(decisions) == 2
        assert decisions[0].outcome == DedupOutcome.ACCEPT
        assert decisions[1].outcome == DedupOutcome.DENY
        assert all(d.decision_source == DedupDecisionSource.LLM for d in decisions)

    async def test_final_output_before_finalize_raises(self) -> None:
        collector = DeduplicationCollector(_codec())
        with pytest.raises(RuntimeError, match="not finalized"):
            collector.final_output()


class TestDeduplicationCollectorBindings:
    def test_bindings_return_two_tools(self) -> None:
        collector = DeduplicationCollector(_codec())
        bindings = collector.bindings("desc1", "desc2")
        assert len(bindings) == 2
        assert bindings[0].contract.name == "deduplicate"
        assert bindings[1].contract.name == "deduplicate_done"
        assert bindings[1].state is collector


class TestDedupValidator:
    async def test_validate_valid_payload(self) -> None:
        codec = _codec()
        validator = DedupValidator(codec)
        payload = json.dumps(
            {
                "schema_version": "1",
                "decisions": [
                    {"verdict_decision_id": "verdict_aaa", "outcome": "accept"},
                    {"verdict_decision_id": "verdict_bbb", "outcome": "deny"},
                ],
            }
        ).encode()
        result = await validator.validate(payload)
        assert isinstance(result, ValidatedDedupBatch)
        assert len(result.decisions) == 2

    async def test_validate_invalid_payload_raises(self) -> None:
        codec = _codec()
        validator = DedupValidator(codec)
        payload = json.dumps(
            {
                "schema_version": "1",
                "decisions": [
                    {"verdict_decision_id": "verdict_aaa", "outcome": "accept"},
                ],
            }
        ).encode()
        with pytest.raises(ValueError, match="does not cover"):
            await validator.validate(payload)

    def test_warnings_empty(self) -> None:
        validator = DedupValidator(_codec())
        assert validator.warnings == ()
