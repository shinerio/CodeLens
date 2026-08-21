"""Deduplication domain model for the post-verifier dedup gate.

The Deduplicator is a codeless agent that runs after the verifier (or after a
single reviewer when no verifier exists) and suppresses review findings that
duplicate already-reported ``existing_findings``. It replaces the prior
prompt-based dedup with structural enforcement.

A two-layer filter applies:

1. **Deterministic pre-filter** — path + line-range overlap + category match
   auto-denies obvious duplicates before the LLM runs, saving tokens.
2. **LLM dedup** — the Deduplicator agent judges the remaining survived
   findings against existing_findings, using a ``deduplicate`` tool for
   batch accept/deny and ``deduplicate_done`` as a coverage gate.

The unified identifier across the dedup layer is ``verdict_decision_id``
(see :func:`codelens.findings.domain.verdict.verdict_decision_id`), which
every survived verdict (ACCEPT or MERGE) already receives at persistence time.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from codelens.findings.domain.existing_findings import ExistingFinding


class DedupOutcome(StrEnum):
    """Terminal outcomes emitted by the Deduplicator for one survived finding."""

    ACCEPT = "accept"
    DENY = "deny"


class DedupDecisionSource(StrEnum):
    """Identify which layer produced a dedup decision."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"


@dataclass(frozen=True)
class DedupDecision:
    """Record one dedup decision over a single survived verdict.

    - ``accept``: publish the finding (it is not a duplicate).
    - ``deny``: suppress the finding (it duplicates an existing finding).

    ``decision_source`` distinguishes deterministic pre-filter denies from
    LLM denies for observability and auditability.
    """

    verdict_decision_id: str
    outcome: DedupOutcome
    decision_source: DedupDecisionSource


@dataclass(frozen=True)
class SurvivedFinding:
    """One publishable verdict resolved to flat fields for dedup judgment.

    For ACCEPT verdicts, fields are resolved from the cluster's canonical
    candidate. For MERGE verdicts, fields come from the verifier-supplied
    merge payload. The ``verdict_decision_id`` is the stable key the
    ``deduplicate`` tool references and that ``DedupDecision`` stores.
    """

    verdict_decision_id: str
    cluster_ids: tuple[str, ...]
    title: str
    content: str
    path: str | None = None
    side: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    existing_code: str | None = None
    category: str | None = None
    severity: str | None = None
    recommendation: str | None = None

    def as_payload(self) -> dict[str, object]:
        """Return the compact stable model-input representation."""

        optional: tuple[tuple[str, object | None], ...] = (
            ("path", self.path),
            ("side", self.side),
            ("start_line", self.start_line),
            ("end_line", self.end_line),
            ("existing_code", self.existing_code),
            ("category", self.category),
            ("severity", self.severity),
            ("recommendation", self.recommendation),
        )
        return {
            "verdict_decision_id": self.verdict_decision_id,
            "cluster_ids": list(self.cluster_ids),
            "title": self.title,
            "content": self.content,
            **{key: value for key, value in optional if value is not None},
        }


def run_deterministic_filter(
    survived: Sequence[SurvivedFinding],
    existing_items: Sequence[ExistingFinding],
) -> tuple[DedupDecision, ...]:
    """Auto-deny survived findings that clearly duplicate existing findings.

    A finding is deterministically denied when it shares the same path, the
    same category, and an overlapping line range with an existing finding.
    This is intentionally conservative — line numbers may shift across
    reviews, so shifted duplicates fall through to the LLM layer.

    Existing findings without a full location (path/lines absent) are
    skipped, as structural comparison is impossible without coordinates.
    """

    denies: list[DedupDecision] = []
    for finding in survived:
        if not finding.path or not finding.category:
            continue
        if finding.start_line is None or finding.end_line is None:
            continue
        for existing in existing_items:
            existing_path = existing.path
            existing_category = existing.category
            if not existing_path or not existing_category:
                continue
            if existing.start_line is None or existing.end_line is None:
                continue
            if (
                finding.path == existing_path
                and finding.category == existing_category
                and finding.start_line <= existing.end_line
                and existing.start_line <= finding.end_line
            ):
                denies.append(
                    DedupDecision(
                        verdict_decision_id=finding.verdict_decision_id,
                        outcome=DedupOutcome.DENY,
                        decision_source=DedupDecisionSource.DETERMINISTIC,
                    )
                )
                break
    return tuple(denies)
