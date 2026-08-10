"""Atomic local persistence for Review file exclusion settings."""

import json
import os
import tempfile
from pathlib import Path

from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy


class FilesystemFileExclusionPolicyStore:
    """Store only the canonical v2 file exclusion policy document."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "file-exclusions.json"

    def get_policy(self) -> ReviewFileExclusionPolicy:
        if not self._path.exists():
            return ReviewFileExclusionPolicy()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {
            "exclude_binary",
            "path_regexes",
            "suffixes",
        }:
            raise ValueError("file exclusion settings are invalid")
        suffixes = raw["suffixes"]
        path_regexes = raw["path_regexes"]
        exclude_binary = raw["exclude_binary"]
        if (
            not isinstance(suffixes, list)
            or not all(isinstance(item, str) for item in suffixes)
            or not isinstance(path_regexes, list)
            or not all(isinstance(item, str) for item in path_regexes)
            or not isinstance(exclude_binary, bool)
        ):
            raise ValueError("file exclusion settings are invalid")
        return ReviewFileExclusionPolicy(tuple(suffixes), tuple(path_regexes), exclude_binary)

    def save_policy(self, policy: ReviewFileExclusionPolicy) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".file-exclusions-",
            suffix=".tmp",
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(policy.canonical_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
