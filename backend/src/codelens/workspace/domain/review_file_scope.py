"""Canonical policy and result models for every Review-visible file scope."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

MAX_FILE_EXCLUSION_RULES = 128
MAX_FILE_EXCLUSION_RULE_LENGTH = 1024


def _normalized_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    parsed = PurePosixPath(normalized)
    if not normalized or parsed.is_absolute() or ".." in parsed.parts or "\0" in normalized:
        raise ValueError("Review file path must be a safe repository-relative path")
    return parsed.as_posix()


@dataclass(frozen=True)
class ReviewFileExclusionPolicy:
    """Freeze user-configurable suffix and path exclusions with deterministic identity."""

    suffixes: tuple[str, ...] = ()
    path_regexes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.suffixes) > MAX_FILE_EXCLUSION_RULES:
            raise ValueError("too many file exclusion suffixes")
        if len(self.path_regexes) > MAX_FILE_EXCLUSION_RULES:
            raise ValueError("too many file exclusion regular expressions")
        normalized_suffixes: list[str] = []
        for index, suffix in enumerate(self.suffixes):
            value = suffix.strip().casefold()
            if not value:
                raise ValueError(f"suffixes[{index}] cannot be empty")
            if len(value) > MAX_FILE_EXCLUSION_RULE_LENGTH:
                raise ValueError(f"suffixes[{index}] is too long")
            normalized_suffixes.append(value)
        normalized_regexes: list[str] = []
        for index, pattern in enumerate(self.path_regexes):
            if not pattern:
                raise ValueError(f"path_regexes[{index}] cannot be empty")
            if len(pattern) > MAX_FILE_EXCLUSION_RULE_LENGTH:
                raise ValueError(f"path_regexes[{index}] is too long")
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"path_regexes[{index}] is invalid: {error.msg}") from error
            normalized_regexes.append(pattern)
        object.__setattr__(self, "suffixes", tuple(sorted(set(normalized_suffixes))))
        object.__setattr__(self, "path_regexes", tuple(sorted(set(normalized_regexes))))

    def canonical_json(self) -> str:
        """Return stable JSON used both for persistence and task freezing."""

        return json.dumps(
            {
                "exclude_binary": True,
                "path_regexes": list(self.path_regexes),
                "suffixes": list(self.suffixes),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> ReviewFileExclusionPolicy:
        """Rehydrate only the canonical v2 policy shape."""

        raw = json.loads(value)
        if not isinstance(raw, dict) or set(raw) != {
            "exclude_binary",
            "path_regexes",
            "suffixes",
        }:
            raise ValueError("frozen file exclusion policy is invalid")
        suffixes = raw["suffixes"]
        path_regexes = raw["path_regexes"]
        exclude_binary = raw["exclude_binary"]
        if (
            not isinstance(suffixes, list)
            or not all(isinstance(item, str) for item in suffixes)
            or not isinstance(path_regexes, list)
            or not all(isinstance(item, str) for item in path_regexes)
            or exclude_binary is not True
        ):
            raise ValueError("frozen file exclusion policy is invalid")
        policy = cls(tuple(suffixes), tuple(path_regexes))
        if policy.canonical_json() != value:
            raise ValueError("frozen file exclusion policy is not canonical")
        return policy

    @property
    def exclude_binary(self) -> bool:
        """Return the non-configurable binary exclusion invariant."""

        return True

    @property
    def policy_hash(self) -> str:
        """Return the SHA-256 identity of the canonical policy."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ReviewFileExclusionReason(StrEnum):
    """Classify deterministic reasons a path is absent from Review visibility."""

    GITIGNORE = "gitignore"
    REPOSITORY_INSTRUCTION = "repository_instruction"
    USER_SUFFIX = "user_suffix"
    USER_REGEX = "user_regex"
    BINARY = "binary"


_REASON_ORDER = {reason: index for index, reason in enumerate(ReviewFileExclusionReason)}


@dataclass(frozen=True)
class ReviewFileExclusion:
    """Retain every exclusion reason for one normalized path."""

    path: str
    reasons: tuple[ReviewFileExclusionReason, ...]


@dataclass(frozen=True)
class ReviewFileScope:
    """Provide the sole canonical Review and context visibility decision."""

    review_paths: tuple[str, ...]
    context_paths: tuple[str, ...]
    exclusions: tuple[ReviewFileExclusion, ...]
    policy_hash: str
    scope_hash: str

    @classmethod
    def include_all(
        cls,
        review_paths: tuple[str, ...] = (),
        context_paths: tuple[str, ...] = (),
    ) -> ReviewFileScope:
        """Build an unfiltered scope, primarily for trusted host-created fixtures."""

        policy_hash = ReviewFileExclusionPolicy().policy_hash
        provisional = cls(review_paths, context_paths, (), policy_hash, "")
        return cls(
            review_paths,
            context_paths,
            (),
            policy_hash,
            hashlib.sha256(provisional.canonical_json().encode()).hexdigest(),
        )

    def with_visible_paths(
        self,
        review_paths: tuple[str, ...],
        context_paths: tuple[str, ...],
    ) -> ReviewFileScope:
        """Return the same exclusion decision with entry-backed visible paths."""

        provisional = ReviewFileScope(
            review_paths=review_paths,
            context_paths=context_paths,
            exclusions=self.exclusions,
            policy_hash=self.policy_hash,
            scope_hash="",
        )
        return ReviewFileScope(
            review_paths=provisional.review_paths,
            context_paths=provisional.context_paths,
            exclusions=provisional.exclusions,
            policy_hash=provisional.policy_hash,
            scope_hash=hashlib.sha256(provisional.canonical_json().encode()).hexdigest(),
        )

    def canonical_json(self) -> str:
        """Return the canonical scope body whose digest is ``scope_hash``."""

        return json.dumps(
            {
                "context_paths": list(self.context_paths),
                "exclusions": [
                    {
                        "path": item.path,
                        "reasons": [reason.value for reason in item.reasons],
                    }
                    for item in self.exclusions
                ],
                "policy_hash": self.policy_hash,
                "review_paths": list(self.review_paths),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str, expected_hash: str) -> ReviewFileScope:
        """Rehydrate a hash-verified canonical persisted scope."""

        if hashlib.sha256(value.encode()).hexdigest() != expected_hash:
            raise ValueError("frozen Review file scope hash mismatch")
        raw = json.loads(value)
        if not isinstance(raw, dict) or set(raw) != {
            "context_paths",
            "exclusions",
            "policy_hash",
            "review_paths",
        }:
            raise ValueError("frozen Review file scope is invalid")
        exclusions = tuple(
            ReviewFileExclusion(
                path=str(item["path"]),
                reasons=tuple(ReviewFileExclusionReason(reason) for reason in item["reasons"]),
            )
            for item in raw["exclusions"]
        )
        scope = cls(
            review_paths=tuple(str(path) for path in raw["review_paths"]),
            context_paths=tuple(str(path) for path in raw["context_paths"]),
            exclusions=exclusions,
            policy_hash=str(raw["policy_hash"]),
            scope_hash=expected_hash,
        )
        if scope.canonical_json() != value:
            raise ValueError("frozen Review file scope is not canonical")
        return scope


class ReviewFileScopeResolver:
    """Combine adapter facts and frozen user policy without performing I/O."""

    def resolve(
        self,
        *,
        candidate_review_paths: tuple[str, ...],
        candidate_context_paths: tuple[str, ...],
        policy: ReviewFileExclusionPolicy,
        git_ignored_paths: tuple[str, ...] = (),
        instruction_excluded_paths: tuple[str, ...] = (),
        binary_paths: tuple[str, ...] = (),
    ) -> ReviewFileScope:
        review = tuple(sorted({_normalized_path(path) for path in candidate_review_paths}))
        context = tuple(sorted({_normalized_path(path) for path in candidate_context_paths}))
        git_ignored = {_normalized_path(path) for path in git_ignored_paths}
        instruction_excluded = {_normalized_path(path) for path in instruction_excluded_paths}
        binary = {_normalized_path(path) for path in binary_paths}
        compiled_regexes = tuple(re.compile(pattern) for pattern in policy.path_regexes)
        exclusions: list[ReviewFileExclusion] = []
        included_review: list[str] = []
        included_context: list[str] = []
        for path in tuple(sorted(set(review) | set(context))):
            reasons: list[ReviewFileExclusionReason] = []
            if path in git_ignored:
                reasons.append(ReviewFileExclusionReason.GITIGNORE)
            if path in instruction_excluded:
                reasons.append(ReviewFileExclusionReason.REPOSITORY_INSTRUCTION)
            basename = PurePosixPath(path).name.casefold()
            if any(basename.endswith(suffix) for suffix in policy.suffixes):
                reasons.append(ReviewFileExclusionReason.USER_SUFFIX)
            if any(pattern.search(path) is not None for pattern in compiled_regexes):
                reasons.append(ReviewFileExclusionReason.USER_REGEX)
            if path in binary:
                reasons.append(ReviewFileExclusionReason.BINARY)
            if reasons:
                exclusions.append(
                    ReviewFileExclusion(
                        path,
                        tuple(sorted(set(reasons), key=_REASON_ORDER.__getitem__)),
                    )
                )
                continue
            if path in review:
                included_review.append(path)
            if path in context:
                included_context.append(path)
        provisional = ReviewFileScope(
            tuple(included_review),
            tuple(included_context),
            tuple(exclusions),
            policy.policy_hash,
            "",
        )
        scope_hash = hashlib.sha256(provisional.canonical_json().encode()).hexdigest()
        return ReviewFileScope(
            provisional.review_paths,
            provisional.context_paths,
            provisional.exclusions,
            provisional.policy_hash,
            scope_hash,
        )
