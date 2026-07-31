from typing import cast

import pytest
from pydantic import ValidationError

from codelens.findings.infrastructure.comment_v2_output import (
    CommentV2BatchSchema,
    CommentV2OutputCodec,
)


def valid_comment_v2_payload() -> dict[str, object]:
    return {
        "schema_version": "2",
        "findings": [
            {
                "reviewer_id": "security",
                "path": "src/webhook.py",
                "side": "new",
                "existing_code": "payload = parse(body)",
                "title": "Body parsed before signature verification",
                "content": "Untrusted input is parsed before authentication.",
                "recommendation": "Verify the signature before parsing.",
                "category": "authentication",
                "severity": "high",
                "primary_dimension": "security",
                "secondary_dimensions": ["performance"],
                "evidence_strength": "direct",
                "impact_certainty": "confirmed",
                "reproducibility": "deterministic",
            }
        ],
    }


def test_comment_v2_accepts_categorical_evidence_axes() -> None:
    batch = CommentV2BatchSchema.model_validate(valid_comment_v2_payload())

    assert batch.findings[0].evidence_strength == "direct"


def test_comment_v2_rejects_numeric_confidence() -> None:
    payload = valid_comment_v2_payload()
    findings = cast(list[dict[str, object]], payload["findings"])
    findings[0]["confidence"] = 0.9

    with pytest.raises(ValidationError, match="confidence"):
        CommentV2BatchSchema.model_validate(payload)


def test_comment_v2_rejects_unknown_fields_and_duplicate_dimensions() -> None:
    payload = valid_comment_v2_payload()
    findings = cast(list[dict[str, object]], payload["findings"])
    findings[0]["secondary_dimensions"] = ["performance", "performance"]

    with pytest.raises(ValidationError, match="secondary_dimensions"):
        CommentV2BatchSchema.model_validate(payload)


def test_comment_v2_codec_is_canonical_and_version_locked() -> None:
    codec = CommentV2OutputCodec()

    encoded = codec.encode(valid_comment_v2_payload())
    decoded = codec.decode(encoded)

    assert codec.schema_version == "2"
    assert decoded == CommentV2BatchSchema.model_validate(valid_comment_v2_payload())
    assert encoded == codec.encode(decoded)

    with pytest.raises(ValueError, match="unsupported"):
        CommentV2OutputCodec("1")  # type: ignore[arg-type]
