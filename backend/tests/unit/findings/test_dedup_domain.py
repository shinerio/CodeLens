"""Tests for the dedup domain model: DedupDecision, SurvivedFinding, run_deterministic_filter."""


from codelens.findings.domain.dedup import (
    DedupDecision,
    DedupDecisionSource,
    DedupOutcome,
    SurvivedFinding,
    run_deterministic_filter,
)
from codelens.findings.domain.existing_findings import ExistingFinding


def _survived(
    verdict_decision_id: str = "verdict_abc",
    *,
    path: str | None = "src/app.py",
    category: str | None = "correctness",
    start_line: int | None = 10,
    end_line: int | None = 20,
) -> SurvivedFinding:
    return SurvivedFinding(
        verdict_decision_id=verdict_decision_id,
        cluster_ids=("cluster_1",),
        title="Stale cache",
        content="The cache is not invalidated.",
        path=path,
        side="new",
        start_line=start_line,
        end_line=end_line,
        existing_code="const x = 1;",
        category=category,
        severity="high",
        recommendation="Invalidate the cache.",
    )


def _existing(
    *,
    path: str | None = "src/app.py",
    category: str | None = "correctness",
    start_line: int | None = 10,
    end_line: int | None = 20,
) -> ExistingFinding:
    has_location = path is not None
    return ExistingFinding(
        source_id="codehub",
        finding_id="existing_1",
        title="Stale cache",
        content="Old report.",
        path=path,
        side="new" if has_location else None,
        start_line=start_line if has_location else None,
        end_line=end_line if has_location else None,
        existing_code="const x = 1;" if has_location else None,
        category=category,
        severity="high",
        recommendation="Invalidate the cache.",
    )


class TestDedupDecision:
    def test_accept_outcome(self) -> None:
        decision = DedupDecision(
            verdict_decision_id="verdict_abc",
            outcome=DedupOutcome.ACCEPT,
            decision_source=DedupDecisionSource.LLM,
        )
        assert decision.outcome == DedupOutcome.ACCEPT
        assert decision.outcome.value == "accept"
        assert decision.decision_source == DedupDecisionSource.LLM

    def test_deny_outcome(self) -> None:
        decision = DedupDecision(
            verdict_decision_id="verdict_abc",
            outcome=DedupOutcome.DENY,
            decision_source=DedupDecisionSource.DETERMINISTIC,
        )
        assert decision.outcome == DedupOutcome.DENY
        assert decision.outcome.value == "deny"
        assert decision.decision_source == DedupDecisionSource.DETERMINISTIC


class TestSurvivedFindingPayload:
    def test_as_payload_includes_required_fields(self) -> None:
        finding = _survived()
        payload = finding.as_payload()
        assert payload["verdict_decision_id"] == "verdict_abc"
        assert payload["cluster_ids"] == ["cluster_1"]
        assert payload["title"] == "Stale cache"
        assert payload["content"] == "The cache is not invalidated."

    def test_as_payload_omits_none_optional_fields(self) -> None:
        finding = SurvivedFinding(
            verdict_decision_id="verdict_abc",
            cluster_ids=("cluster_1",),
            title="T",
            content="C",
            path=None,
            side=None,
            start_line=None,
            end_line=None,
            existing_code=None,
            category=None,
            severity=None,
            recommendation=None,
        )
        payload = finding.as_payload()
        assert "path" not in payload
        assert "side" not in payload
        assert "start_line" not in payload
        assert "category" not in payload

    def test_as_payload_includes_present_optional_fields(self) -> None:
        finding = _survived()
        payload = finding.as_payload()
        assert payload["path"] == "src/app.py"
        assert payload["side"] == "new"
        assert payload["start_line"] == 10
        assert payload["end_line"] == 20
        assert payload["category"] == "correctness"


class TestRunDeterministicFilter:
    def test_path_category_overlap_denies(self) -> None:
        survived = (_survived(),)
        existing = (_existing(),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 1
        assert denies[0].verdict_decision_id == "verdict_abc"
        assert denies[0].outcome == DedupOutcome.DENY
        assert denies[0].decision_source == DedupDecisionSource.DETERMINISTIC

    def test_different_path_passes_through(self) -> None:
        survived = (_survived(path="src/other.py"),)
        existing = (_existing(path="src/app.py"),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_different_category_passes_through(self) -> None:
        survived = (_survived(category="security"),)
        existing = (_existing(category="correctness"),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_non_overlapping_lines_passes_through(self) -> None:
        survived = (_survived(start_line=100, end_line=110),)
        existing = (_existing(start_line=10, end_line=20),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_adjacent_lines_do_not_overlap(self) -> None:
        survived = (_survived(start_line=21, end_line=30),)
        existing = (_existing(start_line=10, end_line=20),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_overlapping_lines_denies(self) -> None:
        survived = (_survived(start_line=15, end_line=25),)
        existing = (_existing(start_line=10, end_line=20),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 1

    def test_missing_path_passes_through(self) -> None:
        survived = (_survived(path=None),)
        existing = (_existing(),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_missing_category_passes_through(self) -> None:
        survived = (_survived(category=None),)
        existing = (_existing(),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_missing_lines_passes_through(self) -> None:
        survived = (_survived(start_line=None, end_line=None),)
        existing = (_existing(),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_existing_missing_location_passes_through(self) -> None:
        survived = (_survived(),)
        existing = (_existing(path=None, start_line=None, end_line=None),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 0

    def test_multiple_survived_some_deny(self) -> None:
        survived = (
            _survived(verdict_decision_id="verdict_a"),
            _survived(verdict_decision_id="verdict_b", path="src/other.py"),
        )
        existing = (_existing(),)
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 1
        assert denies[0].verdict_decision_id == "verdict_a"

    def test_empty_inputs_return_empty(self) -> None:
        denies = run_deterministic_filter((), ())
        assert denies == ()

    def test_break_after_first_match(self) -> None:
        survived = (_survived(),)
        existing = (
            _existing(),
            _existing(path="src/other.py"),
        )
        denies = run_deterministic_filter(survived, existing)
        assert len(denies) == 1
