from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.findings.domain.resolution import VerificationDecision
from codelens.findings.infrastructure.verifier_output import (
    ValidatedVerificationBatch,
    VerifierOutputCodec,
    VerifierSubmissionDto,
)
from codelens.review.domain.ports import FindingValidationWarning
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding


class VerificationSubmissionCollector:
    """Accept exactly one complete bounded Verifier decision batch."""

    def __init__(self, codec: VerifierOutputCodec) -> None:
        self._codec = codec
        self._decisions: tuple[VerificationDecision, ...] | None = None

    @property
    def is_completed(self) -> bool:
        return self._decisions is not None

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        return ()

    def final_output(self) -> tuple[VerificationDecision, ...]:
        if self._decisions is None:
            raise RuntimeError("Verifier has not submitted decisions")
        return self._decisions

    async def submit(self, submission: VerifierSubmissionDto) -> str:
        if self._decisions is not None:
            raise ValueError("Verifier may submit decisions only once")
        self._decisions = self._codec.decode(submission)
        return "Verification decisions accepted."

    def as_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(name_override="submit_verification", description_override=description)
        async def submit_verification(submission: VerifierSubmissionDto) -> str:
            """Submit one bounded outcome for every supplied verification Cluster."""

            return await collector.submit(submission)

        return submit_verification

    def binding(self, description: str) -> RoleOutputToolBinding:
        return RoleOutputToolBinding(
            ToolContractReference("submit_verification", 1),
            self.as_tool(description),
            self,
        )


class VerificationValidator:
    def __init__(self, codec: VerifierOutputCodec) -> None:
        self._codec = codec

    @property
    def warnings(self) -> tuple[FindingValidationWarning, ...]:
        return ()

    async def validate(self, payload: bytes) -> ValidatedVerificationBatch:
        return ValidatedVerificationBatch(self._codec.decode(payload))
