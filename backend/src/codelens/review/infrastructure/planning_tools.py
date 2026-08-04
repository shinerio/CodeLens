from agents import Tool, function_tool

from codelens.capabilities.domain.models import ToolContractReference
from codelens.review.application.planning import PlannerSelection
from codelens.review.infrastructure.capability_tools import RoleOutputToolBinding
from codelens.review.infrastructure.planner_output import (
    PlannerOutputCodec,
    PlannerSelectionDto,
)


class ReviewPlanSubmissionCollector:
    """Accumulate validated Planner reviewer submissions across multiple calls.

    The Planner may call `submit_review_plan` multiple times to add reviewers
    in batches. Each batch is validated against the eligible and unavailable
    sets. Duplicates across batches are silently ignored. When the Planner
    has finished selecting reviewers, it calls `finalize_plan` to validate
    that the accumulated set exactly matches the eligible reviewers and
    produce the final `PlannerSelection`.
    """

    def __init__(self, codec: PlannerOutputCodec) -> None:
        self._codec = codec
        self._accumulated: set[str] = set()
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
        """Return eligible reviewers not yet accumulated."""
        if self._selection is not None:
            return ()
        return tuple(sorted(self._codec._eligible - self._accumulated))

    def final_output(self) -> PlannerSelection:
        return self.selection

    async def submit(self, submission: PlannerSelectionDto) -> str:
        if self._selection is not None:
            raise ValueError("Planner has already finalized the Review Plan")
        batch = self._codec.decode_batch(submission.model_dump(mode="json"))
        before = len(self._accumulated)
        self._accumulated.update(batch)
        added = len(self._accumulated) - before
        return f"Accepted {added} new Reviewer(s), {len(self._accumulated)} total."

    async def finalize(self) -> str:
        if self._selection is not None:
            raise ValueError("Planner has already finalized the Review Plan")
        self._selection = self._codec.decode_final(frozenset(self._accumulated))
        return "Review Plan finalized."

    def as_submit_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(
            name_override="submit_review_plan",
            description_override=description,
        )
        async def submit_review_plan(submission: PlannerSelectionDto) -> str:
            """Add a batch of Reviewer references to the Plan."""

            return await collector.submit(submission)

        return submit_review_plan

    def as_finalize_tool(self, description: str) -> Tool:
        collector = self

        @function_tool(
            name_override="finalize_plan",
            description_override=description,
        )
        async def finalize_plan() -> str:
            """Validate accumulated Reviewers and finalize the Plan."""

            return await collector.finalize()

        return finalize_plan

    def bindings(
        self, submit_description: str, finalize_description: str
    ) -> tuple[RoleOutputToolBinding, RoleOutputToolBinding]:
        return (
            RoleOutputToolBinding(
                ToolContractReference("submit_review_plan", 1),
                self.as_submit_tool(submit_description),
            ),
            RoleOutputToolBinding(
                ToolContractReference("finalize_plan", 1),
                self.as_finalize_tool(finalize_description),
                self,
            ),
        )
