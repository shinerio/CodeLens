"""Remediation domain model for the post-dedup fix-detection gate.

The Remediator is an evidence-capable agent that runs after the Deduplicator
and determines whether previously reported ``existing_findings`` have been
resolved by the current code changes. It replaces silent carry-forward of
stale unresolved comments with explicit fix detection.

A two-layer filter applies:

1. **Deterministic pre-filter** — findings whose source file was not touched
   by the current diff are auto-marked ``unresolved`` (the code hasn't
   changed, so the issue cannot have been fixed). Findings without a path
   (general PR comments) fall through to the LLM.
2. **LLM remediation** — the Remediator agent inspects the current code at
   each remaining finding's location, compares against the ``existing_code``
   anchor, and judges whether the issue is ``resolved``, ``unresolved``, or
   ``unclear`` using the ``resolved_review`` tool for batch marking and
   ``remediation_done`` as a coverage gate.

The unified identifier across the remediation layer is ``remediation_ref``
(``"{source_id}:{finding_id}"``), which every pending finding receives when
the role context is prepared.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class RemediationOutcome(StrEnum):
    """Terminal outcomes emitted by the Remediator for one existing finding."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    UNCLEAR = "unclear"


class RemediationDecisionSource(StrEnum):
    """Identify which layer produced a remediation decision."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"


@dataclass(frozen=True)
class RemediationDecision:
    """Record one remediation decision over a single existing finding.

    - ``resolved``: the current code changes have fixed the issue.
    - ``unresolved``: the issue still exists in the current code.
    - ``unclear``: cannot determine from the available evidence.

    ``decision_source`` distinguishes deterministic pre-filter results from
    LLM judgments for observability and auditability.
    """

    source_id: str
    finding_id: str
    outcome: RemediationOutcome
    evidence_summary: str
    decision_source: RemediationDecisionSource


@dataclass(frozen=True)
class PendingRemediation:
    """One existing finding resolved to flat fields for remediation judgment.

    The ``remediation_ref`` is the stable key the ``resolved_review`` tool
    references and that ``RemediationDecision`` stores (split back into
    ``source_id`` and ``finding_id``).
    """

    remediation_ref: str
    source_id: str
    finding_id: str
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
            "remediation_ref": self.remediation_ref,
            "source_id": self.source_id,
            "finding_id": self.finding_id,
            "title": self.title,
            "content": self.content,
            **{key: value for key, value in optional if value is not None},
        }


def run_deterministic_remediation_filter(
    pending: Sequence[PendingRemediation],
    changed_paths: frozenset[str],
) -> tuple[RemediationDecision, ...]:
    """Auto-resolve findings whose source file was not touched by the diff.

    A finding is deterministically marked ``unresolved`` when its path is
    known but does not appear in the set of changed paths — the code at
    that location hasn't been modified, so the issue cannot have been
    fixed by the current changes.

    Findings without a path (general PR comments without code location)
    are skipped, as structural comparison is impossible without a file
    reference. They fall through to the LLM layer.
    """

    decisions: list[RemediationDecision] = []
    for finding in pending:
        if not finding.path:
            continue
        if finding.path not in changed_paths:
            decisions.append(
                RemediationDecision(
                    source_id=finding.source_id,
                    finding_id=finding.finding_id,
                    outcome=RemediationOutcome.UNRESOLVED,
                    evidence_summary=(
                        f"File {finding.path} was not modified in this review; "
                        "the issue cannot have been resolved."
                    ),
                    decision_source=RemediationDecisionSource.DETERMINISTIC,
                )
            )
    return tuple(decisions)
