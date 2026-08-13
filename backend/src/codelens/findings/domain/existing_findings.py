"""Validated, frozen findings supplied to a later Review as duplicate context."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, cast

MAX_EXISTING_FINDINGS = 500
MAX_EXISTING_FINDINGS_BYTES = 512 * 1024
_SOURCE_ID_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")


def _required_text(value: str, name: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters")
    return normalized


@dataclass(frozen=True, slots=True)
class ExistingFinding:
    """Describe one already-reported problem without granting it trusted authority.

    Existing findings are untrusted duplicate-detection context. A location is
    either wholly absent (for general PR comments) or fully specified. It is not
    resolved against the new Snapshot because a later PR revision may have moved
    or removed the original line.
    """

    source_id: str
    finding_id: str
    title: str
    content: str
    path: str | None = None
    side: Literal["old", "new"] | None = None
    start_line: int | None = None
    end_line: int | None = None
    existing_code: str | None = None
    fingerprint: str | None = None
    recommendation: str | None = None
    category: str | None = None
    severity: str | None = None

    def __post_init__(self) -> None:
        if _SOURCE_ID_PATTERN.fullmatch(self.source_id) is None:
            raise ValueError("source_id is invalid")
        object.__setattr__(self, "finding_id", _required_text(self.finding_id, "finding_id", 256))
        object.__setattr__(self, "title", _required_text(self.title, "title", 500))
        object.__setattr__(self, "content", _required_text(self.content, "content", 8_000))
        location = (self.path, self.side, self.start_line, self.end_line, self.existing_code)
        if any(value is not None for value in location) and not all(
            value is not None for value in location
        ):
            raise ValueError("location fields must be provided together")
        if self.path is not None:
            candidate = PurePosixPath(self.path)
            if (
                not self.path
                or "\0" in self.path
                or "\\" in self.path
                or candidate.is_absolute()
                or ".." in candidate.parts
                or candidate.as_posix() != self.path
            ):
                raise ValueError("path must be a normalized repository-relative POSIX path")
            assert self.start_line is not None and self.end_line is not None
            if self.start_line < 1 or self.end_line < self.start_line:
                raise ValueError("line range is invalid")
            assert self.existing_code is not None
            if not self.existing_code.strip() or len(self.existing_code) > 8_000:
                raise ValueError("existing_code must contain 1 to 8000 characters")
        if self.fingerprint is not None and re.fullmatch(r"[0-9a-f]{64}", self.fingerprint) is None:
            raise ValueError("fingerprint must be a lowercase SHA-256 digest")
        for name, value, maximum in (
            ("recommendation", self.recommendation, 8_000),
            ("category", self.category, 128),
            ("severity", self.severity, 64),
        ):
            if value is not None:
                object.__setattr__(self, name, _required_text(value, name, maximum))

    def as_payload(self) -> dict[str, object]:
        """Return the compact stable model-input representation."""

        optional: tuple[tuple[str, object | None], ...] = (
            ("path", self.path),
            ("side", self.side),
            ("start_line", self.start_line),
            ("end_line", self.end_line),
            ("existing_code", self.existing_code),
            ("fingerprint", self.fingerprint),
            ("recommendation", self.recommendation),
            ("category", self.category),
            ("severity", self.severity),
        )
        return {
            "source_id": self.source_id,
            "finding_id": self.finding_id,
            "title": self.title,
            "content": self.content,
            **{key: value for key, value in optional if value is not None},
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ExistingFinding:
        """Validate a persisted or plugin-provided finding payload."""

        allowed = {
            "source_id",
            "finding_id",
            "title",
            "content",
            "path",
            "side",
            "start_line",
            "end_line",
            "existing_code",
            "fingerprint",
            "recommendation",
            "category",
            "severity",
        }
        if not set(payload).issubset(allowed):
            raise ValueError("existing finding contains unknown fields")
        try:
            source_id = payload["source_id"]
            finding_id = payload["finding_id"]
            title = payload["title"]
            content = payload["content"]
        except KeyError as error:
            raise ValueError("existing finding is missing required fields") from error
        if not all(isinstance(value, str) for value in (source_id, finding_id, title, content)):
            raise ValueError("existing finding required fields must be strings")
        side = payload.get("side")
        if side not in (None, "old", "new"):
            raise ValueError("existing finding side is invalid")
        start_line = payload.get("start_line")
        end_line = payload.get("end_line")
        if isinstance(start_line, bool) or not isinstance(start_line, int | type(None)):
            raise ValueError("existing finding start_line is invalid")
        if isinstance(end_line, bool) or not isinstance(end_line, int | type(None)):
            raise ValueError("existing finding end_line is invalid")
        string_optionals = {
            name: payload.get(name)
            for name in (
                "path",
                "existing_code",
                "fingerprint",
                "recommendation",
                "category",
                "severity",
            )
        }
        if any(
            value is not None and not isinstance(value, str) for value in string_optionals.values()
        ):
            raise ValueError("existing finding optional text fields must be strings")
        return cls(
            source_id=cast(str, source_id),
            finding_id=cast(str, finding_id),
            title=cast(str, title),
            content=cast(str, content),
            path=cast(str | None, string_optionals["path"]),
            side=side,
            start_line=start_line,
            end_line=end_line,
            existing_code=cast(str | None, string_optionals["existing_code"]),
            fingerprint=cast(str | None, string_optionals["fingerprint"]),
            recommendation=cast(str | None, string_optionals["recommendation"]),
            category=cast(str | None, string_optionals["category"]),
            severity=cast(str | None, string_optionals["severity"]),
        )


@dataclass(frozen=True, slots=True)
class ExistingFindingSet:
    """Own a bounded, deterministic and hash-verifiable historical issue set."""

    items: tuple[ExistingFinding, ...]
    canonical_json: str
    content_hash: str

    @classmethod
    def empty(cls) -> ExistingFindingSet:
        return cls.from_findings(())

    @classmethod
    def from_findings(cls, findings: Sequence[ExistingFinding]) -> ExistingFindingSet:
        deduplicated: dict[tuple[str, str], ExistingFinding] = {}
        for finding in findings:
            deduplicated.setdefault((finding.source_id, finding.finding_id), finding)
        items = tuple(
            sorted(deduplicated.values(), key=lambda item: (item.source_id, item.finding_id))
        )
        if len(items) > MAX_EXISTING_FINDINGS:
            raise ValueError(f"existing findings exceed the {MAX_EXISTING_FINDINGS} item limit")
        canonical_json = json.dumps(
            [item.as_payload() for item in items],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(canonical_json.encode("utf-8")) > MAX_EXISTING_FINDINGS_BYTES:
            raise ValueError("existing findings exceed the serialized byte limit")
        return cls(
            items=items,
            canonical_json=canonical_json,
            content_hash=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def from_json(cls, canonical_json: str, expected_hash: str) -> ExistingFindingSet:
        """Rehydrate persisted findings only after verifying their canonical hash."""

        if hashlib.sha256(canonical_json.encode("utf-8")).hexdigest() != expected_hash:
            raise ValueError("frozen existing findings hash mismatch")
        raw = json.loads(canonical_json)
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ValueError("frozen existing findings must be a list of objects")
        restored = cls.from_findings(tuple(ExistingFinding.from_payload(item) for item in raw))
        if restored.canonical_json != canonical_json:
            raise ValueError("frozen existing findings are not canonical")
        return restored
