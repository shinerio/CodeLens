"""Load repository-owned defaults for non-model-gateway Web settings."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from codelens.instruction_policy.domain.models import InstructionLineLimits
from codelens.review.application.settings import (
    ReviewCompletionSettings,
    TriggerIdempotencySettings,
)
from codelens.review.domain.ports import (
    MAX_RECENT_REPOSITORY_LIMIT,
    MIN_RECENT_REPOSITORY_LIMIT,
)
from codelens.review.domain.tool_limits import ToolLimits
from codelens.workspace.domain.review_file_scope import ReviewFileExclusionPolicy

type DefaultLogLevel = Literal["debug", "info", "warning", "error"]

_EXPECTED_SECTION_KEYS = {
    "repositories": {"recent_repository_limit"},
    "instruction_files": {"root_max_lines", "nested_max_lines"},
    "file_exclusions": {"suffixes", "path_regexes"},
    "review_completion": {"max_incomplete_review_retries"},
    "trigger_idempotency": {"enabled"},
    "logging": {"level"},
    "tool_limits": set(ToolLimits.__dataclass_fields__),
}


@dataclass(frozen=True)
class WebSettingsDefaults:
    """Validated base layer overridden by settings persisted from the Web UI."""

    recent_repository_limit: int
    instruction_files: InstructionLineLimits
    file_exclusions: ReviewFileExclusionPolicy
    review_completion: ReviewCompletionSettings
    trigger_idempotency: TriggerIdempotencySettings
    log_level: DefaultLogLevel
    tool_limits: ToolLimits


def _section(payload: dict[str, object], name: str) -> dict[str, object]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"Web settings defaults section {name} is missing or invalid")
    section = cast(dict[str, object], value)
    if set(section) != _EXPECTED_SECTION_KEYS[name]:
        raise ValueError(f"Web settings defaults section {name} has invalid fields")
    return section


def load_web_settings_defaults(path: Path) -> WebSettingsDefaults:
    """Strictly load the complete non-Secret Web settings default document."""

    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load Web settings defaults: {path}") from error
    if set(payload) != set(_EXPECTED_SECTION_KEYS):
        raise ValueError("Web settings defaults contain invalid sections")

    repositories = _section(payload, "repositories")
    instruction_files = _section(payload, "instruction_files")
    file_exclusions = _section(payload, "file_exclusions")
    review_completion = _section(payload, "review_completion")
    trigger_idempotency = _section(payload, "trigger_idempotency")
    logging = _section(payload, "logging")
    tool_limits = _section(payload, "tool_limits")

    recent_repository_limit = repositories.get("recent_repository_limit")
    if (
        isinstance(recent_repository_limit, bool)
        or not isinstance(recent_repository_limit, int)
        or not MIN_RECENT_REPOSITORY_LIMIT
        <= recent_repository_limit
        <= MAX_RECENT_REPOSITORY_LIMIT
    ):
        raise ValueError("recent_repository_limit default is invalid")
    level = logging.get("level")
    if level not in {"debug", "info", "warning", "error"}:
        raise ValueError("logging level default is invalid")
    suffixes = file_exclusions.get("suffixes")
    path_regexes = file_exclusions.get("path_regexes")
    if (
        not isinstance(suffixes, list)
        or not all(isinstance(item, str) for item in suffixes)
        or not isinstance(path_regexes, list)
        or not all(isinstance(item, str) for item in path_regexes)
    ):
        raise ValueError("file exclusion Web defaults are invalid")

    try:
        return WebSettingsDefaults(
            recent_repository_limit=recent_repository_limit,
            instruction_files=InstructionLineLimits(**instruction_files),  # type: ignore[arg-type]
            file_exclusions=ReviewFileExclusionPolicy(
                suffixes=tuple(cast(list[str], suffixes)),
                path_regexes=tuple(cast(list[str], path_regexes)),
            ),
            review_completion=ReviewCompletionSettings(**review_completion),  # type: ignore[arg-type]
            trigger_idempotency=TriggerIdempotencySettings(**trigger_idempotency),  # type: ignore[arg-type]
            log_level=cast(DefaultLogLevel, level),
            tool_limits=ToolLimits(**tool_limits),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Web settings defaults contain invalid values") from error
