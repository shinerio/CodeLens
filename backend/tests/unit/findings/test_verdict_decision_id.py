"""Tests for the shared verdict_decision_id helper."""

from codelens.findings.domain.verdict import verdict_decision_id


def test_produces_verdict_prefix() -> None:
    result = verdict_decision_id("review_abc", ("cluster_1",))
    assert result.startswith("verdict_")


def test_produces_sha256_hex_after_prefix() -> None:
    result = verdict_decision_id("review_abc", ("cluster_1",))
    assert len(result) == len("verdict_") + 64
    assert all(c in "0123456789abcdef" for c in result[len("verdict_"):])


def test_same_inputs_produce_same_id() -> None:
    assert verdict_decision_id("review_abc", ("cluster_1",)) == verdict_decision_id(
        "review_abc", ("cluster_1",)
    )


def test_different_task_id_produces_different_id() -> None:
    assert verdict_decision_id("review_abc", ("cluster_1",)) != verdict_decision_id(
        "review_xyz", ("cluster_1",)
    )


def test_different_cluster_ids_produce_different_id() -> None:
    assert verdict_decision_id("review_abc", ("cluster_1",)) != verdict_decision_id(
        "review_abc", ("cluster_2",)
    )


def test_cluster_order_matters() -> None:
    """The cluster_ids tuple order affects the hash (comma-joined as-is)."""

    assert verdict_decision_id("review_abc", ("cluster_1", "cluster_2")) != (
        verdict_decision_id("review_abc", ("cluster_2", "cluster_1"))
    )


def test_single_cluster_vs_multi_cluster_differ() -> None:
    assert verdict_decision_id("review_abc", ("cluster_1",)) != (
        verdict_decision_id("review_abc", ("cluster_1", "cluster_2"))
    )
