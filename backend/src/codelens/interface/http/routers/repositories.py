from typing import Annotated

from fastapi import APIRouter, Depends

from codelens.interface.http.dependencies import HttpComponents, get_components
from codelens.interface.http.dto import (
    DirectoryBrowseRequest,
    DirectoryEntryResponse,
    DirectoryListingResponse,
    RepositoryBranchResponse,
    RepositoryCatalogRequest,
    RepositoryCatalogResponse,
    RepositoryCommitResponse,
    RepositoryInspectionRequest,
    RepositoryResponse,
)

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


@router.post("/inspect", response_model=RepositoryResponse)
async def inspect_repository(
    request: RepositoryInspectionRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> RepositoryResponse:
    """Resolve one filesystem input to a contained, stable repository identity."""

    repository = await components.repository_inspector.inspect(request.path)
    return RepositoryResponse.from_domain(repository)


@router.post("/catalog", response_model=RepositoryCatalogResponse)
async def get_repository_catalog(
    request: RepositoryCatalogRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> RepositoryCatalogResponse:
    """Return selectable branches and one recent-commit page for an exact Git root."""

    catalog = await components.repository_catalog.handle(
        request.path,
        commit_offset=request.commit_offset,
        commit_limit=request.commit_limit,
    )
    return RepositoryCatalogResponse(
        branches=[
            RepositoryBranchResponse(
                name=branch.name,
                oid=branch.oid,
                is_current=branch.is_current,
                is_remote=branch.is_remote,
            )
            for branch in catalog.branches
        ],
        commits=[
            RepositoryCommitResponse(
                oid=commit.oid,
                short_oid=commit.short_oid,
                author=commit.author,
                message=commit.message,
                committed_at=commit.committed_at,
            )
            for commit in catalog.commits
        ],
        next_commit_offset=catalog.next_commit_offset,
    )


@router.post("/browse", response_model=DirectoryListingResponse)
async def browse_directories(
    request: DirectoryBrowseRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> DirectoryListingResponse:
    """Browse all platform roots and directory children without reading file contents."""

    listing = await components.directory_browser.handle(request.path)
    return DirectoryListingResponse(
        current_path=str(listing.current_path) if listing.current_path is not None else None,
        parent_path=str(listing.parent_path) if listing.parent_path is not None else None,
        roots=[str(root) for root in listing.roots],
        directories=[
            DirectoryEntryResponse(
                name=entry.name,
                path=str(entry.path),
                is_git_repository=entry.is_git_repository,
            )
            for entry in listing.directories
        ],
        current_is_git_repository=listing.current_is_git_repository,
        is_truncated=listing.is_truncated,
    )
