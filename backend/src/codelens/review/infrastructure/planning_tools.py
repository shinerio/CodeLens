from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.review.application.planning import PlannerSelection
from codelens.review.domain.tool_results import ToolDiagnostic, ToolResult, ToolResultStatus
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding
from codelens.review.infrastructure.planner_output import (
    PlannerOutputCodec,
    PlannerSelectionDto,
)
from codelens.review.infrastructure.tool_contract import reject_unknown_arguments


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
            return ToolResult(
                "finalize_plan",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "plan_already_finalized", "The Review Plan is already final.", False
                    ),
                ),
            ).to_json()
        try:
            selection = self._codec.decode(submission.model_dump(mode="json"))
        except ValueError:
            return ToolResult(
                "finalize_plan",
                ToolResultStatus.REJECTED,
                {},
                (
                    ToolDiagnostic(
                        "invalid_reviewer_selection",
                        "The Reviewer selection is invalid.",
                        True,
                        "submission",
                    ),
                ),
            ).to_json()
        self._selection = selection
        reviewer_count = len(self._selection.reviewer_references)
        return ToolResult(
            "finalize_plan",
            ToolResultStatus.SUCCESS,
            {
                "reviewer_count": reviewer_count,
                "reviewer_references": list(self._selection.reviewer_references),
            },
        ).to_json()

    def as_finalize_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(
            name_override="finalize_plan",
            description_override=description,
        )
        async def finalize_plan(submission: PlannerSelectionDto) -> str:
            """Validate one complete Reviewer selection and finalize the Plan."""

            return await collector.finalize(submission)

        return reject_unknown_arguments(finalize_plan)

    def bindings(self, finalize_description: str) -> tuple[RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("finalize_plan", 2),
                self.as_finalize_tool(finalize_description),
                self,
            ),
        )
