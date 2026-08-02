from typing import Literal, cast

from codelens.plugin.api.v2 import (
    BudgetProfile,
    FixedReviewerSelection,
    SupersedePolicy,
    TriggerReviewPolicy,
)


def adapt_v1_trigger_policy(
    selected_agents: tuple[str, ...], prompt_locale: str
) -> TriggerReviewPolicy:
    """Preserve legacy exact versions while supplying v2 host policy defaults."""

    if prompt_locale not in {"en", "zh-CN"}:
        raise ValueError("unsupported prompt_locale")
    return TriggerReviewPolicy(
        reviewer_selection=FixedReviewerSelection("fixed", selected_agents),
        budget_profile=BudgetProfile.STANDARD,
        supersede_policy=SupersedePolicy.PRESERVE_ALL,
        prompt_locale=cast(Literal["en", "zh-CN"], prompt_locale),
    )
