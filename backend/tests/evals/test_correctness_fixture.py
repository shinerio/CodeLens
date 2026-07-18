from __future__ import annotations

from pathlib import Path

from codelens.testing.correctness_fixture import (
    load_simple_branch_batch,
    prepare_simple_branch_repository,
)


async def test_simple_branch_fixture_produces_the_expected_golden_batch(
    tmp_path: Path,
) -> None:
    fixture = await prepare_simple_branch_repository(tmp_path)
    batch = await load_simple_branch_batch(fixture.repository, base_oid=fixture.base_oid)

    assert fixture.repository.joinpath("REVIEW.md").exists()
    assert fixture.repository.joinpath("src/state.py").exists()
    assert batch.schema_version == "1"
    assert len(batch.findings) == 1

    finding = batch.findings[0]
    assert finding.title == "Inverted transition guard allows invalid states"
    assert finding.primary_location.path == "src/state.py"
    assert finding.primary_location.start_line == 7
    assert finding.changed_hunk_id is not None
    assert finding.evidence[0].excerpt_hash == finding.primary_location.excerpt_hash
