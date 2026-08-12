"""Strict TOML configuration source for Review file exclusions."""

import tomllib
from pathlib import Path
from typing import cast

from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy

_EXPECTED_KEYS = {"exclude_binary", "path_regexes", "suffixes"}


class FilesystemFileExclusionPolicySource:
    """Load an operator-managed policy without creating runtime configuration state."""

    def __init__(self, config_path: Path) -> None:
        self._path = config_path.expanduser().resolve()

    def get_policy(self) -> ReviewFileExclusionPolicy:
        """Read and validate the complete configuration document."""

        if not self._path.is_file():
            raise ValueError("file exclusion configuration does not exist")
        try:
            parsed: object = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError("file exclusion configuration is invalid") from error
        if not isinstance(parsed, dict):
            raise ValueError("file exclusion configuration is invalid")
        raw = cast(dict[object, object], parsed)
        keys = set(raw)
        unknown_keys = keys - _EXPECTED_KEYS
        if unknown_keys:
            raise ValueError("file exclusion configuration contains unknown fields")
        if keys != _EXPECTED_KEYS:
            raise ValueError("file exclusion configuration is invalid")
        suffixes = raw["suffixes"]
        path_regexes = raw["path_regexes"]
        exclude_binary = raw["exclude_binary"]
        if (
            not isinstance(suffixes, list)
            or not all(isinstance(item, str) for item in suffixes)
            or not isinstance(path_regexes, list)
            or not all(isinstance(item, str) for item in path_regexes)
            or type(exclude_binary) is not bool
        ):
            raise ValueError("file exclusion configuration is invalid")
        try:
            return ReviewFileExclusionPolicy(
                tuple(cast(list[str], suffixes)),
                tuple(cast(list[str], path_regexes)),
                cast(bool, exclude_binary),
            )
        except ValueError as error:
            raise ValueError("file exclusion configuration is invalid") from error
