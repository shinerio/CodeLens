import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codelens.plugin.report.local_file_export.existing_findings import (
    LocalExistingFindingsProvider,
)


class _PluginStore:
    def __init__(self, record: object) -> None:
        self.record = record

    async def get_plugin(self, _plugin_id: str) -> object:
        return self.record


async def test_loads_and_deduplicates_existing_findings_from_configured_output_directory(
    tmp_path: Path,
) -> None:
    output = tmp_path / "reports" / "reviews"
    output.mkdir(parents=True)
    finding = {
        "finding_id": "finding-1",
        "fingerprint": "f" * 64,
        "title": "Existing issue",
        "explanation": "This was already reported.",
        "recommendation": "Keep the existing discussion open.",
        "category": "correctness",
        "severity": "high",
        "primary_location": {
            "path": "src/service.py",
            "side": "new",
            "start_line": 12,
            "end_line": 13,
        },
        "source_excerpt": {
            "base": None,
            "target": {
                "revision": "a" * 40,
                "start_line": 9,
                "end_line": 16,
                "content": (
                    "def unchanged():\n"
                    "    pass\n"
                    "\n"
                    "if account is None:\n"
                    "    return account.name\n"
                    "\n"
                    "return account\n"
                    "\n"
                ),
            },
        },
    }
    envelope = {"schema_version": "2.0", "findings": [finding]}
    (output / "findings-20260812T010000000000Z.json").write_text(json.dumps(envelope))
    (output / "findings-20260812T020000000000Z.json").write_text(json.dumps(envelope))
    provider = LocalExistingFindingsProvider(
        _PluginStore(
            SimpleNamespace(
                report_enabled=True,
                report_config={
                    "output_dir": "reports/reviews",
                    "use_as_existing_findings": True,
                },
            )
        )
    )

    findings = await provider.load(tmp_path)

    assert len(findings) == 1
    assert findings[0].source_id == "local"
    assert findings[0].finding_id == "finding-1"
    assert findings[0].path == "src/service.py"
    assert findings[0].existing_code == "if account is None:\n    return account.name"


async def test_disabled_local_input_does_not_read_reports(tmp_path: Path) -> None:
    provider = LocalExistingFindingsProvider(
        _PluginStore(
            SimpleNamespace(
                report_enabled=True,
                report_config={
                    "output_dir": "../outside",
                    "use_as_existing_findings": False,
                },
            )
        )
    )

    assert await provider.load(tmp_path) == ()


async def test_positioned_local_finding_rejects_missing_existing_code_source(
    tmp_path: Path,
) -> None:
    output = tmp_path / "CodeLensReview"
    output.mkdir()
    (output / "findings-20260812T010000000000Z.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "findings": [
                    {
                        "finding_id": "finding-1",
                        "title": "Existing issue",
                        "explanation": "This was already reported.",
                        "primary_location": {
                            "path": "src/service.py",
                            "side": "new",
                            "start_line": 12,
                            "end_line": 12,
                        },
                    }
                ],
            }
        )
    )
    provider = LocalExistingFindingsProvider(
        _PluginStore(SimpleNamespace(report_enabled=True, report_config={}))
    )

    with pytest.raises(ValueError, match="source_excerpt"):
        await provider.load(tmp_path)
