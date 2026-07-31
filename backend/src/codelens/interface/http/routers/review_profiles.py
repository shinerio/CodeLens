from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from codelens.interface.http.dependencies import HttpComponents, get_components
from codelens.interface.http.dto import (
    AdaptiveReviewerSelectionDto,
    CopyReviewProfileRequest,
    CreateReviewProfileRequest,
    FixedReviewerSelectionDto,
    ReviewerSelectionDto,
    ReviewProfileResponse,
    UpdateReviewProfileRequest,
)
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    BudgetProfile,
    FixedReviewerSelection,
    ReviewerSelection,
)

router = APIRouter(prefix="/api/review-profiles", tags=["review-profiles"])


def _selection_from_request(selection: ReviewerSelectionDto) -> ReviewerSelection:
    if isinstance(selection, AdaptiveReviewerSelectionDto):
        return AdaptiveReviewerSelection()
    assert isinstance(selection, FixedReviewerSelectionDto)
    return FixedReviewerSelection(tuple(selection.reviewer_versions))


@router.get("", response_model=list[ReviewProfileResponse])
async def list_review_profiles(
    components: Annotated[HttpComponents, Depends(get_components)],
) -> list[ReviewProfileResponse]:
    profiles = await components.list_review_profiles.handle()
    return [ReviewProfileResponse.from_domain(profile) for profile in profiles]


@router.post("", response_model=ReviewProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_review_profile(
    request: CreateReviewProfileRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewProfileResponse:
    profile = await components.create_review_profile.handle(
        name=request.name,
        is_default=request.is_default,
        reviewer_selection=_selection_from_request(request.reviewer_selection),
        budget_profile=BudgetProfile(request.budget_profile),
    )
    return ReviewProfileResponse.from_domain(profile)


@router.put("/{profile_id}", response_model=ReviewProfileResponse)
async def update_review_profile(
    profile_id: str,
    request: UpdateReviewProfileRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewProfileResponse:
    profile = await components.update_review_profile.handle(
        profile_id,
        expected_revision=request.revision,
        name=request.name,
        is_default=request.is_default,
        reviewer_selection=_selection_from_request(request.reviewer_selection),
        budget_profile=BudgetProfile(request.budget_profile),
    )
    return ReviewProfileResponse.from_domain(profile)


@router.post(
    "/{profile_id}/copies",
    response_model=ReviewProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def copy_review_profile(
    profile_id: str,
    request: CopyReviewProfileRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> ReviewProfileResponse:
    profile = await components.copy_review_profile.handle(profile_id, name=request.name)
    return ReviewProfileResponse.from_domain(profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review_profile(
    profile_id: str,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> Response:
    await components.delete_review_profile.handle(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
