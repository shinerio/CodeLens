from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.findings.domain.resolution import ResolutionDecision
from codelens.findings.infrastructure.resolver_output import (
    ResolverOutputCodec,
    ResolverSubmissionDto,
    ValidatedResolutionBatch,
)
from codelens.review.domain.ports import FindingValidationWarning
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding


class ResolutionSubmissionCollector:
    """Accept exactly one complete Resolver decision set for one logical run."""

    def __init__(self, codec: ResolverOutputCodec) -> None:
        self._codec = codec
        self._decisions: tuple[ResolutionDecision, ...] | None = None

    @property
    def is_completed(self) -> bool:
        return self._decisions is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        return ()

    def final_output(self) -> tuple[ResolutionDecision, ...]:
        if self._decisions is None:
            raise RuntimeError("Resolver has not submitted decisions")
        return self._decisions

    async def submit(self, submission: ResolverSubmissionDto) -> str:
        if self._decisions is not None:
            raise ValueError("Resolver may submit decisions only once")
        self._decisions = self._codec.decode(submission)
        return "Resolution decisions accepted."

    def as_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(
            name_override="submit_resolution",
            description_override=description,
        )
        async def submit_resolution(submission: ResolverSubmissionDto) -> str:
            """Submit one constrained decision for every supplied Candidate cluster."""

            return await collector.submit(submission)

        return submit_resolution

    def binding(self, description: str) -> RoleOutputToolBinding:
        return RoleOutputToolBinding(
            ToolContractReference("submit_resolution", 1),
            self.as_tool(description),
            self,
        )


class ResolutionValidator:
    """Validate a persisted Resolver Artifact against its frozen input constraints."""

    def __init__(self, codec: ResolverOutputCodec) -> None:
        self._codec = codec

    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]:
        return ()

    async def validate(self, payload: bytes) -> ValidatedResolutionBatch:
        return ValidatedResolutionBatch(self._codec.decode(payload))
