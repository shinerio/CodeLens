"""Process-level resource limits persisted from the Web UI and applied at startup.

Unlike :class:`~codelens.review.domain.tool_limits.ToolLimits` (which bounds
per-Agent evidence operations and is hot-reloadable for new Agent runs), a
``NodeSettings`` document governs process-wide concerns: the Worker memory cap,
Review/Agent concurrency, and the memory-pressure thresholds. These values are
read once when the Worker process starts and back the ``MemoryGuard`` and
``WorkerSemaphores``; changing them via the Web UI persists a new document that
takes effect on the next process restart.

The defaults mirror ``bootstrap.settings.Settings`` so that a fresh deployment
(without a persisted ``node-settings.json``) boots with the same behaviour as the
previous environment-variable-only configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codelens.bootstrap.settings import Settings

DEFAULT_MEMORY_LIMIT_MB = 2048
DEFAULT_MEMORY_CHECK_INTERVAL_SECONDS = 5.0
DEFAULT_MEMORY_CLEANUP_THRESHOLD_RATIO = 0.85
DEFAULT_MEMORY_REJECT_THRESHOLD_RATIO = 0.95
DEFAULT_MAX_ACTIVE_REVIEWS = 4
DEFAULT_MAX_ACTIVE_AGENT_RUNS = 8
DEFAULT_MAX_AGENT_RUNS_PER_REVIEW = 4

MIN_MEMORY_LIMIT_MB = 512
MIN_MEMORY_CHECK_INTERVAL_SECONDS = 0.01
MIN_MAX_ACTIVE_REVIEWS = 1
MIN_MAX_ACTIVE_AGENT_RUNS = 1


@dataclass(frozen=True)
class NodeSettings:
    """Process-level resource limits persisted from the Web UI (restart to apply)."""

    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB
    memory_check_interval_seconds: float = DEFAULT_MEMORY_CHECK_INTERVAL_SECONDS
    memory_cleanup_threshold_ratio: float = DEFAULT_MEMORY_CLEANUP_THRESHOLD_RATIO
    memory_reject_threshold_ratio: float = DEFAULT_MEMORY_REJECT_THRESHOLD_RATIO
    max_active_reviews: int = DEFAULT_MAX_ACTIVE_REVIEWS
    max_active_agent_runs: int = DEFAULT_MAX_ACTIVE_AGENT_RUNS
    max_agent_runs_per_review: int = DEFAULT_MAX_AGENT_RUNS_PER_REVIEW

    def __post_init__(self) -> None:
        if self.memory_limit_mb < MIN_MEMORY_LIMIT_MB:
            raise ValueError("memory_limit_mb must be at least 512")
        if self.memory_check_interval_seconds <= 0:
            raise ValueError("memory_check_interval_seconds must be positive")
        if not 0 < self.memory_cleanup_threshold_ratio < self.memory_reject_threshold_ratio <= 1:
            raise ValueError(
                "memory thresholds must satisfy 0 < cleanup < reject <= 1"
            )
        if self.max_active_reviews < MIN_MAX_ACTIVE_REVIEWS:
            raise ValueError("max_active_reviews must be positive")
        if self.max_active_agent_runs < MIN_MAX_ACTIVE_AGENT_RUNS:
            raise ValueError("max_active_agent_runs must be positive")
        if not 1 <= self.max_agent_runs_per_review <= self.max_active_agent_runs:
            raise ValueError("per-review Agent limit must not exceed the global limit")

    @classmethod
    def from_settings(cls, settings: Settings) -> NodeSettings:
        """Build node settings from a bootstrap.Settings instance (env-var sourced).

        Used at startup to seed the persisted store on first boot: when no
        ``node-settings.json`` exists the env-var-derived values become the
        defaults, so a fresh deployment matches the previous env-only behaviour.
        """

        return cls(
            memory_limit_mb=settings.memory_limit_mb,
            memory_check_interval_seconds=settings.memory_check_interval_seconds,
            memory_cleanup_threshold_ratio=settings.memory_cleanup_threshold_ratio,
            memory_reject_threshold_ratio=settings.memory_reject_threshold_ratio,
            max_active_reviews=settings.max_active_reviews,
            max_active_agent_runs=settings.max_active_agent_runs,
            max_agent_runs_per_review=settings.max_agent_runs_per_review,
        )
