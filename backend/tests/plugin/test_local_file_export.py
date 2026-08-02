import re
from datetime import UTC, datetime
from pathlib import Path

from codelens.plugin.report.local_file_export.sink import LocalFileExportSink
from codelens.review.application.export_findings import (
    FindingExportEnvelope,
    ReviewCoverageDto,
    ReviewExportMeta,
    ReviewPlanSummaryDto,
    SelectionRequestDto,
)
from codelens.workspace.infrastructure.git_cli import GitCli


def _export_envelope() -> FindingExportEnvelope:
    created_at = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
    return FindingExportEnvelope(
        schema_version="2.0",
        exported_at=created_at,
        review=ReviewExportMeta(
            task_id="review_timestamped_export",
            repository_name="fixture",
            scope_type="commit",
            base_oid="a" * 40,
            head_oid="b" * 40,
            base_ref=None,
            target_ref=None,
            status="completed",
            selection_request=SelectionRequestDto(
                mode="fixed", reviewer_versions=("correctness:v1",)
            ),
            plan_summary=ReviewPlanSummaryDto(
                strategy="fixed",
                selected_reviewer_versions=("correctness:v1",),
                planner_version=None,
                plan_hash="a" * 64,
            ),
            coverage=ReviewCoverageDto(
                completed_reviewer_versions=("correctness:v1",),
                failed_reviewer_versions=(),
                omitted_reviewer_versions=(),
            ),
            created_at=created_at,
        ),
        findings=(),
    )


async def test_export_preserves_prior_results_with_timestamped_filenames(
    git_repository: Path,
) -> None:
    output_dir = git_repository / "CodeLensReview"
    output_dir.mkdir()
    previous_export = output_dir / "findings.json"
    previous_export.write_bytes(b"previous export")
    sink = LocalFileExportSink()

    first_result = await sink.export(_export_envelope(), {}, git_repository)
    second_result = await sink.export(_export_envelope(), {}, git_repository)

    assert first_result.success is True
    assert second_result.success is True
    assert previous_export.read_bytes() == b"previous export"
    timestamped_files = sorted(
        path.name for path in output_dir.iterdir() if path.name != "findings.json"
    )
    assert len(timestamped_files) == 4
    assert all(
        re.fullmatch(r"findings-\d{8}T\d{12}Z\.(?:json|md)", filename)
        for filename in timestamped_files
    )


async def test_export_adds_output_directory_to_gitignore(
    git_repository: Path,
) -> None:
    gitignore = git_repository / ".gitignore"
    gitignore.write_text("*.log", encoding="utf-8")

    result = await LocalFileExportSink().export(
        _export_envelope(),
        {"output_dir": "reports/reviews", "formats": ["json"]},
        git_repository,
    )

    assert result.success is True
    assert gitignore.read_text(encoding="utf-8") == "*.log\n/reports/reviews/\n"
    exported_file = next((git_repository / "reports" / "reviews").iterdir())
    ignored = await GitCli().run(
        git_repository,
        "check-ignore",
        "--quiet",
        exported_file.relative_to(git_repository).as_posix(),
    )
    assert ignored.returncode == 0


async def test_export_does_not_duplicate_an_existing_gitignore_rule(
    git_repository: Path,
) -> None:
    gitignore = git_repository / ".gitignore"
    original_content = "# generated reports\nCodeLensReview/\n"
    gitignore.write_text(original_content, encoding="utf-8")

    result = await LocalFileExportSink().export(
        _export_envelope(),
        {"formats": ["markdown"]},
        git_repository,
    )

    assert result.success is True
    assert gitignore.read_text(encoding="utf-8") == original_content
