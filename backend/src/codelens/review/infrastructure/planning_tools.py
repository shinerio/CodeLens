from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.review.application.planning import PlannerSelection
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding
from codelens.review.infrastructure.planner_output import (
    PlannerOutputCodec,
    PlannerSelectionDto,
)


class ReviewPlanSubmissionCollector:
    """Finalize one validated Planner selection from a single tool call."""

    def __init__(self, codec: PlannerOutputCodec) -> None:
        self._codec = codec
        self._selection: PlannerSelection | None = None

    @property
    def is_completed(self) -> bool:
        return self._selection is not None

    @property
    def selection(self) -> PlannerSelection:
        if self._selection is None:
            raise RuntimeError("Planner has not finalized a Review Plan")
        return self._selection

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        """Planner completion has no Reviewer file-coverage concept."""

        return ()

    def final_output(self) -> PlannerSelection:
        return self.selection

    async def finalize(self, submission: PlannerSelectionDto) -> str:
        if self._selection is not None:
            raise ValueError("Planner has already finalized the Review Plan")
        self._selection = self._codec.decode(submission.model_dump(mode="json"))
        reviewer_count = len(self._selection.reviewer_references)
        return f"Review Plan finalized with {reviewer_count} Reviewer(s)."

    def as_finalize_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(
            name_override="finalize_plan",
            description_override=description,
        )
        async def finalize_plan(submission: PlannerSelectionDto) -> str:
            """Validate one complete Reviewer selection and finalize the Plan."""

            return await collector.finalize(submission)

        return finalize_plan

    def bindings(self, finalize_description: str) -> tuple[RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("finalize_plan", 2),
                self.as_finalize_tool(finalize_description),
                self,
            ),
        )
