"""Load prior local JSON exports as frozen duplicate-detection context."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

from codelens.findings.domain.existing_findings import ExistingFinding, ExistingFindingSet
from codelens.plugin.domain.ports import PluginStorePort

_MAX_REPORT_FILES = 100
_MAX_REPORT_BYTES = 2 * 1024 * 1024


class LocalExistingFindingsProvider:
    """Read bounded v2 JSON exports from the local plugin output directory.

    The provider runs only while a new Review is being created. Returned values
    are validated and frozen by the Review aggregate, so later report writes or
    process restarts cannot change an already-created task's model input.
    """

    def __init__(self, plugin_store: PluginStorePort) -> None:
        self._plugin_store = plugin_store

    async def load(self, repository_path: Path) -> tuple[ExistingFinding, ...]:
        """Return deduplicated prior findings when local input is enabled."""

        record = await self._plugin_store.get_plugin("local")
        if record is None or not record.report_enabled:
            return ()
        config = record.report_config
        if config.get("use_as_existing_findings", True) is not True:
            return ()
        output_dir = config.get("output_dir", "CodeLensReview")
        if not isinstance(output_dir, str) or not output_dir:
            raise ValueError("local existing findings output_dir must be a non-empty string")
        return await asyncio.to_thread(self._load_sync, repository_path, output_dir)

    @staticmethod
    def _load_sync(repository_path: Path, output_dir: str) -> tuple[ExistingFinding, ...]:
        resolved_repository = repository_path.resolve()
        target = (resolved_repository / output_dir).resolve()
        if not target.is_relative_to(resolved_repository) or target == resolved_repository:
            raise ValueError("local existing findings output_dir must stay within repository")
        if not target.is_dir():
            return ()
        report_paths = sorted(target.glob("findings-*.json"), reverse=True)
        if len(report_paths) > _MAX_REPORT_FILES:
            raise ValueError(
                f"local existing findings exceed the {_MAX_REPORT_FILES} report file limit"
            )
        loaded: list[ExistingFinding] = []
        for report_path in report_paths:
            if report_path.is_symlink() or not report_path.is_file():
                continue
            if report_path.stat().st_size > _MAX_REPORT_BYTES:
                raise ValueError("local existing findings report exceeds the byte limit")
            try:
                envelope = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"cannot parse local findings report: {report_path.name}"
                ) from error
            loaded.extend(LocalExistingFindingsProvider._parse_envelope(envelope))
        return ExistingFindingSet.from_findings(tuple(loaded)).items

    @staticmethod
    def _parse_envelope(envelope: object) -> tuple[ExistingFinding, ...]:
        if not isinstance(envelope, Mapping) or envelope.get("schema_version") != "2.0":
            raise ValueError("local findings report must use schema_version 2.0")
        raw_findings = envelope.get("findings")
        if not isinstance(raw_findings, list) or not all(
            isinstance(item, Mapping) for item in raw_findings
        ):
            raise ValueError("local findings report has an invalid findings array")
        resolved_refs = LocalExistingFindingsProvider._resolved_refs(envelope)
        parsed = [
            LocalExistingFindingsProvider._parse_finding(item)
            for item in raw_findings
            if f"local:{item.get('finding_id', '')}" not in resolved_refs
        ]
        return tuple(parsed)

    @staticmethod
    def _resolved_refs(envelope: Mapping[object, object]) -> frozenset[str]:
        """Extract finding IDs marked as resolved by the Remediator."""

        remediation = envelope.get("remediation")
        if not isinstance(remediation, Mapping):
            return frozenset()
        decisions = remediation.get("decisions")
        if not isinstance(decisions, list):
            return frozenset()
        refs: set[str] = set()
        for decision in decisions:
            if not isinstance(decision, Mapping):
                continue
            outcome = decision.get("outcome")
            source_id = decision.get("source_id")
            finding_id = decision.get("finding_id")
            if (
                outcome == "resolved"
                and isinstance(source_id, str)
                and isinstance(finding_id, str)
            ):
                refs.add(f"{source_id}:{finding_id}")
        return frozenset(refs)

    @staticmethod
    def _parse_finding(item: Mapping[object, object]) -> ExistingFinding:
        def required_text(name: str) -> str:
            value = item.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"local findings report has invalid {name}")
            return value

        location = item.get("primary_location")
        if not isinstance(location, Mapping):
            raise ValueError("local findings report has invalid primary_location")
        path = location.get("path")
        side = location.get("side")
        start_line = location.get("start_line")
        end_line = location.get("end_line")
        if (
            not isinstance(path, str)
            or side not in ("old", "new")
            or isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or isinstance(end_line, bool)
            or not isinstance(end_line, int)
        ):
            raise ValueError("local findings report has invalid primary_location")
        explanation = item.get("explanation")
        impact = item.get("impact")
        content = explanation if isinstance(explanation, str) and explanation.strip() else impact
        if not isinstance(content, str) or not content.strip():
            raise ValueError("local findings report has no issue explanation")
        existing_code = LocalExistingFindingsProvider._extract_existing_code(
            item,
            side=side,
            start_line=start_line,
            end_line=end_line,
        )

        def optional_text(name: str) -> str | None:
            value = item.get(name)
            return value if isinstance(value, str) and value.strip() else None

        return ExistingFinding(
            source_id="local",
            finding_id=required_text("finding_id"),
            fingerprint=optional_text("fingerprint"),
            title=required_text("title"),
            content=content,
            path=path,
            side=side,
            start_line=start_line,
            end_line=end_line,
            existing_code=existing_code,
            recommendation=optional_text("recommendation"),
            category=optional_text("category"),
            severity=optional_text("severity"),
        )

    @staticmethod
    def _extract_existing_code(
        item: Mapping[object, object],
        *,
        side: object,
        start_line: int,
        end_line: int,
    ) -> str:
        """Extract the exact historical code anchor instead of trusting stale line numbers."""

        source_excerpt = item.get("source_excerpt")
        if not isinstance(source_excerpt, Mapping):
            raise ValueError("local findings report has invalid source_excerpt")
        version = source_excerpt.get("base" if side == "old" else "target")
        if not isinstance(version, Mapping):
            raise ValueError("local findings report is missing the finding-side source excerpt")
        excerpt_start_line = version.get("start_line")
        excerpt_content = version.get("content")
        if (
            isinstance(excerpt_start_line, bool)
            or not isinstance(excerpt_start_line, int)
            or not isinstance(excerpt_content, str)
        ):
            raise ValueError("local findings report has invalid source_excerpt")
        relative_start = start_line - excerpt_start_line
        relative_end = end_line - excerpt_start_line + 1
        excerpt_lines = excerpt_content.splitlines()
        if (
            relative_start < 0
            or relative_end > len(excerpt_lines)
            or relative_start >= relative_end
        ):
            raise ValueError("local findings location is outside its source_excerpt")
        existing_code = "\n".join(excerpt_lines[relative_start:relative_end])
        if not existing_code.strip():
            raise ValueError("local findings source excerpt contains no issue code")
        return existing_code
