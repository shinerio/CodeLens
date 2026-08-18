"""Normalize model-visible Snapshot paths and implement the shared v2 Glob contract."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath
from typing import Literal

type ModelScopeType = Literal["root", "directory", "file"]
type ModelGlobScope = Literal["recursive_basename", "relative_path"]

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class InvalidModelPathError(ValueError):
    """Reject a path that cannot safely and unambiguously address the Snapshot."""


class InvalidModelGlobError(ValueError):
    """Reject a malformed model-visible Glob pattern."""


class AmbiguousRecursiveGlobError(InvalidModelGlobError):
    """Reject `**` embedded in a normal segment and expose one legal correction."""

    def __init__(self, pattern: str, suggested_pattern: str) -> None:
        super().__init__("recursive ** must be a complete path segment")
        self.pattern = pattern
        self.suggested_pattern = suggested_pattern


@dataclass(frozen=True, slots=True)
class ModelPath:
    """Carry the requested path, its canonical form, and resolved Snapshot scope."""

    requested_path: str
    normalized_path: str
    scope_type: ModelScopeType


@dataclass(frozen=True, slots=True)
class ModelGlob:
    """Carry one validated shared find/grep Glob and its matching scope."""

    requested_pattern: str
    effective_pattern: str
    pattern_scope: ModelGlobScope


def normalize_model_path(path: str, *, visible_paths: tuple[str, ...]) -> ModelPath:
    """Normalize one model path and resolve file versus directory from visible entries."""

    if not isinstance(path, str):
        raise InvalidModelPathError("model path must be a string")
    requested_path = path
    if "\0" in path or "\\" in path:
        raise InvalidModelPathError("model path contains a forbidden character")
    if path.startswith("/") or _WINDOWS_DRIVE.match(path):
        raise InvalidModelPathError("model path must be repository relative")
    if "//" in path:
        raise InvalidModelPathError("model path contains an ambiguous empty segment")
    if path in {"", ".", "./"}:
        return ModelPath(requested_path, "", "root")
    if path.startswith("./"):
        path = path[2:]
    if path.endswith("/"):
        path = path[:-1]
    if not path or path.startswith("./"):
        raise InvalidModelPathError("model path is ambiguous")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidModelPathError("model path contains an unsafe segment")
    is_visible_file = path in visible_paths
    has_visible_descendant = any(candidate.startswith(f"{path}/") for candidate in visible_paths)
    if is_visible_file and has_visible_descendant:
        raise InvalidModelPathError("model path is both a file and a directory")
    scope_type: ModelScopeType = "file" if is_visible_file else "directory"
    return ModelPath(requested_path, path, scope_type)


def parse_model_glob(pattern: str) -> ModelGlob:
    """Validate one Glob and distinguish recursive basename from relative path matching."""

    if not isinstance(pattern, str) or not pattern:
        raise InvalidModelGlobError("glob pattern must be a non-empty string")
    if "\0" in pattern or "\\" in pattern or pattern.startswith("/") or "//" in pattern:
        raise InvalidModelGlobError("glob pattern is unsafe")
    segments = pattern.split("/")
    ambiguous = tuple(segment for segment in segments if "**" in segment and segment != "**")
    if ambiguous:
        suggestion = "/".join(
            segment if segment == "**" else segment.replace("**", "*") for segment in segments
        )
        raise AmbiguousRecursiveGlobError(pattern, suggestion)
    if any(segment in {"", ".", ".."} for segment in segments):
        raise InvalidModelGlobError("glob pattern contains an unsafe segment")
    scope: ModelGlobScope = "recursive_basename" if "/" not in pattern else "relative_path"
    return ModelGlob(pattern, pattern, scope)


def match_model_glob(path: str, pattern: ModelGlob) -> bool:
    """Match a normalized path according to the shared basename/path v2 semantics."""

    if pattern.pattern_scope == "recursive_basename":
        return fnmatch.fnmatchcase(PurePosixPath(path).name, pattern.effective_pattern)
    path_parts = PurePosixPath(path).parts
    pattern_parts = PurePosixPath(pattern.effective_pattern).parts

    @cache
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        pattern_part = pattern_parts[pattern_index]
        if pattern_part == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], pattern_part)
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)
