from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.review.application.planning import PlannerSelection
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding
from codelens.review.infrastructure.planner_output import (
    PlannerOutputCodec,
    PlannerSelectionDto,
)


class ReviewPlanSubmissionCollector:
    """Accept exactly one validated Planner submission for one logical run."""

    def __init__(self, codec: PlannerOutputCodec) -> None:
        self._codec = codec
        self._selection: PlannerSelection | None = None

    @property
    def is_completed(self) -> bool:
        return self._selection is not None

    @property
    def selection(self) -> PlannerSelection:
        if self._selection is None:
            raise RuntimeError("Planner has not submitted a Review Plan")
        return self._selection

    @property
    def incomplete_review_files(self) -> tuple[str, ...]:
        return ()

    def final_output(self) -> PlannerSelection:
        return self.selection

    async def submit(self, submission: PlannerSelectionDto) -> str:
        if self._selection is not None:
            raise ValueError("Planner may submit a Review Plan only once")
        self._selection = self._codec.decode(submission.model_dump(mode="json"))
        return "Review Plan accepted."

    def as_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(
            name_override="submit_review_plan",
            description_override=description,
        )
        async def submit_review_plan(submission: PlannerSelectionDto) -> str:
            """Submit the complete bounded Reviewer selection once."""

            return await collector.submit(submission)

        return submit_review_plan

    def binding(self, description: str) -> RoleOutputToolBinding:
        return RoleOutputToolBinding(
            ToolContractReference("submit_review_plan", 1),
            self.as_tool(description),
            self,
        )
