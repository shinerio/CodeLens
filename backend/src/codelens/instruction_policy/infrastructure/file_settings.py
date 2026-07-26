"""Atomic JSON persistence for repository instruction file limits."""

import json
import os
import tempfile
from pathlib import Path
from typing import TypedDict, cast

from codelens.instruction_policy.domain.models import InstructionLineLimits


class _InstructionSettingsPayload(TypedDict):
    root_max_lines: int
    nested_max_lines: int


class FilesystemInstructionLineLimitsStore:
    """Store instruction limits in the local CodeLens data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir.expanduser().resolve() / "instruction-policy.json"

    def get_line_limits(self) -> InstructionLineLimits:
        """Load persisted limits, using product defaults before first save."""

        if not self._path.exists():
            return InstructionLineLimits()
        raw: object = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("instruction policy settings are invalid")
        payload = cast(dict[object, object], raw)
        root_max_lines = payload.get("root_max_lines")
        nested_max_lines = payload.get("nested_max_lines")
        if (
            not isinstance(root_max_lines, int)
            or isinstance(root_max_lines, bool)
            or not isinstance(nested_max_lines, int)
            or isinstance(nested_max_lines, bool)
        ):
            raise ValueError("instruction policy settings are invalid")
        return InstructionLineLimits(
            root_max_lines=root_max_lines,
            nested_max_lines=nested_max_lines,
        )

    def save_line_limits(self, limits: InstructionLineLimits) -> None:
        """Write a complete settings document and atomically replace the old one."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".instruction-policy-",
            suffix=".tmp",
        )
        temporary = Path(name)
        payload: _InstructionSettingsPayload = {
            "root_max_lines": limits.root_max_lines,
            "nested_max_lines": limits.nested_max_lines,
        }
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
