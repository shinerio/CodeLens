"""Export formatters for different output formats."""

import json
from dataclasses import asdict

from codelens.review.application.export_findings import (
    FindingExportEnvelope,
)


class JsonFindingExportFormatter:
    """Format the export envelope as structured JSON."""

    @property
    def format_id(self) -> str:
        return "json"

    @property
    def media_type(self) -> str:
        return "application/json"

    @property
    def file_extension(self) -> str:
        return "json"

    def format(self, envelope: FindingExportEnvelope) -> bytes:
        """Serialize the envelope as JSON with proper formatting."""

        data = asdict(envelope)
        content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        return content.encode("utf-8")


class MarkdownFindingExportFormatter:
    """Format the export envelope as human-readable Markdown."""

    @property
    def format_id(self) -> str:
        return "markdown"

    @property
    def media_type(self) -> str:
        return "text/markdown"

    @property
    def file_extension(self) -> str:
        return "md"

    def format(self, envelope: FindingExportEnvelope) -> bytes:
        """Render the envelope as Markdown with structured sections."""

        lines: list[str] = []

        # Header
        lines.append("# CodeLens Review Export\n")
        lines.append(f"**Exported:** {envelope.exported_at.isoformat()}  ")
        lines.append(f"**Schema Version:** {envelope.schema_version}\n")

        # Review metadata
        lines.append("## Review Information\n")
        lines.append(f"- **Task ID:** {envelope.review.task_id}")
        lines.append(f"- **Repository:** {envelope.review.repository_name}")
        lines.append(f"- **Scope:** {envelope.review.scope_type}")
        if envelope.review.base_ref:
            lines.append(f"- **Base Branch:** `{envelope.review.base_ref}`")
        if envelope.review.target_ref:
            lines.append(f"- **Target Branch:** `{envelope.review.target_ref}`")
        lines.append(f"- **Base OID:** `{envelope.review.base_oid[:12]}`")
        lines.append(f"- **Head OID:** `{envelope.review.head_oid[:12]}`")
        lines.append(f"- **Status:** {envelope.review.status}")
        lines.append(f"- **Created:** {envelope.review.created_at.isoformat()}")
        selected_versions = envelope.review.plan_summary.selected_reviewer_versions
        if selected_versions:
            lines.append(f"- **Reviewers:** {', '.join(selected_versions)}")
        lines.append(f"- **Strategy:** {envelope.review.plan_summary.strategy}")
        lines.append(f"- **Findings:** {len(envelope.findings)}\n")

        lines.append("---\n")

        # Findings
        for idx, finding in enumerate(envelope.findings, 1):
            lines.append(f"## {idx}. [{finding.severity.upper()}] {finding.title}\n")

            # Metadata table
            lines.append("| Field | Value |")
            lines.append("|---|---|")
            lines.append(f"| ID | `{finding.finding_id}` |")
            lines.append(f"| Category | {finding.category} |")
            lines.append(f"| Severity | {finding.severity} |")
            confidence = (
                f"{finding.confidence * 100:.0f}%"
                if finding.confidence is not None
                else "not applicable"
            )
            lines.append(f"| Confidence | {confidence} |")
            lines.append(f"| Disposition | {finding.disposition} |")
            lines.append(f"| Change Origin | {finding.change_origin} |")
            lines.append(
                f"| File | {finding.primary_location.path}:"
                f"{finding.primary_location.start_line}-"
                f"{finding.primary_location.end_line} "
                f"({finding.primary_location.side}) |"
            )
            lines.append("")

            # Impact
            if finding.impact:
                lines.append("**Impact:**")
                lines.append(f"{finding.impact}\n")

            # Explanation
            if finding.explanation:
                lines.append("**Explanation:**")
                lines.append(f"{finding.explanation}\n")

            # Reproduction
            if finding.reproduction:
                lines.append("**Reproduction:**")
                lines.append(f"{finding.reproduction}\n")

            # Recommendation
            if finding.recommendation:
                lines.append("**Recommendation:**")
                lines.append(f"{finding.recommendation}\n")

            # Evidence
            if finding.evidence:
                lines.append("**Evidence:**")
                for evidence in finding.evidence:
                    lines.append(f"- {evidence.kind}: {evidence.description}")
                lines.append("")

            # Rule sources
            if finding.rule_sources:
                lines.append("**Rule Sources:**")
                for rule in finding.rule_sources:
                    lines.append(f"- {rule.path}")
                lines.append("")

            # Source snippets
            if finding.source_excerpt.base or finding.source_excerpt.target:
                lines.append("**Source Context:**")

                if finding.source_excerpt.base:
                    base = finding.source_excerpt.base
                    lines.append(
                        f"\n**Base (revision `{base.revision[:12]}`, "
                        f"lines {base.start_line}-{base.end_line}):**"
                    )
                    lines.append("```")
                    lines.append(base.content.rstrip("\n"))
                    lines.append("```\n")

                if finding.source_excerpt.target:
                    target = finding.source_excerpt.target
                    lines.append(
                        f"\n**Target (revision `{target.revision[:12]}`, "
                        f"lines {target.start_line}-{target.end_line}):**"
                    )
                    lines.append("```")
                    lines.append(target.content.rstrip("\n"))
                    lines.append("```\n")

            lines.append("---\n")

        content = "\n".join(lines)
        return content.encode("utf-8")
